"""Schema loading and parsing functions."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import polars as pl

from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)


def load_all_schemas(schema_path: Path) -> dict[str, dict]:
    """Load schemas from all relevant tabs in the xlsx file.

    Args:
        schema_path: Path to the Excel schema file

    Returns:
        Dict with keys: 'origination', 'performance', 'reperforming'
        Each containing column mappings and type conversions.
    """
    logger.info(f"Loading schemas from {schema_path}")

    excel_file = pd.ExcelFile(schema_path)
    logger.info(f"Available sheets: {excel_file.sheet_names}")

    schemas = {}

    for sheet_name in excel_file.sheet_names:
        # Skip the header row in the Excel file
        df_schema = pd.read_excel(schema_path, sheet_name=sheet_name, skiprows=1)
        schema_key = map_sheet_to_type(sheet_name)

        if schema_key:
            schemas[schema_key] = parse_schema_dataframe(df_schema)
            logger.info(f"Loaded schema for {schema_key} from sheet '{sheet_name}' ({len(schemas[schema_key]['column_names'])} columns)")

    return schemas


def map_sheet_to_type(sheet_name: str) -> str | None:
    """Map xlsx sheet name to data type.

    Args:
        sheet_name: Name of the Excel sheet

    Returns:
        'origination', 'performance', 'reperforming', or None
    """
    sheet_lower = sheet_name.lower()

    if 'origination' in sheet_lower:
        return 'origination'
    elif 'performance' in sheet_lower or 'monthly' in sheet_lower:
        return 'performance'
    elif 'rpl' in sheet_lower or 'reperform' in sheet_lower:
        return 'reperforming'

    return None


def parse_schema_dataframe(df_schema: pd.DataFrame) -> dict:
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
    """
    schema_info = {
        'column_names': [],
        'column_types': {},
        'date_columns': [],
        'date_formats': {}
    }

    # Detect column name, type, and length columns
    name_col, type_col, length_col = detect_schema_columns(df_schema)

    # Extract schema information
    for _, row in df_schema.iterrows():
        col_name = row[name_col]

        if pd.isna(col_name):
            continue

        col_name = str(col_name).strip()
        schema_info['column_names'].append(col_name)

        # Parse data type if available
        if type_col and not pd.isna(row[type_col]):
            col_type_str = str(row[type_col]).strip()

            # Get length if available (useful for date format detection)
            col_length = None
            if length_col and not pd.isna(row[length_col]):
                try:
                    col_length = int(row[length_col])
                except (ValueError, TypeError):
                    pass

            polars_type = parse_type_string(col_type_str)
            schema_info['column_types'][col_name] = polars_type

            # Track date columns
            if 'date' in col_type_str.lower():
                schema_info['date_columns'].append(col_name)
                date_format = extract_date_format(col_type_str, col_length)
                if date_format:
                    schema_info['date_formats'][col_name] = date_format

    return schema_info


def detect_schema_columns(df_schema: pd.DataFrame) -> tuple[str, str | None, str | None]:
    """Detect which columns contain name, type, and length information.

    Args:
        df_schema: DataFrame containing schema

    Returns:
        Tuple of (name_column, type_column, length_column)
    """
    name_col = None
    type_col = None
    length_col = None

    for col in df_schema.columns:
        col_lower = str(col).lower()

        # Look for attribute/field name column
        if 'attribute' in col_lower or 'field' in col_lower:
            if 'name' in col_lower:
                name_col = col
        elif 'name' in col_lower and not name_col:
            name_col = col

        # Look for type/format column
        if ('type' in col_lower or 'format' in col_lower) and not type_col:
            type_col = col

        # Look for length column
        if 'length' in col_lower and not length_col:
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


def parse_type_string(type_str: str) -> pl.DataType:
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
    if 'date' in type_lower:
        return pl.Utf8  # Load as string, convert after parsing format

    # Alpha or Alpha Numeric -> string (check this BEFORE numeric)
    if 'alpha' in type_lower:
        return pl.Utf8

    # Numeric types with decimal places (e.g., "Numeric - 12,2")
    if 'numeric' in type_lower:
        # If it has a comma in the format (X,Y), it's a decimal/float
        if ',' in type_str:
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
    if 'YYYYMMDD' in type_str:
        return '%Y%m%d'
    elif 'YYYYMM' in type_str:
        return '%Y%m'
    elif 'YYYY-MM-DD' in type_str:
        return '%Y-%m-%d'
    elif 'MM/DD/YYYY' in type_str:
        return '%m/%d/%Y'

    # Infer from length if "Date" with no explicit format
    if 'DATE' in type_str and length:
        if length == 6:
            return '%Y%m'  # YYYYMM
        elif length == 8:
            return '%Y%m%d'  # YYYYMMDD

    return None
