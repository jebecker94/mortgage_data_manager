#!/usr/bin/env python3
"""GNMA Schema - Combination.

Functions for combining multiple schema versions into unified schemas.

Functions:
    - combine_schemas: Combine cleaned CSV files for a single prefix
    - combine_all_schemas: Batch combine schemas for multiple prefixes
    - read_combined_schemas: Read previously combined schema files
    - reconcile_schema_versions: Reconcile differences between versions

"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)


def combine_schemas(
    prefix: str,
    clean_dir: str | Path,
    output_dir: str | Path | None = None,
    save_combined: bool = True,
    verbose: bool = False
) -> pd.DataFrame:
    """Combine cleaned CSV schema files for a prefix into a single DataFrame.

    Reads all cleaned CSV files from the clean directory for the specified prefix,
    concatenates them, and optionally saves the combined result.

    Note: CSV files should already be cleaned by extract_tables_from_pdf().
    This function simply concatenates the pre-cleaned files.

    Args:
        prefix: File prefix to process (e.g., 'monthly', 'llmon1')
        clean_dir: Directory containing cleaned CSV files
        output_dir: Optional output directory for combined schema (defaults to clean_dir/../../combined)
        save_combined: Whether to save the combined schema to CSV
        verbose: Whether to print detailed progress

    Returns:
        Combined DataFrame with all schema versions, or empty DataFrame if no files found
    """
    clean_dir = Path(clean_dir)

    logger.info(f"Combining clean CSV files for prefix: {prefix}")

    # Get all clean CSV files for this prefix
    csv_directory = clean_dir / prefix

    if not csv_directory.exists():
        logger.warning(f"Clean directory does not exist: {csv_directory}")
        return pd.DataFrame()

    csv_paths = list(csv_directory.glob(f'{prefix}_*.csv'))

    logger.info(f"Found {len(csv_paths)} CSV files")

    if not csv_paths:
        logger.warning(f"No CSV files found for prefix: {prefix}")
        return pd.DataFrame()

    # Read and combine all CSV files
    df_list = []
    for i, csv_path in enumerate(csv_paths, 1):
        if verbose and (i == 1 or i % 20 == 0 or i == len(csv_paths)):
            logger.info(f"Reading [{i}/{len(csv_paths)}]: {csv_path.name}")

        try:
            df = pd.read_csv(csv_path)
            if not df.empty:
                df_list.append(df)
                if verbose:
                    logger.debug(f"Added {len(df)} rows from {csv_path.name}")
            else:
                if verbose:
                    logger.debug(f"File is empty: {csv_path.name}")
        except Exception as e:
            logger.warning(f"Error reading file {csv_path.name}: {e}")

    # Combine all results (CSV files are already cleaned individually)
    if df_list:
        combined_df = pd.concat(df_list, ignore_index=True)

        logger.info(f"Combined dataset: {len(combined_df)} total rows from {len(df_list)} files")

        # Optional debug distributions
        if verbose:
            if 'group_id' in combined_df.columns:
                group_counts = combined_df['group_id'].value_counts().sort_index()
                logger.debug(f"Group distribution: {len(group_counts)} groups")
            if 'Record_Type' in combined_df.columns:
                record_type_counts = combined_df['Record_Type'].value_counts()
                logger.debug(f"Record type distribution: {', '.join([f'{k}:{v}' for k, v in record_type_counts.items()])}")

        # Save combined DataFrame to CSV
        if save_combined:
            if output_dir is None:
                output_dir = clean_dir.parent / 'combined'
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f'{prefix}_combined_schema.csv'
            combined_df.to_csv(output_path, index=False)
            logger.info(f"Saved combined schema to: {output_path}")

        return combined_df

    else:
        logger.info(f"No data found for prefix: {prefix}")
        return pd.DataFrame()


def combine_all_schemas(
    prefixes: list[str],
    clean_dir: str | Path,
    output_dir: str | Path | None = None,
    verbose: bool = False
) -> dict:
    """Combine schemas for all specified prefixes.

    Batch processes multiple prefixes, combining their cleaned CSV files
    into unified schema files.

    Args:
        prefixes: List of prefixes to process
        clean_dir: Directory containing cleaned CSV files
        output_dir: Optional output directory for combined schemas
        verbose: Whether to print detailed progress

    Returns:
        Dictionary mapping prefix names to their row counts
    """
    logger.info(f"Combining schemas for {len(prefixes)} prefixes")

    results = {}

    for prefix in prefixes:
        try:
            combined_df = combine_schemas(
                prefix=prefix,
                clean_dir=clean_dir,
                output_dir=output_dir,
                save_combined=True,
                verbose=verbose
            )
            results[prefix] = len(combined_df)
        except Exception as e:
            logger.error(f"Error combining schemas for {prefix}: {e}")
            results[prefix] = 0

    logger.info(f"Completed combining {len(prefixes)} prefixes")

    return results


def read_combined_schemas(
    prefixes: list[str] | str,
    combined_dir: str | Path
) -> dict:
    """Read combined schema files for specified prefixes.

    Args:
        prefixes: Single prefix or list of prefixes to read
        combined_dir: Directory containing combined schema CSV files

    Returns:
        Dictionary mapping prefix names to DataFrames
    """
    combined_dir = Path(combined_dir)

    # Handle single prefix
    if isinstance(prefixes, str):
        prefixes = [prefixes]

    schemas = {}

    for prefix in prefixes:
        combined_file = combined_dir / f'{prefix}_combined_schema.csv'

        if combined_file.exists():
            try:
                df = pd.read_csv(combined_file)
                schemas[prefix] = df
                logger.info(f"Loaded {prefix}: {len(df)} rows")
            except Exception as e:
                logger.error(f"Error reading {prefix}: {e}")
                schemas[prefix] = pd.DataFrame()
        else:
            logger.warning(f"Combined schema not found for {prefix}: {combined_file}")
            schemas[prefix] = pd.DataFrame()

    return schemas


def reconcile_schema_versions(
    schema_versions: list[pd.DataFrame],
    reconcile_column: str = 'Data Item'
) -> pd.DataFrame:
    """Reconcile differences between multiple schema versions.

    This is a placeholder for future implementation. Currently just
    concatenates all versions and removes duplicates based on the
    reconcile_column.

    Args:
        schema_versions: List of DataFrames representing different versions
        reconcile_column: Column to use for deduplication (default: 'Data Item')

    Returns:
        Reconciled DataFrame with duplicates removed
    """
    if not schema_versions:
        return pd.DataFrame()

    # Simple reconciliation: concatenate and drop duplicates
    combined = pd.concat(schema_versions, ignore_index=True)

    if reconcile_column in combined.columns:
        # Keep the first occurrence of each unique value in reconcile_column
        reconciled = combined.drop_duplicates(subset=[reconcile_column], keep='first')
        logger.info(f"Reconciled {len(combined)} rows to {len(reconciled)} unique entries")
        return reconciled
    else:
        logger.warning(f"Column '{reconcile_column}' not found, returning all rows")
        return combined


def reconcile_schemas(
    schema_list: list[tuple[str, list[dict[str, Any]]]],
    position_key: str = 'begin_end'
) -> dict[tuple[int, int], dict[str, Any]]:
    """Reconcile schemas from different versions by field position.

    Takes multiple schema versions and merges them based on field positions
    (begin/end offsets for fixed-width files). Tracks which versions contain
    each field.

    This is particularly useful for fixed-width data files where field positions
    are critical and may change between schema versions.

    Args:
        schema_list: List of (version_name, schema_data) tuples where schema_data
                     is a list of dicts with 'begin', 'end', and 'field_name' keys
        position_key: How to key the reconciliation ('begin_end' uses (begin, end) tuple)

    Returns:
        Dictionary mapping field positions to reconciled field information.
        Each entry contains the field name and flags for which versions include it.

    Example:
        >>> schemas = [
        ...     ('v1', [
        ...         {'begin': 1, 'end': 6, 'field_name': 'Pool_Number'},
        ...         {'begin': 7, 'end': 14, 'field_name': 'Issue_Date'}
        ...     ]),
        ...     ('v2', [
        ...         {'begin': 1, 'end': 6, 'field_name': 'Pool_Number'},
        ...         {'begin': 7, 'end': 14, 'field_name': 'Issue_Date'},
        ...         {'begin': 15, 'end': 20, 'field_name': 'New_Field'}
        ...     ])
        ... ]
        >>> reconciled = reconcile_schemas(schemas)
        >>> # Returns:
        >>> # {(1, 6): {'field_name': 'Pool_Number', 'version_v1': True, 'version_v2': True},
        >>> #  (7, 14): {'field_name': 'Issue_Date', 'version_v1': True, 'version_v2': True},
        >>> #  (15, 20): {'field_name': 'New_Field', 'version_v2': True}}
    """
    reconciled: dict[tuple[int, int], dict[str, Any]] = defaultdict(dict)

    for version, schema in schema_list:
        for field in schema:
            # Use position as key (begin, end)
            key = (field['begin'], field['end'])

            # Update reconciled entry
            reconciled[key].update({
                "field_name": field['field_name'],
                f"version_{version}": True
            })

    logger.info(f"Reconciled {len(reconciled)} unique field positions from {len(schema_list)} schema versions")

    return dict(reconciled)

