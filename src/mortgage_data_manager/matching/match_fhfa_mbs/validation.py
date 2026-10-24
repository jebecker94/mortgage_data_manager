"""FHFA-MBS Match Validation Module.

This module provides validation analyses for the FHFA-MBS matching workflow,
supporting both FNMA (Fannie Mae) and FHLMC (Freddie Mac) matching to FHFA data.

Validation analyses:
1. Match rates over time (yearly for FHLMC, monthly for FNMA)
2. Match rates by loan characteristics (amount, rate, LTV, DTI)
3. Match rates by categorical variables (purpose, channel, occupancy)

The goal is to demonstrate that match quality is consistent across time
and loan characteristics, ensuring the matched sample is representative.

Output:
- One combined figure: docs/matching/figures/fhfa_mbs_validation.png,
  a column per enterprise and a row per dimension. A one-enterprise run
  (``--agency fnma``) writes ``fhfa_mbs_validation_fnma.png`` instead, and
  likewise for the ARM figure.
- The ARM coverage figure: docs/matching/figures/fhfa_mbs_umbs_arm_coverage.png,
  the share of FHFA ARM records matched by acquisition year, by the SF crosswalk
  alone and together with the FHFA->UMBS ARM crosswalk.

Usage:
    # From the command line
    mortgage-data match fhfa-mbs validate --agency fnma
    mortgage-data match fhfa-mbs validate --agency fhlmc
    mortgage-data match fhfa-mbs validate

    # Programmatically
    from mortgage_data_manager.matching.match_fhfa_mbs.validation import run_validation
    run_validation(enterprise="fnma")
    run_validation(enterprise="fhlmc")
    run_validation(enterprise="both")
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

import numpy as np
import polars as pl

from mortgage_data_manager.matching.validation_common import (
    COLORS,
    RateSeries,
    Side,
    binned,
    finite_rates,
    get_pyplot,
    matched_count,
    matched_ids,
    panel_gridspec,
    panel_grouped_bars,
    panel_rate_vs_bin,
    rate_by,
    rate_by_sides,
    save_and_close,
    set_rate_ylim,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from mortgage_data_manager.matching.validation_common import Frame

# =============================================================================
# Configuration
# =============================================================================

# Default year ranges (source data availability)
FNMA_START_YEAR = 2019
FNMA_END_YEAR = 2025
FHLMC_START_YEAR = 2019
FHLMC_END_YEAR = 2025

# Origination years the temporal panel covers, independent of the acquisition years
# being validated: a loan acquired in 2019 may have originated in 2018.
TEMPORAL_MIN_YEAR = 2018
TEMPORAL_MAX_YEAR = 2025

# FHFA acquisition years the ARM coverage figure covers (the ARM pass's default range)
ARM_START_YEAR = 2018
ARM_END_YEAR = 2025

# Output directory for the validation figures
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent
FIGURES_DIR = PROJECT_ROOT / "docs" / "matching" / "figures"
VALIDATION_FIGURE = "fhfa_mbs_validation"
ARM_COVERAGE_FIGURE = "fhfa_mbs_umbs_arm_coverage"

# Colors
MBS_COLOR = COLORS["hunter"]  # MBS data (FNMA/FHLMC)
FHFA_COLOR = COLORS["gold"]  # FHFA data

# Vertical inches allotted to one grid row; the canvas is sized from the row count.
PANEL_HEIGHT_IN = 4.5

# Extra y-headroom above the tallest bar on panels that print their values on top,
# so a near-1.0 rate's label does not collide with the panel title.
LABELLED_BAR_HEADROOM = 0.08

# A temporal panel with at most this many periods is drawn as labelled bars; more
# than this and it becomes a line, which is the only readable form at ~70 months.
TEMPORAL_BAR_LIMIT = 12


class Gse(NamedTuple):
    """One enterprise: how its data is loaded, keyed, and labelled.

    Attributes:
        key: Short name, used in ``results`` and on the CLI.
        label: Display name for titles and legends.
        id_col: The enterprise's loan-ID column, joined against the crosswalk.
        loader: Returns ``(crosswalk, mbs, fhfa)`` for a year range.
        period_cols: Origination-period columns, coarsest first. FNMA carries a
            month, FHLMC only a year, which is what makes their temporal panels
            differ in resolution.
        channel_labels: Channel code map; the enterprises disagree on the code for
            correspondent originations, so this cannot be shared.
        default_years: Default ``(min_year, max_year)`` to validate.
    """

    key: str
    label: str
    id_col: str
    loader: Callable[[int, int], tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]]
    period_cols: tuple[str, ...]
    channel_labels: dict[int, str]
    default_years: tuple[int, int]


# Code -> label for the categorical panels. Channel is per-GSE and lives on ``Gse``.
CATEGORY_LABELS: dict[str, dict[int, str]] = {
    "dti_bin": {1: "<20%", 2: "20-30%", 3: "30-36%", 4: "36-49%", 5: "50-60%", 6: ">60%"},
    "loan_purpose": {1: "Purchase", 2: "Refinance", 7: "Cash-out"},
    "occupancy_code": {1: "Primary", 2: "Second Home", 3: "Investment"},
}

#: FHFA's DTI missing marker; treated as null before any DTI bin is formed.
FHFA_DTI_MISSING = 99


# =============================================================================
# Data Loading - FNMA
# =============================================================================


def load_fnma_validation_data(
    min_year: int = FNMA_START_YEAR,
    max_year: int = FNMA_END_YEAR,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Load crosswalk and prepared FNMA/FHFA data for validation.

    Args:
        min_year: First year to include
        max_year: Last year to include

    Returns:
        Tuple of (crosswalk, fnma_prepared, fhfa_prepared) DataFrames
    """
    from mortgage_data_manager.matching.match_fhfa_mbs.config import FHFAMBSMatchingConfig as Config
    from mortgage_data_manager.matching.match_fhfa_mbs.inputs import (
        FNMA,
        load_fnma,
        prepare_fnma,
    )
    from mortgage_data_manager.matching.match_fhfa_mbs.matching import load_fhfa, prepare_fhfa

    crosswalks = []
    fnma_dfs = []
    fhfa_dfs = []
    years_loaded = []

    for year in range(min_year, max_year + 1):
        crosswalk_path = Config.FHFA_MBS_CROSSWALK_DIR / f"fhfa_mbs_crosswalk_fnma_{year}.parquet"
        if not crosswalk_path.exists():
            print(f"  Skipping {year}: crosswalk not found")
            continue

        try:
            crosswalk = pl.read_parquet(crosswalk_path)
            crosswalk = crosswalk.with_columns(
                [
                    (pl.lit(year * 100_000_000) + pl.col("fhfa_record_id")).alias("fhfa_record_id"),
                    pl.lit(year).alias("match_year"),
                ]
            )
            crosswalks.append(crosswalk)

            fnma_raw = load_fnma(year)
            # Dtype dispatch, mirroring prepare_fnma: the SF issuance silver
            # migrated Origination Date to pl.Date in the 2026-06 rebuild, but the legacy
            # MMYYYY string encoding still appears in older builds.
            if fnma_raw.collect_schema().get("Origination Date") in (pl.Date, pl.Datetime):
                orig_month = pl.col("Origination Date").dt.month().cast(pl.Int64)
            else:
                orig_month = pl.col("Origination Date").str.slice(0, 2).cast(pl.Int64, strict=False)
            fnma_month_lf = fnma_raw.select(
                [
                    pl.col("Loan Identifier").alias("fnma_loan_id"),
                    orig_month.alias("orig_month"),
                ]
            )

            fnma_prepared = prepare_fnma(fnma_raw).rename({"loan_id": "fnma_loan_id"})
            fnma_prepared = fnma_prepared.join(fnma_month_lf, on="fnma_loan_id", how="left")
            fnma_prepared = fnma_prepared.with_columns(
                [
                    pl.col("origination_year").alias("orig_year"),
                    pl.lit(year).alias("acq_year"),
                ]
            )
            fnma_df = fnma_prepared.collect()
            fnma_dfs.append(fnma_df)

            fhfa_prepared = prepare_fhfa(load_fhfa(year, FNMA), FNMA)
            fhfa_prepared = fhfa_prepared.with_columns(
                [
                    (pl.lit(year * 100_000_000) + pl.col("fhfa_record_id")).alias("fhfa_record_id"),
                    pl.lit(year).alias("acq_year"),
                ]
            )
            fhfa_df = fhfa_prepared.collect()
            fhfa_dfs.append(fhfa_df)

            years_loaded.append(year)
            print(
                f"  Loaded {year}: {len(crosswalk):,} matches, {len(fnma_df):,} FNMA, {len(fhfa_df):,} FHFA"
            )

        except Exception as e:
            print(f"  Skipping {year}: {e}")
            continue

    if not crosswalks:
        raise ValueError(f"No FNMA crosswalk data found for years {min_year}-{max_year}")

    combined_crosswalk = pl.concat(crosswalks, how="diagonal_relaxed")
    combined_fnma = pl.concat(fnma_dfs, how="diagonal_relaxed")
    combined_fhfa = pl.concat(fhfa_dfs, how="diagonal_relaxed")

    print(f"\nCombined FNMA data for {years_loaded}:")
    print(f"  Crosswalk: {len(combined_crosswalk):,} matched pairs")
    print(f"  FNMA: {len(combined_fnma):,} loans")
    print(f"  FHFA: {len(combined_fhfa):,} records")

    return combined_crosswalk, combined_fnma, combined_fhfa


# =============================================================================
# Data Loading - FHLMC
# =============================================================================


def load_fhlmc_validation_data(
    min_year: int = FHLMC_START_YEAR,
    max_year: int = FHLMC_END_YEAR,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Load yearly crosswalks and source data for FHLMC-FHFA validation.

    Loads yearly crosswalk files on-the-fly and combines them,
    along with FHLMC/FHFA source data for match rate analysis.

    Args:
        min_year: First year to include
        max_year: Last year to include

    Returns:
        Tuple of (crosswalk, fhlmc_prepared, fhfa_prepared) DataFrames
    """
    from mortgage_data_manager.core.reference.states import STATE_TO_FIPS
    from mortgage_data_manager.matching.match_fhfa_mbs.config import FHFAMBSMatchingConfig as Config
    from mortgage_data_manager.matching.match_fhfa_mbs.inputs import FHLMC
    from mortgage_data_manager.matching.match_fhfa_mbs.matching import prepare_fhfa
    from mortgage_data_manager.sources.fhfa.config import FHFAConfig
    from mortgage_data_manager.sources.fhlmc.config import FHLMCConfig
    from mortgage_data_manager.sources.fhlmc.import_silver import (
        merge_duplicate_rows,
        scan_origination_bronze,
    )

    # Load yearly crosswalks and combine on-the-fly
    crosswalks = []
    years_loaded = []

    for year in range(min_year, max_year + 1):
        crosswalk_path = Config.FHFA_MBS_CROSSWALK_DIR / f"fhfa_mbs_crosswalk_fhlmc_{year}.parquet"
        if not crosswalk_path.exists():
            print(f"  Skipping {year}: crosswalk not found")
            continue

        cw = pl.read_parquet(crosswalk_path)
        # Make FHFA record IDs globally unique by prefixing with year
        cw = cw.with_columns(
            [
                (pl.lit(year).cast(pl.Int64) * 100_000_000 + pl.col("fhfa_record_id")).alias(
                    "fhfa_record_id_global"
                ),
            ]
        )
        crosswalks.append(cw)
        years_loaded.append(year)
        print(f"  Loaded {year}: {len(cw):,} matches")

    if not crosswalks:
        raise ValueError(f"No FHLMC crosswalk data found for years {min_year}-{max_year}")

    crosswalk = pl.concat(crosswalks, how="diagonal_relaxed")
    print(
        f"  Combined crosswalk: {len(crosswalk):,} matches ({years_loaded[0]}-{years_loaded[-1]})"
    )

    # A resolved run keeps one acquisition year per loan; crosswalks written by single-year
    # runs (`run --year`) can still claim a loan in two years. Drop such loans, as the
    # matcher's cross-year resolution does.
    n_before = len(crosswalk)
    crosswalk = crosswalk.unique(subset=["fhlmc_loan_id"], keep="none")
    if len(crosswalk) != n_before:
        print(f"  Dropped {n_before - len(crosswalk):,} pairs on loans claimed in two years")

    # Load FHLMC data for the validation year range and deduplicate by loan ID
    fhlmc_dir = FHLMCConfig.FHLMC_BRONZE_DIR / "origination"
    fhlmc_files = sorted(
        f
        for f in fhlmc_dir.glob("historical_data_*.parquet")
        if any(f"_{y}Q" in f.name for y in range(min_year, max_year + 1))
    )

    print(f"  Loading {len(fhlmc_files)} FHLMC files ({min_year}-{max_year})...")
    # scan_origination_bronze conforms each file's layout era to the established
    # (pre-R47) column names used in the select below.
    fhlmc_raw = pl.concat(
        [scan_origination_bronze(f).collect() for f in fhlmc_files],
        how="diagonal_relaxed",
    )

    # Prepare FHLMC data with standardized columns
    fhlmc = fhlmc_raw.select(
        [
            pl.col("Loan Sequence Number").alias("fhlmc_loan_id"),
            pl.col("Property State").alias("state_abbr"),
            pl.col("Original Interest Rate").alias("interest_rate"),
            pl.col("Original UPB").alias("loan_amount"),
            pl.col("Original Loan-to-Value (LTV)").alias("ltv"),
            pl.col("Original Debt-to-Income (DTI) Ratio").alias("dti"),
            pl.col("Original Loan Term").alias("term"),
            pl.col("Number of Units").alias("num_units"),
            pl.col("Number of Borrowers").alias("num_borrowers"),
            pl.col("First Time Homebuyer Flag").alias("fthb_str"),
            pl.col("Loan Purpose").alias("purpose_str"),
            pl.col("Occupancy Status").alias("occupancy_str"),
            pl.col("Channel").alias("channel_str"),
            pl.col("First Payment Date"),
        ]
    )

    # Add origination year (first payment - 60 days)
    fhlmc = fhlmc.with_columns(
        (pl.col("First Payment Date") - pl.duration(days=60)).dt.year().alias("origination_year")
    )

    # Map state to FIPS
    fhlmc = fhlmc.with_columns(
        pl.col("state_abbr").replace_strict(STATE_TO_FIPS, default=None).alias("state_fips")
    )

    # Map categorical codes
    fhlmc = fhlmc.with_columns(
        [
            # Channel
            pl.when(pl.col("channel_str") == "R")
            .then(1)
            .when(pl.col("channel_str") == "B")
            .then(2)
            .when(pl.col("channel_str") == "C")
            .then(3)
            .otherwise(None)
            .alias("channel"),
            # Loan purpose
            pl.when(pl.col("purpose_str") == "P")
            .then(1)
            .when(pl.col("purpose_str").is_in(["R", "N"]))
            .then(2)
            .when(pl.col("purpose_str") == "C")
            .then(7)
            .otherwise(None)
            .alias("loan_purpose"),
            # Occupancy
            pl.when(pl.col("occupancy_str") == "P")
            .then(1)
            .when(pl.col("occupancy_str") == "S")
            .then(2)
            .when(pl.col("occupancy_str") == "I")
            .then(3)
            .otherwise(None)
            .alias("occupancy_code"),
            # FTHB
            pl.when(pl.col("fthb_str") == "Y")
            .then(1)
            .when(pl.col("fthb_str") == "N")
            .then(2)
            .otherwise(None)
            .alias("fthb"),
        ]
    )

    # Merge repeated loans the way the matcher does, dropping any whose copies disagree
    fhlmc = merge_duplicate_rows(fhlmc.lazy(), "fhlmc_loan_id", exclude_conflicts=True).collect()
    print(f"  FHLMC after merging repeated loans: {len(fhlmc):,} unique loans")

    # Load all FHFA data for Freddie Mac
    fhfa_dfs = []
    for year in range(min_year, max_year + 1):
        fhfa_file = FHFAConfig.FHFA_SILVER_DIR / "sf_c" / f"sf_c_{year}.parquet"
        if fhfa_file.exists():
            df = pl.read_parquet(fhfa_file)
            df = df.filter(pl.col("enterprise_flag") == 2)  # Freddie Mac only
            df = prepare_fhfa(df.lazy(), FHLMC).collect()
            df = df.with_columns(
                [
                    (pl.lit(year) * 100_000_000 + pl.col("fhfa_record_id")).alias(
                        "fhfa_record_id_global"
                    ),
                    pl.lit(year).alias("acq_year"),
                ]
            )
            fhfa_dfs.append(df)
            print(f"  Loaded FHFA {year}: {len(df):,} Freddie records")

    fhfa = pl.concat(fhfa_dfs, how="diagonal_relaxed")

    print(f"\nCombined data ({min_year}-{max_year}):")
    print(f"  Crosswalk: {len(crosswalk):,} matched pairs")
    print(f"  FHLMC: {len(fhlmc):,} unique loans")
    print(f"  FHFA: {len(fhfa):,} records")

    # Use global FHFA ID for joining - drop original and rename global
    crosswalk = crosswalk.drop("fhfa_record_id").rename({"fhfa_record_id_global": "fhfa_record_id"})
    fhfa = fhfa.drop("fhfa_record_id").rename({"fhfa_record_id_global": "fhfa_record_id"})

    return crosswalk, fhlmc, fhfa


# =============================================================================
# GSE Registry
#
# Defined after the loaders because it holds them. Everything downstream -- match
# rate computation, plotting, the runner -- is written against this tuple rather
# than against either enterprise, so the two are handled by one code path.
# =============================================================================


GSES = (
    Gse(
        key="fnma",
        label="FNMA",
        id_col="fnma_loan_id",
        loader=load_fnma_validation_data,
        period_cols=("orig_year", "orig_month"),
        channel_labels={1: "Retail", 2: "Broker", 9: "Correspondent"},
        default_years=(FNMA_START_YEAR, FNMA_END_YEAR),
    ),
    Gse(
        key="fhlmc",
        label="FHLMC",
        id_col="fhlmc_loan_id",
        loader=load_fhlmc_validation_data,
        period_cols=("origination_year",),
        channel_labels={1: "Retail", 2: "Broker", 3: "Correspondent"},
        default_years=(FHLMC_START_YEAR, FHLMC_END_YEAR),
    ),
)


# =============================================================================
# Sides and dimensions
#
# One table drives both halves of the module: the compute loop builds a rates frame
# per dimension, and the figure draws one row per dimension. Adding a dimension is
# one entry here, not a compute function plus a plot function plus two call sites.
# =============================================================================


def gse_sides(
    crosswalk: pl.DataFrame, mbs: pl.DataFrame, fhfa: pl.DataFrame, gse: Gse
) -> tuple[Side, Side]:
    """The enterprise side and the FHFA side, each with its matched ids computed once.

    Args:
        crosswalk: The enterprise's crosswalk (FHFA record ids already made global).
        mbs: The enterprise's prepared loans.
        fhfa: The prepared FHFA records for that enterprise.
        gse: Which enterprise.

    Returns:
        ``(mbs_side, fhfa_side)``.
    """
    dti = pl.col("dti")
    fhfa_clean = fhfa.lazy().with_columns(
        pl.when(dti == FHFA_DTI_MISSING).then(None).otherwise(dti).alias("dti")
    )
    return (
        Side(mbs, matched_ids(crosswalk, gse.id_col), gse.id_col, "mbs"),
        Side(fhfa_clean, matched_ids(crosswalk, "fhfa_record_id"), "fhfa_record_id", "fhfa"),
    )


def _dti_bin(col: str = "dti") -> pl.Expr:
    """FHFA's DTI bin scheme."""
    dti = pl.col(col)
    return (
        pl.when(dti < 20)
        .then(1)
        .when(dti < 30)
        .then(2)
        .when(dti < 36)
        .then(3)
        .when(dti <= 49)
        .then(4)
        .when(dti <= 60)
        .then(5)
        .when(dti > 60)
        .then(6)
        .otherwise(None)
        .cast(pl.Int64)
        .alias("dti_bin")
    )


def _not_null(col: str) -> Callable[[pl.LazyFrame], pl.LazyFrame]:
    return lambda lf: lf.filter(pl.col(col).is_not_null())


class Dimension(NamedTuple):
    """One analysis dimension: how its rates are computed and how they are drawn.

    Attributes:
        key: Name in ``results`` and in the panel title.
        kind: Which panel draws it -- "continuous" or "grouped".
        column: The bin or category column the prepared universes carry.
        xlabel: Axis label for the panel.
        prepare: Applied to both sides' universes before counting -- the filter and
            the bin column. Both sources carry the same column names by construction.
        x_scale: Multiplier applied to the bin values before plotting.
    """

    key: str
    kind: str
    column: str
    xlabel: str
    prepare: Callable[[pl.LazyFrame], pl.LazyFrame]
    x_scale: float = 1.0


DIMENSIONS = (
    Dimension(
        "loan_amount",
        "continuous",
        "loan_amount_bin",
        "Loan Amount ($K)",
        lambda lf: lf.filter(pl.col("loan_amount") <= 750_000).with_columns(
            binned("loan_amount", 10_000, mode="midpoint", dtype=pl.Int64)
        ),
        x_scale=0.001,
    ),
    Dimension(
        "ltv",
        "continuous",
        "ltv_bin",
        "LTV (%)",
        lambda lf: lf.filter(pl.col("ltv").is_not_null() & (pl.col("ltv") <= 100)).with_columns(
            pl.col("ltv").round(0).cast(pl.Int64).alias("ltv_bin")
        ),
    ),
    Dimension(
        "interest_rate",
        "continuous",
        "interest_rate_bin",
        "Interest Rate (%)",
        lambda lf: lf.filter(pl.col("interest_rate").is_between(2.0, 9.0)).with_columns(
            binned("interest_rate", 0.125, mode="round", dtype=pl.Float64)
        ),
    ),
    Dimension(
        "dti",
        "grouped",
        "dti_bin",
        "DTI Bin (FHFA scheme)",
        lambda lf: _not_null("dti")(lf).with_columns(_dti_bin()),
    ),
    Dimension("loan_purpose", "grouped", "loan_purpose", "Loan Purpose", _not_null("loan_purpose")),
    Dimension("channel", "grouped", "channel", "Channel", _not_null("channel")),
    Dimension("occupancy", "grouped", "occupancy_code", "Occupancy", _not_null("occupancy_code")),
)


# =============================================================================
# Statistics
# =============================================================================


def compute_overall_match_rates(
    crosswalk: pl.DataFrame, sides: tuple[Side, Side]
) -> dict[str, Any]:
    """Headline counts and the match rate from each side.

    Args:
        crosswalk: The match crosswalk.
        sides: The two sides, from `gse_sides`.

    Returns:
        Pair count, both totals, both matched counts, both rates.
    """
    mbs, fhfa = sides
    mbs_total, fhfa_total = _height(mbs.universe), _height(fhfa.universe)
    mbs_matched, fhfa_matched = matched_count(mbs), matched_count(fhfa)
    return {
        "total_matches": len(crosswalk),
        "mbs_total": mbs_total,
        "fhfa_total": fhfa_total,
        "mbs_matched": mbs_matched,
        "fhfa_matched": fhfa_matched,
        "mbs_match_rate": mbs_matched / mbs_total if mbs_total else 0,
        "fhfa_match_rate": fhfa_matched / fhfa_total if fhfa_total else 0,
    }


def _height(frame: Frame) -> int:
    return (
        frame.height
        if isinstance(frame, pl.DataFrame)
        else int(frame.select(pl.len()).collect().item())
    )


def compute_temporal_match_rates(mbs: Side, gse: Gse) -> pl.DataFrame:
    """The enterprise's match rate by origination period.

    The period is whatever ``gse.period_cols`` names -- year-month for FNMA, year for
    FHLMC -- so both enterprises produce the same frame shape at different resolution.
    FHFA carries no origination period, so this dimension is one-sided.

    Args:
        mbs: The enterprise side, from `gse_sides`.
        gse: Which enterprise.

    Returns:
        One row per period, coarsest column first.
    """
    period_cols = list(gse.period_cols)
    in_window = mbs.universe.lazy().filter(
        pl.all_horizontal(pl.col(c).is_not_null() for c in period_cols)
        & pl.col(period_cols[0]).is_between(TEMPORAL_MIN_YEAR, TEMPORAL_MAX_YEAR)
    )
    return (
        rate_by(
            in_window,
            mbs.matched,
            mbs.on,
            period_cols,
            prefix=mbs.prefix,
            sort_by=period_cols[0],
            descending=False,
        )
        .collect()
        .sort(period_cols)
    )


def compute_dimension(sides: tuple[Side, Side], dimension: Dimension) -> pl.DataFrame:
    """Both sides' match rates over one dimension.

    Args:
        sides: The two sides, from `gse_sides`.
        dimension: Which dimension.

    Returns:
        One row per bin or category value.
    """
    return rate_by_sides(sides, dimension.column, prepare=dimension.prepare)


# =============================================================================
# Figure
#
# Every panel is a ``_panel_*`` helper that draws into an axis it is handed, so the
# grid can be rearranged without touching the drawing code. A panel with nothing to
# draw blanks its own axis.
# =============================================================================


def _category_labels(dimension: Dimension, gse: Gse) -> dict[int, str]:
    """Code -> label for a grouped panel; channel codes differ between enterprises."""
    return (
        gse.channel_labels if dimension.column == "channel" else CATEGORY_LABELS[dimension.column]
    )


def _gse_series(gse: Gse) -> tuple[RateSeries, RateSeries]:
    """The two sides every panel draws: the enterprise's rate and FHFA's."""
    return (
        RateSeries("mbs_match_rate", "mbs_total", MBS_COLOR, gse.label, "o"),
        RateSeries("fhfa_match_rate", "fhfa_total", FHFA_COLOR, "FHFA", "s"),
    )


def _panel_temporal(ax: Any, rates_df: pl.DataFrame, overall: dict[str, Any], gse: Gse) -> None:
    """Match rates by origination period, against both overall rates."""
    df = rates_df.sort(list(gse.period_cols))
    mbs_rates = df["mbs_match_rate"].to_list()
    if not finite_rates(mbs_rates):
        ax.set_axis_off()
        return
    mbs_overall, fhfa_overall = overall["mbs_match_rate"], overall["fhfa_match_rate"]
    x = np.arange(len(df))
    years = df[gse.period_cols[0]].to_list()
    if len(gse.period_cols) == 2:
        tick_labels = [f"{m:02d}/{y}" for y, m in zip(years, df[gse.period_cols[1]].to_list())]
    else:
        tick_labels = [str(y) for y in years]
    if len(df) <= TEMPORAL_BAR_LIMIT:
        ax.bar(x, mbs_rates, color=MBS_COLOR, alpha=0.8, width=0.6, label=f"{gse.label} period")
        for i, rate in enumerate(mbs_rates):
            if rate is not None and not np.isnan(rate):
                ax.text(i, rate + 0.005, f"{rate:.1%}", ha="center", va="bottom", fontsize=8)
        stride, headroom = 1, LABELLED_BAR_HEADROOM
    else:
        ax.plot(
            x,
            mbs_rates,
            color=MBS_COLOR,
            linewidth=2,
            marker="o",
            markersize=4,
            label=f"{gse.label} period",
        )
        stride, headroom = max(1, len(df) // 12), 0.02
    for value, color, label in (
        (mbs_overall, MBS_COLOR, gse.label),
        (fhfa_overall, FHFA_COLOR, "FHFA"),
    ):
        ax.axhline(
            value,
            color=color,
            linestyle="--",
            alpha=0.8,
            linewidth=1.5,
            label=f"{label} overall ({value:.1%})",
        )
    ax.set_xticks(x[::stride])
    ax.set_xticklabels(tick_labels[::stride], rotation=45, ha="right", fontsize=8)
    set_rate_ylim(ax, [*mbs_rates, mbs_overall, fhfa_overall], headroom=headroom)
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(True, alpha=0.3)


def _draw_panel(ax: Any, gse_results: dict[str, Any], gse: Gse, key: str) -> None:
    """Draw one cell: a dimension's panel (or the temporal one) for one enterprise."""
    rates_df = gse_results.get(key)
    if rates_df is None or len(rates_df) == 0:
        ax.set_axis_off()
        return
    if key == "temporal":
        _panel_temporal(ax, rates_df, gse_results["overall"], gse)
        noun = "Origination Period"
    else:
        dimension = DIMENSIONS_BY_KEY[key]
        noun = dimension.xlabel.split(" (")[0]
        if dimension.kind == "continuous":
            panel_rate_vs_bin(
                ax,
                rates_df,
                dimension.column,
                _gse_series(gse),
                xlabel=dimension.xlabel,
                x_scale=dimension.x_scale,
            )
        else:
            panel_grouped_bars(
                ax,
                rates_df,
                dimension.column,
                _gse_series(gse),
                _category_labels(dimension, gse),
                xlabel=dimension.xlabel,
            )
    ax.set_ylabel("Match Rate")
    ax.set_title(f"{gse.label}-FHFA -- by {noun}", fontsize=11)


DIMENSIONS_BY_KEY = {d.key: d for d in DIMENSIONS}
#: Figure rows, top to bottom.
PANEL_KEYS = ("temporal", *DIMENSIONS_BY_KEY)


def plot_validation_panel(
    results: dict[str, Any],
    gses: Sequence[Gse],
    output_path: Path | None = None,
    interactive: bool = False,
) -> None:
    """Render the validation figure: one row per dimension, one column per GSE.

    Rows: origination period, then loan amount, LTV, interest rate, DTI, loan
    purpose, channel, and occupancy. Each panel carries the enterprise's match rate
    against FHFA's over the same dimension.

    Args:
        results: ``{gse_key: {dimension_key: rates_df, "overall": {...}}}``.
        gses: Enterprises to draw, one column each, in order.
        output_path: Where to save the figure (PNG). Skipped if ``None``.
        interactive: Whether to display the figure interactively.
    """
    plt = get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return
    drawn = [gse for gse in gses if results.get(gse.key)]
    if not drawn:
        print("No validation results to plot")
        return
    fig = plt.figure(figsize=(8.5 * len(drawn), PANEL_HEIGHT_IN * len(PANEL_KEYS) + 1.5))
    grid = panel_gridspec(
        fig,
        len(PANEL_KEYS),
        len(drawn),
        title=f"FHFA-MBS match validation ({' and '.join(gse.label for gse in drawn)} against FHFA public-use data)",
        title_fontsize=14,
    )
    for row, key in enumerate(PANEL_KEYS):
        for col, gse in enumerate(drawn):
            _draw_panel(fig.add_subplot(grid[row, col]), results[gse.key], gse, key)
    save_and_close(fig, output_path, interactive)


# =============================================================================
# FHFA->UMBS ARM coverage
#
# The SF loan-level files carry ARMs only in their non-standard releases, which end
# early, so the SF crosswalks reach few FHFA ARM records after 2021. The ARM
# crosswalk pairs those records with the UMBS disclosures instead. This measures, per
# acquisition year, how many FHFA ARM records each crosswalk set reaches.
# =============================================================================


def compute_arm_coverage(
    gse: Gse, min_year: int = ARM_START_YEAR, max_year: int = ARM_END_YEAR
) -> pl.DataFrame:
    """FHFA ARM records matched by acquisition year, without and with the ARM crosswalk.

    An ARM record is one whose introductory-rate period is not in
    ``NON_ARM_INTRO_PERIODS``, the population the ARM pass matches. A record counts
    as matched by the SF crosswalk when that year's SF crosswalk carries its id, and
    as matched with the ARM crosswalk when either crosswalk does.

    Args:
        gse: Which enterprise.
        min_year: First FHFA acquisition year.
        max_year: Last FHFA acquisition year.

    Returns:
        One row per acquisition year with an SF crosswalk: ``fhfa_year``,
        ``arm_records``, ``sf_matched``, ``combined_matched``, ``sf_rate`` and
        ``combined_rate``. Empty if the ARM crosswalk has not been built.
    """
    from mortgage_data_manager.matching.match_fhfa_mbs.config import FHFAMBSMatchingConfig as Config
    from mortgage_data_manager.matching.match_fhfa_mbs.inputs import SPECS
    from mortgage_data_manager.matching.match_fhfa_mbs.matching import crosswalk_path, load_fhfa
    from mortgage_data_manager.matching.match_fhfa_mbs.matching_arm import (
        CROSSWALK_NAME,
        NON_ARM_INTRO_PERIODS,
    )

    spec = SPECS[gse.key]
    arm_path = Config.FHFA_MBS_CROSSWALK_DIR / CROSSWALK_NAME
    if not arm_path.exists():
        print(f"  Skipping ARM coverage: {arm_path.name} not found")
        return pl.DataFrame()
    arm = (
        pl.scan_parquet(arm_path)
        .filter(pl.col("fhfa_enterprise") == spec.enterprise_flag)
        .select(
            pl.col("fhfa_year").cast(pl.Int32),
            pl.col("fhfa_record_id").cast(pl.Int64),
            pl.lit(True).alias("in_arm"),
        )
    )
    period = pl.col("introductory_rate_period")
    years = []
    for year in range(min_year, max_year + 1):
        sf_path = crosswalk_path(Config.FHFA_MBS_CROSSWALK_DIR, year, spec)
        if not sf_path.exists():
            print(f"  Skipping ARM coverage {year}: {sf_path.name} not found")
            continue
        sf_ids = pl.read_parquet(sf_path, columns=["fhfa_record_id"])["fhfa_record_id"]
        years.append(
            load_fhfa(year, spec)
            .filter(period.is_not_null() & ~period.is_in(NON_ARM_INTRO_PERIODS))
            .select(
                pl.lit(year, dtype=pl.Int32).alias("fhfa_year"),
                pl.col("record_number").cast(pl.Int64).alias("fhfa_record_id"),
            )
            .with_columns(pl.col("fhfa_record_id").is_in(sf_ids.cast(pl.Int64)).alias("in_sf"))
        )
    if not years:
        return pl.DataFrame()
    in_sf, in_arm = pl.col("in_sf"), pl.col("in_arm").fill_null(False)
    return (
        pl.concat(years)
        .join(arm, on=["fhfa_year", "fhfa_record_id"], how="left")
        .group_by("fhfa_year")
        .agg(
            pl.len().alias("arm_records"),
            in_sf.sum().alias("sf_matched"),
            (in_sf | in_arm).sum().alias("combined_matched"),
        )
        .with_columns(
            (pl.col("sf_matched") / pl.col("arm_records")).alias("sf_rate"),
            (pl.col("combined_matched") / pl.col("arm_records")).alias("combined_rate"),
        )
        .sort("fhfa_year")
        .collect()
    )


def _print_arm_coverage(coverage: pl.DataFrame, gse: Gse) -> None:
    """Print one enterprise's ARM coverage by year and over all years."""
    print(f"\n{gse.label} FHFA ARM records matched: SF crosswalk -> with the ARM crosswalk")
    for row in coverage.iter_rows(named=True):
        print(
            f"  {row['fhfa_year']}: {row['arm_records']:,} records, "
            f"{row['sf_rate']:.1%} -> {row['combined_rate']:.1%}"
        )
    records, sf, combined = (
        int(coverage[c].sum()) for c in ("arm_records", "sf_matched", "combined_matched")
    )
    print(f"  All: {records:,} records, {sf / records:.1%} -> {combined / records:.1%}")


def plot_arm_coverage(
    coverage: dict[str, pl.DataFrame],
    gses: Sequence[Gse],
    output_path: Path | None = None,
    interactive: bool = False,
) -> None:
    """Render the ARM coverage figure: one panel per enterprise, paired bars per year.

    Args:
        coverage: ``{gse_key: compute_arm_coverage frame}``.
        gses: Enterprises to draw, one panel each, in order.
        output_path: Where to save the figure (PNG). Skipped if ``None``.
        interactive: Whether to display the figure interactively.
    """
    from matplotlib.ticker import PercentFormatter

    plt = get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return
    drawn = [gse for gse in gses if len(coverage.get(gse.key, pl.DataFrame()))]
    if not drawn:
        print("No ARM coverage to plot")
        return
    fig, axes = plt.subplots(1, len(drawn), figsize=(5.5 * len(drawn), 3.8), squeeze=False)
    width = 0.4
    for ax, gse in zip(axes[0], drawn):
        df = coverage[gse.key]
        x = np.arange(len(df))
        ax.bar(x - width / 2, df["sf_rate"], width, color=COLORS["grey"], label="SF crosswalk")
        ax.bar(
            x + width / 2,
            df["combined_rate"],
            width,
            color=COLORS["midnight"],
            label="with the FHFA→UMBS ARM crosswalk",
        )
        ax.set_xticks(x)
        ax.set_xticklabels([str(y) for y in df["fhfa_year"]])
        ax.set_title(f"{gse.label}: FHFA ARM records matched")
        ax.yaxis.set_major_formatter(PercentFormatter(1.0))
        ax.set_ylim(0, 1.05)
        ax.grid(axis="y", alpha=0.3)
        ax.set_axisbelow(True)
    # One legend under both panels: inside an axis it would cover the early-year bars.
    fig.legend(*axes[0][0].get_legend_handles_labels(), loc="lower center", ncol=2, fontsize=9)
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    save_and_close(fig, output_path, interactive)


# =============================================================================
# Report
# =============================================================================


def _print_category(
    title: str, rates_df: pl.DataFrame, column: str, labels: dict[int, str], mbs_label: str
) -> None:
    print(f"\n{title}:")
    for row in rates_df.iter_rows(named=True):
        fmt = lambda r: f"{r:.1%}" if r is not None else "N/A"  # noqa: E731
        print(
            f"  {labels.get(row[column], str(row[column]))}: {mbs_label}={fmt(row['mbs_match_rate'])}, FHFA={fmt(row['fhfa_match_rate'])}"
        )


def compute_gse_validation(gse: Gse, min_year: int, max_year: int) -> dict[str, Any]:
    """Load one enterprise and compute every dimension's match rates.

    Args:
        gse: Which enterprise to validate.
        min_year: First acquisition year to include.
        max_year: Last acquisition year to include.

    Returns:
        ``{"overall": {...}, "temporal": df, dimension_key: df, ...}``. Empty if the
        enterprise's crosswalks have not been built.
    """
    print(f"\nLoading {gse.label} validation data for {min_year}-{max_year}...")
    try:
        crosswalk, mbs, fhfa = gse.loader(min_year, max_year)
    except (FileNotFoundError, ValueError) as e:
        print(f"\nError: {e}")
        return {}
    sides = gse_sides(crosswalk, mbs, fhfa, gse)

    results: dict[str, Any] = {"overall": compute_overall_match_rates(crosswalk, sides)}
    print("  Computing match rates by origination period...")
    results["temporal"] = compute_temporal_match_rates(sides[0], gse)
    for dimension in DIMENSIONS:
        print(f"  Computing match rates by {dimension.key}...")
        results[dimension.key] = compute_dimension(sides, dimension)

    overall = results["overall"]
    print("\n" + "=" * 60)
    print(f"{gse.label}-FHFA MATCHING SUMMARY STATISTICS")
    print("=" * 60)
    print(f"  Total matched pairs: {overall['total_matches']:,}")
    print(f"  {gse.label} loans: {overall['mbs_total']:,}")
    print(f"  FHFA records: {overall['fhfa_total']:,}")
    print(f"  {gse.label} match rate: {overall['mbs_match_rate']:.1%}")
    print(f"  FHFA match rate: {overall['fhfa_match_rate']:.1%}")
    _print_category(
        "Match Rates by Loan Purpose",
        results["loan_purpose"],
        "loan_purpose",
        CATEGORY_LABELS["loan_purpose"],
        gse.label,
    )
    _print_category(
        "Match Rates by Channel", results["channel"], "channel", gse.channel_labels, gse.label
    )
    print("=" * 60)
    return results


def run_validation(
    enterprise: Literal["fnma", "fhlmc", "both"] = "both",
    save_plots: bool = True,
    show_plots: bool = False,
    fnma_min_year: int = FNMA_START_YEAR,
    fnma_max_year: int = FNMA_END_YEAR,
    fhlmc_min_year: int = FHLMC_START_YEAR,
    fhlmc_max_year: int = FHLMC_END_YEAR,
    arm_coverage: bool = True,
) -> dict[str, Any]:
    """Run the FHFA-MBS validation and write the combined figure.

    Args:
        enterprise: Which enterprise to validate ("fnma", "fhlmc", or "both").
        save_plots: Whether to write the figures.
        show_plots: Whether to display the figures interactively.
        fnma_min_year: First acquisition year for FNMA.
        fnma_max_year: Last acquisition year for FNMA.
        fhlmc_min_year: First acquisition year for FHLMC.
        fhlmc_max_year: Last acquisition year for FHLMC.
        arm_coverage: Also measure ARM coverage (`compute_arm_coverage`, over
            ``ARM_START_YEAR``-``ARM_END_YEAR``) and draw its figure.

    Returns:
        ``{gse_key: {"overall": {...}, "temporal": df, dimension_key: df, ...}}``,
        plus ``"arm_coverage"`` per enterprise when it is measured.
    """
    year_overrides = {
        "fnma": (fnma_min_year, fnma_max_year),
        "fhlmc": (fhlmc_min_year, fhlmc_max_year),
    }
    selected = [gse for gse in GSES if enterprise in (gse.key, "both")]
    # A one-enterprise run gets its own file names so it cannot replace the published
    # two-column figures with a single column.
    suffix = "" if enterprise == "both" else f"_{enterprise}"
    figure, arm_figure = (
        FIGURES_DIR / f"{stem}{suffix}.png" if save_plots else None
        for stem in (VALIDATION_FIGURE, ARM_COVERAGE_FIGURE)
    )
    results: dict[str, Any] = {}
    for gse in selected:
        print("\n" + "=" * 70)
        print(f"{gse.label}-FHFA VALIDATION")
        print("=" * 70)
        gse_results = compute_gse_validation(gse, *year_overrides[gse.key])
        if gse_results:
            results[gse.key] = gse_results
    if save_plots or show_plots:
        plot_validation_panel(results, selected, figure, show_plots)
    if arm_coverage:
        print("\n" + "=" * 70)
        print("FHFA->UMBS ARM COVERAGE")
        print("=" * 70)
        coverage = {}
        for gse in selected:
            frame = compute_arm_coverage(gse)
            if len(frame):
                _print_arm_coverage(frame, gse)
                coverage[gse.key] = frame
                results.setdefault(gse.key, {})["arm_coverage"] = frame
        if save_plots or show_plots:
            plot_arm_coverage(coverage, selected, arm_figure, show_plots)
    return results
