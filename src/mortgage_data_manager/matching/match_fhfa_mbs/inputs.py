"""The enterprise sides of the MBS<->FHFA matcher: loaders, preparers and specs.

Each enterprise contributes a loader (where its loan-level data lives), a
preparer (how its columns map onto the canonical matching schema the engine
works in) and a `~.spec.GseSpec` carrying the option values it runs
with. What stays enterprise-specific is exactly that; the matching itself is
``matching.py``.

The canonical schema every preparer emits: ``loan_id``, the exact join keys
(``state_fips``, ``msa``, ``channel``, ``num_units``, ``num_borrowers``, ``fthb``,
``loan_purpose``, ``occupancy_code``, ``property_type``, ``term``,
``loan_amount_rounded``), the tolerance fields (``interest_rate``, ``loan_amount``,
``ltv``, ``dti``), ``origination_year``, ``fpd_year``, ``zip3`` and the
``_is_bin_edge`` sidecar.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import cache
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import atomic_output, should_process_file
from mortgage_data_manager.core.reference.states import STATE_TO_FIPS
from mortgage_data_manager.matching.match_fhfa_mbs.config import FHFAMBSMatchingConfig as Config
from mortgage_data_manager.matching.match_fhfa_mbs.rounds import ROUNDS_FHLMC, ROUNDS_FNMA
from mortgage_data_manager.matching.match_fhfa_mbs.spec import (
    LOAN_ID,
    GseSpec,
    MatchingTolerances,
    MatchOptions,
)
from mortgage_data_manager.matching.terms import (
    FREDDIE_PRODUCT_UPBIN,
    TERM_TO_FHFA_BIN,
    is_bin_edge,
    round_to_fhfa_bin,
)
from mortgage_data_manager.sources.fhlmc.import_silver import (
    merge_duplicate_rows,
    scan_origination_bronze,
    scan_performance_bronze,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Fannie Mae
# ---------------------------------------------------------------------------


def load_fnma(year: int, prior_years: int = 1) -> pl.LazyFrame:
    """Scan the FNMA issuance files for one acquisition year.

    The quarterly files hold loans *securitized* in that quarter whatever their
    origination date, so a single year's files already carry the prior-year
    originations the base pass's second leg and the seasoned round need;
    ``prior_years`` is accepted for interface parity and ignored.

    Args:
        year: Acquisition year to load.
        prior_years: How many prior origination cohorts to reach back for.

    Returns:
        That acquisition year's FNMA issuance records.
    """
    del prior_years
    directory = Config.get_fnma_config().FNMA_SILVER_DIR / "issuances"
    files = sorted(directory.glob(f"{year}Q*.parquet"))
    if not files:
        raise FileNotFoundError(f"No FNMA files found for year {year} in {directory}")
    return pl.concat([pl.scan_parquet(f) for f in files], how="diagonal_relaxed")


def prepare_fnma(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Map FNMA issuance silver onto the canonical matching schema.

    Args:
        lf: The frame to map.

    Returns:
        FNMA issuance silver mapped onto the canonical matching schema.
    """
    # 2019+ files use "Metropolitan Statistical Area (MSA)"; pre-2019 files append
    # "or Metropolitan Statistical Division Area (MSDA)". Normalise to the short form.
    msa_column = "Metropolitan Statistical Area (MSA)"
    names = lf.collect_schema().names()
    if msa_column not in names:
        alternate = next((c for c in names if c.startswith(msa_column)), None)
        if alternate is not None:
            lf = lf.rename({alternate: msa_column})

    # The SF issuance silver stores FTHB as Boolean and the dates as pl.Date since
    # the 2026-06 dtype rebuild; the legacy string encodings are still handled so
    # the matcher cannot silently null a join key on an older build.
    schema = lf.collect_schema()
    fthb_raw = pl.col("First Time Home Buyer Indicator")
    fthb = (
        pl.when(fthb_raw).then(1).when(~fthb_raw).then(2).otherwise(None)
        if schema.get("First Time Home Buyer Indicator") == pl.Boolean
        else pl.when(fthb_raw == "Y").then(1).when(fthb_raw == "N").then(2).otherwise(None)
    )

    def year_of(column: str, dtype: type[pl.DataType]) -> pl.Expr:
        col = pl.col(column)
        if schema.get(column) in (pl.Date, pl.Datetime):
            return col.dt.year().cast(dtype)
        return col.str.slice(2, 4).cast(dtype, strict=False)

    return lf.select(
        pl.col("Loan Identifier").alias(LOAN_ID),
        pl.col("Property State").replace_strict(STATE_TO_FIPS, default=None).alias("state_fips"),
        _fnma_msa(msa_column).alias("msa"),
        # FHFA codes Fannie correspondent loans 9, unlike Freddie's 3.
        pl.col("Channel").replace_strict({"R": 1, "B": 2, "C": 9}, default=None).alias("channel"),
        pl.col("Original Interest Rate").cast(pl.Float64).alias("interest_rate"),
        pl.col("Original UPB").cast(pl.Float64).alias("loan_amount"),
        # FHFA's "LTV at origination" is CLTV where available, so use CLTV first.
        pl.coalesce(
            pl.col("Original Combined Loan to Value Ratio (CLTV)"),
            pl.col("Original Loan to Value Ratio (LTV)"),
        )
        .cast(pl.Float64)
        .alias("ltv"),
        pl.col("Debt-To-Income (DTI)").cast(pl.Float64).alias("dti"),
        pl.col("Original Loan Term").cast(pl.Int64).alias("term"),
        pl.col("Number of Units").cast(pl.Int64).alias("num_units"),
        pl.col("Number of Borrowers").cast(pl.Int64).alias("num_borrowers"),
        fthb.alias("fthb"),
        pl.col("Loan Purpose ")
        .replace_strict({"P": 1, "R": 2, "C": 7}, default=None)
        .alias("loan_purpose"),
        pl.col("Occupancy Status")
        .replace_strict({"P": 1, "S": 2, "I": 3}, default=None)
        .alias("occupancy_code"),
        pl.col("Property Type")
        .replace_strict({"SF": 1, "PU": 1, "CO": 1, "CP": 1, "MH": 2}, default=None)
        .alias("property_type"),
        year_of("Origination Date", pl.Int64).alias("origination_year"),
        year_of("First Payment Date", pl.Int32).alias("fpd_year"),
        pl.col("Zip Code Short").alias("zip3"),
    ).with_columns(
        round_to_fhfa_bin(pl.col("loan_amount")).alias("loan_amount_rounded"),
        is_bin_edge(pl.col("loan_amount")).alias("_is_bin_edge"),
    )


FNMA = GseSpec(
    name="fnma",
    id_column="fnma_loan_id",
    enterprise_flag=1,
    load=load_fnma,
    prepare=prepare_fnma,
    crosswalk_columns=(
        "fnma_loan_id",
        "fhfa_record_id",
        "fhfa_year",
        "fhfa_enterprise",
        "zip3_consistent",
        "match_round",
        "match_quality_tier",
    ),
    tolerances=MatchingTolerances(),
    options=MatchOptions(
        # No amount term: Fannie amounts join on the bin midpoint, so every
        # surviving candidate carries the same amount deviation and it cannot rank.
        score_fields=("interest_rate", "ltv", "dti"),
        tier_rate_epsilon=0.0,
        tier_on_amount=False,
        term_envelope=None,
        prior_year_cohorts=1,
    ),
    rounds=ROUNDS_FNMA,
)


# ---------------------------------------------------------------------------
# Freddie Mac
# ---------------------------------------------------------------------------

#: Origination cohorts loaded alongside the acquisition year, in years before it.
FHLMC_PRIOR_COHORTS = 5

#: Borrower count Freddie matching top-codes both sides at. FHFA reported Freddie
#: borrowers only as 1 or 2 through 2024, and from 2025 reports the actual count
#: (2025: 15,359 threes and 2,406 fours) while Freddie's own files are verbatim
#: throughout, so capping both sides keeps every acquisition year on one rule.
FHLMC_MAX_BORROWERS = 2

#: Acquisition-consistency window for the rounds that filter their pool, in months
#: before January and after December of the acquisition year: [Nov Y-1, Jan Y+1].
#: Calibrated on production pairs: keeps 99.96-99.99% of same-year and
#: 97.1-98.2% of prior-year pairs; the prior-year pairs outside it are twins on
#: independent checks. Loans without performance history pass (null-neutral).
ACQUISITION_WINDOW_MONTHS: tuple[int, int] = (2, 1)


@cache
def get_md_to_cbsa() -> dict[int, int]:
    """Load (lazily, cached) the Metropolitan Division -> CBSA mapping.

    The crosswalk covers both the 2020 and 2023 OMB delineation vintages: 40
    MD-to-CBSA mappings for the 13 metros with divisions. Loaded on first use
    rather than at import, so a missing crosswalk surfaces only when a GSE leg is prepared.

    Returns:
        The Metropolitan Division to parent CBSA mapping.

    Raises:
        FileNotFoundError: If the Census crosswalk file is not present.
    """
    crosswalk_path = MortgageDataConfig.SCHEMAS_DIR / "census" / "md_to_cbsa_crosswalk.csv"
    if not crosswalk_path.exists():
        raise FileNotFoundError(
            f"Census MD-to-CBSA crosswalk not found at {crosswalk_path}. "
            "Download from Census Bureau delineation files."
        )
    df = pl.read_csv(crosswalk_path)
    return dict(df.select("md_code", "cbsa_code").iter_rows())


def load_fhlmc(year: int, prior_years: int = 1) -> pl.LazyFrame:
    """Scan the FHLMC origination bronze for one acquisition year and its cohorts.

    Freddie's files are organised by origination cohort, so reaching prior-year
    originations means loading prior cohorts. The non-standard (ARM / relief-refi)
    ``excl`` files are included: they share the origination layout and the loan-id
    year encoding, and the standard glob cannot reach them.

    Args:
        year: Acquisition year to load.
        prior_years: How many prior origination cohorts to reach back for.

    Returns:
        That acquisition year's FHLMC originations, standard and ``excl``.
    """
    directory = Config.get_fhlmc_config().FHLMC_BRONZE_DIR / "origination"
    cohorts = list(range(year, year - prior_years - 1, -1))
    files: list[Path] = []
    for cohort in cohorts:
        files += sorted(directory.glob(f"historical_data_{cohort}Q*.parquet"))
        files += sorted(directory.glob(f"historical_data_excl_{cohort}Q*.parquet"))
    if not files:
        raise FileNotFoundError(f"No FHLMC files found for year(s) {cohorts} in {directory}")
    # scan_origination_bronze conforms each file's layout era to the pre-R47 column
    # names and reduces Postal Code to a bare ZIP3.
    return pl.concat([scan_origination_bronze(f) for f in files], how="diagonal_relaxed")


def _build_first_reporting_period(cohort_year: int) -> pl.LazyFrame | None:
    """Per-loan first period for one cohort, or None when it has no performance files."""
    fhlmc_config = Config.get_fhlmc_config()
    silver = fhlmc_config.FHLMC_SILVER_DIR / "performance"
    scans = [
        pl.scan_parquet(f)
        for f in sorted(silver.glob(f"historical_data_time_{cohort_year}Q*.parquet"))
    ]
    scans += [
        scan_performance_bronze(f)
        for f in sorted(
            fhlmc_config.FHLMC_BRONZE_PERFORMANCE.glob(
                f"historical_data_excl_time_{cohort_year}Q*.parquet"
            )
        )
    ]
    if not scans:
        return None
    return (
        pl.concat(
            [s.select("Loan Sequence Number", "Monthly Reporting Period") for s in scans],
            how="vertical_relaxed",
        )
        .group_by("Loan Sequence Number")
        .agg(pl.col("Monthly Reporting Period").min().alias("first_reporting_period"))
        .rename({"Loan Sequence Number": "fhlmc_loan_id"})
    )


def load_first_reporting_period(
    cohort_years: Iterable[int], overwrite: bool = False
) -> pl.LazyFrame:
    """Per-loan first ``Monthly Reporting Period`` over the given origination cohorts.

    Freddie's performance history starts the month a loan is acquired, so the
    first reported period dates the acquisition, a field no match key touches.
    Built from the silver performance files plus the bronze excl performance
    files where they exist (two columns projected, streamed) and cached under the
    matching directory, one parquet per cohort year, keyed ``fhlmc_loan_id`` on
    disk. A stale cache only leaves newer loans without a period, which the filter
    treats as neutral.

    Args:
        cohort_years: Origination years whose performance files to read.
        overwrite: Rebuild the cached cohort tables.

    Returns:
        ``loan_id`` and ``first_reporting_period`` (Date), one row per loan.
    """
    cache_dir = Config.FHFA_MBS_MATCHING_DIR / "fhlmc_first_reporting_period"
    cached = []
    for year in cohort_years:
        path = cache_dir / f"{year}.parquet"
        if should_process_file(path, overwrite):
            lf = _build_first_reporting_period(year)
            if lf is None:
                continue
            cache_dir.mkdir(parents=True, exist_ok=True)
            with atomic_output(path) as tmp:
                lf.sink_parquet(tmp)
            logger.info("Cached first reporting period for cohort %d: %s", year, path)
        if path.exists():
            cached.append(pl.scan_parquet(path))
    if not cached:
        return pl.LazyFrame(schema={LOAN_ID: pl.String, "first_reporting_period": pl.Date})
    return pl.concat(cached, how="vertical_relaxed").rename({"fhlmc_loan_id": LOAN_ID})


def filter_acquisition_consistent(
    pool: pl.DataFrame, acquisition_year: int, overwrite: bool = False
) -> pl.DataFrame:
    """Keep loans whose first performance period sits in the acquisition window.

    The acquisition-year legs already pin origination to the acquisition year or the
    year before; later rounds offer older originations and looser keys, where a twin
    Freddie acquired a year or more earlier can pass every key. Loans without
    performance history are kept.

    Args:
        pool: Prepared FHLMC loans (any origination year), keyed by ``loan_id``.
        acquisition_year: The FHFA acquisition year.
        overwrite: Rebuild the cached first-reporting-period tables.

    Returns:
        The pool without loans first reported outside the window.
    """
    before, after = ACQUISITION_WINDOW_MONTHS
    start = pl.date(acquisition_year, 1, 1).dt.offset_by(f"-{before}mo")
    end = pl.date(acquisition_year, 12, 1).dt.offset_by(f"{after}mo")
    cohorts = range(acquisition_year - FHLMC_PRIOR_COHORTS, acquisition_year + 1)
    first = pl.col("first_reporting_period")

    periods = load_first_reporting_period(cohorts, overwrite).collect()
    with_period = pool.join(periods, on=LOAN_ID, how="left")
    kept = with_period.filter(first.is_null() | first.is_between(start, end))
    logger.info(
        "Acquisition window %d: kept %s of %s loans (%s outside the window removed; "
        "%s without performance history kept)",
        acquisition_year,
        f"{len(kept):,}",
        f"{len(pool):,}",
        f"{len(pool) - len(kept):,}",
        f"{kept['first_reporting_period'].null_count():,}",
    )
    return kept.drop("first_reporting_period")


def _fnma_msa(msa_column: str) -> pl.Expr:
    """Fannie MSA as the FHFA parent CBSA, non-metro coded 0.

    Fannie reported the parent CBSA through its 2024 issuance files, then began
    reporting Metropolitan Division codes during 2025 (2.4% of 2025 loans, a
    quarter of early 2026). FHFA reports the parent CBSA, so the same map as
    `_fhlmc_msa` applies; it is a no-op on the earlier files, which carry no
    division codes. ``'00000'`` is Fannie's non-metro code; FHFA writes 0.

    Args:
        msa_column: Name of the normalised MSA column.

    Returns:
        An expression yielding the parent CBSA, non-metro coded 0.
    """
    msa = (
        pl.when(pl.col(msa_column) == "00000")
        .then(0)
        .otherwise(pl.col(msa_column).cast(pl.Int64, strict=False))
    )
    return msa.replace_strict(get_md_to_cbsa(), default=msa)


def _fhlmc_msa() -> pl.Expr:
    """Freddie MSA as the FHFA parent CBSA, non-metro coded 0.

    Freddie reports Metropolitan Division codes for the 13 metros that have them;
    FHFA reports the parent CBSA. Null and 0 both mean non-metro, which FHFA
    writes as 0.

    Returns:
        An expression yielding the parent CBSA, non-metro coded 0.
    """
    msa = pl.col("Metropolitan Statistical Area (MSA) Or Metropolitan Division").fill_null(0)
    return msa.replace_strict(get_md_to_cbsa(), default=msa)


def _fhlmc_term() -> pl.Expr:
    """Freddie loan term at FHFA's ceiling standard term (144 -> 180, not 120)."""
    term = pl.col("Original Loan Term").cast(pl.Int64)
    return term.replace_strict(TERM_TO_FHFA_BIN, default=term)


def prepare_fhlmc(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Map FHLMC origination bronze onto the canonical matching schema.

    Args:
        lf: The frame to map.

    Returns:
        FHLMC origination bronze mapped onto the canonical matching schema.
    """
    mapped = lf.select(
        pl.col("Loan Sequence Number").alias(LOAN_ID),
        pl.col("Property State").replace_strict(STATE_TO_FIPS, default=None).alias("state_fips"),
        _fhlmc_msa().alias("msa"),
        # FHFA codes Freddie correspondent loans 3, unlike Fannie's 9.
        pl.col("Channel").replace_strict({"R": 1, "B": 2, "C": 3}, default=None).alias("channel"),
        pl.col("Original Interest Rate").cast(pl.Float64).alias("interest_rate"),
        pl.col("Original UPB").cast(pl.Float64).alias("loan_amount"),
        # FHFA's "LTV at origination" is CLTV where available, so use CLTV first.
        pl.coalesce(
            pl.col("Original Combined Loan-to-Value (CLTV)"),
            pl.col("Original Loan-to-Value (LTV)"),
        )
        .cast(pl.Float64)
        .alias("ltv"),
        pl.col("Original Debt-to-Income (DTI) Ratio").cast(pl.Float64).alias("dti"),
        # FHFA reports non-standard Freddie terms at the ceiling standard term.
        _fhlmc_term().alias("term"),
        pl.col("Number of Units").cast(pl.Int64).alias("num_units"),
        pl.col("Number of Borrowers")
        .cast(pl.Int64)
        .clip(upper_bound=FHLMC_MAX_BORROWERS)
        .alias("num_borrowers"),
        pl.col("First Time Homebuyer Flag")
        .replace_strict({"Y": 1, "N": 2}, default=None)
        .alias("fthb"),
        pl.col("Loan Purpose")
        .replace_strict({"P": 1, "R": 2, "N": 2, "C": 7}, default=None)
        .alias("loan_purpose"),
        pl.col("Occupancy Status")
        .replace_strict({"P": 1, "S": 2, "I": 3}, default=None)
        .alias("occupancy_code"),
        pl.col("Property Type")
        .replace_strict({"SF": 1, "PU": 1, "CO": 1, "CP": 1, "MH": 2}, default=None)
        .alias("property_type"),
        # F{YY}Q{Q}{sequence}, e.g. F23Q10031582 -> 2023.
        (pl.col("Loan Sequence Number").str.slice(1, 2).cast(pl.Int64) + 2000).alias(
            "origination_year"
        ),
        pl.col("First Payment Date").dt.year().cast(pl.Int32).alias("fpd_year"),
        # Postal Code is already a bare ZIP3 (Int64) after the bronze scan; 0 and
        # null both mean no geography.
        pl.when(pl.col("Postal Code") > 0)
        .then(pl.col("Postal Code").cast(pl.Utf8).str.zfill(3))
        .otherwise(None)
        .alias("zip3"),
    ).with_columns(
        round_to_fhfa_bin(pl.col("loan_amount")).alias("loan_amount_rounded"),
        is_bin_edge(pl.col("loan_amount")).alias("_is_bin_edge"),
    )
    # A loan in both the standard and the excl release appears twice: a field one
    # copy leaves null is filled from the other, and a loan whose copies carry
    # different values in a matching field is excluded rather than matched on
    # whichever copy happens to agree.
    return merge_duplicate_rows(mapped, LOAN_ID, exclude_conflicts=True)


FHLMC = GseSpec(
    name="fhlmc",
    id_column="fhlmc_loan_id",
    enterprise_flag=2,
    load=load_fhlmc,
    prepare=prepare_fhlmc,
    crosswalk_columns=(
        "fhlmc_loan_id",
        "fhfa_record_id",
        "fhfa_year",
        "fhfa_enterprise",
        "match_round",
        "match_quality_tier",
        "zip3_consistent",
    ),
    tolerances=MatchingTolerances(),
    options=MatchOptions(
        # The amount term ranks the original UPB against the FHFA bin midpoint, so it
        # sorts a record's rival loans on where inside the $10k bin their UPB sits --
        # a quantity flat against truth (held-out census-tract agreement 0.9806-0.9823
        # across all six levels it takes, on 2M pairs it never touched). Its weight is
        # 1/amount_tolerance = 1000 on whole-$1,000 balances, a thousand times the
        # largest deviation the other fields can produce, so it would otherwise decide
        # every contested record on its own. It stays in the score -- the pairs kept
        # are the pairs this ranking kept -- but it may not settle a record: see
        # non_evidence_score_fields.
        score_fields=("interest_rate", "loan_amount", "ltv", "dti"),
        non_evidence_score_fields=("loan_amount",),
        tier_rate_epsilon=0.01,
        tier_on_amount=True,
        term_envelope=FREDDIE_PRODUCT_UPBIN,
        pool_filter=filter_acquisition_consistent,
        prior_year_cohorts=FHLMC_PRIOR_COHORTS,
    ),
    rounds=ROUNDS_FHLMC,
    max_borrowers=FHLMC_MAX_BORROWERS,
)


SPECS: dict[str, GseSpec] = {"fnma": FNMA, "fhlmc": FHLMC}


def specs(names: Iterable[str] | None = None) -> list[GseSpec]:
    """Look up specs by enterprise token; None means both.

    Args:
        names: Enterprise tokens to look up, or None for both.

    Returns:
        The requested enterprise specs.

    Raises:
        KeyError: On an unknown token.
    """
    return [SPECS[n] for n in (list(SPECS) if names is None else names)]
