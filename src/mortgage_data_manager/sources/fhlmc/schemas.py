"""Schema loading and parsing functions.

The Single-Family Loan-Level Dataset layouts are transcribed from Freddie Mac's file
layout workbooks (published on the Single-Family Loan-Level Dataset page,
https://www.freddiemac.com/research/datasets/sf-loanlevel-dataset) into
``schemas/fhlmc/sf_file_layout_july_2026.csv`` (Release 47) and
``schemas/fhlmc/sf_file_layout.csv`` (the earlier layout). Each CSV has one row per
field: ``File`` (the workbook's sheet, e.g. ``Origination Data File``),
``Field Position``, ``Attribute Name``, ``Data Type & Format`` and ``Max Length``.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import polars as pl

from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Release 47 (July 2026) standard-dataset layout change
# ---------------------------------------------------------------------------
# Freddie reissued the WHOLE standard Single-Family Loan-Level Dataset
# (1999Q1-2026Q1) at Release 47 under new file names and new layouts:
#
#   orig_YYYYQn.txt  31 fields (was historical_data_YYYYQn.txt,      32)
#   perf_YYYYQn.txt  35 fields (was historical_data_time_YYYYQn.txt, 32)
#
# Two attributes MOVED out of origination into the monthly file (Servicer Name,
# Mortgage Insurance Cancellation Indicator), two are NEW (origination
# VantageScore 4.0, performance Bankruptcy Cramdown Costs), and ~10 were
# renamed. The renames adopt the vocabulary of Freddie's CRT reference-pool
# layout (``Classic FICO``, ``Loan Identifier``, ``Special Eligibility
# Program``, ...).
#
# Bronze stores the R47 names verbatim (bronze stays faithful to source); the
# silver builders rename to the established names so existing consumers keep
# working. See Freddie's "Disclosure Changes Summary (Effective July 2026)" at
# https://www.freddiemac.com/research/datasets/sf-loanlevel-dataset.
#
# The non-standard (``historical_data_excl_*``) bundle was NOT reissued and
# still carries the pre-R47 layout, so both eras must load side by side.

#: R47 origination name -> pre-R47 name. Names not listed are unchanged.
#: ``VantageScore® 4.0`` is new (no pre-R47 counterpart) but is de-symbolled to
#: match the ``VantageScore 4.0`` spelling of Freddie's CRT reference-pool layout.
R47_ORIGINATION_RENAMES: dict[str, str] = {
    "Classic FICO®": "Credit Score",
    "First Time Homebuyer Indicator": "First Time Homebuyer Flag",
    "Prepayment Penalty Indicator": "Prepayment Penalty Mortgage (PPM) Flag",
    "Amortization Type": "Amortization Type (Formerly Product Type)",
    "Loan Identifier": "Loan Sequence Number",
    "Special Eligibility Program": "Program Indicator",
    "VantageScore® 4.0": "VantageScore 4.0",
}

#: R47 performance name -> pre-R47 name. The three appended attributes
#: (Mortgage Insurance Cancellation Indicator, Servicer Name, Bankruptcy
#: Cramdown Costs) are absent here — they keep their R47 names into silver.
R47_PERFORMANCE_RENAMES: dict[str, str] = {
    "Loan Identifier": "Loan Sequence Number",
    "Period": "Monthly Reporting Period",
    "Underwriting Defect and Major Servicing Defect Settlement Date": "Defect Settlement Date",
    "Current Non-Interest Bearing UPB": "Current Deferred UPB",
    "Total Expenses": "Expenses",
    "Actual Loss": "Actual Loss Calculation",
    "Cumulative Modification Costs": "Modification Cost",
    "Interest Rate Step Indicator": "Step Modification Flag",
    "Payment Deferral Flag": "Deferred Payment Plan",
    "Borrower Assistance Plan": "Borrower Assistance Status Code",
    "Current Period Modification Costs": "Current Month Modification Cost",
    "Current Interest Bearing UPB": "Interest Bearing UPB",
}

#: Attributes that R47 moved OUT of the origination file into the monthly
#: performance file. Origination silver keeps the columns (consumers select
#: them) but they arrive as typed nulls for R47-sourced vintages — the values
#: now live per-month in the performance panel.
R47_MOVED_OFF_ORIGINATION: tuple[str, ...] = (
    "Servicer Name",
    "Mortgage Insurance Cancellation Indicator",
)


def is_r47_origination(column_names: Iterable[str]) -> bool:
    """Return True if these bronze origination columns are the R47 layout.

    Detected from the column set rather than the filename so a part-migrated
    bronze directory (some quarters rebuilt, some not) still resolves per file.

    Args:
        column_names: Column names of a bronze origination parquet.

    Returns:
        True when the columns are the R47 origination layout.
    """
    return "Loan Identifier" in set(column_names)


def is_r47_performance(column_names: Iterable[str]) -> bool:
    """Return True if these bronze performance columns are the R47 layout.

    Args:
        column_names: Column names of a bronze performance parquet.

    Returns:
        True when the columns are the R47 performance layout.
    """
    return "Period" in set(column_names)


def load_standard_schemas(
    legacy_path: Path,
    r47_path: Path,
) -> dict[str, dict]:
    """Load both standard-dataset layout eras, keyed for the bronze importer.

    Args:
        legacy_path: Pre-R47 layout CSV (still used by the non-standard
            ``historical_data_excl_*`` bundle).
        r47_path: Release 47 layout CSV (the reissued standard dataset).

    Returns:
        Schemas keyed ``origination`` / ``performance`` (pre-R47) and
        ``origination_r47`` / ``performance_r47``.
    """
    schemas = load_all_schemas(legacy_path)
    for key, schema in load_all_schemas(r47_path).items():
        schemas[f"{key}_r47"] = schema
    return schemas


#: Column types of the layout CSVs.
LAYOUT_CSV_SCHEMA: dict[str, type[pl.DataType]] = {
    "File": pl.Utf8,
    "Field Position": pl.Int64,
    "Attribute Name": pl.Utf8,
    "Data Type & Format": pl.Utf8,
    "Max Length": pl.Int64,
}


def load_all_schemas(schema_path: Path) -> dict[str, dict]:
    """Load the schema of every data file described in one layout CSV.

    Args:
        schema_path: Path to the layout CSV (see the module docstring).

    Returns:
        Dict with keys: 'origination', 'performance', 'reperforming'
        Each containing column mappings and type conversions.
    """
    logger.info("Loading schemas from %s", schema_path)

    layout = pl.read_csv(schema_path, schema=LAYOUT_CSV_SCHEMA)
    files = layout["File"].unique(maintain_order=True).to_list()
    logger.info("Available files: %s", files)

    schemas = {}

    for sheet_name in files:
        df_schema = layout.filter(pl.col("File") == sheet_name).drop("File")
        schema_key = map_sheet_to_type(sheet_name)

        if schema_key:
            schemas[schema_key] = parse_schema_dataframe(df_schema)
            logger.info(
                "Loaded schema for %s from sheet '%s' (%s columns)",
                schema_key,
                sheet_name,
                len(schemas[schema_key]["column_names"]),
            )

    return schemas


def map_sheet_to_type(sheet_name: str) -> str | None:
    """Map a layout's ``File`` name (the workbook sheet name) to a data type.

    Args:
        sheet_name: The ``File`` value, e.g. ``Origination Data File``

    Returns:
        'origination', 'performance', 'reperforming', or None
    """
    sheet_lower = sheet_name.lower()

    if "origination" in sheet_lower:
        return "origination"
    elif "performance" in sheet_lower or "monthly" in sheet_lower:
        return "performance"
    elif "rpl" in sheet_lower or "reperform" in sheet_lower:
        return "reperforming"

    return None


def parse_schema_dataframe(df_schema: pl.DataFrame) -> dict:
    """Parse schema dataframe to extract column names and types.

    Expected columns in schema df:
    - FIELD POSITION
    - ATTRIBUTE NAME (column name)
    - DATA TYPE & FORMAT (type specification)
    - MAX LENGTH (field length)

    Args:
        df_schema: DataFrame containing schema information

    Returns:
        Dict with:
        - 'column_names': List[str]
        - 'column_types': Dict[str, pl.DataType]
        - 'date_columns': List[str]
        - 'date_formats': Dict[str, str]

    Raises:
        ValueError: If no column holds the attribute names.
    """
    schema_info: dict[str, Any] = {
        "column_names": [],
        "column_types": {},
        "date_columns": [],
        "date_formats": {},
    }

    # Detect column name, type, and length columns
    name_col, type_col, length_col = detect_schema_columns(df_schema)
    if name_col is None:
        raise ValueError(f"No attribute-name column among {df_schema.columns}")

    # Extract schema information
    for row in df_schema.iter_rows(named=True):
        col_name = row[name_col]

        if col_name is None:
            continue

        col_name = str(col_name).strip()
        schema_info["column_names"].append(col_name)

        # Parse data type if available
        if type_col and row[type_col] is not None:
            col_type_str = str(row[type_col]).strip()

            # Get length if available (useful for date format detection)
            col_length = None
            if length_col and row[length_col] is not None:
                try:
                    col_length = int(row[length_col])
                except (ValueError, TypeError):
                    pass

            polars_type = parse_type_string(col_type_str)
            schema_info["column_types"][col_name] = polars_type

            # Track date columns
            if "date" in col_type_str.lower():
                schema_info["date_columns"].append(col_name)
                date_format = extract_date_format(col_type_str, col_length)
                if date_format:
                    schema_info["date_formats"][col_name] = date_format

    return schema_info


def detect_schema_columns(df_schema: pl.DataFrame) -> tuple[str | None, str | None, str | None]:
    """Detect which columns contain name, type, and length information.

    Args:
        df_schema: DataFrame containing schema

    Returns:
        Tuple of (name_column, type_column, length_column); an element is None
        when the corresponding column could not be detected.
    """
    name_col: str | None = None
    type_col: str | None = None
    length_col: str | None = None

    for col in df_schema.columns:
        col_lower = str(col).lower()

        # Look for attribute/field name column
        if "attribute" in col_lower or "field" in col_lower:
            if "name" in col_lower:
                name_col = col
        elif "name" in col_lower and not name_col:
            name_col = col

        # Look for type/format column
        if ("type" in col_lower or "format" in col_lower) and not type_col:
            type_col = col

        # Look for length column
        if "length" in col_lower and not length_col:
            length_col = col

    # Fallback: assume second column is name (after FIELD POSITION)
    if name_col is None and len(df_schema.columns) > 1:
        name_col = df_schema.columns[1]

    # Fallback: assume third column is type
    if type_col is None and len(df_schema.columns) > 2:
        type_col = df_schema.columns[2]

    # Fallback: assume fourth column is length
    if length_col is None and len(df_schema.columns) > 3:
        length_col = df_schema.columns[3]

    return name_col, type_col, length_col


def parse_type_string(type_str: str) -> type[pl.DataType] | pl.DataType:
    """Convert type string from schema to Polars data type.

    Examples:
    - "Numeric - 12,2" -> Float64
    - "Numeric" -> Int64
    - "Date" -> Utf8 (will convert after parsing format)
    - "Alpha" -> Utf8
    - "Alpha Numeric" -> Utf8

    Args:
        type_str: Type specification from schema

    Returns:
        Polars DataType
    """
    type_lower = type_str.lower()

    # Date types
    if "date" in type_lower:
        return pl.Utf8  # Load as string, convert after parsing format

    # Alpha or Alpha Numeric -> string (check this BEFORE numeric)
    if "alpha" in type_lower:
        return pl.Utf8

    # Numeric types with decimal places (e.g., "Numeric - 12,2")
    if "numeric" in type_lower:
        # If it has a comma in the format (X,Y), it's a decimal/float
        if "," in type_str:
            return pl.Float64
        else:
            # Plain numeric without decimals
            return pl.Int64

    # Default to string
    return pl.Utf8


def extract_date_format(type_str: str, length: int | None = None) -> str | None:
    """Extract date format from type string and length.

    Examples:
    - "Date" with length 6 -> "%Y%m" (YYYYMM)
    - "Date" with length 8 -> "%Y%m%d" (YYYYMMDD)
    - "Date YYYYMM" -> "%Y%m"
    - "Date YYYYMMDD" -> "%Y%m%d"

    Args:
        type_str: Type specification containing date format
        length: Optional field length to infer format

    Returns:
        Python strftime format string, or None if not detected
    """
    type_str = type_str.upper()

    # Check for explicit format in the string
    if "YYYYMMDD" in type_str:
        return "%Y%m%d"
    elif "YYYYMM" in type_str:
        return "%Y%m"
    elif "YYYY-MM-DD" in type_str:
        return "%Y-%m-%d"
    elif "MM/DD/YYYY" in type_str:
        return "%m/%d/%Y"

    # Infer from length if "Date" with no explicit format
    if "DATE" in type_str and length:
        if length == 6:
            return "%Y%m"  # YYYYMM
        elif length == 8:
            return "%Y%m%d"  # YYYYMMDD

    return None
