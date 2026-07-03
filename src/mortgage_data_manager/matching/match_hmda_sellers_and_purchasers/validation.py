"""HMDA Sellers-Purchasers Match Validation Module.

This module validates the HMDA seller-purchaser matching workflow that links
loan originations (sellers) with loan purchases (purchasers) within HMDA data.

The matching workflow uses 8 rounds with progressively relaxed criteria:
- Round 1: Same-year exact matches (~7.5M)
- Round 2: Cross-year strict matches (~368K)
- Round 3: Secondary sales (one-to-many) (~1.02M)
- Round 4: Without loan purpose match (~267K)
- Round 5: Loan amount tolerance (~211K)
- Round 6: Exclude portfolio/NA (~165K)
- Round 7: Minimal + fee requirement (~209K)
- Round 8: Purchaser-type constrained (~19K)

Total: ~9.7M matched pairs (2018-2024)

Match Rate Definitions:
- Purchase match rate: Share of HMDA purchases (action_taken=6) that find at least
  one matching origination. This measures coverage from the purchaser's perspective.
- Origination match rate: Share of originations with purchaser_type >= 5 that find
  at least one matching purchase. This excludes GSE sales (Fannie=1, Ginnie=2,
  Freddie=3, Farmer Mac=4) since those go through separate disclosure and wouldn't
  appear as HMDA purchases.

Validation analyses:
1. Match statistics by round
2. Temporal analysis (by seller year, cross-year patterns)
3. Geographic analysis (by state) - both rates
4. Loan characteristics (amount, type, purpose) - both rates
5. Purchaser type analysis (origination rate by ptype, with purchase rate reference)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.matching.match_hmda_sellers_and_purchasers.config import (
    CROSSWALK_OUTPUT_DIR,
    HMDA_SILVER_DIR_POST2018,
    MAX_YEAR_POST2018,
    MIN_YEAR_POST2018,
)
from mortgage_data_manager.matching.match_hmda_sellers_and_purchasers.utils import (
    scan_best_file_type,
)

# Validation output directory - save figures to docs for documentation
VALIDATION_OUTPUT_DIR = (
    MortgageDataConfig.PROJECT_DIR
    / "docs"
    / "matching"
    / "figures"
    / "hmda_sellers_purchasers"
)

# Year range
MIN_YEAR = MIN_YEAR_POST2018
MAX_YEAR = MAX_YEAR_POST2018

ROUND_DESCRIPTIONS = {
    1: "Same-year exact",
    2: "Cross-year strict",
    3: "Secondary sales (1-to-many)",
    4: "Without loan purpose",
    5: "Loan amount tolerance",
    6: "Exclude portfolio/NA",
    7: "Minimal + fee requirement",
    8: "Purchaser-type constrained",
}

PURCHASER_TYPE_LABELS = {
    0: "Not applicable/Not sold",
    1: "Fannie Mae",
    2: "Ginnie Mae",
    3: "Freddie Mac",
    4: "Farmer Mac",
    5: "Private securitizer",
    6: "Commercial bank/savings",
    71: "Credit union/mortgage co/finance co",
    72: "Life insurance company",
    8: "Affiliate institution",
    9: "Other type of purchaser",
}

LOAN_TYPE_LABELS = {1: "Conventional", 2: "FHA", 3: "VA", 4: "RHS/FSA"}

LOAN_PURPOSE_LABELS = {
    1: "Home purchase",
    2: "Home improvement",
    31: "Cash-out refinancing",
    32: "Other refinancing",
    4: "Other purpose",
    5: "Not applicable",
}

# Colors (Jonathan's personal palette)
COLORS = {
    "midnight": "#0E2F44",  # Primary - headers, dominant elements
    "hunter": "#145A32",  # Primary - large datasets, key series
    "gold": "#D4AC0D",  # Secondary - focus data, callouts
    "terracotta": "#BA4A00",  # Secondary - trend lines, emphasis
    "mulberry": "#900C3F",  # Tertiary - high-cardinality categories
    "purple": "#5B2C6F",  # Tertiary - additional categories
    "teal": "#117864",  # Tertiary - additional categories
    "grey": "#85929E",  # Neutral - context, axes, benchmarks
    "slate": "#566573",  # Neutral - borders, secondary text
    "canvas": "#F8F9F9",  # Background
    "ink": "#1B2631",  # Text
}

# Categorical sequence (alternates dark/light, warm/cool for accessibility)
CATEGORICAL_COLORS = [
    COLORS["midnight"],
    COLORS["gold"],
    COLORS["hunter"],
    COLORS["terracotta"],
    COLORS["purple"],
    COLORS["grey"],
    COLORS["mulberry"],
]

try:
    import matplotlib

    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    matplotlib = None


def _get_pyplot(interactive: bool = True):
    if not HAS_MATPLOTLIB:
        return None
    if not interactive:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


# =============================================================================
# Data Loading
# =============================================================================


def load_crosswalk(crosswalk_dir: Path | None = None) -> pl.LazyFrame:
    """Load the seller-purchaser crosswalk."""
    if crosswalk_dir is None:
        crosswalk_dir = CROSSWALK_OUTPUT_DIR / "post2018"
    return pl.scan_parquet(crosswalk_dir / "**/*.parquet")


def load_hmda_originations(hmda_dir: Path | None = None) -> pl.LazyFrame:
    """Load HMDA originations (action_taken=1)."""
    if hmda_dir is None:
        hmda_dir = HMDA_SILVER_DIR_POST2018
    year_dirs = sorted(hmda_dir.glob("activity_year=*"))
    lazy_frames = [scan_best_file_type(year_dir) for year_dir in year_dirs]
    df = pl.concat(lazy_frames, how="diagonal")
    return df.filter(pl.col("action_taken") == 1)


def load_hmda_purchases(hmda_dir: Path | None = None) -> pl.LazyFrame:
    """Load HMDA purchases (action_taken=6)."""
    if hmda_dir is None:
        hmda_dir = HMDA_SILVER_DIR_POST2018
    year_dirs = sorted(hmda_dir.glob("activity_year=*"))
    lazy_frames = [scan_best_file_type(year_dir) for year_dir in year_dirs]
    df = pl.concat(lazy_frames, how="diagonal")
    return df.filter(pl.col("action_taken") == 6)


def enrich_crosswalk_with_seller_info(
    crosswalk: pl.LazyFrame, hmda: pl.LazyFrame
) -> pl.LazyFrame:
    """Enrich crosswalk with seller information."""
    hmda_cols = hmda.select(
        [
            "HMDAIndex",
            "activity_year",
            "loan_type",
            "loan_purpose",
            "loan_amount",
            "purchaser_type",
            "census_tract",
        ]
    )
    hmda_renamed = hmda_cols.rename(
        {c: f"{c}_seller" for c in hmda_cols.collect_schema().names()}
    )
    return crosswalk.join(
        hmda_renamed, left_on="HMDAIndex_s", right_on="HMDAIndex_seller", how="left"
    )


def enrich_crosswalk_with_purchaser_info(
    crosswalk: pl.LazyFrame, hmda: pl.LazyFrame
) -> pl.LazyFrame:
    """Enrich crosswalk with purchaser information."""
    hmda_cols = hmda.select(["HMDAIndex", "activity_year"])
    hmda_renamed = hmda_cols.rename(
        {c: f"{c}_purchaser" for c in hmda_cols.collect_schema().names()}
    )
    return crosswalk.join(
        hmda_renamed, left_on="HMDAIndex_p", right_on="HMDAIndex_purchaser", how="left"
    )


# =============================================================================
# Match Rate Computations
# =============================================================================


def compute_match_statistics_by_round(crosswalk: pl.LazyFrame) -> pl.DataFrame:
    """Compute match statistics by round."""
    return (
        crosswalk.group_by("MatchRound")
        .agg(pl.len().alias("match_count"))
        .sort("MatchRound")  # Sort BEFORE cumsum
        .with_columns(
            [
                (pl.col("match_count") / pl.col("match_count").sum()).alias(
                    "pct_of_total"
                ),
                pl.col("match_count").cum_sum().alias("cumulative_count"),
            ]
        )
        .with_columns(
            (pl.col("cumulative_count") / pl.col("match_count").sum()).alias(
                "cumulative_pct"
            )
        )
        .collect()
    )


def compute_matches_by_seller_year(crosswalk: pl.LazyFrame) -> pl.DataFrame:
    """Compute matches by seller year."""
    return (
        crosswalk.group_by("activity_year_s")
        .agg(
            [pl.len().alias("match_count"), pl.col("MatchRound").mean().alias("avg_round")]
        )
        .sort("activity_year_s")
        .collect()
    )


def compute_cross_year_patterns(
    crosswalk: pl.LazyFrame, hmda_purchases: pl.LazyFrame
) -> pl.DataFrame:
    """Compute cross-year matching patterns."""
    enriched = enrich_crosswalk_with_purchaser_info(crosswalk, hmda_purchases)
    return (
        enriched.with_columns(
            (pl.col("activity_year_purchaser") - pl.col("activity_year_s")).alias(
                "year_diff"
            )
        )
        .group_by("year_diff")
        .agg(pl.len().alias("match_count"))
        .with_columns((pl.col("match_count") / pl.col("match_count").sum()).alias("pct"))
        .sort("year_diff")
        .collect()
    )


def compute_cross_year_by_round(
    crosswalk: pl.LazyFrame, hmda_purchases: pl.LazyFrame
) -> pl.DataFrame:
    """Compute cross-year matching by round."""
    enriched = enrich_crosswalk_with_purchaser_info(crosswalk, hmda_purchases)
    return (
        enriched.with_columns(
            [
                (pl.col("activity_year_purchaser") - pl.col("activity_year_s")).alias(
                    "year_diff"
                ),
                (pl.col("activity_year_purchaser") == pl.col("activity_year_s")).alias(
                    "same_year"
                ),
            ]
        )
        .group_by(["MatchRound", "same_year"])
        .agg(pl.len().alias("match_count"))
        .sort(["MatchRound", "same_year"])
        .collect()
    )


def compute_match_rates_by_state(
    crosswalk: pl.LazyFrame,
    hmda_originations: pl.LazyFrame,
    hmda_purchases: pl.LazyFrame,
) -> pl.DataFrame:
    """Compute match rates by state for both purchases and eligible originations.

    Returns two rates per state:
    - purchase_match_rate: Share of purchases finding at least one matching origination
    - origination_match_rate: Share of originations with purchaser_type >= 5 finding a match
    """
    matched_sellers = crosswalk.select("HMDAIndex_s").unique()
    matched_purchasers = crosswalk.select("HMDAIndex_p").unique()

    # Origination match rate (only for purchaser_type >= 5)
    orig_eligible = hmda_originations.filter(
        (pl.col("census_tract").is_not_null()) & (pl.col("purchaser_type") >= 5)
    ).with_columns(pl.col("census_tract").str.slice(0, 2).alias("state_fips"))
    orig_totals = orig_eligible.group_by("state_fips").agg(
        pl.len().alias("total_originations")
    )
    orig_matched = (
        orig_eligible.join(
            matched_sellers, left_on="HMDAIndex", right_on="HMDAIndex_s", how="inner"
        )
        .group_by("state_fips")
        .agg(pl.len().alias("matched_originations"))
    )

    # Purchase match rate
    purch_with_state = hmda_purchases.filter(
        pl.col("census_tract").is_not_null()
    ).with_columns(pl.col("census_tract").str.slice(0, 2).alias("state_fips"))
    purch_totals = purch_with_state.group_by("state_fips").agg(
        pl.len().alias("total_purchases")
    )
    purch_matched = (
        purch_with_state.join(
            matched_purchasers, left_on="HMDAIndex", right_on="HMDAIndex_p", how="inner"
        )
        .group_by("state_fips")
        .agg(pl.len().alias("matched_purchases"))
    )

    # Combine results
    result = (
        orig_totals.join(orig_matched, on="state_fips", how="left")
        .join(purch_totals, on="state_fips", how="full", coalesce=True)
        .join(purch_matched, on="state_fips", how="left")
        .with_columns(
            [
                pl.col("matched_originations").fill_null(0),
                pl.col("matched_purchases").fill_null(0),
                pl.col("total_originations").fill_null(0),
                pl.col("total_purchases").fill_null(0),
            ]
        )
        .with_columns(
            [
                (pl.col("matched_originations") / pl.col("total_originations")).alias(
                    "origination_match_rate"
                ),
                (pl.col("matched_purchases") / pl.col("total_purchases")).alias(
                    "purchase_match_rate"
                ),
            ]
        )
        .sort("total_purchases", descending=True)
        .collect()
    )
    return result


def compute_match_rates_by_loan_type(
    crosswalk: pl.LazyFrame,
    hmda_originations: pl.LazyFrame,
    hmda_purchases: pl.LazyFrame,
) -> pl.DataFrame:
    """Compute match rates by loan type for both purchases and eligible originations."""
    matched_sellers = crosswalk.select("HMDAIndex_s").unique()
    matched_purchasers = crosswalk.select("HMDAIndex_p").unique()

    # Origination match rate (only for purchaser_type >= 5)
    orig_eligible = hmda_originations.filter(pl.col("purchaser_type") >= 5)
    orig_totals = orig_eligible.group_by("loan_type").agg(
        pl.len().alias("total_originations")
    )
    orig_matched = (
        orig_eligible.join(
            matched_sellers, left_on="HMDAIndex", right_on="HMDAIndex_s", how="inner"
        )
        .group_by("loan_type")
        .agg(pl.len().alias("matched_originations"))
    )

    # Purchase match rate
    purch_totals = hmda_purchases.group_by("loan_type").agg(
        pl.len().alias("total_purchases")
    )
    purch_matched = (
        hmda_purchases.join(
            matched_purchasers, left_on="HMDAIndex", right_on="HMDAIndex_p", how="inner"
        )
        .group_by("loan_type")
        .agg(pl.len().alias("matched_purchases"))
    )

    result = (
        orig_totals.join(orig_matched, on="loan_type", how="left")
        .join(purch_totals, on="loan_type", how="full", coalesce=True)
        .join(purch_matched, on="loan_type", how="left")
        .with_columns(
            [
                pl.col("matched_originations").fill_null(0),
                pl.col("matched_purchases").fill_null(0),
                pl.col("total_originations").fill_null(0),
                pl.col("total_purchases").fill_null(0),
            ]
        )
        .with_columns(
            [
                (pl.col("matched_originations") / pl.col("total_originations")).alias(
                    "origination_match_rate"
                ),
                (pl.col("matched_purchases") / pl.col("total_purchases")).alias(
                    "purchase_match_rate"
                ),
            ]
        )
        .sort("total_purchases", descending=True)
        .collect()
    )
    return result


def compute_match_rates_by_loan_purpose(
    crosswalk: pl.LazyFrame,
    hmda_originations: pl.LazyFrame,
    hmda_purchases: pl.LazyFrame,
) -> pl.DataFrame:
    """Compute match rates by loan purpose for both purchases and eligible originations."""
    matched_sellers = crosswalk.select("HMDAIndex_s").unique()
    matched_purchasers = crosswalk.select("HMDAIndex_p").unique()

    # Origination match rate (only for purchaser_type >= 5)
    orig_eligible = hmda_originations.filter(pl.col("purchaser_type") >= 5)
    orig_totals = orig_eligible.group_by("loan_purpose").agg(
        pl.len().alias("total_originations")
    )
    orig_matched = (
        orig_eligible.join(
            matched_sellers, left_on="HMDAIndex", right_on="HMDAIndex_s", how="inner"
        )
        .group_by("loan_purpose")
        .agg(pl.len().alias("matched_originations"))
    )

    # Purchase match rate
    purch_totals = hmda_purchases.group_by("loan_purpose").agg(
        pl.len().alias("total_purchases")
    )
    purch_matched = (
        hmda_purchases.join(
            matched_purchasers, left_on="HMDAIndex", right_on="HMDAIndex_p", how="inner"
        )
        .group_by("loan_purpose")
        .agg(pl.len().alias("matched_purchases"))
    )

    result = (
        orig_totals.join(orig_matched, on="loan_purpose", how="left")
        .join(purch_totals, on="loan_purpose", how="full", coalesce=True)
        .join(purch_matched, on="loan_purpose", how="left")
        .with_columns(
            [
                pl.col("matched_originations").fill_null(0),
                pl.col("matched_purchases").fill_null(0),
                pl.col("total_originations").fill_null(0),
                pl.col("total_purchases").fill_null(0),
            ]
        )
        .with_columns(
            [
                (pl.col("matched_originations") / pl.col("total_originations")).alias(
                    "origination_match_rate"
                ),
                (pl.col("matched_purchases") / pl.col("total_purchases")).alias(
                    "purchase_match_rate"
                ),
            ]
        )
        .sort("total_purchases", descending=True)
        .collect()
    )
    return result


def compute_match_rates_by_loan_amount_bin(
    crosswalk: pl.LazyFrame,
    hmda_originations: pl.LazyFrame,
    hmda_purchases: pl.LazyFrame,
    bin_size: int = 50000,
    max_amount: int = 1000000,
) -> pl.DataFrame:
    """Compute match rates by loan amount bin for both purchases and eligible originations."""
    matched_sellers = crosswalk.select("HMDAIndex_s").unique()
    matched_purchasers = crosswalk.select("HMDAIndex_p").unique()

    # Origination match rate (only for purchaser_type >= 5)
    orig_eligible = hmda_originations.filter(
        (pl.col("loan_amount").is_not_null())
        & (pl.col("loan_amount") <= max_amount)
        & (pl.col("purchaser_type") >= 5)
    ).with_columns(
        ((pl.col("loan_amount") / bin_size).floor() * bin_size).alias("loan_amount_bin")
    )
    orig_totals = orig_eligible.group_by("loan_amount_bin").agg(
        pl.len().alias("total_originations")
    )
    orig_matched = (
        orig_eligible.join(
            matched_sellers, left_on="HMDAIndex", right_on="HMDAIndex_s", how="inner"
        )
        .group_by("loan_amount_bin")
        .agg(pl.len().alias("matched_originations"))
    )

    # Purchase match rate
    purch_filtered = hmda_purchases.filter(
        (pl.col("loan_amount").is_not_null()) & (pl.col("loan_amount") <= max_amount)
    ).with_columns(
        ((pl.col("loan_amount") / bin_size).floor() * bin_size).alias("loan_amount_bin")
    )
    purch_totals = purch_filtered.group_by("loan_amount_bin").agg(
        pl.len().alias("total_purchases")
    )
    purch_matched = (
        purch_filtered.join(
            matched_purchasers, left_on="HMDAIndex", right_on="HMDAIndex_p", how="inner"
        )
        .group_by("loan_amount_bin")
        .agg(pl.len().alias("matched_purchases"))
    )

    result = (
        orig_totals.join(orig_matched, on="loan_amount_bin", how="left")
        .join(purch_totals, on="loan_amount_bin", how="full", coalesce=True)
        .join(purch_matched, on="loan_amount_bin", how="left")
        .with_columns(
            [
                pl.col("matched_originations").fill_null(0),
                pl.col("matched_purchases").fill_null(0),
                pl.col("total_originations").fill_null(0),
                pl.col("total_purchases").fill_null(0),
            ]
        )
        .with_columns(
            [
                (pl.col("matched_originations") / pl.col("total_originations")).alias(
                    "origination_match_rate"
                ),
                (pl.col("matched_purchases") / pl.col("total_purchases")).alias(
                    "purchase_match_rate"
                ),
            ]
        )
        .sort("loan_amount_bin")
        .collect()
    )
    return result


def compute_matches_by_purchaser_type(
    crosswalk: pl.LazyFrame, hmda_originations: pl.LazyFrame
) -> pl.DataFrame:
    """Compute matches by purchaser type."""
    enriched = enrich_crosswalk_with_seller_info(crosswalk, hmda_originations)
    return (
        enriched.group_by("purchaser_type_seller")
        .agg(
            [pl.len().alias("match_count"), pl.col("MatchRound").mean().alias("avg_round")]
        )
        .with_columns((pl.col("match_count") / pl.col("match_count").sum()).alias("pct"))
        .sort("match_count", descending=True)
        .collect()
    )


def compute_match_rates_by_purchaser_type(
    crosswalk: pl.LazyFrame,
    hmda_originations: pl.LazyFrame,
    hmda_purchases: pl.LazyFrame,
) -> pl.DataFrame:
    """Compute match rates by purchaser type for eligible originations.

    Only originations with purchaser_type >= 5 are considered eligible.
    Also computes overall purchase match rate for reference.
    """
    matched_sellers = crosswalk.select("HMDAIndex_s").unique()
    matched_purchasers = crosswalk.select("HMDAIndex_p").unique()

    # Origination match rate by purchaser_type (only for purchaser_type >= 5)
    orig_eligible = hmda_originations.filter(pl.col("purchaser_type") >= 5)
    orig_totals = orig_eligible.group_by("purchaser_type").agg(
        pl.len().alias("total_originations")
    )
    orig_matched = (
        orig_eligible.join(
            matched_sellers, left_on="HMDAIndex", right_on="HMDAIndex_s", how="inner"
        )
        .group_by("purchaser_type")
        .agg(pl.len().alias("matched_originations"))
    )

    # Overall purchase match rate (to show as reference)
    total_purchases = hmda_purchases.select(pl.len()).collect().item()
    matched_purchases = (
        hmda_purchases.join(
            matched_purchasers, left_on="HMDAIndex", right_on="HMDAIndex_p", how="inner"
        )
        .select(pl.len())
        .collect()
        .item()
    )
    overall_purchase_rate = (
        matched_purchases / total_purchases if total_purchases > 0 else 0
    )

    result = (
        orig_totals.join(orig_matched, on="purchaser_type", how="left")
        .with_columns(
            [
                pl.col("matched_originations").fill_null(0),
                (pl.col("matched_originations") / pl.col("total_originations")).alias(
                    "origination_match_rate"
                ),
                pl.lit(overall_purchase_rate).alias("overall_purchase_match_rate"),
                pl.lit(total_purchases).alias("total_purchases"),
                pl.lit(matched_purchases).alias("matched_purchases"),
            ]
        )
        .sort("total_originations", descending=True)
        .collect()
    )
    return result


# =============================================================================
# Visualization Functions
# =============================================================================


def plot_match_statistics_by_round(
    round_stats: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot match statistics by round."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    rounds = round_stats["MatchRound"].to_list()
    counts = round_stats["match_count"].to_list()
    cumulative = round_stats["cumulative_count"].to_list()

    # Left panel: per-round counts
    bar_colors = [
        CATEGORICAL_COLORS[i % len(CATEGORICAL_COLORS)] for i in range(len(rounds))
    ]
    ax1.bar(rounds, counts, color=bar_colors, edgecolor=COLORS["ink"], alpha=0.9)
    ax1.set_xlabel("Match Round", fontsize=12, color=COLORS["ink"])
    ax1.set_ylabel("Match Count", fontsize=12, color=COLORS["ink"])
    ax1.set_title("Match Counts by Round", fontsize=14, color=COLORS["ink"])
    ax1.set_xticks(rounds)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f"{x/1e6:.1f}M"))
    ax1.grid(True, alpha=0.3, axis="y")

    # Right panel: cumulative counts
    ax2.plot(rounds, cumulative, "o-", color=COLORS["hunter"], linewidth=2, markersize=8)
    ax2.fill_between(rounds, cumulative, alpha=0.3, color=COLORS["hunter"])
    ax2.set_xlabel("Match Round", fontsize=12, color=COLORS["ink"])
    ax2.set_ylabel("Cumulative Match Count", fontsize=12, color=COLORS["ink"])
    ax2.set_title("Cumulative Match Coverage", fontsize=14, color=COLORS["ink"])
    ax2.set_xticks(rounds)
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f"{x/1e6:.1f}M"))
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=COLORS["canvas"])
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_matches_by_year(
    yearly_stats: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot matches by year."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        return
    fig, ax = plt.subplots(figsize=(10, 6))
    years = yearly_stats["activity_year_s"].to_list()
    counts = yearly_stats["match_count"].to_list()
    bars = ax.bar(
        years,
        [c / 1e6 for c in counts],
        color=COLORS["hunter"],
        edgecolor=COLORS["ink"],
        alpha=0.9,
    )
    ax.set_xlabel("Seller (Origination) Year", fontsize=12, color=COLORS["ink"])
    ax.set_ylabel("Matches (Millions)", fontsize=12, color=COLORS["ink"])
    ax.set_title("Matched Loans by Origination Year", fontsize=14, color=COLORS["ink"])
    ax.set_xticks(years)
    for bar, count in zip(bars, counts):
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            bar.get_height() + 0.05,
            f"{count / 1e6:.2f}M",
            ha="center",
            va="bottom",
            fontsize=9,
            color=COLORS["ink"],
        )
    ax.grid(True, alpha=0.3, axis="y")
    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=COLORS["canvas"])
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_cross_year_patterns(
    cross_year_stats: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot cross-year matching patterns."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        return
    fig, ax = plt.subplots(figsize=(10, 6))
    filtered = cross_year_stats.filter(
        (pl.col("year_diff") >= 0) & (pl.col("year_diff") <= 6)
    )
    year_diffs = filtered["year_diff"].to_list()
    pcts = filtered["pct"].to_list()
    colors = [COLORS["hunter"] if d == 0 else COLORS["gold"] for d in year_diffs]
    bars = ax.bar(
        year_diffs,
        [p * 100 for p in pcts],
        color=colors,
        edgecolor=COLORS["ink"],
        alpha=0.9,
    )
    ax.set_xlabel(
        "Year Difference (Purchaser Year - Seller Year)", fontsize=12, color=COLORS["ink"]
    )
    ax.set_ylabel("Percentage of Matches (%)", fontsize=12, color=COLORS["ink"])
    ax.set_title(
        "Same-Year vs Cross-Year Matching Patterns", fontsize=14, color=COLORS["ink"]
    )
    for bar, pct in zip(bars, pcts):
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            bar.get_height() + 0.5,
            f"{pct * 100:.1f}%",
            ha="center",
            va="bottom",
            fontsize=9,
            color=COLORS["ink"],
        )
    ax.set_xticks(year_diffs)
    ax.grid(True, alpha=0.3, axis="y")
    from matplotlib.patches import Patch

    legend_elements = [
        Patch(
            facecolor=COLORS["hunter"],
            edgecolor=COLORS["ink"],
            alpha=0.9,
            label="Same Year",
        ),
        Patch(
            facecolor=COLORS["gold"],
            edgecolor=COLORS["ink"],
            alpha=0.9,
            label="Cross Year",
        ),
    ]
    ax.legend(handles=legend_elements, loc="upper right")
    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=COLORS["canvas"])
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_match_rates_by_purchaser_type(
    purchaser_type_rates: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot match rates by purchaser type."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        return
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    filtered = purchaser_type_rates.filter(pl.col("purchaser_type").is_not_null())
    labels = [
        PURCHASER_TYPE_LABELS.get(pt, f"Type {pt}")
        for pt in filtered["purchaser_type"].to_list()
    ]
    totals = filtered["total_originations"].to_list()
    orig_rates = filtered["origination_match_rate"].to_list()

    # Get overall purchase match rate for reference line
    overall_purch_rate = (
        filtered["overall_purchase_match_rate"].to_list()[0] if len(filtered) > 0 else 0
    )

    y_pos = range(len(labels))

    # Left panel: volume
    ax1.barh(
        y_pos,
        [t / 1e6 for t in totals],
        color=COLORS["gold"],
        edgecolor=COLORS["ink"],
        alpha=0.9,
    )
    ax1.set_yticks(y_pos)
    ax1.set_yticklabels(labels, color=COLORS["ink"])
    ax1.set_xlabel(
        "Total Eligible Originations (Millions)", fontsize=12, color=COLORS["ink"]
    )
    ax1.set_title(
        "Eligible Originations by Purchaser Type\n(purchaser_type >= 5 only)",
        fontsize=14,
        color=COLORS["ink"],
    )
    ax1.invert_yaxis()

    # Right panel: match rates with reference line
    bars = ax2.barh(
        y_pos,
        [r * 100 if r else 0 for r in orig_rates],
        color=COLORS["gold"],
        edgecolor=COLORS["ink"],
        alpha=0.9,
        label="Origination Match Rate",
    )
    ax2.axvline(
        x=overall_purch_rate * 100,
        color=COLORS["hunter"],
        linestyle="--",
        linewidth=2,
        label=f"Overall Purchase Match Rate ({overall_purch_rate * 100:.1f}%)",
    )
    ax2.set_yticks(y_pos)
    ax2.set_yticklabels(labels, color=COLORS["ink"])
    ax2.set_xlabel("Match Rate (%)", fontsize=12, color=COLORS["ink"])
    ax2.set_title(
        "Match Rates by Purchaser Type", fontsize=14, color=COLORS["ink"]
    )
    ax2.invert_yaxis()
    ax2.set_xlim(0, 100)
    ax2.legend(loc="lower right")

    for bar, rate in zip(bars, orig_rates):
        label = f"{rate * 100:.1f}%" if rate else "0%"
        ax2.text(
            bar.get_width() + 2,
            bar.get_y() + bar.get_height() / 2.0,
            label,
            ha="left",
            va="center",
            fontsize=9,
            color=COLORS["ink"],
        )

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=COLORS["canvas"])
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_match_rates_by_loan_amount(
    loan_amount_rates: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot match rates by loan amount."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        return
    fig, ax = plt.subplots(figsize=(12, 6))
    bins = loan_amount_rates["loan_amount_bin"].to_list()
    orig_rates = loan_amount_rates["origination_match_rate"].to_list()
    purch_rates = loan_amount_rates["purchase_match_rate"].to_list()
    orig_totals = loan_amount_rates["total_originations"].to_list()
    purch_totals = loan_amount_rates["total_purchases"].to_list()

    # Normalize sizes
    max_orig = max(orig_totals) if orig_totals else 1
    max_purch = max(purch_totals) if purch_totals else 1
    orig_sizes = [30 + 150 * (t / max_orig) for t in orig_totals]
    purch_sizes = [30 + 150 * (t / max_purch) for t in purch_totals]

    # Plot both rates
    ax.scatter(
        [b / 1000 for b in bins],
        [r * 100 if r else 0 for r in purch_rates],
        s=purch_sizes,
        c=COLORS["hunter"],
        alpha=0.7,
        edgecolors=COLORS["ink"],
        label="Purchase Match Rate",
    )
    ax.plot(
        [b / 1000 for b in bins],
        [r * 100 if r else 0 for r in purch_rates],
        "-",
        color=COLORS["hunter"],
        alpha=0.6,
    )

    ax.scatter(
        [b / 1000 for b in bins],
        [r * 100 if r else 0 for r in orig_rates],
        s=orig_sizes,
        c=COLORS["gold"],
        alpha=0.7,
        edgecolors=COLORS["ink"],
        label="Origination Match Rate (ptype>=5)",
    )
    ax.plot(
        [b / 1000 for b in bins],
        [r * 100 if r else 0 for r in orig_rates],
        "-",
        color=COLORS["gold"],
        alpha=0.6,
    )

    ax.set_xlabel("Loan Amount ($K)", fontsize=12, color=COLORS["ink"])
    ax.set_ylabel("Match Rate (%)", fontsize=12, color=COLORS["ink"])
    ax.set_title(
        "Match Rates by Loan Amount\n(Point size indicates volume)",
        fontsize=14,
        color=COLORS["ink"],
    )
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)

    all_rates = [r for r in orig_rates + purch_rates if r is not None]
    if all_rates:
        ax.set_ylim(max(0, min(all_rates) * 100 - 5), min(100, max(all_rates) * 100 + 5))
    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=COLORS["canvas"])
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_match_rates_by_loan_type(
    loan_type_rates: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot match rates by loan type."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        return
    fig, ax = plt.subplots(figsize=(12, 6))
    filtered = loan_type_rates.filter(pl.col("loan_type").is_not_null())
    labels = [
        LOAN_TYPE_LABELS.get(lt, f"Type {lt}")
        for lt in filtered["loan_type"].to_list()
    ]
    orig_rates = filtered["origination_match_rate"].to_list()
    purch_rates = filtered["purchase_match_rate"].to_list()

    x = np.arange(len(labels))
    width = 0.35

    bars1 = ax.bar(
        x - width / 2,
        [r * 100 if r else 0 for r in purch_rates],
        width,
        label="Purchase Match Rate",
        color=COLORS["hunter"],
        edgecolor=COLORS["ink"],
        alpha=0.9,
    )
    bars2 = ax.bar(
        x + width / 2,
        [r * 100 if r else 0 for r in orig_rates],
        width,
        label="Origination Match Rate (ptype>=5)",
        color=COLORS["gold"],
        edgecolor=COLORS["ink"],
        alpha=0.9,
    )

    ax.set_xlabel("Loan Type", fontsize=12, color=COLORS["ink"])
    ax.set_ylabel("Match Rate (%)", fontsize=12, color=COLORS["ink"])
    ax.set_title("Match Rates by Loan Type", fontsize=14, color=COLORS["ink"])
    ax.set_xticks(x)
    ax.set_xticklabels(labels, color=COLORS["ink"])
    ax.legend(loc="upper right")

    for bar, rate in zip(bars1, purch_rates):
        if rate:
            ax.text(
                bar.get_x() + bar.get_width() / 2.0,
                bar.get_height() + 1,
                f"{rate * 100:.1f}%",
                ha="center",
                va="bottom",
                fontsize=9,
                color=COLORS["ink"],
            )
    for bar, rate in zip(bars2, orig_rates):
        if rate:
            ax.text(
                bar.get_x() + bar.get_width() / 2.0,
                bar.get_height() + 1,
                f"{rate * 100:.1f}%",
                ha="center",
                va="bottom",
                fontsize=9,
                color=COLORS["ink"],
            )

    all_rates = [r for r in orig_rates + purch_rates if r is not None]
    ax.set_ylim(0, max([r * 100 for r in all_rates]) + 15 if all_rates else 100)
    ax.grid(True, alpha=0.3, axis="y")
    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=COLORS["canvas"])
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_match_rates_by_loan_purpose(
    loan_purpose_rates: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot match rates by loan purpose."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        return
    fig, ax = plt.subplots(figsize=(14, 6))
    filtered = loan_purpose_rates.filter(pl.col("loan_purpose").is_not_null())
    labels = [
        LOAN_PURPOSE_LABELS.get(lp, f"Purpose {lp}")
        for lp in filtered["loan_purpose"].to_list()
    ]
    orig_rates = filtered["origination_match_rate"].to_list()
    purch_rates = filtered["purchase_match_rate"].to_list()

    x = np.arange(len(labels))
    width = 0.35

    bars1 = ax.bar(
        x - width / 2,
        [r * 100 if r else 0 for r in purch_rates],
        width,
        label="Purchase Match Rate",
        color=COLORS["hunter"],
        edgecolor=COLORS["ink"],
        alpha=0.9,
    )
    bars2 = ax.bar(
        x + width / 2,
        [r * 100 if r else 0 for r in orig_rates],
        width,
        label="Origination Match Rate (ptype>=5)",
        color=COLORS["gold"],
        edgecolor=COLORS["ink"],
        alpha=0.9,
    )

    ax.set_xlabel("Loan Purpose", fontsize=12, color=COLORS["ink"])
    ax.set_ylabel("Match Rate (%)", fontsize=12, color=COLORS["ink"])
    ax.set_title("Match Rates by Loan Purpose", fontsize=14, color=COLORS["ink"])
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=20, ha="right", color=COLORS["ink"])
    ax.legend(loc="upper right")

    for bar, rate in zip(bars1, purch_rates):
        if rate:
            ax.text(
                bar.get_x() + bar.get_width() / 2.0,
                bar.get_height() + 1,
                f"{rate * 100:.1f}%",
                ha="center",
                va="bottom",
                fontsize=8,
                color=COLORS["ink"],
            )
    for bar, rate in zip(bars2, orig_rates):
        if rate:
            ax.text(
                bar.get_x() + bar.get_width() / 2.0,
                bar.get_height() + 1,
                f"{rate * 100:.1f}%",
                ha="center",
                va="bottom",
                fontsize=8,
                color=COLORS["ink"],
            )

    all_rates = [r for r in orig_rates + purch_rates if r is not None]
    ax.set_ylim(0, max([r * 100 for r in all_rates]) + 15 if all_rates else 100)
    ax.grid(True, alpha=0.3, axis="y")
    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=COLORS["canvas"])
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


# =============================================================================
# Main Validation Runner
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
    results = {}

    print("Loading data...")
    crosswalk = load_crosswalk()
    hmda_originations = load_hmda_originations()
    hmda_purchases = load_hmda_purchases()

    n_matches = crosswalk.select(pl.len()).collect().item()
    n_originations = hmda_originations.select(pl.len()).collect().item()
    n_purchases = hmda_purchases.select(pl.len()).collect().item()

    # Count eligible originations (purchaser_type >= 5)
    n_eligible_originations = (
        hmda_originations.filter(pl.col("purchaser_type") >= 5)
        .select(pl.len())
        .collect()
        .item()
    )

    # Count matched records from each perspective
    matched_sellers = crosswalk.select("HMDAIndex_s").unique()
    matched_purchasers = crosswalk.select("HMDAIndex_p").unique()
    n_matched_originations = (
        hmda_originations.filter(pl.col("purchaser_type") >= 5)
        .join(matched_sellers, left_on="HMDAIndex", right_on="HMDAIndex_s", how="inner")
        .select(pl.len())
        .collect()
        .item()
    )
    n_matched_purchases = (
        hmda_purchases.join(
            matched_purchasers, left_on="HMDAIndex", right_on="HMDAIndex_p", how="inner"
        )
        .select(pl.len())
        .collect()
        .item()
    )

    print(f"Crosswalk: {n_matches:,} matched pairs")
    print(
        f"HMDA originations: {n_originations:,} total, {n_eligible_originations:,} eligible (ptype>=5)"
    )
    print(f"HMDA purchases: {n_purchases:,}")

    origination_match_rate = (
        n_matched_originations / n_eligible_originations
        if n_eligible_originations > 0
        else 0
    )
    purchase_match_rate = n_matched_purchases / n_purchases if n_purchases > 0 else 0
    print(f"Overall origination match rate (ptype>=5): {origination_match_rate:.1%}")
    print(f"Overall purchase match rate: {purchase_match_rate:.1%}")

    results["n_matches"] = n_matches
    results["n_originations"] = n_originations
    results["n_eligible_originations"] = n_eligible_originations
    results["n_purchases"] = n_purchases
    results["origination_match_rate"] = origination_match_rate
    results["purchase_match_rate"] = purchase_match_rate

    print_section("1. Match Statistics by Round")
    round_stats = compute_match_statistics_by_round(crosswalk)
    round_stats = round_stats.with_columns(
        pl.col("MatchRound")
        .replace_strict(ROUND_DESCRIPTIONS, default="Unknown")
        .alias("description")
    )
    print("Match counts by round:")
    print(round_stats)
    results["round_stats"] = round_stats

    if len(round_stats) > 0:
        round1_pct = round_stats.filter(pl.col("MatchRound") == 1)[
            "pct_of_total"
        ].to_list()
        if round1_pct:
            print(f"\nRound 1 captures: {round1_pct[0]:.1%} of all matches")
        first_3_pct = (
            round_stats.filter(pl.col("MatchRound") <= 3)["match_count"].sum() / n_matches
        )
        print(f"Rounds 1-3 capture: {first_3_pct:.1%} of all matches")

    print_section("2. Temporal Analysis")
    yearly_stats = compute_matches_by_seller_year(crosswalk)
    print("Matches by origination year:")
    print(yearly_stats)
    results["yearly_stats"] = yearly_stats

    print("\nCross-year matching patterns:")
    cross_year_stats = compute_cross_year_patterns(crosswalk, hmda_purchases)
    print(cross_year_stats)
    results["cross_year_stats"] = cross_year_stats

    same_year_pct = cross_year_stats.filter(pl.col("year_diff") == 0)["pct"].to_list()
    if same_year_pct:
        print(f"\nSame-year matches: {same_year_pct[0]:.1%}")

    print("\nCross-year matching by round:")
    cross_year_by_round = compute_cross_year_by_round(crosswalk, hmda_purchases)
    print(cross_year_by_round.head(20))
    results["cross_year_by_round"] = cross_year_by_round

    print_section("3. Geographic Analysis")
    print("Match rates by state (top 20 by volume):")
    state_match_rates = compute_match_rates_by_state(
        crosswalk, hmda_originations, hmda_purchases
    )
    print(state_match_rates.head(20))
    results["state_match_rates"] = state_match_rates

    orig_rates = state_match_rates["origination_match_rate"].to_numpy()
    purch_rates = state_match_rates["purchase_match_rate"].to_numpy()
    valid_orig_rates = orig_rates[~np.isnan(orig_rates)]
    valid_purch_rates = purch_rates[~np.isnan(purch_rates)]
    if len(valid_orig_rates) > 0:
        print(
            f"\nOrigination match rate range (ptype>=5): "
            f"{min(valid_orig_rates):.1%} - {max(valid_orig_rates):.1%}"
        )
        print(f"Mean: {np.mean(valid_orig_rates):.1%}, Std: {np.std(valid_orig_rates):.3f}")
    if len(valid_purch_rates) > 0:
        print(
            f"Purchase match rate range: "
            f"{min(valid_purch_rates):.1%} - {max(valid_purch_rates):.1%}"
        )
        print(f"Mean: {np.mean(valid_purch_rates):.1%}, Std: {np.std(valid_purch_rates):.3f}")

    print_section("4. Loan Characteristics Analysis")
    print("Match rates by loan type:")
    loan_type_rates = compute_match_rates_by_loan_type(
        crosswalk, hmda_originations, hmda_purchases
    )
    loan_type_rates = loan_type_rates.with_columns(
        pl.col("loan_type")
        .replace_strict(LOAN_TYPE_LABELS, default="Unknown")
        .alias("loan_type_label")
    )
    print(loan_type_rates)
    results["loan_type_rates"] = loan_type_rates

    print("\nMatch rates by loan purpose:")
    loan_purpose_rates = compute_match_rates_by_loan_purpose(
        crosswalk, hmda_originations, hmda_purchases
    )
    loan_purpose_rates = loan_purpose_rates.with_columns(
        pl.col("loan_purpose")
        .replace_strict(LOAN_PURPOSE_LABELS, default="Unknown")
        .alias("loan_purpose_label")
    )
    print(loan_purpose_rates)
    results["loan_purpose_rates"] = loan_purpose_rates

    print("\nMatch rates by loan amount (bins):")
    loan_amount_rates = compute_match_rates_by_loan_amount_bin(
        crosswalk, hmda_originations, hmda_purchases
    )
    print(loan_amount_rates.head(20))
    results["loan_amount_rates"] = loan_amount_rates

    print_section("5. Purchaser Type Analysis (Key Insight)")
    print("Match rates by purchaser type (eligible originations only, ptype>=5):")
    purchaser_type_rates = compute_match_rates_by_purchaser_type(
        crosswalk, hmda_originations, hmda_purchases
    )
    purchaser_type_rates = purchaser_type_rates.with_columns(
        pl.col("purchaser_type")
        .replace_strict(PURCHASER_TYPE_LABELS, default="Unknown")
        .alias("purchaser_type_label")
    )
    print(purchaser_type_rates)
    results["purchaser_type_rates"] = purchaser_type_rates

    print("\nMatches by purchaser type:")
    purchaser_type_matches = compute_matches_by_purchaser_type(
        crosswalk, hmda_originations
    )
    purchaser_type_matches = purchaser_type_matches.with_columns(
        pl.col("purchaser_type_seller")
        .replace_strict(PURCHASER_TYPE_LABELS, default="Unknown")
        .alias("purchaser_type_label")
    )
    print(purchaser_type_matches)
    results["purchaser_type_matches"] = purchaser_type_matches

    gse_types = [1, 2, 3, 4]
    gse_matches = purchaser_type_matches.filter(
        pl.col("purchaser_type_seller").is_in(gse_types)
    )["match_count"].sum()
    non_gse_matches = purchaser_type_matches.filter(
        ~pl.col("purchaser_type_seller").is_in(gse_types)
    )["match_count"].sum()

    print(f"\nGSE matches: {gse_matches:,} ({gse_matches / n_matches:.1%})")
    print(f"Non-GSE matches: {non_gse_matches:,} ({non_gse_matches / n_matches:.1%})")

    print_section("6. Key Findings Summary")
    print("Match Coverage:")
    print(f"  - Total matched pairs: {n_matches:,}")
    print(
        f"  - Purchase match rate: {purchase_match_rate:.1%} of purchases find a matching origination"
    )
    print(
        f"  - Origination match rate (ptype>=5): {origination_match_rate:.1%} of eligible originations find a match"
    )

    round1_matches = round_stats.filter(pl.col("MatchRound") == 1)["match_count"].to_list()
    if round1_matches:
        print(
            f"  - Round 1 (same-year exact): {round1_matches[0]:,} ({round1_matches[0] / n_matches:.1%})"
        )

    print("\nDenominator Definitions:")
    print(
        "  - Purchase match rate: % of HMDA purchases (action_taken=6) with a matched origination"
    )
    print(
        "  - Origination match rate: % of originations with purchaser_type >= 5 with a matched purchase"
    )
    print("    (Excludes GSE sales: Fannie=1, Ginnie=2, Freddie=3, Farmer Mac=4)")

    if same_year_pct:
        print("\nTiming:")
        print(f"  - Same-year matches: {same_year_pct[0]:.1%}")
        print(f"  - Cross-year matches: {1 - same_year_pct[0]:.1%}")

    if (show_plots or save_plots) and HAS_MATPLOTLIB:
        print_section("7. Generating Visualizations")
        print("Plotting match statistics by round...")
        plot_match_statistics_by_round(
            round_stats,
            output_path=output_dir / "match_statistics_by_round.png"
            if save_plots
            else None,
            interactive=show_plots,
        )
        print("Plotting matches by year...")
        plot_matches_by_year(
            yearly_stats,
            output_path=output_dir / "matches_by_year.png" if save_plots else None,
            interactive=show_plots,
        )
        print("Plotting cross-year patterns...")
        plot_cross_year_patterns(
            cross_year_stats,
            output_path=output_dir / "cross_year_patterns.png" if save_plots else None,
            interactive=show_plots,
        )
        print("Plotting match rates by purchaser type...")
        plot_match_rates_by_purchaser_type(
            purchaser_type_rates,
            output_path=output_dir / "match_rates_by_purchaser_type.png"
            if save_plots
            else None,
            interactive=show_plots,
        )
        print("Plotting match rates by loan amount...")
        plot_match_rates_by_loan_amount(
            loan_amount_rates,
            output_path=output_dir / "match_rates_by_loan_amount.png"
            if save_plots
            else None,
            interactive=show_plots,
        )
        print("Plotting match rates by loan type...")
        plot_match_rates_by_loan_type(
            loan_type_rates,
            output_path=output_dir / "match_rates_by_loan_type.png"
            if save_plots
            else None,
            interactive=show_plots,
        )
        print("Plotting match rates by loan purpose...")
        plot_match_rates_by_loan_purpose(
            loan_purpose_rates,
            output_path=output_dir / "match_rates_by_loan_purpose.png"
            if save_plots
            else None,
            interactive=show_plots,
        )
        if save_plots:
            print(f"\nAll figures saved to: {output_dir}")

    return results


