#!/usr/bin/env python3
"""GNMA Import - Silver Layer.

Functions for transforming bronze parquet files into structured data (bronze → silver layer).

Functions:
    - transform_fixed_width: Process fixed-width format files
    - transform_delimited: Process delimited format files
    - import_file_silver: Transform a single file to silver
    - import_prefix_silver: Transform all files for a prefix to silver
    - detect_file_format: Detect if file is delimited or fixed-width
    - load_schema_by_record_type: Load schema grouped by record type
    - analyze_prefix_format: Analyze format for a prefix

"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file

from .config import ProcessorConfig
from .utils import ProcessingResult, load_prefix_dictionary

logger = get_logger(__name__)

__all__ = [
    "transform_fixed_width",
    "transform_delimited",
    "import_file_silver",
    "import_prefix_silver",
    "detect_file_format",
    "load_schema_by_record_type",
    "analyze_prefix_format",
]


# =============================================================================
# Schema/Format Detection (from parsing.py)
# =============================================================================


def detect_file_format(schema_df: pd.DataFrame) -> str:
    """Detect whether a file is delimited or fixed-width based on schema.

    Args:
        schema_df: DataFrame containing the schema definition

    Returns:
        'DELIMITED', 'FIXED_WIDTH', or 'UNKNOWN'
    """
    columns = list(schema_df.columns)
    has_max_length = 'Max Length' in columns
    has_begin_end = 'Begin' in columns and 'End' in columns
    has_format = 'Format' in columns

    if has_max_length and has_format:
        return 'DELIMITED'
    elif has_begin_end:
        return 'FIXED_WIDTH'
    else:
        return 'UNKNOWN'


def load_schema_by_record_type(
    prefix: str,
    schema_folder: str | Path,
    config: ProcessorConfig | None = None
) -> dict[str, pd.DataFrame]:
    """Load schema for a prefix, grouped by record type.

    Args:
        prefix: File prefix (e.g., 'llmon1')
        schema_folder: Folder containing combined schemas
        config: Optional configuration

    Returns:
        Dictionary with record types as keys and schemas as DataFrames

    Raises:
        FileNotFoundError: If schema file not found
    """
    schema_folder = Path(schema_folder)
    schema_file = schema_folder / f"{prefix}_combined_schema.csv"

    if not schema_file.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_file}")

    # Read the combined schema
    schema_df = pd.read_csv(schema_file)

    # Some schemas (e.g. hsavr) lack an explicit Item column; preserve
    # source row order as the positional ordering when sorting is not possible.
    sort_col = 'Item' if 'Item' in schema_df.columns else None

    schemas_by_record_type = {}
    if 'Record_Type' in schema_df.columns:
        for record_type in schema_df['Record_Type'].unique():
            if pd.notna(record_type):
                record_schema = schema_df[schema_df['Record_Type'] == record_type].copy()
                if sort_col:
                    record_schema = record_schema.sort_values(sort_col)
                # Coerce to str — record_type is used later to build output
                # Paths (clean_folder / prefix / rt / ...). If pandas inferred
                # int64 (e.g. remic1/remic2 use "1"/"2"/"3"), Path / int raises.
                schemas_by_record_type[str(record_type)] = record_schema
    else:
        # Single-record-type schemas (e.g. factorA1/A2/B1/B2, factorAAdd, factorAplat, hsavr).
        # The whole file is one record kind; synthesize a single "ALL" key.
        record_schema = schema_df.copy()
        if sort_col:
            record_schema = record_schema.sort_values(sort_col)
        schemas_by_record_type['ALL'] = record_schema

    logger.info(f"Loaded schema for {prefix}: {len(schemas_by_record_type)} record types")
    return schemas_by_record_type


def analyze_prefix_format(
    prefix: str,
    schema_folder: str | Path,
    config: ProcessorConfig | None = None
) -> dict:
    """Analyze the format for a prefix (delimited vs fixed-width).

    Args:
        prefix: File prefix to analyze
        schema_folder: Folder containing combined schemas
        config: Optional configuration

    Returns:
        Dictionary with format analysis results including:
        - prefix: The prefix name
        - format_type: 'DELIMITED', 'FIXED_WIDTH', or 'ERROR'
        - schema_exists: Whether schema file exists
        - schema_rows: Number of rows in schema (if exists)
        - schema_columns: Number of columns in schema (if exists)
        - has_record_types: Whether Record_Type column exists
        - record_type_count: Number of unique record types
        - record_types: List of record type values
        - column_names: List of column names in schema
        - error: Error message (if any)
    """
    schema_folder = Path(schema_folder)
    schema_file = schema_folder / f"{prefix}_combined_schema.csv"

    if not schema_file.exists():
        result = {
            'prefix': prefix,
            'format_type': 'ERROR',
            'error': f"Schema file not found: {schema_file}",
            'schema_exists': False
        }
        logger.warning(f"Schema file not found for {prefix}: {schema_file}")
        return result

    try:
        schema_df = pd.read_csv(schema_file)

        if schema_df.empty:
            result = {
                'prefix': prefix,
                'format_type': 'ERROR',
                'error': 'Empty schema file',
                'schema_exists': True
            }
            logger.warning(f"Empty schema file for {prefix}")
            return result

        # Detect file format
        file_format = detect_file_format(schema_df)
        columns = list(schema_df.columns)

        # Check for record types
        has_record_types = 'Record_Type' in schema_df.columns
        record_type_count = 0
        record_types = []

        if has_record_types:
            record_types = schema_df['Record_Type'].dropna().unique().tolist()
            record_type_count = len(record_types)

        result = {
            'prefix': prefix,
            'format_type': file_format,
            'schema_exists': True,
            'schema_rows': len(schema_df),
            'schema_columns': len(columns),
            'has_record_types': has_record_types,
            'record_type_count': record_type_count,
            'record_types': record_types,
            'column_names': columns,
            'error': None
        }

        logger.info(f"Analyzed format for {prefix}: {file_format}, {record_type_count} record types")
        return result

    except Exception as e:
        result = {
            'prefix': prefix,
            'format_type': 'ERROR',
            'error': str(e),
            'schema_exists': True
        }
        logger.error(f"Error analyzing format for {prefix}: {e}")
        return result


# =============================================================================
# Helper Functions
# =============================================================================


def _get_data_column_name(schema: pd.DataFrame) -> str:
    """Get the appropriate data column name from schema.

    Args:
        schema: Schema DataFrame

    Returns:
        Name of the data column

    Raises:
        ValueError: If no appropriate column found
    """
    if "Data Item" in schema.columns:
        return "Data Item"
    elif "Data Element" in schema.columns:
        return "Data Element"
    else:
        raise ValueError("Data Item or Data Element column not found in schema")


def _get_length_column_name(schema: pd.DataFrame) -> str:
    """Get the appropriate length column name from schema.

    Args:
        schema: Schema DataFrame

    Returns:
        Name of the length column

    Raises:
        ValueError: If no appropriate column found
    """
    if "Length" in schema.columns:
        return "Length"
    elif "Max Length" in schema.columns:
        return "Max Length"
    else:
        raise ValueError("Length or Max Length column not found in schema")


def _find_record_type_column(columns: list[str]) -> str | None:
    """Find the record type column in a list of column names.

    Args:
        columns: List of column names

    Returns:
        Name of record type column, or None if not found
    """
    for col in columns:
        if 'Record Type' in col:
            return col
    return None


# =============================================================================
# Transformation Functions
# =============================================================================


def transform_fixed_width(
    parquet_file: Path,
    schema: pd.DataFrame,
    record_type: str,
    text_column: str = "text_content"
) -> pl.LazyFrame:
    """Transform a parquet file using fixed-width parsing logic.

    Args:
        parquet_file: Path to the parquet file
        schema: Schema DataFrame for the record type
        record_type: Record type being processed
        text_column: Name of text content column

    Returns:
        Processed Polars LazyFrame
    """
    # Load the parquet file
    df = pl.scan_parquet(parquet_file)

    # Get column information from schema
    data_item_column = _get_data_column_name(schema)
    length_column = _get_length_column_name(schema)

    # Drop schema rows with null Length — these are typically section/group
    # labels pulled out of the PDF (e.g. hsavr "Pool Indicator and Type",
    # "FHA") that describe a grouping of subsequent fields rather than a
    # real fixed-width slice. Without this filter NaN widths propagate into
    # the slice offset arithmetic and polars rejects the u64 cast.
    schema = schema[schema[length_column].notna()].reset_index(drop=True)

    final_column_names = schema[data_item_column].tolist()
    widths = schema[length_column].tolist()

    # Replace NaN names with positional placeholders. PDF extraction sometimes
    # emits blank Data Item rows (mfplmon3 has 30 such rows of 176) — we still
    # need to occupy that slice position, just with a usable column name.
    final_column_names = [
        (x if isinstance(x, str) else f"_unnamed_{i}")
        for i, x in enumerate(final_column_names)
    ]

    # Add number suffixes to final column names with "Filler"
    final_column_names = [
        x + f"_{i}" if "Filler" in x else x
        for i, x in enumerate(final_column_names)
    ]

    # Ensure uniqueness — some PDF-extracted schemas have duplicate Data Items
    # (e.g. nissues "MIP 125 Number of Loans" appears twice in the F record).
    # Suffix any remaining collisions with their position so polars accepts them.
    seen: dict[str, int] = {}
    deduped: list[str] = []
    for i, name in enumerate(final_column_names):
        if name in seen:
            deduped.append(f"{name}_{i}")
        else:
            seen[name] = i
            deduped.append(name)
    final_column_names = deduped

    # Calculate slice positions
    slice_tuples = []
    offset = 0
    for width in widths:
        slice_tuples.append((offset, width))
        offset += width

    # Create final DataFrame by slicing text content
    df = df.with_columns([
        pl.col(text_column)
        .str.slice(slice_tuple[0], slice_tuple[1])
        .str.strip_chars()
        .alias(col)
        for slice_tuple, col in zip(slice_tuples, final_column_names)
    ]).drop(text_column)

    # Filter by record type. Skip the filter when record_type is the
    # synthesized "ALL" sentinel used for single-record-kind schemas —
    # the actual data values won't match "ALL" but every row in the file
    # already belongs to the one record kind.
    record_type_column = _find_record_type_column(df.collect_schema().names())
    if record_type_column and record_type != 'ALL':
        df = df.filter(pl.col(record_type_column) == record_type)

    logger.debug(f"Transformed fixed-width file: {parquet_file.name}")
    return df


def transform_delimited(
    parquet_file: Path,
    schema: pd.DataFrame,
    record_type: str,
    delimiter: str = "|",
    text_column: str = "text_content"
) -> pl.LazyFrame:
    """Transform a parquet file using delimited parsing logic.

    Args:
        parquet_file: Path to the parquet file
        schema: Schema DataFrame for the record type
        record_type: Record type being processed
        delimiter: Delimiter to use
        text_column: Name of text content column

    Returns:
        Processed Polars LazyFrame
    """
    # Load the parquet file
    df = pl.scan_parquet(parquet_file)

    # Get column names from schema
    data_element_column = _get_data_column_name(schema)
    final_column_names = schema[data_element_column].tolist()

    if not final_column_names:
        raise ValueError(f"No column names found in schema for record type {record_type}")

    # Replace NaN names with positional placeholders.
    final_column_names = [
        (x if isinstance(x, str) else f"_unnamed_{i}")
        for i, x in enumerate(final_column_names)
    ]

    # Dedupe schema column names — PDF extraction sometimes emits placeholder
    # labels like "Col1" more than once (e.g. LoanPerf LP record has three
    # "Col1" entries). Suffix collisions by position so the rename below
    # doesn't produce duplicate output columns.
    seen: dict[str, int] = {}
    deduped: list[str] = []
    for i, name in enumerate(final_column_names):
        if name in seen:
            deduped.append(f"{name}_{i}")
        else:
            seen[name] = i
            deduped.append(name)
    final_column_names = deduped

    number_columns = len(final_column_names)

    # Split text content into columns
    df_record = df.with_columns(
        pl.col(text_column)
        .str.split_exact(delimiter, number_columns - 1)
        .alias("parts")
    ).unnest("parts").drop(text_column)

    # Rename columns using final_column_names
    df_record = df_record.rename({
        col: final_column_names[i]
        for i, col in enumerate(df_record.collect_schema().names())
    })

    # Filter by record type
    record_type_column = _find_record_type_column(df_record.collect_schema().names())
    if record_type_column:
        df_record = df_record.filter(pl.col(record_type_column) == record_type)

    logger.debug(f"Transformed delimited file: {parquet_file.name}")
    return df_record


# =============================================================================
# Numeric normalization (fixed-point decoding + typing)
# =============================================================================

# Prefixes whose silver is normalized: the all-String sliced columns are cast to
# their real numeric types AND the implied decimal is applied (e.g. an interest
# rate "03500" -> 3.5). Other prefixes keep the raw fixed-point strings (consumers
# decode them); add a prefix here only after its consumers stop decoding.
# Activated incrementally (a prefix's consumers must stop decoding the now-decoded
# silver first). LIVE: dailyllmni — the OB<->GNMA matcher (match_optimal_blue_mbs)
# has been updated and re-validated (reproduces 421,784 pinned exactly).
# PENDING follow-up before adding more prefixes here:
#   - combined.builders loan_issuance reads dailyllmni via the shared _gnma_rate/
#     _gnma_ratio/_gnma_upb helpers, which ALSO decode the still-raw llmon for
#     loan_performance — so those need per-source handling (stop decoding
#     dailyllmni, keep decoding llmon) before the next loan_issuance rebuild.
#   - llmon1/llmon2 (TRACE<->GNMA) and nimonSFPS/nissues (securities) once their
#     consumers are updated.
NORMALIZE_PREFIXES: frozenset[str] = frozenset({"dailyllmni"})

# Loan-level (fixed-width layout) interest-rate tokens: those fields are 9(2)v9(3)
# (÷1000); every other loan-level Numeric-Float field is a ratio or money amount
# stored 9(n)v9(2) (÷100). The layout schemas don't carry the picture clause, so
# this documented split supplies the implied decimal the disclosure schemas put in
# their ``Format`` column.
_RATE_TOKENS: tuple[str, ...] = (
    "interest rate", "mip", "ceiling", "floor", "margin", "coupon", "prospective interest",
)
_FORMAT_RE = re.compile(r"(\d+)\.(\d+)")


def _fixed_width_scale(name: str) -> int:
    """Implied-decimal divisor for a FIXED-WIDTH Numeric-Float field.

    Fixed-width values are zero-padded integers, so the decimal must be re-applied:
    factors are 9(1)v9(8) (÷10^8); interest-rate-like fields are 9(2)v9(3) (÷1000);
    every other ratio / money amount is 9(n)v9(2) (÷100).
    """
    low = name.lower()
    if "factor" in low:
        return 10**8
    if any(tok in low for tok in _RATE_TOKENS):
        return 1000
    return 100


def _format_kind(fmt: object) -> str | None:
    """Classify a disclosure ``Format`` value: 'X.Y' -> 'float' (Y>0) / 'int' (Y==0).

    Returns None for non-numeric formats (blank, dates ``YYYYMM``, extraction
    artifacts like ``Col4``) so those columns are left as their delimited strings.
    """
    m = _FORMAT_RE.fullmatch(str(fmt).strip())
    if not m:
        return None
    return "float" if int(m.group(2)) > 0 else "int"


def normalize_field_types(
    df: pl.LazyFrame, schema: pd.DataFrame, prefix: str
) -> pl.LazyFrame:
    """Decode the fixed-point numeric (Float) columns of the sliced silver to real values.

    The audit's headline normalization is the fixed-point decode: an interest rate
    ``"03500"`` -> ``3.5``. For FIXED-WIDTH files (zero-padded integers) the implied
    decimal is re-applied (:func:`_fixed_width_scale`: rate ÷1000, ratio/money ÷100,
    factor ÷10^8); for DELIMITED files the value already carries the decimal, so it
    is cast only. ONLY the Numeric-Float fields are touched — the integer-coded
    fields (counts, scores, FIPS/issuer codes, ``YYYYMMDD`` dates) stay as their
    sliced strings, because consumers read several of them as text (e.g.
    ``issuer_id != ""``, ``orig_date.str.to_date``) and cast the rest themselves.
    No-op outside :data:`NORMALIZE_PREFIXES`.
    """
    if prefix not in NORMALIZE_PREFIXES:
        return df

    data_col = _get_data_column_name(schema)
    present = set(df.collect_schema().names())
    is_delimited = "Format" in schema.columns  # DELIMITED schemas carry Format/Max Length
    exprs: list[pl.Expr] = []
    for _, row in schema.iterrows():
        name = row[data_col]
        if not isinstance(name, str) or name not in present:
            continue
        if is_delimited:
            # DELIMITED values already carry the decimal point -> cast only, no scale.
            if _format_kind(row.get("Format")) == "float":
                exprs.append(pl.col(name).cast(pl.Float64, strict=False).alias(name))
        elif "float" in str(row.get("polars_dtype", "")).lower():
            scale = _fixed_width_scale(name)
            exprs.append((pl.col(name).cast(pl.Float64, strict=False) / scale).alias(name))

    return df.with_columns(exprs) if exprs else df


def import_file_silver(
    parquet_file: Path,
    prefix: str,
    record_type: str,
    output_file: Path,
    schema_folder: str | Path,
    config: ProcessorConfig | None = None,
    file_format: str | None = None,
    date_format: str | None = None
) -> tuple[bool, int]:
    """Transform a single parquet file for a specific record type to silver layer.

    Args:
        parquet_file: Path to input parquet file
        prefix: File prefix
        record_type: Record type to process
        output_file: Path for output file
        schema_folder: Folder containing schemas
        config: Optional configuration
        file_format: Optional format override
        date_format: Optional ``strptime`` format for the date token in the
            filename. Defaults to ``config.date_format`` (``%Y%m``). Pass the
            prefix's own format for sources that use a different cadence (e.g.
            issrcutoff filenames are ``%Y%m%d``).

    Returns:
        Tuple of (success, record_count)
    """
    if config is None:
        config = ProcessorConfig()

    try:
        # Load schema
        schemas = load_schema_by_record_type(prefix, schema_folder, config)
        if record_type not in schemas:
            logger.warning(f"Record type '{record_type}' not found in schema for {prefix}")
            return False, 0

        schema = schemas[record_type]

        # Extract date from filename for schema filtering. Honor the prefix's own
        # date format (issrcutoff is daily, %Y%m%d) rather than the global default.
        ym = parquet_file.stem.split(f'{prefix}_')[1]
        ym_dt = pd.to_datetime(ym, format=date_format or config.date_format).strftime('%Y-%m-%d')

        # Filter schema by date range. Some single-version schemas (e.g.
        # factorA1/A2/B1/B2) have Min_Date / Max_Date all-null because the
        # layout has no dated versions. Fill null bounds with sentinels so
        # such rows match every month. Using fillna avoids pandas
        # short-circuit issues when comparing a float64 NaN column to a str.
        min_bounds = schema['Min_Date'].fillna('0000-01-01')
        max_bounds = schema['Max_Date'].fillna('9999-12-31')
        file_schema = schema[(min_bounds <= ym_dt) & (ym_dt <= max_bounds)]

        if file_schema.empty:
            logger.warning(f"No schema found for {prefix} on {ym_dt}")
            return False, 0

        # Detect format if not provided
        if file_format is None:
            analysis = analyze_prefix_format(prefix, schema_folder, config)
            file_format = analysis['format_type']

        # Process based on format
        if file_format == 'DELIMITED':
            df = transform_delimited(parquet_file, file_schema, record_type, config.default_delimiter)
        elif file_format == 'FIXED_WIDTH':
            df = transform_fixed_width(parquet_file, file_schema, record_type)
        else:
            logger.error(f"Unknown file format '{file_format}' for {prefix}")
            return False, 0

        # Cast + scale numeric columns (fixed-point decode) for normalized prefixes.
        df = normalize_field_types(df, file_schema, prefix)

        # Ensure output directory exists
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Save to clean directory
        df.sink_parquet(output_file)

        # Count records for reporting (parquet metadata only — no full read-back)
        record_count = pl.scan_parquet(output_file).select(pl.len()).collect().item()

        logger.debug(f"Processed {parquet_file.name} -> {output_file.name} ({record_count} records)")

        return True, record_count

    except Exception as e:
        logger.error(f"Error processing {parquet_file}: {e}")
        return False, 0


def import_prefix_silver(
    prefix: str,
    raw_folder: str | Path,
    clean_folder: str | Path,
    schema_folder: str | Path,
    config: ProcessorConfig | None = None,
    record_types: list[str] | None = None,
    show_progress: bool = True
) -> ProcessingResult:
    """Transform all parquet files for a specific prefix to silver layer.

    Args:
        prefix: Data prefix to process
        raw_folder: Folder containing staged parquet files (bronze layer)
        clean_folder: Output folder for structured data (silver layer)
        schema_folder: Folder containing schemas
        config: Optional configuration
        record_types: Optional list of record types to process
        show_progress: Whether to show progress updates

    Returns:
        ProcessingResult with detailed results
    """
    if config is None:
        config = ProcessorConfig()

    logger.info(f"Transforming prefix to silver: {prefix}")

    # Initialize result
    result = ProcessingResult()

    # Get parquet files for this prefix
    raw_folder = Path(raw_folder)
    parquet_files = list(raw_folder.rglob(f'{prefix}_*.parquet'))

    if not parquet_files:
        logger.warning(f"No parquet files found for prefix: {prefix}")
        return result

    result.total_files = len(parquet_files)

    # Load schema to get available record types
    try:
        schemas = load_schema_by_record_type(prefix, schema_folder, config)
        available_record_types = list(schemas.keys())

        # Use provided record types or all available
        if record_types is None:
            record_types = available_record_types
        else:
            # Validate requested record types
            invalid_types = [rt for rt in record_types if rt not in available_record_types]
            if invalid_types:
                logger.warning(f"Invalid record types for {prefix}: {invalid_types}")
                record_types = [rt for rt in record_types if rt in available_record_types]

    except Exception as e:
        logger.error(f"Error loading schema for {prefix}: {e}")
        return result

    # Detect file format once for the prefix
    analysis = analyze_prefix_format(prefix, schema_folder, config)
    file_format = analysis['format_type']
    logger.info(f"Detected format for {prefix}: {file_format}")

    # Resolve the prefix's date format from the dictionary (defaults to the
    # global %Y%m). issrcutoff filenames carry a full %Y%m%d date.
    date_format = config.date_format
    try:
        prefix_dict = load_prefix_dictionary(config.prefix_file)
        date_format = prefix_dict.get(prefix, {}).get('date_format', date_format)
    except (FileNotFoundError, ValueError) as e:
        logger.warning(f"Could not load prefix dictionary for date format; using default: {e}")

    # Process each file and record type combination
    total_operations = len(parquet_files) * len(record_types)
    operation_count = 0

    clean_folder = Path(clean_folder)

    for parquet_file in parquet_files:
        for rt in record_types:
            operation_count += 1

            if show_progress and operation_count % 10 == 0:
                logger.info(f"Progress: {operation_count}/{total_operations} operations")

            # Generate output file path
            ym = parquet_file.stem.split(f'{prefix}_')[1]
            output_file = clean_folder / prefix / rt / f'{prefix}_{ym}_{rt}.parquet'

            # Skip if file exists and not overwriting
            if not should_process_file(output_file, overwrite=not config.skip_existing):
                result.add_skip(str(parquet_file))
                continue

            # Transform the file
            success, record_count = import_file_silver(
                parquet_file, prefix, rt, output_file, schema_folder, config,
                file_format, date_format
            )

            if success:
                result.add_success(str(parquet_file), str(output_file), record_count, rt)
            else:
                result.add_failure(str(parquet_file), f"Failed to process record type {rt}")

    logger.info(f"Completed {prefix}: {result.successful}/{result.total_attempted} successful, "
                f"{result.total_records_processed} total records processed")

    return result
