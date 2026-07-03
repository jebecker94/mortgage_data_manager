"""HMDA bronze-layer builders (era-split: pre-2007, 2007-2017, post-2018).

Each HMDA era has its own raw format, so bronze construction is split into
three era-specific builders rather than a single function. All three convert
raw downloads to minimally-processed parquet:

    - build_bronze_pre2007: legacy LAR (1981-2006).
    - build_bronze_period_2007_2017: standardized period (2007-2017).
    - build_bronze_post2018: modern format (2018+), with the HMDAIndex appended.

Examples:
    >>> from mortgage_data_manager.hmda.import_bronze import build_bronze_post2018
    >>> build_bronze_post2018("loans", min_year=2020, max_year=2024)
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Literal

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.hmda.config import (
    DERIVED_COLUMNS,
    HMDA_INDEX_COLUMN,
    RAW_DIR,
    HMDAConfig,
)

from .utils import (
    append_hmda_index,
    get_delimiter,
    normalized_file_stem,
    unzip_hmda_file,
)

logger = get_logger(__name__)


def build_bronze_pre2007(
    dataset: Literal["loans", "panel", "transmittal_series"],
    min_year: int = 1990,
    max_year: int = 2006,
    overwrite: bool = False,
) -> None:
    """Create bronze layer parquet files for pre-2007 data.

    Reads raw ZIPs, extracts TXT files, and writes one parquet per year to
    data/bronze/<dataset>/pre2007/. All columns are kept as strings for
    maximum data preservation - type conversions happen in silver layer.

    Args:
        dataset: Dataset type to process
        min_year: First year to process (default: 1990, skips 1981-1989 aggregates)
        max_year: Last year to process (default: 2006)
        overwrite: Whether to replace existing bronze files (default: False)
    """
    import subprocess
    import time

    # Determine raw folder and archives based on dataset
    if dataset == "loans":
        raw_folder = RAW_DIR / "loans"
    elif dataset == "panel":
        raw_folder = RAW_DIR
    elif dataset == "transmittal_series":
        raw_folder = RAW_DIR
    else:
        raise ValueError(f"Unknown dataset: {dataset}")

    bronze_folder = HMDAConfig.get_dataset_dir("bronze", dataset, "pre2007")
    bronze_folder.resolve().mkdir(parents=True, exist_ok=True)

    for year in range(min_year, max_year + 1):
        # Determine archive path based on dataset and year
        if dataset == "loans":
            if 1981 <= year <= 1989:
                archive = raw_folder / "HMDA_LAR_1981_1989.zip"
            else:
                archive = raw_folder / f"HMDA_LAR_{year}.zip"
        elif dataset == "panel":
            archive = raw_folder / "HMDA_PANEL.zip"
        elif dataset == "transmittal_series":
            archive = raw_folder / "HMDA_TS.zip"

        if not archive.exists():
            logger.debug("Archive not found for year %s: %s", year, archive)
            continue

        # Output filename
        save_file = bronze_folder / f"{dataset}_{year}.parquet"
        if not should_process_file(save_file, overwrite):
            logger.debug("Skipping existing bronze file: %s", save_file)
            continue

        logger.info("[bronze pre2007] Processing %s year %s", dataset, year)

        # Get list of files in archive
        result = subprocess.run(
            ["unzip", "-l", str(archive)], capture_output=True, text=True, check=True
        )

        # Find TXT file for this year
        txt_file = None
        for line in result.stdout.split("\n"):
            if ".txt" in line.lower():
                filename = line.split()[-1]
                if "/" not in filename and str(year) in filename:
                    txt_file = filename
                    break

        if not txt_file:
            logger.warning("No TXT file found for year %s in %s", year, archive)
            continue

        logger.info("Extracting: %s", txt_file)

        # Extract using system unzip
        temp_path = raw_folder / txt_file
        subprocess.run(
            ["unzip", "-o", str(archive), txt_file, "-d", str(raw_folder)],
            check=True,
            capture_output=True,
        )

        try:
            # Detect delimiter
            delimiter = get_delimiter(temp_path, bytes=16000)
            logger.info("Detected delimiter: %r", delimiter)

            # Stream data to parquet with ALL COLUMNS AS STRINGS
            logger.info("Streaming data to parquet (all columns as strings)...")
            lf = pl.scan_csv(
                temp_path,
                separator=delimiter,
                ignore_errors=True,
                infer_schema=False,  # Force all columns to String type
                encoding="utf8-lossy",  # Handle invalid UTF-8 sequences
            )
            lf.sink_parquet(save_file)

            schema = pl.scan_parquet(save_file).collect_schema()
            n_rows = pl.scan_parquet(save_file).select(pl.len()).collect().item()
            logger.info(
                "Saved bronze file: %s (%d rows, %d columns)", save_file, n_rows, len(schema)
            )

        finally:
            # Clean up extracted file
            time.sleep(1)
            if temp_path.exists():
                temp_path.unlink()
                logger.debug("Cleaned up: %s", temp_path)


def build_bronze_period_2007_2017(
    dataset: Literal["loans"],
    min_year: int = 2007,
    max_year: int = 2017,
    overwrite: bool = False,
) -> None:
    """Create bronze parquet files for 2007–2017 data.

    - Reads raw ZIPs from data/raw/<dataset>
    - Extracts, detects delimiter, loads all columns as strings
    - Writes one parquet per archive to data/bronze/<dataset>/period_2007_2017

    All columns are stored as strings in bronze to preserve raw values and
    enable inspection/validation before silver layer type conversions.
    """
    raw_folder = RAW_DIR / dataset
    bronze_folder = HMDAConfig.get_dataset_dir("bronze", dataset, "period_2007_2017")
    bronze_folder.resolve().mkdir(parents=True, exist_ok=True)

    for year in range(min_year, max_year + 1):
        archives = sorted(raw_folder.glob(f"*{year}*.zip"))
        if not archives:
            logger.debug("No raw archives found for %s %s", dataset, year)
            continue

        for archive in archives:
            # Pre-2018 years are served by the three-year and nationwide files.
            # The standalone one-year "public_lar" archive (2017 only, a
            # transition-year extra) is HEADERLESS and merely duplicates the
            # nationwide data, so scanning it with a header row corrupts the
            # bronze (a data row becomes the schema). Skip it.
            stem_lower = archive.stem.lower()
            if "public_lar" in stem_lower and "three_year" not in stem_lower:
                logger.debug("Skipping standalone public_lar archive (pre-2018): %s", archive)
                continue

            file_stem = normalized_file_stem(archive.stem)
            save_file = bronze_folder / f"{file_stem}.parquet"
            if not should_process_file(save_file, overwrite):
                logger.debug("Skipping existing bronze file: %s", save_file)
                continue

            logger.info("[bronze 2007-2017] Processing %s", archive)
            raw_file_path = Path(unzip_hmda_file(archive, archive.parent))
            try:
                delimiter = get_delimiter(raw_file_path, bytes=16000)

                # Load all columns as strings (bronze = raw data preservation)
                lf = pl.scan_csv(
                    raw_file_path,
                    separator=delimiter,
                    low_memory=True,
                    infer_schema=False,  # Force all columns to String type
                )

                # Keep bronze minimal: no renames, no derived handling
                lf.sink_parquet(save_file)
            finally:
                time.sleep(1)
                raw_file_path.unlink(missing_ok=True)


def _get_file_type_code(file_name: Path | str) -> str:
    """Derive the HMDA file type code from a file name.

    Args:
        file_name: Name of the HMDA file.

    Returns:
        Single-character code representing the HMDA file type.

    Raises:
        ValueError: If the file type cannot be determined from ``file_name``.
    """
    # Get Base Name of File
    base_name = Path(file_name).stem

    # Get Version Types from Prefixes
    base_name_lower = base_name.lower()
    if "three_year" in base_name_lower:
        return "a"
    elif "one_year" in base_name_lower:
        return "b"
    elif (
        "public_lar" in base_name_lower
        or "public_panel" in base_name_lower
        or "public_ts" in base_name_lower
    ):
        return "c"
    elif "mlar" in base_name_lower:
        return "e"
    raise ValueError("Cannot parse the HMDA file type from the provided file name.")


def build_bronze_post2018(
    dataset: Literal["loans", "panel", "transmittal_series"],
    min_year: int = 2018,
    max_year: int = 2025,
    overwrite: bool = False,
) -> None:
    """Create bronze layer parquet files for post-2018 data.

    Reads raw ZIPs from data/raw/<dataset>, extracts, detects delimiter,
    loads all columns as strings (bronze = minimal processing), adds file_type
    and (for loans) HMDAIndex, drops derived/tract columns, and writes one
    parquet per archive to data/bronze/<dataset>/post2018.

    All columns are stored as strings in bronze to preserve raw values and
    enable inspection/validation before silver layer type conversions.
    """
    raw_folder = RAW_DIR / dataset
    bronze_folder = HMDAConfig.get_dataset_dir("bronze", dataset, "post2018")
    bronze_folder.resolve().mkdir(parents=True, exist_ok=True)

    add_hmda_index = dataset == "loans"

    for year in range(min_year, max_year + 1):
        archives_found = list(raw_folder.glob(f"*{year}*.zip"))
        if not archives_found:
            logger.debug("No archives found for year %s in %s", year, raw_folder)
            continue

        for archive in archives_found:
            file_name = normalized_file_stem(archive.stem)
            save_file = bronze_folder / f"{file_name}.parquet"

            # Check if we should process the raw file
            if not should_process_file(save_file, overwrite):
                logger.debug("Skipping existing bronze file: %s", save_file)
                continue

            logger.info("[bronze] Processing archive: %s", archive)

            # Extract and process the archive
            raw_file_path = Path(unzip_hmda_file(archive, archive.parent))
            try:
                # Detect delimiter
                delimiter = get_delimiter(raw_file_path, bytes=16000)

                # Build lazyframe; add row index only when creating HMDAIndex
                # Load all columns as strings (bronze = raw data preservation)
                index_name = HMDA_INDEX_COLUMN if add_hmda_index else None
                lf = pl.scan_csv(
                    raw_file_path,
                    separator=delimiter,
                    low_memory=True,
                    row_index_name=index_name,
                    infer_schema=False,  # Force all columns to String type
                )

                # Add file_type and HMDAIndex if requested
                file_type_code = _get_file_type_code(archive)
                lf = lf.with_columns(pl.lit(file_type_code).alias("file_type"))
                if add_hmda_index:
                    lf = append_hmda_index(lf, year, file_type_code)

                # Drop derived columns only (no renames or destring here)
                lf = lf.drop(DERIVED_COLUMNS, strict=False)

                # Write bronze file
                lf.sink_parquet(save_file)
                logger.debug("Saved bronze file: %s", save_file)

            finally:
                # Always remove extracted raw CSV to keep raw folder clean
                time.sleep(1)
                raw_file_path.unlink(missing_ok=True)
