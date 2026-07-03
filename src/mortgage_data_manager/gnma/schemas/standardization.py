#!/usr/bin/env python3
"""GNMA Schema - Standardization.

Functions for standardizing field names and values.

Functions:
    - standardize_field_names: Standardize all field names in a dataset
    - standardize_single_name: Standardize a single field name
    - count_field_names: Count field name occurrences

"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)


def standardize_single_name(name: str) -> str:
    """Apply standardization rules to a single field name.

    Rules:
    - Remove newlines
    - Handle "Filler" special case
    - Handle "Record Type" special case
    - Remove parenthetical text
    - Replace special characters
    - Replace spaces with underscores
    - Prepend "Field_" if starts with digit

    Args:
        name: Field name to standardize

    Returns:
        Standardized field name
    """
    if not isinstance(name, str):
        return str(name)

    # Step 1: Remove newline characters
    standardized = name.replace('\n', ' ').replace('\r', ' ')

    # Step 2: Handle "Filler" special case
    if 'filler' in standardized.lower():
        return 'Filler'

    # Step 3: Handle "Record Type" special case
    if standardized.startswith('Record Type'):
        return 'Record Type'

    # Step 4: Handle specific parentheses cases before general removal
    standardized = standardized.replace('WAGM (ARM pools only)', 'WAGM_ARM')
    standardized = standardized.replace('WAGM (AR pools only)', 'WAGM_AR')
    standardized = standardized.replace('Issuer Name (Long Name)', 'Issuer_Name_Long')
    standardized = standardized.replace('Issuer Name (Short Name)', 'Issuer_Name_Short')

    # Step 5: Remove text within parentheses
    standardized = re.sub(r'\([^)]*\)', '', standardized)

    # Step 6: Replace special characters
    standardized = standardized.replace('%', 'Percent')
    standardized = standardized.replace('/', '_or_')
    standardized = standardized.replace('=', '_equals_')
    standardized = standardized.replace('-', '_')
    standardized = standardized.replace('–', '_')  # em dash
    standardized = standardized.replace('+', '_Plus')
    standardized = standardized.replace('*', '')
    standardized = standardized.replace(',', '')
    standardized = standardized.replace("'", '')
    standardized = standardized.replace('"', '')
    standardized = standardized.replace('"', '')

    # Step 7: Replace spaces with underscores
    standardized = standardized.replace(' ', '_')

    # Step 8: Handle fields that start with a number
    if standardized and standardized[0].isdigit():
        standardized = f"Field_{standardized}"

    # Clean up multiple underscores and trim
    standardized = re.sub(r'_+', '_', standardized)
    standardized = standardized.strip('_')

    return standardized


def standardize_field_names(
    input_file: str | Path | None = None,
    input_df: pd.DataFrame | None = None,
    output_dir: str | Path | None = None,
    save_results: bool = True
) -> pd.DataFrame:
    """Standardize field names by applying consistent transformation rules.

    Takes either a CSV file path (field_name_counts.csv) or a DataFrame
    with field names and counts. Applies `standardize_single_name()` to
    each field, aggregates counts for standardized names, and tracks changes.

    Args:
        input_file: Path to field_name_counts.csv (from count_field_names_from_schemas)
        input_df: Or, provide DataFrame directly (must have 'Field_Name' and 'Count' columns)
        output_dir: Optional directory to save results
        save_results: Whether to save results to CSV

    Returns:
        DataFrame with columns:
        - Field_Name: Original field name
        - Count: Original count
        - Standardized_Field_Name: Transformed field name
        - Standardized_Count: Aggregated count for standardized name
        - Changed: Boolean flag indicating if name was changed

    Example:
        >>> # Count field names first
        >>> counts = count_field_names_from_schemas('dictionary_files/clean')
        >>> # Then standardize
        >>> standardized = standardize_field_names(input_df=counts)
        >>> print(standardized[standardized['Changed']])
    """
    # Load input data
    if input_df is not None:
        df = input_df.copy()
    elif input_file is not None:
        input_file = Path(input_file)
        df = pd.read_csv(input_file)
    else:
        raise ValueError("Must provide either input_file or input_df")

    # Validate input columns
    if 'Field_Name' not in df.columns or 'Count' not in df.columns:
        raise ValueError("Input DataFrame must have 'Field_Name' and 'Count' columns")

    logger.info(f"Standardizing {len(df)} unique field names")

    # Apply standardization rules
    df['Standardized_Field_Name'] = df['Field_Name'].apply(standardize_single_name)

    # Create aggregated counts for standardized names
    standardized_counts = df.groupby('Standardized_Field_Name')['Count'].sum().reset_index()
    standardized_counts.columns = ['Standardized_Field_Name', 'Standardized_Count']

    # Merge back to original data
    result_df = df.merge(standardized_counts, on='Standardized_Field_Name')

    # Add changed flag
    result_df['Changed'] = result_df['Field_Name'] != result_df['Standardized_Field_Name']

    # Reorder columns
    result_df = result_df[['Field_Name', 'Count', 'Standardized_Field_Name', 'Standardized_Count', 'Changed']]

    # Sort by original count descending
    result_df = result_df.sort_values('Count', ascending=False)

    # Log statistics
    num_changed = result_df['Changed'].sum()
    logger.info(f"Standardized {num_changed} field names ({num_changed/len(result_df)*100:.1f}%)")

    # Save results
    if save_results and output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / 'field_name_counts_w_standardized.csv'
        result_df.to_csv(output_path, index=False)
        logger.info(f"Saved standardized field names to: {output_path}")

    return result_df


def count_field_names_from_schemas(
    clean_dir: str | Path,
    output_dir: str | Path = 'output',
    save_results: bool = True
) -> pd.DataFrame:
    """Extract and count all field names from cleaned schema CSV files.

    Searches recursively through the clean directory for all CSV files,
    extracts field names from 'Data Item' and 'Data Element' columns,
    and counts their occurrences.

    Args:
        clean_dir: Directory containing cleaned schema CSV files
        output_dir: Directory to save results
        save_results: Whether to save results to CSV

    Returns:
        DataFrame with columns: Field_Name, Count (sorted by count descending)

    Example:
        >>> counts = count_field_names_from_schemas('dictionary_files/clean')
        >>> print(counts.head())
           Field_Name                Count
        0  Pool Number                  45
        1  Issue Date                   42
        2  Interest Rate                38
    """
    clean_dir = Path(clean_dir)
    output_dir = Path(output_dir)
    all_field_names = []

    logger.info(f"Searching for CSV files in: {clean_dir}")

    # Find all CSV files in clean directory and subdirectories
    csv_files = list(clean_dir.rglob('*.csv'))

    logger.info(f"Found {len(csv_files)} CSV files")

    for csv_file in csv_files:
        try:
            df = pd.read_csv(csv_file)

            # Extract Data Item values
            if 'Data Item' in df.columns:
                data_items = df['Data Item'].dropna().astype(str)
                all_field_names.extend([item.strip() for item in data_items if item and item != 'nan'])

            # Extract Data Element values
            if 'Data Element' in df.columns:
                data_elements = df['Data Element'].dropna().astype(str)
                all_field_names.extend([element.strip() for element in data_elements if element and element != 'nan'])

        except Exception as e:
            logger.warning(f"Error reading {csv_file}: {e}")
            continue

    # Count occurrences
    field_counts = {}
    for name in all_field_names:
        field_counts[name] = field_counts.get(name, 0) + 1

    # Convert to DataFrame
    results_df = pd.DataFrame([
        {'Field_Name': name, 'Count': count}
        for name, count in field_counts.items()
    ]).sort_values('Count', ascending=False)

    logger.info(f"Found {len(results_df)} unique field names")

    # Save to output directory
    if save_results and not results_df.empty:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / 'field_name_counts.csv'
        results_df.to_csv(output_path, index=False)
        logger.info(f"Saved field name counts to: {output_path}")

    return results_df

