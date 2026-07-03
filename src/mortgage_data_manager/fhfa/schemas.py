"""Schema handling utilities for FHFA data dictionaries and PDFs.

Consolidates four concerns into one module:
    - Year inference and dictionary resolution from raw data filenames
    - PDF extraction from enterprise PUDB zip files
    - PDF dictionary table extraction (years < 2024)
    - Excel dictionary conversion (2024+)
    - Master dictionary building across years
"""

from __future__ import annotations

import re
import shutil
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

import fitz  # PyMuPDF
import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.fhfa.config import SCHEMAS_DIR, ImportOptions, PathsConfig

logger = get_logger(__name__)


# ============================================================================
# Year inference and dictionary resolution
# ============================================================================


def infer_year_from_name(name: str) -> int | None:
    """Infer a plausible 4-digit year from a file or path name.

    Looks for years in [1990, 2035]. If multiple are present, returns the
    largest year (most recent), which aligns with common FHFA naming.
    """
    matches = re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', name)
    years = [int(y) for y in matches]
    years = [y for y in years if 1990 <= y <= 2035]
    if not years:
        return None
    return max(years)


def resolve_dictionary_for_data_file(
    data_file: Path,
    paths_project_dir: Path,
    *,
    dictionary_root: Path | None = None,
    prefer_format: Literal['csv', 'parquet'] = 'csv',
) -> Path | None:
    """Return the matching parsed dictionary path for a given FHFA raw data file.

    Rules inferred from file naming conventions:
    - Prefix indicates enterprise: ``fnma_`` or ``fhlmc_`` (ignored for dictionary selection)
    - Segment ``sf`` vs ``mf`` distinguishes Single Family vs Multifamily
    - Letter following the 4-digit year indicates dataset:
      - For Single Family: ``a``, ``b``, ``c``, ``d``
        - ``c`` aligns with "Single_Family_Census_Tract_File_C"
        - ``d`` aligns with "Single_Family_National_File_C"
        - ``a`` -> "Single_Family_National_File_A"
        - ``b`` -> "Single_Family_National_File_B"
      - For Multifamily: ``b`` (Units/Property-level in PUDB), ``c`` (Census tract)
        - We map both MF ``b`` variants to the available MF National File B dictionaries
          (Property-Level and Unit Class-Level). Preference order is Property-Level.

    The function looks in ``dictionary_files/<year>`` for a file named
    ``<year>_<Family>_<Descriptor>.(csv|parquet)`` and returns the preferred
    format if present, else the other. If nothing matches, returns ``None``.
    """
    path = Path(data_file)
    year = infer_year_from_name(path.name)
    if year is None:
        return None

    dict_root = dictionary_root if dictionary_root is not None else (paths_project_dir / 'dictionary_files')
    year_dir = Path(dict_root) / str(year)
    if not year_dir.exists():
        return None

    name_lower = path.name.lower()
    is_single_family = '_sf' in name_lower or 'sf' in name_lower
    is_multifamily = '_mf' in name_lower or 'mf' in name_lower

    # Extract type letter immediately after year, e.g., sf2013c -> "c"
    match = re.search(r'(sf|mf)(?:19|20)\d{2}([a-d])', name_lower)
    letter = match.group(2) if match else None

    candidates: list[str] = []
    if is_single_family:
        if letter == 'a':
            candidates = [f'{year}_Single_Family_National_File_A']
        elif letter == 'b':
            candidates = [f'{year}_Single_Family_National_File_B']
        elif letter == 'c':
            # Census tract file
            candidates = [f'{year}_Single_Family_Census_Tract_File_C']
        elif letter == 'd':
            # National File C (per user rule)
            candidates = [f'{year}_Single_Family_National_File_C']
        else:
            # Fallback preference order for SF if letter not parsed
            candidates = [
                f'{year}_Single_Family_National_File_C',
                f'{year}_Single_Family_Census_Tract_File_C',
                f'{year}_Single_Family_National_File_B',
                f'{year}_Single_Family_National_File_A',
            ]
    elif is_multifamily:
        if letter == 'c':
            candidates = [f'{year}_Multifamily_Census_Tract_File_C']
        else:
            # MF "b" has two flavors: loans (property-level) and units (unit class-level)
            # Disambiguate using filename tokens
            is_units = 'units' in name_lower
            is_loans = 'loans' in name_lower
            if is_units and not is_loans:
                candidates = [
                    f'{year}_Multifamily_National_File_Unit_Class-Level_Data_File_B',
                    f'{year}_Multifamily_National_File_Property-Level_Data_File_B',
                ]
            else:
                # Default to loans/property-level if ambiguous
                candidates = [
                    f'{year}_Multifamily_National_File_Property-Level_Data_File_B',
                    f'{year}_Multifamily_National_File_Unit_Class-Level_Data_File_B',
                ]
    else:
        # Could not determine family; try SF then MF fallbacks
        candidates = [
            f'{year}_Single_Family_National_File_C',
            f'{year}_Single_Family_Census_Tract_File_C',
            f'{year}_Single_Family_National_File_B',
            f'{year}_Single_Family_National_File_A',
            f'{year}_Multifamily_National_File_Property-Level_Data_File_B',
            f'{year}_Multifamily_National_File_Unit_Class-Level_Data_File_B',
            f'{year}_Multifamily_Census_Tract_File_C',
        ]

    # Try building full paths with preferred format, then the alternate
    def pick_existing(stem: str) -> Path | None:
        first = year_dir / f'{stem}.{prefer_format}'
        if first.exists():
            return first
        alt = year_dir / f'{stem}.{"csv" if prefer_format == "parquet" else "parquet"}'
        if alt.exists():
            return alt
        return None

    for stem in candidates:
        found = pick_existing(stem)
        if found is not None:
            return found

    return None


def extract_dictionary_tables_for_year(
    year: int,
    paths_project_dir: Path,
    overwrite: bool = False,
    *,
    dictionary_root: Path | None = None,
    output_formats: Iterable[Literal['csv', 'parquet']] = ('csv',),
    table_settings: dict | None = None,
) -> None:
    """Extract and combine tables per PDF in ``dictionary_files/fhfa/<year>`` and save one file per PDF.

    Args:
        year: Year subfolder under the dictionaries root.
        paths_project_dir: Project directory for default dictionary root.
        overwrite: Whether to overwrite existing clean dictionary files.
        dictionary_root: Root directory that contains fhfa/year subfolders; defaults to ``<project_dir>/dictionary_files``.
        output_formats: One or both of 'csv' and 'parquet'. Defaults to 'csv'. Combined per PDF.
        table_settings: Optional settings for PyMuPDF's table detection (currently not used).

    Note:
        - Iterates all ``*.pdf`` files under ``dictionary_root/fhfa/<year>`` (non-recursive).
        - Extracts all tables from every page using PyMuPDF's ``page.find_tables()``.
        - Retains only tables containing a case/spacing-insensitive "Field #" column.
        - Combines tables per document, standardizes the column to ``Field #``, coerces to int, sorts by it,
          and drops duplicate field numbers keeping the first occurrence.
        - Writes exactly one combined output per PDF next to the source PDF as:
            ``<pdf_stem>.csv`` and/or ``.parquet``.
        - Prints a warning if there are gaps in the numbering between min and max ``Field #``.
        - Honors ``overwrite``. If both outputs already exist and overwrite=False, skips.
    """
    dict_root = dictionary_root if dictionary_root is not None else (paths_project_dir / 'dictionary_files')
    # Add fhfa subdirectory to match extract_enterprise_pudb_pdfs behavior
    fhfa_dict_root = Path(dict_root) / 'fhfa'
    year_dir = fhfa_dict_root / str(year)
    if not year_dir.exists() or not year_dir.is_dir():
        logger.warning('Dictionary directory does not exist: %s', year_dir)
        return

    formats = set(output_formats)

    pdf_files = [p for p in year_dir.iterdir() if p.is_file() and p.suffix.lower() == '.pdf']
    for pdf_path in pdf_files:
        combined_frames: list[pl.DataFrame] = []
        try:
            with fitz.open(pdf_path) as doc:
                for _page_zero_idx in range(doc.page_count):
                    page_index = _page_zero_idx + 1
                    page = doc.load_page(_page_zero_idx)
                    try:
                        _find_tables = getattr(page, 'find_tables', None)
                        tables_result = _find_tables() if callable(_find_tables) else None
                    except Exception as e:
                        logger.warning('find_tables failed on %s page %s: %s', pdf_path, page_index, e)
                        continue

                    tables_list = getattr(tables_result, 'tables', None) if tables_result is not None else None
                    if not tables_list:
                        continue

                    for table in tables_list:
                        try:
                            pdf_df = table.to_pandas()
                        except Exception as e:
                            logger.warning('to_pandas failed on %s p%s: %s', pdf_path, page_index, e)
                            continue

                        df = pl.from_pandas(pdf_df)

                        # Identify a "Field #" column (case/spacing-insensitive)
                        def normalize_label(label: str) -> str:
                            text = str(label).lower()
                            text = re.sub(r'[\s_]+', '', text)
                            return text

                        field_col: str | None = None
                        for col in df.columns:
                            if normalize_label(col) == 'field#':
                                field_col = str(col)
                                break

                        if field_col is None:
                            continue

                        # Try to locate a field name column for aligned name expansion
                        name_col: str | None = None
                        for col in df.columns:
                            norm_col = normalize_label(col)
                            if norm_col in ('fieldname', 'variablename', 'name'):
                                name_col = str(col)
                                break

                        # Standardize and expand single values, ranges (e.g., "19-23"), and comma-separated lists (e.g., "19, 21-23").
                        work = df.rename({field_col: 'Field #'})
                        # keep original as text and normalize unicode dashes to '-'
                        work = work.with_columns([
                            pl.col('Field #').cast(pl.Utf8, strict=False).alias('field_text')
                        ]).with_columns([
                            pl.col('field_text')
                              .str.replace_all('–', '-')
                              .str.replace_all('—', '-')
                              .alias('field_text')
                        ])
                        # also capture variable name text if available and normalize dashes similarly
                        if name_col is not None:
                            work = work.with_columns([
                                pl.col(name_col).cast(pl.Utf8, strict=False).alias('var_text')
                            ]).with_columns([
                                pl.col('var_text')
                                  .str.replace_all('–', '-')
                                  .str.replace_all('—', '-')
                                  .alias('var_text')
                            ])
                        else:
                            work = work.with_columns([
                                pl.lit(None).alias('var_text')
                            ])
                        # split on commas into tokens
                        work = work.with_columns([
                            pl.col('field_text').str.split(by=',').alias('field_tokens')
                        ]).explode('field_tokens').with_columns([
                            pl.col('field_tokens').map_elements(lambda s: s.strip() if isinstance(s, str) else s).alias('field_token')
                        ])
                        # parse start and optional end from each token
                        work = work.with_columns([
                            pl.col('field_token').str.extract(r'(\d+)', 1).cast(pl.Int64, strict=False).alias('field_start'),
                            pl.col('field_token').str.extract(r'\d+\s*-\s*(\d+)', 1).cast(pl.Int64, strict=False).alias('field_end'),
                        ])
                        # if no end, end = start, then build list range [start..end]
                        work = work.with_columns([
                            pl.coalesce([pl.col('field_end'), pl.col('field_start')]).alias('field_end2'),
                        ]).with_columns([
                            pl.int_ranges(pl.col('field_start'), pl.col('field_end2') + 1).alias('FieldNums')
                        ])
                        # explode and set Field # to each number
                        work = work.explode('FieldNums').with_columns([
                            pl.col('FieldNums').alias('Field #')
                        ])
                        # If a variable name with trailing range exists, compute aligned name numbers
                        if name_col is not None:
                            work = work.with_columns([
                                # extract trailing name range and lengths
                                pl.col('var_text').str.extract(r'(\d+)\s*-\s*(\d+)\s*$', 1).cast(pl.Int64, strict=False).alias('name_start'),
                                pl.col('var_text').str.extract(r'(\d+)\s*-\s*(\d+)\s*$', 2).cast(pl.Int64, strict=False).alias('name_end'),
                            ])
                            work = work.with_columns([
                                (pl.col('FieldNums') - pl.col('field_start')).alias('field_offset'),
                                (pl.col('name_end') - pl.col('name_start')).alias('name_len'),
                            ]).with_columns([
                                pl.when(
                                    pl.col('name_start').is_not_null()
                                    & pl.col('name_end').is_not_null()
                                    & (pl.col('name_len') == (pl.col('field_end2') - pl.col('field_start')))
                                )
                                .then(pl.col('name_start') + pl.col('field_offset'))
                                .otherwise(None)
                                .alias('NameNum')
                            ])
                            # build expanded name when NameNum is available
                            work = work.with_columns([
                                # name_prefix by stripping trailing range
                                pl.col('var_text')
                                  .str.replace(r'\s*\d+\s*-\s*\d+\s*$', '', literal=False)
                                  .map_elements(lambda s: s.strip() if isinstance(s, str) else s)
                                  .alias('name_prefix'),
                            ]).with_columns([
                                pl.when(pl.col('NameNum').is_not_null())
                                  .then(pl.col('name_prefix') + pl.lit(' ') + pl.col('NameNum').cast(pl.Utf8))
                                  .otherwise(pl.col('var_text'))
                                  .alias(name_col)
                            ])
                        # Drop helper cols and rows with empty Field #
                        drop_cols = ['field_text', 'field_tokens', 'field_token', 'field_start', 'field_end', 'field_end2', 'FieldNums']
                        if name_col is not None:
                            drop_cols += ['var_text', 'name_start', 'name_end', 'field_offset', 'name_len', 'name_prefix']
                        work = work.drop(drop_cols)
                        work = work.filter(pl.col('Field #').is_not_null())

                        combined_frames.append(work)
        except Exception as e:
            logger.error('Failed processing %s: %s', pdf_path, e)
            combined_frames = []

        if not combined_frames:
            continue

        combined = pl.concat(combined_frames, how='diagonal', rechunk=True)
        # Drop rows without field number, drop dupes on 'Field #', sort by it
        if 'Field #' in combined.columns:
            combined = combined.filter(pl.col('Field #').is_not_null())
            combined = combined.unique(subset=['Field #'], keep='first')
            combined = combined.sort('Field #')

            # Gap check
            vals_list = [int(x) for x in combined['Field #'].to_list() if x is not None]
            if vals_list:
                min_field = min(vals_list)
                max_field = max(vals_list)
                missing_numbers = sorted(set(range(min_field, max_field + 1)) - set(vals_list))
                if missing_numbers:
                    preview = missing_numbers[:20]
                    ellipsis = '...' if len(missing_numbers) > 20 else ''
                    logger.warning(
                        "Gaps detected in '%s' Field # range %s-%s: missing %s%s",
                        pdf_path.name,
                        min_field,
                        max_field,
                        preview,
                        ellipsis,
                    )

        out_csv = pdf_path.with_name(f"{pdf_path.stem}.csv")

        if not should_process_file(out_csv, overwrite):
            continue

        # Clean string columns: replace newlines with space, collapse spaces, strip
        combined = combined.with_columns([
            pl.col(c).str.replace_all(r'[\r\n]+', ' ').str.replace_all(r'\s+', ' ').str.strip_chars()
            for c, dtype in zip(combined.columns, combined.dtypes)
            if dtype == pl.Utf8
        ])

        if 'csv' in formats:
            combined.write_csv(str(out_csv))


# ============================================================================
# PDF extraction from enterprise PUDB zip files
# ============================================================================


def extract_enterprise_pudb_pdfs(
    *,
    paths: PathsConfig | None = None,
    options: ImportOptions | None = None,
    raw_dir: Path | None = None,
    dictionary_root: Path | None = None,
) -> list[Path]:
    """Extract PDF data dictionaries from enterprise PUDB zip files.

    Scans data/raw for *_enterprise_pudb.zip files and extracts all PDF files
    to dictionary_files/fhfa/{year}/ subdirectories based on the year inferred
    from the zip filename.

    Args:
        paths: Path configuration. Defaults to PathsConfig.from_env() if not provided.
        options: Import options. Defaults to ImportOptions() if not provided.
        raw_dir: Override for raw data directory. Defaults to paths.raw_dir.
        dictionary_root: Override for dictionary root directory. Defaults to paths.project_dir / 'dictionary_files'.

    Returns:
        List of PDF file paths extracted to dictionary_files/fhfa.

    Examples:
        >>> from mortgage_data_manager.fhfa.schemas import extract_enterprise_pudb_pdfs
        >>> extracted = extract_enterprise_pudb_pdfs()
        >>> print(f"Extracted {len(extracted)} PDF files")
    """
    _paths = paths or PathsConfig.from_env()
    _options = options or ImportOptions()

    raw_root = Path(raw_dir) if raw_dir is not None else _paths.raw_dir
    dict_root = Path(dictionary_root) if dictionary_root is not None else (_paths.project_dir / 'dictionary_files')

    # Create fhfa subdirectory under dictionary_files
    fhfa_dict_root = dict_root / 'fhfa'
    fhfa_dict_root.mkdir(parents=True, exist_ok=True)

    # Find all enterprise PUDB zip files
    zip_files = sorted(raw_root.glob('*_enterprise_pudb.zip'))

    if not zip_files:
        logger.warning('No enterprise PUDB zip files found in %s', raw_root)
        return []

    extracted_files: list[Path] = []

    for zip_path in zip_files:
        # Extract year from zip filename (e.g., "2024_enterprise_pudb.zip" -> 2024)
        year = infer_year_from_name(zip_path.name)
        year_str = str(year) if year is not None else 'unknown'

        logger.info('Processing %s (year=%s)', zip_path.name, year_str)

        try:
            with zipfile.ZipFile(zip_path) as zf:
                # Get all PDF files in the zip
                pdf_members = [m for m in zf.namelist() if m.lower().endswith('.pdf') and not m.endswith('/')]

                if not pdf_members:
                    logger.debug('No PDF files found in %s', zip_path.name)
                    continue

                for member in pdf_members:
                    base_name = Path(member).name

                    # Output directory: dictionary_files/fhfa/{year}/
                    out_dir = fhfa_dict_root / year_str
                    out_path = out_dir / base_name

                    if out_path.exists() and not _options.overwrite_raw_dicts:
                        logger.debug('Skipping existing file: %s', out_path)
                        continue

                    # Extract PDF to dictionary directory
                    out_dir.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(out_path, 'wb') as dst:
                        shutil.copyfileobj(src, dst)

                    extracted_files.append(out_path)
                    logger.info('Extracted %s', out_path)

        except zipfile.BadZipFile:
            logger.warning('Bad zip file encountered: %s', zip_path)
        except Exception as e:
            logger.error('Failed processing zip %s: %s', zip_path, e)

    logger.info('Extracted %d PDF files total', len(extracted_files))
    return extracted_files


# ============================================================================
# Excel dictionary conversion (2024+)
# ============================================================================


# Map Excel sheet names to full dictionary names
EXCEL_SHEET_NAME_MAP = {
    'SF NFA': 'Single_Family_National_File_A',
    'SF NFB': 'Single_Family_National_File_B',
    'SF NFC': 'Single_Family_National_File_C',
    'SF CTF': 'Single_Family_Census_Tract_File_C',
    'MF NFP': 'Multifamily_National_File_Property-Level_Data_File_B',
    'MF NFU': 'Multifamily_National_File_Unit_Class-Level_Data_File_B',
    'MF CTF': 'Multifamily_Census_Tract_File_C',
}


def _expand_field_ranges(df: pl.DataFrame) -> pl.DataFrame:
    """Expand field number ranges into individual rows and update field names.

    Handles patterns like:
    - "19" -> 19
    - "19-23" -> 19, 20, 21, 22, 23
    - "19, 21-23" -> 19, 21, 22, 23

    Also updates Field Name to replace range notation with individual numbers.
    For example: "Borrower Race 1-5" becomes "Borrower Race 1", "Borrower Race 2", etc.
    """
    if 'Field #' not in df.columns:
        return df

    # Keep original field text for later replacement in Field Name
    work = df.with_columns([
        pl.col('Field #').cast(pl.Utf8, strict=False).alias('field_text_original')
    ]).with_columns([
        pl.col('field_text_original')
          .str.replace_all('–', '-')
          .str.replace_all('—', '-')
          .alias('field_text')
    ])

    # Split on commas into tokens
    work = work.with_columns([
        pl.col('field_text').str.split(by=',').alias('field_tokens')
    ]).explode('field_tokens').with_columns([
        pl.col('field_tokens').map_elements(
            lambda s: s.strip() if isinstance(s, str) else s,
            return_dtype=pl.Utf8
        ).alias('field_token')
    ])

    # Parse start and optional end from each token
    work = work.with_columns([
        pl.col('field_token').str.extract(r'(\d+)', 1).cast(pl.Int64, strict=False).alias('field_start'),
        pl.col('field_token').str.extract(r'\d+\s*-\s*(\d+)', 1).cast(pl.Int64, strict=False).alias('field_end'),
    ])

    # If no end, end = start, then build list range [start..end]
    work = work.with_columns([
        pl.coalesce([pl.col('field_end'), pl.col('field_start')]).alias('field_end2'),
    ]).with_columns([
        pl.int_ranges(pl.col('field_start'), pl.col('field_end2') + 1).alias('FieldNums')
    ])

    # Explode and set Field # to each number
    work = work.explode('FieldNums').with_columns([
        pl.col('FieldNums').alias('Field #')
    ])

    # Update Field Name to replace range notation with individual number
    # E.g., "Borrower Race 1-5" → "Borrower Race 1", "Borrower Race 2", etc.
    # This matches the logic in extract_dictionary_tables_for_year for PDF extraction
    if 'Field Name' in work.columns:
        # Store original field name in var_text for processing
        work = work.with_columns([
            pl.col('Field Name').cast(pl.Utf8, strict=False).alias('var_text')
        ]).with_columns([
            pl.col('var_text')
              .str.replace_all('–', '-')
              .str.replace_all('—', '-')
              .alias('var_text')
        ])

        # Extract trailing name range (e.g., "1-5" from "Borrower Race 1-5")
        work = work.with_columns([
            pl.col('var_text').str.extract(r'(\d+)\s*-\s*(\d+)\s*$', 1).cast(pl.Int64, strict=False).alias('name_start'),
            pl.col('var_text').str.extract(r'(\d+)\s*-\s*(\d+)\s*$', 2).cast(pl.Int64, strict=False).alias('name_end'),
        ])

        # Calculate offset and check if ranges match
        work = work.with_columns([
            (pl.col('FieldNums') - pl.col('field_start')).alias('field_offset'),
            (pl.col('name_end') - pl.col('name_start')).alias('name_len'),
        ]).with_columns([
            pl.when(
                pl.col('name_start').is_not_null()
                & pl.col('name_end').is_not_null()
                & (pl.col('name_len') == (pl.col('field_end2') - pl.col('field_start')))
            )
            .then(pl.col('name_start') + pl.col('field_offset'))
            .otherwise(None)
            .alias('NameNum')
        ])

        # Build expanded name when NameNum is available
        work = work.with_columns([
            # name_prefix by stripping trailing range
            pl.col('var_text')
              .str.replace(r'\s*\d+\s*-\s*\d+\s*$', '', literal=False)
              .map_elements(lambda s: s.strip() if isinstance(s, str) else s, return_dtype=pl.Utf8)
              .alias('name_prefix'),
        ]).with_columns([
            pl.when(pl.col('NameNum').is_not_null())
              # Case 1: Field Name has range notation (e.g., "Borrower Race 1-5")
              .then(pl.col('name_prefix') + pl.lit(' ') + pl.col('NameNum').cast(pl.Utf8))
              # Case 2: Field # is a range but Field Name has no range notation (e.g., "Borrower Race ")
              # Add sequential numbers starting from 1
              .when(pl.col('field_end2') > pl.col('field_start'))
              .then(pl.col('name_prefix') + pl.lit(' ') + (pl.col('field_offset') + 1).cast(pl.Utf8))
              # Case 3: No range expansion, keep original name
              .otherwise(pl.col('var_text'))
              .alias('Field Name')
        ])

        # Drop helper columns
        drop_cols = ['field_text_original', 'field_text', 'field_tokens', 'field_token',
                     'field_start', 'field_end', 'field_end2', 'FieldNums',
                     'var_text', 'name_start', 'name_end', 'field_offset', 'name_len', 'name_prefix', 'NameNum']
    else:
        drop_cols = ['field_text_original', 'field_text', 'field_tokens', 'field_token',
                     'field_start', 'field_end', 'field_end2', 'FieldNums']

    # Drop helper columns
    work = work.drop(drop_cols)

    # Filter out rows without field number
    work = work.filter(pl.col('Field #').is_not_null())

    return work


def convert_excel_dictionary(
    excel_path: Path,
    year: int,
    *,
    output_dir: Path | None = None,
    overwrite: bool = False,
) -> dict[str, Path]:
    """Convert Excel dictionary to CSV format matching PDF dictionaries.

    Args:
        excel_path: Path to Excel dictionary file
        year: Year of the dictionary
        output_dir: Output directory. Defaults to SCHEMAS_DIR/fhfa/{year}
        overwrite: Whether to overwrite existing files

    Returns:
        Mapping of sheet name to output file path
    """
    if output_dir is None:
        output_dir = SCHEMAS_DIR / 'fhfa' / str(year)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Read all sheets from Excel file
    # Each sheet is a different dataset (SF National A, SF Census Tract C, etc.)
    # Get all sheet names using pandas (polars doesn't expose sheet names directly)
    import pandas as pd
    xls = pd.ExcelFile(excel_path)
    sheet_names = xls.sheet_names

    results = {}
    skip_sheets = {'TOC', 'Cover'}

    for sheet_name in sheet_names:
        if sheet_name in skip_sheets:
            logger.debug("Skipping sheet: %s", sheet_name)
            continue

        logger.info("Processing sheet: %s", sheet_name)

        # Read sheet
        df = pl.read_excel(excel_path, sheet_name=sheet_name)

        # Strip spaces from beginning and end of column names
        df = df.rename(mapping={x: x.strip() for x in df.columns})

        # Standardize column names to match PDF format
        if 'Field #' not in df.columns and 'Field Number' in df.columns:
            df = df.rename({'Field Number': 'Field #'})
        if 'Field Width' not in df.columns and 'Width' in df.columns:
            df = df.rename({'Width': 'Field Width'})

        # Expand field number ranges (e.g., "19-23" -> 19, 20, 21, 22, 23)
        if 'Field #' in df.columns:
            df = _expand_field_ranges(df)

        # Create output filename from sheet name
        # Map abbreviated Excel names to full names
        if sheet_name in EXCEL_SHEET_NAME_MAP:
            full_name = EXCEL_SHEET_NAME_MAP[sheet_name]
            output_stem = f"{year}_{full_name}"
        else:
            # Fallback to cleaned sheet name
            clean_name = sheet_name.strip().replace(' ', '_')
            output_stem = f"{year}_{clean_name}"

        # Save as CSV
        csv_path = output_dir / f"{output_stem}.csv"

        if not should_process_file(csv_path, overwrite):
            logger.info("Skipping existing: %s", csv_path)
        else:
            # Clean string columns: replace newlines with space, collapse spaces, strip
            df = df.with_columns([
                pl.col(c).str.replace_all(r'[\r\n]+', ' ').str.replace_all(r'\s+', ' ').str.strip_chars()
                for c, dtype in zip(df.columns, df.dtypes)
                if dtype == pl.Utf8
            ])

            df.write_csv(csv_path)
            logger.info("Saved: %s", csv_path)

        results[sheet_name] = csv_path

    logger.info("Converted %d sheets from %s", len(results), excel_path.name)
    return results


# ============================================================================
# Master dictionary building (combine year-specific dictionaries)
# ============================================================================


# Dataset to dictionary name pattern mappings
DATASET_PATTERNS = {
    'sf_a': 'Single_Family_National_File_A',
    'sf_b': 'Single_Family_National_File_B',
    'sf_c': 'Single_Family_Census_Tract_File_C',
    'sf_d': 'Single_Family_National_File_C',  # Maps to National File C
    'mf_property_b': 'Multifamily_National_File_Property-Level_Data_File_B',
    'mf_unit_b': 'Multifamily_National_File_Unit_Class-Level_Data_File_B',
    'mf_c': 'Multifamily_Census_Tract_File_C',
}


convert_name_dict = {
    'Single_Family_Census_Tract_File_C': 'sf_ctf',
    'Single_Family_National_File_C': 'sf_nfc',
    'Single_Family_National_File_B': 'sf_nfb',
    'Single_Family_National_File_A': 'sf_nfa',
    'Multifamily_National_File_Property-Level_Data_File_B': 'mf_nfp',
    'Multifamily_National_File_Unit_Class-Level_Data_File_B': 'mf_nfu',
    'Multifamily_Census_Tract_File_C': 'mf_ctf',
}


def _find_dictionary_for_year(
    year: int,
    dataset: str,
    dictionary_root: Path,
) -> Path | None:
    """Find the dictionary file for a specific year and dataset.

    Args:
        year: Year to find dictionary for
        dataset: Dataset identifier (e.g., 'sf_c', 'mf_property_b')
        dictionary_root: Root directory containing dictionary_files/fhfa/{year}/ structure

    Returns:
        Path to dictionary file (CSV), or None if not found
    """
    if dataset not in DATASET_PATTERNS:
        logger.warning("Unknown dataset: %s", dataset)
        return None

    pattern = DATASET_PATTERNS[dataset]
    year_dir = dictionary_root / 'fhfa' / str(year)

    if not year_dir.exists():
        return None

    # Build list of patterns to try
    patterns_to_try = [pattern]

    # Special case: 2011 files don't have letter suffix (e.g., "Census_Tract_File" not "Census_Tract_File_C")
    # Try pattern without final letter suffix for all years (more robust)
    if pattern.endswith(('_A', '_B', '_C', '_D')):
        pattern_without_letter = pattern[:-2]  # Remove "_X"
        patterns_to_try.append(pattern_without_letter)

    # Try all pattern variations (CSV only)
    for pattern_variant in patterns_to_try:
        csv_path = year_dir / f'{year}_{pattern_variant}.csv'
        if csv_path.exists():
            return csv_path

    return None


def build_master_dictionary_for_type(
    dataset: Literal['sf_a', 'sf_b', 'sf_c', 'sf_d', 'mf_property_b', 'mf_unit_b', 'mf_c'],
    min_year: int,
    max_year: int,
    *,
    dictionary_root: Path | None = None,
    output_path: Path | None = None,

    overwrite: bool = False,
) -> pl.DataFrame:
    """Build a master dictionary by concatenating year-specific dictionaries.

    Process:
    1. Scan dictionary_files/fhfa/{year}/ for matching dictionaries
    2. For each year in range:
       - Load the year-specific dictionary for this dataset
       - Add a 'year' column
       - Collect into list
    3. Concatenate all year dictionaries vertically
    4. Sort by year, then field_number
    5. Save to dictionary_files/fhfa/masters/

    Args:
        dataset: Dataset identifier (e.g., 'sf_c' for Single Family Census Tract File C)
        min_year: First year to include (inclusive)
        max_year: Last year to include (inclusive)
        dictionary_root: Root directory containing dictionary_files/. Defaults to SCHEMAS_DIR.
        output_path: Override output path stem. If None, saves to dictionary_files/fhfa/masters/master_{dataset}.csv
        overwrite: Whether to overwrite existing master dictionary. Default False.

    Returns:
        Master dictionary with all years concatenated

    Examples:
        >>> from mortgage_data_manager.fhfa.schemas import build_master_dictionary_for_type
        >>> master = build_master_dictionary_for_type('sf_c', min_year=2010, max_year=2023)
        >>> print(f"Built master with {len(master)} total fields across all years")
    """
    dict_root = dictionary_root if dictionary_root is not None else SCHEMAS_DIR

    # Determine output paths
    if output_path is None:
        masters_dir = dict_root / 'fhfa' / 'masters'
        masters_dir.mkdir(parents=True, exist_ok=True)
        output_stem = masters_dir / f'master_{dataset}'
    else:
        output_stem = output_path.with_suffix('')  # Remove any extension

    # Build path
    output_path = output_stem.with_suffix('.csv')

    # Check if already exists
    if not should_process_file(output_path, overwrite):
        logger.info("Master dictionary already exists: %s (use overwrite=True to rebuild)", output_path)
        return pl.read_csv(output_path)

    logger.info("Building master dictionary for %s (years %d-%d)", dataset, min_year, max_year)

    # Collect year dictionaries
    year_dicts = []
    missing_years = []

    for year in range(min_year, max_year + 1):
        dict_path = _find_dictionary_for_year(year, dataset, dict_root)

        if dict_path is None:
            missing_years.append(year)
            logger.debug("No dictionary found for %s year %d", dataset, year)
            continue

        try:
            # Load dictionary (CSV only)
            year_dict = pl.read_csv(dict_path)

            # Select only required columns and add year
            required_cols = ['Field #', 'Field Width', 'Field Name']
            available_cols = [col for col in required_cols if col in year_dict.columns]
            year_dict = year_dict.select(available_cols)

            # Remove line breaks from Field Name (replace with spaces)
            if 'Field Name' in year_dict.columns:
                year_dict = year_dict.with_columns([
                    pl.col('Field Name').str.replace_all(r'\r?\n', ' ').str.replace_all(r'\s+', ' ').str.strip_chars()
                ])

            year_dict = year_dict.with_columns(pl.lit(year).alias('year'))

            year_dicts.append(year_dict)
            logger.debug("Loaded dictionary for %s year %d: %d fields", dataset, year, len(year_dict))

        except Exception as e:
            logger.error("Failed to load dictionary for %s year %d: %s", dataset, year, e)
            missing_years.append(year)

    if not year_dicts:
        raise ValueError(f"No dictionaries found for {dataset} between {min_year} and {max_year}")

    # Concatenate all years
    master = pl.concat(year_dicts, how='diagonal_relaxed')

    # Sort by year, then field number (if field number exists)
    sort_cols = ['year']
    if 'Field #' in master.columns:
        sort_cols.append('Field #')
    master = master.sort(sort_cols)

    # Log summary
    years_found = len(year_dicts)
    total_fields = len(master)
    logger.info(
        "Built master for %s: %d years, %d total field entries",
        dataset, years_found, total_fields
    )

    if missing_years:
        logger.warning("Missing dictionaries for years: %s", missing_years)

    # Save to disk
    master.write_csv(output_path)
    logger.info("Saved master dictionary (CSV): %s", output_path)

    return master


def build_all_master_dictionaries(
    min_year: int = 2010,
    max_year: int = 2024,
    *,
    dictionary_root: Path | None = None,

    overwrite: bool = False,
) -> dict[str, Path]:
    """Build master dictionaries for all datasets.

    Args:
        min_year: First year to include (inclusive). Default 2010.
        max_year: Last year to include (inclusive). Default 2024.
        dictionary_root: Root directory containing dictionary_files/. Defaults to SCHEMAS_DIR.
        overwrite: Whether to overwrite existing master dictionaries. Default False.

    Returns:
        Dictionary mapping dataset to master dictionary path

    Examples:
        >>> from mortgage_data_manager.fhfa.schemas import build_all_master_dictionaries
        >>> masters = build_all_master_dictionaries(min_year=2010, max_year=2023)
        >>> for dataset, path in masters.items():
        ...     print(f"Built {dataset}: {path}")
    """
    dict_root = dictionary_root if dictionary_root is not None else SCHEMAS_DIR
    masters_dir = dict_root / 'fhfa' / 'masters'
    masters_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 70)
    logger.info("Building master dictionaries for all datasets")
    logger.info("Year range: %d-%d", min_year, max_year)
    logger.info("=" * 70)

    results = {}
    failed = []

    for dataset in DATASET_PATTERNS.keys():
        logger.info("")
        logger.info("Processing %s...", dataset)

        try:
            output_path = masters_dir / f'master_{dataset}'
            build_master_dictionary_for_type(
                dataset,
                min_year=min_year,
                max_year=max_year,
                dictionary_root=dict_root,
                output_path=output_path,

                overwrite=overwrite,
            )
            # Record result
            results[dataset] = output_path.with_suffix('.csv')

        except Exception as e:
            logger.error("Failed to build master for %s: %s", dataset, e)
            failed.append(dataset)

    # Summary
    logger.info("")
    logger.info("=" * 70)
    logger.info("SUMMARY")
    logger.info("=" * 70)
    logger.info("Successfully built: %d datasets", len(results))
    logger.info("Failed: %d datasets", len(failed))
    if failed:
        logger.info("Failed datasets: %s", failed)
    logger.info("Output directory: %s", masters_dir)
    logger.info("=" * 70)

    return results


def load_master_dictionary(
    dataset: str,
    *,
    dictionary_root: Path | None = None,
) -> pl.DataFrame:
    """Load a master dictionary from disk.

    Args:
        dataset: Dataset identifier (e.g., 'sf_c', 'mf_property_b')
        dictionary_root: Root directory containing dictionary_files/. Defaults to SCHEMAS_DIR.

    Returns:
        Master dictionary as Polars DataFrame

    Raises:
        FileNotFoundError: If master dictionary doesn't exist for this dataset

    Examples:
        >>> from mortgage_data_manager.fhfa.schemas import load_master_dictionary
        >>> master = load_master_dictionary('sf_c')
        >>> # Get schema for 2015
        >>> schema_2015 = master.filter(pl.col('year') == 2015)
        >>> print(f"2015 has {len(schema_2015)} fields")
    """
    dict_root = dictionary_root if dictionary_root is not None else SCHEMAS_DIR
    master_path = dict_root / 'fhfa' / 'masters' / f'master_{dataset}.csv'

    if not master_path.exists():
        raise FileNotFoundError(
            f"Master dictionary not found for {dataset}. "
            f"Run build_master_dictionary_for_type() or build_all_master_dictionaries() first. "
            f"Expected path: {master_path}"
        )

    logger.debug("Loading master dictionary: %s", master_path)
    return pl.read_csv(master_path)


def get_available_years(
    dataset: str,
    *,
    dictionary_root: Path | None = None,
) -> list[int]:
    """Get list of years available in a master dictionary.

    Args:
        dataset: Dataset identifier (e.g., 'sf_c', 'mf_property_b')
        dictionary_root: Root directory containing dictionary_files/. Defaults to SCHEMAS_DIR.

    Returns:
        Sorted list of years available in the master dictionary

    Examples:
        >>> from mortgage_data_manager.fhfa.schemas import get_available_years
        >>> years = get_available_years('sf_c')
        >>> print(f"SF Census Tract File C available for years: {years}")
    """
    master = load_master_dictionary(dataset, dictionary_root=dictionary_root)
    years = master['year'].unique().sort().to_list()
    return years
