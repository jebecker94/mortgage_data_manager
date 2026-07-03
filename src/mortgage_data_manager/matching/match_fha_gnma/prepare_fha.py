"""FHA Data Preparation for FHA-GNMA Matching.

Loads FHA silver endorsement data and prepares for matching
with GNMA loan-level disclosure records.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import configure_logging, get_logger

from .config import (
    FHA_SILVER_DIR,
    INTERMEDIATE_DIR,
    MAX_YEAR,
    MIN_YEAR,
    PILOT_STATE,
    ensure_directories,
)

logger = get_logger(__name__)


def load_fha_silver_data(
    fha_dir: Path = FHA_SILVER_DIR,
    min_year: int = MIN_YEAR,
    max_year: int = MAX_YEAR,
    state_filter: str | None = PILOT_STATE,
) -> pl.DataFrame:
    """Load FHA silver endorsement data.

    FHA data is hive-partitioned by Year and Month.

    Args:
        fha_dir: Directory containing FHA silver parquet files (hive-partitioned)
        min_year: Minimum year to include
        max_year: Maximum year to include
        state_filter: 2-char state code to filter (e.g., "DC"). None = all states.

    Returns:
        FHA endorsement data
    """
    # Scan all parquet files in hive-partitioned structure
    parquet_pattern = fha_dir / "**/*.parquet"

    logger.info(f"Loading FHA silver data from: {fha_dir}")

    df = pl.scan_parquet(parquet_pattern)

    # Filter by year
    df = df.filter((pl.col("Year") >= min_year) & (pl.col("Year") <= max_year))

    # Filter by state if specified
    if state_filter:
        df = df.filter(pl.col("Property State") == state_filter)

    # Collect to DataFrame
    df = df.collect()

    logger.info(f"  Loaded {len(df):,} FHA records")
    if state_filter:
        logger.info(f"  State filter: {state_filter}")
    logger.info(f"  Years: {min_year}-{max_year}")

    return df


def prepare_fha_for_matching(df: pl.DataFrame) -> pl.DataFrame:
    """Prepare FHA data for matching by selecting and transforming columns.

    Args:
        df: Raw FHA silver data

    Returns:
        Prepared FHA data with standardized columns:
        - FHA_Index: Unique identifier
        - state: Property State (2-char)
        - origination_year: Year from endorsement
        - loan_amount: Mortgage Amount in dollars
        - interest_rate: Interest Rate as percent (e.g., 3.75 = 3.75%)
        - fha_loan_purpose: "Purchase", "Refi_FHA", "Refi_Conv_Curr"
        - is_purchase: 1 if Purchase, 0 if Refi
        - is_arm: 1 if Adjustable Rate, 0 if Fixed Rate
    """
    # Select and rename columns
    df = df.select(
        [
            pl.col("FHA_Index"),
            pl.col("Property State").alias("state"),
            pl.col("Property Zip").alias("zip_code"),
            pl.col("Year").alias("origination_year"),
            pl.col("Month").alias("origination_month"),
            pl.col("Mortgage Amount").alias("loan_amount"),
            # FHA Interest Rate is already in percent (e.g., 3.75 = 3.75%)
            # No conversion needed - matches GNMA format after GNMA / 1000
            pl.col("Interest Rate").alias("interest_rate"),
            pl.col("Loan Purpose").alias("fha_loan_purpose"),
            pl.col("Product Type").alias("product_type"),
        ]
    )

    # Create is_purchase indicator: "Purchase" = 1, Refi = 0
    df = df.with_columns(
        pl.when(pl.col("fha_loan_purpose") == "Purchase").then(1).otherwise(0).alias("is_purchase")
    )

    # Create is_arm indicator based on Product Type
    df = df.with_columns(
        pl.when(pl.col("product_type") == "Adjustable Rate").then(1).otherwise(0).alias("is_arm")
    )

    # Create join keys for exact matching:
    # 1. Floored loan amount (GNMA truncates to thousands)
    # 2. Rate bucket (round to nearest 0.125%)
    df = df.with_columns(
        [
            (pl.col("loan_amount") / 1000).floor().cast(pl.Int64).alias("loan_amount_thousands"),
            (pl.col("interest_rate") / 0.125).round().cast(pl.Int32).alias("rate_bucket"),
        ]
    )

    logger.info(f"Prepared FHA data: {len(df):,} rows")
    logger.debug(f"  Columns: {df.columns}")

    # Summary stats
    purchase_count = df.filter(pl.col("is_purchase") == 1).height
    refi_count = df.filter(pl.col("is_purchase") == 0).height
    logger.info(f"  Purchase loans: {purchase_count:,}")
    logger.info(f"  Refinance loans: {refi_count:,}")

    return df


def run_fha_preparation(
    fha_dir: Path = FHA_SILVER_DIR,
    output_dir: Path = INTERMEDIATE_DIR,
    min_year: int = MIN_YEAR,
    max_year: int = MAX_YEAR,
    state_filter: str | None = PILOT_STATE,
) -> Path:
    """Run the FHA data preparation pipeline.

    Args:
        fha_dir: Directory containing FHA silver parquet files
        output_dir: Directory to save prepared data
        min_year: Minimum endorsement year
        max_year: Maximum endorsement year
        state_filter: State to filter (e.g., "DC"). None = all states.

    Returns:
        Path to saved prepared FHA data
    """
    ensure_directories()

    logger.info("FHA Data Preparation")
    logger.info(f"Source: {fha_dir}")
    logger.info(f"Years: {min_year}-{max_year}")
    logger.info(f"State filter: {state_filter or 'All states'}")

    # Load raw FHA data
    df = load_fha_silver_data(
        fha_dir=fha_dir,
        min_year=min_year,
        max_year=max_year,
        state_filter=state_filter,
    )

    # Prepare for matching
    df = prepare_fha_for_matching(df)

    # Save prepared data
    state_suffix = f"_{state_filter}" if state_filter else ""
    output_file = output_dir / f"fha_prepared_{min_year}_{max_year}{state_suffix}.parquet"
    df.write_parquet(output_file)

    logger.info(f"Saved prepared FHA data to: {output_file}")

    return output_file


if __name__ == "__main__":
    configure_logging(level="INFO")
    run_fha_preparation()
