"""Download utilities for FHFA (Federal Housing Finance Agency) data.

This module provides functions for downloading FHFA enterprise PUDB data
and dictionary files from the FHFA website.

Example:
    >>> from mortgage_data_manager.fhfa.download import download_fhfa_data
    >>> results = download_fhfa_data()
    >>> print(f"Downloaded {len([r for r in results if r.status.value == 'success'])} files")
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.download import (
    DownloadResult,
    DownloadTask,
    batch_download,
    retry_request,
    summarize_results,
)
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.fhfa.config import FHFAConfig

logger = get_logger(__name__)

# FHFA Public Use Database URL
BASE_URL = "https://www.fhfa.gov/data/pudb"


def discover_files_from_page(
    base_url: str,
    allowed_extensions: tuple[str, ...] = (".zip",),
    included_substrings: tuple[str, ...] | None = None,
    timeout: int | None = None,
    retries: int | None = None,
) -> list[tuple[str, str]]:
    """Discover downloadable files from a web page.

    Scrapes the page to find links matching the specified extensions and
    optional filename substrings.

    Args:
        base_url: URL of the page to scrape
        allowed_extensions: Case-insensitive file extensions to include
        included_substrings: Optional case-insensitive substrings to filter by
        timeout: Request timeout in seconds
        retries: Number of retry attempts

    Returns:
        List of (url, filename) tuples for each discovered file
    """
    timeout = timeout if timeout is not None else MortgageDataConfig.DOWNLOAD_TIMEOUT
    retries = retries if retries is not None else MortgageDataConfig.DOWNLOAD_RETRIES

    logger.info(f"Fetching page: {base_url}")

    response = retry_request(base_url, timeout=timeout, retries=retries)

    soup = BeautifulSoup(response.text, "html.parser")
    links = soup.find_all("a", href=True)

    allowed_lower = tuple(ext.lower() for ext in allowed_extensions)
    substrings_lower = (
        tuple(s.lower() for s in included_substrings) if included_substrings else None
    )

    download_links: list[tuple[str, str]] = []

    for link in links:
        if not isinstance(link, Tag):
            continue
        href_value = link.get("href")
        if not isinstance(href_value, str) or not href_value:
            continue

        file_url = urljoin(base_url, href_value)
        parsed = urlparse(file_url)
        file_name = Path(parsed.path).name
        if not file_name:
            continue

        # Check extension
        ext = Path(file_name).suffix.lower()
        if ext not in allowed_lower:
            continue

        # Check substrings if provided
        if substrings_lower and all(s not in file_name.lower() for s in substrings_lower):
            continue

        download_links.append((file_url, file_name))

    logger.info(f"Found {len(download_links)} files matching criteria")
    return download_links


def download_from_page(
    download_dir: Path,
    allowed_extensions: tuple[str, ...] = (".zip",),
    base_url: str = BASE_URL,
    included_substrings: tuple[str, ...] | None = None,
    *,
    overwrite: bool = False,
    pause: float | None = None,
    timeout: int | None = None,
    retries: int | None = None,
    show_progress: bool = True,
) -> list[DownloadResult]:
    """Scrape page for links and download files that match filters.

    Args:
        download_dir: Directory to save downloaded files
        allowed_extensions: Case-insensitive file extensions to download
        base_url: URL of the page to scrape for download links
        included_substrings: Optional case-insensitive filename substrings to include
        overwrite: If True, overwrite existing files
        pause: Seconds to pause between downloads
        timeout: Request timeout in seconds
        retries: Number of retry attempts
        show_progress: If True, show download progress bars

    Returns:
        List of DownloadResult objects for each file
    """
    download_dir.mkdir(parents=True, exist_ok=True)
    pause = pause if pause is not None else MortgageDataConfig.DOWNLOAD_PAUSE

    # Discover files
    download_links = discover_files_from_page(
        base_url=base_url,
        allowed_extensions=allowed_extensions,
        included_substrings=included_substrings,
        timeout=timeout,
        retries=retries,
    )

    if not download_links:
        logger.warning("No files found to download")
        return []

    # Build download tasks
    downloads: list[DownloadTask] = []
    for url, filename in download_links:
        destination = download_dir / filename
        downloads.append(DownloadTask(url=url, destination=destination))

    # Execute batch download
    mode: Literal["skip", "overwrite"] = "overwrite" if overwrite else "skip"
    results = batch_download(
        downloads,
        mode=mode,
        pause_seconds=pause,
        timeout=timeout,
        retries=retries,
        show_progress=show_progress,
    )

    # Log summary
    summary = summarize_results(results)
    logger.info(
        f"Download complete: {summary.get('success', 0)} downloaded, "
        f"{summary.get('skipped_exists', 0)} skipped, "
        f"{summary.get('failed', 0)} failed"
    )

    return results


def download_fhfa_data(
    output_dir: Path | None = None,
    *,
    overwrite: bool = False,
    pause: float | None = None,
    timeout: int | None = None,
    retries: int | None = None,
    show_progress: bool = True,
) -> list[DownloadResult]:
    """Download FHFA enterprise PUDB data files.

    Downloads enterprise PUDB zip files from the FHFA website.
    Files are named like: 2024_enterprise_pudb.zip

    Args:
        output_dir: Output directory for downloaded files.
            Defaults to FHFAConfig.FHFA_RAW_DIR
        overwrite: If True, overwrite existing files
        pause: Seconds to pause between downloads
        timeout: Request timeout in seconds
        retries: Number of retry attempts
        show_progress: If True, show download progress bars

    Returns:
        List of DownloadResult objects for each file
    """
    download_dir = output_dir or FHFAConfig.FHFA_RAW_DIR
    logger.info(f"Downloading FHFA enterprise data to {download_dir}")

    return download_from_page(
        download_dir=download_dir,
        allowed_extensions=(".zip",),
        included_substrings=("_enterprise_pudb",),
        overwrite=overwrite,
        pause=pause,
        timeout=timeout,
        retries=retries,
        show_progress=show_progress,
    )


def download_fhfa_dictionaries(
    output_dir: Path | None = None,
    *,
    overwrite: bool = False,
    pause: float | None = None,
    timeout: int | None = None,
    retries: int | None = None,
    show_progress: bool = True,
) -> list[DownloadResult]:
    """Download FHFA data dictionary files.

    Downloads PDF and Excel dictionary files from the FHFA website.

    Args:
        output_dir: Output directory for downloaded files.
            Defaults to FHFAConfig.FHFA_SCHEMAS_DIR / "fhfa"
        overwrite: If True, overwrite existing files
        pause: Seconds to pause between downloads
        timeout: Request timeout in seconds
        retries: Number of retry attempts
        show_progress: If True, show download progress bars

    Returns:
        List of DownloadResult objects for each file
    """
    download_dir = output_dir or (FHFAConfig.FHFA_SCHEMAS_DIR / "fhfa")
    logger.info(f"Downloading FHFA dictionaries to {download_dir}")

    return download_from_page(
        download_dir=download_dir,
        allowed_extensions=(".xlsx",),
        included_substrings=("enterprise-pudb",),
        overwrite=overwrite,
        pause=pause,
        timeout=timeout,
        retries=retries,
        show_progress=show_progress,
    )


# ---------------------------------------------------------------------------
# HUD-era GSE PUDB archive (1993-2007 multifamily backfill)
# ---------------------------------------------------------------------------
# The pre-FHFA GSE Public Use Database lives on the HUD USER archive portal.
# Each archive zip bundles one or more data years; a few span several (the
# 1993-1995 and 1996-1997 zips). Filenames are inconsistent across years
# (two- vs four-digit, mixed prefixes) so they are listed explicitly.
HUD_GSE_ARCHIVE_BASE = "https://www.huduser.gov/archives/portal/datasets/gse"

# Archive zip filename -> years of multifamily data it contains.
HUD_GSE_ARCHIVE_FILES: dict[str, tuple[int, ...]] = {
    "gse9395.zip": (1993, 1994, 1995),
    "gse9697.zip": (1996, 1997),
    "gse98.zip": (1998,),
    "gse99.zip": (1999,),
    "gse2000.zip": (2000,),
    "gse_2001_data.zip": (2001,),
    "gse_2002_data.zip": (2002,),
    "gse_2003_data.zip": (2003,),
    "gse_2004_data.zip": (2004,),
    "gse_2005_data.zip": (2005,),
    "gse_2006_data.zip": (2006,),
    "GSE_2007_data.zip": (2007,),
}


def download_hud_gse_archive(
    years: list[int] | None = None,
    output_dir: Path | None = None,
    *,
    overwrite: bool = False,
    pause: float | None = None,
    timeout: int | None = None,
    retries: int | None = None,
    show_progress: bool = True,
) -> list[DownloadResult]:
    """Download HUD-era GSE PUDB multifamily archive zips (1993-2007).

    Fetches the pre-FHFA GSE Public Use Database archive zips from HUD USER into
    the ``hud_era`` sub-directory of the FHFA raw layer, where the HUD-era bronze
    builder reads them. Any archive zip whose covered years intersect ``years``
    is downloaded (zips spanning multiple years are fetched whole).

    Args:
        years: Years to fetch. ``None`` downloads the full 1993-2007 archive.
        output_dir: Destination. Defaults to ``FHFA_RAW_DIR / 'hud_era'``.
        overwrite: If True, re-download existing files.
        pause: Seconds to pause between downloads.
        timeout: Request timeout in seconds.
        retries: Number of retry attempts.
        show_progress: If True, show download progress bars.

    Returns:
        List of DownloadResult objects for each requested file.
    """
    download_dir = output_dir or (FHFAConfig.FHFA_RAW_DIR / "hud_era")
    download_dir.mkdir(parents=True, exist_ok=True)
    pause = pause if pause is not None else MortgageDataConfig.DOWNLOAD_PAUSE

    requested = set(years) if years is not None else None
    downloads: list[DownloadTask] = []
    for filename, file_years in HUD_GSE_ARCHIVE_FILES.items():
        if requested is not None and not requested.intersection(file_years):
            continue
        downloads.append(
            DownloadTask(
                url=f"{HUD_GSE_ARCHIVE_BASE}/{filename}",
                destination=download_dir / filename,
            )
        )

    if not downloads:
        logger.warning("No HUD-era archive files match the requested years")
        return []

    logger.info("Downloading %d HUD-era GSE archive zip(s) to %s",
                len(downloads), download_dir)
    mode: Literal["skip", "overwrite"] = "overwrite" if overwrite else "skip"
    results = batch_download(
        downloads,
        mode=mode,
        pause_seconds=pause,
        timeout=timeout,
        retries=retries,
        show_progress=show_progress,
    )

    summary = summarize_results(results)
    logger.info(
        "HUD-era download complete: %d downloaded, %d skipped, %d failed",
        summary.get("success", 0),
        summary.get("skipped_exists", 0),
        summary.get("failed", 0),
    )
    return results


if __name__ == "__main__":
    from mortgage_data_manager.core.logging import configure_logging

    configure_logging(level="INFO")
    download_fhfa_data()
