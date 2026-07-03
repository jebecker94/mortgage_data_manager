#!/usr/bin/env python3
"""GNMA Schema - PDF Extraction.

Functions for extracting table data from GNMA PDF schema files.

Functions:
    - extract_tables_from_pdf: Extract all tables from a PDF
    - standardize_columns: Standardize column names
    - merge_continuation_table: Merge tables that span multiple pages

"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pymupdf

from mortgage_data_manager.core.logging import get_logger

from ..config import SchemaReaderConfig

logger = get_logger(__name__)


def extract_tables_from_pdf(
    pdf_path: str | Path,
    prefix: str,
    config: SchemaReaderConfig | None = None,
    save_to_csv: bool = False,
    augment_with_cobol: bool = True,
    cobol_column: str = "Remarks"
) -> list[pd.DataFrame]:
    """Extract all tables from a PDF file using PyMuPDF.

    Extracts tables with strict line detection, handles multi-page tables
    with continuation pages, and optionally saves cleaned results to CSV.

    Args:
        pdf_path: Path to PDF file
        prefix: Data prefix (e.g., 'monthly', 'llmon1')
        config: Optional schema reader configuration
        save_to_csv: Whether to save cleaned data to CSV
        augment_with_cobol: Whether to add COBOL dtype columns
        cobol_column: Column name containing COBOL format info

    Returns:
        List of DataFrames containing extracted tables
    """
    from .cleaning import clean_extracted_dataframe, extract_dates_from_filename
    from .cobol import augment_with_cobol_dtypes

    if config is None:
        config = SchemaReaderConfig()

    pdf_path = Path(pdf_path)
    logger.info(f"Processing PDF: {pdf_path.name}")

    # Early check: if saving and file exists and not overwriting, skip
    if save_to_csv:
        save_file = config.schemas_folder / f'clean/{prefix}' / pdf_path.with_suffix('.csv').name
        if save_file.exists() and not config.overwrite:
            logger.debug(f"Skipping existing CSV: {save_file.name}")
            return []

    # Extract tables using PyMuPDF
    schema: list[pd.DataFrame] = []
    previous_page_has_item = False
    table_columns = None

    with pymupdf.open(pdf_path) as pdf:
        for page_num in range(len(pdf)):
            page = pdf[page_num]

            # Find tables with strict line detection
            table_list = list(page.find_tables(
                vertical_strategy='lines_strict',
                horizontal_strategy='lines_strict'
            ))

            for table in table_list:
                df = table.to_pandas()

                # Standardize column names immediately
                df = standardize_columns(df)

                # Check if this table is a primary schema table. Most GNMA
                # layouts lead with an "Item" column (item number); some
                # (e.g. hsavr) skip that and lead straight with "Data Item".
                lower_cols = [c.lower() for c in df.columns]
                has_item = "item" in lower_cols or "data item" in lower_cols

                if has_item:
                    # Primary table with headers
                    table_columns = df.columns
                    previous_page_has_item = True
                    schema.append(df)
                else:
                    # Possible continuation table
                    if previous_page_has_item and config.merge_continuations:
                        if table_columns is not None and len(df.columns) == len(table_columns):
                            # Same number of columns - likely a continuation
                            df = merge_continuation_table(df, table_columns)
                            schema.append(df)
                        else:
                            # Different structure - reset
                            previous_page_has_item = False
                            table_columns = None

    # Save to CSV if requested
    if save_to_csv and schema:
        # Combine all tables
        combined_df = pd.concat(schema, ignore_index=True)
        combined_df['File'] = pdf_path.name

        # Extract dates from filename
        min_date, max_date = extract_dates_from_filename(pdf_path)
        combined_df['Min_Date'] = min_date
        combined_df['Max_Date'] = max_date

        # Clean the extracted data
        combined_df = clean_extracted_dataframe(
            combined_df,
            filename=pdf_path.name,
            config=config,
            add_record_types=True
        )

        # Augment with COBOL dtypes if requested
        if augment_with_cobol and not combined_df.empty:
            chosen_col = cobol_column if cobol_column in combined_df.columns else (
                "Format" if "Format" in combined_df.columns else None
            )
            if chosen_col is not None:
                combined_df = augment_with_cobol_dtypes(combined_df, cobol_column=chosen_col)

        # Save to CSV
        if not combined_df.empty:
            save_file = config.schemas_folder / f'clean/{prefix}' / pdf_path.with_suffix('.csv').name
            save_file.parent.mkdir(parents=True, exist_ok=True)
            combined_df.to_csv(save_file, index=False)
            logger.info(f"Saved cleaned data to: {save_file.name}")
        else:
            logger.debug("No data remaining after cleaning - skipping save")

    return schema


# Canonical header synonyms. Some GNMA layouts label the same fixed-width
# columns differently — notably the legacy HMBS Pool Disclosure File, whose
# "D" (Pool Detail) record uses Field#/Field Name/Start where every other
# record (and most other prefixes) uses Item/Data Item/Begin. Without this
# mapping the D-record pages fail the "has Item column" primary-table test and
# are silently dropped. Keys are compared case-insensitively after whitespace
# normalization.
_HEADER_SYNONYMS: dict[str, str] = {
    "field#": "Item",
    "field name": "Data Item",
    "start": "Begin",
}


def standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Standardize column names by normalizing whitespace and removing duplicates.

    Whitespace/newlines are collapsed, recognized header synonyms are mapped to
    their canonical names (see ``_HEADER_SYNONYMS``), and duplicate names are
    de-collided with numeric suffixes.

    Args:
        df: DataFrame with potentially messy column names

    Returns:
        DataFrame with standardized column names
    """
    if df.empty:
        return df

    df_copy = df.copy()
    column_mapping: dict[str, str] = {}
    seen: set[str] = set()

    def normalize(name: str) -> str:
        new_col = str(name).replace('\n', ' ').replace('\r', ' ')
        new_col = ' '.join(new_col.split()).strip()
        return _HEADER_SYNONYMS.get(new_col.lower(), new_col)

    for col in df_copy.columns:
        base = normalize(col)
        new_col = base
        suffix = 1
        while new_col in seen:
            new_col = f"{base}_{suffix}"
            suffix += 1
        seen.add(new_col)
        if new_col != col:
            column_mapping[col] = new_col

    if column_mapping:
        df_copy = df_copy.rename(columns=column_mapping)
        logger.debug(f"Standardized {len(column_mapping)} column names")

    return df_copy


def _is_placeholder_header(columns: pd.Index) -> bool:
    """Detect a PyMuPDF placeholder header ('Col0', 'Col1', ...).

    When a continuation page begins with a field's *definition* text wrapping
    over the page break (rather than a new schema row), PyMuPDF cannot detect a
    real header and falls back to positional names 'Col0', 'Col1', ... for the
    structural columns, dumping the wrapped definition fragment into the last
    one. A genuine continuation instead leads with a real Item code (e.g.
    'LP-06') in the first column, so we anchor on that: a leading 'ColN'
    placeholder marks spillover, not a schema row.

    Args:
        columns: Column index of the continuation table as PyMuPDF read it.

    Returns:
        True if the leading column is a positional placeholder (spillover row).
    """
    if len(columns) == 0:
        return True
    return re.fullmatch(r'Col\d+', str(columns[0])) is not None


def merge_continuation_table(
    df: pd.DataFrame,
    header_columns: pd.Index
) -> pd.DataFrame:
    """Merge a continuation table lacking headers with previous header columns.

    Handles tables that span multiple PDF pages where continuation pages lack
    column headers. PyMuPDF promotes the first physical row of such a page to
    be the column names, so for a genuine continuation that "header" is really
    a data row and must be re-inserted as one.

    The exception is a page that starts mid-definition: the previous field's
    definition wraps over the break, PyMuPDF names the structural columns
    'Col0', 'Col1', ..., and that placeholder header carries no schema field.
    There we drop the placeholder row (it would otherwise become a bogus schema
    entry whose value shifts the record-type column and silently breaks silver
    parsing) and keep only the real body rows below it.

    Args:
        df: DataFrame from continuation table
        header_columns: Column names from previous table

    Returns:
        DataFrame with proper column headers
    """
    df2 = df.copy()
    if not _is_placeholder_header(df2.columns):
        # Genuine continuation: the mis-promoted header is a real data row.
        df2.loc[-1] = df2.columns
        df2.index = df2.index + 1
        df2 = df2.sort_index()
    # else: placeholder spillover header - drop it, keep only real body rows.
    df2.columns = header_columns
    return df2


def extract_schemas_to_csv(
    prefixes: list[str] | str,
    prefix_dict: dict,
    config: SchemaReaderConfig | None = None,
    augment_with_cobol: bool = True,
    cobol_column: str = "Remarks"
) -> dict:
    """Extract tables from PDF files and save as individual CSV files.

    For each prefix, finds all PDF files in the raw directory, extracts tables,
    cleans them, and saves to the clean directory.

    Args:
        prefixes: Single prefix or list of prefixes to process
        prefix_dict: Dictionary mapping prefixes to metadata
        config: Optional schema reader configuration
        augment_with_cobol: Whether to add COBOL dtype columns
        cobol_column: Column containing COBOL format info

    Returns:
        Dictionary with processing statistics per prefix
    """
    if config is None:
        config = SchemaReaderConfig()

    # Handle single prefix
    if isinstance(prefixes, str):
        prefixes = [prefixes]

    results = {}

    for prefix in prefixes:
        logger.info(f"Extracting schemas for prefix: {prefix}")

        # Get all PDF files for this prefix
        pdf_directory = config.schemas_folder / f'raw/{prefix}'

        if not pdf_directory.exists():
            logger.warning(f"PDF directory does not exist: {pdf_directory}")
            results[prefix] = {'processed': 0, 'skipped': 0, 'total': 0}
            continue

        pdf_paths = list(pdf_directory.glob(f'{prefix}_*.pdf'))
        logger.info(f"Found {len(pdf_paths)} PDF files")

        processed = 0
        skipped = 0

        # Process each PDF file
        for i, pdf_path in enumerate(pdf_paths, 1):
            if i == 1 or i % 10 == 0 or i == len(pdf_paths):
                logger.info(f"Processing [{i}/{len(pdf_paths)}]: {pdf_path.name}")

            # Extract tables and save to CSV
            tables = extract_tables_from_pdf(
                pdf_path,
                prefix,
                config=config,
                save_to_csv=True,
                augment_with_cobol=augment_with_cobol,
                cobol_column=cobol_column
            )

            # Track if we actually processed or skipped
            if tables:
                processed += 1
            else:
                skipped += 1

        results[prefix] = {
            'processed': processed,
            'skipped': skipped,
            'total': len(pdf_paths)
        }

        logger.info(
            f"Completed {prefix}: {processed} processed, {skipped} skipped, "
            f"{len(pdf_paths)} total"
        )

    return results

