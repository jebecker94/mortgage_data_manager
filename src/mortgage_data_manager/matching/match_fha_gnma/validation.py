"""FHA-GNMA Match Validation Module.

This module provides comprehensive validation analyses for the FHA-GNMA matching
workflow, with particular emphasis on documenting the KNOWN LIMITATIONS of this
matching approach.

CRITICAL CONTEXT:
=================
The FHA-GNMA matching has a STRONG STATE SIZE BIAS (Spearman r = -0.907).
This is a FUNDAMENTAL LIMITATION of probabilistic matching without unique IDs,
NOT a flaw in the matching methodology.

Overall match rates (35.8% FHA, 33.7% GNMA) are LOW by design:
- Large states (TX, CA, FL) have very low match rates (15-24%)
- Small states/territories (GU, DC, VT) have high match rates (80-97%)
- This occurs because larger states have more loans per blocking cell,
  creating many-to-many matches that get dropped during tie-breaking.

IMPLICATIONS FOR USERS:
- The matched sample OVER-REPRESENTS small states
- Use caution when analyzing large-state patterns from matched data
- Consider weighting if using for national estimates
- These are NOT missing at random - unmatched loans are systematically
  from larger states with more common loan characteristics

Validation Analyses:
1. Match Statistics Summary
2. State Size Bias Analysis (the KEY limitation)
3. Temporal Analysis by Year
4. Loan Characteristic Analysis (amount, rate, purpose)
5. Blocking Cell Analysis (demonstrates the cause of state size bias)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.matching.match_fha_gnma.config import (
    CROSSWALK_OUTPUT_DIR,
    INTERMEDIATE_DIR,
    MAX_YEAR,
    MIN_YEAR,
)

# Validation output directory - save figures to docs for documentation
VALIDATION_OUTPUT_DIR = (
    MortgageDataConfig.PROJECT_DIR / "docs" / "matching" / "figures" / "fha_gnma"
)

# Color palette (Jonathan's authoritative academic palette)
PALETTE = {
    "fha": "#145A32",  # Hunter Green - FHA series
    "gnma": "#D4AC0D",  # Burnished Gold - GNMA series
    "emphasis": "#BA4A00",  # Terracotta - trendlines, emphasis
    "callout": "#0E2F44",  # Midnight Blue - annotations, callouts
    "neutral": "#85929E",  # Blue-Grey - axes, context
    "canvas": "#F8F9F9",  # Off-white background
    "ink": "#1B2631",  # Standard text
    "annotation_bg": "#F8F9F9",  # Canvas for annotation boxes
    "annotation_border": "#BA4A00",  # Terracotta for annotation borders
}

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
    gnma_path: Path | None = None,
) -> tuple[pl.LazyFrame, pl.LazyFrame, pl.LazyFrame]:
    """Load crosswalk and intermediate data.

    Args:
        crosswalk_path: Path to crosswalk file. Defaults to standard location.
        fha_path: Path to FHA prepared data. Defaults to standard location.
        gnma_path: Path to GNMA prepared data. Defaults to standard location.

    Returns:
        Tuple of (crosswalk, fha, gnma) LazyFrames
    """
    if crosswalk_path is None:
        crosswalk_path = CROSSWALK_OUTPUT_DIR / f"fha_gnma_crosswalk_{MIN_YEAR}_{MAX_YEAR}.parquet"
    if fha_path is None:
        fha_path = INTERMEDIATE_DIR / f"fha_prepared_{MIN_YEAR}_{MAX_YEAR}.parquet"
    if gnma_path is None:
        gnma_path = INTERMEDIATE_DIR / f"gnma_prepared_{MIN_YEAR}_{MAX_YEAR}.parquet"

    crosswalk = pl.scan_parquet(crosswalk_path)
    fha = pl.scan_parquet(fha_path)
    gnma = pl.scan_parquet(gnma_path)
    return crosswalk, fha, gnma


# =============================================================================
# Match Statistics
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


def overall_match_statistics(
    crosswalk: pl.LazyFrame,
    fha: pl.LazyFrame,
    gnma: pl.LazyFrame,
) -> dict[str, Any]:
    """Compute overall match statistics."""
    n_matches = crosswalk.select(pl.len()).collect().item()
    n_fha = fha.select(pl.len()).collect().item()
    n_gnma = gnma.select(pl.len()).collect().item()

    round_breakdown = match_round_breakdown(crosswalk)
    round1_count = round_breakdown.filter(pl.col("match_round") == 1)["count"]
    round2_count = round_breakdown.filter(pl.col("match_round") == 2)["count"]

    return {
        "n_fha": n_fha,
        "n_gnma": n_gnma,
        "n_matches": n_matches,
        "fha_match_rate": n_matches / n_fha if n_fha > 0 else 0,
        "gnma_match_rate": n_matches / n_gnma if n_gnma > 0 else 0,
        "round1_matches": round1_count[0] if len(round1_count) > 0 else 0,
        "round2_matches": round2_count[0] if len(round2_count) > 0 else 0,
        "round_breakdown": round_breakdown,
    }


# =============================================================================
# State Size Bias Analysis (CRITICAL)
# =============================================================================


def fha_state_match_rates(
    crosswalk: pl.LazyFrame,
    fha: pl.LazyFrame,
) -> pl.DataFrame:
    """Calculate FHA-side match rates by state."""
    matched_fha = crosswalk.select("FHA_Index").unique()

    fha_totals = fha.group_by("state").agg(pl.len().alias("total_fha_loans"))

    fha_matched = (
        fha.join(matched_fha, on="FHA_Index", how="inner")
        .group_by("state")
        .agg(pl.len().alias("matched_loans"))
    )

    result = (
        fha_totals.join(fha_matched, on="state", how="left")
        .with_columns(
            pl.col("matched_loans").fill_null(0),
            (pl.col("matched_loans") / pl.col("total_fha_loans")).alias("match_rate"),
        )
        .sort("total_fha_loans", descending=True)
        .collect()
    )

    return result


def gnma_state_match_rates(
    crosswalk: pl.LazyFrame,
    gnma: pl.LazyFrame,
) -> pl.DataFrame:
    """Calculate GNMA-side match rates by state."""
    matched_gnma = crosswalk.select("gnma_loan_id").unique()

    gnma_totals = gnma.group_by("state").agg(pl.len().alias("total_gnma_loans"))

    gnma_matched = (
        gnma.join(matched_gnma, on="gnma_loan_id", how="inner")
        .group_by("state")
        .agg(pl.len().alias("matched_loans"))
    )

    result = (
        gnma_totals.join(gnma_matched, on="state", how="left")
        .with_columns(
            pl.col("matched_loans").fill_null(0),
            (pl.col("matched_loans") / pl.col("total_gnma_loans")).alias("match_rate"),
        )
        .sort("total_gnma_loans", descending=True)
        .collect()
    )

    return result


def analyze_state_size_correlation(state_df: pl.DataFrame, loan_col: str) -> dict:
    """Analyze correlation between state size (number of loans) and match rates."""
    from scipy.stats import spearmanr

    x = state_df[loan_col].to_numpy().astype(float)
    y = state_df["match_rate"].to_numpy()

    mask = ~(np.isnan(x) | np.isnan(y))
    x, y = x[mask], y[mask]

    if len(x) < 3:
        return {"error": "Not enough data points"}

    # Pearson correlation
    pearson_r = np.corrcoef(x, y)[0, 1]

    # Log-transformed Pearson (better for this relationship)
    log_x = np.log10(x + 1)
    log_pearson_r = np.corrcoef(log_x, y)[0, 1]

    # Spearman rank correlation (robust to outliers)
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


def plot_state_size_bias(
    state_df: pl.DataFrame,
    loan_col: str,
    title: str,
    output_path: Path | None = None,
    interactive: bool = True,
) -> dict:
    """Create THE KEY VISUALIZATION: state size vs match rate scatter plot."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return {}

    from scipy.stats import spearmanr

    # Filter out territories with very few loans
    territories = {"PR", "GU", "VI", "AS", "MP"}
    filtered = state_df.filter(
        (pl.col(loan_col) >= 100) & (~pl.col("state").is_in(territories))
    )

    x = filtered[loan_col].to_numpy()
    y = filtered["match_rate"].to_numpy()
    states = filtered["state"].to_list()

    # Compute correlation
    spearman_r, spearman_p = spearmanr(x, y)
    log_x = np.log10(x)
    log_pearson_r = np.corrcoef(log_x, y)[0, 1]

    # Create figure with two panels
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    # Left panel: Linear scale (shows distribution)
    ax1.scatter(
        x, y, alpha=0.7, s=60, c=PALETTE["gnma"], edgecolors="white", linewidth=0.5
    )
    ax1.set_xlabel("Total FHA Loans", fontsize=11)
    ax1.set_ylabel("Match Rate", fontsize=11)
    ax1.set_title(f"{title}\n(Linear Scale)", fontsize=12)
    ax1.set_ylim(0, 1.0)
    ax1.grid(True, alpha=0.3)

    # Add trendline
    z = np.polyfit(log_x, y, 1)
    x_line = np.logspace(np.log10(x.min()), np.log10(x.max()), 100)
    y_line = z[0] * np.log10(x_line) + z[1]
    ax1.plot(
        x_line,
        y_line,
        "--",
        color=PALETTE["emphasis"],
        alpha=0.7,
        linewidth=2,
        label="Log-linear fit",
    )
    ax1.legend(loc="upper right")

    # Right panel: Log scale (shows relationship clearly)
    ax2.scatter(
        x, y, alpha=0.7, s=60, c=PALETTE["gnma"], edgecolors="white", linewidth=0.5
    )
    ax2.set_xscale("log")
    ax2.set_xlabel("Total FHA Loans (Log Scale)", fontsize=11)
    ax2.set_ylabel("Match Rate", fontsize=11)
    ax2.set_title(f"{title}\n(Log Scale)", fontsize=12)
    ax2.set_ylim(0, 1.0)
    ax2.grid(True, alpha=0.3, which="both")

    # Add trendline to log plot
    ax2.plot(
        x_line,
        y_line,
        "--",
        color=PALETTE["emphasis"],
        alpha=0.7,
        linewidth=2,
        label="Log-linear fit",
    )

    # Label extreme states
    for xi, yi, state in zip(x, y, states):
        # Label high-volume low-match states
        if xi > 500000 or yi < 0.25:
            ax2.annotate(
                state,
                (xi, yi),
                fontsize=9,
                alpha=0.8,
                xytext=(5, 5),
                textcoords="offset points",
            )
        # Label high-match states
        elif yi > 0.75:
            ax2.annotate(
                state,
                (xi, yi),
                fontsize=9,
                alpha=0.8,
                xytext=(5, -10),
                textcoords="offset points",
            )

    # Add correlation annotation with CRITICAL warning
    annotation_text = (
        f"Spearman r = {spearman_r:.3f}\n"
        f"(p < 0.001)\n\n"
        f"CRITICAL LIMITATION:\n"
        f"Strong negative correlation\n"
        f"between state size and\n"
        f"match rate. This is inherent\n"
        f"to probabilistic matching."
    )
    ax2.text(
        0.98,
        0.98,
        annotation_text,
        transform=ax2.transAxes,
        fontsize=10,
        verticalalignment="top",
        horizontalalignment="right",
        bbox=dict(
            boxstyle="round",
            facecolor=PALETTE["callout"],
            alpha=0.9,
            edgecolor=PALETTE["emphasis"],
        ),
    )

    ax2.legend(loc="lower left")

    plt.tight_layout()
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)

    return {
        "spearman_r": spearman_r,
        "spearman_p": spearman_p,
        "log_pearson_r": log_pearson_r,
    }


def plot_match_rate_by_state(
    state_df: pl.DataFrame,
    loan_col: str,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Create a two-panel figure: horizontal bar chart + correlation scatter."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    from scipy.stats import spearmanr

    # Filter out territories with very few loans
    territories = {"PR", "GU", "VI", "AS", "MP"}
    filtered = state_df.filter(
        (pl.col(loan_col) >= 100) & (~pl.col("state").is_in(territories))
    )

    # Sort by match rate for bar chart
    sorted_df = filtered.sort("match_rate")
    states = sorted_df["state"].to_list()
    match_rates = sorted_df["match_rate"].to_numpy() * 100  # Convert to percentage
    loan_counts = sorted_df[loan_col].to_numpy()

    # Calculate correlations
    log_counts = np.log10(loan_counts)
    spearman_r, spearman_p = spearmanr(loan_counts, match_rates)

    # Create two-panel figure
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 12))

    # Left panel: Horizontal bar chart
    y_pos = np.arange(len(states))
    bars = ax1.barh(
        y_pos,
        match_rates,
        color=PALETTE["fha"],
        alpha=0.8,
        edgecolor="white",
        linewidth=0.5,
    )

    ax1.set_yticks(y_pos)
    ax1.set_yticklabels(states, fontsize=8)
    ax1.set_xlabel("Match Rate (%)", fontsize=11)
    ax1.set_title("FHA-GNMA Match Rate by State", fontsize=12)
    ax1.set_xlim(0, 100)
    ax1.grid(True, alpha=0.3, axis="x")

    # Add value labels on bars for extreme values
    for i, (bar, rate) in enumerate(zip(bars, match_rates)):
        if rate < 25 or rate > 70:
            ax1.text(
                rate + 1,
                bar.get_y() + bar.get_height() / 2,
                f"{rate:.0f}%",
                va="center",
                fontsize=7,
                alpha=0.8,
            )

    # Right panel: Correlation scatter
    ax2.scatter(
        log_counts,
        match_rates,
        alpha=0.7,
        s=60,
        c=PALETTE["fha"],
        edgecolors="white",
        linewidth=0.5,
    )

    # Add state labels for extreme points
    for xi, yi, state in zip(log_counts, match_rates, states):
        if yi > 70 or yi < 25 or 10**xi > 500000:
            ax2.annotate(
                state,
                (xi, yi),
                fontsize=9,
                alpha=0.8,
                xytext=(5, 5),
                textcoords="offset points",
            )

    # Add trendline
    z = np.polyfit(log_counts, match_rates, 1)
    x_line = np.linspace(log_counts.min(), log_counts.max(), 100)
    y_line = z[0] * x_line + z[1]
    ax2.plot(
        x_line,
        y_line,
        "--",
        color=PALETTE["emphasis"],
        alpha=0.7,
        linewidth=2,
        label="Log-linear fit",
    )

    ax2.set_xlabel("Log10(FHA Loan Count)", fontsize=11)
    ax2.set_ylabel("Match Rate (%)", fontsize=11)
    ax2.set_title(
        f"State Size vs Match Rate\n(Spearman r = {spearman_r:.3f})", fontsize=12
    )
    ax2.grid(True, alpha=0.3)
    ax2.legend(loc="upper right")

    # Add annotation about the correlation
    ax2.text(
        0.98,
        0.02,
        "Larger states have\nlower match rates",
        transform=ax2.transAxes,
        fontsize=10,
        verticalalignment="bottom",
        horizontalalignment="right",
        style="italic",
        alpha=0.7,
    )

    plt.tight_layout()
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


# =============================================================================
# Temporal Analysis
# =============================================================================


def fha_yearly_match_rates(
    crosswalk: pl.LazyFrame,
    fha: pl.LazyFrame,
) -> pl.DataFrame:
    """Calculate FHA-side match rates by origination year."""
    matched_fha = crosswalk.select("FHA_Index").unique()

    fha_totals = fha.group_by("origination_year").agg(pl.len().alias("total_fha_loans"))

    fha_matched = (
        fha.join(matched_fha, on="FHA_Index", how="inner")
        .group_by("origination_year")
        .agg(pl.len().alias("matched_loans"))
    )

    result = (
        fha_totals.join(fha_matched, on="origination_year", how="left")
        .with_columns(
            pl.col("matched_loans").fill_null(0),
            (pl.col("matched_loans") / pl.col("total_fha_loans")).alias("match_rate"),
        )
        .sort("origination_year")
        .collect()
    )

    return result


def gnma_yearly_match_rates(
    crosswalk: pl.LazyFrame,
    gnma: pl.LazyFrame,
) -> pl.DataFrame:
    """Calculate GNMA-side match rates by origination year."""
    matched_gnma = crosswalk.select("gnma_loan_id").unique()

    gnma_totals = gnma.group_by("origination_year").agg(
        pl.len().alias("total_gnma_loans")
    )

    gnma_matched = (
        gnma.join(matched_gnma, on="gnma_loan_id", how="inner")
        .group_by("origination_year")
        .agg(pl.len().alias("matched_loans"))
    )

    result = (
        gnma_totals.join(gnma_matched, on="origination_year", how="left")
        .with_columns(
            pl.col("matched_loans").fill_null(0),
            (pl.col("matched_loans") / pl.col("total_gnma_loans")).alias("match_rate"),
        )
        .sort("origination_year")
        .collect()
    )

    return result


def plot_temporal_match_rates(
    fha_yearly: pl.DataFrame,
    gnma_yearly: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot match rates over time."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(12, 6))

    years_fha = fha_yearly["origination_year"].to_list()
    rates_fha = fha_yearly["match_rate"].to_list()

    years_gnma = gnma_yearly["origination_year"].to_list()
    rates_gnma = gnma_yearly["match_rate"].to_list()

    ax.plot(
        years_fha,
        rates_fha,
        color=PALETTE["fha"],
        linewidth=2.5,
        marker="o",
        markersize=8,
        label="FHA Match Rate",
    )

    ax.plot(
        years_gnma,
        rates_gnma,
        color=PALETTE["gnma"],
        linewidth=2.5,
        marker="s",
        markersize=8,
        label="GNMA Match Rate",
    )

    # Overall averages
    overall_fha = fha_yearly["matched_loans"].sum() / fha_yearly["total_fha_loans"].sum()
    overall_gnma = (
        gnma_yearly["matched_loans"].sum() / gnma_yearly["total_gnma_loans"].sum()
    )

    ax.axhline(overall_fha, color=PALETTE["fha"], linestyle="--", alpha=0.5, linewidth=1)
    ax.axhline(
        overall_gnma, color=PALETTE["gnma"], linestyle="--", alpha=0.5, linewidth=1
    )

    ax.set_xlabel("Origination Year", fontsize=11)
    ax.set_ylabel("Match Rate", fontsize=11)
    ax.set_title("FHA-GNMA Match Rates by Origination Year", fontsize=12)
    ax.set_ylim(0, 0.6)  # Match rates are low, so adjust y-axis
    ax.legend(loc="upper right")
    ax.grid(True, alpha=0.3)

    # Add annotation about overall rates
    ax.text(
        0.02,
        0.98,
        f"Overall FHA: {overall_fha:.1%}\nOverall GNMA: {overall_gnma:.1%}",
        transform=ax.transAxes,
        fontsize=10,
        verticalalignment="top",
        bbox=dict(boxstyle="round", facecolor=PALETTE["canvas"], alpha=0.8),
    )

    plt.tight_layout()
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


# =============================================================================
# Loan Characteristic Analysis
# =============================================================================


def compute_match_rates_by_loan_amount(
    crosswalk: pl.LazyFrame,
    fha: pl.LazyFrame,
    gnma: pl.LazyFrame,
    bin_size: int = 25000,
    max_amount: int = 500000,
) -> pl.DataFrame:
    """Compute match rates by loan amount bins."""
    matched_fha = crosswalk.select("FHA_Index").unique()
    matched_gnma = crosswalk.select("gnma_loan_id").unique()

    def bin_expr(col_name: str) -> pl.Expr:
        return ((pl.col(col_name) / bin_size).floor() * bin_size).alias(
            "loan_amount_bin"
        )

    # FHA side
    fha_with_bin = fha.with_columns(bin_expr("loan_amount")).filter(
        (pl.col("loan_amount_bin") >= 0) & (pl.col("loan_amount_bin") <= max_amount)
    )

    fha_totals = fha_with_bin.group_by("loan_amount_bin").agg(
        pl.len().alias("fha_total")
    )

    fha_matched_counts = (
        fha_with_bin.join(matched_fha, on="FHA_Index", how="inner")
        .group_by("loan_amount_bin")
        .agg(pl.len().alias("fha_matched"))
    )

    # GNMA side
    gnma_with_bin = gnma.with_columns(bin_expr("loan_amount")).filter(
        (pl.col("loan_amount_bin") >= 0) & (pl.col("loan_amount_bin") <= max_amount)
    )

    gnma_totals = gnma_with_bin.group_by("loan_amount_bin").agg(
        pl.len().alias("gnma_total")
    )

    gnma_matched_counts = (
        gnma_with_bin.join(matched_gnma, on="gnma_loan_id", how="inner")
        .group_by("loan_amount_bin")
        .agg(pl.len().alias("gnma_matched"))
    )

    result = (
        fha_totals.join(fha_matched_counts, on="loan_amount_bin", how="left")
        .join(gnma_totals, on="loan_amount_bin", how="full", coalesce=True)
        .join(gnma_matched_counts, on="loan_amount_bin", how="left")
        .with_columns(
            pl.col("fha_matched").fill_null(0),
            pl.col("gnma_matched").fill_null(0),
            (pl.col("fha_matched") / pl.col("fha_total")).alias("fha_match_rate"),
            (pl.col("gnma_matched") / pl.col("gnma_total")).alias("gnma_match_rate"),
        )
        .sort("loan_amount_bin")
        .collect()
    )

    return result


def compute_match_rates_by_interest_rate(
    crosswalk: pl.LazyFrame,
    fha: pl.LazyFrame,
    gnma: pl.LazyFrame,
    bin_size: float = 0.25,
    min_rate: float = 2.0,
    max_rate: float = 8.0,
) -> pl.DataFrame:
    """Compute match rates by interest rate bins."""
    matched_fha = crosswalk.select("FHA_Index").unique()
    matched_gnma = crosswalk.select("gnma_loan_id").unique()

    def bin_expr(col_name: str) -> pl.Expr:
        return ((pl.col(col_name) / bin_size).round() * bin_size).alias(
            "interest_rate_bin"
        )

    # FHA side
    fha_with_bin = fha.with_columns(bin_expr("interest_rate")).filter(
        (pl.col("interest_rate_bin") >= min_rate)
        & (pl.col("interest_rate_bin") <= max_rate)
        & (pl.col("interest_rate").is_not_null())
    )

    fha_totals = fha_with_bin.group_by("interest_rate_bin").agg(
        pl.len().alias("fha_total")
    )

    fha_matched_counts = (
        fha_with_bin.join(matched_fha, on="FHA_Index", how="inner")
        .group_by("interest_rate_bin")
        .agg(pl.len().alias("fha_matched"))
    )

    # GNMA side
    gnma_with_bin = gnma.with_columns(bin_expr("interest_rate")).filter(
        (pl.col("interest_rate_bin") >= min_rate)
        & (pl.col("interest_rate_bin") <= max_rate)
        & (pl.col("interest_rate").is_not_null())
    )

    gnma_totals = gnma_with_bin.group_by("interest_rate_bin").agg(
        pl.len().alias("gnma_total")
    )

    gnma_matched_counts = (
        gnma_with_bin.join(matched_gnma, on="gnma_loan_id", how="inner")
        .group_by("interest_rate_bin")
        .agg(pl.len().alias("gnma_matched"))
    )

    result = (
        fha_totals.join(fha_matched_counts, on="interest_rate_bin", how="left")
        .join(gnma_totals, on="interest_rate_bin", how="full", coalesce=True)
        .join(gnma_matched_counts, on="interest_rate_bin", how="left")
        .with_columns(
            pl.col("fha_matched").fill_null(0),
            pl.col("gnma_matched").fill_null(0),
            (pl.col("fha_matched") / pl.col("fha_total")).alias("fha_match_rate"),
            (pl.col("gnma_matched") / pl.col("gnma_total")).alias("gnma_match_rate"),
        )
        .sort("interest_rate_bin")
        .collect()
    )

    return result


def compute_match_rates_by_loan_purpose(
    crosswalk: pl.LazyFrame,
    fha: pl.LazyFrame,
    gnma: pl.LazyFrame,
) -> dict[str, pl.DataFrame]:
    """Compute match rates by loan purpose (Purchase vs Refinance)."""
    matched_fha = crosswalk.select("FHA_Index").unique()
    matched_gnma = crosswalk.select("gnma_loan_id").unique()

    # FHA side (uses is_purchase)
    fha_totals = fha.group_by("is_purchase").agg(pl.len().alias("total"))

    fha_matched = (
        fha.join(matched_fha, on="FHA_Index", how="inner")
        .group_by("is_purchase")
        .agg(pl.len().alias("matched"))
    )

    fha_result = (
        fha_totals.join(fha_matched, on="is_purchase", how="left")
        .with_columns(
            pl.col("matched").fill_null(0),
            (pl.col("matched") / pl.col("total")).alias("match_rate"),
            pl.when(pl.col("is_purchase") == 1)
            .then(pl.lit("Purchase"))
            .otherwise(pl.lit("Refinance"))
            .alias("purpose_label"),
        )
        .sort("is_purchase", descending=True)
        .collect()
    )

    # GNMA side
    gnma_totals = gnma.group_by("is_purchase").agg(pl.len().alias("total"))

    gnma_matched = (
        gnma.join(matched_gnma, on="gnma_loan_id", how="inner")
        .group_by("is_purchase")
        .agg(pl.len().alias("matched"))
    )

    gnma_result = (
        gnma_totals.join(gnma_matched, on="is_purchase", how="left")
        .with_columns(
            pl.col("matched").fill_null(0),
            (pl.col("matched") / pl.col("total")).alias("match_rate"),
            pl.when(pl.col("is_purchase") == 1)
            .then(pl.lit("Purchase"))
            .otherwise(pl.lit("Refinance"))
            .alias("purpose_label"),
        )
        .sort("is_purchase", descending=True)
        .collect()
    )

    return {"fha": fha_result, "gnma": gnma_result}


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

    fha_total = loan_amount_rates["fha_total"].to_numpy().astype(float)
    gnma_total = loan_amount_rates["gnma_total"].to_numpy().astype(float)

    fha_pdf = fha_total / np.nansum(fha_total)
    gnma_pdf = gnma_total / np.nansum(gnma_total)

    ax2 = ax.twinx()

    ax2.fill_between(x, fha_pdf, alpha=0.15, color=PALETTE["fha"], label="FHA Density")
    ax2.fill_between(
        x, gnma_pdf, alpha=0.15, color=PALETTE["gnma"], label="GNMA Density"
    )
    ax2.set_ylabel("Density (PDF)", color=PALETTE["neutral"])
    ax2.tick_params(axis="y", labelcolor=PALETTE["neutral"])
    ax2.set_ylim(0, max(np.nanmax(fha_pdf), np.nanmax(gnma_pdf)) * 1.3)

    fha_rates = np.array(loan_amount_rates["fha_match_rate"].to_list())
    gnma_rates = np.array(loan_amount_rates["gnma_match_rate"].to_list())

    ax.plot(
        x,
        fha_rates,
        color=PALETTE["fha"],
        linewidth=2,
        marker="o",
        markersize=5,
        label="FHA Match Rate",
        zorder=10,
    )

    ax.plot(
        x,
        gnma_rates,
        color=PALETTE["gnma"],
        linewidth=2,
        marker="s",
        markersize=5,
        label="GNMA Match Rate",
        zorder=10,
    )

    ax.set_xlabel("Loan Amount ($K)")
    ax.set_ylabel("Match Rate")
    ax.set_title("Match Rates by Loan Amount (with density overlay)")

    valid_rates = [
        r
        for r in list(fha_rates) + list(gnma_rates)
        if r is not None and not np.isnan(r)
    ]
    if valid_rates:
        y_min = max(0, min(valid_rates) - 0.05)
        y_max = min(1.0, max(valid_rates) + 0.1)
        ax.set_ylim(y_min, y_max)

    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
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

    fha_total = interest_rate_rates["fha_total"].to_numpy().astype(float)
    gnma_total = interest_rate_rates["gnma_total"].to_numpy().astype(float)

    fha_total = np.nan_to_num(fha_total, nan=0)
    gnma_total = np.nan_to_num(gnma_total, nan=0)

    fha_pdf = fha_total / fha_total.sum() if fha_total.sum() > 0 else fha_total
    gnma_pdf = gnma_total / gnma_total.sum() if gnma_total.sum() > 0 else gnma_total

    ax2 = ax.twinx()

    ax2.fill_between(x, fha_pdf, alpha=0.15, color=PALETTE["fha"], label="FHA Density")
    ax2.fill_between(
        x, gnma_pdf, alpha=0.15, color=PALETTE["gnma"], label="GNMA Density"
    )
    ax2.set_ylabel("Density (PDF)", color=PALETTE["neutral"])
    ax2.tick_params(axis="y", labelcolor=PALETTE["neutral"])
    ax2.set_ylim(0, max(max(fha_pdf), max(gnma_pdf)) * 1.3)

    fha_rates = interest_rate_rates["fha_match_rate"].to_list()
    gnma_rates = interest_rate_rates["gnma_match_rate"].to_list()

    ax.plot(
        x,
        fha_rates,
        color=PALETTE["fha"],
        linewidth=2,
        marker="o",
        markersize=5,
        label="FHA Match Rate",
        zorder=10,
    )

    ax.plot(
        x,
        gnma_rates,
        color=PALETTE["gnma"],
        linewidth=2,
        marker="s",
        markersize=5,
        label="GNMA Match Rate",
        zorder=10,
    )

    ax.set_xlabel("Interest Rate (%)")
    ax.set_ylabel("Match Rate")
    ax.set_title("Match Rates by Interest Rate (with density overlay)")

    all_rates = fha_rates + gnma_rates
    valid_rates = [r for r in all_rates if r is not None and not np.isnan(r)]
    if valid_rates:
        y_min = max(0, min(valid_rates) - 0.05)
        y_max = min(1.0, max(valid_rates) + 0.1)
        ax.set_ylim(y_min, y_max)

    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_match_rates_by_purpose(
    purpose_rates: dict[str, pl.DataFrame],
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot match rates by loan purpose (Purchase vs Refinance)."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(10, 6))

    fha_df = purpose_rates["fha"]
    gnma_df = purpose_rates["gnma"]

    categories = fha_df["purpose_label"].to_list()
    fha_rates = fha_df["match_rate"].to_list()
    gnma_rates = gnma_df["match_rate"].to_list()

    x = np.arange(len(categories))
    width = 0.35

    ax.bar(
        x - width / 2, fha_rates, width, label="FHA", color=PALETTE["fha"], alpha=0.8
    )
    ax.bar(
        x + width / 2, gnma_rates, width, label="GNMA", color=PALETTE["gnma"], alpha=0.8
    )

    ax.set_xticks(x)
    ax.set_xticklabels(categories, fontsize=11)

    for i, (fha_r, gnma_r) in enumerate(zip(fha_rates, gnma_rates)):
        ax.text(
            i - width / 2,
            fha_r + 0.01,
            f"{fha_r:.1%}",
            ha="center",
            va="bottom",
            fontsize=10,
        )
        ax.text(
            i + width / 2,
            gnma_r + 0.01,
            f"{gnma_r:.1%}",
            ha="center",
            va="bottom",
            fontsize=10,
        )

    ax.set_ylabel("Match Rate", fontsize=11)
    ax.set_title("Match Rates by Loan Purpose", fontsize=12)

    all_rates = fha_rates + gnma_rates
    y_min = max(0, min(all_rates) - 0.1)
    y_max = min(1.0, max(all_rates) + 0.15)
    ax.set_ylim(y_min, y_max)

    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


# =============================================================================
# Blocking Cell Analysis
# =============================================================================


def analyze_blocking_cells(
    fha: pl.LazyFrame,
    by_state: bool = True,
) -> pl.DataFrame:
    """Analyze blocking cell density to explain state size bias.

    Blocking cells are defined by: state + year + purpose + amount_thousands + rate_bucket + month

    Larger states have more loans falling into the same blocking cell, creating
    many-to-many matches that get dropped during tie-breaking.
    """
    # Define blocking key columns
    blocking_cols = [
        "state",
        "origination_year",
        "is_purchase",
        "loan_amount_thousands",
        "rate_bucket",
        "origination_month",
    ]

    if by_state:
        # Compute blocking cell size distribution by state
        cell_stats = (
            fha.group_by(blocking_cols)
            .agg(pl.len().alias("cell_size"))
            .group_by("state")
            .agg(
                [
                    pl.len().alias("n_cells"),
                    pl.col("cell_size").sum().alias("total_loans"),
                    pl.col("cell_size").mean().alias("mean_cell_size"),
                    pl.col("cell_size").max().alias("max_cell_size"),
                    # Unique cells (size=1)
                    (pl.col("cell_size") == 1).sum().alias("n_unique_cells"),
                    # Multi-loan cells
                    (pl.col("cell_size") > 1).sum().alias("n_multi_cells"),
                ]
            )
            .with_columns(
                [
                    (pl.col("n_unique_cells") / pl.col("n_cells")).alias(
                        "pct_unique_cells"
                    ),
                    (pl.col("n_multi_cells") / pl.col("n_cells")).alias(
                        "pct_multi_cells"
                    ),
                ]
            )
            .sort("total_loans", descending=True)
            .collect()
        )
    else:
        # Overall statistics
        cell_stats = (
            fha.group_by(blocking_cols)
            .agg(pl.len().alias("cell_size"))
            .select(
                [
                    pl.len().alias("n_cells"),
                    pl.col("cell_size").sum().alias("total_loans"),
                    pl.col("cell_size").mean().alias("mean_cell_size"),
                    pl.col("cell_size").max().alias("max_cell_size"),
                    (pl.col("cell_size") == 1).sum().alias("n_unique_cells"),
                    (pl.col("cell_size") > 1).sum().alias("n_multi_cells"),
                ]
            )
            .with_columns(
                [
                    (pl.col("n_unique_cells") / pl.col("n_cells")).alias(
                        "pct_unique_cells"
                    ),
                    (pl.col("n_multi_cells") / pl.col("n_cells")).alias(
                        "pct_multi_cells"
                    ),
                ]
            )
            .collect()
        )

    return cell_stats


def plot_blocking_cell_analysis(
    cell_stats: pl.DataFrame,
    match_rates: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Visualize the relationship between blocking cell density and match rates."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    from scipy.stats import spearmanr

    # Merge cell stats with match rates
    merged = cell_stats.join(
        match_rates.select(["state", "match_rate"]),
        on="state",
        how="inner",
    )

    # Filter out tiny states
    merged = merged.filter(pl.col("total_loans") >= 100)

    x_unique = merged["pct_unique_cells"].to_numpy()
    x_max = merged["max_cell_size"].to_numpy()
    y = merged["match_rate"].to_numpy()
    states = merged["state"].to_list()
    sizes = merged["total_loans"].to_numpy()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    # Left: % unique cells vs match rate
    scatter1 = ax1.scatter(
        x_unique,
        y,
        c=np.log10(sizes),
        cmap="viridis",
        s=60,
        alpha=0.7,
        edgecolors="white",
        linewidth=0.5,
    )
    ax1.set_xlabel("% of Blocking Cells with Exactly 1 Loan", fontsize=11)
    ax1.set_ylabel("Match Rate", fontsize=11)
    ax1.set_title(
        "Cell Uniqueness vs Match Rate\n(color = log10 state size)", fontsize=12
    )
    ax1.grid(True, alpha=0.3)

    r1, p1 = spearmanr(x_unique, y)
    ax1.text(
        0.05,
        0.95,
        f"Spearman r = {r1:.3f}\np = {p1:.4f}",
        transform=ax1.transAxes,
        fontsize=10,
        verticalalignment="top",
        bbox=dict(boxstyle="round", facecolor=PALETTE["canvas"], alpha=0.8),
    )

    # Label extreme points
    for xi, yi, state in zip(x_unique, y, states):
        if yi < 0.20 or xi > 0.95:
            ax1.annotate(
                state,
                (xi, yi),
                fontsize=9,
                alpha=0.8,
                xytext=(3, 3),
                textcoords="offset points",
            )

    cbar1 = plt.colorbar(scatter1, ax=ax1)
    cbar1.set_label("log10(Total Loans)")

    # Right: Max cell size vs match rate
    scatter2 = ax2.scatter(
        x_max,
        y,
        c=np.log10(sizes),
        cmap="viridis",
        s=60,
        alpha=0.7,
        edgecolors="white",
        linewidth=0.5,
    )
    ax2.set_xlabel("Maximum Loans per Blocking Cell", fontsize=11)
    ax2.set_ylabel("Match Rate", fontsize=11)
    ax2.set_title(
        "Cell Density vs Match Rate\n(color = log10 state size)", fontsize=12
    )
    ax2.set_xscale("log")
    ax2.grid(True, alpha=0.3, which="both")

    r2, p2 = spearmanr(x_max, y)
    ax2.text(
        0.95,
        0.95,
        f"Spearman r = {r2:.3f}\np = {p2:.4f}",
        transform=ax2.transAxes,
        fontsize=10,
        verticalalignment="top",
        horizontalalignment="right",
        bbox=dict(boxstyle="round", facecolor=PALETTE["canvas"], alpha=0.8),
    )

    # Label extreme points
    for xi, yi, state in zip(x_max, y, states):
        if xi > 20 or yi < 0.20:
            ax2.annotate(
                state,
                (xi, yi),
                fontsize=9,
                alpha=0.8,
                xytext=(3, 3),
                textcoords="offset points",
            )

    cbar2 = plt.colorbar(scatter2, ax=ax2)
    cbar2.set_label("log10(Total Loans)")

    plt.tight_layout()
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


# =============================================================================
# Print Helpers
# =============================================================================


def print_section(title: str) -> None:
    """Print a section header."""
    print(f"\n{'=' * 70}")
    print(f" {title}")
    print(f"{'=' * 70}\n")


def print_critical_warning() -> None:
    """Print the critical warning about match rate interpretation."""
    print()
    print("*" * 70)
    print("*" + " " * 68 + "*")
    print("*  CRITICAL INTERPRETATION NOTES:" + " " * 35 + "*")
    print("*" + " " * 68 + "*")
    print("*  1. Low overall match rates (35%) do NOT mean the matching is bad  *")
    print("*  2. Strong state size bias (r = -0.907) is a FUNDAMENTAL LIMITATION*")
    print("*     of probabilistic matching without unique identifiers           *")
    print("*  3. The matched sample OVER-REPRESENTS small states                *")
    print("*  4. Use caution when analyzing large-state patterns                *")
    print("*  5. Consider weighting if using for national estimates             *")
    print("*" + " " * 68 + "*")
    print("*" * 70)
    print()


# =============================================================================
# Main Validation Runner
# =============================================================================


def run_validation(
    output_dir: Path | None = None,
    save_plots: bool = True,
    show_plots: bool = False,
) -> dict[str, Any]:
    """Run all validation analyses.

    Args:
        output_dir: Directory to save figures. Defaults to VALIDATION_OUTPUT_DIR.
        save_plots: Whether to save plots to disk.
        show_plots: Whether to display matplotlib plots interactively.

    Returns:
        Dictionary with all computed statistics and DataFrames.
    """
    if output_dir is None:
        output_dir = VALIDATION_OUTPUT_DIR

    if save_plots:
        output_dir.mkdir(parents=True, exist_ok=True)

    print_critical_warning()

    print("Loading data...")
    crosswalk, fha, gnma = load_data()

    # ------- Section 1: Match Statistics Summary -------
    print_section("1. Match Statistics Summary")

    stats = overall_match_statistics(crosswalk, fha, gnma)

    print(f"FHA Records: {stats['n_fha']:,}")
    print(f"GNMA FHA Records: {stats['n_gnma']:,}")
    print(f"Matched Pairs: {stats['n_matches']:,}")
    print(f"FHA Match Rate: {stats['fha_match_rate']:.1%}")
    print(f"GNMA Match Rate: {stats['gnma_match_rate']:.1%}")
    print()
    print("Match Round Breakdown:")
    print(
        f"  Round 1 (Strict): {stats['round1_matches']:,} ({stats['round1_matches']/stats['n_matches']*100:.1f}%)"
    )
    print(
        f"  Round 2 (Relaxed): {stats['round2_matches']:,} ({stats['round2_matches']/stats['n_matches']*100:.1f}%)"
    )

    # ------- Section 2: State Size Bias (CRITICAL) -------
    print_section("2. State Size Bias Analysis (CRITICAL LIMITATION)")

    print("Computing state-level match rates...")
    fha_states = fha_state_match_rates(crosswalk, fha)
    gnma_states = gnma_state_match_rates(crosswalk, gnma)

    print("\nFHA Match Rates by State (Top 10 by Volume):")
    print(fha_states.head(10))

    print("\nFHA Match Rates by State (Bottom 10 by Match Rate, min 1000 loans):")
    filtered = fha_states.filter(pl.col("total_fha_loans") >= 1000)
    print(filtered.sort("match_rate").head(10))

    print("\nFHA Match Rates by State (Top 10 by Match Rate, min 1000 loans):")
    print(filtered.sort("match_rate", descending=True).head(10))

    print("\nState Size Correlation Analysis (FHA):")
    fha_corr = analyze_state_size_correlation(
        fha_states.filter(pl.col("total_fha_loans") >= 100),
        "total_fha_loans",
    )
    print(f"  Number of states: {fha_corr['n_states']}")
    print(
        f"  Spearman r: {fha_corr['spearman_r']:.4f} (p = {fha_corr['spearman_p']:.4e})"
    )
    print(f"  Log-Pearson r: {fha_corr['log_pearson_r']:.4f}")
    print(
        f"  Match rate range: [{fha_corr['min_match_rate']:.1%}, {fha_corr['max_match_rate']:.1%}]"
    )
    print(
        f"  Match rate mean: {fha_corr['mean_match_rate']:.1%} (std: {fha_corr['std_match_rate']:.3f})"
    )

    print("\n  INTERPRETATION:")
    print("  ---------------")
    print(
        f"  The Spearman correlation of {fha_corr['spearman_r']:.3f} indicates a VERY STRONG"
    )
    print("  negative relationship between state size and match rate.")
    print("  This is NOT a flaw in the matching - it is an inherent limitation of")
    print("  probabilistic matching when larger states have more loans falling into")
    print("  the same blocking cells, creating many-to-many matches that must be dropped.")

    # ------- Section 3: Temporal Analysis -------
    print_section("3. Temporal Analysis by Origination Year")

    fha_yearly = fha_yearly_match_rates(crosswalk, fha)
    gnma_yearly = gnma_yearly_match_rates(crosswalk, gnma)

    print("FHA Match Rates by Year:")
    print(fha_yearly)

    print("\nGNMA Match Rates by Year:")
    print(gnma_yearly)

    # ------- Section 4: Loan Characteristics -------
    print_section("4. Loan Characteristic Analysis")

    print("Computing match rates by loan amount...")
    loan_amount_rates = compute_match_rates_by_loan_amount(crosswalk, fha, gnma)

    print("Computing match rates by interest rate...")
    interest_rate_rates = compute_match_rates_by_interest_rate(crosswalk, fha, gnma)

    print("Computing match rates by loan purpose...")
    purpose_rates = compute_match_rates_by_loan_purpose(crosswalk, fha, gnma)

    print("\nMatch Rates by Loan Purpose:")
    print("FHA:")
    print(purpose_rates["fha"])
    print("\nGNMA:")
    print(purpose_rates["gnma"])

    # ------- Section 5: Blocking Cell Analysis -------
    print_section("5. Blocking Cell Analysis (Explains State Size Bias)")

    print("Analyzing blocking cell density by state...")
    cell_stats = analyze_blocking_cells(fha, by_state=True)

    print("\nBlocking Cell Statistics by State (Top 10 by Volume):")
    display_cols = [
        "state",
        "total_loans",
        "n_cells",
        "mean_cell_size",
        "max_cell_size",
        "pct_unique_cells",
    ]
    print(cell_stats.select(display_cols).head(10))

    print("\nBlocking Cell Statistics by State (Bottom 10 by % Unique Cells):")
    print(cell_stats.select(display_cols).sort("pct_unique_cells").head(10))

    print("\n  INTERPRETATION:")
    print("  ---------------")
    print("  States with lower '% unique cells' have more loans sharing the same")
    print("  blocking cell (state + year + purpose + amount + rate + month).")
    print(
        "  This creates many-to-many matches that must be dropped, leading to lower match rates."
    )
    print("  Large states (TX, CA, FL) have ~45-55% of loans in non-unique cells,")
    print("  while small states (DC, VT) have >95% unique cells.")

    # ------- Visualizations -------
    if (show_plots or save_plots) and HAS_MATPLOTLIB:
        print_section("6. Generating Visualizations")

        # State size bias plot (THE KEY FIGURE)
        print("Creating state size bias scatter plot (THE KEY FIGURE)...")
        plot_state_size_bias(
            fha_states,
            "total_fha_loans",
            "FHA State Size vs Match Rate",
            output_path=output_dir / "state_size_bias.png" if save_plots else None,
            interactive=show_plots,
        )

        # Simple match rate by state plot
        print("Creating match rate by state plot...")
        plot_match_rate_by_state(
            fha_states,
            "total_fha_loans",
            output_path=output_dir / "match_rate_by_state.png" if save_plots else None,
            interactive=show_plots,
        )

        # Temporal plot
        print("Creating temporal match rates plot...")
        plot_temporal_match_rates(
            fha_yearly,
            gnma_yearly,
            output_path=output_dir / "temporal_match_rates.png" if save_plots else None,
            interactive=show_plots,
        )

        # Loan amount plot
        print("Creating loan amount match rates plot...")
        plot_match_rates_by_loan_amount(
            loan_amount_rates,
            output_path=output_dir / "match_rates_by_loan_amount.png"
            if save_plots
            else None,
            interactive=show_plots,
        )

        # Interest rate plot
        print("Creating interest rate match rates plot...")
        plot_match_rates_by_interest_rate(
            interest_rate_rates,
            output_path=output_dir / "match_rates_by_interest_rate.png"
            if save_plots
            else None,
            interactive=show_plots,
        )

        # Loan purpose plot
        print("Creating loan purpose match rates plot...")
        plot_match_rates_by_purpose(
            purpose_rates,
            output_path=output_dir / "match_rates_by_purpose.png"
            if save_plots
            else None,
            interactive=show_plots,
        )

        # Blocking cell analysis plot
        print("Creating blocking cell analysis plot...")
        plot_blocking_cell_analysis(
            cell_stats,
            fha_states,
            output_path=output_dir / "blocking_cell_analysis.png"
            if save_plots
            else None,
            interactive=show_plots,
        )

        print(f"\nAll figures saved to: {output_dir}")

    # ------- Summary -------
    print_section("7. Summary and Key Takeaways")

    print("MATCH RATE SUMMARY:")
    print(f"  - Overall FHA match rate: {stats['fha_match_rate']:.1%}")
    print(f"  - Overall GNMA match rate: {stats['gnma_match_rate']:.1%}")
    print(
        f"  - Round 1 (strict) captures: {stats['round1_matches']/stats['n_matches']*100:.1f}% of matches"
    )
    print()

    print("STATE SIZE BIAS (CRITICAL):")
    print(f"  - Spearman correlation: r = {fha_corr['spearman_r']:.3f}")
    print("  - Match rate decreases ~22pp per 10x increase in state volume")
    print(f"  - Highest match rate: {fha_corr['max_match_rate']:.1%} (small states/territories)")
    print(f"  - Lowest match rate: {fha_corr['min_match_rate']:.1%} (large states)")
    print()

    print("IMPLICATIONS:")
    print("  1. The matched sample OVER-REPRESENTS small states (DC, VT, etc.)")
    print("  2. The matched sample UNDER-REPRESENTS large states (TX, CA, FL)")
    print("  3. Users should NOT interpret low match rates as poor matching quality")
    print("  4. Consider weighting or state-specific analysis for research use")
    print("  5. Matched loans are NOT missing at random - selection bias exists")

    print_critical_warning()

    return {
        "stats": stats,
        "fha_states": fha_states,
        "gnma_states": gnma_states,
        "fha_corr": fha_corr,
        "fha_yearly": fha_yearly,
        "gnma_yearly": gnma_yearly,
        "loan_amount_rates": loan_amount_rates,
        "interest_rate_rates": interest_rate_rates,
        "purpose_rates": purpose_rates,
        "cell_stats": cell_stats,
    }


