"""Bronze layer import: Convert raw FHA Excel snapshots to Parquet format."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing import get_context
from os import cpu_count
from pathlib import Path
from typing import Literal

import fastexcel
import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file

type PathLike = Path | str
type SnapshotType = Literal['single_family', 'hecm']

logger = get_logger(__name__)

# Column dtype mappings for FHA snapshots
SF_DATA_TYPES: dict[str, str] = {
    'Property State': 'str',
    'Property City': 'str',
    'Property County': 'str',
    'Property Zip': 'Int32',
    'Originating Mortgagee': 'str',
    'Originating Mortgagee Number': 'Int32',
    'Sponsor Name': 'str',
    'Sponsor Number': 'Int32',
    'Down Payment Source': 'str',
    'Non Profit Number': 'Int64',
    'Product Type': 'str',
    'Loan Purpose': 'str',
    'Property Type': 'str',
    'Interest Rate': 'float64',
    'Mortgage Amount': 'Int64',
    'Year': 'Int16',
    'Month': 'Int16',
    'FHA_Index': 'str',
}

HECM_DATA_TYPES: dict[str, str] = {
    'Property State': 'str',
    'Property City': 'str',
    'Property County': 'str',
    'Property Zip': 'Int32',
    'Originating Mortgagee': 'str',
    'Originating Mortgagee Number': 'Int32',
    'Sponsor Name': 'str',
    'Sponsor Number': 'Int32',
    'Sponsor Originator': 'str',
    'NMLS': 'Int64',
    'Standard/Saver': 'str',
    'Purchase/Refinance': 'str',
    'Rate Type': 'str',
    'Interest Rate': 'float64',
    'Initial Principal Limit': 'float64',
    'Maximum Claim Amount': 'float64',
    'Year': 'Int16',
    'Month': 'Int16',
    'HECM Type': 'str',
    'Current Servicer ID': 'Int64',
    'Previous Servicer ID': 'Int64',
}


@dataclass(frozen=True)
class _SnapshotConversionTask:
    """Encapsulate the information needed to convert a monthly snapshot."""

    input_file: Path
    output_file: Path
    year: int
    month: int


def _run_parallel_conversions(
    tasks: list[_SnapshotConversionTask],
    worker: Callable[[_SnapshotConversionTask], None],
) -> None:
    """Execute snapshot conversion tasks, leveraging multiprocessing when useful."""
    if not tasks:
        return

    # ``spawn`` works across platforms and avoids issues when the project is embedded in
    # other applications. Fallback to a sequential loop if only one task needs work.
    process_count = min(len(tasks), max(1, cpu_count() or 1))

    if process_count <= 1:
        for task in tasks:
            worker(task)
        return

    ctx = get_context("spawn")
    with ctx.Pool(processes=process_count) as pool:
        pool.map(worker, tasks)


def clean_sf_sheets(df: pl.DataFrame) -> pl.DataFrame:
    """Clean Excel sheets for FHA single-family data using Polars.

    Args:
        df: Raw single-family data.

    Returns:
        Cleaned single-family data.
    """
    # Initial column name cleaning:
    # 1. Strip whitespace from column names
    # 2. Replace underscores with spaces in column names
    df = df.rename(lambda col: col.strip() if isinstance(col, str) else col)
    df = df.rename(lambda col: col.replace('_', ' ') if isinstance(col, str) else col)

    # Rename Columns to Standardize - only rename columns that exist
    rename_dict: dict[str, str] = {
        'Endorsement Month': 'Month',
        'Original Mortgage Amount': 'Mortgage Amount',
        'Origination Mortgagee/Sponsor Originator': 'Originating Mortgagee',
        'Origination Mortgagee Sponsor Or': 'Originating Mortgagee',
        'Orig Num': 'Originating Mortgagee Number',
        'Property/Product Type': 'Property Type',
        'Property Type Final': 'Property Type',
        'Sponosr Number': 'Sponsor Number',
        'Sponsor Num': 'Sponsor Number',
        'Endorsement  Year': 'Year',
        'Endorsment Year': 'Year',
        'Endorsement Year': 'Year',
    }
    rename_dict_filtered = {old: new for old, new in rename_dict.items() if old in df.columns}
    df = df.rename(rename_dict_filtered)

    # Drop unnamed columns
    unnamed_cols = [col for col in df.columns if 'unnamed' in col.lower()]
    if unnamed_cols:
        df = df.drop(unnamed_cols)

    # Convert numeric columns
    numeric_columns = [
        'Property Zip',
        'Originating Mortgagee Number',
        'Sponsor Number',
        'Non Profit Number',
        'Interest Rate',
        'Mortgage Amount',
        'Year',
        'Month',
    ]

    for column in numeric_columns:
        if column in df.columns:
            df = df.with_columns(
                pl.col(column).cast(pl.Float64, strict=False)
            )

    # Drop bad observations
    if 'Loan Purpose' in df.columns:
        df = df.filter(pl.col('Loan Purpose') != 'Loan_Purpose')

    # Replace bad loan purposes for 2016
    if 'Loan Purpose' in df.columns:
        df = df.with_columns(
            pl.when(pl.col('Loan Purpose').is_in(['Fixed Rate', 'Adjustable Rate']))
            .then(pl.lit('Purchase'))
            .when(pl.col('Loan Purpose').is_in(['Rehabilitation', 'Single Family']))
            .then(pl.lit('Purchase'))
            .otherwise(pl.col('Loan Purpose'))
            .alias('Loan Purpose')
        )

        # Replace '-' with '_' in Loan Purpose
        # Note: Replaces Refi_Conv-Curr with Refi_Conv_Curr
        df = df.with_columns(
            pl.col('Loan Purpose').str.replace('-', '_').alias('Loan Purpose')
        )

    # Standardize down payment types
    if 'Down Payment Source' in df.columns:
        df = df.with_columns(
            pl.when(pl.col('Down Payment Source') == 'NonProfit')
            .then(pl.lit('Non Profit'))
            .when(
                (pl.col('Down Payment Source').cast(pl.Utf8).str.strip_chars() == '') |
                (pl.col('Down Payment Source') == 'nan')
            )
            .then(None)
            .otherwise(pl.col('Down Payment Source'))
            .alias('Down Payment Source')
        )

    # Replace loan purpose types
    if 'Loan Purpose' in df.columns:
        df = df.with_columns(
            pl.col('Loan Purpose').str.replace('-', '_').alias('Loan Purpose')
        )

    # Fix county names and sponsor names
    if 'Property County' in df.columns:
        df = df.with_columns(
            pl.when(pl.col('Property County') == '#NULL!')
            .then(None)
            .otherwise(pl.col('Property County'))
            .alias('Property County')
        )

    if 'Sponsor Name' in df.columns:
        df = df.with_columns(
            pl.when(pl.col('Sponsor Name') == 'Not Available')
            .then(None)
            .otherwise(pl.col('Sponsor Name'))
            .alias('Sponsor Name')
        )

    # Convert to appropriate data types based on schema
    for column, dtype in SF_DATA_TYPES.items():
        if column in df.columns:
            # Map string dtypes to polars types
            if dtype == 'str':
                df = df.with_columns(pl.col(column).cast(pl.Utf8))
            elif dtype == 'Int32':
                df = df.with_columns(pl.col(column).cast(pl.Int32))
            elif dtype == 'Int64':
                df = df.with_columns(pl.col(column).cast(pl.Int64))
            elif dtype == 'Int16':
                df = df.with_columns(pl.col(column).cast(pl.Int16))
            elif dtype == 'float64':
                df = df.with_columns(pl.col(column).cast(pl.Float64))

    return df


def _convert_single_family_snapshot(task: _SnapshotConversionTask) -> None:
    """Worker function for converting a single-family monthly snapshot using Polars."""
    logger.info('Reading and Converting File: %s', task.input_file)

    try:
        reader = fastexcel.read_excel(task.input_file)
        sheets = reader.sheet_names
        sheets = [x for x in sheets if "Data" in x or "Purchase" in x or "Refinance" in x]

        if not sheets:
            logger.warning("No relevant sheets found in %s", task.input_file)
            return

        # Read each sheet, convert to polars, and clean it
        frames = []
        for sheet in sheets:
            try:
                df = reader.load_sheet(sheet).to_polars()
                df = clean_sf_sheets(df)
                frames.append(df)
            except Exception as exc:
                logger.warning("Error reading sheet %s from %s: %s", sheet, task.input_file, exc)
                continue

        if not frames:
            logger.warning("No valid sheets could be read from %s", task.input_file)
            return

        # Concatenate all sheets
        df = pl.concat(frames, how='diagonal_relaxed')

        # Add FHA_Index
        df = df.with_columns(
            (pl.int_range(0, len(df)) + 1).alias('row_num')
        ).with_columns(
            pl.concat_str([
                pl.lit(f'{task.year}{task.month:02d}01_'),
                pl.col('row_num').cast(pl.Utf8).str.zfill(7)
            ]).alias('FHA_Index')
        ).drop('row_num')

        # Save to parquet
        df.write_parquet(task.output_file)

    except Exception as exc:
        logger.error('Error converting file %s: %s', task.input_file, exc)


def convert_fha_sf_snapshots(data_folder: Path, save_folder: Path, overwrite: bool = False) -> None:
    """Convert raw single-family snapshots to cleaned parquet files using Polars.

    Args:
        data_folder: Directory containing the raw Excel monthly SF snapshots.
        save_folder: Directory where cleaned parquet snapshots are saved.
        overwrite: Whether to overwrite output files if a version already exists.
            The default is False.

    Returns:
        None.
    """
    save_folder.mkdir(parents=True, exist_ok=True)

    tasks: list[_SnapshotConversionTask] = []

    # Read data file-by-file
    for year in range(2010, 2099):
        for mon in range(1, 13):
            files = sorted(data_folder.glob(f'fha_sf_snapshot_{year}{mon:02d}01*.xls*'))
            if not files:
                continue

            input_file = files[0]
            output_file = save_folder / f'fha_sf_snapshot_{year}{mon:02d}01.parquet'

            if not should_process_file(output_file, overwrite=overwrite):
                logger.info('File %s already exists!', output_file)
                continue

            tasks.append(
                _SnapshotConversionTask(
                    input_file=input_file,
                    output_file=output_file,
                    year=year,
                    month=mon,
                )
            )

    logger.info(f'Found {len(tasks)} files to process')
    if tasks:
        logger.info(f'First file: {tasks[0].input_file}, output: {tasks[0].output_file}')

    _run_parallel_conversions(tasks, _convert_single_family_snapshot)


def clean_hecm_sheets(df: pl.DataFrame) -> pl.DataFrame:
    """Clean HECM sheets using Polars.

    Args:
        df: Raw HECM data.

    Returns:
        Cleaned HECM data.
    """
    # Initial column name cleaning:
    # 1. Strip whitespace from column names
    # 2. Replace underscores with spaces in column names
    df = df.rename(lambda col: col.strip() if isinstance(col, str) else col)
    df = df.rename(lambda col: col.replace('_', ' ') if isinstance(col, str) else col)

    # Rename columns
    rename_dict: dict[str, str] = {
        'NMLS*': 'NMLS',
        'Sponosr Number': 'Sponsor Number',
        'Standard Saver': 'Standard/Saver',
        'Purchase /Refinance': 'Purchase/Refinance',
        'Purchase Refinance': 'Purchase/Refinance',
        'Previous Servicer': 'Previous Servicer ID',
        'Endorsement Year': 'Year',
        'Endorsement Month': 'Month',
        'Hecm Type': 'HECM Type',
        'Originating Mortgagee/Sponsor Originator': 'Originating Mortgagee',
        'Originating Mortgagee Sponsor Originator': 'Originating Mortgagee',
        'Originating Mortgagee Sponsor Or': 'Originating Mortgagee',
        'Sponsored Originator': 'Sponsor Originator',
    }
    rename_dict_filtered = {old: new for old, new in rename_dict.items() if old in df.columns}
    df = df.rename(rename_dict_filtered)

    # Drop unnamed columns and junk header columns
    cols_to_drop = [col for col in df.columns if
                    'unnamed' in col.lower() or
                    col.startswith('*Colum') or
                    col.startswith('Column I') or
                    'Modified to reflect' in col or
                    col in ['New', 'Modified', 'New 1', 'New 2']]
    if cols_to_drop:
        df = df.drop(cols_to_drop)

    # Replace "Not Available" and null values with None
    for col in df.columns:
        # if string column, replace 'Not Available' and null values with None
        if df.schema[col] in [pl.Utf8, pl.Categorical, pl.String]:
            df = df.with_columns(
                pl.when(pl.col(col).is_in(['Not Available', 'nan', 'None']))
                .then(pl.lit(None))
                .otherwise(pl.col(col))
                .alias(col)
            )

        df = df.with_columns(
            pl.when(pl.col(col).is_null())
            .then(pl.lit(None))
            .otherwise(pl.col(col))
            .alias(col)
        )

    # Convert numeric columns
    numeric_cols = [
        'Property Zip',
        'Originating Mortgagee Number',
        'Sponsor Number',
        'NMLS',
        'Interest Rate',
        'Initial Principal Limit',
        'Maximum Claim Amount',
        'Year',
        'Month',
        'Current Servicer ID',
        'Previous Servicer ID',
    ]

    for col in numeric_cols:
        if col in df.columns:
            df = df.with_columns(
                pl.col(col).cast(pl.Float64, strict=False)
            )

    # Convert to appropriate data types based on schema
    for column, dtype in HECM_DATA_TYPES.items():
        if column in df.columns:
            # Map string dtypes to polars types
            if dtype == 'str':
                df = df.with_columns(pl.col(column).cast(pl.Utf8))
            elif dtype == 'Int32':
                df = df.with_columns(pl.col(column).cast(pl.Int32))
            elif dtype == 'Int64':
                df = df.with_columns(pl.col(column).cast(pl.Int64))
            elif dtype == 'Int16':
                df = df.with_columns(pl.col(column).cast(pl.Int16))
            elif dtype == 'float64':
                df = df.with_columns(pl.col(column).cast(pl.Float64))

    return df


def _convert_hecm_snapshot(task: _SnapshotConversionTask) -> None:
    """Worker function for converting a HECM monthly snapshot using Polars."""
    logger.info('Reading and Converting File: %s', task.input_file)

    try:
        reader = fastexcel.read_excel(task.input_file)
        sheets = reader.sheet_names
        sheets = [x for x in sheets if "Data" in x or "Purchase" in x or "Refinance" in x or "data" in x]

        if not sheets:
            logger.warning("No relevant sheets found in %s", task.input_file)
            return

        # Read each sheet, convert to polars, and clean it
        frames = []
        for sheet in sheets:
            try:
                df = reader.load_sheet(sheet).to_polars()
                df = clean_hecm_sheets(df)
                frames.append(df)
            except Exception as exc:
                logger.warning("Error reading sheet %s from %s: %s", sheet, task.input_file, exc)
                continue

        if not frames:
            logger.warning("No valid sheets could be read from %s", task.input_file)
            return

        # Concatenate all sheets
        df = pl.concat(frames, how="diagonal")

        # Add FHA_Index
        df = df.with_columns(
            (pl.int_range(0, len(df)) + 1).alias('row_num')
        ).with_columns(
            pl.concat_str([
                pl.lit(f'H{task.year}{task.month:02d}01_'),
                pl.col('row_num').cast(pl.Utf8).str.zfill(7)
            ]).alias('FHA_Index')
        ).drop('row_num')

        # Save to parquet
        df.write_parquet(task.output_file)

    except Exception as exc:
        logger.error('Error saving file %s: %s', task.output_file, exc)


def convert_fha_hecm_snapshots(data_folder: Path, save_folder: Path, overwrite: bool = False) -> None:
    """Convert raw HECM snapshots to cleaned parquet files using Polars.

    Args:
        data_folder: Directory containing the raw Excel monthly HECM snapshots.
        save_folder: Directory where cleaned parquet HECM snapshots are saved.
        overwrite: Whether to overwrite output files if a version already exists.
            The default is False.

    Returns:
        None.
    """
    save_folder.mkdir(parents=True, exist_ok=True)

    tasks: list[_SnapshotConversionTask] = []

    # Read data file-by-file
    for year in range(2010, 2099):
        for mon in range(1, 13):
            files = sorted(data_folder.glob(f'fha_hecm_snapshot_{year}{mon:02d}01*.xls*'))
            if not files:
                continue

            input_file = files[0]
            output_file = save_folder / f'fha_hecm_snapshot_{year}{mon:02d}01.parquet'

            if not should_process_file(output_file, overwrite=overwrite):
                logger.info('File %s already exists!', output_file)
                continue

            tasks.append(
                _SnapshotConversionTask(
                    input_file=input_file,
                    output_file=output_file,
                    year=year,
                    month=mon,
                )
            )

    _run_parallel_conversions(tasks, _convert_hecm_snapshot)
