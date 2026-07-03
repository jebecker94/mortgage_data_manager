"""HMDA-FHLB Match Validation Module.

This module provides two types of validation for the HMDA-FHLB matching workflow:

1. DATA VALIDATION (Pre-match):
   - Schema validation: Ensures required columns exist
   - Magnitude checks: Verifies units are correct (dollars vs thousands, etc.)

2. MATCH QUALITY VALIDATION (Post-match):
   - Match statistics summary
   - Temporal analysis (match counts by year)
   - Loan characteristics analysis (match rates by loan amount)
   - Purchaser type distribution
   - Lender (PFI) analysis

Key Context for HMDA-FHLB Matching:
- Match rates of 40-55% are EXPECTED and do NOT indicate methodology problems
- FHLBs only acquire a subset of loans from their member institutions
- Not all FHLB member originations are sold to FHLBs
- The key insight is identifying which lenders (PFIs) sell loans to FHLBs

Usage:
    # Data validation (before matching)
    from mortgage_data_manager.matching.match_hmda_fhlb.validation import (
        validate_hmda_post2018,
        validate_fhlb_post2018,
        validate_datasets,
    )

    # Match quality validation (after matching)
    from mortgage_data_manager.matching.match_hmda_fhlb.validation import (
        run_validation,
        load_post2018_data,
        compute_overall_stats,
    )
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.matching.match_hmda_fhlb.config import (
    DATA_DIR,
    PROJECT_ROOT,
)

logger = get_logger(__name__)

# =============================================================================
# Configuration
# =============================================================================

# Year range for analysis
MIN_YEAR_POST2018 = 2018
MAX_YEAR_POST2018 = 2024
MIN_YEAR_PRE2018 = 2009
MAX_YEAR_PRE2018 = 2017

# Output directory for figures
VALIDATION_OUTPUT_DIR = PROJECT_ROOT / "docs" / "matching" / "figures" / "hmda_fhlb"

# Color palette (Jonathan's authoritative academic palette)
PALETTE = {
    # Primary anchors
    "midnight": "#0E2F44",  # Headers, dominant elements
    "hunter": "#145A32",  # Large datasets, key series
    # Secondary highlights
    "gold": "#D4AC0D",  # Focus data, callouts
    "terracotta": "#BA4A00",  # Trend lines, emphasis
    # Tertiary extensions
    "mulberry": "#900C3F",  # High-cardinality categories
    "purple": "#5B2C6F",  # Additional categories
    "teal": "#117864",  # Additional categories
    # Neutrals
    "grey": "#85929E",  # Context, axes, benchmarks
    "slate": "#566573",  # Borders, secondary text
    # Background & text
    "canvas": "#F8F9F9",  # Background (off-white)
    "ink": "#1B2631",  # All standard text
}

# Categorical sequence (hunter green + gold as primary pair)
CATEGORICAL = [
    PALETTE["hunter"],
    PALETTE["gold"],
    PALETTE["midnight"],
    PALETTE["terracotta"],
    PALETTE["purple"],
    PALETTE["grey"],
    PALETTE["mulberry"],
]

# Dataset-specific aliases
HMDA_COLOR = PALETTE["hunter"]
FHLB_COLOR = PALETTE["gold"]

# Check for matplotlib availability
try:
    import matplotlib

    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    matplotlib = None  # type: ignore

# Check for scipy availability
try:
    from scipy.stats import spearmanr

    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


def _get_pyplot(interactive: bool = True):
    """Get pyplot with appropriate backend."""
    if not HAS_MATPLOTLIB:
        return None
    if not interactive:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


# =============================================================================
# DATA VALIDATION (Pre-match) - Schema and Magnitude Checks
# =============================================================================

# Expected Magnitude Ranges (Min Mean, Max Mean)
# Used to verify if units are correct (e.g. dollars vs thousands, percent vs decimal)
MAGNITUDE_CHECKS = {
    # Dollars (Raw) - Expect average around 100k-500k
    "amount": (10_000, 5_000_000),
    # Income (Raw Annual) - Expect average around 50k-300k
    # Note: FHLB "TotalMonthlyIncomeAmount" is treated as Annual in matching logic
    "income": (20_000, 1_000_000),
    # Rate (Percent) - Expect average around 2.0 - 10.0
    # If decimal (0.05), it will be < 1.0
    "rate": (1.0, 15.0),
    # Term (Months) - Expect average around 180-360
    "term": (60, 480),
    # LTV (Percent) - Expect average around 60-95
    "ltv": (10, 150),
    # DTI (Percent) - Expect average around 20-50
    "dti": (5, 80),
}


def check_magnitudes(df: pl.DataFrame, col_map: dict[str, str], dataset_name: str) -> bool:
    """Check if numeric columns fall within expected average magnitudes.

    Args:
        df: DataFrame to check
        col_map: Maps logical name (e.g. 'income') to dataframe column name
        dataset_name: Name for logging

    Returns:
        True if all checks pass
    """
    passed = True

    # Calculate means for numeric columns, ignoring nulls and common HMDA sentinels
    stats = df.select([
        pl.col(actual_col)
        .fill_nan(None)
        .replace(-99999, None)  # Treat HMDA sentinel as null
        .mean()
        .alias(f"mean_{logic_col}")
        for logic_col, actual_col in col_map.items()
        if actual_col in df.columns
    ]).to_dict(as_series=False)

    for logic_col, actual_col in col_map.items():
        if actual_col not in df.columns:
            continue

        key = f"mean_{logic_col}"
        if key not in stats or not stats[key]:
            continue

        mean_val = stats[key][0]
        if mean_val is None:
            continue

        expected_min, expected_max = MAGNITUDE_CHECKS.get(logic_col, (None, None))

        if expected_min is not None:
            if not (expected_min <= mean_val <= expected_max):
                logger.error(
                    f"[{dataset_name}] Magnitude Check FAILED for '{actual_col}' ({logic_col}). "
                    f"Mean: {mean_val:,.2f}. Expected range: {expected_min:,.0f} - {expected_max:,.0f}."
                )
                passed = False
            else:
                logger.info(f"[{dataset_name}] Magnitude OK: '{actual_col}' mean = {mean_val:,.2f}")

    return passed


def check_schema(df: pl.DataFrame, required_cols: list[str], dataset_name: str) -> bool:
    """Verify existence of required columns."""
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        logger.error(f"[{dataset_name}] Schema Check FAILED. Missing columns: {missing}")
        return False
    return True


def validate_hmda_post2018(df: pl.DataFrame) -> bool:
    """Validate HMDA Post-2018 Data schema and magnitudes."""
    logger.info("Validating HMDA Data (Post-2018)")

    # 1. Schema Check
    required_cols = [
        "activity_year",
        "census_tract",
        "loan_amount",
        "loan_type",
        "total_units",
        "occupancy_type",
        "income",
        "loan_term",
        "interest_rate",
        "combined_loan_to_value_ratio",
        "debt_to_income_ratio",
    ]
    if not check_schema(df, required_cols, "HMDA"):
        return False

    # 2. Magnitude Check
    col_map = {
        "amount": "loan_amount",
        "income": "income",
        "rate": "interest_rate",
        "term": "loan_term",
        "ltv": "combined_loan_to_value_ratio",
        "dti": "debt_to_income_ratio",
    }

    return check_magnitudes(df, col_map, "HMDA")


def validate_fhlb_post2018(df: pl.DataFrame) -> bool:
    """Validate FHLB Post-2018 Data schema and magnitudes."""
    logger.info("Validating FHLB Data (Post-2018)")

    # 1. Schema Check
    required_cols = [
        "NoteDate",
        "Census Tract String",
        "NoteAmount",
        "Loan Type",
        "PropertyUnitCount",
        "PropertyUsageType",
        "TotalMonthlyIncomeAmount",
        "LoanAmortizationMaxTermMonths",
        "NoteRatePercent",
        "LTVRatioPercent",
        "TotalDebtExpenseRatioPercent",
    ]
    if not check_schema(df, required_cols, "FHLB"):
        return False

    # 2. Magnitude Check
    col_map = {
        "amount": "NoteAmount",
        # Validating "TotalMonthly" against "Annual" range per README findings
        "income": "TotalMonthlyIncomeAmount",
        "rate": "NoteRatePercent",
        "term": "LoanAmortizationMaxTermMonths",
        "ltv": "LTVRatioPercent",
        "dti": "TotalDebtExpenseRatioPercent",
    }

    return check_magnitudes(df, col_map, "FHLB")


def validate_datasets(match_folder: Path) -> None:
    """Run validation on cleaned parquet files before matching."""
    hmda_path = match_folder / "hmda_fhlb_members_post2018.parquet"
    fhlb_path = match_folder / "fhlb_acquisitions_post2018.parquet"

    if not hmda_path.exists() or not fhlb_path.exists():
        logger.warning("Data validation skipped: One or more input files missing")
        return

    # Load samples to save memory (first 10,000 rows should be enough for magnitude)
    try:
        hmda = pl.scan_parquet(hmda_path).limit(10000).collect()
        if not validate_hmda_post2018(hmda):
            logger.warning("HMDA Validation FAILED. Proceeding with caution")
    except Exception as e:
        logger.error(f"Error validating HMDA data: {e}")

    try:
        fhlb = pl.scan_parquet(fhlb_path).limit(10000).collect()
        if not validate_fhlb_post2018(fhlb):
            logger.warning("FHLB Validation FAILED. Proceeding with caution")
    except Exception as e:
        logger.error(f"Error validating FHLB data: {e}")


# =============================================================================
# MATCH QUALITY VALIDATION (Post-match) - Data Loading
# =============================================================================


def load_post2018_data() -> tuple[pl.DataFrame, pl.DataFrame | None, pl.DataFrame | None]:
    """Load post-2018 matching data.

    Returns:
        Tuple of (matches, hmda_members, fhlb_acquisitions) DataFrames
    """
    matches_path = DATA_DIR / "fhlb_hmda_matches_post2018.parquet"
    hmda_path = DATA_DIR / "hmda_fhlb_members_post2018.parquet"
    fhlb_path = DATA_DIR / "fhlb_acquisitions_post2018.parquet"

    if not matches_path.exists():
        raise FileNotFoundError(f"Matches file not found: {matches_path}")

    matches = pl.read_parquet(matches_path)
    hmda = pl.read_parquet(hmda_path) if hmda_path.exists() else None
    fhlb = pl.read_parquet(fhlb_path) if fhlb_path.exists() else None

    return matches, hmda, fhlb


def load_pre2018_data() -> tuple[pl.DataFrame, pl.DataFrame | None, pl.DataFrame | None]:
    """Load pre-2018 matching data.

    Returns:
        Tuple of (matches, hmda_members, fhlb_acquisitions) DataFrames
    """
    matches_path = DATA_DIR / "fhlb_hmda_matches_pre2018.parquet"
    hmda_path = DATA_DIR / "hmda_fhlb_members_pre2018.parquet"
    fhlb_path = DATA_DIR / "fhlb_acquisitions_pre2018.parquet"

    if not matches_path.exists():
        raise FileNotFoundError(f"Matches file not found: {matches_path}")

    matches = pl.read_parquet(matches_path)
    hmda = pl.read_parquet(hmda_path) if hmda_path.exists() else None
    fhlb = pl.read_parquet(fhlb_path) if fhlb_path.exists() else None

    return matches, hmda, fhlb


def load_lender_statistics() -> pl.DataFrame | None:
    """Load lender match statistics."""
    stats_path = DATA_DIR / "lender_match_statistics.csv"
    if stats_path.exists():
        return pl.read_csv(stats_path)
    return None


# =============================================================================
# MATCH QUALITY VALIDATION (Post-match) - Statistics
# =============================================================================


def compute_overall_stats(
    matches: pl.DataFrame,
    hmda: pl.DataFrame | None,
    fhlb: pl.DataFrame | None,
    hmda_id_col: str = "HMDAIndex",
    fhlb_id_col: str = "LoanCharacteristicsID",
) -> dict[str, Any]:
    """Compute overall match statistics."""
    n_matches = len(matches)

    # HMDA-side stats
    if hmda is not None and hmda_id_col in matches.columns and hmda_id_col in hmda.columns:
        matched_hmda_ids = matches.select(hmda_id_col).unique()
        n_hmda = len(hmda)
        n_hmda_matched = len(hmda.join(matched_hmda_ids, on=hmda_id_col, how="inner"))
        hmda_match_rate = n_hmda_matched / n_hmda if n_hmda > 0 else 0
    else:
        n_hmda = None
        n_hmda_matched = None
        hmda_match_rate = None

    # FHLB-side stats
    if fhlb is not None and fhlb_id_col in matches.columns and fhlb_id_col in fhlb.columns:
        matched_fhlb_ids = matches.select(fhlb_id_col).unique()
        n_fhlb = len(fhlb)
        n_fhlb_matched = len(fhlb.join(matched_fhlb_ids, on=fhlb_id_col, how="inner"))
        fhlb_match_rate = n_fhlb_matched / n_fhlb if n_fhlb > 0 else 0
    else:
        n_fhlb = None
        n_fhlb_matched = None
        fhlb_match_rate = None

    return {
        "n_matches": n_matches,
        "n_hmda": n_hmda,
        "n_hmda_matched": n_hmda_matched,
        "hmda_match_rate": hmda_match_rate,
        "n_fhlb": n_fhlb,
        "n_fhlb_matched": n_fhlb_matched,
        "fhlb_match_rate": fhlb_match_rate,
    }


def compute_yearly_match_counts(
    matches: pl.DataFrame,
    year_col: str = "activity_year",
) -> pl.DataFrame:
    """Compute match counts by year."""
    if year_col not in matches.columns:
        # Try to find an alternative year column
        for col in ["year", "origination_year", "Year"]:
            if col in matches.columns:
                year_col = col
                break
        else:
            return pl.DataFrame({"year": [], "matches": []})

    return (
        matches.group_by(year_col)
        .agg(pl.len().alias("matches"))
        .sort(year_col)
        .rename({year_col: "year"})
    )


def compute_match_rates_by_loan_amount(
    matches: pl.DataFrame,
    hmda: pl.DataFrame,
    hmda_id_col: str = "HMDAIndex",
    bin_size: int = 25000,
    max_amount: int = 750000,
) -> pl.DataFrame:
    """Compute match rates by loan amount bins."""
    if "loan_amount" not in hmda.columns:
        return pl.DataFrame()

    matched_ids = matches.select(hmda_id_col).unique()

    hmda_with_bin = hmda.filter(
        (pl.col("loan_amount") >= 0) & (pl.col("loan_amount") <= max_amount)
    ).with_columns(
        ((pl.col("loan_amount") / bin_size).floor() * bin_size + bin_size / 2)
        .cast(pl.Int64)
        .alias("loan_amount_bin")
    )

    totals = hmda_with_bin.group_by("loan_amount_bin").agg(pl.len().alias("total"))
    matched = (
        hmda_with_bin.join(matched_ids, on=hmda_id_col, how="inner")
        .group_by("loan_amount_bin")
        .agg(pl.len().alias("matched"))
    )

    result = (
        totals.join(matched, on="loan_amount_bin", how="left")
        .with_columns(
            pl.col("matched").fill_null(0),
            (pl.col("matched") / pl.col("total")).alias("match_rate"),
        )
        .sort("loan_amount_bin")
    )

    return result


def compute_match_rates_by_loan_amount_fhlb(
    matches: pl.DataFrame,
    fhlb: pl.DataFrame,
    bin_size: int = 25000,
    max_amount: int = 750000,
) -> pl.DataFrame:
    """Compute match rates by loan amount bins from FHLB perspective.

    Args:
        matches: Matched records (contains both HMDA and FHLB columns)
        fhlb: FHLB acquisitions data
        bin_size: Bin size in dollars (default 25000)
        max_amount: Maximum loan amount to include (default 750000)

    Returns:
        Match rates by loan amount bin with columns:
        - loan_amount_bin: bin midpoint
        - total: total FHLB loans in bin
        - matched: matched FHLB loans in bin
        - match_rate: matched / total
    """
    # Determine loan amount column and unique key based on period
    if "Note Date" in matches.columns:
        # Pre-2018: amounts in thousands, LoanCharacteristicsID unique within year
        loan_amt_col = "Round Loan Amount"
        year_col_fhlb = "data_year"
        year_col_matches = "activity_year"

        fhlb_with_amount = fhlb.with_columns(
            (pl.col(loan_amt_col) * 1000).cast(pl.Int64).alias("loan_amount_dollars")
        )

        # Add bins to FHLB data
        fhlb_with_bin = fhlb_with_amount.filter(
            (pl.col("loan_amount_dollars") >= 0) & (pl.col("loan_amount_dollars") <= max_amount)
        ).with_columns(
            ((pl.col("loan_amount_dollars") / bin_size).floor() * bin_size + bin_size / 2)
            .cast(pl.Int64)
            .alias("loan_amount_bin")
        )

        # Pre-2018: Use (year, LoanCharacteristicsID) composite key
        matched_fhlb = matches.select([year_col_matches, "LoanCharacteristicsID"]).unique()

        matched_in_bins = (
            fhlb_with_bin.join(
                matched_fhlb,
                left_on=[year_col_fhlb, "LoanCharacteristicsID"],
                right_on=[year_col_matches, "LoanCharacteristicsID"],
                how="inner"
            )
            .group_by("loan_amount_bin")
            .agg(pl.len().alias("matched"))
        )

        totals = fhlb_with_bin.group_by("loan_amount_bin").agg(pl.len().alias("total"))

    else:
        # Post-2018: amounts already in dollars, LoanCharacteristicsID globally unique
        loan_amt_col = "LoanAcquisitionActualUPBAmt"
        fhlb_with_amount = fhlb.with_columns(
            pl.col(loan_amt_col).cast(pl.Int64).alias("loan_amount_dollars")
        )

        # Add bins to FHLB data
        fhlb_with_bin = fhlb_with_amount.filter(
            (pl.col("loan_amount_dollars") >= 0) & (pl.col("loan_amount_dollars") <= max_amount)
        ).with_columns(
            ((pl.col("loan_amount_dollars") / bin_size).floor() * bin_size + bin_size / 2)
            .cast(pl.Int64)
            .alias("loan_amount_bin")
        )

        # Post-2018: Use LoanCharacteristicsID alone (globally unique)
        matched_fhlb = matches.select("LoanCharacteristicsID").unique()

        matched_in_bins = (
            fhlb_with_bin.join(
                matched_fhlb,
                on="LoanCharacteristicsID",
                how="inner"
            )
            .group_by("loan_amount_bin")
            .agg(pl.len().alias("matched"))
        )

        totals = fhlb_with_bin.group_by("loan_amount_bin").agg(pl.len().alias("total"))

    # Combine
    result = (
        totals.join(matched_in_bins, on="loan_amount_bin", how="left")
        .with_columns(
            pl.col("matched").fill_null(0),
            (pl.col("matched") / pl.col("total")).alias("match_rate"),
        )
        .sort("loan_amount_bin")
    )

    return result


def compute_purchaser_type_distribution(matches: pl.DataFrame) -> pl.DataFrame:
    """Compute distribution of purchaser types in matched records."""
    purchaser_col = None
    for col in ["purchaser_type", "purchaser_type_s", "purchaser_type_h"]:
        if col in matches.columns:
            purchaser_col = col
            break

    if purchaser_col is None:
        return pl.DataFrame()

    purchaser_labels = {
        0: "Not Applicable",
        1: "Fannie Mae",
        2: "Ginnie Mae",
        3: "Freddie Mac",
        4: "Farmer Mac or Other",
        5: "Private Securitizer",
        6: "Commercial Bank/Savings",
        7: "Credit Union/Mortgage Co",
        8: "Life Insurance Co",
        9: "Other",
    }

    result = (
        matches.group_by(purchaser_col)
        .agg(pl.len().alias("count"))
        .with_columns(
            (pl.col("count") / pl.col("count").sum()).alias("pct"),
            pl.col(purchaser_col)
            .replace_strict(purchaser_labels, default="Unknown")
            .alias("purchaser_label"),
        )
        .sort("count", descending=True)
    )

    return result.rename({purchaser_col: "purchaser_type"})


def analyze_lender_statistics(stats_df: pl.DataFrame) -> dict[str, Any]:
    """Analyze lender match statistics."""
    if stats_df is None or len(stats_df) == 0:
        return {}

    # Find the relevant columns
    lei_col = None
    for col in ["lei", "LEI", "lender_id"]:
        if col in stats_df.columns:
            lei_col = col
            break

    match_col = None
    for col in ["matched_loans", "total_matches", "matches"]:
        if col in stats_df.columns:
            match_col = col
            break

    year_col = None
    for col in ["activity_year", "year", "Year"]:
        if col in stats_df.columns:
            year_col = col
            break

    if lei_col is None or match_col is None:
        return {"error": "Required columns not found in lender statistics"}

    # Active lenders with matches
    active_lenders = stats_df.filter(pl.col(match_col) > 0)
    n_active = active_lenders[lei_col].n_unique()

    # Top lenders by total matches
    top_lenders = (
        stats_df.group_by(lei_col)
        .agg(pl.col(match_col).sum().alias("total_matches"))
        .sort("total_matches", descending=True)
        .head(20)
    )

    result = {
        "n_active_lenders": n_active,
        "top_lenders": top_lenders,
    }

    # Yearly summary if year column exists
    if year_col is not None:
        yearly_summary = (
            active_lenders.group_by(year_col)
            .agg([
                pl.col(lei_col).n_unique().alias("active_lenders"),
                pl.col(match_col).sum().alias("total_matches"),
            ])
            .sort(year_col)
        )
        result["yearly_summary"] = yearly_summary

    return result


# =============================================================================
# MATCH QUALITY VALIDATION (Post-match) - Plotting
# =============================================================================


def plot_yearly_match_counts(
    yearly_df: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
    title_suffix: str = "",
) -> None:
    """Plot match counts by year."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None or len(yearly_df) == 0:
        print("matplotlib not available or no data, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(12, 6))

    years = yearly_df["year"].to_list()
    counts = yearly_df["matches"].to_list()

    ax.bar(years, counts, color=HMDA_COLOR, alpha=0.8)

    for year, count in zip(years, counts):
        ax.text(year, count, f"{count:,}", ha="center", va="bottom", fontsize=9)

    ax.set_xlabel("Year")
    ax.set_ylabel("Number of Matches")
    ax.set_title(f"HMDA-FHLB Match Counts by Year{title_suffix}")
    ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_yearly_match_counts_combined(
    yearly_pre: pl.DataFrame,
    yearly_post: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot match counts by year for both pre-2018 and post-2018 combined."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None or (len(yearly_pre) == 0 and len(yearly_post) == 0):
        print("matplotlib not available or no data, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(14, 6))

    # Combine data and mark periods
    years_pre = yearly_pre["year"].to_list()
    counts_pre = yearly_pre["matches"].to_list()
    years_post = yearly_post["year"].to_list()
    counts_post = yearly_post["matches"].to_list()

    # Plot pre-2018 in one color, post-2018 in another
    ax.bar(years_pre, counts_pre, color=PALETTE["midnight"], alpha=0.8, label="Pre-2018 (2009-2017)")
    ax.bar(years_post, counts_post, color=PALETTE["gold"], alpha=0.8, label="Post-2018 (2018-2024)")

    # Add value labels
    for year, count in zip(years_pre, counts_pre):
        ax.text(year, count, f"{count:,}", ha="center", va="bottom", fontsize=8)
    for year, count in zip(years_post, counts_post):
        ax.text(year, count, f"{count:,}", ha="center", va="bottom", fontsize=8)

    # Add vertical line to mark 2018 transition
    if years_pre and years_post:
        ax.axvline(x=2017.5, color="gray", linestyle="--", alpha=0.5, linewidth=1.5)
        ax.text(2017.5, ax.get_ylim()[1] * 0.95, "2018 HMDA\nRedesign",
                ha="center", va="top", fontsize=9, style="italic", color="gray")

    ax.set_xlabel("Year")
    ax.set_ylabel("Number of Matches")
    ax.set_title("HMDA-FHLB Match Counts by Year (2009-2024)")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_match_rates_by_loan_amount(
    loan_amount_df: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
    title_suffix: str = "",
) -> None:
    """Plot match rates by loan amount with density overlay."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None or len(loan_amount_df) == 0:
        print("matplotlib not available or no data, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(12, 6))

    x = loan_amount_df["loan_amount_bin"].to_numpy() / 1000
    totals = loan_amount_df["total"].to_numpy().astype(float)
    rates = loan_amount_df["match_rate"].to_list()

    pdf = totals / totals.sum()
    ax2 = ax.twinx()
    ax2.fill_between(x, pdf, alpha=0.15, color=HMDA_COLOR, label="Loan Distribution")
    ax2.set_ylabel("Density (PDF)", color=PALETTE["grey"])
    ax2.tick_params(axis="y", labelcolor=PALETTE["grey"])
    ax2.set_ylim(0, max(pdf) * 1.3)

    ax.plot(
        x,
        rates,
        color=HMDA_COLOR,
        linewidth=2,
        marker="o",
        markersize=5,
        label="Match Rate",
        zorder=10,
    )

    ax.set_xlabel("Loan Amount ($K)")
    ax.set_ylabel("Match Rate")
    ax.set_title(f"HMDA-FHLB Match Rates by Loan Amount{title_suffix}")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)

    valid_rates = [r for r in rates if r is not None and not np.isnan(r)]
    if valid_rates:
        y_min = max(0, min(valid_rates) - 0.05)
        y_max = min(1.0, max(valid_rates) + 0.05)
        ax.set_ylim(y_min, y_max)

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_match_rates_by_loan_amount_fhlb(
    loan_amount_df: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
    title_suffix: str = "",
) -> None:
    """Plot FHLB match rates by loan amount with FHLB density overlay.

    This shows the matching from FHLB perspective: what % of FHLB acquisitions
    can be matched to HMDA records.
    """
    plt = _get_pyplot(interactive=interactive)
    if plt is None or len(loan_amount_df) == 0:
        print("matplotlib not available or no data, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(12, 6))

    x = loan_amount_df["loan_amount_bin"].to_numpy() / 1000
    totals = loan_amount_df["total"].to_numpy().astype(float)
    rates = loan_amount_df["match_rate"].to_list()

    # Use FHLB loan distribution for density overlay
    pdf = totals / totals.sum()
    ax2 = ax.twinx()
    ax2.fill_between(x, pdf, alpha=0.15, color=FHLB_COLOR, label="FHLB Distribution")
    ax2.set_ylabel("Density (PDF)", color=PALETTE["grey"])
    ax2.tick_params(axis="y", labelcolor=PALETTE["grey"])
    ax2.set_ylim(0, max(pdf) * 1.3)

    # Use FHLB color for match rate line
    ax.plot(
        x,
        rates,
        color=FHLB_COLOR,
        linewidth=2,
        marker="o",
        markersize=5,
        label="Match Rate",
        zorder=10,
    )

    ax.set_xlabel("Loan Amount ($K)")
    ax.set_ylabel("Match Rate (FHLB Acquisitions → HMDA)")
    ax.set_title(f"FHLB-to-HMDA Match Rates by Loan Amount{title_suffix}")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)

    valid_rates = [r for r in rates if r is not None and not np.isnan(r)]
    if valid_rates:
        y_min = max(0, min(valid_rates) - 0.05)
        y_max = min(1.0, max(valid_rates) + 0.05)
        ax.set_ylim(y_min, y_max)

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_match_rates_by_loan_amount_combined(
    loan_amount_pre: pl.DataFrame,
    loan_amount_post: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
    fhlb_perspective: bool = False,
) -> None:
    """Plot match rates by loan amount for both pre-2018 and post-2018 combined.

    Args:
        loan_amount_pre: Pre-2018 match rates by loan amount
        loan_amount_post: Post-2018 match rates by loan amount
        output_path: Path to save figure
        interactive: Whether to show plot interactively
        fhlb_perspective: If True, uses FHLB colors and perspective labels
    """
    plt = _get_pyplot(interactive=interactive)
    if plt is None or (len(loan_amount_pre) == 0 and len(loan_amount_post) == 0):
        print("matplotlib not available or no data, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(14, 6))

    # Plot pre-2018
    if len(loan_amount_pre) > 0:
        x_pre = loan_amount_pre["loan_amount_bin"].to_numpy() / 1000
        rates_pre = loan_amount_pre["match_rate"].to_list()
        ax.plot(
            x_pre,
            rates_pre,
            color=PALETTE["midnight"],
            linewidth=2.5,
            marker="o",
            markersize=6,
            label="Pre-2018 (2009-2017)",
            zorder=10,
        )

    # Plot post-2018
    if len(loan_amount_post) > 0:
        x_post = loan_amount_post["loan_amount_bin"].to_numpy() / 1000
        rates_post = loan_amount_post["match_rate"].to_list()
        ax.plot(
            x_post,
            rates_post,
            color=PALETTE["gold"],
            linewidth=2.5,
            marker="s",
            markersize=6,
            label="Post-2018 (2018-2024)",
            zorder=10,
        )

    ax.set_xlabel("Loan Amount ($K)")

    if fhlb_perspective:
        ax.set_ylabel("Match Rate (FHLB Acquisitions → HMDA)")
        ax.set_title("FHLB-to-HMDA Match Rates by Loan Amount (2009-2024)")
    else:
        ax.set_ylabel("Match Rate (HMDA → FHLB)")
        ax.set_title("HMDA-to-FHLB Match Rates by Loan Amount (2009-2024)")

    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)
    ax.set_ylim(0, 1.0)

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_purchaser_type_distribution(
    purchaser_df: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
    title_suffix: str = "",
) -> None:
    """Plot distribution of purchaser types in matched records."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None or len(purchaser_df) == 0:
        print("matplotlib not available or no data, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(12, 6))

    labels = purchaser_df["purchaser_label"].to_list()
    pcts = purchaser_df["pct"].to_list()
    counts = purchaser_df["count"].to_list()

    x = np.arange(len(labels))
    ax.bar(x, pcts, color=HMDA_COLOR, alpha=0.8)

    for i, (pct, count) in enumerate(zip(pcts, counts)):
        ax.text(i, pct + 0.01, f"{pct:.1%}\n({count:,})", ha="center", va="bottom", fontsize=8)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_ylabel("Percentage of Matches")
    ax.set_title(
        f"Purchaser Type Distribution in HMDA-FHLB Matches{title_suffix}\n"
        "Note: FHLBs have no dedicated purchaser code; most show as 'Other' (9)"
    )
    ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_purchaser_type_distribution_combined(
    purchaser_pre: pl.DataFrame,
    purchaser_post: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot purchaser type distribution for both pre-2018 and post-2018 side-by-side."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None or (len(purchaser_pre) == 0 and len(purchaser_post) == 0):
        print("matplotlib not available or no data, skipping plots")
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))

    # Pre-2018 (left panel)
    if len(purchaser_pre) > 0:
        purchaser_pre = purchaser_pre.sort("pct", descending=True).head(10)
        labels_pre = purchaser_pre["purchaser_label"].to_list()
        pcts_pre = purchaser_pre["pct"].to_list()
        counts_pre = purchaser_pre["count"].to_list()

        x_pre = np.arange(len(labels_pre))
        ax1.bar(x_pre, pcts_pre, color=PALETTE["midnight"], alpha=0.8)

        for i, (pct, count) in enumerate(zip(pcts_pre, counts_pre)):
            ax1.text(i, pct + 0.01, f"{pct:.1%}\n({count:,})", ha="center", va="bottom", fontsize=7)

        ax1.set_xticks(x_pre)
        ax1.set_xticklabels(labels_pre, rotation=45, ha="right", fontsize=9)
        ax1.set_ylabel("Percentage of Matches")
        ax1.set_title("Pre-2018 (2009-2017)")
        ax1.grid(True, alpha=0.3, axis="y")

    # Post-2018 (right panel)
    if len(purchaser_post) > 0:
        purchaser_post = purchaser_post.sort("pct", descending=True).head(10)
        labels_post = purchaser_post["purchaser_label"].to_list()
        pcts_post = purchaser_post["pct"].to_list()
        counts_post = purchaser_post["count"].to_list()

        x_post = np.arange(len(labels_post))
        ax2.bar(x_post, pcts_post, color=PALETTE["gold"], alpha=0.8)

        for i, (pct, count) in enumerate(zip(pcts_post, counts_post)):
            ax2.text(i, pct + 0.01, f"{pct:.1%}\n({count:,})", ha="center", va="bottom", fontsize=7)

        ax2.set_xticks(x_post)
        ax2.set_xticklabels(labels_post, rotation=45, ha="right", fontsize=9)
        ax2.set_ylabel("Percentage of Matches")
        ax2.set_title("Post-2018 (2018-2024)")
        ax2.grid(True, alpha=0.3, axis="y")

    # Match y-axis limits
    if len(purchaser_pre) > 0 and len(purchaser_post) > 0:
        max_y = max(max(pcts_pre), max(pcts_post)) * 1.15
        ax1.set_ylim(0, max_y)
        ax2.set_ylim(0, max_y)

    fig.suptitle(
        "Purchaser Type Distribution in HMDA-FHLB Matches (2009-2024)\n"
        "Note: FHLBs have no dedicated purchaser code; most show as 'Other' (9)",
        fontsize=12,
        y=1.0
    )

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_lender_distribution(
    lender_stats: dict[str, Any],
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot distribution of matches across lenders."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None or "top_lenders" not in lender_stats:
        print("matplotlib not available or no data, skipping plots")
        return

    top_lenders = lender_stats["top_lenders"]
    if len(top_lenders) == 0:
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    # Histogram of match counts
    match_counts = top_lenders["total_matches"].to_numpy()
    ax1.hist(match_counts, bins=20, color=HMDA_COLOR, alpha=0.8, edgecolor="white")
    ax1.set_xlabel("Total Matches per Lender")
    ax1.set_ylabel("Number of Lenders")
    ax1.set_title("Distribution of Matches Across Top 20 Lenders")
    ax1.grid(True, alpha=0.3)

    # Cumulative share
    sorted_counts = np.sort(match_counts)[::-1]
    cumsum = np.cumsum(sorted_counts) / sorted_counts.sum()
    x = np.arange(1, len(cumsum) + 1)

    ax2.plot(x, cumsum, color=HMDA_COLOR, linewidth=2, marker="o")
    ax2.axhline(0.5, color=PALETTE["grey"], linestyle="--", alpha=0.7, label="50% of matches")
    ax2.axhline(0.9, color=PALETTE["grey"], linestyle=":", alpha=0.7, label="90% of matches")

    ax2.set_xlabel("Number of Top Lenders")
    ax2.set_ylabel("Cumulative Share of Matches")
    ax2.set_title("Concentration of Matches Among Top Lenders")
    ax2.legend()
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


# =============================================================================
# Main Entry Point
# =============================================================================


def print_section(title: str) -> None:
    """Print a section header."""
    print(f"\n{'=' * 70}")
    print(f" {title}")
    print(f"{'=' * 70}\n")


def run_validation(
    output_dir: Path | None = None,
    save_plots: bool = True,
    show_plots: bool = False,
) -> dict[str, Any]:
    """Run all match quality validation analyses.

    Args:
        output_dir: Directory to save figures. Defaults to VALIDATION_OUTPUT_DIR.
        save_plots: Whether to save plots to disk
        show_plots: Whether to display matplotlib plots interactively

    Returns:
        Dictionary with all computed statistics and DataFrames
    """
    if output_dir is None:
        output_dir = VALIDATION_OUTPUT_DIR

    if save_plots:
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"Saving figures to: {output_dir}")

    results = {}

    # POST-2018 Analysis
    print_section("1. Post-2018 Match Statistics")

    try:
        matches_post, hmda_post, fhlb_post = load_post2018_data()
        print(f"Loaded {len(matches_post):,} post-2018 matches")

        stats = compute_overall_stats(matches_post, hmda_post, fhlb_post)
        results["post2018_stats"] = stats

        print("\nOverall Statistics (Post-2018):")
        print(f"  Total matched pairs: {stats['n_matches']:,}")
        if stats["hmda_match_rate"] is not None:
            print(
                f"  HMDA member match rate: {stats['hmda_match_rate']:.1%} "
                f"({stats['n_hmda_matched']:,} / {stats['n_hmda']:,})"
            )
        if stats["fhlb_match_rate"] is not None:
            print(
                f"  FHLB acquisition match rate: {stats['fhlb_match_rate']:.1%} "
                f"({stats['n_fhlb_matched']:,} / {stats['n_fhlb']:,})"
            )

        print_section("2. Post-2018 Match Counts by Year")
        yearly_post = compute_yearly_match_counts(matches_post)
        results["yearly_post2018"] = yearly_post
        print(yearly_post)

        if (save_plots or show_plots) and len(yearly_post) > 0:
            plot_yearly_match_counts(
                yearly_post,
                output_path=output_dir / "yearly_match_counts_post2018.png" if save_plots else None,
                interactive=show_plots,
                title_suffix=" (Post-2018)",
            )

        if hmda_post is not None:
            print_section("3. Match Rates by Loan Amount (Post-2018, HMDA Perspective)")
            loan_amount_rates = compute_match_rates_by_loan_amount(matches_post, hmda_post)
            results["loan_amount_rates"] = loan_amount_rates

            if len(loan_amount_rates) > 0 and (save_plots or show_plots):
                plot_match_rates_by_loan_amount(
                    loan_amount_rates,
                    output_path=output_dir / "match_rates_by_loan_amount.png" if save_plots else None,
                    interactive=show_plots,
                )

        if fhlb_post is not None:
            print_section("3b. Match Rates by Loan Amount (Post-2018, FHLB Perspective)")
            loan_amount_rates_fhlb = compute_match_rates_by_loan_amount_fhlb(matches_post, fhlb_post)
            results["loan_amount_rates_fhlb"] = loan_amount_rates_fhlb

            if len(loan_amount_rates_fhlb) > 0 and (save_plots or show_plots):
                plot_match_rates_by_loan_amount_fhlb(
                    loan_amount_rates_fhlb,
                    output_path=output_dir / "match_rates_by_loan_amount_fhlb.png" if save_plots else None,
                    interactive=show_plots,
                    title_suffix=" (Post-2018: 2018-2024)",
                )

        print_section("4. Purchaser Type Distribution (Post-2018)")
        purchaser_dist = compute_purchaser_type_distribution(matches_post)
        results["purchaser_distribution"] = purchaser_dist

        if len(purchaser_dist) > 0:
            print(purchaser_dist)
            print("\nNote: FHLBs have no dedicated HMDA purchaser code.")
            print("Most matched loans show purchaser_type=9 ('Other'), which is expected.")

            if save_plots or show_plots:
                plot_purchaser_type_distribution(
                    purchaser_dist,
                    output_path=output_dir / "purchaser_type_distribution.png" if save_plots else None,
                    interactive=show_plots,
                )

    except FileNotFoundError as e:
        print(f"Post-2018 data not found: {e}")

    # PRE-2018 Analysis
    print_section("5. Pre-2018 Match Statistics")

    try:
        matches_pre, hmda_pre, fhlb_pre = load_pre2018_data()
        print(f"Loaded {len(matches_pre):,} pre-2018 matches")

        # 5a. Overall statistics
        stats_pre = compute_overall_stats(matches_pre, hmda_pre, fhlb_pre)
        results["pre2018_stats"] = stats_pre

        print("\nOverall Statistics (Pre-2018, 2009-2017):")
        print(f"  Total matched pairs: {stats_pre['n_matches']:,}")
        if stats_pre["hmda_match_rate"] is not None:
            print(
                f"  HMDA member match rate: {stats_pre['hmda_match_rate']:.1%} "
                f"({stats_pre['n_hmda_matched']:,} / {stats_pre['n_hmda']:,})"
            )
        if stats_pre["fhlb_match_rate"] is not None:
            print(
                f"  FHLB acquisition match rate: {stats_pre['fhlb_match_rate']:.1%} "
                f"({stats_pre['n_fhlb_matched']:,} / {stats_pre['n_fhlb']:,})"
            )

        # 5b. Yearly match counts
        print_section("6. Pre-2018 Match Counts by Year")
        yearly_pre = compute_yearly_match_counts(matches_pre)
        results["yearly_pre2018"] = yearly_pre
        print(yearly_pre)

        print("\nNote: Pre-2018 match counts increase over time (2009-2017)")
        print("This reflects improving data quality in both HMDA and FHLB sources.")

        if (save_plots or show_plots) and len(yearly_pre) > 0:
            plot_yearly_match_counts(
                yearly_pre,
                output_path=output_dir / "yearly_match_counts_pre2018.png" if save_plots else None,
                interactive=show_plots,
                title_suffix=" (Pre-2018: 2009-2017)",
            )

        # 5c. Match rates by loan amount (HMDA perspective)
        if hmda_pre is not None:
            print_section("7. Match Rates by Loan Amount (Pre-2018, HMDA Perspective)")
            loan_amount_rates_pre = compute_match_rates_by_loan_amount(matches_pre, hmda_pre)
            results["loan_amount_rates_pre2018"] = loan_amount_rates_pre

            if len(loan_amount_rates_pre) > 0 and (save_plots or show_plots):
                plot_match_rates_by_loan_amount(
                    loan_amount_rates_pre,
                    output_path=output_dir / "match_rates_by_loan_amount_pre2018.png" if save_plots else None,
                    interactive=show_plots,
                    title_suffix=" (Pre-2018: 2009-2017)",
                )

        # 5c2. Match rates by loan amount (FHLB perspective)
        if fhlb_pre is not None:
            print_section("7b. Match Rates by Loan Amount (Pre-2018, FHLB Perspective)")
            loan_amount_rates_pre_fhlb = compute_match_rates_by_loan_amount_fhlb(matches_pre, fhlb_pre)
            results["loan_amount_rates_pre2018_fhlb"] = loan_amount_rates_pre_fhlb

            if len(loan_amount_rates_pre_fhlb) > 0 and (save_plots or show_plots):
                plot_match_rates_by_loan_amount_fhlb(
                    loan_amount_rates_pre_fhlb,
                    output_path=output_dir / "match_rates_by_loan_amount_pre2018_fhlb.png" if save_plots else None,
                    interactive=show_plots,
                    title_suffix=" (Pre-2018: 2009-2017)",
                )

        # 5d. Purchaser type distribution
        print_section("8. Purchaser Type Distribution (Pre-2018)")
        purchaser_dist_pre = compute_purchaser_type_distribution(matches_pre)
        results["purchaser_distribution_pre2018"] = purchaser_dist_pre

        if len(purchaser_dist_pre) > 0:
            print(purchaser_dist_pre)
            print("\nNote: FHLBs have no dedicated HMDA purchaser code.")
            print("Most matched loans show purchaser_type=9 ('Other'), which is expected.")

            if save_plots or show_plots:
                plot_purchaser_type_distribution(
                    purchaser_dist_pre,
                    output_path=output_dir / "purchaser_type_distribution_pre2018.png" if save_plots else None,
                    interactive=show_plots,
                    title_suffix=" (Pre-2018: 2009-2017)",
                )

    except FileNotFoundError as e:
        print(f"Pre-2018 data not found: {e}")

    # Lender Analysis
    print_section("9. Lender (PFI) Analysis")

    lender_stats = load_lender_statistics()

    if lender_stats is not None:
        lender_analysis = analyze_lender_statistics(lender_stats)
        results["lender_analysis"] = lender_analysis

        if "yearly_summary" in lender_analysis:
            print("Yearly summary of active PFIs:")
            print(lender_analysis["yearly_summary"])

        print(f"\nTotal unique PFIs: {lender_analysis.get('n_active_lenders', 'N/A')}")

        if "top_lenders" in lender_analysis:
            print("\nTop 10 lenders by total matches:")
            print(lender_analysis["top_lenders"].head(10))

        if save_plots or show_plots:
            plot_lender_distribution(
                lender_analysis,
                output_path=output_dir / "lender_distribution.png" if save_plots else None,
                interactive=show_plots,
            )
    else:
        print("Lender statistics file not found.")

    # COMBINED PLOTS (Pre-2018 + Post-2018)
    print_section("10. Combined Analyses (2009-2024)")

    # Check if we have both datasets
    has_pre = "yearly_pre2018" in results and len(results["yearly_pre2018"]) > 0
    has_post = "yearly_post2018" in results and len(results["yearly_post2018"]) > 0

    if has_pre and has_post:
        print("Creating combined plots for pre-2018 and post-2018 data...")

        # Combined yearly match counts
        if save_plots or show_plots:
            plot_yearly_match_counts_combined(
                results["yearly_pre2018"],
                results["yearly_post2018"],
                output_path=output_dir / "yearly_match_counts_combined.png" if save_plots else None,
                interactive=show_plots,
            )

        # Combined match rates by loan amount (HMDA perspective)
        if "loan_amount_rates_pre2018" in results and "loan_amount_rates" in results:
            if len(results["loan_amount_rates_pre2018"]) > 0 and len(results["loan_amount_rates"]) > 0:
                if save_plots or show_plots:
                    plot_match_rates_by_loan_amount_combined(
                        results["loan_amount_rates_pre2018"],
                        results["loan_amount_rates"],
                        output_path=output_dir / "match_rates_by_loan_amount_combined.png" if save_plots else None,
                        interactive=show_plots,
                        fhlb_perspective=False,
                    )

        # Combined match rates by loan amount (FHLB perspective)
        if "loan_amount_rates_pre2018_fhlb" in results and "loan_amount_rates_fhlb" in results:
            if len(results["loan_amount_rates_pre2018_fhlb"]) > 0 and len(results["loan_amount_rates_fhlb"]) > 0:
                if save_plots or show_plots:
                    plot_match_rates_by_loan_amount_combined(
                        results["loan_amount_rates_pre2018_fhlb"],
                        results["loan_amount_rates_fhlb"],
                        output_path=output_dir / "match_rates_by_loan_amount_combined_fhlb.png" if save_plots else None,
                        interactive=show_plots,
                        fhlb_perspective=True,
                    )

        # Combined purchaser type distribution
        if "purchaser_distribution_pre2018" in results and "purchaser_distribution" in results:
            if len(results["purchaser_distribution_pre2018"]) > 0 and len(results["purchaser_distribution"]) > 0:
                if save_plots or show_plots:
                    plot_purchaser_type_distribution_combined(
                        results["purchaser_distribution_pre2018"],
                        results["purchaser_distribution"],
                        output_path=output_dir / "purchaser_type_distribution_combined.png" if save_plots else None,
                        interactive=show_plots,
                    )

        print("\nCombined plots complete!")
    else:
        print("Skipping combined plots (need both pre-2018 and post-2018 data)")

    # Interpretation
    print_section("11. Interpretation & Key Findings")

    print(
        """
IMPORTANT CONTEXT FOR HMDA-FHLB MATCHING:

1. Match rates of 40-55% are EXPECTED and do NOT indicate methodology problems.
   - FHLBs only acquire a subset of loans from member institutions
   - Not all FHLB member originations are sold to FHLBs
   - FHLBs are selective in their acquisitions

2. The key insight from this matching is identifying:
   - Which lenders (PFIs) actively sell loans to FHLBs
   - What types of loans FHLBs acquire
   - How FHLB acquisition patterns vary over time

3. Purchaser type distribution:
   - Most matches show purchaser_type=9 ("Other")
   - This is expected because FHLBs have no dedicated HMDA code
   - ~10-11% show purchaser_type=6 (Commercial Banks)
   - ~10% show purchaser_type=0 (Not Applicable)

4. Pre-2018 match growth:
   - Match counts increase significantly over time
   - This reflects improving data quality in both datasets
   - Earlier years had less complete reporting
"""
    )

    print("\nValidation complete!")
    if save_plots:
        print(f"Figures saved to: {output_dir}")

    return results


