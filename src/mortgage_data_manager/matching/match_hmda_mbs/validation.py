"""HMDA-MBS Validation Module.

This module provides validation analyses for the HMDA-MBS matching workflow,
which links HMDA loans to UMBS data through chain crosswalks and direct matching.

Key validation analyses:
1. HMDA Match Rates by Year - Retail GSE-sold loans matched to UMBS
2. UMBS Match Rates by Month - UMBS loans matched back to HMDA

The analyses use the combined crosswalks (chain + direct match) to compute
match rates from both HMDA and UMBS perspectives.

Usage:
    from mortgage_data_manager.matching.match_hmda_mbs.validation import run_validation

    # Run validation and generate figures
    results = run_validation(save_plots=True)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.matching.match_hmda_mbs.config import HMDAMBSMatchingConfig as Config

# =============================================================================
# Configuration
# =============================================================================

PROJECT_DIR = MortgageDataConfig.PROJECT_DIR
VALIDATION_OUTPUT_DIR = PROJECT_DIR / "docs" / "matching" / "figures" / "hmda_mbs"

# Color palette
HUNTER_GREEN = "#145A32"  # FNMA
BURNISHED_GOLD = "#D4AC0D"  # FHLMC

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
# Data Loading Functions
# =============================================================================


def load_hmda_retail_gse_loans(
    min_year: int = 2019,
    max_year: int = 2024,
) -> pl.DataFrame:
    """Load HMDA retail loans sold to GSEs (FNMA/FHLMC).

    Filters for:
    - action_taken = 1 (originated)
    - loan_type = 1 (conventional)
    - purchaser_type in [1, 3] (sold to FNMA or FHLMC)
    - submission_of_application = 1 (retail/direct submission)
    - Uses correct file_type for each year per FILE_TYPE_BY_YEAR

    Args:
        min_year: Minimum activity year
        max_year: Maximum activity year

    Returns:
        DataFrame with HMDAIndex, activity_year, purchaser_type
    """
    frames = []

    for year in range(min_year, max_year + 1):
        year_dir = Config.HMDA_SILVER_DIR / f"activity_year={year}"
        if not year_dir.exists():
            continue

        file_type = Config.FILE_TYPE_BY_YEAR.get(year, "c")

        lf = pl.scan_parquet(str(year_dir / "**/*.parquet"))

        # Apply filters
        lf = lf.filter(
            (pl.col("file_type") == file_type)
            & (pl.col("action_taken") == 1)
            & (pl.col("loan_type") == 1)
            & (pl.col("purchaser_type").is_in([1, 3]))
            & (pl.col("submission_of_application") == 1)
        )

        # Select needed columns
        lf = lf.select(["HMDAIndex", "activity_year", "purchaser_type"])

        frames.append(lf.collect())

    if not frames:
        return pl.DataFrame(
            schema={"HMDAIndex": pl.Utf8, "activity_year": pl.Int64, "purchaser_type": pl.Int64}
        )

    return pl.concat(frames, how="diagonal_relaxed")


def load_combined_crosswalks(
    agency: Literal["fnma", "fhlmc"],
    min_year: int = 2019,
    max_year: int = 2024,
) -> pl.DataFrame:
    """Load and combine chain and direct match crosswalks for an agency.

    Args:
        agency: Which GSE ("fnma" or "fhlmc")
        min_year: Minimum activity year
        max_year: Maximum activity year

    Returns:
        DataFrame with HMDAIndex, activity_year, umbs_loan_id (deduplicated by HMDAIndex)
    """
    frames = []

    # Load chain crosswalk
    chain_path = Config.get_output_path(agency, enriched=False)
    if chain_path.exists():
        chain = (
            pl.scan_parquet(str(chain_path))
            .filter(
                (pl.col("activity_year") >= min_year) & (pl.col("activity_year") <= max_year)
            )
            .select(["HMDAIndex", "activity_year", "umbs_loan_id"])
            .collect()
        )
        frames.append(chain)

    # Load direct match crosswalk
    direct_path = Config.get_direct_match_output_path(agency)
    if direct_path.exists():
        direct = (
            pl.scan_parquet(str(direct_path))
            .filter(
                (pl.col("activity_year") >= min_year) & (pl.col("activity_year") <= max_year)
            )
            .select(["HMDAIndex", "activity_year", "umbs_loan_id"])
            .collect()
        )
        frames.append(direct)

    if not frames:
        return pl.DataFrame(
            schema={"HMDAIndex": pl.Utf8, "activity_year": pl.Int64, "umbs_loan_id": pl.Utf8}
        )

    # Combine and dedupe by HMDAIndex (chain takes precedence)
    combined = pl.concat(frames, how="diagonal_relaxed")
    return combined.unique(subset=["HMDAIndex"], keep="first")


def load_umbs_data(
    agency: Literal["fnma", "fhlmc"],
    min_year: int = 2019,
    max_year: int = 2024,
) -> pl.DataFrame:
    """Load UMBS ILLD data with First Payment Date.

    Args:
        agency: Which GSE ("fnma" or "fhlmc")
        min_year: Minimum first payment year
        max_year: Maximum first payment year

    Returns:
        DataFrame with umbs_loan_id, first_payment_date (Int64 MMYYYY format)
    """
    illd_dir = Config.get_umbs_illd_dir(agency)
    if not illd_dir.exists():
        return pl.DataFrame(
            schema={"umbs_loan_id": pl.Utf8, "first_payment_date": pl.Int64}
        )

    files = list(illd_dir.glob("*.parquet"))
    if not files:
        return pl.DataFrame(
            schema={"umbs_loan_id": pl.Utf8, "first_payment_date": pl.Int64}
        )

    lf = pl.scan_parquet(
        [str(f) for f in files],
        missing_columns="insert",
        extra_columns="ignore",
    )

    # Extract year from First Payment Date (MMYYYY format)
    lf = lf.with_columns([(pl.col("First Payment Date") % 10000).alias("first_payment_year")])

    # Filter to relevant years
    lf = lf.filter(
        (pl.col("first_payment_year") >= min_year)
        & (pl.col("first_payment_year") <= max_year + 1)
    )

    # Select and rename
    lf = lf.select(
        [
            pl.col("Loan Identifier").cast(pl.Utf8).alias("umbs_loan_id"),
            pl.col("First Payment Date").alias("first_payment_date"),
        ]
    )

    # Keep unique loans
    return lf.unique(subset=["umbs_loan_id"]).collect()


# =============================================================================
# Match Rate Calculations
# =============================================================================


def compute_hmda_yearly_match_rates(
    hmda_data: pl.DataFrame,
    fnma_crosswalk: pl.DataFrame,
    fhlmc_crosswalk: pl.DataFrame,
) -> pl.DataFrame:
    """Compute HMDA match rates by year for FNMA and FHLMC.

    Denominator: Retail loans in HMDA with purchaser_type in [1, 3]
    Numerator: Loans that have a match to UMBS

    Args:
        hmda_data: HMDA retail GSE-sold loans
        fnma_crosswalk: Combined FNMA crosswalk
        fhlmc_crosswalk: Combined FHLMC crosswalk

    Returns:
        DataFrame with activity_year, fnma_total, fnma_matched, fnma_match_rate,
        fhlmc_total, fhlmc_matched, fhlmc_match_rate
    """
    # FNMA: purchaser_type = 1
    hmda_fnma = hmda_data.filter(pl.col("purchaser_type") == 1)
    fnma_matched_ids = set(fnma_crosswalk["HMDAIndex"].to_list())

    fnma_stats = (
        hmda_fnma.with_columns(
            pl.col("HMDAIndex").is_in(fnma_matched_ids).alias("is_matched")
        )
        .group_by("activity_year")
        .agg(
            [
                pl.len().alias("fnma_total"),
                pl.col("is_matched").sum().alias("fnma_matched"),
            ]
        )
    )

    # FHLMC: purchaser_type = 3
    hmda_fhlmc = hmda_data.filter(pl.col("purchaser_type") == 3)
    fhlmc_matched_ids = set(fhlmc_crosswalk["HMDAIndex"].to_list())

    fhlmc_stats = (
        hmda_fhlmc.with_columns(
            pl.col("HMDAIndex").is_in(fhlmc_matched_ids).alias("is_matched")
        )
        .group_by("activity_year")
        .agg(
            [
                pl.len().alias("fhlmc_total"),
                pl.col("is_matched").sum().alias("fhlmc_matched"),
            ]
        )
    )

    # Join and compute rates
    result = (
        fnma_stats.join(fhlmc_stats, on="activity_year", how="full", coalesce=True)
        .with_columns(
            [
                (pl.col("fnma_matched") / pl.col("fnma_total")).alias("fnma_match_rate"),
                (pl.col("fhlmc_matched") / pl.col("fhlmc_total")).alias("fhlmc_match_rate"),
            ]
        )
        .sort("activity_year")
    )

    return result


def compute_umbs_monthly_match_rates(
    fnma_umbs: pl.DataFrame,
    fhlmc_umbs: pl.DataFrame,
    fnma_crosswalk: pl.DataFrame,
    fhlmc_crosswalk: pl.DataFrame,
) -> pl.DataFrame:
    """Compute UMBS match rates by month for FNMA and FHLMC.

    Denominator: All UMBS loans with a given First Payment Date
    Numerator: UMBS loans that match back to HMDA

    Args:
        fnma_umbs: FNMA UMBS data
        fhlmc_umbs: FHLMC UMBS data
        fnma_crosswalk: Combined FNMA crosswalk
        fhlmc_crosswalk: Combined FHLMC crosswalk

    Returns:
        DataFrame with first_payment_date (MMYYYY), fnma_total, fnma_matched,
        fnma_match_rate, fhlmc_total, fhlmc_matched, fhlmc_match_rate
    """
    # FNMA
    fnma_matched_ids = set(fnma_crosswalk["umbs_loan_id"].to_list())

    fnma_stats = (
        fnma_umbs.with_columns(
            pl.col("umbs_loan_id").is_in(fnma_matched_ids).alias("is_matched")
        )
        .group_by("first_payment_date")
        .agg(
            [
                pl.len().alias("fnma_total"),
                pl.col("is_matched").sum().alias("fnma_matched"),
            ]
        )
    )

    # FHLMC
    fhlmc_matched_ids = set(fhlmc_crosswalk["umbs_loan_id"].to_list())

    fhlmc_stats = (
        fhlmc_umbs.with_columns(
            pl.col("umbs_loan_id").is_in(fhlmc_matched_ids).alias("is_matched")
        )
        .group_by("first_payment_date")
        .agg(
            [
                pl.len().alias("fhlmc_total"),
                pl.col("is_matched").sum().alias("fhlmc_matched"),
            ]
        )
    )

    # Join and compute rates
    # Sort by extracting year and month from MMYYYY format for proper chronological order
    result = (
        fnma_stats.join(fhlmc_stats, on="first_payment_date", how="full", coalesce=True)
        .with_columns(
            [
                (pl.col("fnma_matched") / pl.col("fnma_total")).alias("fnma_match_rate"),
                (pl.col("fhlmc_matched") / pl.col("fhlmc_total")).alias("fhlmc_match_rate"),
                # Extract year (last 4 digits) and month (first 1-2 digits) for sorting
                (pl.col("first_payment_date") % 10000).alias("_year"),
                (pl.col("first_payment_date") // 10000).alias("_month"),
            ]
        )
        .sort(["_year", "_month"])
        .drop(["_year", "_month"])
    )

    return result


# =============================================================================
# Plotting Functions
# =============================================================================


def plot_hmda_match_rates_by_year(
    yearly_data: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot HMDA match rates by year (FNMA and FHLMC lines).

    Args:
        yearly_data: DataFrame with activity_year, fnma_match_rate, fhlmc_match_rate
        output_path: Path to save the figure
        interactive: Whether to display interactively
    """
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(10, 6))

    years = yearly_data["activity_year"].to_list()
    fnma_rates = [r * 100 if r is not None else None for r in yearly_data["fnma_match_rate"].to_list()]
    fhlmc_rates = [r * 100 if r is not None else None for r in yearly_data["fhlmc_match_rate"].to_list()]

    ax.plot(years, fnma_rates, color=HUNTER_GREEN, linewidth=2, marker="o", markersize=8, label="FNMA")
    ax.plot(years, fhlmc_rates, color=BURNISHED_GOLD, linewidth=2, marker="s", markersize=8, label="FHLMC")

    ax.set_xlabel("Activity Year", fontsize=12)
    ax.set_ylabel("Match Rate (%)", fontsize=12)
    ax.set_title("HMDA-to-UMBS Match Rate by Year\n(Retail GSE-Sold Conventional Loans)", fontsize=14)

    ax.set_xticks(years)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=10)

    # Set reasonable y-limits
    all_rates = [r for r in fnma_rates + fhlmc_rates if r is not None]
    if all_rates:
        y_min = max(0, min(all_rates) - 5)
        y_max = min(100, max(all_rates) + 5)
        ax.set_ylim(y_min, y_max)

    plt.tight_layout()

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")

    if interactive:
        plt.show()

    plt.close(fig)


def plot_umbs_match_rates_by_month(
    monthly_data: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot UMBS match rates by month (FNMA and FHLMC lines).

    Args:
        monthly_data: DataFrame with first_payment_date, fnma_match_rate, fhlmc_match_rate
        output_path: Path to save the figure
        interactive: Whether to display interactively
    """
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(14, 6))

    # Convert MMYYYY to sortable format for x-axis labels
    months = monthly_data["first_payment_date"].to_list()

    # Create labels in "Mon YYYY" format
    month_names = {
        1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
        7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"
    }

    labels = []
    for m in months:
        if m is not None:
            mm = m // 10000  # Extract month
            yyyy = m % 10000  # Extract year
            labels.append(f"{month_names.get(mm, mm)} {yyyy}")
        else:
            labels.append("")

    fnma_rates = [r * 100 if r is not None else None for r in monthly_data["fnma_match_rate"].to_list()]
    fhlmc_rates = [r * 100 if r is not None else None for r in monthly_data["fhlmc_match_rate"].to_list()]

    x = range(len(labels))
    ax.plot(x, fnma_rates, color=HUNTER_GREEN, linewidth=1.5, alpha=0.8, label="FNMA")
    ax.plot(x, fhlmc_rates, color=BURNISHED_GOLD, linewidth=1.5, alpha=0.8, label="FHLMC")

    ax.set_xlabel("First Payment Date", fontsize=12)
    ax.set_ylabel("Match Rate (%)", fontsize=12)
    ax.set_title("UMBS-to-HMDA Match Rate by First Payment Date", fontsize=14)

    # Show every Nth label to avoid crowding
    n_labels = len(labels)
    step = max(1, n_labels // 12)
    ax.set_xticks(range(0, n_labels, step))
    ax.set_xticklabels([labels[i] for i in range(0, n_labels, step)], rotation=45, ha="right")

    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=10)

    # Set reasonable y-limits
    all_rates = [r for r in fnma_rates + fhlmc_rates if r is not None]
    if all_rates:
        y_min = max(0, min(all_rates) - 5)
        y_max = min(100, max(all_rates) + 5)
        ax.set_ylim(y_min, y_max)

    plt.tight_layout()

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
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
    min_year: int = 2019,
    max_year: int = 2024,
    output_dir: Path | None = None,
    save_plots: bool = True,
    show_plots: bool = False,
) -> dict[str, Any]:
    """Run all validation analyses for HMDA-MBS matching.

    Args:
        min_year: Minimum year to analyze
        max_year: Maximum year to analyze
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

    results: dict[str, Any] = {}

    # --- Section 1: Load Data ---
    print_section("1. Loading Data")

    print("Loading HMDA retail GSE-sold loans...")
    hmda_data = load_hmda_retail_gse_loans(min_year, max_year)
    print(f"  Total HMDA loans: {len(hmda_data):,}")
    print(f"  FNMA (purchaser_type=1): {hmda_data.filter(pl.col('purchaser_type') == 1).height:,}")
    print(f"  FHLMC (purchaser_type=3): {hmda_data.filter(pl.col('purchaser_type') == 3).height:,}")
    results["hmda_data"] = hmda_data

    print("\nLoading FNMA crosswalks (chain + direct)...")
    fnma_crosswalk = load_combined_crosswalks("fnma", min_year, max_year)
    print(f"  FNMA matched pairs: {len(fnma_crosswalk):,}")
    results["fnma_crosswalk"] = fnma_crosswalk

    print("\nLoading FHLMC crosswalks (chain + direct)...")
    fhlmc_crosswalk = load_combined_crosswalks("fhlmc", min_year, max_year)
    print(f"  FHLMC matched pairs: {len(fhlmc_crosswalk):,}")
    results["fhlmc_crosswalk"] = fhlmc_crosswalk

    print("\nLoading FNMA UMBS data...")
    fnma_umbs = load_umbs_data("fnma", min_year, max_year)
    print(f"  FNMA UMBS loans: {len(fnma_umbs):,}")
    results["fnma_umbs"] = fnma_umbs

    print("\nLoading FHLMC UMBS data...")
    fhlmc_umbs = load_umbs_data("fhlmc", min_year, max_year)
    print(f"  FHLMC UMBS loans: {len(fhlmc_umbs):,}")
    results["fhlmc_umbs"] = fhlmc_umbs

    # --- Section 2: HMDA Match Rates by Year ---
    print_section("2. HMDA Match Rates by Year (Figure 1)")

    yearly_data = compute_hmda_yearly_match_rates(
        hmda_data, fnma_crosswalk, fhlmc_crosswalk
    )
    print("Match rates by activity year:")
    print(yearly_data)
    results["yearly_data"] = yearly_data

    # Print summary
    if len(yearly_data) > 0:
        fnma_avg = yearly_data["fnma_match_rate"].mean()
        fhlmc_avg = yearly_data["fhlmc_match_rate"].mean()
        if fnma_avg is not None:
            print(f"\nFNMA average match rate: {fnma_avg:.1%}")
        if fhlmc_avg is not None:
            print(f"FHLMC average match rate: {fhlmc_avg:.1%}")

    # --- Section 3: UMBS Match Rates by Month ---
    print_section("3. UMBS Match Rates by Month (Figure 2)")

    monthly_data = compute_umbs_monthly_match_rates(
        fnma_umbs, fhlmc_umbs, fnma_crosswalk, fhlmc_crosswalk
    )
    print(f"Monthly data points: {len(monthly_data)}")
    print("\nFirst 10 rows:")
    print(monthly_data.head(10))
    results["monthly_data"] = monthly_data

    # Print summary
    if len(monthly_data) > 0:
        fnma_avg = monthly_data["fnma_match_rate"].mean()
        fhlmc_avg = monthly_data["fhlmc_match_rate"].mean()
        if fnma_avg is not None:
            print(f"\nFNMA average match rate: {fnma_avg:.1%}")
        if fhlmc_avg is not None:
            print(f"FHLMC average match rate: {fhlmc_avg:.1%}")

    # --- Section 4: Generate Plots ---
    if (save_plots or show_plots) and HAS_MATPLOTLIB:
        print_section("4. Generating Visualizations")

        print("1. Plotting HMDA match rates by year...")
        plot_hmda_match_rates_by_year(
            yearly_data,
            output_path=output_dir / "hmda_match_rates_by_year.png" if save_plots else None,
            interactive=show_plots,
        )

        print("2. Plotting UMBS match rates by month...")
        plot_umbs_match_rates_by_month(
            monthly_data,
            output_path=output_dir / "umbs_match_rates_by_month.png" if save_plots else None,
            interactive=show_plots,
        )

    print("\nDone! Validation complete.")
    if save_plots:
        print(f"Figures saved to: {output_dir}")

    return results


