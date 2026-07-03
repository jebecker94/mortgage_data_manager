#!/usr/bin/env python3
"""GNMA Schema - COBOL Format Parsing.

Functions for parsing COBOL format notation and converting values.

Functions:
    - parse_cobol_format: Parse COBOL format notation
    - convert_cobol_value: Convert a value using COBOL format
    - validate_cobol_value: Validate a value against COBOL format
    - create_pandas_dtype_mapping: Create pandas dtype mapping from schema
    - create_polars_dtype_mapping: Create polars dtype mapping from schema

"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd
import polars as pl

from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)


def parse_cobol_format(cobol_notation: str) -> dict[str, Any] | None:
    """Parse COBOL format notation into Python data types and processing instructions.

    Supported formats:
    - X(n): Alphanumeric field of length n
    - 9(n): Numeric integer field of length n
    - 9(m)v9(n): Numeric decimal with m digits before and n after decimal

    Args:
        cobol_notation: COBOL format string like 'X(9)', '9(6)', '9(13)v9(2)'

    Returns:
        Dictionary with type information, or None if parsing fails

    Examples:
        >>> parse_cobol_format('X(9)')
        {'python_type': str, 'pandas_dtype': 'string', 'polars_dtype': pl.Utf8, ...}

        >>> parse_cobol_format('9(6)')
        {'python_type': int, 'pandas_dtype': 'Int64', 'polars_dtype': pl.Int64, ...}

        >>> parse_cobol_format('9(13)v9(2)')
        {'python_type': float, 'pandas_dtype': 'float64', 'polars_dtype': pl.Float64, ...}
    """
    if not isinstance(cobol_notation, str):
        return None

    cobol_notation = cobol_notation.strip()

    # Handle alphanumeric fields: X(n) or just X
    if cobol_notation == 'X':
        return {
            'python_type': str,
            'pandas_dtype': 'string',
            'decimal_places': 0,
            'total_length': 1,
            'validation_pattern': '^.{1}$',
            'cobol_type': 'alphanumeric',
            'polars_dtype': pl.Utf8
        }

    x_match = re.match(r'^X\((\d+)\)$', cobol_notation)
    if x_match:
        length = int(x_match.group(1))
        return {
            'python_type': str,
            'pandas_dtype': 'string',
            'decimal_places': 0,
            'total_length': length,
            'validation_pattern': f'^.{{{length}}}$',
            'cobol_type': 'alphanumeric',
            'polars_dtype': pl.Utf8
        }

    # Handle numeric fields with implied decimal: 9(m)v9(n)
    decimal_match = re.match(r'^9\((\d+)\)v9\((\d+)\)$', cobol_notation)
    if decimal_match:
        before_decimal = int(decimal_match.group(1))
        after_decimal = int(decimal_match.group(2))
        total_length = before_decimal + after_decimal
        return {
            'python_type': float,
            'pandas_dtype': 'float64',
            'decimal_places': after_decimal,
            'total_length': total_length,
            'validation_pattern': f'^\\d{{{total_length}}}$',
            'cobol_type': 'numeric_decimal',
            'polars_dtype': pl.Float64
        }

    # Handle integer numeric fields: 9(n)
    int_match = re.match(r'^9\((\d+)\)$', cobol_notation)
    if int_match:
        length = int(int_match.group(1))
        return {
            'python_type': int,
            'pandas_dtype': 'Int64',
            'decimal_places': 0,
            'total_length': length,
            'validation_pattern': f'^\\d{{{length}}}$',
            'cobol_type': 'numeric_integer',
            'polars_dtype': pl.Int64
        }

    return None


def convert_cobol_value(raw_value: str, cobol_format_info: dict) -> Any:
    """Convert a raw string value using COBOL format information.

    Args:
        raw_value: Raw string value from file
        cobol_format_info: Output from parse_cobol_format()

    Returns:
        Converted value in appropriate Python type, or None if conversion fails
    """
    if not cobol_format_info or not isinstance(raw_value, str):
        return raw_value

    try:
        cobol_type = cobol_format_info['cobol_type']

        if cobol_type == 'alphanumeric':
            return raw_value
        elif cobol_type == 'numeric_integer':
            return int(raw_value)
        elif cobol_type == 'numeric_decimal':
            decimal_places = cobol_format_info['decimal_places']
            int_value = int(raw_value)
            float_value = int_value / (10 ** decimal_places)
            return float_value
        else:
            return raw_value

    except (ValueError, TypeError):
        return None


def validate_cobol_value(raw_value: str, cobol_format_info: dict) -> dict[str, Any]:
    """Validate a raw string value against COBOL format requirements.

    Args:
        raw_value: Raw string value from file
        cobol_format_info: Output from parse_cobol_format()

    Returns:
        Dictionary with 'is_valid' (bool) and 'error_message' (str)
    """
    if not cobol_format_info:
        return {'is_valid': False, 'error_message': 'No format information provided'}

    if not isinstance(raw_value, str):
        return {'is_valid': False, 'error_message': 'Value must be a string'}

    # Check length
    expected_length = cobol_format_info['total_length']
    if len(raw_value) != expected_length:
        return {
            'is_valid': False,
            'error_message': f'Expected length {expected_length}, got {len(raw_value)}'
        }

    # Check pattern
    pattern = cobol_format_info['validation_pattern']
    if not re.match(pattern, raw_value):
        cobol_type = cobol_format_info['cobol_type']
        if 'numeric' in cobol_type:
            return {'is_valid': False, 'error_message': 'Value contains non-numeric characters'}
        else:
            return {'is_valid': False, 'error_message': 'Value does not match expected pattern'}

    return {'is_valid': True, 'error_message': None}


def augment_with_cobol_dtypes(
    df: pd.DataFrame,
    cobol_column: str = "Remarks"
) -> pd.DataFrame:
    """Augment a DataFrame with pandas_dtype and polars_dtype columns inferred from COBOL notation.

    This adds two new columns to the DataFrame:
    - pandas_dtype: String representation of pandas dtype
    - polars_dtype: String representation of polars dtype

    Args:
        df: DataFrame with COBOL format notation
        cobol_column: Name of column containing COBOL notation

    Returns:
        DataFrame with added dtype columns
    """
    if df.empty or cobol_column not in df.columns:
        return df

    augmented = df.copy()

    # Initialize columns if they don't exist
    if 'pandas_dtype' not in augmented.columns:
        augmented['pandas_dtype'] = None
    if 'polars_dtype' not in augmented.columns:
        augmented['polars_dtype'] = None

    # Process each row
    for idx, row in augmented.iterrows():
        notation = row.get(cobol_column)
        if not isinstance(notation, str) or not notation.strip():
            continue

        # Parse COBOL format
        fmt = parse_cobol_format(notation)
        if not fmt:
            continue

        # Store pandas dtype
        augmented.at[idx, 'pandas_dtype'] = fmt.get('pandas_dtype')

        # Store string repr of polars dtype to avoid non-serializable objects
        polars_dt = fmt.get('polars_dtype')
        augmented.at[idx, 'polars_dtype'] = str(polars_dt)

    return augmented


def create_pandas_dtype_mapping(
    schema_df: pd.DataFrame,
    cobol_column: str = 'Remarks',
    field_name_column: str | None = None
) -> dict[str, str]:
    """Create a pandas dtype mapping from a schema DataFrame with COBOL notation.

    Parses COBOL format notation from the schema and creates a dictionary
    mapping field names to pandas dtypes. This can be used directly with
    pandas read_csv or other data loading functions.

    Args:
        schema_df: Schema DataFrame with COBOL format notation
        cobol_column: Name of column containing COBOL notation (default: 'Remarks')
        field_name_column: Optional field name column (auto-detects 'Data Item' or 'Data Element')

    Returns:
        Dictionary mapping field names to pandas dtypes

    Example:
        >>> schema = pd.DataFrame({
        ...     'Data Item': ['Pool Number', 'Issue Date', 'Interest Rate'],
        ...     'Remarks': ['X(6)', '9(8)', '9(2)v9(3)']
        ... })
        >>> dtype_map = create_pandas_dtype_mapping(schema)
        >>> # {'Pool Number': 'string', 'Issue Date': 'Int64', 'Interest Rate': 'float64'}
        >>> data = pd.read_csv('data.txt', dtype=dtype_map)
    """
    dtype_mapping = {}

    # Auto-detect field name column if not specified
    if field_name_column is None:
        if 'Data Item' in schema_df.columns:
            field_name_column = 'Data Item'
        elif 'Data Element' in schema_df.columns:
            field_name_column = 'Data Element'
        else:
            logger.warning("Could not find 'Data Item' or 'Data Element' column")
            return dtype_mapping

    for _, row in schema_df.iterrows():
        field_name = row.get(field_name_column)
        if not field_name or pd.isna(field_name):
            continue

        cobol_notation = row.get(cobol_column)
        if not cobol_notation or pd.isna(cobol_notation):
            continue

        format_info = parse_cobol_format(cobol_notation)
        if format_info:
            dtype_mapping[field_name] = format_info['pandas_dtype']

    logger.info(f"Created pandas dtype mapping for {len(dtype_mapping)} fields")
    return dtype_mapping


def create_polars_dtype_mapping(
    schema_df: pd.DataFrame,
    cobol_column: str = 'Remarks',
    field_name_column: str | None = None
) -> dict[str, Any]:
    """Create a Polars dtype mapping from a schema DataFrame with COBOL notation.

    Parses COBOL format notation from the schema and creates a dictionary
    mapping field names to polars dtypes. This can be used directly with
    polars DataFrame creation or read functions.

    Args:
        schema_df: Schema DataFrame with COBOL format notation
        cobol_column: Name of column containing COBOL notation (default: 'Remarks')
        field_name_column: Optional field name column (auto-detects 'Data Item' or 'Data Element')

    Returns:
        Dictionary mapping field names to Polars dtypes

    Example:
        >>> import polars as pl
        >>> schema = pd.DataFrame({
        ...     'Data Item': ['Pool Number', 'Issue Date', 'Interest Rate'],
        ...     'Remarks': ['X(6)', '9(8)', '9(2)v9(3)']
        ... })
        >>> dtype_map = create_polars_dtype_mapping(schema)
        >>> # {'Pool Number': pl.Utf8, 'Issue Date': pl.Int64, 'Interest Rate': pl.Float64}
        >>> data = pl.read_csv('data.txt', schema=dtype_map)

    Note:
        Requires Polars to be installed. If Polars is not available,
        the dtype objects may be string representations.
    """
    dtype_mapping = {}

    # Auto-detect field name column if not specified
    if field_name_column is None:
        if 'Data Item' in schema_df.columns:
            field_name_column = 'Data Item'
        elif 'Data Element' in schema_df.columns:
            field_name_column = 'Data Element'
        else:
            logger.warning("Could not find 'Data Item' or 'Data Element' column")
            return dtype_mapping

    for _, row in schema_df.iterrows():
        field_name = row.get(field_name_column)
        if not field_name or pd.isna(field_name):
            continue

        cobol_notation = row.get(cobol_column)
        if not cobol_notation or pd.isna(cobol_notation):
            continue

        format_info = parse_cobol_format(cobol_notation)
        if format_info:
            dtype_mapping[field_name] = format_info['polars_dtype']

    logger.info(f"Created polars dtype mapping for {len(dtype_mapping)} fields")
    return dtype_mapping


def create_polars_schema(
    schema_df: pd.DataFrame,
    cobol_column: str = 'Remarks',
    field_name_column: str | None = None
) -> dict[str, Any]:
    """Create a Polars schema dictionary from a schema DataFrame with COBOL notation.

    This is an alias for create_polars_dtype_mapping() and is the preferred
    format for Polars DataFrame creation with explicit schemas.

    Args:
        schema_df: Schema DataFrame with COBOL format notation
        cobol_column: Name of column containing COBOL notation (default: 'Remarks')
        field_name_column: Optional field name column (auto-detects 'Data Item' or 'Data Element')

    Returns:
        Polars schema mapping {column_name: polars_dtype}

    Example:
        >>> import polars as pl
        >>> schema = pd.DataFrame({
        ...     'Data Item': ['Pool Number', 'Issue Date', 'Interest Rate'],
        ...     'Remarks': ['X(6)', '9(8)', '9(2)v9(3)']
        ... })
        >>> polars_schema = create_polars_schema(schema)
        >>> df = pl.DataFrame(data, schema=polars_schema)

    Note:
        Requires Polars to be installed.
    """
    return create_polars_dtype_mapping(schema_df, cobol_column, field_name_column)

