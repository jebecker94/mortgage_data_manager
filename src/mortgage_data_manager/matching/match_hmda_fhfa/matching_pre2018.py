"""FHFA pre-2018 matching workflow: three rounds run by one engine.

HMDA (2007-2017) is matched to the FHFA Single-Family Census Tract file (2008-2017
acquisitions). Each round is a `Pre2018Round` in ``rounds.py`` (`ROUNDS_PRE2018`) -- which
HMDA year meets which FHFA year, whether income is a join key or an AMFI-deflated
tolerance, which purchaser types are barred, how earlier rounds' claims are honored, and
how candidates resolve to pairs. `HMDAFHFAPre2018Matcher.run_round` runs any of them.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import atomic_output, should_process_file
from mortgage_data_manager.core.reference import (
    load_tract_relationship_2000_2010,
    needs_vintage_bridge_2000_2010,
)
from mortgage_data_manager.matching.match_hmda_fhfa.rounds import ROUNDS_PRE2018, Pre2018Round
from mortgage_data_manager.matching.match_hmda_fhfa.utils import (
    assign_pre2018_hmda_index,
)
from mortgage_data_manager.matching.resolve import drop_ambiguous, margin_best
from mortgage_data_manager.sources.hmda.load import scan_hmda

logger = get_logger(__name__)


# FHFA codes non-metro loans with this MSA value (~10% of pre-2018 volume).
NON_METRO_MSA = "99999"

_FHFA_FILE = "fhfa_pre2018_round1.parquet"
_HMDA_FILE = "hmda_pre2018_round1.parquet"
_MSA = "Metropolitan Statistical Area (MSA) Code"
_AMFI = "Area Median Family Income"
_UPB = "Acquisition Unpaid Principal Balance (UPB)"
_FHFA_INCOME = "Borrower(s) Annual Income"

#: Unique on every match file (1:1), so sorting on it fixes the written row order: the joins
#: and concats upstream do not preserve order, and reruns would otherwise differ.
_FHFA_KEY = ["year", "Enterprise Flag", "Record Number"]
_CROSSWALK_COLS = [
    *_FHFA_KEY,
    "activity_year",
    "respondent_id",
    "agency_code",
    "sequence_number",
    "HMDAIndex",
]

#: HMDA join key -> its FHFA counterpart.
_JOIN_KEYS = {
    "census_tract": "census_string",
    "loan_type": "Federal Guarantee",
    "income": _FHFA_INCOME,
}

# Acquisition UPB is post-paydown and paydown scales with balance, so
# 0 <= HMDA amount - UPB <= max($2k, 1% x HMDA amount); silver stores both in dollars.
_PAYDOWN = pl.col("loan_amount") - pl.col(_UPB)
_PAYDOWN_CAP = pl.max_horizontal(pl.lit(2000), (0.01 * pl.col("loan_amount")).cast(pl.Int64))

#: Every round's candidates must pass these. Beyond the paydown window they drop
#: physically impossible pairings rather than requiring equality on noisy fields:
#: enterprise vs HMDA purchaser, purchase vs refinance, co-borrower presence,
#: owner-occupancy, and 1-4 family vs manufactured housing.
_PAIR_FILTERS: tuple[pl.Expr, ...] = (
    (_PAYDOWN >= 0) & (_PAYDOWN <= _PAYDOWN_CAP),
    ~((pl.col("Enterprise Flag") == 1) & (pl.col("purchaser_type") == 3)),
    ~((pl.col("Enterprise Flag") == 2) & (pl.col("purchaser_type") == 1)),
    ~((pl.col("Purpose of Loan") == 1) & pl.col("loan_purpose").is_in([2, 3])),
    ~((pl.col("Purpose of Loan") == 2) & (pl.col("loan_purpose") == 1)),
    ~((pl.col("co_applicant_sex") == 5) & pl.col("Co-Borrower Gender").is_in([1, 2])),
    ~(pl.col("co_applicant_sex").is_in([1, 2]) & pl.col("Co-Borrower Gender").is_null()),
    ~((pl.col("occupancy_type") == 1) & (pl.col("Occupancy Code") == 2)),
    ~((pl.col("occupancy_type") == 3) & (pl.col("Occupancy Code") == 1)),
    ~((pl.col("property_type") == 1) & (pl.col("Property Type") == 2)),
    ~((pl.col("property_type") == 2) & (pl.col("Property Type") == 1)),
)


def _evidence(ok: pl.Expr, agree: pl.Expr) -> pl.Expr:
    """1 agree, -1 disagree, 0 no evidence (not provided / not applicable)."""
    return pl.when(~ok).then(0).when(agree).then(1).otherwise(-1)


def _round3_evidence() -> list[pl.Expr]:
    """Evidence on the seven fields Round 1 never keys on; codes outside the domain are 0."""
    h_race, f_race = (
        pl.col("applicant_race_1"),
        pl.col("Borrower Race or National Origin 1"),
    )
    h_eth, f_eth = pl.col("applicant_ethnicity"), pl.col("Borrower Ethnicity")
    h_sex, f_sex = pl.col("applicant_sex"), pl.col("Borrower Gender")
    h_nb = pl.when(pl.col("co_applicant_sex") == 5).then(1).otherwise(2)
    f_n = pl.col("Number of Borrowers")
    f_nb = pl.when(f_n == 1).then(1).when(f_n.is_between(2, 6)).then(2).otherwise(None)
    h_pur, f_pur = pl.col("loan_purpose"), pl.col("Purpose of Loan")
    h_occ, f_occ = pl.col("occupancy_type"), pl.col("Occupancy Code")
    h_prop, f_prop = pl.col("property_type"), pl.col("Property Type")
    return [
        _evidence(h_race.is_between(1, 5) & f_race.is_between(1, 5), h_race == f_race),
        _evidence(h_eth.is_in([1, 2]) & f_eth.is_in([1, 2]), h_eth == f_eth),
        _evidence(h_sex.is_in([1, 2]) & f_sex.is_in([1, 2]), h_sex == f_sex),
        _evidence(f_nb.is_not_null(), h_nb == f_nb),
        _evidence(
            f_pur.is_in([1, 2]) & h_pur.is_in([1, 2, 3]),
            ((f_pur == 1) & (h_pur == 1)) | ((f_pur == 2) & (h_pur == 3)),
        ),
        _evidence(h_occ.is_in([1, 2]) & f_occ.is_in([1, 2]), h_occ == f_occ),
        _evidence(
            h_prop.is_in([1, 2, 3]) & f_prop.is_in([1, 2]),
            ((h_prop == 1) & (f_prop == 1)) | ((h_prop == 2) & (f_prop == 2)),
        ),
    ]


# --------------------------------------------------------------------------- #
# Round specs                                                                 #
# --------------------------------------------------------------------------- #
def _unique_cells(pairs: pl.LazyFrame) -> pl.LazyFrame:
    """Keep pairs whose FHFA record and HMDA loan each have exactly one candidate."""
    return drop_ambiguous(pairs, _FHFA_KEY, "HMDAIndex")


#: Round 3 admits a pair only when at least this many of the seven scored fields agree
#: and none disagree. Measured 2026-09-02 on 2016 and 2012: scoring on one disjoint field
#: set and validating on the other gives 97.4-99.5% held-out agreement against a
#: 96.7-99.6% same-year Round 1 ceiling and a 67-74% placebo, and 82-88% of admissions
#: agree on every scored field.
ROUND3_MIN_EVIDENCE = 3


def _demographic_tiebreak(pairs: pl.LazyFrame) -> pl.LazyFrame:
    """Re-score Round 1's contested cells on held-out fields; admit unique bests.

    Takes the candidates `_unique_cells` refuses (a cell with two or more candidates
    on either side) and scores each on the fields Round 1 never keys on: net agreement
    dominates and a smaller loan-amount gap breaks score ties. A pair is admitted only
    when it is the strict unique best for its FHFA record AND for its HMDA loan, both
    judged over the whole contested set at once; the score is integer-valued, so
    `margin_best` with a margin of 1 is exactly that rule. It must also agree on at
    least `ROUND3_MIN_EVIDENCE` fields and disagree on none. Any remaining tie is
    refused, so the rule has no row-order dependence.

    Returns:
        The admitted pairs.
    """
    evidence = _round3_evidence()
    agree = pl.sum_horizontal([(e == 1).cast(pl.Int32) for e in evidence])
    disagree = pl.sum_horizontal([(e == -1).cast(pl.Int32) for e in evidence])
    scored = pairs.filter(
        (pl.len().over(_FHFA_KEY) > 1) | (pl.len().over("HMDAIndex") > 1)
    ).with_columns(
        agree.alias("agree_count"),
        disagree.alias("disagree_count"),
        ((agree - disagree) * 10_000_000 - _PAYDOWN).alias("score"),
    )
    return margin_best(scored, "score", _FHFA_KEY, "HMDAIndex", margin=1, descending=True).filter(
        (pl.col("agree_count") >= ROUND3_MIN_EVIDENCE) & (pl.col("disagree_count") == 0)
    )


#: The resolvers a round names in ``Pre2018Round.resolve``.
RESOLVERS: dict[str, Callable[[pl.LazyFrame], pl.LazyFrame]] = {
    "unique_cells": _unique_cells,
    "tiebreak": _demographic_tiebreak,
}


# --------------------------------------------------------------------------- #
# AMFI deflation (Round 2)                                                    #
# --------------------------------------------------------------------------- #
def _census_string_expr() -> pl.Expr:
    """11-digit state + county + tract GEOID from FHFA silver's three components."""
    return (
        pl.col("state_code").cast(pl.Utf8).str.zfill(2)
        + pl.col("county").cast(pl.Utf8).str.zfill(3)
        + pl.col("census_tract").cast(pl.Utf8).str.zfill(6)
    ).alias("census_string")


#: The AMFI deflation area: the MSA, or for a non-metro record its state. One national
#: modal value for every non-metro loan is not an area adjustment, and a state panel
#: roughly doubles its within-$1k hit rate.
_AMFI_AREA = (
    pl.when(pl.col(_MSA).cast(pl.Utf8) == NON_METRO_MSA)
    .then(pl.lit("state ") + pl.col("census_string").str.slice(0, 2))
    .otherwise(pl.col(_MSA).cast(pl.Utf8))
)


def _modal_amfi(fhfa: pl.DataFrame) -> pl.DataFrame:
    """Build an (area, year) -> AMFI panel from FHFA cleaned data.

    Deflates on ``Area Median Family Income`` (HUD's annual estimate for the loan's
    area), not ``Local Area Median Income`` (the Census tract-income basis, frozen
    2009-2011 and rebased 2000 -> 2010 Census in 2012). On fingerprint-unique candidate
    pairs that agree on every held-out demographic, the AMFI ratio lands inside the $1k
    rounding band 65-87% of the time in every acquisition year 2010-2017, against 1-49%
    for the LAMI ratio and 18-52% for no deflation; it is also monotonic across the 2012
    boundary, so no year-conditional override is needed.

    Takes the modal value per (area, year) so a few bad or sentinel rows do not drop
    the area, after dropping sentinels (>= 9,999,999, <= 0). An (area, year) whose
    plurality is tied has no modal value and is left out.

    Args:
        fhfa: FHFA cleaned data with the MSA code, ``census_string``, ``year`` and AMFI.

    Returns:
        Columns: ``area``, ``year``, ``amfi``.
    """
    panel = fhfa.select(
        _AMFI_AREA.alias("area"),
        pl.col("year"),
        pl.col(_AMFI).cast(pl.Int64).alias("amfi"),
    ).filter(pl.col("amfi").is_not_null() & (pl.col("amfi") < 9_999_999) & (pl.col("amfi") > 0))
    cell = ["area", "year"]
    return (
        panel.group_by([*cell, "amfi"])
        .agg(pl.len().alias("n"))
        .filter(pl.col("n") == pl.col("n").max().over(cell))
        .filter(pl.len().over(cell) == 1)
        .select([*cell, "amfi"])
    )


def _attach_amfi(fhfa: pl.LazyFrame, panel: pl.DataFrame, orig_year: int) -> pl.LazyFrame:
    """Attach each FHFA record's origination-year AMFI; drop records without one."""
    orig = panel.filter(pl.col("year") == orig_year).select(
        "area", pl.col("amfi").alias("__amfi_orig")
    )
    return (
        fhfa.with_columns(_AMFI_AREA.alias("__area"))
        .join(orig.lazy(), left_on="__area", right_on="area", how="inner")
        .filter(pl.col(_AMFI).is_not_null() & (pl.col(_AMFI) > 0))
    )


#: FHFA income deflated to the origination year by AMFI_orig / AMFI_acq and rounded to
#: $1k AFTER scaling (FHFA's published rounding procedure) must land within ±$1k of HMDA.
_DEFLATOR = pl.col("__amfi_orig").cast(pl.Float64) / pl.col(_AMFI).cast(pl.Float64)
_DEFLATED = (pl.col(_FHFA_INCOME).cast(pl.Float64) * _DEFLATOR / 1000.0).round(0) * 1000
_DEFLATED_INCOME_OK = (_DEFLATED.cast(pl.Int64) - pl.col("income").cast(pl.Int64)).abs() <= 1000


# --------------------------------------------------------------------------- #
# Workflow                                                                    #
# --------------------------------------------------------------------------- #
class HMDAFHFAPre2018Matcher:
    """Orchestrates the FHFA pre-2018 matching process (the rounds in `ROUNDS_PRE2018`)."""

    # FHFA silver -> canonical raw-CSV column names, kept so the round logic reads
    # against the public 2016 SF File C dictionary.
    _FHFA_RENAME = {
        "borrower_annual_income": _FHFA_INCOME,
        "enterprise_flag": "Enterprise Flag",
        "record_number": "Record Number",
        "upb_acquisition": _UPB,
        "loan_purpose": "Purpose of Loan",
        "occupancy_code": "Occupancy Code",
        "property_type": "Property Type",
        "borrower_sex": "Borrower Gender",
        "co_borrower_sex": "Co-Borrower Gender",
        "borrower_race_1": "Borrower Race or National Origin 1",
        "borrower_ethnicity": "Borrower Ethnicity",
        "number_of_borrowers": "Number of Borrowers",
        "federal_guarantee": "Federal Guarantee",
        "msa_code": _MSA,
        "local_area_median_income": "Local Area Median Income",
        "area_median_family_income": _AMFI,
    }

    ROUND3_MIN_EVIDENCE = ROUND3_MIN_EVIDENCE

    def __init__(
        self,
        data_folder: Path | str,
        match_folder: Path | str,
        min_year: int = 2008,
        max_year: int = 2017,
    ):
        """Initialize FHFA pre-2018 workflow.

        Args:
            data_folder: Project data root containing `fhfa/silver/sf_c/sf_c_{year}.parquet`
                and `hmda/silver/loans/period_2007_2017/activity_year={year}/file_type=d/`.
            match_folder: Folder where cleaned data and matches will be saved.
            min_year: First FHFA acquisition year. Default 2008, the oldest FHFA `sf_c`
                silver year. Round 2 for `min_year` needs `sf_c_{min_year - 1}` for its
                AMFI panel and skips the year when that file is absent, as it is for
                2008. Pre-2012 years use 2000 Census tracts on both sides.
            max_year: Last FHFA acquisition year (default 2017).
        """
        self.data_folder = Path(data_folder)
        self.match_folder = Path(match_folder)
        self.min_year = min_year
        self.max_year = max_year
        self.rounds = ROUNDS_PRE2018
        self.match_folder.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ cleaning
    def clean_fhfa_data(self, overwrite: bool = False) -> None:
        """Clean FHFA data for pre-2018 matching, drawing from FHFA silver.

        Reads each year's `fhfa/silver/sf_c/sf_c_{year}.parquet` (already limited to
        Fannie and Freddie with missing values cleaned), builds `census_string`,
        renames columns to the canonical raw-CSV names, and writes a combined parquet.

        Args:
            overwrite: Rebuild even if the output file already exists.
        """
        output_file = self.match_folder / _FHFA_FILE
        if not should_process_file(output_file, overwrite):
            logger.info("FHFA cleaned data already exists: %s", output_file)
            return
        logger.info("Cleaning FHFA silver (sf_c) for years %s-%s", self.min_year, self.max_year)

        silver_root = self.data_folder / "fhfa" / "silver" / "sf_c"
        # Int64 so amount and income join cleanly against HMDA silver (FHFA silver
        # stores income as Float64).
        int_cols = {
            "borrower_annual_income",
            "upb_acquisition",
            "local_area_median_income",
            "area_median_family_income",
        }
        select_cols = [pl.col("year"), pl.col("census_string")] + [
            (pl.col(src).cast(pl.Int64) if src in int_cols else pl.col(src)).alias(dst)
            for src, dst in self._FHFA_RENAME.items()
        ]
        frames: list[pl.LazyFrame] = []
        for year in range(self.min_year, self.max_year + 1):
            f = silver_root / f"sf_c_{year}.parquet"
            if not f.exists():
                logger.warning("FHFA silver missing for %s: %s", year, f)
                continue
            frames.append(
                pl.scan_parquet(f)
                .filter(pl.col("enterprise_flag").is_in([1, 2]))
                .with_columns(_census_string_expr())
                .select(select_cols)
            )
        if not frames:
            raise FileNotFoundError(
                f"No FHFA silver files found under {silver_root} for "
                f"years {self.min_year}-{self.max_year}."
            )

        combined = pl.concat(frames, how="diagonal_relaxed").collect(engine="streaming")
        combined.write_parquet(output_file)
        logger.info("Saved cleaned FHFA data (%s rows) to %s", f"{len(combined):,}", output_file)

    def clean_hmda_data(self, overwrite: bool = False) -> None:
        """Clean HMDA data for pre-2018 matching, drawing from HMDA silver.

        Reads `period_2007_2017` file_type ``d`` for ``min_year - 1`` (Round 2's prior
        year) through ``max_year``, keeps originated loans not sold to Ginnie or Farmer
        Mac (`purchaser_type ∉ {2, 4}`), drops non-match columns, and writes a combined
        parquet.

        Args:
            overwrite: Rebuild even if the output file already exists.
        """
        output_file = self.match_folder / _HMDA_FILE
        if not should_process_file(output_file, overwrite):
            # A cache written before `abf2dfc` carries the old synthetic `hmda_row`
            # locator instead of `HMDAIndex`, and every round keys on the latter.
            # Existence alone is not a valid skip condition for it.
            if "HMDAIndex" in pl.scan_parquet(output_file).collect_schema().names():
                logger.info("HMDA cleaned data already exists: %s", output_file)
                return
            logger.warning(
                "Cached HMDA frame at %s predates the HMDAIndex re-key; rebuilding "
                "it rather than matching on a stale locator.",
                output_file,
            )
        logger.info(
            "Cleaning HMDA silver (period_2007_2017, file_type=d) for years %s-%s",
            self.min_year,
            self.max_year,
        )

        silver_root = self.data_folder / "hmda" / "silver" / "loans" / "period_2007_2017"
        drop_cols = {
            "denial_reason_1", "denial_reason_2", "denial_reason_3", "action_taken",
            "msa_md", "state_code", "county_code", "edit_status",
            "application_date_indicator", "rate_spread", "hoepa_status", "lien_status",
            "preapproval", "tract_population", "tract_minority_population_percent",
            "ffiec_msa_md_median_family_income", "tract_to_msa_income_percentage",
            "tract_owner_occupied_units", "tract_one_to_four_family_units", "file_type",
        }  # fmt: skip
        frames: list[pl.LazyFrame] = []
        for year in range(self.min_year - 1, self.max_year + 1):
            try:
                lf = scan_hmda("period_2007_2017", year, file_type="d", root=silver_root)
            except FileNotFoundError as exc:
                if year >= self.min_year:
                    logger.warning("HMDA silver missing for %s: %s", year, exc)
                continue
            lf = lf.filter((pl.col("action_taken") == 1) & ~pl.col("purchaser_type").is_in([2, 4]))
            # A stable HMDAIndex on every year (persisted for 2017, synthesized from the
            # unique identity tuple for 2007-2016), pinned first so the per-year frames
            # share one column order.
            lf = assign_pre2018_hmda_index(lf)
            keep = ["HMDAIndex"] + [
                c for c in lf.collect_schema().names() if c not in drop_cols and c != "HMDAIndex"
            ]
            frames.append(lf.select(keep))
        if not frames:
            raise FileNotFoundError(f"No HMDA silver partitions found under {silver_root}.")

        combined = pl.concat(frames, how="diagonal_relaxed").collect(engine="streaming")
        combined.write_parquet(output_file)
        n_dup = combined.height - combined["HMDAIndex"].n_unique()
        if n_dup:
            logger.warning(
                "HMDA HMDAIndex is not unique: %s duplicate rows in the "
                "combined pre-2018 frame — downstream 1:1 dedup may be affected.",
                f"{n_dup:,}",
            )
        logger.info("Saved cleaned HMDA data (%s rows) to %s", f"{len(combined):,}", output_file)

    def clean_data(self, overwrite: bool = False) -> None:
        """Clean both HMDA and FHFA data.

        Args:
            overwrite: Rebuild even if the output files already exist.
        """
        self.clean_fhfa_data(overwrite=overwrite)
        self.clean_hmda_data(overwrite=overwrite)

    # -------------------------------------------------------------------- engine
    def _amfi_panel(self) -> pl.DataFrame:
        """`_modal_amfi` over the cleaned FHFA frame plus FHFA silver for ``min_year - 1``.

        The AMFI round for `min_year` needs the year before it, which the cleaned frame
        does not hold; it is read straight from silver when that file exists.
        """
        frames = [
            pl.scan_parquet(self.match_folder / _FHFA_FILE).select(
                _MSA, "census_string", "year", _AMFI
            )
        ]
        prior = self.data_folder / "fhfa" / "silver" / "sf_c" / f"sf_c_{self.min_year - 1}.parquet"
        if prior.exists():
            frames.append(
                pl.scan_parquet(prior)
                .filter(pl.col("enterprise_flag").is_in([1, 2]))
                .select(
                    pl.col("msa_code").alias(_MSA),
                    _census_string_expr(),
                    pl.col("year"),
                    pl.col("area_median_family_income").cast(pl.Int64).alias(_AMFI),
                )
            )
        else:
            logger.info(
                "No FHFA silver for %s: no AMFI source for acquisition year %s, which the "
                "AMFI round will skip.",
                self.min_year - 1,
                self.min_year,
            )
        return _modal_amfi(pl.concat(frames, how="diagonal_relaxed").collect())

    def _admit(
        self,
        spec: Pre2018Round,
        year: int,
        claimed: pl.DataFrame | None,
        amfi: pl.DataFrame | None,
        *,
        bridge: bool = False,
    ) -> pl.DataFrame:
        """One round's admitted pairs for FHFA acquisition year ``year``."""
        hmda = pl.scan_parquet(self.match_folder / _HMDA_FILE).filter(
            (pl.col("activity_year") == year - spec.lag)
            & pl.col("census_tract").is_not_null()
            & pl.col("loan_type").is_not_null()
            & pl.col("income").is_not_null()
        )
        if spec.barred_purchasers:
            hmda = hmda.filter(~pl.col("purchaser_type").is_in(list(spec.barred_purchasers)))
        fhfa = pl.scan_parquet(self.match_folder / _FHFA_FILE).filter(pl.col("year") == year)
        if claimed is not None and spec.claimed == "pool":
            hmda = hmda.join(claimed.select("HMDAIndex").lazy(), on="HMDAIndex", how="anti")
            fhfa = fhfa.join(claimed.select(_FHFA_KEY).lazy(), on=_FHFA_KEY, how="anti")
        if amfi is not None:
            fhfa = _attach_amfi(fhfa, amfi, year - spec.lag)
        if bridge:
            # Each FHFA (2010-vintage) tract expands to its related 2000-vintage tracts.
            rel = load_tract_relationship_2000_2010().select(
                pl.col("GEOID10").alias("census_string"),
                pl.col("GEOID00").alias("__tract_2000"),
            )
            fhfa = (
                fhfa.join(rel, on="census_string", how="inner")
                .drop("census_string")
                .rename({"__tract_2000": "census_string"})
            )

        keys = ["census_tract", "loan_type"] + (["income"] if spec.income == "exact" else [])
        pairs = hmda.join(
            fhfa, left_on=keys, right_on=[_JOIN_KEYS[k] for k in keys], how="inner"
        ).filter(*_PAIR_FILTERS)
        if spec.income == "amfi":
            pairs = pairs.filter(_DEFLATED_INCOME_OK)

        admitted = RESOLVERS[spec.resolve](pairs)
        if claimed is not None and spec.claimed == "admissions":
            admitted = admitted.join(
                claimed.select(_FHFA_KEY).lazy(), on=_FHFA_KEY, how="anti"
            ).join(claimed.select("HMDAIndex").lazy(), on="HMDAIndex", how="anti")
        return admitted.select(_CROSSWALK_COLS).collect(engine="streaming")

    def run_round(
        self, number: int, spec: Pre2018Round, crosswalk: pl.DataFrame | None = None
    ) -> pl.DataFrame:
        """Run one round over every acquisition year and append it to ``crosswalk``.

        Writes the cumulative crosswalk to
        ``hmda_fhfa_round{n}_pre2018.parquet`` in the match folder.

        Args:
            number: The round's position, written as ``match_round``.
            spec: The round to run.
            crosswalk: Pairs from earlier rounds (None before Round 1), honored as
                ``spec.claimed`` says.

        Returns:
            ``crosswalk`` with this round's pairs appended, tagged ``match_round``.
            If no acquisition year is eligible, ``crosswalk`` unchanged and nothing
            is written.
        """
        logger.info("Running pre-2018 matching Round %s (%s)", number, spec.description)
        amfi = self._amfi_panel() if spec.income == "amfi" else None
        amfi_years = set() if amfi is None else set(amfi["year"].to_list())

        frames: list[pl.DataFrame] = []
        for year in range(self.min_year, self.max_year + 1):
            if amfi is not None and year - spec.lag not in amfi_years:
                logger.info(
                    "  R%s year %s: skipped (no AMFI panel for %s)",
                    number,
                    year,
                    year - spec.lag,
                )
                continue
            pairs = self._admit(spec, year, crosswalk, amfi)
            logger.info("  R%s year %s: %s new matches", number, year, f"{len(pairs):,}")
            if spec.vintage_bridge and needs_vintage_bridge_2000_2010(year - spec.lag, year):
                # Additive: both pools exclude this year's identity pairs as claims.
                claims = pairs.select(*_FHFA_KEY, "HMDAIndex")
                if crosswalk is not None:
                    claims = pl.concat([crosswalk.select(claims.columns), claims])
                bridged = self._admit(spec, year, claims, amfi, bridge=True)
                logger.info(
                    "  R%s year %s: %s additional matches via the 2000↔2010 tract bridge",
                    number,
                    year,
                    f"{len(bridged):,}",
                )
                pairs = pl.concat([pairs, bridged], how="diagonal")
            frames.append(pairs)

        if not frames and crosswalk is not None:
            logger.info("Round %s complete: 0 new matches (no eligible years)", number)
            return crosswalk
        new = pl.concat(frames, how="diagonal_relaxed").with_columns(
            pl.lit(number, dtype=pl.UInt8).alias("match_round")
        )
        full = (new if crosswalk is None else pl.concat([crosswalk, new], how="diagonal")).sort(
            _FHFA_KEY
        )
        full.write_parquet(self.match_folder / f"hmda_fhfa_round{number}_pre2018.parquet")
        logger.info(
            "Round %s complete: %s new matches (%s total)",
            number,
            f"{len(new):,}",
            f"{len(full):,}",
        )
        return full

    def run_all_rounds(self, overwrite: bool = False) -> pl.DataFrame:
        """Clean the inputs, then execute every round in order.

        Args:
            overwrite: Rebuild the cleaned intermediates even if they exist. Needed
                after an upstream HMDA or FHFA silver rebuild -- the cleaned frames are
                this workflow's own cache and nothing else invalidates them.

        Returns:
            Complete crosswalk from all rounds.
        """
        self.clean_data(overwrite=overwrite)
        crosswalk: pl.DataFrame | None = None
        for number, spec in enumerate(self.rounds, start=1):
            crosswalk = self.run_round(number, spec, crosswalk)
        assert crosswalk is not None  # ROUNDS_PRE2018 is never empty
        logger.info("All rounds completed!")
        return crosswalk


def run_matching_pre2018(
    years: list[int] | None = None,
    save: bool = True,
    overwrite: bool = False,
) -> dict:
    """Run the full pre-2018 (2008-2017) matching workflow.

    The pre-2018 analogue of `matching_post2018.run_matching`. Writes the published
    crosswalk to ``Config.get_crosswalk_path("pre2018", years)``, which is where
    `validation.load_crosswalk_pre2018` looks for it first.

    Args:
        years: Years to process. Defaults to 2008 through 2017.
        save: Whether to write the crosswalk to the published location.
        overwrite: Rebuild the cleaned FHFA/HMDA intermediates even when they
            already exist. Required after any upstream HMDA or FHFA silver
            rebuild — the cleaned frames are the workflow's own cache, not a
            medallion layer, so nothing else invalidates them.

    Returns:
        Per-round counts plus the output path.
    """
    from mortgage_data_manager.matching.match_hmda_fhfa.config import (
        HMDAFHFAMatchingConfig as Config,
    )

    years_list = years or list(range(2008, 2018))
    min_yr, max_yr = min(years_list), max(years_list)

    workflow = HMDAFHFAPre2018Matcher(
        data_folder=Config.DATA_DIR,
        match_folder=Config.HMDA_FHFA_MATCHING_DIR / "pre2018_silver",
        min_year=min_yr,
        max_year=max_yr,
    )
    crosswalk = workflow.run_all_rounds(overwrite=overwrite)

    stats: dict = {"total": len(crosswalk)}
    for number in range(1, len(workflow.rounds) + 1):
        stats[f"round{number}"] = int((crosswalk["match_round"] == number).sum())

    if save:
        Config.ensure_directories()
        out = Config.get_crosswalk_path("pre2018", years_list)
        with atomic_output(out) as tmp:
            crosswalk.write_parquet(tmp)
        logger.info("Saved pre-2018 crosswalk (%s pairs) to %s", f"{len(crosswalk):,}", out)
        stats["output_path"] = str(out)

    return stats
