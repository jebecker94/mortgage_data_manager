#!/usr/bin/env python3
"""GNMA Schema - Cleaning.

Functions for cleaning and organizing extracted schema data.

Functions:
    - clean_extracted_dataframe: Clean a raw extracted DataFrame
    - add_record_type_column: Extract and add record type information
    - extract_record_type_code: Parse record type from text
    - propagate_record_types_to_groups: Propagate record types across groups
    - extract_dates_from_filename: Extract date range from PDF filename

"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

import pandas as pd

from mortgage_data_manager.core.logging import get_logger

from ..config import SchemaReaderConfig

logger = get_logger(__name__)


def clean_extracted_dataframe(
    df: pd.DataFrame,
    filename: str,
    config: SchemaReaderConfig | None = None,
    add_record_types: bool = True
) -> pd.DataFrame:
    """Apply cleaning operations to a raw extracted DataFrame.

    This includes:
    - Grouping by Item resets
    - Filtering unwanted rows and groups
    - Record type extraction

    Args:
        df: Raw DataFrame from PDF extraction
        filename: Source filename for context
        config: Optional configuration
        add_record_types: Whether to extract record types

    Returns:
        Cleaned DataFrame
    """
    if config is None:
        config = SchemaReaderConfig()

    if df.empty:
        return df

    logger.info(f"Cleaning extracted data from {filename} (rows={len(df)})")

    # Normalize newlines in key columns
    for col in ['Data Item', 'Data Element']:
        if col in df.columns:
            df[col] = df[col].apply(
                lambda v: v.replace('\r', ' ').replace('\n', ' ') if isinstance(v, str) else v
            )

    # Step 0: Drop PyMuPDF placeholder rows. A definition wrapping over a PDF
    # page break leaves a row whose Item is a positional placeholder ('Col0',
    # 'Col1', ...) instead of a real item code. merge_continuation_table now
    # suppresses these at extraction, but historic/edge cases can still leak
    # one through - and because 'Col0' yields a numeric 0, the Item-numeric
    # filter below would otherwise keep it, shifting the record-type column and
    # silently emptying the affected silver table.
    for col in ['Item', 'Data Item']:
        if col in df.columns:
            placeholder_mask = df[col].astype(str).str.fullmatch(r'Col\d+')
            if placeholder_mask.any():
                logger.debug(f"Removed {int(placeholder_mask.sum())} placeholder '{col}' rows")
                df = df[~placeholder_mask]

    # Step 1: Add grouping logic based on Item field resets
    if 'Item' in df.columns:
        # Extract numeric part from Item column
        df['Item_numeric'] = df['Item'].astype(str).str.extract(r'(\d+)', expand=False)
        df['Item_numeric'] = pd.to_numeric(df['Item_numeric'], errors='coerce')

        # Filter out rows without valid Item numbers. Copy so the subsequent
        # group_id assignment writes to an owned frame, not a slice/view
        # (avoids pandas SettingWithCopyWarning and undefined write behavior).
        valid_item_mask = df['Item_numeric'].notna()
        df = df[valid_item_mask].copy()

        if len(df) > 0:
            # Create groups based on Item resets (vectorized)
            item_resets = (df['Item_numeric'] == 1) & (df['Item_numeric'].shift(1, fill_value=1) != 1)
            df['group_id'] = item_resets.cumsum() - 1  # Start from 0

            # Clean up temporary column
            df = df.drop('Item_numeric', axis=1)

    # Step 2: Filter unwanted rows
    initial_row_count = len(df)

    # Remove rows where 'Data Item' = 'Length of Record'
    if 'Data Item' in df.columns:
        mask = df['Data Item'] != 'Length of Record'
        df = df[mask]
        removed = initial_row_count - len(df)
        if removed > 0:
            logger.debug(f"Removed {removed} 'Length of Record' rows")

    # Remove rows where all original schema fields are missing
    original_schema_columns = [
        col for col in df.columns
        if col not in ['File', 'Min_Date', 'Max_Date', 'group_id']
    ]
    if original_schema_columns:
        mask = df[original_schema_columns].notna().any(axis=1)
        rows_before = len(df)
        df = df[mask]
        removed = rows_before - len(df)
        if removed > 0:
            logger.debug(f"Removed {removed} rows with all fields missing")

    # Step 3: Filter out groups without proper record type references
    if 'group_id' in df.columns and len(df) > 0:
        groups_before = df['group_id'].nunique()

        # Check for 'record type' substring but exclude exact matches
        record_type_mask = pd.Series(False, index=df.index)

        for col in ['Data Item', 'Data Element']:
            if col in df.columns:
                col_str = df[col].astype(str).str.lower()
                contains_rt = col_str.str.contains('record type', na=False)
                not_exact_rt = col_str.str.strip() != 'record type'
                record_type_mask = record_type_mask | (contains_rt & not_exact_rt)

        total_record_type_rows = record_type_mask.sum()

        if total_record_type_rows > 0:
            # PDF has record type references - filter groups
            valid_groups = df[record_type_mask]['group_id'].unique()
            df = df[df['group_id'].isin(valid_groups)]

            groups_after = df['group_id'].nunique() if len(df) > 0 else 0
            groups_removed = groups_before - groups_after

            if groups_removed > 0:
                logger.debug(f"Removed {groups_removed} groups without record type references")
        else:
            # No record type references - keep all groups (single record type PDF)
            logger.debug(f"No record type references found - keeping all {groups_before} groups")

    # Step 4: Add record types if requested
    has_record_type_references = False
    if 'group_id' in df.columns and len(df) > 0:
        # Check if this PDF has record type references
        record_type_check_mask = pd.Series(False, index=df.index)
        for col in ['Data Item', 'Data Element']:
            if col in df.columns:
                col_str = df[col].astype(str).str.lower()
                contains_rt = col_str.str.contains('record type', na=False)
                not_exact_rt = col_str.str.strip() != 'record type'
                record_type_check_mask = record_type_check_mask | (contains_rt & not_exact_rt)
        has_record_type_references = record_type_check_mask.sum() > 0

    if add_record_types and not df.empty and has_record_type_references:
        # Add record types for multi-record type PDFs
        df = add_record_type_column(df)

        if 'Record_Type' in df.columns:
            record_type_counts = df['Record_Type'].value_counts()
            logger.debug(f"Extracted {len(record_type_counts)} unique record types")
    elif add_record_types and not df.empty and not has_record_type_references:
        logger.debug("Skipping record type extraction for single record type PDF")

    # Step 5: Validate and clean up group_id column
    if 'group_id' in df.columns and not df.empty:
        unique_groups = df['group_id'].nunique()

        if unique_groups == 1:
            # Only one group - group_id is redundant
            df = df.drop('group_id', axis=1)
            logger.debug("Only one group found, dropped group_id column")
        elif 'Record_Type' in df.columns:
            # Multiple groups with record types - validate mapping
            record_type_to_groups = df.groupby('Record_Type')['group_id'].nunique()
            groups_to_record_types = df.groupby('group_id')['Record_Type'].nunique()

            max_groups_per_record_type = record_type_to_groups.max()
            max_record_types_per_group = groups_to_record_types.max()

            if max_groups_per_record_type == 1 and max_record_types_per_group == 1:
                # Perfect 1:1 mapping - drop group_id
                df = df.drop('group_id', axis=1)
                logger.debug("Validated 1:1 record_type↔group_id mapping, dropped group_id")
            else:
                # Keep group_id for complex mappings
                logger.debug(
                    f"Complex mapping detected: max_groups_per_record_type={max_groups_per_record_type}, "
                    f"max_record_types_per_group={max_record_types_per_group}; keeping group_id"
                )
        else:
            # Multiple groups but no record types - keep group_id
            logger.debug(f"Multiple groups ({unique_groups}) but no record types - keeping group_id")

    logger.info(f"Cleaned rows: {len(df)} (removed {initial_row_count - len(df)})")

    return df


def extract_record_type_code(value: str) -> str | None:
    """Extract the record type code from a record type string.

    Handles multiple patterns:
    - "Record Type = XX" format
    - "Record Type X = Description" format
    - "Record Type (X = Description)" format
    - "Record Type (X)" format
    - "Record Type X" format

    Args:
        value: String containing record type information

    Returns:
        Extracted code (e.g., 'H', 'PS', '01') or None if no code found
    """
    if not value or not isinstance(value, str):
        return None

    value = str(value).strip()

    # Skip if this is just the field name "Record Type"
    if value.lower().strip() == 'record type':
        return None

    # Pattern 1: "Record Type = XX" or "Record Type=XX"
    match = re.search(r'record\s*type\s*=\s*([A-Z0-9]+)', value, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    # Pattern 2: "Record Type (X = Description)"
    match = re.search(r'record\s*type\s*\(\s*([A-Z0-9]+)\s*=', value, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    # Pattern 3: "Record Type X = Description"
    match = re.search(r'record\s*type\s+([A-Z0-9]+)\s*=', value, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    # Pattern 4: "Record Type (X)"
    match = re.search(r'record\s*type\s*\(\s*([A-Z0-9]+)\s*\)', value, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    # Pattern 5: "Record Type X"
    match = re.search(r'record\s*type\s+([A-Z0-9]+)', value, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    return None


def add_record_type_column(df: pd.DataFrame) -> pd.DataFrame:
    """Add a 'Record_Type' column by extracting codes from data columns.

    Extracts record type codes from 'Data Item' and 'Data Element' columns,
    then propagates them to entire groups if group_id exists.

    Args:
        df: DataFrame with schema data

    Returns:
        DataFrame with added 'Record_Type' column
    """
    if df.empty:
        return df

    # Create a copy to avoid modifying the original
    df_copy = df.copy()

    # Initialize record_type column
    df_copy['Record_Type'] = None

    # Check both Data Item and Data Element columns
    data_cols = [col for col in df_copy.columns if col in ['Data Item', 'Data Element']]

    for col in data_cols:
        if col in df_copy.columns:
            # Extract record type codes
            df_copy['Record_Type'] = df_copy.apply(
                lambda row: extract_record_type_code(row[col]) if pd.isna(row['Record_Type']) else row['Record_Type'],
                axis=1
            )

    # If we have group_id column, propagate record types to entire groups
    if 'group_id' in df_copy.columns:
        df_copy = propagate_record_types_to_groups(df_copy)

    return df_copy


def propagate_record_types_to_groups(df: pd.DataFrame) -> pd.DataFrame:
    """Propagate record types to all rows within each group_id.

    First validates that each group has at most one unique record type,
    then fills all rows in each group with that record type.

    Args:
        df: DataFrame with group_id and Record_Type columns

    Returns:
        DataFrame with record types propagated to entire groups
    """
    if 'group_id' not in df.columns or 'Record_Type' not in df.columns:
        return df

    df_copy = df.copy()

    # Check each group for record type consistency
    groups_with_conflicts = []

    for group_id in df_copy['group_id'].unique():
        group_data = df_copy[df_copy['group_id'] == group_id]

        # Get non-null record types in this group
        group_record_types = group_data['Record_Type'].dropna().unique()

        # Check for conflicts (more than one record type per group)
        if len(group_record_types) > 1:
            groups_with_conflicts.append({
                'group_id': group_id,
                'record_types': list(group_record_types),
                'rows': len(group_data)
            })
            logger.warning(
                f"Group {group_id} has multiple record types: {list(group_record_types)}"
            )

    # Propagate record types within each group
    for group_id in df_copy['group_id'].unique():
        group_mask = df_copy['group_id'] == group_id
        group_record_types = df_copy.loc[group_mask, 'Record_Type'].dropna().unique()

        if len(group_record_types) > 0:
            # Use the first non-null record type found
            record_type = group_record_types[0]
            df_copy.loc[group_mask, 'Record_Type'] = record_type

    return df_copy


def extract_dates_from_filename(
    file_path: str | Path
) -> list[datetime.datetime | None]:
    """Extract date range from filename (dates in parentheses).

    Handles patterns like:
    - filename_(2020-01-01 - 2021-12-31).pdf -> [datetime(2020,1,1), datetime(2021,12,31)]
    - filename_(2020-01-01 - Present).pdf -> [datetime(2020,1,1), datetime(2099,12,31)]

    Args:
        file_path: Path to file

    Returns:
        List containing [min_date, max_date] or [None, None]
    """
    filename = Path(file_path).name

    # Check if file has parentheses
    if '(' not in filename or ')' not in filename:
        return [None, None]

    # Try to extract date range from filename
    try:
        date_range = filename.split('(')[-1].split(')')[0]

        # Check if the content looks like a date range (should contain a hyphen)
        if '-' not in date_range:
            return [None, None]

        # Convert min and max dates to datetime
        min_date_str, max_date_str = date_range.split('-', 1)  # Split on first hyphen only
        min_date_str = min_date_str.strip()
        max_date_str = max_date_str.strip()

        # Check if we have non-empty strings
        if not min_date_str or not max_date_str:
            return [None, None]

        min_date = pd.to_datetime(min_date_str)

        if max_date_str.lower() == 'present':
            max_date = datetime.datetime(2099, 12, 31)
        else:
            max_date = pd.to_datetime(max_date_str)

        return [min_date, max_date]

    except Exception:
        return [None, None]

