"""HMDA-FHFA post-2018 matching: one round engine for the rounds in ``rounds.py``.

Every round joins an HMDA pool to one FHFA acquisition year on census tract, loan amount,
loan type and occupancy (plus, per round, purchaser type and a 12.5 bp rate bucket),
screens the candidate pairs on the fields the join does not key on, and keeps the pairs
that are one-to-one on both sides. Rounds run in order, each on the loans earlier rounds
left unclaimed. What differs between rounds is data (`Round`, tuple `ROUNDS_POST2018`).

Same-year rounds break same-fingerprint ties by closest income and resolve 1:1 per year;
cross-year rounds pool every year pair and resolve 1:1 once.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import atomic_output
from mortgage_data_manager.core.reference import (
    load_tract_relationship,
    needs_vintage_bridge,
    to_planning_region,
)
from mortgage_data_manager.core.types import null_sentinels
from mortgage_data_manager.matching.match_hmda_fhfa.config import HMDAFHFAMatchingConfig
from mortgage_data_manager.matching.match_hmda_fhfa.rounds import (
    ROUNDS_POST2018,
    Pool,
    Round,
)
from mortgage_data_manager.matching.resolve import drop_ambiguous
from mortgage_data_manager.matching.terms import freddie_term_diff_expr
from mortgage_data_manager.sources.fhfa.config import FHFAConfig
from mortgage_data_manager.sources.hmda.load import best_file_type, scan_hmda

logger = get_logger(__name__)

# Data paths
FHFA_SILVER = FHFAConfig.FHFA_SILVER_DIR / "sf_c"

#: Activity years the post-2018 matcher covers by default.
MIN_YEAR, MAX_YEAR = HMDAFHFAMatchingConfig.ERA_YEARS["post2018"]

#: The FHFA side's composite loan key, as it is spelled after the join.
FHFA_KEY = ["fhfa_year", "enterprise_flag", "record_number"]

#: Suffix applied to FHFA columns whose names collide with HMDA's on the join.
#: Explicit rather than polars' default ``_right`` so the post-merge filters name
#: the side they mean (``loan_purpose_fhfa``, ``loan_term_fhfa``,
#: ``discount_points_fhfa``) and a polars default change cannot silently repoint
#: them at the HMDA column.
FHFA_SUFFIX = "_fhfa"

_POOL_FILTERS: dict[Pool, pl.Expr] = {
    "pt0": pl.col("purchaser_type") == 0,
    "gse": pl.col("purchaser_type").is_in([1, 3]),
    "secondary": pl.col("purchaser_type") >= 5,
}

# Columns each year pair returns, and the crosswalk keeps (plus match_round).
_PAIR_COLS = [
    "HMDAIndex", "activity_year", "fhfa_year", "enterprise_flag", "record_number",
    "purchaser_type", "rate_diff", "term_diff", "dti_relaxed", "dti_gap",
]  # fmt: skip
_CROSSWALK_COLS = [c for c in _PAIR_COLS if c not in ("rate_diff", "term_diff")]

#: HMDA columns whose missing-value codes are nulled before matching: -99999 (numeric
#: fields) and 8888 / 9999 (demographic codes).
_HMDA_NUMERIC_NA = [
    "loan_term", "interest_rate", "intro_rate_period", "debt_to_income_ratio",
    "combined_loan_to_value_ratio", "discount_points", "income", "property_value",
]  # fmt: skip
_HMDA_CATEGORICAL_NA = [
    "applicant_age", "co_applicant_age", "applicant_sex", "co_applicant_sex",
    "applicant_ethnicity_1", "co_applicant_ethnicity_1", "applicant_race_1",
    "co_applicant_race_1",
]  # fmt: skip

#: DTI bin codes both sides share: <20, 20-30, 30-36, 50-60, >60 (36-49 are integers).
_DTI_BINS = [10, 20, 30, 50, 60]


@dataclass
class MatchResult:
    """Result of the matching workflow.

    Attributes:
        matched_hmda: DataFrame with matched HMDAIndex values.
        matched_fhfa: DataFrame with matched FHFA keys (fhfa_year, enterprise_flag, record_number).
        round_results: List of DataFrames with match results per round.
        stats: Statistics dictionary with round-by-round and summary stats.
    """

    matched_hmda: pl.DataFrame
    matched_fhfa: pl.DataFrame
    round_results: list[pl.DataFrame] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def load_fhfa_year(year: int) -> pl.LazyFrame:
    """Load FHFA data for a single year as LazyFrame.

    Creates derived columns needed for matching:
    - census_string: 11-char FIPS code (state + county + tract)
    - NoteRate125: Interest rate bucketed to 0.125 increments
    - PurchaserType: Maps enterprise_flag to HMDA purchaser_type
    - fhfa_year: Year column for cross-year matching

    Args:
        year: Activity year.

    Returns:
        That year's FHFA records with the derived matching columns added.
    """
    fhfa_file = FHFA_SILVER / f"sf_c_{year}.parquet"
    lf = pl.scan_parquet(fhfa_file).with_columns(
        [
            # Create census tract string (state 2 + county 3 + tract 6 = 11 chars)
            (
                pl.col("state_code").cast(pl.Int64).cast(pl.Utf8).str.zfill(2)
                + pl.col("county").cast(pl.Int64).cast(pl.Utf8).str.zfill(3)
                + pl.col("census_tract").cast(pl.Int64).cast(pl.Utf8).str.zfill(6)
            ).alias("census_string"),
            # Bucket interest rate to 0.125 increments
            ((pl.col("interest_rate_at_origination") * 8).round() * 125)
            .cast(pl.Int32)
            .alias("NoteRate125"),
            # Map enterprise_flag to HMDA purchaser_type
            pl.when(pl.col("enterprise_flag") == 1)
            .then(1)  # Fannie Mae
            .when(pl.col("enterprise_flag") == 2)
            .then(3)  # Freddie Mac
            .otherwise(None)
            .alias("PurchaserType"),
            pl.lit(year).cast(pl.Int32).alias("fhfa_year"),
        ]
    )
    # Normalize CT tracts to planning-region convention. FHFA switched CT county
    # coding to planning regions in 2024 while HMDA did so in 2024 too; via the
    # FHFA[Y] <-> HMDA[Y-1] directionality, FHFA 2024 (PR) joins HMDA 2023
    # (county-coded) and the census_string never agrees. Idempotent (PR/non-CT
    # pass through), and orthogonal to the 2010<->2020 vintage bridge (that
    # boundary is 2021/2022, where these CT GEOIDs aren't in the crosswalk).
    return to_planning_region(lf, tract_col="census_string")


def load_hmda_year(year: int, pool: Sequence[Pool]) -> tuple[pl.LazyFrame, str | None]:
    """Load one year of HMDA originations, restricted to a purchaser-type pool.

    Args:
        year: Activity year to load.
        pool: Purchaser-type pools to keep (see `Pool`).

    Returns:
        LazyFrame and the release (file_type) it was read from.
    """
    best_type = best_file_type("post2018", year)
    lf = scan_hmda("post2018", year, file_type=best_type)
    lf = lf.filter(pl.col("action_taken") == 1)  # Originated loans only

    # Filter out 5+ unit properties (FHFA only covers 1-4 unit)
    lf = lf.filter(pl.col("total_units") <= 4)

    # HMDA missing-value codes -> null
    names = lf.collect_schema().names()
    lf = lf.with_columns(
        [null_sentinels(pl.col(c), [-99999]).alias(c) for c in _HMDA_NUMERIC_NA if c in names]
        + [
            null_sentinels(pl.col(c), [8888, 9999]).alias(c)
            for c in _HMDA_CATEGORICAL_NA
            if c in names
        ]
    )

    if pool:
        combined = _POOL_FILTERS[pool[0]]
        for name in pool[1:]:
            combined = combined | _POOL_FILTERS[name]
        lf = lf.filter(combined)

    lf = lf.with_columns(
        ((pl.col("interest_rate") * 8).round() * 125).cast(pl.Int32).alias("NoteRate125"),
        pl.lit(year).cast(pl.Int32).alias("activity_year"),
    )

    # Normalize CT tracts to planning-region convention so cross-year CT joins
    # against FHFA line up across the 2023/2024 coding cut (see load_fhfa_year).
    lf = to_planning_region(lf, tract_col="census_tract")

    return lf, best_type


def _purpose_category(code: pl.Expr, purchase: int, refi: int, cash_out: int) -> pl.Expr:
    """Map one side's loan-purpose code to Purchase / Refi / CashOut / Other."""
    return (
        pl.when(code == purchase)
        .then(pl.lit("Purchase"))
        .when(code == refi)
        .then(pl.lit("Refi"))
        .when(code == cash_out)
        .then(pl.lit("CashOut"))
        .otherwise(pl.lit("Other"))
    )


def apply_post_merge_filters(merged: pl.LazyFrame, round_: Round) -> pl.LazyFrame:
    """Screen joined candidate pairs on the fields the join does not key on.

    Row-wise checks come first and run as one filter. The DTI gate runs last because its
    skip-when-unique relaxation counts candidates in what the other checks left.

    Args:
        merged: Merged HMDA-FHFA data.
        round_: The round whose checks apply.

    Returns:
        Filtered merged data, with ``rate_diff``, ``term_diff``, ``dti_relaxed`` and
        ``dti_gap`` added.
    """
    # term_diff is zeroed when FHFA's Freddie term-reporting conventions explain the gap
    # (ceiling / product-envelope binning, matching/terms.py): a true 240-month loan FHFA
    # reports as 360 is the same loan. The post-unique term check reuses term_diff.
    merged = merged.with_columns(
        (pl.col("interest_rate") - pl.col("interest_rate_at_origination")).abs().alias("rate_diff"),
        freddie_term_diff_expr("loan_term", "loan_term_fhfa").alias("term_diff"),
    )
    keep = [
        (pl.col("rate_diff") <= round_.rate_tol) | pl.col("rate_diff").is_null(),
        (pl.col("term_diff") <= round_.term_tol) | pl.col("term_diff").is_null(),
    ]
    # date_of_mortgage_note: 1 = originated in the acquisition year, 2 = the year before.
    if round_.current_year_orig:
        keep.append(pl.col("date_of_mortgage_note") == 1)

    # FHFA purpose 1 purchase, 2 refinance, 7 cash-out; HMDA 1, 31, 32.
    fhfa_purpose, hmda_purpose = pl.col("loan_purpose_fhfa"), pl.col("loan_purpose")
    if round_.purpose == "coarse":
        keep += [
            ~((fhfa_purpose == 1) & hmda_purpose.is_in([31, 32])),
            ~(fhfa_purpose.is_in([2, 7]) & (hmda_purpose == 1)),
        ]
    elif round_.purpose == "strict":
        f_cat = _purpose_category(fhfa_purpose, 1, 2, 7)
        h_cat = _purpose_category(hmda_purpose, 1, 31, 32)
        keep.append((f_cat == h_cat) | (f_cat == "Other") | (h_cat == "Other"))

    is_cross_year = pl.col("activity_year") != pl.col("fhfa_year")
    is_prior_year_orig = pl.col("date_of_mortgage_note") == 2
    # A same-year origination sold to a GSE belongs to an earlier, same-year round.
    if round_.prior_year_for_gse:
        is_gse = pl.col("purchaser_type").is_in([1, 3])
        keep.append(~is_gse | ~is_cross_year | is_prior_year_orig)
    # A loan not sold in its HMDA year (pt 0) can only reach FHFA as a later acquisition.
    if round_.cross_year_for_pt0:
        is_pt0 = pl.col("purchaser_type") == 0
        keep.append(~is_pt0 | (is_cross_year & is_prior_year_orig))

    if round_.demographics:
        # NA codes: HMDA age 8888/9999, FHFA 99/999. Sex compares only male (1) / female (2).
        hmda_sex = pl.when(pl.col("applicant_sex").is_in([1, 2])).then(pl.col("applicant_sex"))
        fhfa_sex = pl.when(pl.col("borrower_sex").is_in([1, 2])).then(pl.col("borrower_sex"))
        keep += [
            (pl.col("borrower_age_62_plus") == pl.col("applicant_age_above_62"))
            | pl.col("borrower_age_62_plus").is_null()
            | pl.col("applicant_age_above_62").is_null(),
            (pl.col("age_borrower") == pl.col("applicant_age"))
            | pl.col("age_borrower").is_null()
            | pl.col("age_borrower").is_in([99, 999])
            | pl.col("applicant_age").is_null()
            | pl.col("applicant_age").is_in([8888, 9999]),
            (fhfa_sex == hmda_sex) | fhfa_sex.is_null() | hmda_sex.is_null(),
        ]
    merged = merged.filter(*keep)

    # DTI gate — SURGICAL form (skip-when-unique). Both sides bin 10/20/30/50/60, report
    # 36-49 as integers, and code missing as 99 (FHFA) / -99999 (HMDA). HMDA
    # applicant-stage and FHFA delivery DTI disagree beyond tolerance on a real share of
    # TRUE pairs (82% of clean chain-diagnosed misses die here), so a DTI-failing
    # candidate is kept when it is fingerprint-unique on both sides of this candidate set
    # (the gate does no disambiguation work there) and held-out discount points (the same
    # Closing-Disclosure dollar concept on both sides) don't clearly disagree.
    # The gate runs in every round; only the skip-when-unique relaxation is per round.
    fhfa_dti, hmda_dti = pl.col("dti_ratio"), pl.col("debt_to_income_ratio")
    has_both_dti = (
        fhfa_dti.is_not_null()
        & ~fhfa_dti.is_in([99, -99999])
        & hmda_dti.is_not_null()
        & ~hmda_dti.is_in([99, -99999])
    )
    both_binned = fhfa_dti.is_in(_DTI_BINS) & hmda_dti.is_in(_DTI_BINS)
    both_numeric = fhfa_dti.is_between(36, 49) & hmda_dti.is_between(36, 49)
    dti_pass = (
        ~has_both_dti
        | (both_binned & (fhfa_dti == hmda_dti))
        | (both_numeric & ((fhfa_dti - hmda_dti).abs() <= round_.dti_tol))
        | (~both_binned & ~both_numeric)
    )
    if round_.dti_skip_when_unique:
        fp_unique = (pl.len().over("HMDAIndex") == 1) & (pl.len().over(FHFA_KEY) == 1)
    else:
        fp_unique = pl.lit(False)

    # Lenders with known dp unit conventions are dp-unusable, never dp-rejected.
    dp_usable = (
        (pl.col("discount_points") > 0).fill_null(False)
        & (pl.col("discount_points_fhfa") > 0).fill_null(False)
        & ~pl.col("lei").is_in(DP_UNIT_ANOMALY_LEIS).fill_null(False)
    )
    dp_diff = (pl.col("discount_points") - pl.col("discount_points_fhfa")).abs()
    dp_agree = (dp_diff / pl.max_horizontal("discount_points", "discount_points_fhfa") <= 0.10) | (
        dp_diff <= 10
    )
    dp_reject = dp_usable & ~dp_agree

    return (
        merged.with_columns(
            (~dti_pass & fp_unique & ~dp_reject).alias("dti_relaxed"),
            dti_pass.alias("_dti_pass"),
        )
        .filter(pl.col("_dti_pass") | pl.col("dti_relaxed"))
        .drop("_dti_pass")
        # Persisted DTI gap (HMDA - FHFA, shared code space; null when either side is
        # missing) so crosswalk consumers can filter or study relaxed pairs.
        .with_columns(
            pl.when(has_both_dti)
            .then((hmda_dti - fhfa_dti).cast(pl.Int32))
            .otherwise(None)
            .alias("dti_gap")
        )
    )


def apply_post_unique_filters(df: pl.DataFrame, tolerances: tuple[float, int]) -> pl.DataFrame:
    """Apply a round's tighter post-1:1 rate and term tolerances.

    Args:
        df: Matched data after uniqueness constraint.
        tolerances: ``(rate, term)`` tolerances.

    Returns:
        Filtered matches meeting quality criteria.
    """
    rate_tol, term_tol = tolerances
    return df.filter(
        (pl.col("rate_diff") <= rate_tol) | pl.col("rate_diff").is_null(),
        (pl.col("term_diff") <= term_tol) | pl.col("term_diff").is_null(),
    )


def apply_uniqueness(merged: pl.LazyFrame) -> pl.LazyFrame:
    """Keep only 1:1 matches (unique on both sides).

    Args:
        merged: Merged data to filter.

    Returns:
        Data with only unique matches on both HMDA and FHFA sides.
    """
    return drop_ambiguous(merged, "HMDAIndex", FHFA_KEY)


def apply_quality_selection(merged: pl.LazyFrame) -> pl.LazyFrame:
    """Select best matches when multiple candidates exist.

    Uses multi-metric tie-breaking based on rate difference:
    - Tight match: rate_diff <= 0.01
    - Good match: rate_diff <= 0.05

    For each HMDA/FHFA loan with multiple potential matches, if any match
    is "tight", drop all non-tight matches. This preserves all tight matches
    for the uniqueness filter to resolve.

    Args:
        merged: Merged data with potential duplicate matches.

    Returns:
        Filtered data with lower-quality duplicates removed.
    """
    # Define thresholds
    rate_tight = 0.01
    rate_good = 0.05

    # Calculate quality indicators
    result = merged.with_columns(
        [
            (pl.col("rate_diff").abs() <= rate_tight).fill_null(True).alias("_tight_rate"),
            (pl.col("rate_diff").abs() <= rate_good).fill_null(True).alias("_good_rate"),
        ]
    )

    # Check if each HMDA/FHFA loan has any tight match
    result = result.with_columns(
        [
            pl.col("_tight_rate").max().over(["HMDAIndex"]).alias("_hmda_has_tight"),
            pl.col("_tight_rate").max().over(FHFA_KEY).alias("_fhfa_has_tight"),
        ]
    )

    # Filter: if loan has tight match, keep only good or better matches
    # (This drops poor matches when better ones exist)
    result = result.filter((~pl.col("_hmda_has_tight")) | pl.col("_good_rate")).filter(
        (~pl.col("_fhfa_has_tight")) | pl.col("_good_rate")
    )

    # Further tighten: if loan has tight match, keep only tight matches
    result = result.filter((~pl.col("_hmda_has_tight")) | pl.col("_tight_rate")).filter(
        (~pl.col("_fhfa_has_tight")) | pl.col("_tight_rate")
    )

    # Clean up temporary columns
    return result.drop(["_tight_rate", "_good_rate", "_hmda_has_tight", "_fhfa_has_tight"])


# Lenders whose discount_points carry a unit convention error on one side
# (FHFA in whole points, or HMDA in points), so a dollar comparison is
# meaningless — dp is treated as unusable for them, never as a rejection.
DP_UNIT_ANOMALY_LEIS = [
    "DRMSV1Q0EKMEXLAU1P80",  # Citizens Bank NA (FHFA side in whole points, 2018-2024)
    "5493001WHVQBGRSWEU75",  # Homebridge Financial (HMDA side in points, 2019-2021)
    "5493005EYZOUVJUXAX38",  # Lakeview Community Capital (HMDA side in points, 2021-2022)
]

INCOME_TIEBREAK_MAX_DIFF = 1000.0


def apply_income_tiebreak(merged: pl.LazyFrame) -> pl.LazyFrame:
    """Break same-fingerprint ties by closest borrower income, only on a near-exact income match.

    When several candidates share the join fingerprint, the rate-based quality selection cannot
    separate them and the 1:1 uniqueness filter would drop them all as non-unique. Borrower income
    (held out of the match keys) resolves the true pair among such ties. Keep, per HMDA loan and per
    FHFA record, the candidate with the smallest |income difference| — but ONLY when that winning
    difference is within ``INCOME_TIEBREAK_MAX_DIFF`` ($1k, one rounding unit since both sources are
    $1k-granular). A weak income match is not trusted to break a tie: a genuinely cross-year loan has
    no true same-year twin, so its closest same-year income is usually far, and letting it win here
    would steal the loan from its correct cross-year round (which runs after this one). Already-unique
    matches are kept regardless of income (there is no tie to break), so recall is never reduced below
    baseline. Ties the gate declines are left for the uniqueness filter to drop, exactly as before, so
    the loan falls through to a later round. Gated to same-year rounds by the caller because FHFA
    income is definitionally inflated for prior-year acquisitions.

    Args:
        merged: The merged candidate pairs.

    Returns:
        The candidates with income-resolved ties kept and declined ties left in place.
    """
    scored = merged.with_columns(
        pl.when(
            (pl.col("income") > 0)
            & (pl.col("borrower_annual_income") > 0)
            & (pl.col("borrower_annual_income") < 9_999_999)
        )
        .then((pl.col("income") - pl.col("borrower_annual_income")).abs().cast(pl.Float64))
        .otherwise(1e15)
        .alias("_income_diff")
    ).with_columns(
        pl.col("_income_diff").min().over(["HMDAIndex"]).alias("_min_hmda"),
        pl.col("_income_diff").min().over(FHFA_KEY).alias("_min_fhfa"),
        pl.len().over(["HMDAIndex"]).alias("_n_hmda"),
        pl.len().over(FHFA_KEY).alias("_n_fhfa"),
    )
    already_unique = (pl.col("_n_hmda") == 1) & (pl.col("_n_fhfa") == 1)
    strong_tiebreak = (
        (pl.col("_income_diff") == pl.col("_min_hmda"))
        & (pl.col("_income_diff") == pl.col("_min_fhfa"))
        & (pl.col("_income_diff") <= INCOME_TIEBREAK_MAX_DIFF)
    )
    return scored.filter(already_unique | strong_tiebreak).drop(
        ["_income_diff", "_min_hmda", "_min_fhfa", "_n_hmda", "_n_fhfa"]
    )


def get_fhfa_counts(years: list[int] | None = None) -> dict[int, int]:
    """Get FHFA loan counts by year.

    Args:
        years: Activity years to include.

    Returns:
        FHFA loan counts keyed by year.
    """
    if years is None:
        years = list(range(MIN_YEAR, MAX_YEAR + 1))
    return {
        year: pl.scan_parquet(FHFA_SILVER / f"sf_c_{year}.parquet")
        .select(pl.len())
        .collect()
        .item()
        for year in years
    }


class HMDAFHFAMatcher:
    """Run `ROUNDS_POST2018` (or any rounds) in order over a span of years.

    Args:
        rounds: Rounds to run, in order; a round's ``match_round`` is its 1-based
            position. Defaults to `ROUNDS_POST2018`.
        years: Years to process. Defaults to MIN_YEAR through MAX_YEAR.

    Example:
        >>> result = HMDAFHFAMatcher().run()
        >>> print(f"Matched {len(result.matched_fhfa):,} FHFA loans")
    """

    def __init__(
        self,
        rounds: Sequence[Round] | None = None,
        years: list[int] | None = None,
    ):
        self.rounds = rounds or ROUNDS_POST2018
        self.years = years or list(range(MIN_YEAR, MAX_YEAR + 1))
        self._matched_hmda = pl.DataFrame(schema={"HMDAIndex": pl.Utf8})
        self._matched_fhfa = pl.DataFrame(
            schema={"fhfa_year": pl.Int32, "enterprise_flag": pl.Int64, "record_number": pl.Int64}
        )
        self._round_results: list[pl.DataFrame] = []
        self._stats: dict = {"rounds": {}, "summary": {}}

    def run(self) -> MatchResult:
        """Execute all rounds sequentially.

        Returns:
            Dataclass containing matched data and statistics.
        """
        fhfa_counts = get_fhfa_counts(self.years)
        total_fhfa = sum(fhfa_counts.values())
        self._stats["total_fhfa"] = total_fhfa
        self._stats["fhfa_by_year"] = fhfa_counts

        for number, round_ in enumerate(self.rounds, start=1):
            self._run_round(number, round_)

        total_matched = len(self._matched_fhfa)
        self._stats["summary"] = {
            "total_matched": total_matched,
            "total_fhfa": total_fhfa,
            "fhfa_match_rate": total_matched / total_fhfa * 100 if total_fhfa > 0 else 0,
        }
        self._log_summary()
        return MatchResult(
            matched_hmda=self._matched_hmda,
            matched_fhfa=self._matched_fhfa,
            round_results=self._round_results,
            stats=self._stats,
        )

    def _run_round(self, n: int, round_: Round) -> None:
        """Run one round over every year (pair) and record its matches.

        Args:
            n: The round's position, written as ``match_round``.
            round_: The round to run.
        """
        logger.info("Starting Round %s: %s", n, round_.description)
        stats: dict[str, Any] = {
            "round": n,
            "description": round_.description,
            "by_year": {},
            "total": 0,
            "same_year": 0,
            "cross_year": 0,
        }
        frames: list[pl.DataFrame] = []

        if not round_.cross_year:
            # Same year: each year resolved independently.
            for year in self.years:
                matches = self._match_year_pair(round_, hmda_year=year, fhfa_year=year)
                if matches is not None:
                    frames.append(matches)
                    stats["by_year"][year] = len(matches)
                    stats["total"] += len(matches)
                    stats["same_year"] += len(matches)
                    logger.info("R%s %s: %s matches", n, year, f"{len(matches):,}")
        else:
            # Cross year: HMDA[Y] against every FHFA year >= Y, pooled, then 1:1 once.
            for hmda_year in self.years:
                logger.info("R%s: HMDA year %s", n, hmda_year)
                for fhfa_year in range(hmda_year, max(self.years) + 1):
                    matches = self._match_year_pair(round_, hmda_year, fhfa_year)
                    if matches is not None:
                        frames.append(matches)
                        stats["by_year"][f"{hmda_year}->{fhfa_year}"] = len(matches)
                        logger.debug("  FHFA %s: %s potential", fhfa_year, f"{len(matches):,}")
            if frames:
                combined = pl.concat(frames, how="diagonal")
                logger.info("R%s: %s potential matches before uniqueness", n, f"{len(combined):,}")
                unique = apply_uniqueness(apply_quality_selection(combined.lazy())).collect()
                same_year = unique.filter(pl.col("activity_year") == pl.col("fhfa_year")).height
                stats["same_year"] = same_year
                stats["cross_year"] = len(unique) - same_year
                stats["total"] = len(unique)
                frames = [unique]
                logger.info(
                    "R%s: %s unique matches (%s same-year, %s cross-year)",
                    n,
                    f"{len(unique):,}",
                    f"{same_year:,}",
                    f"{len(unique) - same_year:,}",
                )

        if frames:
            matched = pl.concat(frames, how="diagonal")
            if round_.post_unique is not None:
                before = len(matched)
                matched = apply_post_unique_filters(matched, round_.post_unique)
                stats["post_unique"] = {
                    "before": before,
                    "after": len(matched),
                    "retained_pct": len(matched) / before * 100 if before > 0 else 0,
                }
                stats["total"] = len(matched)
                logger.info(
                    "R%s: Post-unique filter: %s -> %s (%.1f%% retained)",
                    n,
                    f"{before:,}",
                    f"{len(matched):,}",
                    stats["post_unique"]["retained_pct"],
                )
            self._update_matched(matched)
            self._round_results.append(
                matched.select(_CROSSWALK_COLS).with_columns(
                    pl.lit(n).cast(pl.UInt8).alias("match_round")
                )
            )

        self._stats["rounds"][n] = stats

    def _match_year_pair(
        self, round_: Round, hmda_year: int, fhfa_year: int
    ) -> pl.DataFrame | None:
        """Match one HMDA year against one FHFA year under a round's rules.

        Args:
            round_: The round being run.
            hmda_year: HMDA activity year.
            fhfa_year: FHFA year.

        Returns:
            Matched records, or None if there are none.
        """
        # The bridge only adds pairs that cross the 2010↔2020 tract vintage boundary
        # (HMDA <= 2021, FHFA >= 2022); other pairs are covered by round 3.
        if round_.vintage_bridge and not needs_vintage_bridge(hmda_year, fhfa_year):
            return None

        hmda_lf, _ = load_hmda_year(hmda_year, round_.pool)
        fhfa_lf = load_fhfa_year(fhfa_year)
        if round_.vintage_bridge:
            fhfa_lf = self._apply_vintage_bridge(fhfa_lf)
        hmda_lf = self._exclude_matched_hmda(hmda_lf)
        fhfa_lf = self._exclude_matched_fhfa(fhfa_lf)

        merged = self._merge(hmda_lf, fhfa_lf, round_)
        filtered = apply_quality_selection(apply_post_merge_filters(merged, round_))

        # Same-year rounds: break same-fingerprint ties by closest income, then resolve
        # 1:1 here. Income only agrees same year (FHFA income is inflated for prior-year
        # acquisitions), so cross-year rounds resolve over their pooled pairs instead.
        if not round_.cross_year:
            filtered = apply_uniqueness(apply_income_tiebreak(filtered))

        result = filtered.select(_PAIR_COLS).collect()
        return result if len(result) > 0 else None

    def _exclude_matched_hmda(self, hmda_lf: pl.LazyFrame) -> pl.LazyFrame:
        """Exclude already matched HMDA records via anti-join."""
        if len(self._matched_hmda) > 0:
            hmda_lf = hmda_lf.join(
                self._matched_hmda.lazy().select("HMDAIndex"), on="HMDAIndex", how="anti"
            )
        return hmda_lf

    def _exclude_matched_fhfa(self, fhfa_lf: pl.LazyFrame) -> pl.LazyFrame:
        """Exclude already matched FHFA records via anti-join."""
        if len(self._matched_fhfa) > 0:
            fhfa_lf = fhfa_lf.join(
                self._matched_fhfa.lazy().select(FHFA_KEY), on=FHFA_KEY, how="anti"
            )
        return fhfa_lf

    def _apply_vintage_bridge(self, fhfa_lf: pl.LazyFrame) -> pl.LazyFrame:
        """Translate FHFA's 2020-vintage census_string to 2010-vintage candidates.

        Inner-joins FHFA against the Census Bureau's 2010↔2020 tract
        relationship file on `census_string == GEOID_TRACT_20`, then replaces
        `census_string` with the corresponding `GEOID_TRACT_10`. Each FHFA
        record is duplicated once per overlapping 2010 tract — 66% of records
        map to one candidate, 34% to multiple.

        Returns:
            The FHFA side re-keyed to 2010-vintage tracts, one row per overlapping tract.
        """
        rel = load_tract_relationship().select(
            pl.col("GEOID_TRACT_20").alias("census_string"),
            pl.col("GEOID_TRACT_10").alias("census_string_2010"),
        )
        return (
            fhfa_lf.join(rel, on="census_string", how="inner")
            .drop("census_string")
            .rename({"census_string_2010": "census_string"})
        )

    def _merge(self, hmda_lf: pl.LazyFrame, fhfa_lf: pl.LazyFrame, round_: Round) -> pl.LazyFrame:
        """Join HMDA and FHFA on the round's keys.

        Args:
            hmda_lf: HMDA data.
            fhfa_lf: FHFA data.
            round_: The round, which sets the purchaser and rate keys.

        Returns:
            Merged data.
        """
        left = ["census_tract", "loan_amount", "loan_type", "occupancy_type"]
        right = ["census_string", "note_amount", "federal_guarantee", "occupancy_code"]
        if round_.purchaser_key:
            left, right = ["purchaser_type", *left], ["PurchaserType", *right]

        if round_.rate_key:
            left, right = [*left, "NoteRate125"], [*right, "NoteRate125"]
        return hmda_lf.join(fhfa_lf, left_on=left, right_on=right, how="inner", suffix=FHFA_SUFFIX)

    def _update_matched(self, matches: pl.DataFrame) -> None:
        """Add a round's matches to the claimed sets.

        Args:
            matches: The round's matches.
        """
        if len(matches) == 0:
            return
        self._matched_hmda = pl.concat(
            [self._matched_hmda, matches.select("HMDAIndex")], how="diagonal"
        ).unique()
        new_fhfa = matches.select(FHFA_KEY)
        if len(self._matched_fhfa) > 0:
            self._matched_fhfa = pl.concat([self._matched_fhfa, new_fhfa], how="diagonal").unique()
        else:
            self._matched_fhfa = new_fhfa.unique()

    def _log_summary(self) -> None:
        """Log matching summary."""
        logger.info("MATCHING SUMMARY")
        for n, round_stats in self._stats["rounds"].items():
            logger.info(
                "Round %s (%s): %s", n, round_stats["description"], f"{round_stats['total']:,}"
            )
            if round_stats.get("same_year", 0) > 0 or round_stats.get("cross_year", 0) > 0:
                logger.info("  - Same-year: %s", f"{round_stats['same_year']:,}")
                logger.info("  - Cross-year: %s", f"{round_stats['cross_year']:,}")
            if "post_unique" in round_stats:
                pu = round_stats["post_unique"]
                logger.info(
                    "  - Post-unique: %s -> %s (%.1f%% retained)",
                    f"{pu['before']:,}",
                    f"{pu['after']:,}",
                    pu["retained_pct"],
                )
        summary = self._stats["summary"]
        logger.info(
            "Total matched: %s / %s FHFA (%.2f%%)",
            f"{summary['total_matched']:,}",
            f"{summary['total_fhfa']:,}",
            summary["fhfa_match_rate"],
        )


def run_matching(
    years: list[int] | None = None,
    save: bool = True,
) -> dict:
    """Run the full multi-round matching workflow.

    Args:
        years: Years to process. Defaults to MIN_YEAR through MAX_YEAR.
        save: Whether to save crosswalk to disk. Defaults to True.

    Returns:
        Statistics for each round and overall summary.
    """
    result = HMDAFHFAMatcher(years=years).run()

    if save and result.round_results:
        HMDAFHFAMatchingConfig.ensure_directories()
        crosswalk = pl.concat(result.round_results, how="diagonal")
        output_path = HMDAFHFAMatchingConfig.get_crosswalk_path("post2018", years)
        with atomic_output(output_path) as tmp:
            crosswalk.write_parquet(tmp)
        logger.info("Saved crosswalk (%s rows) to %s", f"{len(crosswalk):,}", output_path)

    return result.stats
