"""End-to-end pipeline helpers for FHA snapshot data.

Provides import functions that orchestrate bronze and silver processing,
plus a top-level ``run_pipeline`` function that combines download and import.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.fha.config import FHAConfig
from mortgage_data_manager.fha.download import (
    download_hecm_snapshots,
    download_single_family_snapshots,
)
from mortgage_data_manager.fha.import_bronze import (
    SnapshotType,
    convert_fha_hecm_snapshots,
    convert_fha_sf_snapshots,
)
from mortgage_data_manager.fha.import_silver import (
    save_clean_snapshots_to_db,
    update_clean_snapshots_to_db,
)

logger = get_logger(__name__)

DEFAULT_MIN_YEAR = 2010
DEFAULT_MAX_YEAR = 2025

DatasetType = Literal["single-family", "hecm", "both"]


@dataclass(frozen=True)
class _ImportDefaults:
    """Container for defaults shared by snapshot import helpers."""

    raw_dir: Path
    bronze_dir: Path
    silver_dir: Path
    file_type: SnapshotType


_SINGLE_FAMILY_DEFAULTS = _ImportDefaults(
    raw_dir=FHAConfig.FHA_RAW_DIR / "single_family",
    bronze_dir=FHAConfig.FHA_BRONZE_DIR / "single_family",
    silver_dir=FHAConfig.FHA_SILVER_DIR / "single_family",
    file_type="single_family",
)

_HECM_DEFAULTS = _ImportDefaults(
    raw_dir=FHAConfig.FHA_RAW_DIR / "hecm",
    bronze_dir=FHAConfig.FHA_BRONZE_DIR / "hecm",
    silver_dir=FHAConfig.FHA_SILVER_DIR / "hecm",
    file_type="hecm",
)


def import_single_family_snapshots(
    raw_dir: Path | str = _SINGLE_FAMILY_DEFAULTS.raw_dir,
    bronze_dir: Path | str = _SINGLE_FAMILY_DEFAULTS.bronze_dir,
    silver_dir: Path | str = _SINGLE_FAMILY_DEFAULTS.silver_dir,
    *,
    overwrite: bool = False,
    min_year: int = DEFAULT_MIN_YEAR,
    max_year: int = DEFAULT_MAX_YEAR,
    add_fips: bool = True,
    add_date: bool = True,
) -> None:
    """Import cleaned Single Family snapshots into the hive-structured database."""
    _run_import_pipeline(
        raw_dir=Path(raw_dir),
        bronze_dir=Path(bronze_dir),
        silver_dir=Path(silver_dir),
        file_type="single_family",
        overwrite=overwrite,
        min_year=min_year,
        max_year=max_year,
        add_fips=add_fips,
        add_date=add_date,
    )


def import_hecm_snapshots(
    raw_dir: Path | str = _HECM_DEFAULTS.raw_dir,
    bronze_dir: Path | str = _HECM_DEFAULTS.bronze_dir,
    silver_dir: Path | str = _HECM_DEFAULTS.silver_dir,
    *,
    overwrite: bool = False,
    min_year: int = DEFAULT_MIN_YEAR,
    max_year: int = DEFAULT_MAX_YEAR,
    add_fips: bool = True,
    add_date: bool = True,
) -> None:
    """Import cleaned HECM snapshots into the hive-structured database."""
    _run_import_pipeline(
        raw_dir=Path(raw_dir),
        bronze_dir=Path(bronze_dir),
        silver_dir=Path(silver_dir),
        file_type="hecm",
        overwrite=overwrite,
        min_year=min_year,
        max_year=max_year,
        add_fips=add_fips,
        add_date=add_date,
    )


def _run_bronze_step(
    *,
    raw_dir: Path,
    bronze_dir: Path,
    file_type: SnapshotType,
    overwrite: bool,
) -> None:
    """Run only the bronze conversion step for a snapshot type."""
    raw_dir = raw_dir.expanduser()
    bronze_dir = bronze_dir.expanduser()
    bronze_dir.mkdir(parents=True, exist_ok=True)

    if file_type == "single_family":
        convert_fha_sf_snapshots(raw_dir, bronze_dir, overwrite=overwrite)
    else:
        convert_fha_hecm_snapshots(raw_dir, bronze_dir, overwrite=overwrite)


def _run_silver_step(
    *,
    bronze_dir: Path,
    silver_dir: Path,
    file_type: SnapshotType,
    overwrite: bool,
    min_year: int,
    max_year: int,
    add_fips: bool,
    add_date: bool,
) -> None:
    """Run only the silver cleaning step for a snapshot type."""
    bronze_dir = bronze_dir.expanduser()
    silver_dir = silver_dir.expanduser()
    silver_dir.mkdir(parents=True, exist_ok=True)

    silver_fn = save_clean_snapshots_to_db if overwrite else update_clean_snapshots_to_db
    silver_fn(
        bronze_dir,
        silver_dir,
        min_year=min_year,
        max_year=max_year,
        file_type=file_type,
        add_fips=add_fips,
        add_date=add_date,
    )


def _run_import_pipeline(
    *,
    raw_dir: Path,
    bronze_dir: Path,
    silver_dir: Path,
    file_type: SnapshotType,
    overwrite: bool,
    min_year: int,
    max_year: int,
    add_fips: bool,
    add_date: bool,
) -> None:
    """Execute the two-step import process shared across snapshot types."""
    _run_bronze_step(
        raw_dir=raw_dir,
        bronze_dir=bronze_dir,
        file_type=file_type,
        overwrite=overwrite,
    )
    _run_silver_step(
        bronze_dir=bronze_dir,
        silver_dir=silver_dir,
        file_type=file_type,
        overwrite=overwrite,
        min_year=min_year,
        max_year=max_year,
        add_fips=add_fips,
        add_date=add_date,
    )


def bronze_single_family(
    raw_dir: Path | str = _SINGLE_FAMILY_DEFAULTS.raw_dir,
    bronze_dir: Path | str = _SINGLE_FAMILY_DEFAULTS.bronze_dir,
    *,
    overwrite: bool = False,
) -> None:
    """Convert Single Family raw snapshots to bronze parquet."""
    _run_bronze_step(
        raw_dir=Path(raw_dir),
        bronze_dir=Path(bronze_dir),
        file_type="single_family",
        overwrite=overwrite,
    )


def bronze_hecm(
    raw_dir: Path | str = _HECM_DEFAULTS.raw_dir,
    bronze_dir: Path | str = _HECM_DEFAULTS.bronze_dir,
    *,
    overwrite: bool = False,
) -> None:
    """Convert HECM raw snapshots to bronze parquet."""
    _run_bronze_step(
        raw_dir=Path(raw_dir),
        bronze_dir=Path(bronze_dir),
        file_type="hecm",
        overwrite=overwrite,
    )


def silver_single_family(
    bronze_dir: Path | str = _SINGLE_FAMILY_DEFAULTS.bronze_dir,
    silver_dir: Path | str = _SINGLE_FAMILY_DEFAULTS.silver_dir,
    *,
    overwrite: bool = False,
    min_year: int = DEFAULT_MIN_YEAR,
    max_year: int = DEFAULT_MAX_YEAR,
    add_fips: bool = True,
    add_date: bool = True,
) -> None:
    """Clean Single Family bronze snapshots into the hive-structured silver layer."""
    _run_silver_step(
        bronze_dir=Path(bronze_dir),
        silver_dir=Path(silver_dir),
        file_type="single_family",
        overwrite=overwrite,
        min_year=min_year,
        max_year=max_year,
        add_fips=add_fips,
        add_date=add_date,
    )


def silver_hecm(
    bronze_dir: Path | str = _HECM_DEFAULTS.bronze_dir,
    silver_dir: Path | str = _HECM_DEFAULTS.silver_dir,
    *,
    overwrite: bool = False,
    min_year: int = DEFAULT_MIN_YEAR,
    max_year: int = DEFAULT_MAX_YEAR,
    add_fips: bool = True,
    add_date: bool = True,
) -> None:
    """Clean HECM bronze snapshots into the hive-structured silver layer."""
    _run_silver_step(
        bronze_dir=Path(bronze_dir),
        silver_dir=Path(silver_dir),
        file_type="hecm",
        overwrite=overwrite,
        min_year=min_year,
        max_year=max_year,
        add_fips=add_fips,
        add_date=add_date,
    )


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
    add_fips: bool = True,
    add_date: bool = True,
) -> None:
    """Run the complete download and import pipeline for FHA snapshots.

    Args:
        dataset: Which dataset to process ("single-family", "hecm", or "both").
        skip_download: Skip the download step and only run import.
        skip_import: Skip the import step and only run download.
        pause: Seconds to pause between downloads. Defaults to
            MortgageDataConfig.DOWNLOAD_PAUSE.
        include_zip: Whether to download zip archives.
        overwrite: Regenerate parquet files even if they already exist.
        min_year: Earliest endorsement year to include.
        max_year: Latest endorsement year to include.
        add_fips: Add county FIPS codes to the database output.
        add_date: Add derived Date column to the database output.
    """
    datasets_to_process = []
    if dataset == "both":
        datasets_to_process = ["single-family", "hecm"]
    else:
        datasets_to_process = [dataset]

    for ds in datasets_to_process:
        logger.info(f"Processing {ds.upper()} dataset")

        if not skip_download:
            logger.info(f"Step 1: Downloading {ds} snapshots")
            if ds == "single-family":
                download_single_family_snapshots(
                    pause=pause,
                    include_zip=include_zip,
                )
            else:
                download_hecm_snapshots(
                    pause=pause,
                    include_zip=include_zip,
                )

        if not skip_import:
            logger.info(f"Step 2: Importing and cleaning {ds} snapshots")
            if ds == "single-family":
                import_single_family_snapshots(
                    overwrite=overwrite,
                    min_year=min_year,
                    max_year=max_year,
                    add_fips=add_fips,
                    add_date=add_date,
                )
            else:
                import_hecm_snapshots(
                    overwrite=overwrite,
                    min_year=min_year,
                    max_year=max_year,
                    add_fips=add_fips,
                    add_date=add_date,
                )

        logger.info(f"{ds.upper()} pipeline complete")

    logger.info("All pipelines complete")
