"""Core download functionality for FHA snapshot datasets.

This module provides functions for downloading FHA data files from the HUD website.
It handles both Single Family and HECM snapshot data, with automatic filename
standardization and ZIP file processing.

Example:
    >>> from mortgage_data_manager.fha.download import download_fha_files
    >>> results = download_fha_files(
    ...     "https://www.hud.gov/stat/sfh/fha-sf-portfolio-snapshot",
    ...     Path("data/raw/single_family"),
    ...     file_type="sf",
    ... )
    >>> print(f"Downloaded {len([r for r in results if r.status.value == 'success'])} files")
"""

from __future__ import annotations

import re
import tempfile
import zipfile
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.download import (
    DownloadResult,
    DownloadStatus,
    DownloadTask,
    batch_download,
    retry_request,
    summarize_results,
)
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.fha.config import FHAConfig

logger = get_logger(__name__)

# Type aliases
PathLike = Path | str
ExcelExtensions = tuple[str, ...]

# Default extensions for FHA files
EXCEL_EXTENSIONS: ExcelExtensions = (".xlsx", ".xls", ".xlsm", ".xlsb")
ZIP_EXTENSIONS: ExcelExtensions = (".zip",)


def find_years_in_string(text: str) -> int:
    """Return the four-digit year encoded in ``text``.

    The helper looks for four-digit year patterns first and then for legacy
    two-digit month/year combinations (e.g. ``0113`` for January 2013).

    Args:
        text: The string to search within.

    Returns:
        The four-digit year extracted from ``text``.

    Raises:
        TypeError: If ``text`` is not a string.
        ValueError: If no year-like pattern can be found.
    """
    if not isinstance(text, str):
        raise TypeError("Expected text to be a string when extracting years.")

    # First try to find 4-digit years
    found_years = re.findall(r"(20\d{2})", text)
    if found_years:
        if len(found_years) > 1:
            logger.warning(
                "Multiple candidate years found in %r (%s); using the first match.",
                text,
                found_years,
            )
        return int(found_years[0])

    # If no 4-digit year found, look for 2-digit pattern (e.g., '0113' for Jan 2013)
    two_digit_pattern = re.findall(r"(0[1-9]|1[0-2])(\d{2})", text)
    if two_digit_pattern:
        month, year = two_digit_pattern[0]
        full_year = 2000 + int(year)
        return full_year

    raise ValueError(f"No valid year pattern found in: {text}")


def find_month_in_string(text: str) -> int | None:
    """Return the month number referenced in ``text`` if one is present.

    The search recognises common three-letter month abbreviations and a handful of
    variants used in the FHA downloads (e.g. ``"JLY"``).

    Args:
        text: The string to inspect.

    Returns:
        The numeric month (``1``-``12``) when a match is found, otherwise ``None``.
    """
    month_abbreviations = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "jly": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }

    text = text.lower()
    for month_abbreviation, month_number in month_abbreviations.items():
        if month_abbreviation in text:
            return month_number
    return None


def standardize_filename(original_filename: str | Path, file_type: str | None) -> str:
    """Convert FHA snapshot filenames into a standard ``YYYYMMDD`` form.

    Handles multiple filename patterns:

    * Modern format: ``FHA_SFSnapshot_Aug2023.xlsx``
    * Legacy format: ``fha_0113.zip`` (where ``01`` is month and ``13`` is year)

    Args:
        original_filename: The original filename, with or without a path component.
        file_type: Indicates which naming convention to apply (``"sf"`` or ``"hecm"``).
            If ``None`` the original filename is returned.

    Returns:
        A standardised filename matching the project's naming conventions. If the
        filename cannot be parsed the original ``original_filename`` is returned.

    Raises:
        ValueError: If ``file_type`` is not recognised.
    """
    base_name = Path(original_filename).name

    if file_type is None:
        return base_name

    extension = Path(base_name).suffix

    try:
        # First try to find month using month name abbreviations
        month = find_month_in_string(base_name)

        # If no month name found, try to extract from numeric pattern
        if not month:
            month_year_pattern = re.findall(r"(0[1-9]|1[0-2])(\d{2})", base_name)
            if month_year_pattern:
                month = int(month_year_pattern[0][0])

        if not month:
            raise ValueError(f"Could not extract month from filename: {base_name}")

        # Get year using find_years_in_string function
        year = find_years_in_string(base_name)

        if not year:
            raise ValueError(f"Could not extract year from filename: {base_name}")

        # Create standardized date string (YYYYMMDD)
        date_str = f"{year}{str(month).zfill(2)}01"

        if file_type == "sf":
            new_filename = f"fha_sf_snapshot_{date_str}{extension}"
        elif file_type == "hecm":
            new_filename = f"fha_hecm_snapshot_{date_str}{extension}"
        else:
            raise ValueError(f"Invalid file type: {file_type}")

        return new_filename

    except Exception as e:
        logger.error("Error standardizing filename %s: %s", base_name, e)
        return base_name


def process_zip_file(
    zip_path: PathLike,
    destination_folder: PathLike,
    file_type: str | None,
) -> list[Path]:
    """Extract files from ``zip_path`` into ``destination_folder``.

    Any spreadsheets discovered inside the archive are renamed using
    :func:`standardize_filename` so long as ``file_type`` is provided.

    Args:
        zip_path: Path to the zip file to extract.
        destination_folder: Folder where processed files should be saved.
        file_type: Snapshot type used to standardise filenames (``"sf"`` or
            ``"hecm"``). When ``None`` filenames are preserved as extracted.

    Returns:
        A list containing the paths to extracted spreadsheet files located in
        ``destination_folder``.
    """
    try:
        zip_path = Path(zip_path)
        destination_path = Path(destination_folder)
        destination_path.mkdir(parents=True, exist_ok=True)
        zip_filename = zip_path.name

        try:
            zip_year = find_years_in_string(zip_filename)
            zip_month = find_month_in_string(zip_filename)
            has_zip_date = zip_year is not None and zip_month is not None
        except ValueError:
            has_zip_date = False

        extracted_files: list[Path] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(temp_dir)

                for source_path in temp_dir_path.rglob("*"):
                    if not source_path.is_file() or source_path.suffix.lower() not in EXCEL_EXTENSIONS:
                        continue

                    try:
                        new_filename = standardize_filename(source_path.name, file_type)
                    except ValueError:
                        if has_zip_date and file_type is not None:
                            date_str = f"{zip_year}{str(zip_month).zfill(2)}01"
                            extension = source_path.suffix
                            if file_type == "sf":
                                new_filename = f"fha_sf_snapshot_{date_str}{extension}"
                            elif file_type == "hecm":
                                new_filename = f"fha_hecm_snapshot_{date_str}{extension}"
                            else:
                                new_filename = source_path.name
                            logger.info("Using zip file date for %s: %s", source_path.name, new_filename)
                        else:
                            new_filename = source_path.name
                            logger.warning(
                                "No date information found for %s, keeping original name",
                                source_path.name,
                            )

                    dest_path = destination_path / new_filename

                    if not dest_path.exists():
                        logger.info("Processing extracted file: %s -> %s", source_path.name, new_filename)
                        dest_path.write_bytes(source_path.read_bytes())
                    else:
                        logger.info("Skipping existing file: %s", new_filename)

                    extracted_files.append(dest_path)

        return extracted_files

    except zipfile.BadZipFile as e:
        logger.error("Error processing zip file %s: %s", zip_path, e)
    except Exception as e:
        logger.error("Unexpected error processing zip file %s: %s", zip_path, e)

    return []


def discover_fha_files(
    page_url: str,
    include_zip: bool = True,
    timeout: int | None = None,
    retries: int | None = None,
) -> list[tuple[str, str]]:
    """Discover downloadable files from an FHA/HUD page.

    Scrapes the page to find links to Excel and optionally ZIP files.

    Args:
        page_url: URL of the page to scrape
        include_zip: Whether to include ZIP files in addition to Excel files
        timeout: Request timeout in seconds
        retries: Number of retry attempts

    Returns:
        List of (url, filename) tuples for each discovered file
    """
    timeout = timeout if timeout is not None else MortgageDataConfig.DOWNLOAD_TIMEOUT
    retries = retries if retries is not None else MortgageDataConfig.DOWNLOAD_RETRIES

    logger.info(f"Fetching FHA page: {page_url}")

    headers = {"User-Agent": MortgageDataConfig.USER_AGENT}
    response = retry_request(page_url, timeout=timeout, retries=retries, headers=headers)

    soup = BeautifulSoup(response.content, "html.parser")

    # Build list of allowed extensions
    allowed_extensions = EXCEL_EXTENSIONS
    if include_zip:
        allowed_extensions = allowed_extensions + ZIP_EXTENSIONS

    download_links: list[tuple[str, str]] = []

    for link_tag in soup.find_all("a", href=True):
        href = link_tag["href"]

        if not href.lower().endswith(allowed_extensions):
            continue

        # Construct full URL
        file_url = urljoin(page_url, href)

        # Extract filename
        try:
            file_name = Path(urlparse(file_url).path).name
            if not file_name:
                continue
        except Exception:
            continue

        download_links.append((file_url, file_name))

    logger.info(f"Found {len(download_links)} files matching criteria")
    return download_links


def download_fha_files(
    page_url: str,
    destination_folder: PathLike,
    file_type: str | None = None,
    *,
    include_zip: bool = True,
    overwrite: bool = False,
    pause: float | None = None,
    timeout: int | None = None,
    retries: int | None = None,
    show_progress: bool = True,
    process_zips: bool = True,
) -> list[DownloadResult]:
    """Download FHA files from a HUD page.

    Discovers and downloads Excel/ZIP files from the specified URL,
    with optional filename standardization and ZIP extraction.

    Args:
        page_url: URL of the page to scrape for download links
        destination_folder: Directory to save downloaded files
        file_type: Type for filename standardization ("sf" or "hecm"), or None to keep original names
        include_zip: Whether to download ZIP archives
        overwrite: If True, overwrite existing files
        pause: Seconds to pause between downloads
        timeout: Request timeout in seconds
        retries: Number of retry attempts
        show_progress: If True, show download progress bars
        process_zips: If True, extract and process ZIP files after download

    Returns:
        List of DownloadResult objects for each file
    """
    dest_path = Path(destination_folder)
    dest_path.mkdir(parents=True, exist_ok=True)

    pause = pause if pause is not None else MortgageDataConfig.DOWNLOAD_PAUSE

    # Discover files
    download_links = discover_fha_files(
        page_url=page_url,
        include_zip=include_zip,
        timeout=timeout,
        retries=retries,
    )

    if not download_links:
        logger.warning("No files found to download")
        return []

    # Build download tasks with standardized filenames
    headers = {"User-Agent": MortgageDataConfig.USER_AGENT}
    downloads: list[DownloadTask] = []
    filename_map: dict[Path, str] = {}  # Map destination to original filename for ZIP processing

    for url, original_filename in download_links:
        standardized_name = standardize_filename(original_filename, file_type)
        destination = dest_path / standardized_name
        downloads.append(DownloadTask(url=url, destination=destination, headers=headers))
        filename_map[destination] = original_filename

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

    # Process ZIP files if requested
    if process_zips:
        for result in results:
            if result.status == DownloadStatus.SUCCESS and result.path.suffix.lower() == ".zip":
                logger.info(f"Processing zip file: {result.path.name}")
                process_zip_file(result.path, dest_path, file_type)

    # Log summary
    summary = summarize_results(results)
    logger.info(
        f"FHA download complete: {summary.get('success', 0)} downloaded, "
        f"{summary.get('skipped_exists', 0)} skipped, "
        f"{summary.get('failed', 0)} failed"
    )

    return results


SINGLE_FAMILY_SNAPSHOT_URL = "https://www.hud.gov/stat/sfh/fha-sf-portfolio-snapshot"
HECM_SNAPSHOT_URL = "https://www.hud.gov/hud-partners/hecmsf-snapshot"

DEFAULT_SINGLE_FAMILY_DESTINATION = FHAConfig.FHA_RAW_DIR / "single_family"
DEFAULT_HECM_DESTINATION = FHAConfig.FHA_RAW_DIR / "hecm"


def download_single_family_snapshots(
    destination: Path | str = DEFAULT_SINGLE_FAMILY_DESTINATION,
    *,
    pause: float | None = None,
    include_zip: bool = True,
    overwrite: bool = False,
    timeout: int | None = None,
    retries: int | None = None,
    show_progress: bool = True,
    url: str = SINGLE_FAMILY_SNAPSHOT_URL,
) -> list[DownloadResult]:
    """Download the latest Single Family snapshot files.

    Args:
        destination: Directory to save downloaded files
        pause: Seconds to pause between downloads
        include_zip: Whether to download ZIP archives
        overwrite: If True, overwrite existing files
        timeout: Request timeout in seconds
        retries: Number of retry attempts
        show_progress: If True, show download progress bars
        url: URL of the page to scrape

    Returns:
        List of DownloadResult objects for each file
    """
    return download_fha_files(
        page_url=url,
        destination_folder=destination,
        file_type="sf",
        include_zip=include_zip,
        overwrite=overwrite,
        pause=pause,
        timeout=timeout,
        retries=retries,
        show_progress=show_progress,
    )


def download_hecm_snapshots(
    destination: Path | str = DEFAULT_HECM_DESTINATION,
    *,
    pause: float | None = None,
    include_zip: bool = True,
    overwrite: bool = False,
    timeout: int | None = None,
    retries: int | None = None,
    show_progress: bool = True,
    url: str = HECM_SNAPSHOT_URL,
) -> list[DownloadResult]:
    """Download the latest HECM snapshot files.

    Args:
        destination: Directory to save downloaded files
        pause: Seconds to pause between downloads
        include_zip: Whether to download ZIP archives
        overwrite: If True, overwrite existing files
        timeout: Request timeout in seconds
        retries: Number of retry attempts
        show_progress: If True, show download progress bars
        url: URL of the page to scrape

    Returns:
        List of DownloadResult objects for each file
    """
    return download_fha_files(
        page_url=url,
        destination_folder=destination,
        file_type="hecm",
        include_zip=include_zip,
        overwrite=overwrite,
        pause=pause,
        timeout=timeout,
        retries=retries,
        show_progress=show_progress,
    )
