"""FHLB pipeline - High-level orchestration.

This module orchestrates multi-step operations by calling core functions
in the correct sequence. Pattern follows GNMA/FHFA pipelines.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.fhlb.config import FHLBConfig
from mortgage_data_manager.fhlb.download import (
    download_ama_data,
    download_ama_dictionaries,
    download_members_data,
)
from mortgage_data_manager.fhlb.import_bronze import import_ama_bronze
from mortgage_data_manager.fhlb.import_silver import import_ama_silver

logger = get_logger(__name__)


def run_pipeline(
    years: list[int] | None = None,
    skip_download: bool = False,
    skip_bronze: bool = False,
    skip_silver: bool = False,
    overwrite: bool = False,
    raw_dir: Path | None = None,
    bronze_dir: Path | None = None,
    silver_dir: Path | None = None,
    pause: float | None = None,
) -> dict[str, Any]:
    """Run the complete FHLB end-to-end pipeline.

    AMA is FHLB's primary end-to-end flow, so this is the module's main
    pipeline; it covers the AMA download -> bronze -> silver path.

    Steps:
        1. Download AMA data files (optional)
        2. Load data to bronze layer (optional)
        3. Transform data to silver layer (optional)

    Args:
        years: Years to process. Defaults to FHLB_DEFAULT_AMA_MIN_YEAR to FHLB_DEFAULT_AMA_MAX_YEAR
        skip_download: Skip download step (use existing raw files)
        skip_bronze: Skip bronze loading step
        skip_silver: Skip silver transformation step
        overwrite: Overwrite existing files
        raw_dir: Raw data directory. Defaults to FHLBConfig.FHLB_RAW_AMA_DIR
        bronze_dir: Bronze directory. Defaults to FHLBConfig.FHLB_BRONZE_DIR / "ama"
        silver_dir: Silver directory. Defaults to FHLBConfig.FHLB_SILVER_DIR / "ama"
        pause: Seconds to pause between downloads. Defaults to
            MortgageDataConfig.DOWNLOAD_PAUSE

    Returns:
        Dictionary with pipeline results:
        - files_downloaded: Number of files downloaded
        - bronze_loaded: Number of years loaded to bronze
        - silver_transformed: Number of years transformed to silver
        - bronze_skipped: Number of years skipped in bronze
        - silver_skipped: Number of years skipped in silver
    """
    # Set defaults
    if years is None:
        years = list(range(
            FHLBConfig.FHLB_DEFAULT_AMA_MIN_YEAR,
            FHLBConfig.FHLB_DEFAULT_AMA_MAX_YEAR + 1
        ))

    raw = raw_dir or FHLBConfig.FHLB_RAW_AMA_DIR
    bronze = bronze_dir or (FHLBConfig.FHLB_BRONZE_DIR / "ama")
    silver = silver_dir or (FHLBConfig.FHLB_SILVER_DIR / "ama")

    results: dict[str, Any] = {
        'files_downloaded': 0,
        'bronze_loaded': 0,
        'bronze_skipped': 0,
        'silver_transformed': 0,
        'silver_skipped': 0,
    }

    # Step 1: Download
    if not skip_download:
        logger.info("Step 1: Downloading AMA data files")
        try:
            downloaded = download_ama_data(
                output_dir=raw,
                pause=pause,
                overwrite=overwrite
            )
            results['files_downloaded'] = len(downloaded)
            logger.info(f"Downloaded {len(downloaded)} file(s)")
        except Exception as e:
            logger.error(f"Download failed: {e}")

    # Step 2: Load to bronze
    if not skip_bronze:
        logger.info("Step 2: Loading AMA data to bronze layer")
        try:
            bronze_results = import_ama_bronze(
                years=years,
                raw_dir=raw,
                output_dir=bronze,
                overwrite=overwrite
            )
            results['bronze_loaded'] = bronze_results['loaded']
            results['bronze_skipped'] = bronze_results['skipped']
            logger.info(f"Loaded {bronze_results['loaded']} year(s) to bronze")
        except Exception as e:
            logger.error(f"Bronze loading failed: {e}")

    # Step 3: Transform to silver
    if not skip_silver:
        logger.info("Step 3: Transforming AMA data to silver layer")
        try:
            silver_results = import_ama_silver(
                years=years,
                bronze_dir=bronze,
                output_dir=silver,
                overwrite=overwrite
            )
            results['silver_transformed'] = silver_results['loaded']
            results['silver_skipped'] = silver_results['skipped']
            logger.info(f"Transformed {silver_results['loaded']} year(s) to silver")
        except Exception as e:
            logger.error(f"Silver transformation failed: {e}")

    return results


def run_download_all(
    ama_dir: Path | None = None,
    members_dir: Path | None = None,
    dict_dir: Path | None = None,
    pause: float | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Download all FHLB data files and dictionaries.

    Args:
        ama_dir: Directory for AMA data files. Defaults to FHLBConfig.FHLB_RAW_AMA_DIR
        members_dir: Directory for members data files. Defaults to FHLBConfig.FHLB_RAW_MEMBERS_DIR
        dict_dir: Directory for dictionary files. Defaults to FHLBConfig.FHLB_DATA_DIR / "dictionaries"
        pause: Seconds to pause between downloads. Defaults to
            MortgageDataConfig.DOWNLOAD_PAUSE
        overwrite: Overwrite existing files

    Returns:
        Dictionary with workflow results:
        - ama_files: Number of AMA data files downloaded
        - members_files: Number of members data files downloaded
        - dictionary_files: Number of dictionary files downloaded
    """
    results: dict[str, Any] = {
        'ama_files': 0,
        'members_files': 0,
        'dictionary_files': 0,
    }

    # Download AMA data files
    logger.info("Downloading AMA data files")
    try:
        ama_files = download_ama_data(
            output_dir=ama_dir,
            pause=pause,
            overwrite=overwrite
        )
        results['ama_files'] = len(ama_files)
    except Exception as e:
        logger.error(f"AMA data download failed: {e}")

    # Download members data files
    logger.info("Downloading members data files")
    try:
        members_files = download_members_data(
            output_dir=members_dir,
            pause=pause,
            overwrite=overwrite
        )
        results['members_files'] = len(members_files)
    except Exception as e:
        logger.error(f"Members data download failed: {e}")

    # Download dictionary files
    logger.info("Downloading dictionary files")
    try:
        dict_files = download_ama_dictionaries(
            output_dir=dict_dir,
            pause=pause,
            overwrite=overwrite
        )
        results['dictionary_files'] = len(dict_files)
    except Exception as e:
        logger.error(f"Dictionary download failed: {e}")

    return results
