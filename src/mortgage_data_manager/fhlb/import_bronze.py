"""FHLB bronze layer data imports.

This module handles importing raw FHLB data files to the bronze layer:
- AMA (Acquired Member Assets) loan-level data
- FHLB member institution data
"""

from __future__ import annotations

import datetime
import glob
from pathlib import Path

import pandas as pd
from dateutil.relativedelta import relativedelta

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.fhlb.utils import (
    FHLB_MEMBERS_COLUMN_MAPPING,
    drop_unnamed_columns,
    ensure_parent_dir,
    standardize_fhlb_columns,
)

logger = get_logger(__name__)


def import_ama_bronze(
    years: list[int],
    raw_dir: Path,
    output_dir: Path,
    overwrite: bool = False
) -> dict[str, int]:
    """Import FHLB AMA (Acquired Member Asset) data to bronze layer.

    AMA files are CSV files with headers, making them much simpler to process
    than the fixed-width FHFA enterprise data files.

    Handles multiple filename patterns:
    - 2009-2014: ama_pudb_export_1231YY.csv
    - 2015-2022: YYYY_pudb_export_1231YY.csv or YYYY_pudb_export.csv
    - 2023-2024: YYYY-pudb.csv

    Args:
        years: Years to load (e.g., [2009, 2010, 2011])
        raw_dir: Directory containing raw AMA CSV files
        output_dir: Output directory for bronze parquet files
        overwrite: Whether to overwrite existing files

    Returns:
        Summary with 'loaded' and 'skipped' counts
    """
    import polars as pl

    loaded = 0
    skipped = 0

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    for year in years:
        # Try different filename patterns
        year_suffix = str(year)[2:]  # Get last 2 digits (e.g., "09" for 2009)

        # Pattern 1: ama_pudb_export_1231YY.csv (2009-2014)
        input_file = raw_dir / f"ama_pudb_export_1231{year_suffix}.csv"

        # Pattern 2: YYYY_pudb_export_1231YY.csv (2015-2022)
        if not input_file.exists():
            input_file = raw_dir / f"{year}_pudb_export_1231{year_suffix}.csv"

        # Pattern 3: YYYY_pudb_export.csv (some years)
        if not input_file.exists():
            input_file = raw_dir / f"{year}_pudb_export.csv"

        # Pattern 4: YYYY-pudb.csv (2023-2024)
        if not input_file.exists():
            input_file = raw_dir / f"{year}-pudb.csv"

        if not input_file.exists():
            logger.warning(f"File not found for year {year} (tried multiple patterns)")
            skipped += 1
            continue

        # Construct output filename
        output_file = output_dir / f"fhlb_ama_{year}.parquet"

        if not should_process_file(output_file, overwrite):
            logger.info(f"Skipping {year} - file exists (use --overwrite to rebuild)")
            skipped += 1
            continue

        logger.info(f"Loading FHLB AMA data for {year} from {input_file.name}")

        try:
            # Read CSV with headers using polars
            # Use larger schema inference and try_parse_dates for better type inference
            df = pl.read_csv(
                input_file,
                infer_schema_length=None,  # Scan entire file for schema
                try_parse_dates=True
            )

            # Standardize column names to 2019+ naming convention
            rename_map = standardize_fhlb_columns(df.columns)
            if rename_map:
                logger.info(f"Renaming {len(rename_map)} columns to standard names")
                df = df.rename(rename_map)

            # Add year column for consistency
            df = df.with_columns(pl.lit(year).alias("data_year"))

            # Write to parquet
            df.write_parquet(output_file)

            logger.info(f"Saved {len(df):,} records to {output_file.name}")
            loaded += 1

        except Exception as e:
            logger.error(f"Failed to process {year}: {e}")
            skipped += 1

    return {"loaded": loaded, "skipped": skipped}


def import_members_bronze(
    years: list[int],
    raw_dir: Path,
    output_dir: Path,
    overwrite: bool = False
) -> dict[str, int]:
    """Import FHLB member data to bronze layer.

    Reads raw Excel files containing FHLB member institution data and
    converts them to individual Parquet files per quarter.

    Handles files matching pattern: FHLB_Members*{year}*.xls*

    Args:
        years: Years to process (e.g., [2009, 2010, 2011])
        raw_dir: Directory containing raw FHLB member Excel files
        output_dir: Output directory for bronze parquet files
        overwrite: Whether to overwrite existing files

    Returns:
        Summary with 'loaded' and 'skipped' counts
    """
    loaded = 0
    skipped = 0

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    # Collect all files for the requested years
    # Handle uppercase, lowercase, and hyphenated filename patterns
    files: list[str] = []
    for year in years:
        year_2digit = str(year)[2:]  # e.g., "14" for 2014
        # Try uppercase pattern with underscore (older files)
        files += glob.glob(str(raw_dir / f"FHLB_Members*{year}*.xls*"))
        # Try lowercase pattern with underscore (newer files from 2024+)
        files += glob.glob(str(raw_dir / f"fhlb_members*{year}*.xls*"))
        # Try hyphenated pattern (some 2018 files use FHLB-Members-MMDDYYYY.xlsx)
        files += glob.glob(str(raw_dir / f"FHLB-Members*{year}*.xls*"))
        files += glob.glob(str(raw_dir / f"fhlb-members*{year}*.xls*"))
        # Try 2-digit year date format (e.g., FHLB_Members_033114.xls for Q1 2014)
        files += glob.glob(str(raw_dir / f"FHLB_Members_*{year_2digit}.xls*"))
        files += glob.glob(str(raw_dir / f"FHLB-Members-*{year_2digit}.xls*"))
        files += glob.glob(str(raw_dir / f"fhlb_members_*{year_2digit}.xls*"))
        files += glob.glob(str(raw_dir / f"fhlb-members-*{year_2digit}.xls*"))
    # Remove duplicates while preserving order
    files = list(dict.fromkeys(files))

    if not files:
        logger.warning(f"No FHLB Members files found in {raw_dir} for years {min(years)}-{max(years)}")
        return {"loaded": 0, "skipped": 0}

    for file in files:
        file_path = Path(file)
        filename = file_path.name

        # Parse year and quarter from filename
        # Supports patterns:
        #   - FHLB_Members_Q12020.xlsx or fhlb_members_q12020.xlsx (quarter format)
        #   - FHLB_Members_12312018.xlsx (MMDDYYYY date format, typically Q4)
        #   - FHLB_Members_033114.xls (MMDDYY date format, typically Q1)
        try:
            # Normalize to uppercase for parsing
            filename_upper = filename.upper()
            parts = filename_upper.split('Q')
            if len(parts) > 1:
                # Quarter format: Q12020, Q42023_Release, etc.
                qy = parts[1]
                if len(qy) >= 5:
                    quarter = int(qy[0])
                    year = int(qy[1:5])
                else:
                    logger.warning(f"Could not parse year/quarter from filename {filename}")
                    skipped += 1
                    continue
            else:
                # Try date format: FHLB_Members_MMDDYYYY.xlsx, FHLB-Members-MMDDYYYY.xlsx, etc.
                import re
                # Match 8-digit date (MMDDYYYY) or 6-digit date (MMDDYY) after _ or -
                date_match = re.search(r'[-_](\d{8}|\d{6})\.', filename_upper)
                if date_match:
                    date_str = date_match.group(1)
                    if len(date_str) == 8:
                        # MMDDYYYY format
                        month = int(date_str[0:2])
                        year = int(date_str[4:8])
                    else:
                        # MMDDYY format
                        month = int(date_str[0:2])
                        year_short = int(date_str[4:6])
                        year = 2000 + year_short if year_short < 50 else 1900 + year_short
                    # Derive quarter from month
                    quarter = (month - 1) // 3 + 1
                else:
                    logger.warning(f"Could not parse year/quarter from filename {filename}")
                    skipped += 1
                    continue
        except Exception as e:
            logger.warning(f"Could not parse year/quarter from filename {filename}: {e}")
            skipped += 1
            continue

        # Construct output filename
        output_file = output_dir / f"fhlb_members_{year}_q{quarter}.parquet"

        if not should_process_file(output_file, overwrite):
            logger.info(f"Skipping {filename} - file exists (use --overwrite to rebuild)")
            skipped += 1
            continue

        logger.info(f"Loading FHLB members from {filename}")

        try:
            # Q1 2020 has bad first line (check case-insensitively)
            if filename_upper == 'FHLB_MEMBERS_Q12020_RELEASE.XLSX':
                df = pd.read_excel(file, skiprows=[0])
            else:
                df = pd.read_excel(file)

            # Standardize column names
            df.columns = [str(x).upper().replace('_', ' ').strip() for x in df.columns]
            df.rename(columns=FHLB_MEMBERS_COLUMN_MAPPING, inplace=True, errors='ignore')

            # Add metadata columns
            df['SOURCE FILE'] = filename
            df['YEAR'] = year
            df['QUARTER'] = quarter

            # Fix date parsing for certain years
            if (year > 2014) or (year == 2014 and quarter > 2):
                if 'APPROVAL DATE' in df.columns:
                    df['APPROVAL DATE'] = pd.to_datetime(df['APPROVAL DATE'], format='%m/%d/%y', errors='coerce')
                    df['APPROVAL DATE'] = [
                        x - relativedelta(years=100) if (pd.notna(x) and x > datetime.datetime.today()) else x
                        for x in df['APPROVAL DATE']
                    ]

                mem_date_series = df.get('MEM DATE')
                if mem_date_series is not None:
                    df['MEM DATE'] = pd.to_datetime(mem_date_series, format='%m/%d/%y', errors='coerce')
                    df['MEM DATE'] = [
                        x - relativedelta(years=100) if (pd.notna(x) and x > datetime.datetime.today()) else x
                        for x in df['MEM DATE']
                    ]

            # Drop unnamed columns
            df = drop_unnamed_columns(df)

            # Write to parquet
            ensure_parent_dir(output_file)
            df.to_parquet(output_file, index=False)

            logger.info(f"Saved {len(df):,} records to {output_file.name}")
            loaded += 1

        except Exception as e:
            logger.error(f"Failed to process {filename}: {e}")
            skipped += 1

    return {"loaded": loaded, "skipped": skipped}
