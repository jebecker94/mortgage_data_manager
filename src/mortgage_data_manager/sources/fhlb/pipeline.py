"""FHLB pipeline - High-level orchestration.

This module orchestrates multi-step operations by calling core functions
in the correct sequence. Pattern follows GNMA/FHFA pipelines.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mortgage_data_manager.core.download import DownloadResult
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.sources.fhlb.config import FHLBConfig, resolve_datasets
from mortgage_data_manager.sources.fhlb.download import (
    download_ama_data,
    download_ama_dictionaries,
    download_members_data,
)
from mortgage_data_manager.sources.fhlb.import_bronze import (
    import_ama_bronze,
    import_members_bronze,
)
from mortgage_data_manager.sources.fhlb.import_silver import import_ama_silver

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
        - bronze_failed: Number of years that failed in bronze
        - silver_failed: Number of years that failed in silver
    """
    # Set defaults
    if years is None:
        years = list(
            range(FHLBConfig.FHLB_DEFAULT_AMA_MIN_YEAR, FHLBConfig.FHLB_DEFAULT_AMA_MAX_YEAR + 1)
        )

    raw = raw_dir or FHLBConfig.FHLB_RAW_AMA_DIR
    bronze = bronze_dir or (FHLBConfig.FHLB_BRONZE_DIR / "ama")
    silver = silver_dir or (FHLBConfig.FHLB_SILVER_DIR / "ama")

    results: dict[str, Any] = {
        "files_downloaded": 0,
        "bronze_loaded": 0,
        "bronze_skipped": 0,
        "bronze_failed": 0,
        "silver_transformed": 0,
        "silver_skipped": 0,
        "silver_failed": 0,
    }

    # Step 1: Download
    if not skip_download:
        logger.info("Step 1: Downloading AMA data files")
        try:
            downloaded = download_ama_data(output_dir=raw, pause=pause, overwrite=overwrite)
            results["files_downloaded"] = len(downloaded)
            logger.info("Downloaded %s file(s)", len(downloaded))
        except Exception as e:
            logger.error("Download failed: %s", e)

    # Step 2: Load to bronze
    if not skip_bronze:
        logger.info("Step 2: Loading AMA data to bronze layer")
        bronze_results = import_ama_bronze(
            years=years, raw_dir=raw, output_dir=bronze, overwrite=overwrite
        )
        results["bronze_loaded"] = bronze_results["loaded"]
        results["bronze_skipped"] = bronze_results["skipped"]
        results["bronze_failed"] = bronze_results["failed"]
        logger.info("Loaded %s year(s) to bronze", bronze_results["loaded"])

    # Step 3: Transform to silver
    if not skip_silver:
        logger.info("Step 3: Transforming AMA data to silver layer")
        silver_results = import_ama_silver(
            years=years, bronze_dir=bronze, output_dir=silver, overwrite=overwrite
        )
        results["silver_transformed"] = silver_results["loaded"]
        results["silver_skipped"] = silver_results["skipped"]
        results["silver_failed"] = silver_results["failed"]
        logger.info("Transformed %s year(s) to silver", silver_results["loaded"])

    return results


def run_download(
    datasets: list[str] | None = None,
    *,
    output_dir: Path | None = None,
    pause: float | None = None,
    overwrite: bool = False,
    timeout: int | None = None,
    retries: int | None = None,
    show_progress: bool = True,
) -> dict[str, list[DownloadResult]]:
    """Download step: fetch the requested raw FHLB artifacts.

    Args:
        datasets: Subset of `~.config.DOWNLOAD_DATASETS`. ``None`` = all.
        output_dir: Override the destination for the requested datasets. Only
            valid for a single dataset — each dataset otherwise lands in its own
            configured directory.
        pause: Seconds between downloads (None = config default).
        overwrite: Redownload files that already exist.
        timeout: Per-request timeout (None = config default).
        retries: Retry attempts per file (None = config default).
        show_progress: Render per-file progress.

    Returns:
        Per-dataset download results, keyed by dataset name.

    Raises:
        ValueError: If a dataset is unknown, or ``output_dir`` is given for more
            than one dataset.
    """
    selected = resolve_datasets(datasets, stage="download")
    if output_dir is not None and len(selected) > 1:
        raise ValueError(
            f"output_dir applies to a single dataset, but {len(selected)} were selected "
            f"({', '.join(selected)}); pass --datasets with exactly one."
        )

    downloaders = {
        "ama": (download_ama_data, FHLBConfig.FHLB_RAW_AMA_DIR),
        "members": (download_members_data, FHLBConfig.FHLB_RAW_MEMBERS_DIR),
        "dictionaries": (download_ama_dictionaries, FHLBConfig.FHLB_DATA_DIR / "dictionaries"),
    }
    results: dict[str, list[DownloadResult]] = {}
    for dataset in selected:
        download, default_dir = downloaders[dataset]
        logger.info("FHLB download: %s", dataset)
        results[dataset] = download(
            output_dir=output_dir or default_dir,
            overwrite=overwrite,
            pause=pause,
            timeout=timeout,
            retries=retries,
            show_progress=show_progress,
        )
    return results


def run_bronze(
    datasets: list[str] | None = None,
    *,
    min_year: int | None = None,
    max_year: int | None = None,
    overwrite: bool = False,
) -> dict[str, dict[str, int]]:
    """Bronze step: raw artifacts → parquet.

    Args:
        datasets: Subset of `~.config.BRONZE_DATASETS`. ``None`` = all.
        min_year: Earliest year to import. ``None`` = each dataset's own default.
        max_year: Latest year to import. ``None`` = each dataset's own default.
        overwrite: Rebuild bronze parquet that already exists.

    Returns:
        Per-dataset counts (``loaded``/``skipped``/``failed``), keyed by dataset name.

    Raises:
        ValueError: If a dataset is unknown.
    """
    selected = resolve_datasets(datasets, stage="bronze")
    importers = {"ama": import_ama_bronze, "members": import_members_bronze}
    results: dict[str, dict[str, int]] = {}
    for dataset in selected:
        default_min, default_max = FHLBConfig.default_years(dataset)
        years = list(range(min_year or default_min, (max_year or default_max) + 1))
        logger.info("FHLB bronze: %s (%d-%d)", dataset, years[0], years[-1])
        results[dataset] = importers[dataset](
            years=years,
            raw_dir=FHLBConfig.FHLB_RAW_AMA_DIR
            if dataset == "ama"
            else FHLBConfig.FHLB_RAW_MEMBERS_DIR,
            output_dir=FHLBConfig.FHLB_BRONZE_DIR / dataset,
            overwrite=overwrite,
        )
    return results


def run_silver(
    datasets: list[str] | None = None,
    *,
    min_year: int | None = None,
    max_year: int | None = None,
    overwrite: bool = False,
) -> dict[str, dict[str, int]]:
    """Silver step: bronze parquet → cleaned, analysis-ready parquet.

    Args:
        datasets: Subset of `~.config.SILVER_DATASETS`. ``None`` = all.
        min_year: Earliest year to transform. ``None`` = the dataset's default.
        max_year: Latest year to transform. ``None`` = the dataset's default.
        overwrite: Rebuild silver parquet that already exists.

    Returns:
        Per-dataset counts (``loaded``/``skipped``/``failed``), keyed by dataset name.

    Raises:
        ValueError: If a dataset is unknown.
    """
    selected = resolve_datasets(datasets, stage="silver")
    builders = {"ama": import_ama_silver}
    results: dict[str, dict[str, int]] = {}
    for dataset in selected:
        default_min, default_max = FHLBConfig.default_years(dataset)
        years = list(range(min_year or default_min, (max_year or default_max) + 1))
        logger.info("FHLB silver: %s (%d-%d)", dataset, years[0], years[-1])
        results[dataset] = builders[dataset](
            years=years,
            bronze_dir=FHLBConfig.FHLB_BRONZE_DIR / dataset,
            output_dir=FHLBConfig.FHLB_SILVER_DIR / dataset,
            overwrite=overwrite,
        )
    return results
