"""End-to-end pipeline helpers for FHA snapshot data.

Stage entries mirror the medallion layers -- `run_bronze` and
`run_silver`, composed by `run_import` -- plus `run_pipeline`,
which prepends the download step. Each takes a dataset selector and returns
per-dataset counts, so callers can report progress and exit non-zero on failure.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.sources.fha.config import (
    DATASET_MAP,
    VALID_DATASETS,
    FHAConfig,
    resolve_datasets,
)
from mortgage_data_manager.sources.fha.download import DOWNLOADERS
from mortgage_data_manager.sources.fha.import_bronze import (
    convert_fha_hecm_snapshots,
    convert_fha_sf_snapshots,
)
from mortgage_data_manager.sources.fha.import_silver import build_silver_snapshots

logger = get_logger(__name__)

DEFAULT_MIN_YEAR = 2010
DEFAULT_MAX_YEAR = 2025

DatasetType = Literal["single-family", "hecm", "both"]

_CONVERTERS: dict[str, Callable[..., None]] = {
    "single-family": convert_fha_sf_snapshots,
    "hecm": convert_fha_hecm_snapshots,
}


def _layer_dirs(
    dataset: str,
    *,
    raw_root: Path | str | None = None,
    bronze_root: Path | str | None = None,
    silver_root: Path | str | None = None,
) -> tuple[Path, Path, Path]:
    """Resolve the raw, bronze and silver directories for one dataset."""
    name = DATASET_MAP[dataset]
    return (
        Path(raw_root or FHAConfig.FHA_RAW_DIR) / name,
        Path(bronze_root or FHAConfig.FHA_BRONZE_DIR) / name,
        Path(silver_root or FHAConfig.FHA_SILVER_DIR) / name,
    )


def _run_across(
    datasets: list[str] | None,
    label: str,
    step: Callable[[str], None],
) -> dict[str, int]:
    """Run ``step`` once per selected dataset, counting outcomes.

    A failure is logged and counted rather than raised, so one broken dataset
    does not abandon the other.

    Returns:
        Counts keyed by ``processed``, ``successful`` and ``failed``.
    """
    counts = {"processed": 0, "successful": 0, "failed": 0}
    for dataset in resolve_datasets(datasets):
        counts["processed"] += 1
        logger.info("FHA %s: %s", label, dataset)
        try:
            step(dataset)
        except Exception:
            logger.exception("FHA %s failed for %s", label, dataset)
            counts["failed"] += 1
        else:
            counts["successful"] += 1
    return counts


def run_bronze(
    datasets: list[str] | None = None,
    *,
    overwrite: bool = False,
    raw_root: Path | str | None = None,
    bronze_root: Path | str | None = None,
) -> dict[str, int]:
    """Bronze step: raw Excel snapshots -> parquet.

    Args:
        datasets: Subset of `VALID_DATASETS`. ``None`` = all.
        overwrite: Rebuild bronze parquet that already exists.
        raw_root: Override the raw layer root (defaults to the configured path).
        bronze_root: Override the bronze layer root.

    Returns:
        Counts: ``processed``, ``successful``, ``failed`` datasets.
    """

    def step(dataset: str) -> None:
        raw_dir, bronze_dir, _ = _layer_dirs(dataset, raw_root=raw_root, bronze_root=bronze_root)
        _CONVERTERS[dataset](raw_dir, bronze_dir, overwrite=overwrite)

    return _run_across(datasets, "bronze", step)


def run_silver(
    datasets: list[str] | None = None,
    *,
    overwrite: bool = False,
    min_year: int = DEFAULT_MIN_YEAR,
    max_year: int = DEFAULT_MAX_YEAR,
    bronze_root: Path | str | None = None,
    silver_root: Path | str | None = None,
) -> dict[str, int]:
    """Silver step: bronze parquet -> hive-partitioned, enriched silver.

    Args:
        datasets: Subset of `VALID_DATASETS`. ``None`` = all.
        overwrite: Rebuild every partition in range rather than appending only
            the periods that are missing.
        min_year: Earliest snapshot year to include.
        max_year: Latest snapshot year to include.
        bronze_root: Override the bronze layer root.
        silver_root: Override the silver layer root.

    Returns:
        Counts: ``processed``, ``successful``, ``failed`` datasets.
    """

    def step(dataset: str) -> None:
        _, bronze_dir, silver_dir = _layer_dirs(
            dataset, bronze_root=bronze_root, silver_root=silver_root
        )
        build_silver_snapshots(
            bronze_dir,
            silver_dir,
            file_type=DATASET_MAP[dataset],
            min_year=min_year,
            max_year=max_year,
            overwrite=overwrite,
        )

    return _run_across(datasets, "silver", step)


def run_import(
    datasets: list[str] | None = None,
    *,
    overwrite: bool = False,
    min_year: int = DEFAULT_MIN_YEAR,
    max_year: int = DEFAULT_MAX_YEAR,
    raw_root: Path | str | None = None,
    bronze_root: Path | str | None = None,
    silver_root: Path | str | None = None,
) -> dict[str, int]:
    """Import step: bronze then silver for each selected dataset.

    Silver is skipped for any dataset whose bronze step failed, so a broken
    conversion cannot quietly rebuild silver from stale parquet.

    Args:
        datasets: Subset of `VALID_DATASETS`. ``None`` = all.
        overwrite: Rebuild outputs that already exist.
        min_year: Earliest snapshot year to include.
        max_year: Latest snapshot year to include.
        raw_root: Override the raw layer root.
        bronze_root: Override the bronze layer root.
        silver_root: Override the silver layer root.

    Returns:
        Counts: ``processed``, ``successful``, ``failed`` datasets. A dataset is
        successful only if both of its steps succeeded.
    """
    counts = {"processed": 0, "successful": 0, "failed": 0}
    for dataset in resolve_datasets(datasets):
        counts["processed"] += 1

        bronze = run_bronze(
            [dataset], overwrite=overwrite, raw_root=raw_root, bronze_root=bronze_root
        )
        if bronze["failed"]:
            counts["failed"] += 1
            continue

        silver = run_silver(
            [dataset],
            overwrite=overwrite,
            min_year=min_year,
            max_year=max_year,
            bronze_root=bronze_root,
            silver_root=silver_root,
        )
        if silver["failed"]:
            counts["failed"] += 1
        else:
            counts["successful"] += 1

    return counts


def run_pipeline(
    dataset: DatasetType,
    *,
    skip_download: bool = False,
    skip_import: bool = False,
    pause: float | None = None,
    include_zip: bool = True,
    overwrite: bool = False,
    min_year: int = DEFAULT_MIN_YEAR,
    max_year: int = DEFAULT_MAX_YEAR,
) -> dict[str, int]:
    """Run the complete download and import pipeline for FHA snapshots.

    Args:
        dataset: Which dataset to process ("single-family", "hecm", or "both").
        skip_download: Skip the download step and only run import.
        skip_import: Skip the import step and only run download.
        pause: Seconds to pause between downloads. Defaults to
            MortgageDataConfig.DOWNLOAD_PAUSE.
        include_zip: Whether to download zip archives.
        overwrite: Regenerate parquet files even if they already exist.
        min_year: Earliest snapshot year to include.
        max_year: Latest snapshot year to include.

    Returns:
        Counts: ``processed``, ``successful``, ``failed``, summed across the
        steps that ran.
    """
    datasets: list[str] = list(VALID_DATASETS) if dataset == "both" else [dataset]
    counts = {"processed": 0, "successful": 0, "failed": 0}

    def accumulate(result: dict[str, int]) -> None:
        for key in counts:
            counts[key] += result[key]

    if not skip_download:

        def download(ds: str) -> None:
            DOWNLOADERS[ds](pause=pause, include_zip=include_zip)

        accumulate(_run_across(datasets, "download", download))

    if not skip_import:
        accumulate(
            run_import(
                datasets,
                overwrite=overwrite,
                min_year=min_year,
                max_year=max_year,
            )
        )

    logger.info("All pipelines complete")
    return counts
