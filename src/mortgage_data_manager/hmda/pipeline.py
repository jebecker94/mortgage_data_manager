"""HMDA Data Manager Pipeline.

High-level orchestration functions for common HMDA data operations.

This module provides convenient wrapper functions that combine download, bronze,
and silver layer operations into complete pipelines. These functions are designed
to be used both programmatically and via the CLI.

The main entry points are ``run_download`` for fetching raw files from CFPB and
the per-era import functions (``import_pre2007``, ``import_2007_2017``,
``import_post2018``), each of which builds the bronze and silver layers for one
HMDA era. ``run_pipeline`` is a thin dispatcher that selects one or more eras,
optionally downloads first, and then runs the corresponding import functions.

Examples:
    >>> from mortgage_data_manager.hmda.pipeline import (
    ...     run_download,
    ...     import_post2018,
    ... )
    >>> # Download data
    >>> run_download(years=range(2018, 2026))
    >>> # Import and process
    >>> results = import_post2018(min_year=2018, max_year=2025)
    >>> print(results)
    {'loans': True, 'panel': True, 'transmittal_series': True}
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.hmda.config import BRONZE_DIR, RAW_DIR, SILVER_DIR

from .download import download_hmda_files
from .import_bronze import (
    build_bronze_period_2007_2017,
    build_bronze_post2018,
    build_bronze_pre2007,
)
from .import_silver import (
    build_silver_period_2007_2017,
    build_silver_post2018,
    build_silver_pre2007,
)

logger = get_logger(__name__)


def run_download(
    years: range,
    destination_folder: Path | str | None = None,
    include_mlar: bool = False,
    include_historical: bool = False,
    **kwargs: Any,
) -> None:
    """Download HMDA data files from CFPB website.

    This is a convenience wrapper around download_hmda_files that provides
    sensible defaults and logging for the download step.

    Args:
        years: Range of years to download (e.g., range(2018, 2025))
        destination_folder: Destination folder for downloads. If None, uses configured RAW_DIR
        include_mlar: Whether to download Modified LAR (MLAR) files
        include_historical: Whether to download historical 2007-2017 files
        **kwargs: Additional keyword arguments to pass to download_hmda_files:
            - pause: Seconds between downloads (default: MortgageDataConfig.DOWNLOAD_PAUSE)
            - wait_time: Seconds to wait for JavaScript to load (default: 10)
            - overwrite_mode: "skip", "always", "if_newer", "if_size_diff" (default: "skip")
            - download_csvs: Whether to download CSV format (default: False)
            - download_pipes: Whether to download pipe-delimited format (default: True)

    Returns:
        None

    Examples:
        >>> # Download post-2018 data
        >>> run_download(years=range(2018, 2025))

        >>> # Download with MLAR files
        >>> run_download(
        ...     years=range(2020, 2025),
        ...     include_mlar=True,
        ... )

        >>> # Download to custom location
        >>> run_download(
        ...     years=range(2018, 2025),
        ...     destination_folder="./my_data",
        ... )

    Note:
        - Files are automatically organized into subdirectories:
          loans/, panel/, transmittal_series/, msamd/, misc/
        - Download progress is logged to INFO level
        - Existing files are skipped by default (use overwrite_mode to change)
    """
    if destination_folder is None:
        destination_folder = RAW_DIR

    dest_path = Path(destination_folder)
    dest_path.resolve().mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info("HMDA Download Pipeline")
    logger.info("=" * 60)
    logger.info(f"Years: {min(years)}-{max(years)}")
    logger.info(f"Destination: {dest_path}")
    logger.info(f"Include MLAR: {include_mlar}")
    logger.info(f"Include Historical: {include_historical}")
    logger.info("")

    try:
        download_hmda_files(
            years=years,
            destination_folder=str(dest_path),
            include_mlar=include_mlar,
            include_historical=include_historical,
            **kwargs,
        )
        logger.info("")
        logger.info("✅ Download completed successfully!")

    except Exception as e:
        logger.error(f"❌ Download failed: {e}")
        raise


def bronze_post2018(
    min_year: int = 2018,
    max_year: int = 2025,
    datasets: list[str] | None = None,
    overwrite: bool = False,
) -> dict[str, bool]:
    """Build the post-2018 bronze layer (raw → parquet).

    Args:
        min_year: Minimum year to process.
        max_year: Maximum year to process.
        datasets: Datasets to process. Defaults to
            ["loans", "panel", "transmittal_series"].
        overwrite: Whether to replace existing files.

    Returns:
        Dict mapping dataset name to success status.
    """
    if datasets is None:
        datasets = ["loans", "panel", "transmittal_series"]

    logger.info("HMDA Post-2018 Bronze Layer")
    logger.info(f"Years: {min_year}-{max_year} | Datasets: {', '.join(datasets)} | Overwrite: {overwrite}")

    results: dict[str, bool] = {}
    for dataset in datasets:
        (BRONZE_DIR / dataset / "post2018").resolve().mkdir(parents=True, exist_ok=True)
        try:
            logger.info(f"Building bronze for {dataset}...")
            build_bronze_post2018(dataset, min_year=min_year, max_year=max_year, overwrite=overwrite)
            results[dataset] = True
            logger.info(f"✅ Bronze {dataset} completed")
        except Exception as e:
            logger.error(f"❌ Bronze {dataset} failed: {e}")
            results[dataset] = False

    return results


def silver_post2018(
    min_year: int = 2018,
    max_year: int = 2025,
    datasets: list[str] | None = None,
    overwrite: bool = False,
) -> dict[str, bool]:
    """Build the post-2018 silver layer (Hive-partitioned).

    Args:
        min_year: Minimum year to process.
        max_year: Maximum year to process.
        datasets: Datasets to process. Defaults to
            ["loans", "panel", "transmittal_series"].
        overwrite: Whether to replace existing files.

    Returns:
        Dict mapping dataset name to success status.
    """
    if datasets is None:
        datasets = ["loans", "panel", "transmittal_series"]

    logger.info("HMDA Post-2018 Silver Layer (Hive-partitioned)")
    logger.info(f"Years: {min_year}-{max_year} | Datasets: {', '.join(datasets)} | Overwrite: {overwrite}")

    results: dict[str, bool] = {}
    for dataset in datasets:
        (SILVER_DIR / dataset / "post2018").resolve().mkdir(parents=True, exist_ok=True)
        try:
            logger.info(f"Building silver for {dataset}...")
            build_silver_post2018(
                dataset,
                min_year=min_year,
                max_year=max_year,
                overwrite=overwrite,
            )
            results[dataset] = True
            logger.info(f"✅ Silver {dataset} completed")
        except Exception as e:
            logger.error(f"❌ Silver {dataset} failed: {e}")
            results[dataset] = False

    return results


def import_post2018(
    min_year: int = 2018,
    max_year: int = 2025,
    datasets: list[str] | None = None,
    overwrite: bool = False,
) -> dict[str, bool]:
    """Complete post-2018 HMDA import pipeline (bronze + silver layers).

    Runs ``bronze_post2018`` followed by ``silver_post2018``,
    skipping silver for any dataset whose bronze build failed.

    Args:
        min_year: Minimum year to process.
        max_year: Maximum year to process.
        datasets: Datasets to process. Defaults to all three.
        overwrite: Whether to replace existing files.

    Returns:
        Dict mapping dataset name to overall (bronze + silver) success status.

    Notes:
        - Bronze layer: data/bronze/{dataset}/post2018/
        - Silver layer: data/silver/{dataset}/post2018/activity_year=YYYY/file_type=X/
        - HMDAIndex unique identifier is automatically created
    """
    if datasets is None:
        datasets = ["loans", "panel", "transmittal_series"]

    logger.info("=" * 60)
    logger.info("HMDA Post-2018 Import Pipeline")
    logger.info("=" * 60)

    bronze_results = bronze_post2018(
        min_year=min_year,
        max_year=max_year,
        datasets=datasets,
        overwrite=overwrite,
    )

    silver_datasets = [d for d, ok in bronze_results.items() if ok]
    silver_results = silver_post2018(
        min_year=min_year,
        max_year=max_year,
        datasets=silver_datasets,
        overwrite=overwrite,
    )

    results: dict[str, bool] = {}
    for dataset in datasets:
        results[dataset] = bronze_results.get(dataset, False) and silver_results.get(dataset, False)

    logger.info("Pipeline Summary")
    for dataset, success in results.items():
        status = "✅" if success else "❌"
        logger.info(f"{status} {dataset}: {'Success' if success else 'Failed'}")

    return results


def bronze_2007_2017(
    min_year: int = 2007,
    max_year: int = 2017,
    overwrite: bool = False,
) -> bool:
    """Build the 2007-2017 bronze layer (loans only).

    Args:
        min_year: Minimum year to process.
        max_year: Maximum year to process.
        overwrite: Whether to replace existing files.

    Returns:
        True if bronze build succeeded, False otherwise.
    """
    dataset = "loans"
    logger.info("HMDA 2007-2017 Bronze Layer")
    logger.info(f"Years: {min_year}-{max_year} | Dataset: {dataset} | Overwrite: {overwrite}")

    (BRONZE_DIR / dataset / "period_2007_2017").resolve().mkdir(parents=True, exist_ok=True)

    try:
        build_bronze_period_2007_2017(dataset, min_year=min_year, max_year=max_year, overwrite=overwrite)
        logger.info(f"✅ Bronze {dataset} completed")
        return True
    except Exception as e:
        logger.error(f"❌ Bronze {dataset} failed: {e}")
        return False


def silver_2007_2017(
    min_year: int = 2007,
    max_year: int = 2017,
    overwrite: bool = False,
) -> bool:
    """Build the 2007-2017 silver layer (loans only).

    Args:
        min_year: Minimum year to process.
        max_year: Maximum year to process.
        overwrite: Whether to replace existing files.

    Returns:
        True if silver build succeeded, False otherwise.
    """
    dataset = "loans"
    logger.info("HMDA 2007-2017 Silver Layer (Hive-partitioned)")
    logger.info(f"Years: {min_year}-{max_year} | Dataset: {dataset} | Overwrite: {overwrite}")

    (SILVER_DIR / dataset / "period_2007_2017").resolve().mkdir(parents=True, exist_ok=True)

    try:
        build_silver_period_2007_2017(
            dataset,
            min_year=min_year,
            max_year=max_year,
            overwrite=overwrite,
        )
        logger.info(f"✅ Silver {dataset} completed")
        return True
    except Exception as e:
        logger.error(f"❌ Silver {dataset} failed: {e}")
        return False


def import_2007_2017(
    min_year: int = 2007,
    max_year: int = 2017,
    overwrite: bool = False,
) -> bool:
    """Complete 2007-2017 HMDA import pipeline (bronze + silver layers).

    Runs ``bronze_2007_2017`` followed by ``silver_2007_2017``,
    skipping silver if bronze fails. Only the loans dataset is available for
    this period.

    Args:
        min_year: Minimum year to process.
        max_year: Maximum year to process.
        overwrite: Whether to replace existing files.

    Returns:
        True if both bronze and silver completed successfully.
    """
    logger.info("=" * 60)
    logger.info("HMDA 2007-2017 Import Pipeline")
    logger.info("=" * 60)

    if not bronze_2007_2017(min_year=min_year, max_year=max_year, overwrite=overwrite):
        return False
    if not silver_2007_2017(min_year=min_year, max_year=max_year, overwrite=overwrite):
        return False

    logger.info("✅ Pipeline completed successfully!")
    return True


def bronze_pre2007(
    min_year: int = 1990,
    max_year: int = 2006,
    datasets: list[str] | None = None,
    overwrite: bool = False,
) -> dict[str, bool]:
    """Build the pre-2007 bronze layer (raw → parquet).

    Args:
        min_year: Minimum year (must be >= 1990).
        max_year: Maximum year (must be <= 2006).
        datasets: Datasets to process. Defaults to all three.
        overwrite: Whether to replace existing files.

    Returns:
        Dict mapping dataset name to success status.
    """
    if datasets is None:
        datasets = ["loans", "panel", "transmittal_series"]

    logger.info("HMDA Pre-2007 Bronze Layer")
    logger.info(f"Years: {min_year}-{max_year} | Datasets: {', '.join(datasets)} | Overwrite: {overwrite}")

    results: dict[str, bool] = {}
    for dataset in datasets:
        (BRONZE_DIR / dataset / "pre2007").resolve().mkdir(parents=True, exist_ok=True)
        try:
            build_bronze_pre2007(dataset, min_year=min_year, max_year=max_year, overwrite=overwrite)
            results[dataset] = True
            logger.info(f"✅ Bronze {dataset} completed")
        except Exception as e:
            logger.error(f"❌ Bronze {dataset} failed: {e}")
            results[dataset] = False

    return results


def silver_pre2007(
    min_year: int = 1990,
    max_year: int = 2006,
    datasets: list[str] | None = None,
    overwrite: bool = False,
) -> dict[str, bool]:
    """Build the pre-2007 silver layer (Hive-partitioned).

    Args:
        min_year: Minimum year (must be >= 1990).
        max_year: Maximum year (must be <= 2006).
        datasets: Datasets to process. Defaults to all three.
        overwrite: Whether to replace existing files.

    Returns:
        Dict mapping dataset name to success status.
    """
    if datasets is None:
        datasets = ["loans", "panel", "transmittal_series"]

    logger.info("HMDA Pre-2007 Silver Layer (Hive-partitioned)")
    logger.info(f"Years: {min_year}-{max_year} | Datasets: {', '.join(datasets)} | Overwrite: {overwrite}")

    results: dict[str, bool] = {}
    for dataset in datasets:
        (SILVER_DIR / dataset / "pre2007").resolve().mkdir(parents=True, exist_ok=True)
        try:
            build_silver_pre2007(
                dataset,
                min_year=min_year,
                max_year=max_year,
                overwrite=overwrite,
            )
            results[dataset] = True
            logger.info(f"✅ Silver {dataset} completed")
        except Exception as e:
            logger.error(f"❌ Silver {dataset} failed: {e}")
            results[dataset] = False

    return results


def import_pre2007(
    min_year: int = 1990,
    max_year: int = 2006,
    datasets: list[str] | None = None,
    overwrite: bool = False,
) -> dict[str, bool]:
    """Complete pre-2007 HMDA import pipeline (bronze + silver layers).

    Runs ``bronze_pre2007`` followed by ``silver_pre2007``,
    skipping silver for any dataset whose bronze build failed.

    Args:
        min_year: Minimum year (must be >= 1990).
        max_year: Maximum year (must be <= 2006).
        datasets: Datasets to process. Defaults to all three.
        overwrite: Whether to replace existing files.

    Returns:
        Dict mapping dataset name to overall success status.

    Notes:
        - Data source: openICPSR Project 151921
        - 1981-1989 excluded due to different schema
        - Schema changes: 1990-2003 (23 columns), 2004-2006 (38 columns)
    """
    if datasets is None:
        datasets = ["loans", "panel", "transmittal_series"]

    logger.info("=" * 60)
    logger.info("HMDA Pre-2007 Import Pipeline")
    logger.info("=" * 60)

    bronze_results = bronze_pre2007(
        min_year=min_year,
        max_year=max_year,
        datasets=datasets,
        overwrite=overwrite,
    )

    silver_datasets = [d for d, ok in bronze_results.items() if ok]
    silver_results = silver_pre2007(
        min_year=min_year,
        max_year=max_year,
        datasets=silver_datasets,
        overwrite=overwrite,
    )

    results: dict[str, bool] = {}
    for dataset in datasets:
        results[dataset] = bronze_results.get(dataset, False) and silver_results.get(dataset, False)

    logger.info("Pipeline Summary")
    for dataset, success in results.items():
        status = "✅" if success else "❌"
        logger.info(f"{status} {dataset}: {'Success' if success else 'Failed'}")

    return results


ERA_PIPELINES = {
    "pre2007": import_pre2007,
    "2007_2017": import_2007_2017,
    "post2018": import_post2018,
}


def run_pipeline(
    eras: list[str] | None = None,
    download: bool = False,
    overwrite: bool = False,
) -> dict[str, bool]:
    """Run the HMDA import pipeline for one or more eras.

    Thin dispatcher over the per-era import functions in ``ERA_PIPELINES``.
    Optionally downloads the raw source files for each era before importing,
    then runs each era's import (bronze + silver) and normalizes the result to
    a single success boolean per era.

    Args:
        eras: Eras to process. Each must be a key of ``ERA_PIPELINES``
            ("pre2007", "2007_2017", "post2018"). Defaults to all three.
        download: If True, download raw files before importing each era.
            Note: pre-2007 source lives on openICPSR and cannot be fetched
            automatically — a warning is logged and the import runs against
            whatever is already present.
        overwrite: Whether to reprocess (overwrite) existing outputs. Each
            era's import keeps its own year-range defaults.

    Returns:
        Dict mapping each requested era to its overall success status.

    Raises:
        ValueError: If any requested era is not a valid ``ERA_PIPELINES`` key.

    Examples:
        >>> run_pipeline(eras=["post2018"], download=True)
        {'post2018': True}
    """
    if eras is None:
        eras = list(ERA_PIPELINES)

    invalid = [era for era in eras if era not in ERA_PIPELINES]
    if invalid:
        valid = ", ".join(ERA_PIPELINES)
        raise ValueError(
            f"Invalid era(s): {', '.join(invalid)}. Valid eras are: {valid}"
        )

    results: dict[str, bool] = {}
    for era in eras:
        if download:
            if era == "post2018":
                run_download(years=range(2018, 2026))
            elif era == "2007_2017":
                # include_historical fetches the full 2007-2017 archive from a
                # single URL regardless of `years`; pass a one-year range only
                # because run_download's logging needs a non-empty range.
                run_download(years=range(2018, 2019), include_historical=True)
            else:  # pre2007
                logger.warning(
                    "pre-2007 source is on openICPSR and must be fetched "
                    "manually; skipping download for this era."
                )

        result = ERA_PIPELINES[era](overwrite=overwrite)
        results[era] = result if isinstance(result, bool) else all(result.values())

    return results


__all__ = [
    "run_download",
    # Bronze-only helpers
    "bronze_post2018",
    "bronze_2007_2017",
    "bronze_pre2007",
    # Silver-only helpers
    "silver_post2018",
    "silver_2007_2017",
    "silver_pre2007",
    # Combined bronze + silver (per-era import)
    "import_post2018",
    "import_2007_2017",
    "import_pre2007",
    # Multi-era dispatcher
    "run_pipeline",
]
