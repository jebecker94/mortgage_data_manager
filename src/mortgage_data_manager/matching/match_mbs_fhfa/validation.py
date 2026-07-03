"""MBS-FHFA Match Validation Module.

This module provides validation analyses for the MBS-FHFA matching workflow,
supporting both FNMA (Fannie Mae) and FHLMC (Freddie Mac) matching to FHFA data.

Validation analyses:
1. Match rates over time (yearly for FHLMC, monthly for FNMA)
2. Match rates by loan characteristics (amount, rate, LTV, DTI)
3. Match rates by categorical variables (purpose, channel, occupancy)

The goal is to demonstrate that match quality is consistent across time
and loan characteristics, ensuring the matched sample is representative.

Output:
- All figures: docs/matching/figures/mbs_fhfa/

Usage:
    # From the command line
    mortgage-data match mbs-fhfa validate --enterprise fnma
    mortgage-data match mbs-fhfa validate --enterprise fhlmc
    mortgage-data match mbs-fhfa validate --enterprise both

    # Programmatically
    from mortgage_data_manager.matching.match_mbs_fhfa.validation import run_validation
    run_validation(enterprise="fnma")
    run_validation(enterprise="fhlmc")
    run_validation(enterprise="both")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import numpy as np
import polars as pl

# Check for matplotlib availability
try:
    import matplotlib

    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    matplotlib = None  # type: ignore


def _get_pyplot(interactive: bool = True):
    """Get pyplot with appropriate backend."""
    if not HAS_MATPLOTLIB:
        return None
    if not interactive:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


# =============================================================================
# Configuration
# =============================================================================

# Default year ranges (source data availability)
FNMA_START_YEAR = 2019
FNMA_END_YEAR = 2024
FHLMC_START_YEAR = 2019
FHLMC_END_YEAR = 2024

# Output directories for figures
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent
FNMA_FIGURES_DIR = PROJECT_ROOT / "docs" / "matching" / "figures" / "mbs_fhfa"
FHLMC_FIGURES_DIR = PROJECT_ROOT / "docs" / "matching" / "figures" / "mbs_fhfa"

# Colors (Jonathan's palette)
MBS_COLOR = "#145A32"  # Hunter Green - for MBS data (FNMA/FHLMC)
FHFA_COLOR = "#D4AC0D"  # Burnished Gold - for FHFA data
ACCENT_COLOR = "#900C3F"  # Mulberry - for highlights/third category


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
    from mortgage_data_manager.matching.match_mbs_fhfa.config import CROSSWALK_OUTPUT_DIR
    from mortgage_data_manager.matching.match_mbs_fhfa.matching_fnma import (
        load_fhfa_data,
        load_fnma_data,
        prepare_fhfa_for_matching,
        prepare_fnma_for_matching,
    )

    crosswalks = []
    fnma_dfs = []
    fhfa_dfs = []
    years_loaded = []

    for year in range(min_year, max_year + 1):
        crosswalk_path = CROSSWALK_OUTPUT_DIR / f"fnma_fhfa_crosswalk_{year}.parquet"
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

            fnma_raw = load_fnma_data(year)
            fnma_month_lf = fnma_raw.select(
                [
                    pl.col("Loan Identifier").alias("fnma_loan_id"),
                    pl.col("Origination Date")
                    .str.slice(0, 2)
                    .cast(pl.Int64, strict=False)
                    .alias("orig_month"),
                ]
            )

            fnma_prepared = prepare_fnma_for_matching(fnma_raw)
            fnma_prepared = fnma_prepared.join(fnma_month_lf, on="fnma_loan_id", how="left")
            fnma_prepared = fnma_prepared.with_columns(
                [
                    pl.col("year").alias("orig_year"),
                    pl.lit(year).alias("acq_year"),
                ]
            )
            fnma_df = fnma_prepared.collect()
            fnma_dfs.append(fnma_df)

            fhfa_raw = load_fhfa_data(year)
            fhfa_prepared = prepare_fhfa_for_matching(fhfa_raw, year=year)
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
    from mortgage_data_manager.fhfa.config import FHFAConfig
    from mortgage_data_manager.fhlmc.config import FHLMCConfig
    from mortgage_data_manager.matching.match_mbs_fhfa.config import CROSSWALK_OUTPUT_DIR
    from mortgage_data_manager.matching.match_mbs_fhfa.matching_fhlmc import (
        prepare_fhfa_for_matching as prepare_fhfa,
    )
    from mortgage_data_manager.matching.match_mbs_fhfa.matching_fnma import STATE_FIPS

    # Load yearly crosswalks and combine on-the-fly
    crosswalks = []
    years_loaded = []

    for year in range(min_year, max_year + 1):
        crosswalk_path = CROSSWALK_OUTPUT_DIR / f"fhlmc_fhfa_crosswalk_{year}.parquet"
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

    # Deduplicate by FHLMC loan ID, keeping earliest acquisition year match
    # (same loan can match to different FHFA records across years due to year-by-year matching)
    n_before_dedup = len(crosswalk)
    crosswalk = crosswalk.sort("fhfa_year").unique(subset=["fhlmc_loan_id"], keep="first")
    n_after_dedup = len(crosswalk)
    if n_before_dedup != n_after_dedup:
        print(
            f"  After dedup by fhlmc_loan_id: {n_after_dedup:,} matches (removed {n_before_dedup - n_after_dedup:,} duplicates)"
        )

    # Load FHLMC data for the validation year range and deduplicate by loan ID
    fhlmc_dir = FHLMCConfig.FHLMC_BRONZE_DIR / "origination"
    fhlmc_files = sorted(
        f
        for f in fhlmc_dir.glob("historical_data_*.parquet")
        if any(f"_{y}Q" in f.name for y in range(min_year, max_year + 1))
    )

    print(f"  Loading {len(fhlmc_files)} FHLMC files ({min_year}-{max_year})...")
    fhlmc_raw = pl.concat([pl.read_parquet(f) for f in fhlmc_files], how="diagonal_relaxed")

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
        pl.col("state_abbr").replace_strict(STATE_FIPS, default=None).alias("state_fips")
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

    # Deduplicate FHLMC by loan ID (keep first occurrence)
    fhlmc = fhlmc.unique(subset=["fhlmc_loan_id"], keep="first")
    print(f"  FHLMC after dedup: {len(fhlmc):,} unique loans")

    # Load all FHFA data for Freddie Mac
    fhfa_dfs = []
    for year in range(min_year, max_year + 1):
        fhfa_file = FHFAConfig.FHFA_SILVER_DIR / "sf_c" / f"sf_c_{year}.parquet"
        if fhfa_file.exists():
            df = pl.read_parquet(fhfa_file)
            df = df.filter(pl.col("enterprise_flag") == 2)  # Freddie Mac only
            df = prepare_fhfa(df)
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
# Match Rate Calculations (Shared)
# =============================================================================


def compute_match_rate_by_field(
    crosswalk: pl.DataFrame,
    source_df: pl.DataFrame,
    id_col: str,
    group_col: str,
    bin_expr: pl.Expr | None = None,
) -> pl.DataFrame:
    """Generic match rate calculation by grouping field.

    Args:
        crosswalk: Crosswalk with matched IDs
        source_df: Full source data
        id_col: ID column name in source data
        group_col: Column to group by
        bin_expr: Optional expression to create bins

    Returns:
        DataFrame with group_col, total, matched, match_rate
    """
    matched_ids = crosswalk.select(id_col).unique()

    if bin_expr is not None:
        source_with_bin = source_df.with_columns(bin_expr.alias("_group_col"))
        group_col_name = "_group_col"
    else:
        source_with_bin = source_df
        group_col_name = group_col

    totals = source_with_bin.group_by(group_col_name).agg(pl.len().alias("total"))
    matched = (
        source_with_bin.join(matched_ids, on=id_col, how="inner")
        .group_by(group_col_name)
        .agg(pl.len().alias("matched"))
    )

    result = (
        totals.join(matched, on=group_col_name, how="left")
        .with_columns(
            pl.col("matched").fill_null(0),
            (pl.col("matched") / pl.col("total")).alias("match_rate"),
        )
        .sort(group_col_name)
    )

    if bin_expr is not None:
        result = result.rename({"_group_col": group_col})

    return result


def compute_overall_match_rates(
    crosswalk: pl.DataFrame,
    mbs: pl.DataFrame,
    fhfa: pl.DataFrame,
    mbs_id_col: str,
) -> dict[str, Any]:
    """Compute overall match rates from both perspectives."""
    mbs_matched_ids = crosswalk.select(mbs_id_col).unique()
    fhfa_matched_ids = crosswalk.select("fhfa_record_id").unique()

    mbs_total = len(mbs)
    fhfa_total = len(fhfa)

    mbs_matched = len(mbs.join(mbs_matched_ids, on=mbs_id_col, how="inner"))
    fhfa_matched = len(fhfa.join(fhfa_matched_ids, on="fhfa_record_id", how="inner"))

    return {
        "total_matches": len(crosswalk),
        "mbs_total": mbs_total,
        "fhfa_total": fhfa_total,
        "mbs_matched": mbs_matched,
        "fhfa_matched": fhfa_matched,
        "mbs_match_rate": mbs_matched / mbs_total if mbs_total > 0 else 0,
        "fhfa_match_rate": fhfa_matched / fhfa_total if fhfa_total > 0 else 0,
    }


# =============================================================================
# Match Rate Calculations - FNMA
# =============================================================================


def compute_fnma_temporal_match_rates(
    crosswalk: pl.DataFrame,
    fnma: pl.DataFrame,
    fhfa: pl.DataFrame,
) -> pl.DataFrame:
    """Compute FNMA match rates by origination year-month."""
    fnma_matched_ids = crosswalk.select("fnma_loan_id").unique()

    fnma_filtered = fnma.filter(
        pl.col("orig_month").is_not_null()
        & pl.col("orig_year").is_not_null()
        & (pl.col("orig_year") >= 2018)
        & (pl.col("orig_year") <= 2024)
    )

    fnma_monthly_totals = fnma_filtered.group_by(["orig_year", "orig_month"]).agg(
        pl.len().alias("mbs_total")
    )

    fnma_monthly_matched = (
        fnma_filtered.join(fnma_matched_ids, on="fnma_loan_id", how="inner")
        .group_by(["orig_year", "orig_month"])
        .agg(pl.len().alias("mbs_matched"))
    )

    fnma_monthly = (
        fnma_monthly_totals.join(fnma_monthly_matched, on=["orig_year", "orig_month"], how="left")
        .with_columns(
            pl.col("mbs_matched").fill_null(0),
            (pl.col("mbs_matched") / pl.col("mbs_total")).alias("mbs_match_rate"),
        )
        .sort(["orig_year", "orig_month"])
    )

    fhfa_matched_ids = crosswalk.select("fhfa_record_id").unique()
    fhfa_total = len(fhfa)
    fhfa_matched = len(fhfa.join(fhfa_matched_ids, on="fhfa_record_id", how="inner"))
    fhfa_overall_rate = fhfa_matched / fhfa_total if fhfa_total > 0 else 0

    fnma_total = len(fnma)
    fnma_matched = len(fnma.join(fnma_matched_ids, on="fnma_loan_id", how="inner"))
    mbs_overall_rate = fnma_matched / fnma_total if fnma_total > 0 else 0

    fnma_monthly = fnma_monthly.with_columns(
        [
            pl.lit(fhfa_overall_rate).alias("fhfa_overall_rate"),
            pl.lit(mbs_overall_rate).alias("mbs_overall_rate"),
        ]
    )

    return fnma_monthly


# =============================================================================
# Match Rate Calculations - FHLMC
# =============================================================================


def compute_fhlmc_temporal_match_rates(
    crosswalk: pl.DataFrame,
    fhlmc: pl.DataFrame,
    fhfa: pl.DataFrame,
) -> pl.DataFrame:
    """Compute FHLMC match rates by origination year."""
    fhlmc_matched_ids = crosswalk.select("fhlmc_loan_id").unique()

    # Filter to valid origination years
    fhlmc_filtered = fhlmc.filter(
        pl.col("origination_year").is_not_null()
        & (pl.col("origination_year") >= 2018)
        & (pl.col("origination_year") <= 2024)
    )

    fhlmc_yearly_totals = fhlmc_filtered.group_by("origination_year").agg(
        pl.len().alias("mbs_total")
    )

    fhlmc_yearly_matched = (
        fhlmc_filtered.join(fhlmc_matched_ids, on="fhlmc_loan_id", how="inner")
        .group_by("origination_year")
        .agg(pl.len().alias("mbs_matched"))
    )

    fhlmc_yearly = (
        fhlmc_yearly_totals.join(fhlmc_yearly_matched, on="origination_year", how="left")
        .with_columns(
            pl.col("mbs_matched").fill_null(0),
            (pl.col("mbs_matched") / pl.col("mbs_total")).alias("mbs_match_rate"),
        )
        .sort("origination_year")
        .rename({"origination_year": "year"})  # Keep "year" for plotting compatibility
    )

    fhfa_matched_ids = crosswalk.select("fhfa_record_id").unique()
    fhfa_total = len(fhfa)
    fhfa_matched = len(fhfa.join(fhfa_matched_ids, on="fhfa_record_id", how="inner"))
    fhfa_overall_rate = fhfa_matched / fhfa_total if fhfa_total > 0 else 0

    fhlmc_total = len(fhlmc_filtered)
    fhlmc_matched = len(fhlmc_filtered.join(fhlmc_matched_ids, on="fhlmc_loan_id", how="inner"))
    mbs_overall_rate = fhlmc_matched / fhlmc_total if fhlmc_total > 0 else 0

    fhlmc_yearly = fhlmc_yearly.with_columns(
        [
            pl.lit(fhfa_overall_rate).alias("fhfa_overall_rate"),
            pl.lit(mbs_overall_rate).alias("mbs_overall_rate"),
        ]
    )

    return fhlmc_yearly


# =============================================================================
# Match Rate Calculations - Shared by Loan Characteristics
# =============================================================================


def compute_match_rates_by_loan_amount(
    crosswalk: pl.DataFrame,
    mbs: pl.DataFrame,
    fhfa: pl.DataFrame,
    mbs_id_col: str,
    bin_size: int = 10000,
    max_amount: int = 750000,
) -> pl.DataFrame:
    """Compute match rates by loan amount bins ($10K bins)."""

    def amount_bin_expr(col_name: str) -> pl.Expr:
        return ((pl.col(col_name) / bin_size).floor() * bin_size + bin_size / 2).cast(pl.Int64)

    mbs_rates = compute_match_rate_by_field(
        crosswalk,
        mbs.filter(pl.col("loan_amount") <= max_amount),
        mbs_id_col,
        "loan_amount_bin",
        amount_bin_expr("loan_amount"),
    ).rename({"total": "mbs_total", "matched": "mbs_matched", "match_rate": "mbs_match_rate"})

    fhfa_rates = compute_match_rate_by_field(
        crosswalk,
        fhfa.filter(pl.col("loan_amount") <= max_amount),
        "fhfa_record_id",
        "loan_amount_bin",
        amount_bin_expr("loan_amount"),
    ).rename({"total": "fhfa_total", "matched": "fhfa_matched", "match_rate": "fhfa_match_rate"})

    result = mbs_rates.join(fhfa_rates, on="loan_amount_bin", how="full", coalesce=True).sort(
        "loan_amount_bin"
    )
    return result


def compute_match_rates_by_ltv(
    crosswalk: pl.DataFrame,
    mbs: pl.DataFrame,
    fhfa: pl.DataFrame,
    mbs_id_col: str,
) -> pl.DataFrame:
    """Compute match rates by LTV bins (1% intervals)."""

    def ltv_bin_expr(col_name: str) -> pl.Expr:
        return pl.col(col_name).round(0).cast(pl.Int64)

    mbs_filtered = mbs.filter(pl.col("ltv").is_not_null() & (pl.col("ltv") <= 100))
    fhfa_filtered = fhfa.filter(pl.col("ltv").is_not_null() & (pl.col("ltv") <= 100))

    mbs_rates = compute_match_rate_by_field(
        crosswalk,
        mbs_filtered,
        mbs_id_col,
        "ltv_bin",
        ltv_bin_expr("ltv"),
    ).rename({"total": "mbs_total", "matched": "mbs_matched", "match_rate": "mbs_match_rate"})

    fhfa_rates = compute_match_rate_by_field(
        crosswalk,
        fhfa_filtered,
        "fhfa_record_id",
        "ltv_bin",
        ltv_bin_expr("ltv"),
    ).rename({"total": "fhfa_total", "matched": "fhfa_matched", "match_rate": "fhfa_match_rate"})

    result = mbs_rates.join(fhfa_rates, on="ltv_bin", how="full", coalesce=True).sort("ltv_bin")
    return result


def compute_match_rates_by_interest_rate(
    crosswalk: pl.DataFrame,
    mbs: pl.DataFrame,
    fhfa: pl.DataFrame,
    mbs_id_col: str,
    bin_size: float = 0.125,
    min_rate: float = 2.0,
    max_rate: float = 9.0,
) -> pl.DataFrame:
    """Compute match rates by interest rate bins (0.125% intervals)."""

    def rate_bin_expr(col_name: str) -> pl.Expr:
        return ((pl.col(col_name) / bin_size).round() * bin_size).cast(pl.Float64)

    mbs_filtered = mbs.filter(
        pl.col("interest_rate").is_not_null()
        & (pl.col("interest_rate") >= min_rate)
        & (pl.col("interest_rate") <= max_rate)
    )
    fhfa_filtered = fhfa.filter(
        pl.col("interest_rate").is_not_null()
        & (pl.col("interest_rate") >= min_rate)
        & (pl.col("interest_rate") <= max_rate)
    )

    mbs_rates = compute_match_rate_by_field(
        crosswalk,
        mbs_filtered,
        mbs_id_col,
        "interest_rate_bin",
        rate_bin_expr("interest_rate"),
    ).rename({"total": "mbs_total", "matched": "mbs_matched", "match_rate": "mbs_match_rate"})

    fhfa_rates = compute_match_rate_by_field(
        crosswalk,
        fhfa_filtered,
        "fhfa_record_id",
        "interest_rate_bin",
        rate_bin_expr("interest_rate"),
    ).rename({"total": "fhfa_total", "matched": "fhfa_matched", "match_rate": "fhfa_match_rate"})

    result = mbs_rates.join(fhfa_rates, on="interest_rate_bin", how="full", coalesce=True).sort(
        "interest_rate_bin"
    )
    return result


def compute_match_rates_by_dti(
    crosswalk: pl.DataFrame,
    mbs: pl.DataFrame,
    fhfa: pl.DataFrame,
    mbs_id_col: str,
) -> pl.DataFrame:
    """Compute match rates by DTI using FHFA bin scheme."""

    def dti_bin_expr(col_name: str) -> pl.Expr:
        dti = pl.col(col_name)
        return (
            pl.when(dti < 20)
            .then(pl.lit(1))
            .when((dti >= 20) & (dti < 30))
            .then(pl.lit(2))
            .when((dti >= 30) & (dti < 36))
            .then(pl.lit(3))
            .when((dti >= 36) & (dti <= 49))
            .then(pl.lit(4))
            .when((dti > 49) & (dti <= 60))
            .then(pl.lit(5))
            .when(dti > 60)
            .then(pl.lit(6))
            .otherwise(None)
        ).cast(pl.Int64)

    mbs_filtered = mbs.filter(pl.col("dti").is_not_null())
    fhfa_filtered = fhfa.filter(pl.col("dti").is_not_null() & (pl.col("dti") != 99))

    mbs_rates = compute_match_rate_by_field(
        crosswalk,
        mbs_filtered,
        mbs_id_col,
        "dti_bin",
        dti_bin_expr("dti"),
    ).rename({"total": "mbs_total", "matched": "mbs_matched", "match_rate": "mbs_match_rate"})

    fhfa_rates = compute_match_rate_by_field(
        crosswalk,
        fhfa_filtered,
        "fhfa_record_id",
        "dti_bin",
        dti_bin_expr("dti"),
    ).rename({"total": "fhfa_total", "matched": "fhfa_matched", "match_rate": "fhfa_match_rate"})

    result = mbs_rates.join(fhfa_rates, on="dti_bin", how="full", coalesce=True).sort("dti_bin")
    return result


def compute_match_rates_by_category(
    crosswalk: pl.DataFrame,
    mbs: pl.DataFrame,
    fhfa: pl.DataFrame,
    mbs_id_col: str,
    category_col: str,
) -> pl.DataFrame:
    """Compute match rates by categorical field (loan_purpose, channel, occupancy)."""
    mbs_filtered = mbs.filter(pl.col(category_col).is_not_null())
    fhfa_filtered = fhfa.filter(pl.col(category_col).is_not_null())

    mbs_rates = compute_match_rate_by_field(
        crosswalk,
        mbs_filtered,
        mbs_id_col,
        category_col,
    ).rename({"total": "mbs_total", "matched": "mbs_matched", "match_rate": "mbs_match_rate"})

    fhfa_rates = compute_match_rate_by_field(
        crosswalk,
        fhfa_filtered,
        "fhfa_record_id",
        category_col,
    ).rename({"total": "fhfa_total", "matched": "fhfa_matched", "match_rate": "fhfa_match_rate"})

    result = mbs_rates.join(fhfa_rates, on=category_col, how="full", coalesce=True).sort(
        category_col
    )
    return result


# =============================================================================
# Plotting Functions
# =============================================================================


def plot_fnma_temporal_match_rates(
    temporal_df: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot FNMA temporal match rates by origination month."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(12, 6))

    df = temporal_df.sort(["orig_year", "orig_month"])
    x = np.arange(len(df))
    months = df["orig_month"].to_list()
    years = df["orig_year"].to_list()
    mbs_rates = df["mbs_match_rate"].to_list()
    mbs_overall = df["mbs_overall_rate"][0]
    fhfa_overall = df["fhfa_overall_rate"][0]

    ax.plot(
        x, mbs_rates, color=MBS_COLOR, linewidth=2, marker="o", markersize=5, label="FNMA Monthly"
    )
    ax.axhline(
        mbs_overall,
        color=MBS_COLOR,
        linestyle="--",
        alpha=0.7,
        linewidth=1.5,
        label=f"FNMA Overall ({mbs_overall:.1%})",
    )
    ax.axhline(
        fhfa_overall,
        color=FHFA_COLOR,
        linestyle="--",
        alpha=0.7,
        linewidth=1.5,
        label=f"FHFA Overall ({fhfa_overall:.1%})",
    )

    tick_labels = [f"{m:02d}/{y}" for m, y in zip(months, years)]
    ax.set_xticks(x[::3])
    ax.set_xticklabels(tick_labels[::3], rotation=45, ha="right", fontsize=9)

    ax.set_xlabel("Origination Month")
    ax.set_ylabel("Match Rate")
    ax.set_title("FNMA-FHFA Match Rates by Origination Month")

    all_rates = mbs_rates + [mbs_overall, fhfa_overall]
    valid_rates = [r for r in all_rates if r is not None and not np.isnan(r)]
    if valid_rates:
        y_min = max(0, min(valid_rates) - 0.05)
        y_max = min(1.0, max(valid_rates) + 0.02)
        ax.set_ylim(y_min, y_max)

    ax.legend(loc="lower right")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_fhlmc_temporal_match_rates(
    temporal_df: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot FHLMC temporal match rates by origination year."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(10, 6))

    df = temporal_df.sort("year")
    years = df["year"].to_list()
    mbs_rates = df["mbs_match_rate"].to_list()
    mbs_overall = df["mbs_overall_rate"][0]
    fhfa_overall = df["fhfa_overall_rate"][0]

    x = np.arange(len(years))

    ax.bar(x, mbs_rates, color=MBS_COLOR, alpha=0.8, label="FHLMC Yearly", width=0.6)
    ax.axhline(
        mbs_overall,
        color=MBS_COLOR,
        linestyle="--",
        alpha=0.9,
        linewidth=2,
        label=f"FHLMC Overall ({mbs_overall:.1%})",
    )
    ax.axhline(
        fhfa_overall,
        color=FHFA_COLOR,
        linestyle="--",
        alpha=0.9,
        linewidth=2,
        label=f"FHFA Overall ({fhfa_overall:.1%})",
    )

    ax.set_xticks(x)
    ax.set_xticklabels([str(y) for y in years], fontsize=11)

    for i, rate in enumerate(mbs_rates):
        if rate is not None and not np.isnan(rate):
            ax.text(i, rate + 0.01, f"{rate:.1%}", ha="center", va="bottom", fontsize=10)

    ax.set_xlabel("Origination Year")
    ax.set_ylabel("Match Rate")
    ax.set_title("FHLMC-FHFA Match Rates by Origination Year")

    all_rates = mbs_rates + [mbs_overall, fhfa_overall]
    valid_rates = [r for r in all_rates if r is not None and not np.isnan(r)]
    if valid_rates:
        y_min = max(0, min(valid_rates) - 0.05)
        y_max = min(1.0, max(valid_rates) + 0.05)
        ax.set_ylim(y_min, y_max)

    ax.legend(loc="lower right")
    ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_match_rates_by_continuous(
    rates_df: pl.DataFrame,
    bin_col: str,
    xlabel: str,
    title: str,
    mbs_label: str,
    output_path: Path | None = None,
    interactive: bool = True,
    x_scale: float = 1.0,
) -> None:
    """Plot match rates by continuous variable bins with density overlay."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(12, 6))

    x = np.array(rates_df[bin_col].to_list()) * x_scale

    mbs_total = rates_df["mbs_total"].to_numpy().astype(float)
    fhfa_total = rates_df["fhfa_total"].to_numpy().astype(float)
    mbs_total = np.nan_to_num(mbs_total, nan=0)
    fhfa_total = np.nan_to_num(fhfa_total, nan=0)

    mbs_pdf = mbs_total / mbs_total.sum() if mbs_total.sum() > 0 else mbs_total
    fhfa_pdf = fhfa_total / fhfa_total.sum() if fhfa_total.sum() > 0 else fhfa_total

    ax2 = ax.twinx()
    ax2.fill_between(x, mbs_pdf, alpha=0.15, color=MBS_COLOR, label=f"{mbs_label} Density")
    ax2.fill_between(x, fhfa_pdf, alpha=0.15, color=FHFA_COLOR, label="FHFA Density")
    ax2.set_ylabel("Density (PDF)", color="gray")
    ax2.tick_params(axis="y", labelcolor="gray")
    max_pdf = max(
        max(mbs_pdf) if len(mbs_pdf) > 0 else 0, max(fhfa_pdf) if len(fhfa_pdf) > 0 else 0
    )
    ax2.set_ylim(0, max_pdf * 1.3 if max_pdf > 0 else 1)

    mbs_rates = rates_df["mbs_match_rate"].to_list()
    fhfa_rates = rates_df["fhfa_match_rate"].to_list()

    ax.plot(
        x,
        mbs_rates,
        color=MBS_COLOR,
        linewidth=2,
        marker="o",
        markersize=5,
        label=f"{mbs_label} Match Rate",
        zorder=10,
    )
    ax.plot(
        x,
        fhfa_rates,
        color=FHFA_COLOR,
        linewidth=2,
        marker="s",
        markersize=5,
        label="FHFA Match Rate",
        zorder=10,
    )

    ax.set_xlabel(xlabel)
    ax.set_ylabel("Match Rate")
    ax.set_title(title)

    all_rates = mbs_rates + fhfa_rates
    valid_rates = [r for r in all_rates if r is not None and not np.isnan(r)]
    if valid_rates:
        y_min = max(0, min(valid_rates) - 0.05)
        y_max = min(1.0, max(valid_rates) + 0.02)
        ax.set_ylim(y_min, y_max)

    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_match_rates_by_dti(
    rates_df: pl.DataFrame,
    title: str,
    mbs_label: str,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot match rates by DTI bins as grouped bar chart."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    dti_labels = {1: "<20%", 2: "20-30%", 3: "30-36%", 4: "36-49%", 5: "50-60%", 6: ">60%"}

    fig, ax = plt.subplots(figsize=(10, 6))

    df = rates_df.filter(pl.col("dti_bin").is_not_null()).sort("dti_bin")
    bins = df["dti_bin"].to_list()
    labels = [dti_labels.get(b, str(b)) for b in bins]

    mbs_rates = [r if r is not None else 0 for r in df["mbs_match_rate"].to_list()]
    fhfa_rates = [r if r is not None else 0 for r in df["fhfa_match_rate"].to_list()]

    x = np.arange(len(labels))
    width = 0.35

    ax.bar(x - width / 2, mbs_rates, width, label=mbs_label, color=MBS_COLOR, alpha=0.8)
    ax.bar(x + width / 2, fhfa_rates, width, label="FHFA", color=FHFA_COLOR, alpha=0.8)

    ax.set_xticks(x)
    ax.set_xticklabels(labels)

    mbs_rates_raw = df["mbs_match_rate"].to_list()
    fhfa_rates_raw = df["fhfa_match_rate"].to_list()
    for i, (mbs_r, fhfa_r) in enumerate(zip(mbs_rates_raw, fhfa_rates_raw)):
        if mbs_r is not None and not np.isnan(mbs_r):
            ax.text(
                i - width / 2, mbs_r + 0.01, f"{mbs_r:.1%}", ha="center", va="bottom", fontsize=8
            )
        if fhfa_r is not None and not np.isnan(fhfa_r):
            ax.text(
                i + width / 2, fhfa_r + 0.01, f"{fhfa_r:.1%}", ha="center", va="bottom", fontsize=8
            )

    ax.set_xlabel("DTI Bin")
    ax.set_ylabel("Match Rate")
    ax.set_title(title)

    all_rates = mbs_rates + fhfa_rates
    valid_rates = [r for r in all_rates if r is not None and not np.isnan(r)]
    if valid_rates:
        y_min = max(0, min(valid_rates) - 0.05)
        y_max = min(1.0, max(valid_rates) + 0.05)
        ax.set_ylim(y_min, y_max)

    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_match_rates_by_category(
    rates_df: pl.DataFrame,
    category_col: str,
    label_map: dict[int, str],
    title: str,
    mbs_label: str,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot match rates by categorical variable as grouped bar chart."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(10, 6))

    df = rates_df.filter(pl.col(category_col).is_not_null()).sort(category_col)
    categories = df[category_col].to_list()
    labels = [label_map.get(c, str(c)) for c in categories]

    mbs_rates_raw = df["mbs_match_rate"].to_list()
    fhfa_rates_raw = df["fhfa_match_rate"].to_list()
    mbs_rates = [r if r is not None else 0 for r in mbs_rates_raw]
    fhfa_rates = [r if r is not None else 0 for r in fhfa_rates_raw]

    x = np.arange(len(labels))
    width = 0.35

    ax.bar(x - width / 2, mbs_rates, width, label=mbs_label, color=MBS_COLOR, alpha=0.8)
    ax.bar(x + width / 2, fhfa_rates, width, label="FHFA", color=FHFA_COLOR, alpha=0.8)

    ax.set_xticks(x)
    ax.set_xticklabels(labels)

    for i, (mbs_r, fhfa_r) in enumerate(zip(mbs_rates_raw, fhfa_rates_raw)):
        if mbs_r is not None and not np.isnan(mbs_r):
            ax.text(
                i - width / 2, mbs_r + 0.01, f"{mbs_r:.1%}", ha="center", va="bottom", fontsize=9
            )
        if fhfa_r is not None and not np.isnan(fhfa_r):
            ax.text(
                i + width / 2, fhfa_r + 0.01, f"{fhfa_r:.1%}", ha="center", va="bottom", fontsize=9
            )

    ax.set_xlabel(category_col.replace("_", " ").title())
    ax.set_ylabel("Match Rate")
    ax.set_title(title)

    all_rates = mbs_rates + fhfa_rates
    valid_rates = [r for r in all_rates if r is not None and not np.isnan(r)]
    if valid_rates:
        y_min = max(0, min(valid_rates) - 0.05)
        y_max = min(1.0, max(valid_rates) + 0.05)
        ax.set_ylim(y_min, y_max)

    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


# =============================================================================
# Summary Statistics
# =============================================================================


def print_summary_statistics(
    crosswalk: pl.DataFrame,
    mbs: pl.DataFrame,
    fhfa: pl.DataFrame,
    mbs_id_col: str,
    mbs_label: str,
) -> None:
    """Print summary statistics for the matching."""
    print("\n" + "=" * 60)
    print(f"{mbs_label}-FHFA MATCHING SUMMARY STATISTICS")
    print("=" * 60)

    overall = compute_overall_match_rates(crosswalk, mbs, fhfa, mbs_id_col)

    print("\nOverall Match Statistics:")
    print(f"  Total matched pairs: {overall['total_matches']:,}")
    print(f"  {mbs_label} loans: {overall['mbs_total']:,}")
    print(f"  FHFA records: {overall['fhfa_total']:,}")
    print(f"  {mbs_label} match rate: {overall['mbs_match_rate']:.1%}")
    print(f"  FHFA match rate: {overall['fhfa_match_rate']:.1%}")

    print("\nMatch Rates by Loan Purpose:")
    purpose_labels = {1: "Purchase", 2: "Refinance", 7: "Cash-out Refi"}
    purpose_df = compute_match_rates_by_category(crosswalk, mbs, fhfa, mbs_id_col, "loan_purpose")
    for row in purpose_df.iter_rows(named=True):
        purpose = purpose_labels.get(row["loan_purpose"], str(row["loan_purpose"]))
        mbs_rate = row["mbs_match_rate"]
        fhfa_rate = row["fhfa_match_rate"]
        mbs_str = f"{mbs_rate:.1%}" if mbs_rate is not None else "N/A"
        fhfa_str = f"{fhfa_rate:.1%}" if fhfa_rate is not None else "N/A"
        print(f"  {purpose}: {mbs_label}={mbs_str}, FHFA={fhfa_str}")

    print("\nMatch Rates by Channel:")
    channel_labels = {1: "Retail", 2: "Broker", 3: "Correspondent", 9: "Correspondent"}
    channel_df = compute_match_rates_by_category(crosswalk, mbs, fhfa, mbs_id_col, "channel")
    for row in channel_df.iter_rows(named=True):
        channel = channel_labels.get(row["channel"], str(row["channel"]))
        mbs_rate = row["mbs_match_rate"]
        fhfa_rate = row["fhfa_match_rate"]
        mbs_str = f"{mbs_rate:.1%}" if mbs_rate is not None else "N/A"
        fhfa_str = f"{fhfa_rate:.1%}" if fhfa_rate is not None else "N/A"
        print(f"  {channel}: {mbs_label}={mbs_str}, FHFA={fhfa_str}")

    print("\n" + "=" * 60)


# =============================================================================
# Main Validation Runners
# =============================================================================


def run_fnma_validation(
    min_year: int = FNMA_START_YEAR,
    max_year: int = FNMA_END_YEAR,
    output_dir: Path | None = None,
    save_plots: bool = True,
    show_plots: bool = False,
) -> dict[str, Any]:
    """Run FNMA-FHFA validation and generate plots."""
    if output_dir is None:
        output_dir = FNMA_FIGURES_DIR

    if save_plots:
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"Saving FNMA figures to: {output_dir}")

    print(f"\nLoading FNMA validation data for {min_year}-{max_year}...")
    crosswalk, fnma, fhfa = load_fnma_validation_data(min_year, max_year)

    print_summary_statistics(crosswalk, fnma, fhfa, "fnma_loan_id", "FNMA")

    results = {"crosswalk": crosswalk, "fnma": fnma, "fhfa": fhfa}

    print("\n1. Computing temporal match rates...")
    temporal_df = compute_fnma_temporal_match_rates(crosswalk, fnma, fhfa)
    plot_fnma_temporal_match_rates(
        temporal_df,
        output_path=output_dir / "fnma_temporal_match_rates.png" if save_plots else None,
        interactive=show_plots,
    )

    print("2. Computing match rates by loan amount...")
    loan_amount_df = compute_match_rates_by_loan_amount(crosswalk, fnma, fhfa, "fnma_loan_id")
    plot_match_rates_by_continuous(
        loan_amount_df,
        "loan_amount_bin",
        "Loan Amount ($K)",
        "FNMA-FHFA Match Rates by Loan Amount",
        "FNMA",
        output_path=output_dir / "fnma_match_rates_by_loan_amount.png" if save_plots else None,
        interactive=show_plots,
        x_scale=0.001,
    )

    print("3. Computing match rates by LTV...")
    ltv_df = compute_match_rates_by_ltv(crosswalk, fnma, fhfa, "fnma_loan_id")
    plot_match_rates_by_continuous(
        ltv_df,
        "ltv_bin",
        "LTV (%)",
        "FNMA-FHFA Match Rates by LTV",
        "FNMA",
        output_path=output_dir / "fnma_match_rates_by_ltv.png" if save_plots else None,
        interactive=show_plots,
    )

    print("4. Computing match rates by interest rate...")
    rate_df = compute_match_rates_by_interest_rate(crosswalk, fnma, fhfa, "fnma_loan_id")
    plot_match_rates_by_continuous(
        rate_df,
        "interest_rate_bin",
        "Interest Rate (%)",
        "FNMA-FHFA Match Rates by Interest Rate",
        "FNMA",
        output_path=output_dir / "fnma_match_rates_by_interest_rate.png" if save_plots else None,
        interactive=show_plots,
    )

    print("5. Computing match rates by DTI...")
    dti_df = compute_match_rates_by_dti(crosswalk, fnma, fhfa, "fnma_loan_id")
    plot_match_rates_by_dti(
        dti_df,
        "FNMA-FHFA Match Rates by DTI (FHFA Bin Scheme)",
        "FNMA",
        output_path=output_dir / "fnma_match_rates_by_dti.png" if save_plots else None,
        interactive=show_plots,
    )

    print("6. Computing match rates by loan purpose...")
    purpose_df = compute_match_rates_by_category(
        crosswalk, fnma, fhfa, "fnma_loan_id", "loan_purpose"
    )
    plot_match_rates_by_category(
        purpose_df,
        "loan_purpose",
        {1: "Purchase", 2: "Refinance", 7: "Cash-out"},
        "FNMA-FHFA Match Rates by Loan Purpose",
        "FNMA",
        output_path=output_dir / "fnma_match_rates_by_loan_purpose.png" if save_plots else None,
        interactive=show_plots,
    )

    print("7. Computing match rates by channel...")
    channel_df = compute_match_rates_by_category(crosswalk, fnma, fhfa, "fnma_loan_id", "channel")
    plot_match_rates_by_category(
        channel_df,
        "channel",
        {1: "Retail", 2: "Broker", 9: "Correspondent"},
        "FNMA-FHFA Match Rates by Channel",
        "FNMA",
        output_path=output_dir / "fnma_match_rates_by_channel.png" if save_plots else None,
        interactive=show_plots,
    )

    print("8. Computing match rates by occupancy...")
    occupancy_df = compute_match_rates_by_category(
        crosswalk, fnma, fhfa, "fnma_loan_id", "occupancy_code"
    )
    plot_match_rates_by_category(
        occupancy_df,
        "occupancy_code",
        {1: "Primary", 2: "Second Home", 3: "Investment"},
        "FNMA-FHFA Match Rates by Occupancy Type",
        "FNMA",
        output_path=output_dir / "fnma_match_rates_by_occupancy.png" if save_plots else None,
        interactive=show_plots,
    )

    print("\nDone! Generated 8 FNMA validation plots.")
    if save_plots:
        print(f"Figures saved to: {output_dir}")

    return results


def run_fhlmc_validation(
    min_year: int = FHLMC_START_YEAR,
    max_year: int = FHLMC_END_YEAR,
    output_dir: Path | None = None,
    save_plots: bool = True,
    show_plots: bool = False,
) -> dict[str, Any]:
    """Run FHLMC-FHFA validation and generate plots."""
    if output_dir is None:
        output_dir = FHLMC_FIGURES_DIR

    if save_plots:
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"Saving FHLMC figures to: {output_dir}")

    print(f"\nLoading FHLMC validation data for {min_year}-{max_year}...")
    try:
        crosswalk, fhlmc, fhfa = load_fhlmc_validation_data(min_year, max_year)
    except FileNotFoundError as e:
        print(f"\nError: {e}")
        return {}

    print_summary_statistics(crosswalk, fhlmc, fhfa, "fhlmc_loan_id", "FHLMC")

    results = {"crosswalk": crosswalk, "fhlmc": fhlmc, "fhfa": fhfa}

    print("\n1. Computing temporal match rates...")
    temporal_df = compute_fhlmc_temporal_match_rates(crosswalk, fhlmc, fhfa)
    plot_fhlmc_temporal_match_rates(
        temporal_df,
        output_path=output_dir / "fhlmc_temporal_match_rates.png" if save_plots else None,
        interactive=show_plots,
    )

    print("2. Computing match rates by loan amount...")
    loan_amount_df = compute_match_rates_by_loan_amount(crosswalk, fhlmc, fhfa, "fhlmc_loan_id")
    plot_match_rates_by_continuous(
        loan_amount_df,
        "loan_amount_bin",
        "Loan Amount ($K)",
        "FHLMC-FHFA Match Rates by Loan Amount",
        "FHLMC",
        output_path=output_dir / "fhlmc_match_rates_by_loan_amount.png" if save_plots else None,
        interactive=show_plots,
        x_scale=0.001,
    )

    print("3. Computing match rates by LTV...")
    ltv_df = compute_match_rates_by_ltv(crosswalk, fhlmc, fhfa, "fhlmc_loan_id")
    plot_match_rates_by_continuous(
        ltv_df,
        "ltv_bin",
        "LTV (%)",
        "FHLMC-FHFA Match Rates by LTV",
        "FHLMC",
        output_path=output_dir / "fhlmc_match_rates_by_ltv.png" if save_plots else None,
        interactive=show_plots,
    )

    print("4. Computing match rates by interest rate...")
    rate_df = compute_match_rates_by_interest_rate(crosswalk, fhlmc, fhfa, "fhlmc_loan_id")
    plot_match_rates_by_continuous(
        rate_df,
        "interest_rate_bin",
        "Interest Rate (%)",
        "FHLMC-FHFA Match Rates by Interest Rate",
        "FHLMC",
        output_path=output_dir / "fhlmc_match_rates_by_interest_rate.png" if save_plots else None,
        interactive=show_plots,
    )

    print("5. Computing match rates by DTI...")
    dti_df = compute_match_rates_by_dti(crosswalk, fhlmc, fhfa, "fhlmc_loan_id")
    plot_match_rates_by_dti(
        dti_df,
        "FHLMC-FHFA Match Rates by DTI (FHFA Bin Scheme)",
        "FHLMC",
        output_path=output_dir / "fhlmc_match_rates_by_dti.png" if save_plots else None,
        interactive=show_plots,
    )

    print("6. Computing match rates by loan purpose...")
    purpose_df = compute_match_rates_by_category(
        crosswalk, fhlmc, fhfa, "fhlmc_loan_id", "loan_purpose"
    )
    plot_match_rates_by_category(
        purpose_df,
        "loan_purpose",
        {1: "Purchase", 2: "Refinance", 7: "Cash-out"},
        "FHLMC-FHFA Match Rates by Loan Purpose",
        "FHLMC",
        output_path=output_dir / "fhlmc_match_rates_by_loan_purpose.png" if save_plots else None,
        interactive=show_plots,
    )

    print("7. Computing match rates by channel...")
    channel_df = compute_match_rates_by_category(crosswalk, fhlmc, fhfa, "fhlmc_loan_id", "channel")
    plot_match_rates_by_category(
        channel_df,
        "channel",
        {1: "Retail", 2: "Broker", 3: "Correspondent"},
        "FHLMC-FHFA Match Rates by Channel",
        "FHLMC",
        output_path=output_dir / "fhlmc_match_rates_by_channel.png" if save_plots else None,
        interactive=show_plots,
    )

    print("8. Computing match rates by occupancy...")
    occupancy_df = compute_match_rates_by_category(
        crosswalk, fhlmc, fhfa, "fhlmc_loan_id", "occupancy_code"
    )
    plot_match_rates_by_category(
        occupancy_df,
        "occupancy_code",
        {1: "Primary", 2: "Second Home", 3: "Investment"},
        "FHLMC-FHFA Match Rates by Occupancy Type",
        "FHLMC",
        output_path=output_dir / "fhlmc_match_rates_by_occupancy.png" if save_plots else None,
        interactive=show_plots,
    )

    print("\nDone! Generated 8 FHLMC validation plots.")
    if save_plots:
        print(f"Figures saved to: {output_dir}")

    return results


def run_validation(
    enterprise: Literal["fnma", "fhlmc", "both"] = "both",
    save_plots: bool = True,
    show_plots: bool = False,
    fnma_min_year: int = FNMA_START_YEAR,
    fnma_max_year: int = FNMA_END_YEAR,
    fhlmc_min_year: int = FHLMC_START_YEAR,
    fhlmc_max_year: int = FHLMC_END_YEAR,
) -> dict[str, Any]:
    """Run MBS-FHFA validation for specified enterprise(s).

    Args:
        enterprise: Which enterprise to validate ("fnma", "fhlmc", or "both")
        save_plots: Whether to save plots to disk
        show_plots: Whether to display matplotlib plots interactively
        fnma_min_year: First year for FNMA validation
        fnma_max_year: Last year for FNMA validation
        fhlmc_min_year: First year for FHLMC validation
        fhlmc_max_year: Last year for FHLMC validation

    Returns:
        Dictionary with validation results
    """
    results = {}

    if enterprise in ("fnma", "both"):
        print("\n" + "=" * 70)
        print("FNMA-FHFA VALIDATION")
        print("=" * 70)
        results["fnma"] = run_fnma_validation(
            min_year=fnma_min_year,
            max_year=fnma_max_year,
            save_plots=save_plots,
            show_plots=show_plots,
        )

    if enterprise in ("fhlmc", "both"):
        print("\n" + "=" * 70)
        print("FHLMC-FHFA VALIDATION")
        print("=" * 70)
        results["fhlmc"] = run_fhlmc_validation(
            min_year=fhlmc_min_year,
            max_year=fhlmc_max_year,
            save_plots=save_plots,
            show_plots=show_plots,
        )

    return results
