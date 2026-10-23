"""USPS crosswalk pipeline — high-level orchestration.

Composes download → bronze. There is no silver step for this source (see
``config`` for why), so bronze is the end of the line.

Both steps are idempotent and skip work already done, so ``run_pipeline()`` with
no arguments is the normal way to bring the source current: it probes HUD for
anything newer than what is on disk, fetches it, and builds the parquet.
"""

from __future__ import annotations

from typing import Any

from mortgage_data_manager.core.download import DownloadStatus
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.sources.usps_crosswalk.download import download_all
from mortgage_data_manager.sources.usps_crosswalk.import_bronze import build_bronze

logger = get_logger(__name__)


def run_download(
    datasets: list[str] | None = None,
    *,
    min_year: int | None = None,
    max_year: int | None = None,
    overwrite: bool = False,
    pause: float | None = None,
    timeout: int | None = None,
) -> dict[str, int]:
    """Download step: raw JSON for every missing type-quarter.

    Args:
        datasets: Crosswalk types to fetch. ``None`` = all 12.
        min_year: First year to consider (``None`` = each type's own start).
        max_year: Last year to consider (``None`` = through the current quarter).
        overwrite: Re-download type-quarters already present.
        pause: Seconds between requests (``None`` = config default).
        timeout: Per-request timeout in seconds.

    Returns:
        ``{"saved": <raw files written>, "failed": <type-quarters that failed>}``.
    """
    results = download_all(
        datasets,
        min_year=min_year,
        max_year=max_year,
        overwrite=overwrite,
        pause=pause,
        timeout=timeout,
    )
    return {
        "saved": sum(1 for r in results if r.status == DownloadStatus.SUCCESS),
        "failed": sum(1 for r in results if r.status == DownloadStatus.FAILED),
    }


def run_pipeline(
    datasets: list[str] | None = None,
    *,
    min_year: int | None = None,
    max_year: int | None = None,
    overwrite: bool = False,
    pause: float | None = None,
    timeout: int | None = None,
    skip_download: bool = False,
    skip_bronze: bool = False,
) -> dict[str, Any]:
    """End-to-end: download → bronze.

    Args:
        datasets: Crosswalk types to process. ``None`` = all 12.
        min_year: First year to consider (``None`` = each type's own start).
        max_year: Last year to consider (``None`` = through the current quarter).
        overwrite: Redo work already on disk.
        pause: Seconds between API requests (``None`` = config default).
        timeout: Per-request timeout in seconds.
        skip_download: Build bronze from the raw already present.
        skip_bronze: Fetch raw only.

    Returns:
        ``{"download": {...}, "bronze": {...}}``, omitting skipped steps.
    """
    results: dict[str, Any] = {}

    if not skip_download:
        logger.info("Step 1/2: downloading raw JSON from the HUD API")
        results["download"] = run_download(
            datasets,
            min_year=min_year,
            max_year=max_year,
            overwrite=overwrite,
            pause=pause,
            timeout=timeout,
        )

    if not skip_bronze:
        logger.info("Step 2/2: building bronze parquet")
        results["bronze"] = build_bronze(
            datasets,
            min_year=min_year,
            max_year=max_year,
            overwrite=overwrite,
        )

    return results
