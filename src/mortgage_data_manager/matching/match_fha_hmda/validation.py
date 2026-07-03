"""FHA-HMDA Match Validation Module.

This module provides validation analyses for the FHA-HMDA matching workflow:
1. Match rates over time (yearly for HMDA, monthly for FHA)
2. Match rates by geography (state, county)
3. Match rates by loan characteristics (amount, rate, purpose, ARM)
4. Correlation analysis between region size and match rates

The goal is to demonstrate that match quality is consistent across time,
geography, and loan characteristics, ensuring the matched sample is representative.

IMPORTANT CONTEXT:
- HMDA data covers 2018-2024 (activity_year)
- FHA data includes late 2017 and early 2025, but these can't match to HMDA
- For valid match rate calculations, filter to the overlapping period (2018-2024)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.matching.match_fha_hmda.config import (
    CROSSWALK_OUTPUT_DIR,
    INTERMEDIATE_DIR,
    MAX_YEAR,
    MIN_YEAR,
)

# Validation output directory - save figures to docs for documentation
VALIDATION_OUTPUT_DIR = MortgageDataConfig.PROJECT_DIR / "docs" / "matching" / "figures" / "fha_hmda"

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
# Data Loading
# =============================================================================


def load_data(
    crosswalk_path: Path | None = None,
    fha_path: Path | None = None,
    hmda_path: Path | None = None,
) -> tuple[pl.LazyFrame, pl.LazyFrame, pl.LazyFrame]:
    """Load crosswalk and intermediate data.

    Args:
        crosswalk_path: Path to crosswalk file. Defaults to standard location.
        fha_path: Path to FHA intermediate data. Defaults to standard location.
        hmda_path: Path to HMDA intermediate data. Defaults to standard location.

    Returns:
        Tuple of (crosswalk, fha, hmda) LazyFrames
    """
    if crosswalk_path is None:
        crosswalk_path = CROSSWALK_OUTPUT_DIR / f"fha_hmda_crosswalk_{MIN_YEAR}_{MAX_YEAR}.parquet"
    if fha_path is None:
        fha_path = INTERMEDIATE_DIR / "fha_match_data_post2018.parquet"
    if hmda_path is None:
        hmda_path = INTERMEDIATE_DIR / "hmda_match_data_post2018.parquet"

    crosswalk = pl.scan_parquet(crosswalk_path)
    fha = pl.scan_parquet(fha_path)
    hmda = pl.scan_parquet(hmda_path)
    return crosswalk, fha, hmda


# =============================================================================
# SECTION 1: Match Rates by HMDA Year
# =============================================================================


def hmda_yearly_match_rates(crosswalk: pl.LazyFrame, hmda: pl.LazyFrame) -> pl.DataFrame:
    """Calculate HMDA-side match rates by activity year.

    Returns DataFrame with:
    - activity_year: The HMDA reporting year
    - total_hmda_loans: Total FHA loans in HMDA data for that year
    - matched_loans: Number of HMDA loans that matched to FHA
    - match_rate: matched_loans / total_hmda_loans
    """
    matched_hmda = crosswalk.select("HMDAIndex").unique()

    hmda_totals = hmda.group_by("activity_year").agg(pl.len().alias("total_hmda_loans"))

    hmda_matched = (
        hmda.join(matched_hmda, on="HMDAIndex", how="inner")
        .group_by("activity_year")
        .agg(pl.len().alias("matched_loans"))
    )

    result = (
        hmda_totals.join(hmda_matched, on="activity_year", how="left")
        .with_columns(
            pl.col("matched_loans").fill_null(0),
            (pl.col("matched_loans") / pl.col("total_hmda_loans")).alias("match_rate"),
        )
        .sort("activity_year")
        .collect()
    )

    return result


# =============================================================================
# SECTION 2: Match Rates by FHA Year-Month
# =============================================================================


def fha_monthly_match_rates(
    crosswalk: pl.LazyFrame,
    fha: pl.LazyFrame,
    min_year: int = MIN_YEAR,
    max_year: int = MAX_YEAR,
) -> pl.DataFrame:
    """Calculate FHA-side match rates by year and month.

    Args:
        crosswalk: Match crosswalk
        fha: FHA intermediate data
        min_year: Minimum year to include (default 2018 = first HMDA year)
        max_year: Maximum year to include (default 2024 = last complete HMDA year)

    Returns DataFrame with:
    - Year, Month: The FHA endorsement year and month
    - total_fha_loans: Total FHA loans endorsed in that month
    - matched_loans: Number of FHA loans that matched to HMDA
    - match_rate: matched_loans / total_fha_loans
    """
    matched_fha = crosswalk.select("FHA_Index").unique()

    fha_filtered = fha.filter((pl.col("Year") >= min_year) & (pl.col("Year") <= max_year))

    fha_totals = fha_filtered.group_by("Year", "Month").agg(pl.len().alias("total_fha_loans"))

    fha_matched = (
        fha_filtered.join(matched_fha, on="FHA_Index", how="inner")
        .group_by("Year", "Month")
        .agg(pl.len().alias("matched_loans"))
    )

    result = (
        fha_totals.join(fha_matched, on=["Year", "Month"], how="left")
        .with_columns(
            pl.col("matched_loans").fill_null(0),
            (pl.col("matched_loans") / pl.col("total_fha_loans")).alias("match_rate"),
        )
        .sort("Year", "Month")
        .collect()
    )

    return result


def fha_yearly_match_rates(
    crosswalk: pl.LazyFrame,
    fha: pl.LazyFrame,
    min_year: int | None = None,
    max_year: int | None = None,
) -> pl.DataFrame:
    """Calculate FHA-side match rates by year (aggregated from monthly).

    Args:
        crosswalk: Match crosswalk
        fha: FHA intermediate data
        min_year: Optional minimum year filter
        max_year: Optional maximum year filter
    """
    matched_fha = crosswalk.select("FHA_Index").unique()

    fha_filtered = fha
    if min_year is not None:
        fha_filtered = fha_filtered.filter(pl.col("Year") >= min_year)
    if max_year is not None:
        fha_filtered = fha_filtered.filter(pl.col("Year") <= max_year)

    fha_totals = fha_filtered.group_by("Year").agg(pl.len().alias("total_fha_loans"))

    fha_matched = (
        fha_filtered.join(matched_fha, on="FHA_Index", how="inner")
        .group_by("Year")
        .agg(pl.len().alias("matched_loans"))
    )

    result = (
        fha_totals.join(fha_matched, on="Year", how="left")
        .with_columns(
            pl.col("matched_loans").fill_null(0),
            (pl.col("matched_loans") / pl.col("total_fha_loans")).alias("match_rate"),
        )
        .sort("Year")
        .collect()
    )

    return result


# =============================================================================
# SECTION 3: Match Rates by State
# =============================================================================


def fha_state_match_rates(
    crosswalk: pl.LazyFrame,
    fha: pl.LazyFrame,
    min_year: int = MIN_YEAR,
    max_year: int = MAX_YEAR,
) -> pl.DataFrame:
    """Calculate FHA-side match rates by property state.

    Args:
        crosswalk: Match crosswalk
        fha: FHA intermediate data
        min_year: Minimum year to include (default 2018)
        max_year: Maximum year to include (default 2024)

    Returns DataFrame with:
    - Property State: Two-letter state code
    - total_fha_loans: Total FHA loans in that state
    - matched_loans: Number matched to HMDA
    - match_rate: matched_loans / total_fha_loans
    """
    matched_fha = crosswalk.select("FHA_Index").unique()

    fha_filtered = fha.filter((pl.col("Year") >= min_year) & (pl.col("Year") <= max_year))

    fha_totals = fha_filtered.group_by("Property State").agg(pl.len().alias("total_fha_loans"))

    fha_matched = (
        fha_filtered.join(matched_fha, on="FHA_Index", how="inner")
        .group_by("Property State")
        .agg(pl.len().alias("matched_loans"))
    )

    result = (
        fha_totals.join(fha_matched, on="Property State", how="left")
        .with_columns(
            pl.col("matched_loans").fill_null(0),
            (pl.col("matched_loans") / pl.col("total_fha_loans")).alias("match_rate"),
        )
        .sort("total_fha_loans", descending=True)
        .collect()
    )

    return result


def hmda_state_match_rates(crosswalk: pl.LazyFrame, hmda: pl.LazyFrame) -> pl.DataFrame:
    """Calculate HMDA-side match rates by state."""
    matched_hmda = crosswalk.select("HMDAIndex").unique()

    hmda_totals = hmda.group_by("state_code").agg(pl.len().alias("total_hmda_loans"))

    hmda_matched = (
        hmda.join(matched_hmda, on="HMDAIndex", how="inner")
        .group_by("state_code")
        .agg(pl.len().alias("matched_loans"))
    )

    result = (
        hmda_totals.join(hmda_matched, on="state_code", how="left")
        .with_columns(
            pl.col("matched_loans").fill_null(0),
            (pl.col("matched_loans") / pl.col("total_hmda_loans")).alias("match_rate"),
        )
        .sort("total_hmda_loans", descending=True)
        .collect()
    )

    return result


# =============================================================================
# SECTION 4: Match Rates by County
# =============================================================================


def fha_county_match_rates(
    crosswalk: pl.LazyFrame,
    fha: pl.LazyFrame,
    min_year: int = MIN_YEAR,
    max_year: int = MAX_YEAR,
) -> pl.DataFrame:
    """Calculate FHA-side match rates by county FIPS."""
    matched_fha = crosswalk.select("FHA_Index").unique()

    fha_filtered = fha.filter(
        (pl.col("Year") >= min_year) & (pl.col("Year") <= max_year) & (pl.col("FIPS").is_not_null())
    )

    fha_totals = fha_filtered.group_by("FIPS").agg(pl.len().alias("total_fha_loans"))

    fha_matched = (
        fha_filtered.join(matched_fha, on="FHA_Index", how="inner")
        .group_by("FIPS")
        .agg(pl.len().alias("matched_loans"))
    )

    result = (
        fha_totals.join(fha_matched, on="FIPS", how="left")
        .with_columns(
            pl.col("matched_loans").fill_null(0),
            (pl.col("matched_loans") / pl.col("total_fha_loans")).alias("match_rate"),
        )
        .sort("total_fha_loans", descending=True)
        .collect()
    )

    return result


def hmda_county_match_rates(
    crosswalk: pl.LazyFrame,
    hmda: pl.LazyFrame,
) -> pl.DataFrame:
    """Calculate HMDA-side match rates by county FIPS."""
    matched_hmda = crosswalk.select("HMDAIndex").unique()

    hmda_filtered = hmda.filter(pl.col("county_code").is_not_null())

    hmda_totals = hmda_filtered.group_by("county_code").agg(pl.len().alias("total_hmda_loans"))

    hmda_matched = (
        hmda_filtered.join(matched_hmda, on="HMDAIndex", how="inner")
        .group_by("county_code")
        .agg(pl.len().alias("matched_loans"))
    )

    result = (
        hmda_totals.join(hmda_matched, on="county_code", how="left")
        .with_columns(
            pl.col("matched_loans").fill_null(0),
            (pl.col("matched_loans") / pl.col("total_hmda_loans")).alias("match_rate"),
        )
        .sort("total_hmda_loans", descending=True)
        .collect()
    )

    return result


# =============================================================================
# SECTION 5: State/County Size vs Match Rate Correlation
# =============================================================================


def analyze_state_size_correlation(state_df: pl.DataFrame, loan_col: str) -> dict:
    """Analyze correlation between state size (number of loans) and match rates.

    Args:
        state_df: DataFrame with state-level match rates
        loan_col: Name of the column with total loans (for calculating state size)

    Returns:
        Dictionary with correlation statistics
    """
    from scipy.stats import spearmanr

    x = state_df[loan_col].to_numpy().astype(float)
    y = state_df["match_rate"].to_numpy()

    mask = ~(np.isnan(x) | np.isnan(y))
    x, y = x[mask], y[mask]

    if len(x) < 3:
        return {"error": "Not enough data points"}

    pearson_r = np.corrcoef(x, y)[0, 1]

    log_x = np.log10(x + 1)
    log_pearson_r = np.corrcoef(log_x, y)[0, 1]

    spearman_r, spearman_p = spearmanr(x, y)

    return {
        "n_states": len(x),
        "pearson_r": pearson_r,
        "log_pearson_r": log_pearson_r,
        "spearman_r": spearman_r,
        "spearman_p": spearman_p,
        "min_match_rate": y.min(),
        "max_match_rate": y.max(),
        "mean_match_rate": y.mean(),
        "std_match_rate": y.std(),
    }


# =============================================================================
# SECTION 6: Match Round Analysis
# =============================================================================


def match_round_breakdown(crosswalk: pl.LazyFrame) -> pl.DataFrame:
    """Analyze the distribution of match rounds."""
    result = (
        crosswalk.group_by("match_round")
        .agg(pl.len().alias("count"))
        .with_columns((pl.col("count") / pl.col("count").sum()).alias("pct"))
        .sort("match_round")
        .collect()
    )
    return result


def match_round_by_year(crosswalk: pl.LazyFrame, fha: pl.LazyFrame) -> pl.DataFrame:
    """Analyze match round distribution by FHA year."""
    enriched = crosswalk.join(fha.select("FHA_Index", "Year"), on="FHA_Index", how="left")

    result = (
        enriched.group_by("Year", "match_round")
        .agg(pl.len().alias("count"))
        .sort("Year", "match_round")
        .collect()
    )

    pivoted = result.pivot(
        on="match_round",
        index="Year",
        values="count",
    ).sort("Year")

    return pivoted


# =============================================================================
# SECTION 7: Match Rates by Loan Characteristics
# =============================================================================


def compute_match_rates_by_loan_amount(
    crosswalk: pl.LazyFrame,
    fha: pl.LazyFrame,
    hmda: pl.LazyFrame,
    bin_size: int = 10000,
    max_amount: int = 750000,
) -> pl.DataFrame:
    """Compute match rates by loan amount bins (using HMDA loan_amount).

    Bins are $10K wide, centered at $5K intervals (5, 15, 25, ...) to match
    HMDA's default rounding.
    """
    matched_fha = crosswalk.select("FHA_Index").unique()
    matched_hmda = crosswalk.select("HMDAIndex").unique()

    def bin_expr(col_name: str) -> pl.Expr:
        return (((pl.col(col_name) - 5000) / bin_size).round() * bin_size + 5000).alias(
            "loan_amount_bin"
        )

    hmda_with_bin = hmda.with_columns(bin_expr("loan_amount")).filter(
        (pl.col("loan_amount_bin") >= 5000) & (pl.col("loan_amount_bin") <= max_amount)
    )

    hmda_totals = hmda_with_bin.group_by("loan_amount_bin").agg(pl.len().alias("hmda_total"))

    hmda_matched_counts = (
        hmda_with_bin.join(matched_hmda, on="HMDAIndex", how="inner")
        .group_by("loan_amount_bin")
        .agg(pl.len().alias("hmda_matched"))
    )

    fha_with_bin = fha.with_columns(bin_expr("Mortgage Amount")).filter(
        (pl.col("loan_amount_bin") >= 5000)
        & (pl.col("loan_amount_bin") <= max_amount)
        & (pl.col("Year") >= MIN_YEAR)
        & (pl.col("Year") <= MAX_YEAR)
    )

    fha_totals = fha_with_bin.group_by("loan_amount_bin").agg(pl.len().alias("fha_total"))

    fha_matched_counts = (
        fha_with_bin.join(matched_fha, on="FHA_Index", how="inner")
        .group_by("loan_amount_bin")
        .agg(pl.len().alias("fha_matched"))
    )

    result = (
        hmda_totals.join(hmda_matched_counts, on="loan_amount_bin", how="left")
        .join(fha_totals, on="loan_amount_bin", how="outer_coalesce")
        .join(fha_matched_counts, on="loan_amount_bin", how="left")
        .with_columns(
            pl.col("hmda_matched").fill_null(0),
            pl.col("fha_matched").fill_null(0),
            (pl.col("hmda_matched") / pl.col("hmda_total")).alias("hmda_match_rate"),
            (pl.col("fha_matched") / pl.col("fha_total")).alias("fha_match_rate"),
        )
        .sort("loan_amount_bin")
        .collect()
    )

    return result


def compute_match_rates_by_interest_rate(
    crosswalk: pl.LazyFrame,
    fha: pl.LazyFrame,
    hmda: pl.LazyFrame,
    bin_size: float = 0.125,
    min_rate: float = 2.0,
    max_rate: float = 9.0,
) -> pl.DataFrame:
    """Compute match rates by interest rate bins (using HMDA interest_rate).

    Rounds to nearest 0.125% (12.5 basis points) to match FHA reporting.
    """
    matched_fha = crosswalk.select("FHA_Index").unique()
    matched_hmda = crosswalk.select("HMDAIndex").unique()

    def bin_expr(col_name: str) -> pl.Expr:
        return ((pl.col(col_name) / bin_size).round() * bin_size).alias("interest_rate_bin")

    hmda_with_bin = hmda.with_columns(bin_expr("interest_rate")).filter(
        (pl.col("interest_rate_bin") >= min_rate)
        & (pl.col("interest_rate_bin") <= max_rate)
        & (pl.col("interest_rate").is_not_null())
    )

    hmda_totals = hmda_with_bin.group_by("interest_rate_bin").agg(pl.len().alias("hmda_total"))

    hmda_matched_counts = (
        hmda_with_bin.join(matched_hmda, on="HMDAIndex", how="inner")
        .group_by("interest_rate_bin")
        .agg(pl.len().alias("hmda_matched"))
    )

    fha_with_bin = fha.with_columns(bin_expr("Interest Rate")).filter(
        (pl.col("interest_rate_bin") >= min_rate)
        & (pl.col("interest_rate_bin") <= max_rate)
        & (pl.col("Interest Rate").is_not_null())
        & (pl.col("Year") >= MIN_YEAR)
        & (pl.col("Year") <= MAX_YEAR)
    )

    fha_totals = fha_with_bin.group_by("interest_rate_bin").agg(pl.len().alias("fha_total"))

    fha_matched_counts = (
        fha_with_bin.join(matched_fha, on="FHA_Index", how="inner")
        .group_by("interest_rate_bin")
        .agg(pl.len().alias("fha_matched"))
    )

    result = (
        hmda_totals.join(hmda_matched_counts, on="interest_rate_bin", how="left")
        .join(fha_totals, on="interest_rate_bin", how="outer_coalesce")
        .join(fha_matched_counts, on="interest_rate_bin", how="left")
        .with_columns(
            pl.col("hmda_matched").fill_null(0),
            pl.col("fha_matched").fill_null(0),
            (pl.col("hmda_matched") / pl.col("hmda_total")).alias("hmda_match_rate"),
            (pl.col("fha_matched") / pl.col("fha_total")).alias("fha_match_rate"),
        )
        .sort("interest_rate_bin")
        .collect()
    )

    return result


def compute_match_rates_by_loan_purpose(
    crosswalk: pl.LazyFrame,
    fha: pl.LazyFrame,
    hmda: pl.LazyFrame,
) -> dict[str, pl.DataFrame]:
    """Compute match rates by loan purpose.

    FHA Loan Purpose: Purchase, Refi_FHA, Refi_Conv_Curr
    HMDA loan_purpose: 1=Purchase, 31=Cash-out refi, 32=No cash-out refi

    Returns dict with 'fha' and 'hmda' DataFrames.
    """
    matched_fha = crosswalk.select("FHA_Index").unique()
    matched_hmda = crosswalk.select("HMDAIndex").unique()

    fha_filtered = fha.filter((pl.col("Year") >= MIN_YEAR) & (pl.col("Year") <= MAX_YEAR))

    fha_totals = fha_filtered.group_by("Loan Purpose").agg(pl.len().alias("total"))

    fha_matched = (
        fha_filtered.join(matched_fha, on="FHA_Index", how="inner")
        .group_by("Loan Purpose")
        .agg(pl.len().alias("matched"))
    )

    fha_result = (
        fha_totals.join(fha_matched, on="Loan Purpose", how="left")
        .with_columns(
            pl.col("matched").fill_null(0),
            (pl.col("matched") / pl.col("total")).alias("match_rate"),
        )
        .sort("total", descending=True)
        .collect()
    )

    purpose_labels = {
        1: "Purchase",
        31: "Cash-out Refi",
        32: "No Cash-out Refi",
        2: "Home Improvement",
        4: "Other",
    }

    hmda_totals = hmda.group_by("loan_purpose").agg(pl.len().alias("total"))

    hmda_matched = (
        hmda.join(matched_hmda, on="HMDAIndex", how="inner")
        .group_by("loan_purpose")
        .agg(pl.len().alias("matched"))
    )

    hmda_result = (
        hmda_totals.join(hmda_matched, on="loan_purpose", how="left")
        .with_columns(
            pl.col("matched").fill_null(0),
            (pl.col("matched") / pl.col("total")).alias("match_rate"),
            pl.col("loan_purpose")
            .replace_strict(purpose_labels, default="Other")
            .alias("purpose_label"),
        )
        .sort("total", descending=True)
        .collect()
    )

    return {"fha": fha_result, "hmda": hmda_result}


def compute_match_rates_by_arm(
    crosswalk: pl.LazyFrame,
    fha: pl.LazyFrame,
    hmda: pl.LazyFrame,
) -> dict[str, pl.DataFrame]:
    """Compute match rates by ARM vs Fixed rate.

    FHA: i_ARM (0=Fixed, 1=ARM)
    HMDA: 1(ARM) (0=Fixed, 1=ARM)
    """
    matched_fha = crosswalk.select("FHA_Index").unique()
    matched_hmda = crosswalk.select("HMDAIndex").unique()

    arm_labels = {0: "Fixed Rate", 1: "ARM"}

    fha_filtered = fha.filter((pl.col("Year") >= MIN_YEAR) & (pl.col("Year") <= MAX_YEAR))

    fha_totals = fha_filtered.group_by("i_ARM").agg(pl.len().alias("total"))
    fha_matched = (
        fha_filtered.join(matched_fha, on="FHA_Index", how="inner")
        .group_by("i_ARM")
        .agg(pl.len().alias("matched"))
    )

    fha_result = (
        fha_totals.join(fha_matched, on="i_ARM", how="left")
        .with_columns(
            pl.col("matched").fill_null(0),
            (pl.col("matched") / pl.col("total")).alias("match_rate"),
            pl.col("i_ARM").replace_strict(arm_labels, default="Unknown").alias("rate_type"),
        )
        .sort("total", descending=True)
        .collect()
    )

    hmda_filtered = hmda.filter(pl.col("1(ARM)").is_not_null())

    hmda_totals = hmda_filtered.group_by("1(ARM)").agg(pl.len().alias("total"))
    hmda_matched = (
        hmda_filtered.join(matched_hmda, on="HMDAIndex", how="inner")
        .group_by("1(ARM)")
        .agg(pl.len().alias("matched"))
    )

    hmda_result = (
        hmda_totals.join(hmda_matched, on="1(ARM)", how="left")
        .with_columns(
            pl.col("matched").fill_null(0),
            (pl.col("matched") / pl.col("total")).alias("match_rate"),
            pl.col("1(ARM)").replace_strict(arm_labels, default="Unknown").alias("rate_type"),
        )
        .sort("total", descending=True)
        .collect()
    )

    return {"fha": fha_result, "hmda": hmda_result}


def compute_match_rates_by_submission_channel(
    crosswalk: pl.LazyFrame,
    hmda: pl.LazyFrame,
) -> pl.DataFrame:
    """Compute HMDA match rates by submission channel.

    submission_of_application: 1=Direct to lender, 2=Not direct (broker)
    """
    matched_hmda = crosswalk.select("HMDAIndex").unique()

    channel_labels = {1: "Direct to Lender", 2: "Broker/Correspondent"}

    hmda_filtered = hmda.filter(pl.col("submission_of_application").is_not_null())

    hmda_totals = hmda_filtered.group_by("submission_of_application").agg(pl.len().alias("total"))
    hmda_matched = (
        hmda_filtered.join(matched_hmda, on="HMDAIndex", how="inner")
        .group_by("submission_of_application")
        .agg(pl.len().alias("matched"))
    )

    result = (
        hmda_totals.join(hmda_matched, on="submission_of_application", how="left")
        .with_columns(
            pl.col("matched").fill_null(0),
            (pl.col("matched") / pl.col("total")).alias("match_rate"),
            pl.col("submission_of_application")
            .replace_strict(channel_labels, default="Unknown")
            .alias("channel"),
        )
        .sort("total", descending=True)
        .collect()
    )

    return result


# =============================================================================
# SECTION 8: Visualization Functions
# =============================================================================


def plot_temporal_match_rates(
    hmda_yearly: pl.DataFrame,
    fha_yearly: pl.DataFrame,
    fha_monthly: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot temporal match rates on a single chart.

    Shows:
    - HMDA yearly match rate (blue line) - centered at July of each year
    - FHA yearly match rate (green line) - centered at July of each year
    - FHA monthly match rate (light green, using alpha)
    """
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    hmda_color = "#D4AC0D"  # Burnished Gold
    fha_color = "#145A32"  # Hunter Green
    fha_monthly_color = "#145A32"  # Hunter Green
    fha_monthly_alpha = 0.3

    fig, ax = plt.subplots(figsize=(14, 6))

    def to_x(year: int, month: int) -> float:
        return (year - 2018) * 12 + (month - 1)

    monthly_x = [to_x(row["Year"], row["Month"]) for row in fha_monthly.iter_rows(named=True)]

    ax.fill_between(
        monthly_x,
        fha_monthly["match_rate"].to_list(),
        alpha=fha_monthly_alpha,
        color=fha_monthly_color,
        label="FHA Monthly",
    )
    ax.plot(
        monthly_x,
        fha_monthly["match_rate"].to_list(),
        color=fha_monthly_color,
        alpha=0.5,
        linewidth=0.8,
    )

    hmda_years_x = [to_x(int(y), 7) for y in hmda_yearly["activity_year"].to_list()]
    fha_years_x = [to_x(int(y), 7) for y in fha_yearly["Year"].to_list()]

    ax.plot(
        hmda_years_x,
        hmda_yearly["match_rate"].to_list(),
        color=hmda_color,
        linewidth=2.5,
        marker="o",
        markersize=8,
        label="HMDA Yearly (centered at July)",
    )

    ax.plot(
        fha_years_x,
        fha_yearly["match_rate"].to_list(),
        color=fha_color,
        linewidth=2.5,
        marker="s",
        markersize=8,
        label="FHA Yearly (centered at July)",
    )

    overall_hmda = hmda_yearly["matched_loans"].sum() / hmda_yearly["total_hmda_loans"].sum()
    overall_fha = fha_yearly["matched_loans"].sum() / fha_yearly["total_fha_loans"].sum()

    ax.axhline(overall_hmda, color=hmda_color, linestyle="--", alpha=0.5, linewidth=1)
    ax.axhline(overall_fha, color=fha_color, linestyle="--", alpha=0.5, linewidth=1)

    ax.set_xlabel("Month")
    ax.set_ylabel("Match Rate")
    ax.set_title("FHA-HMDA Match Rates Over Time")

    years = sorted(set(fha_yearly["Year"].to_list()) | set(hmda_yearly["activity_year"].to_list()))
    tick_positions = []
    tick_labels = []
    for year in years:
        tick_positions.append(to_x(year, 1))
        tick_labels.append(f"Jan\n{year}")
        tick_positions.append(to_x(year, 7))
        tick_labels.append("Jul")

    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels, fontsize=9)

    ax.set_xlim(to_x(min(years), 1) - 1, to_x(max(years), 12) + 1)
    ax.set_ylim(0.7, 0.95)

    ax.legend(loc="lower right")
    ax.grid(True, alpha=0.3, axis="y")

    for year in years:
        ax.axvline(to_x(year, 1), color="gray", alpha=0.2, linewidth=0.5)

    ax.text(
        to_x(max(years), 12) + 2,
        overall_hmda + 0.003,
        f"HMDA: {overall_hmda:.1%}",
        color=hmda_color,
        fontsize=9,
        va="bottom",
    )
    ax.text(
        to_x(max(years), 12) + 2,
        overall_fha - 0.003,
        f"FHA: {overall_fha:.1%}",
        color=fha_color,
        fontsize=9,
        va="top",
    )

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_match_rates_by_loan_amount(
    loan_amount_rates: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot match rates by loan amount bin with density overlay."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(12, 6))

    x = loan_amount_rates["loan_amount_bin"].to_numpy() / 1000

    hmda_total = loan_amount_rates["hmda_total"].to_numpy().astype(float)
    fha_total = loan_amount_rates["fha_total"].to_numpy().astype(float)

    hmda_pdf = hmda_total / hmda_total.sum()
    fha_pdf = fha_total / fha_total.sum()

    ax2 = ax.twinx()

    ax2.fill_between(x, hmda_pdf, alpha=0.15, color="#D4AC0D", label="HMDA Density")
    ax2.fill_between(x, fha_pdf, alpha=0.15, color="#145A32", label="FHA Density")
    ax2.set_ylabel("Density (PDF)", color="gray")
    ax2.tick_params(axis="y", labelcolor="gray")
    ax2.set_ylim(0, max(max(hmda_pdf), max(fha_pdf)) * 1.3)

    ax.plot(
        x,
        loan_amount_rates["hmda_match_rate"].to_list(),
        color="#D4AC0D",  # Burnished Gold
        linewidth=2,
        marker="o",
        markersize=5,
        label="HMDA Match Rate",
        zorder=10,
    )

    ax.plot(
        x,
        loan_amount_rates["fha_match_rate"].to_list(),
        color="#145A32",  # Hunter Green
        linewidth=2,
        marker="s",
        markersize=5,
        label="FHA Match Rate",
        zorder=10,
    )

    ax.set_xlabel("Loan Amount ($K)")
    ax.set_ylabel("Match Rate")
    ax.set_title("Match Rates by Loan Amount (with density overlay)")

    all_rates = (
        loan_amount_rates["hmda_match_rate"].to_list()
        + loan_amount_rates["fha_match_rate"].to_list()
    )
    valid_rates = [r for r in all_rates if r is not None and not np.isnan(r)]
    if valid_rates:
        y_min = max(0.5, min(valid_rates) - 0.05)
        y_max = min(1.0, max(valid_rates) + 0.05)
        ax.set_ylim(y_min, y_max)

    ax.legend(loc="lower left")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_match_rates_by_interest_rate(
    interest_rate_rates: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot match rates by interest rate bin with density overlay."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(12, 6))

    x = np.array(interest_rate_rates["interest_rate_bin"].to_list())

    hmda_total = interest_rate_rates["hmda_total"].to_numpy().astype(float)
    fha_total = interest_rate_rates["fha_total"].to_numpy().astype(float)

    hmda_total = np.nan_to_num(hmda_total, nan=0)
    fha_total = np.nan_to_num(fha_total, nan=0)

    hmda_pdf = hmda_total / hmda_total.sum() if hmda_total.sum() > 0 else hmda_total
    fha_pdf = fha_total / fha_total.sum() if fha_total.sum() > 0 else fha_total

    ax2 = ax.twinx()

    ax2.fill_between(x, hmda_pdf, alpha=0.15, color="#D4AC0D", label="HMDA Density")
    ax2.fill_between(x, fha_pdf, alpha=0.15, color="#145A32", label="FHA Density")
    ax2.set_ylabel("Density (PDF)", color="gray")
    ax2.tick_params(axis="y", labelcolor="gray")
    ax2.set_ylim(0, max(max(hmda_pdf), max(fha_pdf)) * 1.3)

    ax.plot(
        x,
        interest_rate_rates["hmda_match_rate"].to_list(),
        color="#D4AC0D",  # Burnished Gold
        linewidth=2,
        marker="o",
        markersize=5,
        label="HMDA Match Rate",
        zorder=10,
    )

    ax.plot(
        x,
        interest_rate_rates["fha_match_rate"].to_list(),
        color="#145A32",  # Hunter Green
        linewidth=2,
        marker="s",
        markersize=5,
        label="FHA Match Rate",
        zorder=10,
    )

    ax.set_xlabel("Interest Rate (%)")
    ax.set_ylabel("Match Rate")
    ax.set_title("Match Rates by Interest Rate (with density overlay)")

    all_rates = (
        interest_rate_rates["hmda_match_rate"].to_list()
        + interest_rate_rates["fha_match_rate"].to_list()
    )
    valid_rates = [r for r in all_rates if r is not None and not np.isnan(r)]
    if valid_rates:
        y_min = max(0.5, min(valid_rates) - 0.05)
        y_max = min(1.0, max(valid_rates) + 0.05)
        ax.set_ylim(y_min, y_max)

    ax.legend(loc="lower left")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_match_rates_by_category(
    data: dict[str, pl.DataFrame] | pl.DataFrame,
    title: str,
    category_col: str,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot match rates by category as a grouped bar chart.

    If data is a dict with 'fha' and 'hmda' keys, plots both side by side.
    If data is a single DataFrame, plots just that.
    """
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(10, 6))

    if isinstance(data, dict):
        fha_df = data["fha"]
        hmda_df = data["hmda"]

        fha_cats = fha_df[category_col].to_list()

        if category_col == "Loan Purpose":
            categories = ["Purchase", "Refinance"]
            fha_rates = []
            hmda_rates = []

            purchase_fha = fha_df.filter(pl.col("Loan Purpose") == "Purchase")["match_rate"]
            fha_rates.append(purchase_fha[0] if len(purchase_fha) > 0 else 0)

            purchase_hmda = hmda_df.filter(pl.col("loan_purpose") == 1)["match_rate"]
            hmda_rates.append(purchase_hmda[0] if len(purchase_hmda) > 0 else 0)

            refi_fha = fha_df.filter(pl.col("Loan Purpose").is_in(["Refi_FHA", "Refi_Conv_Curr"]))
            fha_refi_matched = refi_fha["matched"].sum()
            fha_refi_total = refi_fha["total"].sum()
            fha_rates.append(fha_refi_matched / fha_refi_total if fha_refi_total > 0 else 0)

            refi_hmda = hmda_df.filter(pl.col("loan_purpose").is_in([31, 32]))
            hmda_refi_matched = refi_hmda["matched"].sum()
            hmda_refi_total = refi_hmda["total"].sum()
            hmda_rates.append(hmda_refi_matched / hmda_refi_total if hmda_refi_total > 0 else 0)

        elif category_col == "rate_type":
            categories = fha_df[category_col].to_list()
            fha_rates = fha_df["match_rate"].to_list()

            hmda_rates = []
            for cat in categories:
                row = hmda_df.filter(pl.col(category_col) == cat)
                hmda_rates.append(row["match_rate"][0] if len(row) > 0 else 0)
        else:
            categories = fha_cats
            fha_rates = fha_df["match_rate"].to_list()
            hmda_rates = [0] * len(categories)

        x = np.arange(len(categories))
        width = 0.35

        ax.bar(x - width / 2, fha_rates, width, label="FHA", color="#145A32", alpha=0.8)
        ax.bar(x + width / 2, hmda_rates, width, label="HMDA", color="#D4AC0D", alpha=0.8)

        ax.set_xticks(x)
        ax.set_xticklabels(categories)

        for i, (fha_r, hmda_r) in enumerate(zip(fha_rates, hmda_rates)):
            ax.text(
                i - width / 2, fha_r + 0.01, f"{fha_r:.1%}", ha="center", va="bottom", fontsize=9
            )
            ax.text(
                i + width / 2, hmda_r + 0.01, f"{hmda_r:.1%}", ha="center", va="bottom", fontsize=9
            )

    else:
        categories = (
            data[category_col].to_list()
            if category_col in data.columns
            else data["channel"].to_list()
        )
        rates = data["match_rate"].to_list()

        x = np.arange(len(categories))
        ax.bar(x, rates, color="#D4AC0D", alpha=0.8)  # Burnished Gold
        ax.set_xticks(x)
        ax.set_xticklabels(categories)

        for i, r in enumerate(rates):
            ax.text(i, r + 0.01, f"{r:.1%}", ha="center", va="bottom", fontsize=9)

    ax.set_ylabel("Match Rate")
    ax.set_title(title)

    if isinstance(data, dict):
        all_rates = fha_rates + hmda_rates
    else:
        all_rates = rates
    y_min = max(0.5, min(all_rates) - 0.1)
    y_max = min(1.0, max(all_rates) + 0.1)
    ax.set_ylim(y_min, y_max)

    if isinstance(data, dict):
        ax.legend()

    ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_state_match_rate_map(
    fha_states: pl.DataFrame,
    hmda_states: pl.DataFrame,
    output_path: Path | None = None,
) -> None:
    """Plot choropleth maps of match rates by state.

    Creates a two-panel figure:
    - Left: FHA match rates (FHA denominator)
    - Right: HMDA match rates (HMDA denominator)
    """
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        print("plotly not available, skipping state map")
        return

    territories = {"PR", "GU", "VI", "AS", "MP", "DC"}

    fha_filtered = fha_states.filter(
        (~pl.col("Property State").is_in(territories)) & (pl.col("match_rate").is_not_null())
    ).select(
        [
            pl.col("Property State").alias("state"),
            pl.col("match_rate"),
            pl.col("total_fha_loans").alias("total"),
        ]
    )

    hmda_filtered = hmda_states.filter(
        (~pl.col("state_code").is_in(territories)) & (pl.col("match_rate").is_not_null())
    ).select(
        [
            pl.col("state_code").alias("state"),
            pl.col("match_rate"),
            pl.col("total_hmda_loans").alias("total"),
        ]
    )

    all_rates = [
        r
        for r in (fha_filtered["match_rate"].to_list() + hmda_filtered["match_rate"].to_list())
        if r is not None
    ]
    color_min = max(0.70, min(all_rates) - 0.02)
    color_max = min(1.0, max(all_rates) + 0.02)

    fig = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=("FHA Match Rate by State", "HMDA Match Rate by State"),
        specs=[[{"type": "choropleth"}, {"type": "choropleth"}]],
        horizontal_spacing=0.02,
    )

    fig.add_trace(
        go.Choropleth(
            locations=fha_filtered["state"].to_list(),
            z=fha_filtered["match_rate"].to_list(),
            locationmode="USA-states",
            colorscale="RdYlGn",
            zmin=color_min,
            zmax=color_max,
            colorbar=dict(
                title="Match Rate",
                x=0.45,
                len=0.8,
                tickformat=".0%",
            ),
            hovertemplate="<b>%{location}</b><br>Match Rate: %{z:.1%}<extra></extra>",
        ),
        row=1,
        col=1,
    )

    fig.add_trace(
        go.Choropleth(
            locations=hmda_filtered["state"].to_list(),
            z=hmda_filtered["match_rate"].to_list(),
            locationmode="USA-states",
            colorscale="RdYlGn",
            zmin=color_min,
            zmax=color_max,
            colorbar=dict(
                title="Match Rate",
                x=1.0,
                len=0.8,
                tickformat=".0%",
            ),
            hovertemplate="<b>%{location}</b><br>Match Rate: %{z:.1%}<extra></extra>",
        ),
        row=1,
        col=2,
    )

    fig.update_geos(
        scope="usa",
        showlakes=False,
        showland=True,
        landcolor="lightgray",
    )

    fig.update_layout(
        title_text="Match Rates by State",
        title_x=0.5,
        width=1400,
        height=600,
        margin=dict(l=0, r=0, t=60, b=0),
    )

    if output_path:
        fig.write_image(str(output_path), scale=2)
        print(f"Saved: {output_path}")


def plot_county_match_rate_map(
    fha_counties: pl.DataFrame,
    hmda_counties: pl.DataFrame,
    output_path: Path | None = None,
    min_loans: int = 50,
) -> None:
    """Plot choropleth maps of match rates by county.

    Creates a two-panel figure:
    - Left: FHA match rates (FHA denominator)
    - Right: HMDA match rates (HMDA denominator)

    Args:
        fha_counties: DataFrame with FIPS, match_rate, total_fha_loans
        hmda_counties: DataFrame with county_code, match_rate, total_hmda_loans
        output_path: Path to save the figure
        min_loans: Minimum loans required to show a county (reduces noise)
    """
    try:
        import json
        from urllib.request import urlopen

        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        print("plotly not available, skipping county map")
        return

    with urlopen(
        "https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json"
    ) as response:
        counties_geojson = json.load(response)

    territory_prefixes = {"72", "78", "66", "60", "69"}

    fha_filtered = fha_counties.filter(
        (pl.col("total_fha_loans") >= min_loans)
        & (pl.col("match_rate").is_not_null())
        & (~pl.col("FIPS").str.slice(0, 2).is_in(territory_prefixes))
    )

    hmda_filtered = hmda_counties.filter(
        (pl.col("total_hmda_loans") >= min_loans)
        & (pl.col("match_rate").is_not_null())
        & (~pl.col("county_code").str.slice(0, 2).is_in(territory_prefixes))
    )

    all_rates = [
        r
        for r in (fha_filtered["match_rate"].to_list() + hmda_filtered["match_rate"].to_list())
        if r is not None
    ]

    if not all_rates:
        print("No county data available for mapping")
        return

    color_min = max(0.50, min(all_rates) - 0.02)
    color_max = min(1.0, max(all_rates) + 0.02)

    fig = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=(
            f"FHA Match Rate by County (n={len(fha_filtered):,})",
            f"HMDA Match Rate by County (n={len(hmda_filtered):,})",
        ),
        specs=[[{"type": "choropleth"}, {"type": "choropleth"}]],
        horizontal_spacing=0.02,
    )

    fig.add_trace(
        go.Choropleth(
            geojson=counties_geojson,
            locations=fha_filtered["FIPS"].to_list(),
            z=fha_filtered["match_rate"].to_list(),
            colorscale="RdYlGn",
            zmin=color_min,
            zmax=color_max,
            marker_line_width=0.1,
            marker_line_color="white",
            colorbar=dict(
                title="Match Rate",
                x=0.45,
                len=0.8,
                tickformat=".0%",
            ),
            hovertemplate="<b>FIPS: %{location}</b><br>Match Rate: %{z:.1%}<extra></extra>",
        ),
        row=1,
        col=1,
    )

    fig.add_trace(
        go.Choropleth(
            geojson=counties_geojson,
            locations=hmda_filtered["county_code"].to_list(),
            z=hmda_filtered["match_rate"].to_list(),
            colorscale="RdYlGn",
            zmin=color_min,
            zmax=color_max,
            marker_line_width=0.1,
            marker_line_color="white",
            colorbar=dict(
                title="Match Rate",
                x=1.0,
                len=0.8,
                tickformat=".0%",
            ),
            hovertemplate="<b>FIPS: %{location}</b><br>Match Rate: %{z:.1%}<extra></extra>",
        ),
        row=1,
        col=2,
    )

    fig.update_geos(
        scope="usa",
        showlakes=False,
        showland=True,
        landcolor="lightgray",
        showsubunits=True,
        subunitcolor="white",
    )

    fig.update_layout(
        title_text=f"Match Rates by County (min {min_loans} loans)",
        title_x=0.5,
        width=1600,
        height=700,
        margin=dict(l=0, r=0, t=80, b=0),
    )

    if output_path:
        fig.write_image(str(output_path), scale=2)
        print(f"Saved: {output_path}")


def plot_county_size_vs_match_rate(
    fha_counties: pl.DataFrame,
    hmda_counties: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
    min_loans: int = 50,
) -> dict:
    """Scatter plot of county size vs match rate for both FHA and HMDA.

    Returns correlation statistics for both.
    """
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return {}

    from scipy.stats import spearmanr

    territory_prefixes = ["72", "78", "66", "60", "69"]

    fha_filtered = fha_counties.filter(
        (pl.col("total_fha_loans") >= min_loans)
        & (pl.col("match_rate").is_not_null())
        & (~pl.col("FIPS").str.slice(0, 2).is_in(territory_prefixes))
    )

    hmda_filtered = hmda_counties.filter(
        (pl.col("total_hmda_loans") >= min_loans)
        & (pl.col("match_rate").is_not_null())
        & (~pl.col("county_code").str.slice(0, 2).is_in(territory_prefixes))
    )

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    fha_x = fha_filtered["total_fha_loans"].to_numpy()
    fha_y = fha_filtered["match_rate"].to_numpy()

    ax1.scatter(fha_x, fha_y, alpha=0.3, s=15, color="#145A32")  # Hunter Green
    ax1.set_xscale("log")
    ax1.set_xlabel("Total FHA Loans (Log Scale)")
    ax1.set_ylabel("Match Rate")
    ax1.set_title(f"FHA County Size vs Match Rate\n(n={len(fha_filtered):,} counties)")

    all_rates = list(fha_y) + list(hmda_filtered["match_rate"].to_numpy())
    y_min = max(0.40, min(all_rates) - 0.05)
    y_max = min(1.0, max(all_rates) + 0.05)
    ax1.set_ylim(y_min, y_max)
    ax1.grid(True, alpha=0.3)

    fha_r, fha_p = spearmanr(fha_x, fha_y)
    ax1.text(
        0.05,
        0.95,
        f"Spearman r = {fha_r:.3f}\np = {fha_p:.4f}",
        transform=ax1.transAxes,
        fontsize=10,
        verticalalignment="top",
        bbox=dict(boxstyle="round", facecolor="white", alpha=0.8),
    )

    hmda_x = hmda_filtered["total_hmda_loans"].to_numpy()
    hmda_y = hmda_filtered["match_rate"].to_numpy()

    ax2.scatter(hmda_x, hmda_y, alpha=0.3, s=15, color="#D4AC0D")  # Burnished Gold
    ax2.set_xscale("log")
    ax2.set_xlabel("Total HMDA Loans (Log Scale)")
    ax2.set_ylabel("Match Rate")
    ax2.set_title(f"HMDA County Size vs Match Rate\n(n={len(hmda_filtered):,} counties)")
    ax2.set_ylim(y_min, y_max)
    ax2.grid(True, alpha=0.3)

    hmda_r, hmda_p = spearmanr(hmda_x, hmda_y)
    ax2.text(
        0.05,
        0.95,
        f"Spearman r = {hmda_r:.3f}\np = {hmda_p:.4f}",
        transform=ax2.transAxes,
        fontsize=10,
        verticalalignment="top",
        bbox=dict(boxstyle="round", facecolor="white", alpha=0.8),
    )

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)

    return {
        "fha_spearman_r": fha_r,
        "fha_spearman_p": fha_p,
        "fha_n_counties": len(fha_filtered),
        "hmda_spearman_r": hmda_r,
        "hmda_spearman_p": hmda_p,
        "hmda_n_counties": len(hmda_filtered),
    }


def plot_state_size_vs_match_rate(
    state_df: pl.DataFrame,
    loan_col: str,
    title: str,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Scatter plot of state size vs match rate."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    territories = {"PR", "GU", "VI", "AS", "MP", "DC"}

    state_col = "Property State" if "Property State" in state_df.columns else "state_code"
    filtered = state_df.filter((pl.col(loan_col) >= 100) & (~pl.col(state_col).is_in(territories)))

    x = filtered[loan_col].to_numpy()
    y = filtered["match_rate"].to_numpy()
    states = filtered[state_col].to_list()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    y_min = max(0.70, y.min() - 0.02)
    y_max = min(1.0, y.max() + 0.02)

    ax1.scatter(x, y, alpha=0.7, s=50)
    ax1.set_xlabel("Total Loans")
    ax1.set_ylabel("Match Rate")
    ax1.set_title(f"{title}\n(Linear Scale)")
    ax1.set_ylim(y_min, y_max)

    ax2.scatter(x, y, alpha=0.7, s=50)
    ax2.set_xscale("log")
    ax2.set_xlabel("Total Loans (Log Scale)")
    ax2.set_ylabel("Match Rate")
    ax2.set_title(f"{title}\n(Log Scale)")
    ax2.set_ylim(y_min, y_max)

    for xi, yi, state in zip(x, y, states):
        if yi < 0.80 or xi > 500000:
            ax2.annotate(
                state,
                (xi, yi),
                fontsize=8,
                alpha=0.8,
                xytext=(5, 0),
                textcoords="offset points",
            )

    ax1.grid(True, alpha=0.3)
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


# =============================================================================
# SECTION 9: Main Entry Point
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
    min_loans: int = 50,
) -> dict[str, Any]:
    """Run all validation analyses.

    Args:
        output_dir: Directory to save figures. Defaults to VALIDATION_OUTPUT_DIR.
        save_plots: Whether to save plots to disk
        show_plots: Whether to display matplotlib plots interactively
        min_loans: Minimum loans for county filtering

    Returns:
        Dictionary with all computed statistics and DataFrames
    """
    if output_dir is None:
        output_dir = VALIDATION_OUTPUT_DIR

    if save_plots:
        output_dir.mkdir(parents=True, exist_ok=True)

    print("Loading data...")
    crosswalk, fha, hmda = load_data()

    n_matches = crosswalk.select(pl.len()).collect().item()
    n_fha = fha.select(pl.len()).collect().item()
    n_hmda = hmda.select(pl.len()).collect().item()

    print(f"Crosswalk: {n_matches:,} matched pairs")
    print(f"FHA intermediate: {n_fha:,} loans")
    print(f"HMDA intermediate: {n_hmda:,} loans")
    print("\nNote: HMDA data covers 2018-2024. FHA data includes 2017 and 2025,")
    print("      but those years cannot match to HMDA. Filtering to 2018-2024.")

    # ------- Match Round Breakdown -------
    print_section("1. Match Round Breakdown")
    round_breakdown = match_round_breakdown(crosswalk)
    print(round_breakdown)

    # ------- HMDA Yearly Match Rates -------
    print_section("2. HMDA Match Rates by Year")
    hmda_yearly = hmda_yearly_match_rates(crosswalk, hmda)
    print(hmda_yearly)

    overall_hmda_rate = hmda_yearly["matched_loans"].sum() / hmda_yearly["total_hmda_loans"].sum()
    print(f"\nOverall HMDA match rate: {overall_hmda_rate:.1%}")

    # ------- FHA Yearly Match Rates -------
    print_section("3. FHA Match Rates by Year (2018-2024 only)")
    fha_yearly = fha_yearly_match_rates(crosswalk, fha, min_year=MIN_YEAR, max_year=MAX_YEAR)
    print(fha_yearly)

    overall_fha_rate = fha_yearly["matched_loans"].sum() / fha_yearly["total_fha_loans"].sum()
    print(f"\nOverall FHA match rate (2018-2024): {overall_fha_rate:.1%}")

    # ------- FHA Monthly Match Rates (summary stats) -------
    print_section("4. FHA Monthly Match Rate Summary (2018-2024)")
    fha_monthly = fha_monthly_match_rates(crosswalk, fha, min_year=MIN_YEAR, max_year=MAX_YEAR)

    print(f"Number of months: {len(fha_monthly)}")
    print(f"Min monthly match rate: {fha_monthly['match_rate'].min():.1%}")
    print(f"Max monthly match rate: {fha_monthly['match_rate'].max():.1%}")
    print(f"Mean monthly match rate: {fha_monthly['match_rate'].mean():.1%}")
    print(f"Std dev: {fha_monthly['match_rate'].std():.3f}")

    print("\nLowest match rate months:")
    print(fha_monthly.sort("match_rate").head(5))

    print("\nHighest match rate months:")
    print(fha_monthly.sort("match_rate", descending=True).head(5))

    # ------- State Match Rates -------
    print_section("5. FHA Match Rates by State (Top 20 by Volume)")
    fha_states = fha_state_match_rates(crosswalk, fha, min_year=MIN_YEAR, max_year=MAX_YEAR)
    print(fha_states.head(20))

    print("\nLowest match rate states (min 1000 loans):")
    filtered = fha_states.filter(pl.col("total_fha_loans") >= 1000)
    print(filtered.sort("match_rate").head(10))

    # ------- State Size Correlation -------
    print_section("6. State Size vs Match Rate Correlation")

    print("FHA-side correlation (Property State):")
    fha_corr = analyze_state_size_correlation(
        fha_states.filter(pl.col("total_fha_loans") >= 100),
        "total_fha_loans",
    )
    for k, v in fha_corr.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.4f}")
        else:
            print(f"  {k}: {v}")

    print("\nHMDA-side correlation (state_code):")
    hmda_states = hmda_state_match_rates(crosswalk, hmda)
    hmda_corr = analyze_state_size_correlation(
        hmda_states.filter(pl.col("total_hmda_loans") >= 100),
        "total_hmda_loans",
    )
    for k, v in hmda_corr.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.4f}")
        else:
            print(f"  {k}: {v}")

    # ------- Match Round by Year -------
    print_section("7. Match Round Distribution by Year")
    round_by_year = match_round_by_year(crosswalk, fha)
    round_by_year = round_by_year.filter(
        (pl.col("Year") >= MIN_YEAR) & (pl.col("Year") <= MAX_YEAR)
    )
    print(round_by_year)

    # ------- Interpretation -------
    print_section("8. Interpretation & Key Findings")

    print("Match Rate Summary:")
    print(f"  - Overall FHA match rate (2018-2024): {overall_fha_rate:.1%}")
    print(f"  - Overall HMDA match rate: {overall_hmda_rate:.1%}")
    print(
        f"  - Round 1 (strict) captures: "
        f"{round_breakdown.filter(pl.col('match_round') == 1)['pct'][0]:.1%} of matches"
    )

    print("\nTemporal Stability:")
    yearly_std = fha_yearly["match_rate"].std()
    monthly_std = fha_monthly["match_rate"].std()
    print(f"  - Yearly match rate std dev: {yearly_std:.3f}")
    print(f"  - Monthly match rate std dev: {monthly_std:.3f}")

    print("\nState Size Correlation:")
    print(f"  - FHA Spearman r: {fha_corr['spearman_r']:.3f} (p={fha_corr['spearman_p']:.4f})")
    print(f"  - HMDA Spearman r: {hmda_corr['spearman_r']:.3f} (p={hmda_corr['spearman_p']:.4f})")

    r = fha_corr["spearman_r"]
    if abs(r) < 0.1:
        interpretation = "negligible"
    elif abs(r) < 0.3:
        interpretation = "weak"
    elif abs(r) < 0.5:
        interpretation = "moderate"
    else:
        interpretation = "strong"

    direction = "negative" if r < 0 else "positive"
    print(
        f"\n  Interpretation: {interpretation} {direction} correlation "
        "between state size and match rate"
    )

    if abs(r) < 0.3:
        print("  This suggests match quality is NOT heavily driven by state size,")
        print("  which is a good sign for the matching methodology.")

    # ------- Visualizations -------
    county_corr = {}
    if (show_plots or save_plots) and HAS_MATPLOTLIB:
        print_section("9. Visualizations")

        plot_temporal_match_rates(
            hmda_yearly,
            fha_yearly,
            fha_monthly,
            output_path=output_dir / "temporal_match_rates.png" if save_plots else None,
            interactive=show_plots,
        )

        plot_state_size_vs_match_rate(
            fha_states,
            "total_fha_loans",
            "FHA State Size vs Match Rate",
            output_path=output_dir / "state_size_correlation.png" if save_plots else None,
            interactive=show_plots,
        )

        print("Generating state match rate map...")
        plot_state_match_rate_map(
            fha_states,
            hmda_states,
            output_path=output_dir / "state_match_rate_map.png" if save_plots else None,
        )

        print("Computing county match rates...")
        fha_counties = fha_county_match_rates(crosswalk, fha, min_year=MIN_YEAR, max_year=MAX_YEAR)
        hmda_counties = hmda_county_match_rates(crosswalk, hmda)
        print(f"  FHA counties: {len(fha_counties):,}, HMDA counties: {len(hmda_counties):,}")

        print("Generating county match rate map...")
        plot_county_match_rate_map(
            fha_counties,
            hmda_counties,
            output_path=output_dir / "county_match_rate_map.png" if save_plots else None,
            min_loans=min_loans,
        )

        print("Generating county size vs match rate scatter...")
        county_corr = plot_county_size_vs_match_rate(
            fha_counties,
            hmda_counties,
            output_path=output_dir / "county_size_correlation.png" if save_plots else None,
            interactive=show_plots,
            min_loans=min_loans,
        )
        if county_corr:
            print(
                f"  FHA: Spearman r = {county_corr['fha_spearman_r']:.3f} "
                f"(p={county_corr['fha_spearman_p']:.4f})"
            )
            print(
                f"  HMDA: Spearman r = {county_corr['hmda_spearman_r']:.3f} "
                f"(p={county_corr['hmda_spearman_p']:.4f})"
            )

        print("Computing match rates by loan amount...")
        loan_amount_rates = compute_match_rates_by_loan_amount(crosswalk, fha, hmda)
        plot_match_rates_by_loan_amount(
            loan_amount_rates,
            output_path=output_dir / "match_rates_by_loan_amount.png" if save_plots else None,
            interactive=show_plots,
        )

        print("Computing match rates by interest rate...")
        interest_rate_rates = compute_match_rates_by_interest_rate(crosswalk, fha, hmda)
        plot_match_rates_by_interest_rate(
            interest_rate_rates,
            output_path=output_dir / "match_rates_by_interest_rate.png" if save_plots else None,
            interactive=show_plots,
        )

        print("Computing match rates by loan purpose...")
        purpose_rates = compute_match_rates_by_loan_purpose(crosswalk, fha, hmda)
        plot_match_rates_by_category(
            purpose_rates,
            "Match Rates by Loan Purpose",
            "Loan Purpose",
            output_path=output_dir / "match_rates_by_loan_purpose.png" if save_plots else None,
            interactive=show_plots,
        )

        print("Computing match rates by ARM vs Fixed...")
        arm_rates = compute_match_rates_by_arm(crosswalk, fha, hmda)
        plot_match_rates_by_category(
            arm_rates,
            "Match Rates by Rate Type (ARM vs Fixed)",
            "rate_type",
            output_path=output_dir / "match_rates_by_arm.png" if save_plots else None,
            interactive=show_plots,
        )

        print("Computing match rates by submission channel...")
        channel_rates = compute_match_rates_by_submission_channel(crosswalk, hmda)
        plot_match_rates_by_category(
            channel_rates,
            "HMDA Match Rates by Submission Channel",
            "channel",
            output_path=output_dir / "match_rates_by_channel.png" if save_plots else None,
            interactive=show_plots,
        )

    return {
        "round_breakdown": round_breakdown,
        "hmda_yearly": hmda_yearly,
        "fha_yearly": fha_yearly,
        "fha_monthly": fha_monthly,
        "fha_states": fha_states,
        "hmda_states": hmda_states,
        "fha_corr": fha_corr,
        "hmda_corr": hmda_corr,
        "county_corr": county_corr,
        "round_by_year": round_by_year,
        "overall_fha_rate": overall_fha_rate,
        "overall_hmda_rate": overall_hmda_rate,
    }


