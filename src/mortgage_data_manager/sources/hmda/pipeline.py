"""HMDA pipeline - high-level orchestration.

High-level orchestration functions for common HMDA data operations.

This module provides convenient wrapper functions that combine download, bronze,
and silver layer operations into complete pipelines. These functions are designed
to be used both programmatically and via the CLI.

The main entry points are ``run_download`` for fetching raw files from CFPB and
the per-era import functions (``import_pre2007``, ``import_2007_2017``,
``import_post2018``), each of which builds the bronze and silver layers for one
HMDA era. ``run_pipeline`` is a thin dispatcher that selects one or more eras,
optionally downloads first, and then runs the corresponding import functions.

Example:
    >>> from mortgage_data_manager.sources.hmda.pipeline import (
    ...     run_download,
    ...     import_post2018,
    ... )
    >>> # Download data
    >>> run_download(["snapshot"], min_year=2018)
    >>> # Import and process
    >>> results = import_post2018(min_year=2018, max_year=2025)
    >>> print(results)
    {'loans': True, 'panel': True, 'transmittal_series': True}
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mortgage_data_manager.core.download import summarize_results
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.sources.hmda.config import (
    HMDAConfig,
    resolve_datasets,
)

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
    products: Sequence[str] | None = None,
    datasets: Sequence[str] | None = None,
    *,
    output_dir: Path | str | None = None,
    min_year: int | None = None,
    max_year: int | None = None,
    **kwargs: Any,
) -> dict[str, int]:
    """Download HMDA raw files from the CFPB file servers.

    Thin wrapper around `~mortgage_data_manager.sources.hmda.download.download_hmda_files`
    that adds the pipeline's banner logging and reduces the per-file results to
    a count per outcome.

    Args:
        products: Products to fetch. ``None`` = the default set (everything but
            ``mlar``).
        datasets: Record types to fetch. ``None`` = all four.
        output_dir: Root of the raw layer. ``None`` = configured HMDAConfig.HMDA_RAW_DIR.
        min_year: First filing year to fetch. ``None`` = each series' own start.
        max_year: Last filing year to fetch. ``None`` = the current year.
        **kwargs: Forwarded to ``download_hmda_files`` — ``file_format``,
            ``overwrite``, ``refresh``, ``pause``, ``timeout``, ``retries``,
            ``show_progress``.

    Returns:
        Counts keyed by ``DownloadStatus`` value — ``success``, ``skipped_exists``,
        ``skipped_size_match``, ``failed``. Not-yet-published candidates are never
        queued, so they appear in none of them.

    Example:
        >>> # Everything not already on disk
        >>> run_download()

        >>> # Just the snapshot LAR files for recent years
        >>> run_download(["snapshot"], ["loans"], min_year=2020)

        >>> # Re-check what is on disk against the server
        >>> run_download(refresh=True)

    Note:
        Files are organized into ``loans/``, ``panel/``, ``transmittal_series/``,
        ``msamd/`` and ``misc/`` under the raw root. Existing files are skipped
        unless ``overwrite`` or ``refresh`` is passed.
    """
    dest_path = Path(output_dir) if output_dir is not None else HMDAConfig.HMDA_RAW_DIR
    dest_path.resolve().mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info("HMDA Download Pipeline")
    logger.info("=" * 60)
    logger.info("Products: %s", ", ".join(products) if products else "default")
    logger.info("Datasets: %s", ", ".join(datasets) if datasets else "all")
    logger.info("Years: %s-%s", min_year or "start", max_year or "current")
    logger.info("Output dir: %s", dest_path)
    logger.info("")

    results = download_hmda_files(
        products,
        datasets,
        output_dir=dest_path,
        min_year=min_year,
        max_year=max_year,
        **kwargs,
    )
    summary = summarize_results(results)
    if summary.get("failed"):
        logger.error("%s file(s) failed to download", summary["failed"])
    return summary


def bronze_post2018(
    min_year: int = 2018,
    max_year: int = 2025,
    datasets: Sequence[str] | None = None,
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
    datasets = resolve_datasets(datasets)

    logger.info("HMDA Post-2018 Bronze Layer")
    logger.info(
        "Years: %s-%s | Datasets: %s | Overwrite: %s",
        min_year,
        max_year,
        ", ".join(datasets),
        overwrite,
    )

    results: dict[str, bool] = {}
    for dataset in datasets:
        (HMDAConfig.HMDA_BRONZE_DIR / dataset).resolve().mkdir(parents=True, exist_ok=True)
        try:
            logger.info("Building bronze for %s...", dataset)
            build_bronze_post2018(
                dataset, min_year=min_year, max_year=max_year, overwrite=overwrite
            )
            results[dataset] = True
            logger.info("✅ Bronze %s completed", dataset)
        except Exception as e:
            logger.error("❌ Bronze %s failed: %s", dataset, e)
            results[dataset] = False

    return results


def silver_post2018(
    min_year: int = 2018,
    max_year: int = 2025,
    datasets: Sequence[str] | None = None,
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
    datasets = resolve_datasets(datasets)

    logger.info("HMDA Post-2018 Silver Layer (Hive-partitioned)")
    logger.info(
        "Years: %s-%s | Datasets: %s | Overwrite: %s",
        min_year,
        max_year,
        ", ".join(datasets),
        overwrite,
    )

    results: dict[str, bool] = {}
    for dataset in datasets:
        (HMDAConfig.HMDA_SILVER_DIR / dataset / "post2018").resolve().mkdir(
            parents=True, exist_ok=True
        )
        try:
            logger.info("Building silver for %s...", dataset)
            build_silver_post2018(
                dataset,
                min_year=min_year,
                max_year=max_year,
                overwrite=overwrite,
            )
            results[dataset] = True
            logger.info("✅ Silver %s completed", dataset)
        except Exception as e:
            logger.error("❌ Silver %s failed: %s", dataset, e)
            results[dataset] = False

    return results


def import_post2018(
    min_year: int = 2018,
    max_year: int = 2025,
    datasets: Sequence[str] | None = None,
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
        - Bronze layer: data/bronze/{dataset}/ (flat — all eras together)
        - Silver layer: data/silver/{dataset}/post2018/activity_year=YYYY/file_type=X/
        - HMDAIndex unique identifier is automatically created
    """
    datasets = resolve_datasets(datasets)

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
    for name, success in results.items():
        status = "✅" if success else "❌"
        logger.info("%s %s: %s", status, name, "Success" if success else "Failed")

    return results


def bronze_2007_2017(
    min_year: int = 2007,
    max_year: int = 2017,
    datasets: Sequence[str] | None = None,
    overwrite: bool = False,
) -> dict[str, bool]:
    """Build the 2007-2017 bronze layer.

    Args:
        min_year: Minimum year to process.
        max_year: Maximum year to process.
        datasets: Datasets to process. ``None`` = all.
        overwrite: Whether to replace existing files.

    Returns:
        Per-dataset success flags.
    """
    datasets = resolve_datasets(datasets)
    logger.info("HMDA 2007-2017 Bronze Layer")
    logger.info(
        "Years: %s-%s | Datasets: %s | Overwrite: %s",
        min_year,
        max_year,
        ", ".join(datasets),
        overwrite,
    )

    results: dict[str, bool] = {}
    for dataset in datasets:
        (HMDAConfig.HMDA_BRONZE_DIR / dataset).resolve().mkdir(parents=True, exist_ok=True)
        try:
            build_bronze_period_2007_2017(
                dataset, min_year=min_year, max_year=max_year, overwrite=overwrite
            )
            results[dataset] = True
            logger.info("✅ Bronze %s completed", dataset)
        except Exception as e:
            logger.error("❌ Bronze %s failed: %s", dataset, e)
            results[dataset] = False
    return results


def silver_2007_2017(
    min_year: int = 2007,
    max_year: int = 2017,
    datasets: Sequence[str] | None = None,
    overwrite: bool = False,
) -> dict[str, bool]:
    """Build the 2007-2017 silver layer.

    Args:
        min_year: Minimum year to process.
        max_year: Maximum year to process.
        datasets: Datasets to process. ``None`` = all.
        overwrite: Whether to replace existing files.

    Returns:
        Per-dataset success flags.
    """
    datasets = resolve_datasets(datasets)
    logger.info("HMDA 2007-2017 Silver Layer (Hive-partitioned)")
    logger.info(
        "Years: %s-%s | Datasets: %s | Overwrite: %s",
        min_year,
        max_year,
        ", ".join(datasets),
        overwrite,
    )

    results: dict[str, bool] = {}
    for dataset in datasets:
        (HMDAConfig.HMDA_SILVER_DIR / dataset / "period_2007_2017").resolve().mkdir(
            parents=True, exist_ok=True
        )
        try:
            build_silver_period_2007_2017(
                dataset,
                min_year=min_year,
                max_year=max_year,
                overwrite=overwrite,
            )
            results[dataset] = True
            logger.info("✅ Silver %s completed", dataset)
        except Exception as e:
            logger.error("❌ Silver %s failed: %s", dataset, e)
            results[dataset] = False
    return results


def import_2007_2017(
    min_year: int = 2007,
    max_year: int = 2017,
    datasets: Sequence[str] | None = None,
    overwrite: bool = False,
) -> dict[str, bool]:
    """Complete 2007-2017 HMDA import pipeline (bronze + silver layers).

    Runs ``bronze_2007_2017`` followed by ``silver_2007_2017``. Silver runs only
    for the datasets whose bronze succeeded.

    Args:
        min_year: Minimum year to process.
        max_year: Maximum year to process.
        datasets: Datasets to process. ``None`` = all.
        overwrite: Whether to replace existing files.

    Returns:
        Per-dataset success flags.
    """
    logger.info("=" * 60)
    logger.info("HMDA 2007-2017 Import Pipeline")
    logger.info("=" * 60)

    bronze_results = bronze_2007_2017(
        min_year=min_year, max_year=max_year, datasets=datasets, overwrite=overwrite
    )
    silver_datasets = [d for d, ok in bronze_results.items() if ok]
    silver_results = silver_2007_2017(
        min_year=min_year, max_year=max_year, datasets=silver_datasets, overwrite=overwrite
    )

    results = {
        d: bronze_results.get(d, False) and silver_results.get(d, False) for d in bronze_results
    }
    if all(results.values()):
        logger.info("✅ Pipeline completed successfully!")
    return results


def bronze_pre2007(
    min_year: int = 1990,
    max_year: int = 2006,
    datasets: Sequence[str] | None = None,
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
    datasets = resolve_datasets(datasets)

    logger.info("HMDA Pre-2007 Bronze Layer")
    logger.info(
        "Years: %s-%s | Datasets: %s | Overwrite: %s",
        min_year,
        max_year,
        ", ".join(datasets),
        overwrite,
    )

    results: dict[str, bool] = {}
    for dataset in datasets:
        (HMDAConfig.HMDA_BRONZE_DIR / dataset).resolve().mkdir(parents=True, exist_ok=True)
        try:
            build_bronze_pre2007(dataset, min_year=min_year, max_year=max_year, overwrite=overwrite)
            results[dataset] = True
            logger.info("✅ Bronze %s completed", dataset)
        except Exception as e:
            logger.error("❌ Bronze %s failed: %s", dataset, e)
            results[dataset] = False

    return results


def silver_pre2007(
    min_year: int = 1990,
    max_year: int = 2006,
    datasets: Sequence[str] | None = None,
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
    datasets = resolve_datasets(datasets)

    logger.info("HMDA Pre-2007 Silver Layer (Hive-partitioned)")
    logger.info(
        "Years: %s-%s | Datasets: %s | Overwrite: %s",
        min_year,
        max_year,
        ", ".join(datasets),
        overwrite,
    )

    results: dict[str, bool] = {}
    for dataset in datasets:
        (HMDAConfig.HMDA_SILVER_DIR / dataset / "pre2007").resolve().mkdir(
            parents=True, exist_ok=True
        )
        try:
            build_silver_pre2007(
                dataset,
                min_year=min_year,
                max_year=max_year,
                overwrite=overwrite,
            )
            results[dataset] = True
            logger.info("✅ Silver %s completed", dataset)
        except Exception as e:
            logger.error("❌ Silver %s failed: %s", dataset, e)
            results[dataset] = False

    return results


def import_pre2007(
    min_year: int = 1990,
    max_year: int = 2006,
    datasets: Sequence[str] | None = None,
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
    datasets = resolve_datasets(datasets)

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
    for name, success in results.items():
        status = "✅" if success else "❌"
        logger.info("%s %s: %s", status, name, "Success" if success else "Failed")

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

    Example:
        >>> run_pipeline(eras=["post2018"], download=True)
        {'post2018': True}
    """
    if eras is None:
        eras = list(ERA_PIPELINES)

    invalid = [era for era in eras if era not in ERA_PIPELINES]
    if invalid:
        valid = ", ".join(ERA_PIPELINES)
        raise ValueError(f"Invalid era(s): {', '.join(invalid)}. Valid eras are: {valid}")

    results: dict[str, bool] = {}
    for era in eras:
        if download:
            if era == "post2018":
                run_download(["snapshot", "one_year", "three_year"], min_year=2018)
            elif era == "2007_2017":
                run_download(["historic"])
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
