"""HUD multifamily pipeline - high-level orchestration.

Composes download → bronze → silver for the HUD multifamily snapshot extracts.
Pattern follows the NFIP/GNMA/FHFA pipelines.
"""

from __future__ import annotations

from typing import Any

from mortgage_data_manager.core.download import DownloadStatus
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.hud_mf.download import download_dictionaries, download_files
from mortgage_data_manager.hud_mf.import_bronze import build_bronze
from mortgage_data_manager.hud_mf.import_silver import build_silver

logger = get_logger(__name__)


def run_download(
    datasets: list[str] | None = None,
    *,
    overwrite: bool = False,
    pause: float | None = None,
    timeout: int | None = None,
    skip_dictionaries: bool = False,
) -> dict[str, int]:
    """Download step: source workbooks plus Data Element Dictionary PDFs.

    Args:
        datasets: Subset of HUD-MF tables. ``None`` = all.
        overwrite: Redownload existing files.
        pause: Seconds between downloads (None = config default).
        timeout: Per-request timeout (None = config default).
        skip_dictionaries: Skip the DED PDFs (workbooks only).

    Returns:
        Counts: ``downloaded``, ``skipped``, ``failed``, ``dictionaries``.
    """
    results = download_files(datasets, overwrite=overwrite, pause=pause, timeout=timeout)
    dictionaries = (
        []
        if skip_dictionaries
        else download_dictionaries(overwrite=overwrite, pause=pause, timeout=timeout)
    )
    return {
        "downloaded": sum(1 for r in results if r.status == DownloadStatus.SUCCESS),
        "skipped": sum(1 for r in results if r.status == DownloadStatus.SKIPPED_EXISTS),
        "failed": sum(1 for r in results if r.status == DownloadStatus.FAILED),
        "dictionaries": sum(1 for r in dictionaries if r.status == DownloadStatus.SUCCESS),
    }


def run_bronze(
    datasets: list[str] | None = None,
    *,
    overwrite: bool = False,
) -> dict[str, int]:
    """Bronze step: typed parquet per logical table.

    Args:
        datasets: Subset of HUD-MF tables. ``None`` = all.
        overwrite: Rebuild existing bronze parquets.

    Returns:
        Counts: ``built``.
    """
    return {"built": len(build_bronze(datasets, overwrite=overwrite))}


def run_silver(
    datasets: list[str] | None = None,
    *,
    overwrite: bool = False,
) -> dict[str, int]:
    """Silver step: cleaned, key-normalized parquet per logical table.

    Args:
        datasets: Subset of HUD-MF tables. ``None`` = all.
        overwrite: Rebuild existing silver parquets.

    Returns:
        Counts: ``built``.
    """
    return {"built": len(build_silver(datasets, overwrite=overwrite))}


def run_pipeline(
    datasets: list[str] | None = None,
    *,
    overwrite: bool = False,
    skip_download: bool = False,
    skip_bronze: bool = False,
    skip_silver: bool = False,
    skip_dictionaries: bool = False,
    pause: float | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Run the complete HUD multifamily end-to-end pipeline.

    Steps:
        1. Download source workbooks + DED PDFs (optional)
        2. Build typed bronze parquets (optional)
        3. Build cleaned silver parquets (optional)

    Args:
        datasets: Subset of HUD-MF tables. ``None`` = all.
        overwrite: Overwrite/rebuild existing outputs at every step.
        skip_download: Skip the download step (use existing raw files).
        skip_bronze: Skip the bronze step.
        skip_silver: Skip the silver step.
        skip_dictionaries: Skip the DED PDFs during download.
        pause: Seconds between downloads (None = config default).
        timeout: Per-request timeout (None = config default).

    Returns:
        Dictionary with per-step result counts (see the ``run_*`` steps).

    Raises:
        RuntimeError: If any workbook download fails (downstream layers would
            otherwise silently build from a stale or partial snapshot).
    """
    results: dict[str, Any] = {}

    if not skip_download:
        logger.info("Step 1: Downloading HUD-MF workbooks and dictionaries")
        results["download"] = run_download(
            datasets,
            overwrite=overwrite,
            pause=pause,
            timeout=timeout,
            skip_dictionaries=skip_dictionaries,
        )
        if results["download"]["failed"]:
            raise RuntimeError(
                f"HUD-MF download failed for {results['download']['failed']} workbook(s); "
                "aborting before bronze/silver build from stale data."
            )

    if not skip_bronze:
        logger.info("Step 2: Building HUD-MF bronze layer")
        results["bronze"] = run_bronze(datasets, overwrite=overwrite)

    if not skip_silver:
        logger.info("Step 3: Building HUD-MF silver layer")
        results["silver"] = run_silver(datasets, overwrite=overwrite)

    return results
