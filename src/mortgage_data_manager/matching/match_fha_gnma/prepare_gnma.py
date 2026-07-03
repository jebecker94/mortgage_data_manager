"""GNMA Data Preparation for FHA-GNMA Matching.

Loads GNMA silver dailyllmni data, filters to FHA loans (Agency='F'),
and prepares for matching with FHA endorsement records.

Optimized for memory efficiency by filtering each file before concatenation.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import configure_logging, get_logger

from .config import (
    GNMA_SILVER_DIR,
    INTERMEDIATE_DIR,
    MAX_YEAR,
    MIN_YEAR,
    PILOT_STATE,
    ensure_directories,
)

logger = get_logger(__name__)


def find_column(df: pl.DataFrame | pl.LazyFrame, pattern: str) -> str | None:
    """Find a column name that starts with the given pattern.

    Handles slight variations in GNMA column names across files (e.g., trailing spaces).
    """
    columns = df.columns if isinstance(df, pl.DataFrame) else df.collect_schema().names()
    for col in columns:
        if col.startswith(pattern):
            return col
    return None


def find_column_in_schema(schema: dict[str, pl.DataType], pattern: str) -> str | None:
    """Find a column name in schema that starts with the given pattern."""
    for col in schema.keys():
        if col.startswith(pattern):
            return col
    return None


def load_and_filter_gnma_file(
    file_path: Path,
    state_filter: str | None,
    min_year: int,
    max_year: int,
) -> pl.LazyFrame | None:
    """Load a single GNMA file and apply filters lazily.

    Applies state, agency, and year filters before collecting to minimize memory.

    Returns None if the file doesn't have required columns.
    """
    # Scan the file lazily
    lf = pl.scan_parquet(file_path)
    schema = lf.collect_schema()

    # Find column names in this file's schema
    agency_col = find_column_in_schema(schema, "Agency")
    state_col = find_column_in_schema(schema, "State")
    origination_date_col = find_column_in_schema(schema, "Loan Origination Date")

    # Skip files without required columns
    if agency_col is None or origination_date_col is None:
        return None

    # Apply filters lazily (before collecting)
    # Filter to FHA loans (Agency = 'F')
    lf = lf.filter(pl.col(agency_col) == "F")

    # Filter to valid origination dates
    lf = lf.filter(pl.col(origination_date_col).is_not_null())

    # Filter by year
    lf = lf.filter(
        (pl.col(origination_date_col).str.slice(0, 4).cast(pl.Int64, strict=False) >= min_year)
        & (pl.col(origination_date_col).str.slice(0, 4).cast(pl.Int64, strict=False) <= max_year)
    )

    # Filter by state (most selective filter - apply early!)
    if state_filter and state_col:
        lf = lf.filter(pl.col(state_col) == state_filter)

    return lf


def normalize_gnma_columns(df: pl.DataFrame) -> pl.DataFrame:
    """Normalize GNMA column names by stripping trailing spaces.

    GNMA data has inconsistent column names across file versions (trailing spaces).
    When using diagonal_relaxed concat, this creates duplicate columns.
    This function coalesces the duplicate columns into single normalized names.
    """
    # Map of normalized name -> list of variant names
    column_variants: dict[str, list[str]] = {}
    for col in df.columns:
        # Normalize by stripping trailing whitespace
        normalized = col.rstrip()
        if ")" in col:
            # Also normalize before closing paren
            normalized = col.rstrip(" ").rstrip(")").rstrip(" ") + ")"
            normalized = normalized.replace(" )", ")")
        if normalized not in column_variants:
            column_variants[normalized] = []
        column_variants[normalized].append(col)

    # For each normalized name with multiple variants, coalesce them
    coalesce_exprs = []
    rename_map = {}

    for normalized, variants in column_variants.items():
        if len(variants) > 1:
            # Coalesce multiple columns into one
            coalesce_exprs.append(pl.coalesce([pl.col(v) for v in variants]).alias(normalized))
        elif variants[0] != normalized:
            # Single variant but name differs - rename
            rename_map[variants[0]] = normalized

    # Apply coalesces
    if coalesce_exprs:
        df = df.with_columns(coalesce_exprs)
        # Drop the original variant columns
        for normalized, variants in column_variants.items():
            if len(variants) > 1:
                for v in variants:
                    if v in df.columns and v != normalized:
                        df = df.drop(v)

    # Apply renames
    if rename_map:
        df = df.rename(rename_map)

    return df


def load_gnma_silver_data(
    gnma_dir: Path = GNMA_SILVER_DIR,
    min_year: int = MIN_YEAR,
    max_year: int = MAX_YEAR,
    state_filter: str | None = PILOT_STATE,
) -> pl.DataFrame:
    """Load GNMA silver dailyllmni loan-level data with early filtering.

    Applies filters to each file BEFORE concatenation for memory efficiency.
    Uses diagonal_relaxed concat to handle schema evolution across files.

    Args:
        gnma_dir: Directory containing GNMA silver L parquet files
        min_year: Minimum year to include (based on Loan Origination Date)
        max_year: Maximum year to include
        state_filter: 2-char state code to filter (e.g., "DC"). None = all states.

    Returns:
        Combined GNMA loan-level data filtered to FHA loans
    """
    parquet_files = sorted(gnma_dir.glob("*.parquet"))

    if not parquet_files:
        raise FileNotFoundError(f"No parquet files found in {gnma_dir}")

    logger.info(f"Scanning {len(parquet_files)} GNMA silver files...")
    if state_filter:
        logger.info(f"  Filtering to state={state_filter}, Agency=F, years {min_year}-{max_year}")

    # Load each file with filters applied, collect only filtered rows
    filtered_dfs = []
    files_with_data = 0

    for i, f in enumerate(parquet_files):
        lf = load_and_filter_gnma_file(f, state_filter, min_year, max_year)
        if lf is not None:
            # Collect the filtered data from this file
            df_filtered = lf.collect()
            if len(df_filtered) > 0:
                filtered_dfs.append(df_filtered)
                files_with_data += 1

        # Progress indicator for large file sets
        if (i + 1) % 25 == 0:
            logger.debug(f"  Processed {i + 1}/{len(parquet_files)} files...")

    if not filtered_dfs:
        raise ValueError(
            f"No GNMA data found for state={state_filter}, years {min_year}-{max_year}"
        )

    logger.info(f"  Found data in {files_with_data} files")

    # Concat filtered data with diagonal_relaxed for schema differences
    df = pl.concat(filtered_dfs, how="diagonal_relaxed")

    # Normalize column names (handles trailing space variations)
    df = normalize_gnma_columns(df)

    logger.info(f"  Total filtered rows: {len(df):,}")

    return df


def prepare_gnma_for_matching(df: pl.DataFrame) -> pl.DataFrame:
    """Prepare GNMA data for matching by selecting and transforming columns.

    Args:
        df: Raw GNMA silver data (already filtered to FHA loans)

    Returns:
        Prepared GNMA data with standardized columns
    """
    # Find column names dynamically
    disclosure_col = find_column(df, "Disclosure Sequence Number")
    state_col = find_column(df, "State")
    origination_date_col = find_column(df, "Loan Origination Date")
    interest_rate_col = find_column(df, "Loan Interest Rate")
    opb_col = find_column(df, "Original Principal Balance")
    purpose_col = find_column(df, "Loan Purpose")
    pool_id_col = find_column(df, "Pool ID")
    term_col = find_column(df, "Original Loan Term")

    if disclosure_col is None:
        raise ValueError("Could not find Disclosure Sequence Number column")
    if interest_rate_col is None:
        raise ValueError("Could not find Loan Interest Rate column")
    if opb_col is None:
        raise ValueError("Could not find Original Principal Balance column")

    # Build column selection
    select_cols = [
        pl.col(disclosure_col).alias("gnma_loan_id"),
        pl.col(state_col).alias("state") if state_col else pl.lit(None).alias("state"),
        pl.col(origination_date_col).alias("origination_date"),
        pl.col(interest_rate_col).alias("interest_rate_raw"),
        pl.col(opb_col).alias("loan_amount_raw"),
        pl.col(purpose_col).alias("gnma_loan_purpose")
        if purpose_col
        else pl.lit(None).alias("gnma_loan_purpose"),
    ]

    if pool_id_col:
        select_cols.append(pl.col(pool_id_col).alias("pool_id"))
    if term_col:
        select_cols.append(pl.col(term_col).alias("original_term"))

    df = df.select(select_cols)

    # Convert to numeric and transform
    df = df.with_columns(
        [
            # Extract origination year from YYYYMMDD format
            pl.col("origination_date")
            .str.slice(0, 4)
            .cast(pl.Int32, strict=False)
            .alias("origination_year"),
            # Extract origination month from YYYYMMDD format
            pl.col("origination_date")
            .str.slice(4, 2)
            .cast(pl.Int32, strict=False)
            .alias("origination_month"),
            # Convert loan amount: GNMA stores in cents, divide by 100 for dollars
            (pl.col("loan_amount_raw").cast(pl.Float64, strict=False) / 100).alias("loan_amount"),
            # Convert interest rate: GNMA stores as basis points * 100, divide by 1000 for percent
            # e.g., 3750 = 3.75%
            (pl.col("interest_rate_raw").cast(pl.Float64, strict=False) / 1000).alias(
                "interest_rate"
            ),
            # Convert loan purpose to int
            pl.col("gnma_loan_purpose").cast(pl.Int32, strict=False).alias("gnma_loan_purpose"),
        ]
    )

    # Create is_purchase indicator: GNMA Loan Purpose 1 = Purchase
    df = df.with_columns(
        pl.when(pl.col("gnma_loan_purpose") == 1).then(1).otherwise(0).alias("is_purchase")
    )

    # Create join keys for exact matching:
    # 1. Loan amount in thousands (GNMA already truncated, just divide by 1000)
    # 2. Rate bucket (round to nearest 0.125%)
    df = df.with_columns(
        [
            (pl.col("loan_amount") / 1000).round().cast(pl.Int64).alias("loan_amount_thousands"),
            (pl.col("interest_rate") / 0.125).round().cast(pl.Int32).alias("rate_bucket"),
        ]
    )

    # Drop raw columns
    df = df.drop(["loan_amount_raw", "interest_rate_raw"])

    logger.info(f"Prepared GNMA data: {len(df):,} rows")
    logger.debug(f"  Columns: {df.columns}")

    return df


def run_gnma_preparation(
    gnma_dir: Path = GNMA_SILVER_DIR,
    output_dir: Path = INTERMEDIATE_DIR,
    min_year: int = MIN_YEAR,
    max_year: int = MAX_YEAR,
    state_filter: str | None = PILOT_STATE,
) -> Path:
    """Run the GNMA data preparation pipeline.

    Args:
        gnma_dir: Directory containing GNMA silver L parquet files
        output_dir: Directory to save prepared data
        min_year: Minimum origination year
        max_year: Maximum origination year
        state_filter: State to filter (e.g., "DC"). None = all states.

    Returns:
        Path to saved prepared GNMA data
    """
    ensure_directories()

    logger.info("GNMA Data Preparation")
    logger.info(f"Source: {gnma_dir}")
    logger.info(f"Years: {min_year}-{max_year}")
    logger.info(f"State filter: {state_filter or 'All states'}")

    # Load raw GNMA data (with early filtering)
    df = load_gnma_silver_data(
        gnma_dir=gnma_dir,
        min_year=min_year,
        max_year=max_year,
        state_filter=state_filter,
    )

    # Prepare for matching
    df = prepare_gnma_for_matching(df)

    # Save prepared data
    state_suffix = f"_{state_filter}" if state_filter else ""
    output_file = output_dir / f"gnma_prepared_{min_year}_{max_year}{state_suffix}.parquet"
    df.write_parquet(output_file)

    logger.info(f"Saved prepared GNMA data to: {output_file}")

    return output_file


if __name__ == "__main__":
    configure_logging(level="INFO")
    run_gnma_preparation()
