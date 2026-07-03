#!/usr/bin/env python3
"""GNMA Data Manager - Performance Module.

Functions for loading and analyzing GNMA loan-level performance data.

This module provides:
    - Data loading from the silver layer (processed parquet files)
    - Summary statistics calculation
    - Time series aggregation at various levels (total, pool, issuer, loan)

Public API:
    - load_performance_data: Load performance data from multiple prefixes
    - find_data_files: Find available data files for a prefix
    - load_file: Load a single parquet file
    - get_summary_statistics: Calculate summary statistics for performance data
    - create_upb_time_series: Create time series of unpaid principal balances
    - aggregate_by_pool: Aggregate UPB by pool and date
    - aggregate_by_issuer: Aggregate UPB by issuer and date
    - aggregate_total: Aggregate total UPB by date

"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import polars as pl

from mortgage_data_manager.core.logging import get_logger

from .config import ProcessorConfig

logger = get_logger(__name__)

__all__ = [
    # Loader functions
    "load_performance_data",
    "find_data_files",
    "load_file",
    "get_summary_statistics",
    # Aggregation functions
    "create_upb_time_series",
    "aggregate_by_pool",
    "aggregate_by_issuer",
    "aggregate_total",
]


# =============================================================================
# Data Loading Functions
# =============================================================================


def load_performance_data(
    prefixes: list[str] | None = None,
    data_folder: str | Path | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    record_type: str = "L",
    config: ProcessorConfig | None = None,
) -> pl.DataFrame:
    """Load performance data from specified prefixes.

    Args:
        prefixes: List of prefixes to load (default: ['llmon1', 'llmon2'])
        data_folder: Path to data folder
        start_date: Optional start date in YYYYMM format
        end_date: Optional end date in YYYYMM format
        record_type: Record type to load (default: 'L')
        config: Optional configuration

    Returns:
        Combined Polars DataFrame with performance data
    """
    if prefixes is None:
        prefixes = ["llmon1", "llmon2"]

    # Determine data folder (default to silver layer)
    if data_folder is None:
        if config is None:
            config = ProcessorConfig()
        data_folder = config.silver_folder

    data_folder = Path(data_folder)
    all_data = []

    for prefix in prefixes:
        logger.info(f"Loading data for prefix: {prefix}")

        # Find all files for this prefix
        files = find_data_files(prefix, data_folder, start_date, end_date)

        if not files:
            logger.warning(f"No files found for prefix {prefix}")
            continue

        # Load each file and filter to specified record type
        for file_path in files:
            df = load_file(file_path)

            if df.is_empty():
                continue

            # Filter to specified record type if column exists
            if "Record_Type" in df.columns:
                df = df.filter(pl.col("Record_Type") == record_type)

            # Add prefix column for tracking
            df = df.with_columns(pl.lit(prefix).alias("data_source"))

            all_data.append(df)

    if not all_data:
        logger.warning("No data loaded from any prefix")
        return pl.DataFrame()

    # Combine all data
    combined_df = pl.concat(all_data, how="diagonal_relaxed")
    logger.info(f"Loaded total of {len(combined_df)} records")

    return combined_df


def find_data_files(
    prefix: str,
    data_folder: str | Path,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[Path]:
    """Find all data files for a prefix within the date range.

    Args:
        prefix: File prefix (e.g., 'llmon1', 'llmon2')
        data_folder: Path to data folder
        start_date: Optional start date in YYYYMM format
        end_date: Optional end date in YYYYMM format

    Returns:
        List of Path objects for matching files
    """
    data_folder = Path(data_folder)
    prefix_folder = data_folder / prefix
    files = []

    if not prefix_folder.exists():
        logger.warning(f"Prefix folder does not exist: {prefix_folder}")
        return files

    # Look for parquet files (processed data)
    parquet_files = list(prefix_folder.glob("*.parquet"))

    # Filter by date if specified
    if start_date or end_date:
        filtered_files = []
        for f in parquet_files:
            # Extract date from filename (format like llmon1_YYYYMM_L.parquet)
            try:
                parts = f.stem.split("_")
                if len(parts) >= 2:
                    file_date = parts[1]
                    if start_date and file_date < start_date:
                        continue
                    if end_date and file_date > end_date:
                        continue
                    filtered_files.append(f)
            except (IndexError, ValueError):
                logger.warning(f"Could not parse date from filename: {f}")
                continue
        files = filtered_files
    else:
        files = parquet_files

    logger.info(f"Found {len(files)} files for prefix {prefix}")
    return sorted(files)


def load_file(file_path: Path) -> pl.DataFrame:
    """Load a single data file.

    Args:
        file_path: Path to the parquet file

    Returns:
        Polars DataFrame with the data
    """
    try:
        df = pl.read_parquet(file_path)
        logger.debug(f"Loaded {len(df)} records from {file_path}")
        return df
    except Exception as e:
        logger.error(f"Error loading file {file_path}: {e}")
        return pl.DataFrame()


def get_summary_statistics(df: pl.DataFrame, upb_column: str | None = None) -> dict:
    """Get summary statistics for performance data.

    Args:
        df: Performance data DataFrame
        upb_column: Optional name of UPB column

    Returns:
        Dictionary with summary statistics
    """
    if df.is_empty():
        logger.warning("Empty DataFrame provided for statistics")
        return {}

    # Find UPB column if not specified
    if upb_column is None:
        upb_columns = [
            col
            for col in df.columns
            if "Unpaid Principal Balance" in col or col == "UPB"
        ]
        if not upb_columns:
            logger.error("Could not find UPB column for statistics")
            return {}
        upb_column = upb_columns[0]

    stats = {
        "total_loans": len(df),
        "total_upb": float(df[upb_column].sum()),
        "mean_upb": float(df[upb_column].mean()),
        "median_upb": float(df[upb_column].median()),
        "min_upb": float(df[upb_column].min()),
        "max_upb": float(df[upb_column].max()),
    }

    # Add pool and issuer counts if available
    if "Pool ID" in df.columns or "Pool_ID" in df.columns:
        pool_col = "Pool ID" if "Pool ID" in df.columns else "Pool_ID"
        stats["unique_pools"] = df[pool_col].n_unique()

    if "Issuer ID" in df.columns or "Issuer_ID" in df.columns:
        issuer_col = "Issuer ID" if "Issuer ID" in df.columns else "Issuer_ID"
        stats["unique_issuers"] = df[issuer_col].n_unique()

    return stats


# =============================================================================
# Aggregation Functions
# =============================================================================


def create_upb_time_series(
    df: pl.DataFrame,
    aggregation_level: str = "total",
    upb_column: str | None = None,
    date_column: str | None = None,
) -> pd.DataFrame:
    """Create a time series of unpaid principal balances (UPB).

    Args:
        df: Performance data DataFrame
        aggregation_level: Level of aggregation:
            - 'total': Total UPB across all loans
            - 'pool': UPB by pool
            - 'issuer': UPB by issuer
            - 'loan': Individual loan-level UPB
        upb_column: Optional name of UPB column
        date_column: Optional name of date column

    Returns:
        Pandas DataFrame with time series of UPB
    """
    if df.is_empty():
        logger.warning("Empty DataFrame provided for time series creation")
        return pd.DataFrame()

    # Identify UPB column if not specified
    if upb_column is None:
        upb_columns = [
            col
            for col in df.columns
            if "Unpaid Principal Balance" in col or col == "UPB"
        ]
        if not upb_columns:
            logger.error("Could not find UPB column in data")
            logger.info(f"Available columns: {df.columns}")
            return pd.DataFrame()
        upb_column = upb_columns[0]

    # Identify date column if not specified
    if date_column is None:
        date_columns = [
            col for col in df.columns if "As of Date" in col or "As-Of Date" in col
        ]
        if not date_columns:
            logger.error("Could not find date column in data")
            logger.info(f"Available columns: {df.columns}")
            return pd.DataFrame()
        date_column = date_columns[0]

    logger.info(f"Creating UPB time series at {aggregation_level} level")
    logger.info(f"Using UPB column: {upb_column}")
    logger.info(f"Using date column: {date_column}")

    # Convert date column to proper format if needed
    if df[date_column].dtype == pl.Int64 or df[date_column].dtype == pl.Int32:
        # Convert YYYYMM integer to date
        df = df.with_columns(
            pl.col(date_column).cast(pl.Utf8).str.to_date("%Y%m").alias("as_of_date")
        )
    else:
        df = df.with_columns(pl.col(date_column).alias("as_of_date"))

    # Perform aggregation based on level
    if aggregation_level == "total":
        time_series = aggregate_total(df, upb_column, "as_of_date")

    elif aggregation_level == "pool":
        # Find pool column
        pool_col = "Pool ID" if "Pool ID" in df.columns else "Pool_ID"
        if pool_col not in df.columns:
            logger.error("Pool column not found in data")
            return pd.DataFrame()
        time_series = aggregate_by_pool(df, upb_column, "as_of_date", pool_col)

    elif aggregation_level == "issuer":
        # Find issuer column
        issuer_col = "Issuer ID" if "Issuer ID" in df.columns else "Issuer_ID"
        if issuer_col not in df.columns:
            logger.error("Issuer column not found in data")
            return pd.DataFrame()
        time_series = aggregate_by_issuer(df, upb_column, "as_of_date", issuer_col)

    elif aggregation_level == "loan":
        # Individual loan-level UPB
        seq_col = (
            "Disclosure Sequence Number"
            if "Disclosure Sequence Number" in df.columns
            else "Disclosure_Sequence_Number"
        )
        if seq_col not in df.columns:
            logger.error("Sequence number column not found in data")
            return pd.DataFrame()
        select_cols = ["as_of_date", seq_col, upb_column]
        time_series = df.select(select_cols).sort("as_of_date")

    else:
        logger.error(f"Invalid aggregation level: {aggregation_level}")
        return pd.DataFrame()

    # Convert to pandas for easier time series manipulation
    result = time_series.to_pandas()

    logger.info(f"Created time series with {len(result)} records")

    return result


def aggregate_by_pool(
    df: pl.DataFrame,
    upb_column: str,
    date_column: str,
    pool_column: str = "Pool_ID",
) -> pl.DataFrame:
    """Aggregate UPB by pool and date.

    Args:
        df: Performance data DataFrame
        upb_column: Name of UPB column
        date_column: Name of date column
        pool_column: Name of pool ID column

    Returns:
        Aggregated DataFrame
    """
    return (
        df.group_by([date_column, pool_column])
        .agg(pl.col(upb_column).sum().alias("pool_upb"))
        .sort([date_column, pool_column])
    )


def aggregate_by_issuer(
    df: pl.DataFrame,
    upb_column: str,
    date_column: str,
    issuer_column: str = "Issuer_ID",
) -> pl.DataFrame:
    """Aggregate UPB by issuer and date.

    Args:
        df: Performance data DataFrame
        upb_column: Name of UPB column
        date_column: Name of date column
        issuer_column: Name of issuer ID column

    Returns:
        Aggregated DataFrame
    """
    return (
        df.group_by([date_column, issuer_column])
        .agg(pl.col(upb_column).sum().alias("issuer_upb"))
        .sort([date_column, issuer_column])
    )


def aggregate_total(
    df: pl.DataFrame,
    upb_column: str,
    date_column: str,
) -> pl.DataFrame:
    """Aggregate total UPB by date.

    Args:
        df: Performance data DataFrame
        upb_column: Name of UPB column
        date_column: Name of date column

    Returns:
        Aggregated DataFrame
    """
    return (
        df.group_by(date_column)
        .agg(pl.col(upb_column).sum().alias("total_upb"))
        .sort(date_column)
    )
