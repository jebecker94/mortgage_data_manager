"""HMDA Data Download Functions.

This module provides functions for downloading HMDA data files from the CFPB website.
It handles both current and historical HMDA data releases, automatically organizing
files into appropriate subdirectories based on file type.

Key Features:
- Automated web scraping with Selenium (handles JavaScript-rendered pages)
- Smart file organization by type (loans, panel, transmittal_series, etc.)
- Multiple download modes (skip existing, always overwrite, conditional updates)
- Error handling and logging
- Support for different HMDA file formats and years

Main Functions:
- download_zip_files_from_url: Download all ZIP files from a CFPB data page
- determine_raw_subfolder: Route files to appropriate subdirectories

Dependencies:
- Selenium WebDriver (for JavaScript-rendered pages)
- Beautiful Soup (for HTML parsing)
- Requests (for file downloads)
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.download import DownloadStatus, atomic_download
from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)


def determine_raw_subfolder(file_name: str) -> str:
    """Route downloaded ZIP files into subfolders under data/raw based on filename.

    This function analyzes HMDA file names to determine the appropriate subdirectory
    for organization. Files are categorized into: loans, panel, transmittal_series,
    msamd (geography), or misc.

    Args:
        file_name: Name of the HMDA file (with or without extension)

    Returns:
        Subfolder name: 'loans', 'msamd', 'panel', 'transmittal_series', or 'misc'

    Examples:
        >>> determine_raw_subfolder("2023_public_lar_csv.zip")
        'loans'
        >>> determine_raw_subfolder("2023_public_panel_csv.zip")
        'panel'
        >>> determine_raw_subfolder("2023_public_ts_csv.zip")
        'transmittal_series'
    """
    name = file_name.lower()

    # MSAMD geography files
    if "msamd" in name:
        return "msamd"

    # Transmittal Series (TS) files
    if (
        ("public_ts" in name)
        or ("transmiss" in name)
        or re.search(r"(^|[_-])ts([_-]|$)", name)
    ):
        return "transmittal_series"

    # Panel files
    if ("public_panel" in name) or re.search(r"(^|[_-])panel([_-]|$)", name):
        return "panel"

    # Loan files (LAR/MLAR/nationwide loan-level)
    if (
        ("mlar" in name)
        or ("public_lar" in name)
        or re.search(r"(^|[_-])lar([_-]|$)", name)
        or ("nationwide" in name)
    ):
        return "loans"

    # Other miscellaneous assets
    if ("arid2017" in name) or ("avery" in name):
        return "misc"

    # Default catch-all
    return "misc"


def download_zip_files_from_url(
    page_url: str,
    destination_folder: str,
    pause: float | None = None,
    wait_time: int = 10,
    download_csvs: bool = True,
    download_pipes: bool = False,
    download_all: bool = False,
    overwrite_mode: str = "skip",
) -> None:
    """Find all ZIP links on a webpage and download them to a folder.

    Finds all ZIP links on a webpage (after JavaScript rendering) and
    downloads them to a specified folder.

    This function uses Selenium WebDriver to handle JavaScript-rendered CFPB pages
    that don't work with simple HTTP requests. Files are automatically organized
    into subdirectories based on their type.

    Args:
        page_url: URL to scrape for ZIP links
        destination_folder: Base folder where ZIP files will be downloaded (subdirectories created automatically)
        pause: Seconds to pause between downloads. Defaults to
            MortgageDataConfig.DOWNLOAD_PAUSE (5.0).
        wait_time: Seconds to wait for JavaScript to load page content. Default is 10.
        download_csvs: Whether to download CSV format files. Default is True.
        download_pipes: Whether to download pipe-delimited format files. Default is False.
        download_all: Whether to download all ZIP files regardless of format. Default is False.
        overwrite_mode: Behavior when destination file exists. Options:
            - 'skip' (default): do not re-download existing files
            - 'always': always re-download and overwrite existing files
            - 'if_newer': re-download if server Last-Modified is newer than local file
            - 'if_size_diff': re-download if server Content-Length differs from local file size

    Returns:
        Files are downloaded to destination_folder with automatic subdirectory organization

    Examples:
        >>> # Download 2023 snapshot data
        >>> download_zip_files_from_url(
        ...     "https://ffiec.cfpb.gov/data-publication/snapshot-national-loan-level-dataset/2023",
        ...     "./data/raw"
        ... )

        >>> # Download with overwrite if files are newer on server
        >>> download_zip_files_from_url(
        ...     "https://ffiec.cfpb.gov/data-publication/one-year-national-loan-level-dataset/2024",
        ...     "./data/raw",
        ...     overwrite_mode="if_newer"
        ... )

    Note:
        Requires Chrome/Chromium to be installed for Selenium WebDriver.
        Uses webdriver-manager to automatically download and manage ChromeDriver.
    """
    if pause is None:
        pause = MortgageDataConfig.DOWNLOAD_PAUSE

    try:
        # Ensure the destination folder exists
        dest_path = Path(destination_folder)
        dest_path.mkdir(parents=True, exist_ok=True)

        # Set up the Selenium WebDriver
        chrome_options = Options()
        chrome_options.add_argument("--headless")  # Run Chrome in headless mode
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")

        # Use webdriver-manager to download and manage the ChromeDriver
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)

        logger.info("Fetching content from URL with Selenium: %s", page_url)
        driver.get(page_url)

        # Wait for JavaScript to load content
        logger.info("Waiting %s seconds for JavaScript to load...", wait_time)
        time.sleep(wait_time)

        page_source = driver.page_source
        driver.quit()

        # Parse the HTML page
        soup = BeautifulSoup(page_source, "html.parser")

        # Find all <a> tags with an href attribute
        zip_links_found = 0
        for link_tag in soup.find_all("a", href=True):
            href = str(link_tag["href"])  # BS4 attrs are str | list; href is always str

            # Check if the link points to a ZIP file and matches download criteria
            zip_extensions = (".zip",)
            if href.lower().endswith(zip_extensions) & (
                (("csv" in href.lower()) & download_csvs)
                | (("pipe" in href.lower()) & download_pipes)
                | download_all
            ):
                zip_links_found += 1

                # Construct the full URL (handles relative links)
                file_url = urljoin(page_url, href)

                # Extract a clean filename from the URL
                try:
                    file_name = Path(urlparse(file_url).path).name
                    if not file_name:  # Handle cases where the path ends with /
                        file_name = (
                            f"downloaded_zip_{zip_links_found}{Path(file_url).suffix}"
                        )
                except Exception as e:
                    logger.warning(
                        "Could not derive filename from URL %s: %s. Using a generic name.",
                        file_url, e
                    )
                    file_name = (
                        f"zip_file_{zip_links_found}{Path(href).suffix or '.zip'}"
                    )

                # Determine destination subfolder based on the file name
                subfolder = determine_raw_subfolder(file_name)
                subfolder_path = dest_path / subfolder
                subfolder_path.mkdir(parents=True, exist_ok=True)

                # Download new files or overwrite existing ones based on mode
                file_path = subfolder_path / file_name
                need_download = not file_path.exists()

                if not need_download and overwrite_mode.lower() in ["always"]:
                    need_download = True

                if not need_download and overwrite_mode.lower() in [
                    "if_newer",
                    "if_size_diff",
                ]:
                    try:
                        headers = {
                            "User-Agent": MortgageDataConfig.USER_AGENT
                        }
                        head_resp = requests.head(
                            file_url, headers=headers, allow_redirects=True, timeout=30
                        )
                        head_resp.raise_for_status()

                        if overwrite_mode.lower() == "if_newer":
                            last_mod = head_resp.headers.get("Last-Modified")
                            if last_mod is not None:
                                try:
                                    remote_dt = parsedate_to_datetime(last_mod)
                                    local_dt = datetime.utcfromtimestamp(
                                        file_path.stat().st_mtime
                                    )
                                    if remote_dt.tzinfo is None:
                                        remote_dt = remote_dt.replace(tzinfo=None)
                                    # Compare in UTC naive format
                                    if remote_dt > local_dt:
                                        need_download = True
                                except Exception as e:
                                    logger.warning(
                                        "Could not parse Last-Modified for %s: %s",
                                        file_name, e
                                    )
                        if (not need_download) and (
                            overwrite_mode.lower() == "if_size_diff"
                        ):
                            cl = head_resp.headers.get("Content-Length")
                            if cl is not None:
                                try:
                                    remote_size = int(cl)
                                    local_size = file_path.stat().st_size
                                    if remote_size != local_size:
                                        need_download = True
                                except Exception as e:
                                    logger.warning(
                                        "Size compare failed for %s: %s", file_name, e
                                    )
                    except requests.exceptions.RequestException as e:
                        logger.warning(
                            "HEAD request failed for %s: %s. Proceeding per overwrite_mode='%s'.",
                            file_url, e, overwrite_mode
                        )

                if need_download:
                    logger.info("Downloading %s to %s...", file_url, file_path)
                    # Selenium above is only for link discovery; the actual fetch
                    # goes through core.download for atomic .tmp+rename, config-
                    # driven retries/timeout, and a uniform result (never raises).
                    headers = {"User-Agent": MortgageDataConfig.USER_AGENT}
                    result = atomic_download(
                        file_url, file_path, headers=headers, show_progress=False
                    )
                    if result.status == DownloadStatus.SUCCESS:
                        logger.info("Successfully downloaded %s", file_name)
                    else:
                        logger.error(
                            "Error downloading %s: %s", file_url, result.error_message
                        )

                    # Pause between downloads
                    time.sleep(pause)
                else:
                    logger.info(
                        "File %s already exists. Skipping download (overwrite_mode='%s').",
                        file_path, overwrite_mode
                    )

        if zip_links_found == 0:
            logger.info("No ZIP links found on the page.")

    except Exception as e:  # Catch WebDriver and other general errors
        logger.error("An unexpected error occurred: %s", e)


def download_hmda_files(
    years: range,
    destination_folder: str = "./data/raw",
    include_mlar: bool = False,
    include_historical: bool = False,
    **kwargs: Any,
) -> None:
    """Convenience function to download HMDA files for multiple years and file types.

    This function downloads from the standard CFPB data publication URLs for
    snapshot, one-year, and three-year datasets. Optionally includes MLAR
    (Modified LAR) and historical (2007-2017) files.

    Args:
        years: Range of years to download (e.g., range(2020, 2025))
        destination_folder: Base folder for downloads. Default is "./data/raw"
        include_mlar: Whether to download Modified LAR files. Default is False.
        include_historical: Whether to download historical files (2007-2017). Default is False.
        **kwargs: Additional arguments passed to download_zip_files_from_url

    Examples:
        >>> # Download recent years
        >>> download_hmda_files(range(2020, 2025))

        >>> # Download with MLAR files
        >>> download_hmda_files(range(2022, 2025), include_mlar=True)
    """
    # Base URLs for different HMDA datasets
    snapshot_base_url = "https://ffiec.cfpb.gov/data-publication/snapshot-national-loan-level-dataset"
    one_year_base_url = "https://ffiec.cfpb.gov/data-publication/one-year-national-loan-level-dataset"
    three_year_base_url = "https://ffiec.cfpb.gov/data-publication/three-year-national-loan-level-dataset"
    mlar_base_url = "https://ffiec.cfpb.gov/data-publication/modified-lar"
    historical_url = "https://www.consumerfinance.gov/data-research/hmda/historic-data/?geo=nationwide&records=all-records&field_descriptions=codes"

    # Download standard files for each year
    for year in years:
        for base_url in [snapshot_base_url, one_year_base_url, three_year_base_url]:
            target_url = f"{base_url}/{year}"
            download_zip_files_from_url(target_url, destination_folder, **kwargs)

        # Download MLAR files if requested
        if include_mlar:
            target_url = f"{mlar_base_url}/{year}"
            download_zip_files_from_url(
                target_url, destination_folder, download_all=True, **kwargs
            )

    # Download historical files if requested
    if include_historical:
        download_zip_files_from_url(
            historical_url, destination_folder, download_all=True, **kwargs
        )
