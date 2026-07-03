"""Download utilities for FHLB AMA data files.

This module provides functions to download FHLB (Federal Home Loan Bank)
Acquired Member Assets (AMA) data files from the FHFA public use database.

Example:
    >>> from mortgage_data_manager.fhlb.download import download_ama_data
    >>> results = download_ama_data()
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
from mortgage_data_manager.fhlb.config import FHLBConfig

logger = get_logger(__name__)

# FHFA Public Use Database URL (source for both FHFA and FHLB data)
FHFA_PUDB_URL = "https://www.fhfa.gov/data/pudb"

# FHFA FHLB Membership Data URL
FHFA_FHLB_MEMBERSHIP_URL = "https://www.fhfa.gov/data/fhlb-membership"


def discover_files_from_page(
    base_url: str,
    allowed_extensions: tuple[str, ...] = (".csv",),
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
    allowed_extensions: tuple[str, ...] = (".csv",),
    base_url: str = FHFA_PUDB_URL,
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


def download_ama_data(
    output_dir: Path | None = None,
    *,
    overwrite: bool = False,
    pause: float | None = None,
    timeout: int | None = None,
    retries: int | None = None,
    show_progress: bool = True,
) -> list[DownloadResult]:
    """Download FHLB AMA data CSV files.

    Downloads AMA (Acquired Member Assets) CSV files from the FHFA
    public use database website.

    Args:
        output_dir: Output directory for downloaded files.
            Defaults to FHLBConfig.FHLB_RAW_AMA_DIR
        overwrite: If True, overwrite existing files
        pause: Seconds to pause between downloads
        timeout: Request timeout in seconds
        retries: Number of retry attempts
        show_progress: If True, show download progress bars

    Returns:
        List of DownloadResult objects for each file
    """
    download_dir = output_dir or FHLBConfig.FHLB_RAW_AMA_DIR
    logger.info(f"Downloading FHLB AMA data to {download_dir}")

    return download_from_page(
        download_dir=download_dir,
        allowed_extensions=(".csv",),
        included_substrings=("-pudb", "pudb_export"),
        overwrite=overwrite,
        pause=pause,
        timeout=timeout,
        retries=retries,
        show_progress=show_progress,
    )


def download_ama_dictionaries(
    output_dir: Path | None = None,
    *,
    overwrite: bool = False,
    pause: float | None = None,
    timeout: int | None = None,
    retries: int | None = None,
    show_progress: bool = True,
) -> list[DownloadResult]:
    """Download FHLB AMA data dictionary files.

    Downloads PDF and Excel dictionary files that describe the
    AMA data schema.

    Args:
        output_dir: Output directory for downloaded files.
            Defaults to FHLBConfig.FHLB_DATA_DIR / "dictionaries"
        overwrite: If True, overwrite existing files
        pause: Seconds to pause between downloads
        timeout: Request timeout in seconds
        retries: Number of retry attempts
        show_progress: If True, show download progress bars

    Returns:
        List of DownloadResult objects for each file
    """
    download_dir = output_dir or (FHLBConfig.FHLB_DATA_DIR / "dictionaries")
    logger.info(f"Downloading FHLB dictionaries to {download_dir}")

    return download_from_page(
        download_dir=download_dir,
        allowed_extensions=(".pdf", ".xls", ".xlsx"),
        included_substrings=("pudb-definitions", "pudb_definitions"),
        overwrite=overwrite,
        pause=pause,
        timeout=timeout,
        retries=retries,
        show_progress=show_progress,
    )


def download_members_data(
    output_dir: Path | None = None,
    *,
    overwrite: bool = False,
    pause: float | None = None,
    timeout: int | None = None,
    retries: int | None = None,
    show_progress: bool = True,
) -> list[DownloadResult]:
    """Download FHLB membership data Excel files.

    Downloads quarterly FHLB member institution Excel files from the FHFA
    FHLB membership data page. Files follow pattern: FHLB_Members_Q{quarter}{year}.xls(x)

    Args:
        output_dir: Output directory for downloaded files.
            Defaults to FHLBConfig.FHLB_RAW_MEMBERS_DIR
        overwrite: If True, overwrite existing files
        pause: Seconds to pause between downloads
        timeout: Request timeout in seconds
        retries: Number of retry attempts
        show_progress: If True, show download progress bars

    Returns:
        List of DownloadResult objects for each file
    """
    download_dir = output_dir or FHLBConfig.FHLB_RAW_MEMBERS_DIR
    logger.info(f"Downloading FHLB membership data to {download_dir}")

    return download_from_page(
        download_dir=download_dir,
        base_url=FHFA_FHLB_MEMBERSHIP_URL,
        allowed_extensions=(".xls", ".xlsx"),
        included_substrings=("fhlb_members", "fhlb-members"),
        overwrite=overwrite,
        pause=pause,
        timeout=timeout,
        retries=retries,
        show_progress=show_progress,
    )
