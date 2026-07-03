"""HMDA silver-layer builders (era-split: pre-2007, 2007-2017, post-2018).

Each HMDA era has its own raw format, so silver construction is split into
three era-specific builders rather than a single function. All three clean,
standardize, and hive-partition the bronze parquet into analysis-ready data:

    - build_silver_pre2007: legacy LAR (1981-2006).
    - build_silver_period_2007_2017: standardized period (2007-2017).
    - build_silver_post2018: modern format (2018+).

Examples:
    >>> from mortgage_data_manager.hmda.import_silver import build_silver_post2018
    >>> build_silver_post2018("loans", min_year=2020, max_year=2024)
"""

from __future__ import annotations

import shutil
from typing import Literal

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.core.types import apply_silver_types, enforce_schema
from mortgage_data_manager.hmda.config import (
    DERIVED_COLUMNS,
    HMDA_INDEX_COLUMN,
    HMDA_SILVER_SPECS,
    PERIOD_2007_2017_FLOAT_COLUMNS,
    PERIOD_2007_2017_INTEGER_COLUMNS,
    POST2018_EXEMPT_COLUMNS,
    POST2018_FLOAT_COLUMNS,
    POST2018_INTEGER_COLUMNS,
    RENAME_DICTIONARY,
    HMDAConfig,
)
from mortgage_data_manager.hmda.utils import append_hmda_index


def _apply_hmda_spec(lf: pl.LazyFrame, dataset: str, era: str) -> pl.LazyFrame:
    """Apply the (dataset, era) close-to-final dtype spec and assert the schema.

    Right-sizes the Int64 code columns to the smallest signed int that holds their
    full range (sentinels stay live so matchers detecting -99999/1111/9999 keep
    working), keeps geo/identifier columns Utf8, and conforms to the per-era target
    so every year/file_type shares one schema. No-op if no spec is registered.
    """
    spec = HMDA_SILVER_SPECS.get((dataset, era))
    if spec is None:
        return lf
    lf = apply_silver_types(lf, spec)
    enforce_schema(lf.collect_schema(), spec.target, name=f"hmda/{dataset}/{era}")
    return lf


logger = get_logger(__name__)


def _standardize_geographic_codes(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Standardize geographic codes for pre-2007 data.

    Creates properly formatted FIPS codes and MSA/MD codes:
    - state_code: 2-digit string with leading zeros
    - county_code: 5-digit string (state + county) with leading zeros
    - census_tract: 11-digit string (state + county + tract) with leading zeros
    - msa_md: 5-digit string with leading zeros

    Pre-2007 format:
    - state_code: numeric 1-2 digits
    - county_code: numeric 3 digits (county within state)
    - census_tract: string format "####.##" (e.g., "9509.02")
    - msa_md: numeric variable length

    Post-standardization format:
    - state_code: "01" to "56" (2-digit FIPS)
    - county_code: "01001" (state + county, 5-digit)
    - census_tract: "01001950902" (state + county + tract, 11-digit)
    - msa_md: "01234" (5-digit MSA/MD code)

    Args:
        lf: Input lazy frame with geographic codes as strings

    Returns:
        Lazy frame with standardized geographic codes
    """
    # The panel and transmittal-series datasets carry no loan-level geography
    # (only respondent_state / msa_md), so there is nothing to standardize here.
    if "state_code" not in lf.collect_schema().names():
        return lf

    # Step 1: Standardize state_code to 2-digit string with leading zeros
    lf = lf.with_columns(
        pl.col("state_code")
        .cast(pl.Int64, strict=False)  # Convert to int first
        .cast(pl.String)  # Then to string
        .str.zfill(2)  # Pad to 2 digits
        .alias("state_code")
    )

    # Step 2: Create 5-digit county_code (state + county)
    # Bronze county_code is 3-digit county within state
    lf = lf.with_columns(
        # Combine state (2-digit) + county (3-digit)
        (
            pl.col("state_code")
            + pl.col("county_code").cast(pl.Int64, strict=False).cast(pl.String).str.zfill(3)
        ).alias("county_code")
    )

    # Step 3: Standardize census_tract to 11-digit string
    # Format: "####.##" -> convert to "01001950902"
    # Formula: state (2) + county (3) + tract*100 (6 digits)
    if "census_tract" in lf.collect_schema().names():
        lf = lf.with_columns(
            (
                # Take state_code (2-digit) + county last 3 digits + tract (6-digit)
                pl.col("state_code")
                + pl.col("county_code").str.slice(-3, 3)
                + pl.col("census_tract")
                .cast(pl.Float64, strict=False)
                .mul(100)
                .round(0)
                .cast(pl.Int64, strict=False)
                .cast(pl.String)
                .str.zfill(6)
            ).alias("census_tract")
        )

    # Step 4: Standardize msa_md to 5-digit string with leading zeros
    if "msa_md" in lf.collect_schema().names():
        lf = lf.with_columns(
            pl.col("msa_md")
            .cast(pl.Float64, strict=False)
            .cast(pl.Int64, strict=False)
            .cast(pl.String)
            .str.zfill(5)
            .alias("msa_md")
        )

    return lf


def _harmonize_schema_pre2007(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Harmonize schema for pre-2007 data - type conversions and standardization.

    This function performs type conversions from bronze (all strings) to silver
    (appropriate types for analysis):

    Numeric conversions (string -> integer):
    - activity_year, occupancy_type, edit_status, sequence_number: int
    - loan_amount: int (multiplied by 1000 to get actual dollar amount)
    - income: int (multiplied by 1000 to get actual dollar amount)

    String standardization:
    - msa_md: 5-digit string with leading zeros (like county_code)

    Geographic codes (handled separately by _standardize_geographic_codes):
    - state_code: 2-digit string with leading zeros
    - county_code: 5-digit string (state + county)
    - census_tract: 11-digit string (state + county + tract)

    Columns NOT converted (kept as strings due to non-numeric values):
    - respondent_id: contains Tax ID formats like "95-2318940"
    - agency_code: contains letters (C, E, D, B)
    - Other categorical variables with non-numeric codes

    Args:
        lf: Input lazy frame with all columns as strings

    Returns:
        Lazy frame with harmonized schema
    """
    from mortgage_data_manager.hmda.config import PRE2007_FLOAT_COLUMNS, PRE2007_INTEGER_COLUMNS

    lf_columns = lf.collect_schema().names()

    # Convert integer columns (except loan_amount and income which need special handling)
    # Use strict=False to convert non-numeric values to null (treats stray characters as errors)
    integer_cols_to_convert = [
        col
        for col in PRE2007_INTEGER_COLUMNS
        if col in lf_columns and col not in ["loan_amount", "income"]
    ]

    if integer_cols_to_convert:
        lf = lf.with_columns(
            [pl.col(col).cast(pl.Int64, strict=False).alias(col) for col in integer_cols_to_convert]
        )

    # Convert float columns
    float_cols_to_convert = [col for col in PRE2007_FLOAT_COLUMNS if col in lf_columns]

    if float_cols_to_convert:
        lf = lf.with_columns(
            [pl.col(col).cast(pl.Float64, strict=False).alias(col) for col in float_cols_to_convert]
        )

    # Convert loan_amount: string -> int -> multiply by 1000
    if "loan_amount" in lf_columns and "loan_amount" in PRE2007_INTEGER_COLUMNS:
        lf = lf.with_columns(
            pl.col("loan_amount").cast(pl.Int64, strict=False).mul(1000).alias("loan_amount")
        )

    # Convert income: string -> int -> multiply by 1000
    if "income" in lf_columns and "income" in PRE2007_INTEGER_COLUMNS:
        lf = lf.with_columns(
            pl.col("income").cast(pl.Int64, strict=False).mul(1000).alias("income")
        )

    return lf


def build_silver_pre2007(
    dataset: Literal["loans", "panel", "transmittal_series"],
    min_year: int = 1990,
    max_year: int = 2006,
    overwrite: bool = False,
) -> None:
    """Create silver layer parquet files for pre-2007 data.

    Reads bronze parquet files, applies schema harmonization (type conversions),
    geographic code standardization, and column renaming. Outputs Hive-partitioned
    parquet files to data/silver/<dataset>/pre2007/.

    Transformations applied:
    - Integer conversions: 31 columns (activity_year, loan_amount*1000, income*1000,
      loan_type, loan_purpose, action_taken, demographics, denial_reasons, etc.)
    - Float conversions: rate_spread
    - Geographic standardization: state_code (2-digit), county_code (5-digit),
      census_tract (11-digit), msa_md (5-digit)
    - Column renaming: occupancy -> occupancy_type, msamd -> msa_md

    Args:
        dataset: Dataset type to process
        min_year: First year to process (default: 1990)
        max_year: Last year to process (default: 2006)
        overwrite: Whether to replace existing silver files (default: False)
    """
    bronze_folder = HMDAConfig.get_dataset_dir("bronze", dataset, "pre2007")
    silver_folder = HMDAConfig.get_dataset_dir("silver", dataset, "pre2007")
    silver_folder.resolve().mkdir(parents=True, exist_ok=True)

    for year in range(min_year, max_year + 1):
        bronze_file = bronze_folder / f"{dataset}_{year}.parquet"
        # Pre-2007 transmittal-series bronze files carry a historical misspelling
        # ("transmissal_series_YYYY"); fall back to it when the canonical name is absent.
        if not bronze_file.exists() and dataset == "transmittal_series":
            alt = bronze_folder / f"transmissal_series_{year}.parquet"
            if alt.exists():
                bronze_file = alt

        if not bronze_file.exists():
            logger.debug("Bronze file not found for year %s: %s", year, bronze_file)
            continue

        # Output filename (year-based partitioning)
        save_file = silver_folder / f"activity_year={year}" / f"{dataset}_{year}.parquet"

        if not should_process_file(save_file, overwrite):
            logger.debug("Skipping existing silver file: %s", save_file)
            continue

        logger.info("[silver pre2007] Processing %s year %s", dataset, year)

        # Load bronze as LazyFrame
        lf = pl.scan_parquet(bronze_file)

        # Apply column renames (only renames columns that exist)
        existing_cols = lf.collect_schema().names()
        renames_to_apply = {
            old: new for old, new in RENAME_DICTIONARY.items() if old in existing_cols
        }
        if renames_to_apply:
            logger.debug("Renaming %d columns: %s", len(renames_to_apply), renames_to_apply)
            lf = lf.rename(renames_to_apply)

        # Apply transformations
        lf = _harmonize_schema_pre2007(lf)
        lf = _standardize_geographic_codes(lf)

        # Recover the garbled pre-2007 denial_reason_3: the fixed-width parse
        # captured a packed multi-code value (a giant int); only the leading
        # character is the real single-digit reason code.
        if "denial_reason_3" in lf.collect_schema().names():
            lf = lf.with_columns(
                pl.col("denial_reason_3")
                .cast(pl.String)
                .str.slice(0, 1)
                .cast(pl.Int64, strict=False)
                .alias("denial_reason_3")
            )

        # Close-to-final dtypes + schema enforcement.
        lf = _apply_hmda_spec(lf, dataset, "pre2007")

        # Create output directory
        save_file.parent.resolve().mkdir(parents=True, exist_ok=True)

        # Write to parquet
        lf.sink_parquet(save_file)
        logger.info("Saved silver file: %s", save_file)


def _destring_and_cast_hmda_cols_2007_2017(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Destring numeric HMDA variables and cast to consistent types.

    Casts integer columns to Int64 and float columns to Float64.
    Non-numeric strings are automatically converted to null via strict=False.

    Special handling for loan_amount and income:
    - Stored in thousands in raw data (e.g., "150" = $150,000)
    - Multiplied by 1000 to get actual dollar amounts

    Args:
        lf: HMDA data with potentially mixed string/numeric columns

    Returns:
        LazyFrame with consistent Int64/Float64 types for numeric columns
    """
    logger.info("Destringing HMDA variables and casting to consistent types (2007-2017)")

    # Cast float columns to Float64
    float_cols_to_cast = [
        col for col in lf.collect_schema().names() if col in PERIOD_2007_2017_FLOAT_COLUMNS
    ]
    if float_cols_to_cast:
        lf = lf.with_columns(
            [pl.col(col).cast(pl.Float64, strict=False).alias(col) for col in float_cols_to_cast]
        )

    # Cast integer columns to Int64
    int_cols_to_cast = [
        col for col in lf.collect_schema().names() if col in PERIOD_2007_2017_INTEGER_COLUMNS
    ]
    if int_cols_to_cast:
        lf = lf.with_columns(
            [pl.col(col).cast(pl.Int64, strict=False).alias(col) for col in int_cols_to_cast]
        )

    # Special handling: loan_amount stored in thousands, multiply by 1000
    if "loan_amount" in lf.collect_schema().names():
        lf = lf.with_columns(pl.col("loan_amount").mul(1000).alias("loan_amount"))

    # Special handling: income stored in thousands, multiply by 1000
    if "income" in lf.collect_schema().names():
        lf = lf.with_columns(pl.col("income").mul(1000).alias("income"))

    return lf


def _standardize_geographic_codes_period_2007_2017(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Standardize geographic codes for 2007-2017 data.

    Creates properly formatted FIPS codes and MSA/MD codes:
    - state_code: 2-digit string with leading zeros
    - county_code: 5-digit string (state + county) with leading zeros
    - census_tract: 11-digit string (state + county + tract) with leading zeros
    - msa_md: 5-digit string with leading zeros

    2007-2017 format (after renaming):
    - state_code: numeric 1-2 digits (or already 2-digit string)
    - county_code: numeric 3 digits (county within state)
    - census_tract: string/numeric format (varies by year)
    - msa_md: numeric variable length

    Post-standardization format:
    - state_code: "01" to "56" (2-digit FIPS)
    - county_code: "01001" (state + county, 5-digit)
    - census_tract: "01001950902" (state + county + tract, 11-digit)
    - msa_md: "01234" (5-digit MSA/MD code)

    Args:
        lf: Input lazy frame with geographic codes

    Returns:
        Lazy frame with standardized geographic codes
    """
    # Step 1: Standardize state_code to 2-digit string with leading zeros
    if "state_code" in lf.collect_schema().names():
        lf = lf.with_columns(
            pl.col("state_code")
            .cast(pl.Int64, strict=False)
            .cast(pl.String)
            .str.zfill(2)
            .alias("state_code")
        )

    # Step 2: Create 5-digit county_code (state + county)
    if "county_code" in lf.collect_schema().names() and "state_code" in lf.collect_schema().names():
        lf = lf.with_columns(
            (
                pl.col("state_code")
                + pl.col("county_code").cast(pl.Int64, strict=False).cast(pl.String).str.zfill(3)
            ).alias("county_code")
        )

    # Step 3: Standardize census_tract to 11-digit string
    if "census_tract" in lf.collect_schema().names():
        lf = lf.with_columns(
            (
                pl.col("state_code")
                + pl.col("county_code").str.slice(-3, 3)
                + pl.col("census_tract")
                .cast(pl.Float64, strict=False)
                .mul(100)
                .round(0)
                .cast(pl.Int64, strict=False)
                .cast(pl.String)
                .str.zfill(6)
            ).alias("census_tract")
        )

    # Step 4: Standardize msa_md to 5-digit string with leading zeros
    if "msa_md" in lf.collect_schema().names():
        lf = lf.with_columns(
            pl.col("msa_md")
            .cast(pl.Float64, strict=False)
            .cast(pl.Int64, strict=False)
            .cast(pl.String)
            .str.zfill(5)
            .alias("msa_md")
        )

    return lf


def _infer_pre2018_file_type_from_name(name: str) -> str:
    """Infer a single-character file_type code from a pre-2018 filename.

    'a' = three-year aggregate, 'b' = one-year, 'c' = public LAR,
    'd' = nationwide, 'e' = MLAR.
    """
    lower = name.lower()
    if "three_year" in lower:
        return "a"
    elif "one_year" in lower:
        return "b"
    elif "public_lar" in lower:
        return "c"
    elif "nationwide" in lower:
        return "d"
    elif "mlar" in lower:
        return "e"
    else:
        raise ValueError(f"Unknown file type: {name}")


def build_silver_period_2007_2017(
    dataset: Literal["loans"],
    min_year: int = 2007,
    max_year: int = 2017,
    overwrite: bool = False,
) -> None:
    """Create hive-partitioned silver layer for 2007–2017 data.

    Processes bronze parquet files one-at-a-time, applies light standardization
    (renames and integer-friendly casting), and writes to
    data/silver/<dataset>/period_2007_2017/activity_year=YYYY/file_type=X/.

    Args:
        dataset: Dataset to process.
        min_year: First year to process.
        max_year: Last year to process.
        overwrite: Whether to replace existing silver files.
    """
    bronze_folder = HMDAConfig.get_dataset_dir("bronze", dataset, "period_2007_2017")
    silver_folder = HMDAConfig.get_dataset_dir("silver", dataset, "period_2007_2017")
    silver_folder.resolve().mkdir(parents=True, exist_ok=True)

    for year in range(min_year, max_year + 1):
        # When overwrite=True, clear ONLY the year(s) being rebuilt — never the
        # entire silver folder, which would wipe years outside the requested
        # range (see incident 2026-05-15 / post2018 fix).
        if overwrite:
            year_dir = silver_folder / f"activity_year={year}"
            if year_dir.exists():
                shutil.rmtree(year_dir)

        for file in sorted(bronze_folder.glob(f"*{year}*.parquet")):
            lf = pl.scan_parquet(file, low_memory=True)

            # Apply column renames (only renames columns that exist)
            existing_cols = lf.collect_schema().names()
            renames_to_apply = {
                old: new for old, new in RENAME_DICTIONARY.items() if old in existing_cols
            }
            if renames_to_apply:
                logger.debug("Renaming %d columns: %s", len(renames_to_apply), renames_to_apply)
                lf = lf.rename(renames_to_apply)

            # Destring and cast integer columns to consistent Int64 types
            lf = _destring_and_cast_hmda_cols_2007_2017(lf)

            # Standardize geographic codes (state, county, census_tract, msa_md)
            lf = _standardize_geographic_codes_period_2007_2017(lf)

            # Drop derived columns that only appear in some files (inconsistent)
            lf = lf.drop(DERIVED_COLUMNS, strict=False)

            # Ensure partition keys exist
            if "activity_year" not in lf.collect_schema().names():
                lf = lf.with_columns(pl.lit(year).alias("activity_year"))

            file_type_code = _infer_pre2018_file_type_from_name(file.name)
            lf = lf.with_columns(pl.lit(file_type_code).alias("file_type"))

            # Stable per-record locator, identical scheme to post-2018
            # ({year}{file_type}_{row:09d}). Post-2018 assigns this at bronze via
            # scan_csv(row_index_name=...); pre-2018 bronze predates the column,
            # so we assign it here over the bronze parquet (whose row order is the
            # raw-CSV order). This gives pre-2018 a uniform join key — and in
            # particular gives 2017 a usable identifier, since its native
            # sequence_number is entirely null.
            lf = lf.with_row_index(HMDA_INDEX_COLUMN)
            lf = append_hmda_index(lf, year, file_type_code)

            # Validate and write using hive partitioning
            if lf.limit(1).collect().height == 0:
                logger.warning("Skipping empty file: %s", file)
                continue

            # Close-to-final dtypes + schema enforcement.
            lf = _apply_hmda_spec(lf, dataset, "period_2007_2017")

            lf.sink_parquet(
                pl.PartitionBy(
                    str(silver_folder),
                    key=["activity_year", "file_type"],
                    include_key=True,
                ),
                mkdir=True,
            )


def _harmonize_schema(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Harmonize schema for post-2018 HMDA data.

    This function consolidates all schema transformations including:
    - Destringing numeric variables
    - Casting integer-like floats to integers
    - Standardizing census tract format
    - Handling exempt/special values

    Args:
        lf: HMDA data with numeric columns represented as strings.
        schema: ``dict[str, pl.DataType | str]``, optional. Schema dictionary for
            determining target integer types. If None, defaults to Int64.

    Returns:
        LazyFrame with harmonized schema including properly typed numeric fields
        and formatted census tract column.
    """
    # Get existing columns once at the start
    lf_columns = lf.collect_schema().names()

    # Replace exempt columns with -99999 (only if they exist)
    for exempt_col in POST2018_EXEMPT_COLUMNS:
        if exempt_col in lf_columns:
            lf = lf.with_columns(pl.col(exempt_col).replace("Exempt", "-99999").alias(exempt_col))

    # Clean Units (only if column exists)
    if "total_units" in lf_columns:
        lf = lf.with_columns(
            pl.col("total_units")
            .replace(
                ["5-24", "25-49", "50-99", "100-149", ">149"],
                [5, 6, 7, 8, 9],
            )
            .cast(pl.Int16, strict=False)
            .alias("total_units")
        )

    # Clean Age (only if columns exist)
    for replace_column in ["applicant_age", "co_applicant_age"]:
        if replace_column in lf_columns:
            lf = lf.with_columns(
                pl.col(replace_column)
                .replace(
                    ["<25", "25-34", "35-44", "45-54", "55-64", "65-74", ">74"],
                    [1, 2, 3, 4, 5, 6, 7],
                )
                .cast(pl.Int16, strict=False)
                .alias(replace_column)
            )

    # Clean Age Dummy Variables (only if columns exist)
    for replace_column in ["applicant_age_above_62", "co_applicant_age_above_62"]:
        if replace_column in lf_columns:
            lf = lf.with_columns(
                pl.col(replace_column)
                .replace(
                    ["No", "no", "NO", "Yes", "yes", "YES", "Na", "na", "NA"],
                    [0, 0, 0, 1, 1, 1, None, None, None],
                )
                .cast(pl.Int16, strict=False)
                .alias(replace_column)
            )

    # Clean Debt-to-Income (only if column exists)
    if "debt_to_income_ratio" in lf_columns:
        lf = lf.with_columns(
            pl.col("debt_to_income_ratio")
            .replace(
                ["<20%", "20%-<30%", "30%-<36%", "50%-60%", ">60%", "Exempt"],
                [10, 20, 30, 50, 60, -99999],
            )
            .cast(pl.Int64, strict=False)
            .alias("debt_to_income_ratio")
        )

    # Clean Conforming Loan Limit (only if column exists)
    if "conforming_loan_limit" in lf_columns:
        lf = lf.with_columns(
            pl.col("conforming_loan_limit")
            .replace(["NC", "C", "U", "NA"], [0, 1, -99999, -99999])
            .cast(pl.Int64, strict=False)
            .alias("conforming_loan_limit")
        )

    # Cast safe strings to floats
    for column in POST2018_FLOAT_COLUMNS:
        if column in lf_columns:
            lf = lf.with_columns(pl.col(column).cast(pl.Float64, strict=False).alias(column))
    for column in POST2018_INTEGER_COLUMNS:
        if column in lf_columns:
            lf = lf.with_columns(pl.col(column).cast(pl.Int64, strict=False).alias(column))

    # Clean income columns (only if column exists)
    if "income" in lf_columns:
        lf = lf.with_columns(
            pl.col("income").mul(1000).replace([-99999000], [-99999]).alias("income")
        )

    # Standardize census_tract column format (if present)
    if "census_tract" in lf_columns:
        lf = (
            lf.cast({"census_tract": pl.Float64}, strict=False)
            .cast({"census_tract": pl.Int64}, strict=False)
            .cast({"census_tract": pl.String}, strict=False)
            .with_columns(pl.col("census_tract").str.zfill(11).alias("census_tract"))
        )

    # Standardize county_code column format (if present)
    if "county_code" in lf_columns:
        lf = (
            lf.cast({"county_code": pl.Float64}, strict=False)
            .cast({"county_code": pl.Int64}, strict=False)
            .cast({"county_code": pl.String}, strict=False)
            .with_columns(pl.col("county_code").str.zfill(5).alias("county_code"))
        )

    return lf


def build_silver_post2018(
    dataset: Literal["loans", "panel", "transmittal_series"],
    min_year: int = 2018,
    max_year: int = 2025,
    overwrite: bool = False,
) -> None:
    """Create hive-partitioned silver layer for post-2018 data.

    Processes bronze parquet files one-at-a-time, applies standard cleaning
    transforms with schema-guided typing, and writes to
    data/silver/<dataset>/post2018/activity_year=YYYY/file_type=X/.
    """
    bronze_folder = HMDAConfig.get_dataset_dir("bronze", dataset, "post2018")
    silver_folder = HMDAConfig.get_dataset_dir("silver", dataset, "post2018")
    silver_folder.resolve().mkdir(parents=True, exist_ok=True)

    for year in range(min_year, max_year + 1):
        # When overwrite=True, clear ONLY the year(s) being rebuilt — never the
        # entire silver folder, which would wipe years outside the requested
        # range (see incident 2026-05-15).
        if overwrite:
            year_dir = silver_folder / f"activity_year={year}"
            if year_dir.exists():
                shutil.rmtree(year_dir)

        for file in bronze_folder.glob(f"*{year}*.parquet"):
            lf = pl.scan_parquet(file, low_memory=True)

            # Apply column renames (only renames columns that exist)
            existing_cols = lf.collect_schema().names()
            renames_to_apply = {
                old: new for old, new in RENAME_DICTIONARY.items() if old in existing_cols
            }
            if renames_to_apply:
                logger.debug("Renaming %d columns: %s", len(renames_to_apply), renames_to_apply)
                lf = lf.rename(renames_to_apply)

            # Apply schema harmonization (type conversions) to all datasets
            lf = _harmonize_schema(lf)

            # Close-to-final dtypes + schema enforcement.
            lf = _apply_hmda_spec(lf, dataset, "post2018")

            # Write using hive partitioning
            lf.sink_parquet(
                pl.PartitionBy(
                    str(silver_folder),
                    key=["activity_year", "file_type"],
                    include_key=True,
                ),
                mkdir=True,
            )
