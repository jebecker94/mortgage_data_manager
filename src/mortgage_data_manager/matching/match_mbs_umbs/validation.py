"""MBS Match Validation Module.

This module provides validation analyses for the MBS matching workflow for both
FNMA and FHLMC MBS-UMBS matching. This workflow links the same GSE loans across two
different disclosure formats:
- MBS: Monthly disclosure data
- UMBS: Loan-level security disclosure (ILLD)

Key validation analyses:
1. Match Statistics Summary
2. Temporal Analysis (match/unmatched rates by month)
3. Match Rates by LTV (interesting behavior at extremes expected)
4. Match Rates by DTI (interesting behavior at extremes expected)
5. Match Rates by FICO (interesting behavior at extremes expected)

Usage:
    from mortgage_data_manager.matching.match_mbs_umbs.validation import run_validation

    # FNMA validation
    results = run_validation(gse="fnma")

    # FHLMC validation
    results = run_validation(gse="fhlmc")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.matching.match_mbs_umbs.config import (
    CROSSWALK_OUTPUT_DIR,
    MBSUMBSConfig,
    get_fhlmc_config,
    get_umbs_bronze_dir,
)

# First month of ILLD issuance disclosure; pre-this loans come from the monthly snapshot.
ISSUANCE_START_YM = MBSUMBSConfig.UMBS_ISSUANCE_START[:7]  # "2019-06"

# =============================================================================
# Configuration
# =============================================================================

# Project directories
PROJECT_DIR = MortgageDataConfig.PROJECT_DIR
DATA_DIR = CROSSWALK_OUTPUT_DIR

# FNMA data paths
FNMA_MBS_DIR = MortgageDataConfig.get_subpackage_data_dir("fnma") / "silver" / "issuances"
FNMA_UMBS_DIR = MortgageDataConfig.get_subpackage_data_dir("umbs") / "bronze" / "FNMA" / "FNM_ILLD"

# FHLMC data paths
FHLMC_CONFIG = get_fhlmc_config()
FHLMC_MBS_DIR = FHLMC_CONFIG.FHLMC_BRONZE_ORIGINATION
FHLMC_UMBS_DIR = get_umbs_bronze_dir() / "FHLMC" / "FRE_ILLD"

# Output directory for figures
VALIDATION_OUTPUT_DIR = PROJECT_DIR / "docs" / "matching" / "figures" / "mbs_umbs"


def get_gse_paths(gse: str) -> tuple[Path, Path, Path]:
    """Get MBS dir, UMBS dir, and crosswalk path for the specified GSE.

    Args:
        gse: Either "fnma" or "fhlmc"

    Returns:
        Tuple of (mbs_dir, umbs_dir, crosswalk_path)
    """
    gse = gse.lower()
    if gse == "fnma":
        return FNMA_MBS_DIR, FNMA_UMBS_DIR, CROSSWALK_OUTPUT_DIR / "fnma_crosswalk.parquet"
    elif gse == "fhlmc":
        return FHLMC_MBS_DIR, FHLMC_UMBS_DIR, CROSSWALK_OUTPUT_DIR / "fhlmc_crosswalk.parquet"
    else:
        raise ValueError(f"Unknown GSE: {gse}. Must be 'fnma' or 'fhlmc'")


# Color palette (Jonathan's palette)
MIDNIGHT_BLUE = "#0E2F44"
HUNTER_GREEN = "#145A32"
BURNISHED_GOLD = "#D4AC0D"
TERRACOTTA = "#BA4A00"
MULBERRY = "#900C3F"
IMPERIAL_PURPLE = "#5B2C6F"
DEEP_TEAL = "#117864"
BLUE_GREY = "#85929E"
SLATE = "#566573"

# Dataset-specific colors (primary: Hunter Green & Gold)
MBS_COLOR = HUNTER_GREEN
UMBS_COLOR = BURNISHED_GOLD
MATCHED_COLOR = HUNTER_GREEN
UNMATCHED_COLOR = TERRACOTTA
DENSITY_COLOR = BLUE_GREY
REFERENCE_COLOR_1 = TERRACOTTA
REFERENCE_COLOR_2 = MIDNIGHT_BLUE

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


def load_crosswalk(gse: str = "fnma") -> pl.DataFrame:
    """Load the MBS-UMBS crosswalk for the specified GSE.

    Args:
        gse: Either "fnma" or "fhlmc"
    """
    _, _, crosswalk_path = get_gse_paths(gse)
    if not crosswalk_path.exists():
        raise FileNotFoundError(
            f"Crosswalk not found: {crosswalk_path}\n"
            f"Run the matching first: mortgage-data match mbs-umbs {gse}"
        )
    return pl.read_parquet(crosswalk_path)


def load_mbs_data(gse: str = "fnma") -> pl.LazyFrame:
    """Load MBS data with key columns for analysis.

    Args:
        gse: Either "fnma" or "fhlmc"

    Returns:
        LazyFrame with standardized column names: Loan Identifier, LTV, CLTV, DTI, FICO
    """
    mbs_dir, _, _ = get_gse_paths(gse)

    if not mbs_dir.exists():
        raise FileNotFoundError(f"MBS data not found: {mbs_dir}")

    if gse.lower() == "fnma":
        # FNMA column names
        cols = [
            "Loan Identifier",
            "Monthly Reporting Period",
            "First Payment Date",
            "Original Loan to Value Ratio (LTV)",
            "Original Combined Loan to Value Ratio (CLTV)",
            "Debt-To-Income (DTI)",
            "Borrower Credit Score at Origination",
            "Original Interest Rate",
            "Original UPB",
            "Origination Date",
            "Property State",
        ]
        rename_map = {
            "Original Loan to Value Ratio (LTV)": "LTV",
            "Original Combined Loan to Value Ratio (CLTV)": "CLTV",
            "Debt-To-Income (DTI)": "DTI",
            "Borrower Credit Score at Origination": "FICO",
        }
    else:  # fhlmc
        # FHLMC column names
        cols = [
            "Loan Sequence Number",
            "First Payment Date",
            "Original Loan-to-Value (LTV)",
            "Original Combined Loan-to-Value (CLTV)",
            "Original Debt-to-Income (DTI) Ratio",
            "Credit Score",
            "Original Interest Rate",
            "Original UPB",
            "Property State",
        ]
        rename_map = {
            "Loan Sequence Number": "Loan Identifier",
            "Original Loan-to-Value (LTV)": "LTV",
            "Original Combined Loan-to-Value (CLTV)": "CLTV",
            "Original Debt-to-Income (DTI) Ratio": "DTI",
            "Credit Score": "FICO",
        }

    lf = pl.scan_parquet(
        str(mbs_dir / "**/*.parquet"),
        missing_columns="insert",
        extra_columns="ignore",
    )

    # Get available columns
    available = set(lf.collect_schema().names())
    use_cols = [c for c in cols if c in available]

    lf = lf.select(use_cols).rename(rename_map)

    # Normalized First Payment year-month ("YYYY-MM") so non-temporal validation stats can be
    # restricted to the clean full-data window (First Payment Date >= 2019-06). FNMA stores it
    # as an MMYYYY string; FHLMC as a Date.
    if "First Payment Date" in lf.collect_schema().names():
        if gse.lower() == "fnma":
            fpd = pl.col("First Payment Date").cast(pl.Utf8).str.zfill(6)
            ym = fpd.str.slice(2, 4) + "-" + fpd.str.slice(0, 2)
        else:
            ym = pl.col("First Payment Date").dt.strftime("%Y-%m")
        lf = lf.with_columns(ym.alias("first_payment_ym"))

    return lf


def load_umbs_data(gse: str = "fnma") -> pl.DataFrame:
    """Load UMBS ILLD data with key columns for analysis.

    Args:
        gse: Either "fnma" or "fhlmc"

    Returns:
        DataFrame (not LazyFrame) due to schema inconsistencies across files.
        UMBS data uses 'First Payment Date' in MMYYYY integer format.
        Returns columns: Loan Identifier, year_month, LTV, CLTV, DTI, FICO

    Filters:
    - Excludes modifications (Loan Purpose = 'M')
    - Combines the post-2019 ILLD population with the first monthly snapshot (FNM_MLLD / FU),
      split on First Payment Date (ILLD >= 2019-06, snapshot < 2019-06) so the UMBS universe
      mirrors the combined matcher and spans pre-2019 cohorts.
    """
    _, umbs_dir, _ = get_gse_paths(gse)

    if not umbs_dir.exists():
        raise FileNotFoundError(f"UMBS data not found: {umbs_dir}")

    # ILLD monthly files (post-2019) plus the first monthly snapshot (pre-2019 recovery). Each
    # source is tagged so we can apply the complementary First Payment Date filter below.
    sources = [(f, False) for f in sorted(umbs_dir.glob("*.parquet"))]
    snapshot_file = MBSUMBSConfig.get_umbs_snapshot_file(gse)
    if snapshot_file is not None:
        sources.append((snapshot_file, True))
    files = sources

    # Columns we want to load (same for both FNMA and FHLMC)
    cols = [
        "Loan Identifier",
        "First Payment Date",
        "Loan-To-Value (LTV)",
        "Combined Loan-To-Value (CLTV)",
        "Debt-To-Income (DTI)",
        "Borrower Credit Score",
        "Loan Purpose",  # For filtering out modifications
    ]

    # Expected final columns with standardized names
    final_cols = [
        "Loan Identifier",
        "year_month",
        "LTV",
        "CLTV",
        "DTI",
        "FICO",
    ]

    dfs = []
    for file, is_snapshot in files:
        df = pl.read_parquet(file)

        # Select columns that exist
        available = [c for c in cols if c in df.columns]
        if "Loan Identifier" not in available or "First Payment Date" not in available:
            continue

        df_subset = df.select(available)

        # Filter out modifications (Loan Purpose = 'M')
        if "Loan Purpose" in df_subset.columns:
            df_subset = df_subset.filter(
                (pl.col("Loan Purpose") != "M") | pl.col("Loan Purpose").is_null()
            )

        # Convert First Payment Date to year_month format
        df_subset = (
            df_subset.with_columns(pl.col("First Payment Date").cast(pl.Utf8).alias("fpm_str"))
            .with_columns(
                [
                    pl.col("fpm_str").str.tail(4).alias("year"),
                    pl.col("fpm_str")
                    .str.head(pl.col("fpm_str").str.len_chars() - 4)
                    .str.pad_start(2, "0")
                    .alias("month"),
                ]
            )
            .with_columns((pl.col("year") + "-" + pl.col("month")).alias("year_month"))
            .drop(["fpm_str", "year", "month", "First Payment Date"])
        )

        # Split on the ILLD boundary: the snapshot supplies pre-2019, ILLD supplies the rest,
        # so the two sources never double-count loans issued around June 2019.
        if is_snapshot:
            df_subset = df_subset.filter(pl.col("year_month") < ISSUANCE_START_YM)
        else:
            df_subset = df_subset.filter(pl.col("year_month") >= ISSUANCE_START_YM)

        # Drop Loan Purpose if it exists (no longer needed)
        if "Loan Purpose" in df_subset.columns:
            df_subset = df_subset.drop("Loan Purpose")

        # Rename columns to standardized names (only rename columns that exist)
        rename_map = {
            "Loan-To-Value (LTV)": "LTV",
            "Combined Loan-To-Value (CLTV)": "CLTV",
            "Debt-To-Income (DTI)": "DTI",
            "Borrower Credit Score": "FICO",
        }
        rename_map = {k: v for k, v in rename_map.items() if k in df_subset.columns}
        df_subset = df_subset.rename(rename_map)

        # Ensure all files have same columns (with nulls for missing ones)
        for col in final_cols:
            if col not in df_subset.columns:
                df_subset = df_subset.with_columns(pl.lit(None).alias(col))

        dfs.append(df_subset.select(final_cols))

    return pl.concat(dfs, how="diagonal")


def get_mbs_monthly_counts() -> pl.DataFrame:
    """Get MBS loan counts by monthly reporting period (issuances only)."""
    lf = pl.scan_parquet(
        str(FNMA_MBS_DIR / "**/*.parquet"),
        missing_columns="insert",
        extra_columns="ignore",
    )

    return (
        lf.select(["Loan Identifier", "Monthly Reporting Period"])
        .unique()
        .with_columns(
            # Convert MMYYYY to YYYY-MM for proper sorting
            (
                pl.col("Monthly Reporting Period").str.slice(2, 4)
                + "-"
                + pl.col("Monthly Reporting Period").str.slice(0, 2)
            ).alias("year_month")
        )
        .group_by("year_month")
        .agg(pl.len().alias("mbs_count"))
        .sort("year_month")
        .collect()
    )


# =============================================================================
# Match Rate Analysis
# =============================================================================


def compute_match_statistics(
    crosswalk: pl.DataFrame,
    mbs_data: pl.LazyFrame,
    umbs_data: pl.DataFrame,
) -> dict[str, Any]:
    """Compute overall match statistics within the provided (already windowed) data.

    Match counts are the intersection of the provided loans with the crosswalk, so the rate
    is matched-in-window / total-in-window. Callers pass clean-window data (First Payment Date
    >= 2019-06) for the headline non-temporal stats, giving an interpretable denominator.
    """
    matched_mbs_ids = set(crosswalk["Loan Sequence Number (MBS)"].to_list())
    matched_umbs_ids = set(crosswalk["Loan Sequence Number (UMBS)"].to_list())

    mbs_ids = (
        mbs_data.select(pl.col("Loan Identifier").cast(pl.Utf8))
        .unique()
        .collect()["Loan Identifier"]
    )
    umbs_ids = umbs_data.select(pl.col("Loan Identifier").cast(pl.Utf8)).unique()["Loan Identifier"]

    n_total_mbs = mbs_ids.len()
    n_total_umbs = umbs_ids.len()
    n_matched_mbs = int(mbs_ids.is_in(matched_mbs_ids).sum())
    n_matched_umbs = int(umbs_ids.is_in(matched_umbs_ids).sum())

    return {
        # In-window matched pairs (1:1 matches => matched MBS loans in window == pairs in window).
        "n_matched_pairs": n_matched_mbs,
        "n_matched_mbs": n_matched_mbs,
        "n_matched_umbs": n_matched_umbs,
        "n_total_mbs": n_total_mbs,
        "n_total_umbs": n_total_umbs,
        "mbs_match_rate": n_matched_mbs / n_total_mbs if n_total_mbs > 0 else 0,
        "umbs_match_rate": n_matched_umbs / n_total_umbs if n_total_umbs > 0 else 0,
    }


def compute_match_rates_by_characteristic(
    crosswalk: pl.DataFrame,
    mbs_data: pl.LazyFrame,
    umbs_data: pl.DataFrame,
    mbs_column: str,
    umbs_column: str,
    bin_size: int | float,
    min_val: float | None = None,
    max_val: float | None = None,
) -> pl.DataFrame:
    """Compute match rates by a loan characteristic from both MBS and UMBS perspectives.

    Args:
        crosswalk: The MBS-UMBS crosswalk
        mbs_data: MBS data with loan characteristics
        umbs_data: UMBS data with loan characteristics
        mbs_column: Column name in MBS data to bin by
        umbs_column: Column name in UMBS data to bin by
        bin_size: Size of bins
        min_val: Minimum value to include
        max_val: Maximum value to include

    Returns:
        DataFrame with columns: bin, mbs_total, umbs_total, matched,
                                mbs_match_rate, umbs_match_rate
    """
    # Get MBS data with the characteristic
    mbs_with_char = (
        mbs_data.filter(pl.col(mbs_column).is_not_null())
        .select(["Loan Identifier", mbs_column])
        .unique(subset=["Loan Identifier"])
        .collect()
    )

    # Apply range filter if specified
    if min_val is not None:
        mbs_with_char = mbs_with_char.filter(pl.col(mbs_column) >= min_val)
    if max_val is not None:
        mbs_with_char = mbs_with_char.filter(pl.col(mbs_column) <= max_val)

    # Create bins for MBS loans
    mbs_with_char = mbs_with_char.with_columns(
        ((pl.col(mbs_column) / bin_size).floor() * bin_size).alias("bin")
    )

    # Get matched IDs for filtering
    matched_mbs_ids = set(crosswalk["Loan Sequence Number (MBS)"].to_list())
    matched_umbs_ids = set(crosswalk["Loan Sequence Number (UMBS)"].to_list())

    # Count MBS perspective: total MBS loans and matched MBS loans per bin
    mbs_counts = (
        mbs_with_char.with_columns(
            pl.col("Loan Identifier").is_in(matched_mbs_ids).alias("is_matched")
        )
        .group_by("bin")
        .agg(
            [
                pl.len().alias("mbs_total"),
                pl.col("is_matched").sum().alias("mbs_matched"),
            ]
        )
    )

    # Load ALL UMBS data with the characteristic
    umbs_with_char = (
        umbs_data.filter(pl.col(umbs_column).is_not_null())
        .select(["Loan Identifier", umbs_column])
        .unique(subset=["Loan Identifier"])
    )

    # Apply range filter
    if min_val is not None:
        umbs_with_char = umbs_with_char.filter(pl.col(umbs_column) >= min_val)
    if max_val is not None:
        umbs_with_char = umbs_with_char.filter(pl.col(umbs_column) <= max_val)

    # Create bins for UMBS loans
    umbs_with_char = umbs_with_char.with_columns(
        ((pl.col(umbs_column) / bin_size).floor() * bin_size).alias("bin")
    )

    # Count UMBS perspective: total UMBS loans and matched UMBS loans per bin
    umbs_counts = (
        umbs_with_char.with_columns(
            pl.col("Loan Identifier").cast(pl.Utf8).is_in(matched_umbs_ids).alias("is_matched")
        )
        .group_by("bin")
        .agg(
            [
                pl.len().alias("umbs_total"),
                pl.col("is_matched").sum().alias("umbs_matched"),
            ]
        )
    )

    # Merge both perspectives and filter out null bins
    result = (
        mbs_counts.join(umbs_counts, on="bin", how="full")
        .filter(pl.col("bin").is_not_null())
        .with_columns(
            [
                (pl.col("mbs_matched") / pl.col("mbs_total")).alias("mbs_match_rate"),
                (pl.col("umbs_matched") / pl.col("umbs_total")).alias("umbs_match_rate"),
                pl.col("mbs_matched").alias("matched"),  # For backwards compatibility
            ]
        )
        .sort("bin")
    )

    return result


def compute_match_rates_by_month(
    crosswalk: pl.DataFrame,
    umbs_data: pl.DataFrame,
    gse: str = "fnma",
) -> pl.DataFrame:
    """Compute match rates by month from both MBS and UMBS perspectives.

    Args:
        crosswalk: The MBS-UMBS crosswalk
        umbs_data: UMBS data with year_month column
        gse: Either "fnma" or "fhlmc"

    Returns both MBS match rate (matched_mbs / total_mbs) and
    UMBS match rate (matched_umbs / total_umbs) for each month,
    along with a first_payment_date column (derived from year_month).
    """
    gse = gse.lower()
    mbs_dir, _, _ = get_gse_paths(gse)

    # Load MBS issuance data keyed on origination (First Payment Date) so the temporal axis is
    # the origination cohort — that is where the pre-2019 survivorship gradient shows up.
    # FNMA First Payment Date is MMYYYY string; FHLMC is a date.
    if gse == "fnma":
        mbs_with_date = (
            pl.scan_parquet(
                str(mbs_dir / "**/*.parquet"), missing_columns="insert", extra_columns="ignore"
            )
            .select(["Loan Identifier", "First Payment Date"])
            .unique()
            .with_columns(
                # Convert MMYYYY to YYYY-MM format for proper sorting
                (
                    pl.col("First Payment Date").str.slice(2, 4)
                    + "-"
                    + pl.col("First Payment Date").str.slice(0, 2)
                ).alias("year_month")
            )
            .collect()
        )
    else:  # fhlmc
        mbs_with_date = (
            pl.scan_parquet(
                str(mbs_dir / "**/*.parquet"), missing_columns="insert", extra_columns="ignore"
            )
            .select(["Loan Sequence Number", "First Payment Date"])
            .rename({"Loan Sequence Number": "Loan Identifier"})
            .unique()
            .with_columns(
                # First Payment Date is a date type, convert to YYYY-MM format
                pl.col("First Payment Date").dt.strftime("%Y-%m").alias("year_month")
            )
            .collect()
        )

    # UMBS data already has year_month column from load_umbs_data and now spans pre-2019 via the
    # snapshot, so no lower-bound filter — the inner join on year_month below intersects months.
    umbs_with_date = umbs_data.unique(subset=["Loan Identifier"])

    # Join crosswalk with MBS dates to get matched loan dates
    crosswalk.join(
        mbs_with_date.select(["Loan Identifier", "year_month"]),
        left_on="Loan Sequence Number (MBS)",
        right_on="Loan Identifier",
        how="inner",
    )

    # Get matched IDs
    matched_mbs_ids = set(crosswalk["Loan Sequence Number (MBS)"].to_list())
    matched_umbs_ids = set(crosswalk["Loan Sequence Number (UMBS)"].to_list())

    # MBS perspective: count all MBS loans and matched MBS loans by month
    mbs_counts = (
        mbs_with_date.with_columns(
            pl.col("Loan Identifier").is_in(matched_mbs_ids).alias("is_matched")
        )
        .group_by("year_month")
        .agg(
            [
                pl.len().alias("mbs_total"),
                pl.col("is_matched").sum().alias("mbs_matched"),
            ]
        )
    )

    # UMBS perspective: count ALL UMBS loans by month and identify matched ones
    # Note: UMBS Loan Identifier is Int64, crosswalk has String, so convert for comparison
    umbs_counts = (
        umbs_with_date.with_columns(
            pl.col("Loan Identifier").cast(pl.Utf8).is_in(matched_umbs_ids).alias("is_matched")
        )
        .group_by("year_month")
        .agg(
            [
                pl.len().alias("umbs_total"),
                pl.col("is_matched").sum().alias("umbs_matched"),
            ]
        )
    )

    # Merge and compute rates
    # Use inner join to only include months where both MBS and UMBS data exist
    result = (
        mbs_counts.join(umbs_counts, on="year_month", how="inner")
        .with_columns(
            [
                (pl.col("mbs_matched") / pl.col("mbs_total")).alias("mbs_match_rate"),
                (pl.col("umbs_matched") / pl.col("umbs_total")).alias("umbs_match_rate"),
                # Create first_payment_date column (YYYY-MM-01 format for x-axis)
                (pl.col("year_month") + "-01").alias("first_payment_date"),
            ]
        )
        .sort("year_month")
    )

    return result


# Legacy function names for backwards compatibility
def compute_match_rates_by_year(
    crosswalk: pl.DataFrame,
    source_data: pl.LazyFrame,
) -> pl.DataFrame:
    """Calculate match rates by year.

    For MBS-UMBS matching, use compute_match_rates_by_month instead.
    """
    umbs_data = load_umbs_data()
    monthly_data = compute_match_rates_by_month(crosswalk, umbs_data)

    # Aggregate to yearly
    return (
        monthly_data.with_columns(pl.col("year_month").str.slice(0, 4).alias("year"))
        .group_by("year")
        .agg(
            [
                pl.col("mbs_total").sum(),
                pl.col("mbs_matched").sum(),
                pl.col("umbs_total").sum(),
                pl.col("umbs_matched").sum(),
            ]
        )
        .with_columns(
            [
                (pl.col("mbs_matched") / pl.col("mbs_total")).alias("mbs_match_rate"),
                (pl.col("umbs_matched") / pl.col("umbs_total")).alias("umbs_match_rate"),
            ]
        )
        .sort("year")
    )


def compute_match_rates_by_state(
    crosswalk: pl.DataFrame,
    source_data: pl.LazyFrame,
) -> pl.DataFrame:
    """Calculate match rates by state.

    Note: State-level analysis is limited for MBS-UMBS matching since
    both sources are from FNMA and should have identical state distributions.
    """
    # Get MBS data with state
    mbs_with_state = (
        source_data.filter(pl.col("Property State").is_not_null())
        .select(["Loan Identifier", "Property State"])
        .unique(subset=["Loan Identifier"])
        .collect()
    )

    # Get matched IDs
    matched_mbs_ids = set(crosswalk["Loan Sequence Number (MBS)"].to_list())

    # Count by state
    result = (
        mbs_with_state.with_columns(
            pl.col("Loan Identifier").is_in(matched_mbs_ids).alias("is_matched")
        )
        .group_by("Property State")
        .agg(
            [
                pl.len().alias("total"),
                pl.col("is_matched").sum().alias("matched"),
            ]
        )
        .with_columns((pl.col("matched") / pl.col("total")).alias("match_rate"))
        .sort("total", descending=True)
    )

    return result


def load_data(
    crosswalk_path: Path | None = None,
) -> tuple[pl.DataFrame, pl.LazyFrame, pl.DataFrame]:
    """Load crosswalk and source data.

    Returns:
        Tuple of (crosswalk, mbs_data, umbs_data)
    """
    crosswalk = load_crosswalk()
    mbs_data = load_mbs_data()
    umbs_data = load_umbs_data()
    return crosswalk, mbs_data, umbs_data


# =============================================================================
# Plotting Functions
# =============================================================================


def plot_match_rates_by_month(
    monthly_data: pl.DataFrame,
    gse: str = "fnma",
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot match rates by first payment date (both MBS and UMBS perspectives).

    Args:
        monthly_data: DataFrame with mbs_match_rate, umbs_match_rate, first_payment_date
        gse: Either "fnma" or "fhlmc" (used for title)
        output_path: Path to save the figure
        interactive: Whether to display the plot interactively
    """
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    gse_upper = gse.upper()
    fig, ax = plt.subplots(figsize=(14, 6))

    # Extract dates and rates
    dates = []
    mbs_valid = []
    umbs_valid = []

    for row in monthly_data.iter_rows(named=True):
        fpd = row.get("first_payment_date")
        mbs_rate = row.get("mbs_match_rate")
        embs_rate = row.get("umbs_match_rate")

        if fpd is not None:
            dates.append(str(fpd))
            mbs_valid.append(mbs_rate * 100 if mbs_rate is not None else None)
            umbs_valid.append(embs_rate * 100 if embs_rate is not None else None)

    if not dates:
        print("No valid date data found for plotting")
        return

    # Plot both lines (no fill)
    ax.plot(dates, mbs_valid, color=MBS_COLOR, linewidth=2, alpha=0.8, label="MBS Match Rate")
    ax.plot(dates, umbs_valid, color=UMBS_COLOR, linewidth=2, alpha=0.8, label="UMBS Match Rate")

    ax.set_xlabel("First Payment Date")
    ax.set_ylabel("Match Rate (%)")
    ax.set_title(f"{gse_upper} MBS-UMBS Match Rates by First Payment Date")

    # Rotate x-axis labels
    n_labels = len(dates)
    step = max(1, n_labels // 20)
    ax.set_xticks(range(0, n_labels, step))
    ax.set_xticklabels([dates[i] for i in range(0, n_labels, step)], rotation=45, ha="right")

    ax.grid(True, alpha=0.3)
    ax.set_ylim(80, 100)  # Start y-axis at 80%
    ax.legend()

    plt.tight_layout()
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_match_rates_by_characteristic(
    rates_df: pl.DataFrame,
    xlabel: str,
    title: str,
    output_path: Path | None = None,
    interactive: bool = True,
    x_scale: float = 1.0,
    show_density: bool = True,
) -> None:
    """Plot match rates by characteristic (both MBS and UMBS perspectives)."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(12, 6))

    x = np.array(rates_df["bin"].to_list()) * x_scale
    mbs_match_rates = np.array(
        [r * 100 if r is not None else np.nan for r in rates_df["mbs_match_rate"].to_list()]
    )
    umbs_match_rates = np.array(
        [r * 100 if r is not None else np.nan for r in rates_df["umbs_match_rate"].to_list()]
    )
    mbs_totals = np.array(rates_df["mbs_total"].to_list())

    # Secondary axis for density (using MBS totals)
    if show_density:
        ax2 = ax.twinx()
        pdf = mbs_totals / mbs_totals.sum()
        ax2.fill_between(x, pdf, alpha=0.2, color=DENSITY_COLOR, label="Density")
        ax2.set_ylabel("Density (PDF)", color=DENSITY_COLOR)
        ax2.tick_params(axis="y", labelcolor=DENSITY_COLOR)
        ax2.set_ylim(0, pdf.max() * 1.5)

    # Plot both match rates (no markers - too many points with fine bins)
    ax.plot(x, mbs_match_rates, color=MBS_COLOR, linewidth=1.5, label="MBS Match Rate")
    ax.plot(x, umbs_match_rates, color=UMBS_COLOR, linewidth=1.5, label="UMBS Match Rate")

    ax.set_xlabel(xlabel)
    ax.set_ylabel("Match Rate (%)")
    ax.set_title(title)

    # Set reasonable y-limits
    all_rates = np.concatenate([mbs_match_rates, umbs_match_rates])
    valid_rates = all_rates[~np.isnan(all_rates)]
    if len(valid_rates) > 0:
        y_min = max(0, valid_rates.min() - 5)
        y_max = min(100, valid_rates.max() + 5)
        ax.set_ylim(y_min, y_max)

    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")

    plt.tight_layout()
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_match_rates_extremes_comparison(
    ltv_rates: pl.DataFrame,
    dti_rates: pl.DataFrame,
    fico_rates: pl.DataFrame,
    gse: str = "fnma",
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot a 3-panel comparison of match rates at extremes (both perspectives).

    Args:
        ltv_rates: DataFrame with LTV match rates
        dti_rates: DataFrame with DTI match rates
        fico_rates: DataFrame with FICO match rates
        gse: Either "fnma" or "fhlmc" (used for title)
        output_path: Path to save the figure
        interactive: Whether to display the plot interactively
    """
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    gse_upper = gse.upper()
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))

    # LTV
    ax = axes[0]
    x = np.array(ltv_rates["bin"].to_list())
    mbs_y = np.array(
        [r * 100 if r is not None else np.nan for r in ltv_rates["mbs_match_rate"].to_list()]
    )
    umbs_y = np.array(
        [r * 100 if r is not None else np.nan for r in ltv_rates["umbs_match_rate"].to_list()]
    )

    ax.plot(x, mbs_y, color=MBS_COLOR, linewidth=1.5, label="MBS Match Rate")
    ax.plot(x, umbs_y, color=UMBS_COLOR, linewidth=1.5, label="UMBS Match Rate")
    ax.axvline(80, color=REFERENCE_COLOR_1, linestyle="--", alpha=0.5)
    ax.axvline(95, color=REFERENCE_COLOR_2, linestyle="--", alpha=0.5)
    ax.set_xlabel("LTV (%)")
    ax.set_ylabel("Match Rate (%)")
    ax.set_title("Match Rate by LTV")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    # DTI
    ax = axes[1]
    x = np.array(dti_rates["bin"].to_list())
    mbs_y = np.array(
        [r * 100 if r is not None else np.nan for r in dti_rates["mbs_match_rate"].to_list()]
    )
    umbs_y = np.array(
        [r * 100 if r is not None else np.nan for r in dti_rates["umbs_match_rate"].to_list()]
    )

    ax.plot(x, mbs_y, color=MBS_COLOR, linewidth=1.5, label="MBS Match Rate")
    ax.plot(x, umbs_y, color=UMBS_COLOR, linewidth=1.5, label="UMBS Match Rate")
    ax.axvline(43, color=REFERENCE_COLOR_1, linestyle="--", alpha=0.5)
    ax.axvline(50, color=REFERENCE_COLOR_2, linestyle="--", alpha=0.5)
    ax.set_xlabel("DTI (%)")
    ax.set_ylabel("Match Rate (%)")
    ax.set_title("Match Rate by DTI")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    # FICO
    ax = axes[2]
    x = np.array(fico_rates["bin"].to_list())
    mbs_y = np.array(
        [r * 100 if r is not None else np.nan for r in fico_rates["mbs_match_rate"].to_list()]
    )
    umbs_y = np.array(
        [r * 100 if r is not None else np.nan for r in fico_rates["umbs_match_rate"].to_list()]
    )

    ax.plot(x, mbs_y, color=MBS_COLOR, linewidth=1.5, label="MBS Match Rate")
    ax.plot(x, umbs_y, color=UMBS_COLOR, linewidth=1.5, label="UMBS Match Rate")
    ax.axvline(620, color=REFERENCE_COLOR_1, linestyle="--", alpha=0.5)
    ax.axvline(740, color=REFERENCE_COLOR_2, linestyle="--", alpha=0.5)
    ax.set_xlabel("FICO Score")
    ax.set_ylabel("Match Rate (%)")
    ax.set_title("Match Rate by FICO")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    plt.suptitle(f"{gse_upper} MBS-UMBS Match Rates at Characteristic Extremes", fontsize=12)
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
    gse: str = "fnma",
    output_dir: Path | None = None,
    save_plots: bool = True,
    show_plots: bool = False,
) -> dict[str, Any]:
    """Run all validation analyses for MBS-UMBS matching.

    Args:
        gse: Either "fnma" or "fhlmc"
        output_dir: Directory to save figures. Defaults to VALIDATION_OUTPUT_DIR.
        save_plots: Whether to save plots to disk
        show_plots: Whether to display matplotlib plots interactively

    Returns:
        Dictionary with all computed statistics and DataFrames
    """
    gse = gse.lower()
    gse_upper = gse.upper()

    if output_dir is None:
        output_dir = VALIDATION_OUTPUT_DIR

    if save_plots:
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"Saving figures to: {output_dir}")

    results = {}

    # --- Section 1: Load Data ---
    print_section(f"1. Loading Data ({gse_upper})")

    print("Loading crosswalk...")
    crosswalk = load_crosswalk(gse)
    print(f"  Crosswalk: {len(crosswalk):,} matched pairs")
    print(f"  Columns: {list(crosswalk.schema.keys())}")

    print("Loading MBS data...")
    mbs_data = load_mbs_data(gse)

    print("Loading UMBS data...")
    umbs_data = load_umbs_data(gse)

    # Clean full-data window for all NON-TEMPORAL stats: First Payment Date >= UMBS issuance
    # start (2019-06). Before then ILLD does not exist and the snapshot / MBS series have
    # asymmetric coverage, so an unrestricted denominator is not interpretable. The temporal
    # by-month analysis below deliberately keeps the FULL range to show match rate over time.
    clean = ISSUANCE_START_YM
    mbs_clean = mbs_data.filter(pl.col("first_payment_ym") >= clean)
    umbs_clean = umbs_data.filter(pl.col("year_month") >= clean)

    # --- Section 2: Match Statistics ---
    print_section("2. Match Statistics Summary")

    stats = compute_match_statistics(crosswalk, mbs_clean, umbs_clean)
    print(
        f"(clean window: First Payment Date >= {clean}; pre-{clean} excluded for full-data denominator)"
    )
    print(f"Matched pairs (in window): {stats['n_matched_pairs']:,}")
    print("\nMBS Perspective:")
    print(f"  Total MBS loans: {stats['n_total_mbs']:,}")
    print(f"  Matched MBS loans: {stats['n_matched_mbs']:,}")
    print(f"  MBS match rate: {stats['mbs_match_rate']:.1%}")
    print("\nUMBS Perspective:")
    print(f"  Total UMBS loans: {stats['n_total_umbs']:,}")
    print(f"  Matched UMBS loans: {stats['n_matched_umbs']:,}")
    print(f"  UMBS match rate: {stats['umbs_match_rate']:.1%}")
    results["stats"] = stats

    # --- Section 3: Temporal Analysis ---
    print_section("3. Temporal Analysis (Match Rates by Month)")

    print("Computing match rates by month...")
    monthly_data = compute_match_rates_by_month(crosswalk, umbs_data, gse=gse)
    print(f"  {len(monthly_data)} monthly periods")

    if len(monthly_data) > 0:
        avg_mbs_match = monthly_data["mbs_match_rate"].mean()
        avg_umbs_match = monthly_data["umbs_match_rate"].mean()
        print(f"  Average match rate (MBS): {avg_mbs_match:.1%}")
        print(f"  Average match rate (UMBS): {avg_umbs_match:.1%}")
    results["monthly_data"] = monthly_data

    # --- Section 4: Match Rates by LTV ---
    print_section("4. Match Rates by LTV")

    print("Computing match rates by LTV...")
    ltv_rates = compute_match_rates_by_characteristic(
        crosswalk,
        mbs_clean,
        umbs_clean,
        mbs_column="LTV",
        umbs_column="LTV",
        bin_size=1,
        min_val=20,
        max_val=105,
    )
    print(ltv_rates)
    results["ltv_rates"] = ltv_rates

    # --- Section 5: Match Rates by DTI ---
    print_section("5. Match Rates by DTI")

    print("Computing match rates by DTI...")
    dti_rates = compute_match_rates_by_characteristic(
        crosswalk,
        mbs_clean,
        umbs_clean,
        mbs_column="DTI",
        umbs_column="DTI",
        bin_size=1,
        min_val=0,
        max_val=65,
    )
    print(dti_rates)
    results["dti_rates"] = dti_rates

    # --- Section 6: Match Rates by FICO ---
    print_section("6. Match Rates by FICO Score")

    print("Computing match rates by FICO...")
    fico_rates = compute_match_rates_by_characteristic(
        crosswalk,
        mbs_clean,
        umbs_clean,
        mbs_column="FICO",
        umbs_column="FICO",
        bin_size=1,
        min_val=580,
        max_val=850,
    )
    print(fico_rates)
    results["fico_rates"] = fico_rates

    # --- Section 7: Key Findings ---
    print_section("7. Key Findings")

    # Analyze extremes
    print("LTV Extremes:")
    low_ltv = ltv_rates.filter(pl.col("bin") <= 60)
    high_ltv = ltv_rates.filter(pl.col("bin") >= 90)
    if len(low_ltv) > 0:
        print(
            f"  Low LTV (<=60): MBS={low_ltv['mbs_match_rate'].mean():.1%}, UMBS={low_ltv['umbs_match_rate'].mean():.1%}"
        )
    if len(high_ltv) > 0:
        print(
            f"  High LTV (>=90): MBS={high_ltv['mbs_match_rate'].mean():.1%}, UMBS={high_ltv['umbs_match_rate'].mean():.1%}"
        )

    print("\nDTI Extremes:")
    low_dti = dti_rates.filter(pl.col("bin") <= 25)
    high_dti = dti_rates.filter(pl.col("bin") >= 45)
    if len(low_dti) > 0:
        print(
            f"  Low DTI (<=25): MBS={low_dti['mbs_match_rate'].mean():.1%}, UMBS={low_dti['umbs_match_rate'].mean():.1%}"
        )
    if len(high_dti) > 0:
        print(
            f"  High DTI (>=45): MBS={high_dti['mbs_match_rate'].mean():.1%}, UMBS={high_dti['umbs_match_rate'].mean():.1%}"
        )

    print("\nFICO Extremes:")
    low_fico = fico_rates.filter(pl.col("bin") <= 660)
    high_fico = fico_rates.filter(pl.col("bin") >= 760)
    if len(low_fico) > 0:
        print(
            f"  Low FICO (<=660): MBS={low_fico['mbs_match_rate'].mean():.1%}, UMBS={low_fico['umbs_match_rate'].mean():.1%}"
        )
    if len(high_fico) > 0:
        print(
            f"  High FICO (>=760): MBS={high_fico['mbs_match_rate'].mean():.1%}, UMBS={high_fico['umbs_match_rate'].mean():.1%}"
        )

    # --- Section 8: Generate Plots ---
    if (show_plots or save_plots) and HAS_MATPLOTLIB:
        print_section("8. Generating Visualizations")

        print("1. Plotting match rates by month...")
        plot_match_rates_by_month(
            monthly_data,
            gse=gse,
            output_path=output_dir / f"{gse}_match_rates_by_month.png" if save_plots else None,
            interactive=show_plots,
        )

        print("2. Plotting match rates by LTV...")
        plot_match_rates_by_characteristic(
            ltv_rates,
            xlabel="LTV (%)",
            title=f"{gse_upper} MBS-UMBS Match Rates by LTV (FPD >= 2019-06)",
            output_path=output_dir / f"{gse}_match_rates_by_ltv.png" if save_plots else None,
            interactive=show_plots,
        )

        print("3. Plotting match rates by DTI...")
        plot_match_rates_by_characteristic(
            dti_rates,
            xlabel="DTI (%)",
            title=f"{gse_upper} MBS-UMBS Match Rates by DTI (FPD >= 2019-06)",
            output_path=output_dir / f"{gse}_match_rates_by_dti.png" if save_plots else None,
            interactive=show_plots,
        )

        print("4. Plotting match rates by FICO...")
        plot_match_rates_by_characteristic(
            fico_rates,
            xlabel="FICO Score",
            title=f"{gse_upper} MBS-UMBS Match Rates by FICO Score (FPD >= 2019-06)",
            output_path=output_dir / f"{gse}_match_rates_by_fico.png" if save_plots else None,
            interactive=show_plots,
        )

        print("5. Plotting extremes comparison panel...")
        plot_match_rates_extremes_comparison(
            ltv_rates,
            dti_rates,
            fico_rates,
            gse=gse,
            output_path=output_dir / f"{gse}_match_rates_extremes.png" if save_plots else None,
            interactive=show_plots,
        )

    print("\nDone! Validation complete.")
    if save_plots:
        print(f"Figures saved to: {output_dir}")

    return results
