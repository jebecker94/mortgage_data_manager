#!/usr/bin/env python3
"""GNMA Data Manager - Download Module.

Functions for downloading GNMA data and schema files from the web.

This module handles:
    - Session setup with GNMA cookie-based authentication
    - Web scraping to discover available data and schema files
    - Downloading data files (ZIP archives) to the raw layer
    - Downloading schema files (PDFs) for data documentation

Public API:
    - create_session: Create authenticated session with GNMA cookies
    - find_data_links: Scrape GNMA page for data file links
    - find_schema_links: Scrape GNMA page for schema/PDF links
    - download_data_file: Download a single data file
    - download_prefix_data: Download all data files for a prefix
    - download_all_data: Download data for multiple prefixes
    - download_schema_file: Download a single schema/PDF file
    - download_prefix_schemas: Download all schema files for a prefix
    - download_all_schemas: Download schemas for multiple prefixes

"""

from __future__ import annotations

import datetime
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from mortgage_data_manager.core.download import DownloadStatus, atomic_download
from mortgage_data_manager.core.logging import get_logger

from .config import DownloadConfig, GNMAConfig

logger = get_logger(__name__)

__all__ = [
    "create_session",
    "download_data_file",
    "download_prefix_data",
    "download_all_data",
    "download_schema_file",
    "download_prefix_schemas",
    "download_all_schemas",
    "find_data_links",
    "find_schema_links",
    "find_layout_links",
    "download_layout_schemas",
    "scrape_bulk_page",
    "download_bulk_data",
]


# =============================================================================
# Session / Authentication
# =============================================================================


def create_session(config: DownloadConfig) -> requests.Session:
    """Create and configure a requests session with authentication.

    Sets up a session with GNMA-specific authentication cookies and
    a retry strategy for handling transient failures.

    Args:
        config: DownloadConfig with authentication details

    Returns:
        Configured requests.Session instance
    """
    # Create cookie value
    cookie_value = f"e={config.email_value}&i={config.id_value}"

    # Calculate expiration
    expiration_datetime = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
        days=config.cookie_expiry_days
    )
    expiration_timestamp = expiration_datetime.timestamp()

    # Create cookie jar
    cookie_jar = requests.cookies.RequestsCookieJar()
    cookie_jar.set(
        name=config.cookie_name,
        value=cookie_value,
        domain=config.cookie_domain,
        path=config.cookie_path,
        expires=expiration_timestamp,
        secure=True,
    )

    # Create session
    session = requests.Session()
    session.cookies = cookie_jar
    session.headers.update({"user-agent": config.user_agent})

    # Configure retries
    retry_strategy = Retry(
        total=config.retry_total,
        backoff_factor=config.retry_backoff,
        status_forcelist=config.retry_statuses,
        allowed_methods=config.retry_allowed_methods,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


# =============================================================================
# Discovery Functions
# =============================================================================


def find_data_links(prefix: str, session: requests.Session, config: DownloadConfig) -> list[dict]:
    """Find all data file links for a prefix on the GNMA disclosure page.

    Scrapes the GNMA bulk download page to discover available data files
    for a given prefix. Filters out PDF links (those are schemas).

    Args:
        prefix: File prefix to search for
        session: Authenticated requests session
        config: Download configuration

    Returns:
        List of dictionaries with link information (href, text, prefix)
    """
    url = f"{config.schema_base_url}?prefix={prefix}"

    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Get all links that include the prefix
        # These are typically ZIP files with data
        data_links = soup.find_all("a", href=lambda href: href and prefix in href)

        time.sleep(config.request_delay)

        links_info = []
        for link in data_links:
            href = link.get("href", "")
            text = link.get_text(strip=True)

            # Filter out PDF links (those are schemas)
            if not href.lower().endswith(".pdf"):
                links_info.append({"href": href, "text": text, "prefix": prefix})

        logger.info(f"Found {len(links_info)} data links for {prefix}")
        return links_info

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to access data page for {prefix}: {e}")
        return []


def find_schema_links(
    prefix: str,
    session: requests.Session,
    config: DownloadConfig,
    bad_text_filters: list | None = None,
) -> list[dict]:
    """Find all schema/PDF links for a prefix on the GNMA disclosure page.

    Scrapes the GNMA page to discover available schema PDF files for a
    given prefix. Filters out files matching bad_text_filters.

    Args:
        prefix: File prefix to search for
        session: Authenticated requests session
        config: Download configuration
        bad_text_filters: Optional list of text patterns to filter out

    Returns:
        List of dictionaries with link information (href, text, prefix)
    """
    if bad_text_filters is None:
        bad_text_filters = config.bad_text_filters or []

    # Set default filters if none provided
    if not bad_text_filters:
        bad_text_filters = ["Supplemental Loan Level Forbearance File"]

    url = f"{config.schema_base_url}?prefix={prefix}"

    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Find all PDF links
        pdf_links = soup.find_all("a", href=lambda href: href and href.lower().endswith(".pdf"))

        time.sleep(config.request_delay)

        links_info = []
        for link in pdf_links:
            link_text = link.get_text(strip=True)

            # Check if link text contains any bad text filters
            if any(bad_text in link_text for bad_text in bad_text_filters):
                logger.debug(f"Skipping filtered file: {link_text}")
                continue

            href = link["href"]
            # Construct full URL if relative
            if not href.startswith("http"):
                href = "https://www.ginniemae.gov" + href

            links_info.append({"href": href, "text": link_text, "prefix": prefix})

        logger.info(f"Found {len(links_info)} schema links for {prefix}")
        return links_info

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to access schema page for {prefix}: {e}")
        return []


_LAYOUT_HREF_RE = re.compile(r"/Attachments/(\d+)/([A-Za-z0-9_]+)_layout\.pdf", re.IGNORECASE)


def find_layout_links(session: requests.Session, config: DownloadConfig) -> dict[str, dict]:
    """Scrape the Bulk Data Download Layout page for per-prefix layout PDFs.

    Unlike :func:`find_schema_links` (which scrapes ``disclosurehistoryfiles.aspx``
    one prefix at a time), this fetches the master "LayoutsAndSamples" page once and
    returns every ``<prefix>_layout.pdf`` it lists. That page covers prefixes the
    per-prefix scrape omits (platcoll, hnissuesPS/S, issrcutoff, SRF, FRR, ...).

    The SharePoint attachment id in each href is arbitrary and not constructable, so
    the mapping must be scraped rather than synthesized.

    Args:
        session: Requests session (auth not required for this page, but harmless).
        config: Download configuration (provides ``layout_base_url``/``site_base_url``).

    Returns:
        Mapping of ``prefix`` -> ``{"href": absolute_url, "attachment_id": str}``.
        Empty on request failure.
    """
    try:
        response = session.get(config.layout_base_url, timeout=30)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to access layout page: {e}")
        return {}

    soup = BeautifulSoup(response.text, "html.parser")
    time.sleep(config.request_delay)

    layouts: dict[str, dict] = {}
    for link in soup.find_all("a", href=lambda h: h and h.lower().endswith(".pdf")):
        href = link["href"]
        match = _LAYOUT_HREF_RE.search(href)
        if not match:
            continue
        attachment_id, prefix = match.group(1), match.group(2)
        full_href = href if href.startswith("http") else config.site_base_url + href
        # First occurrence wins (page lists each layout once).
        layouts.setdefault(prefix, {"href": full_href, "attachment_id": attachment_id})

    logger.info(f"Found {len(layouts)} layout PDFs on the bulk-layout page")
    return layouts


def download_layout_schemas(
    prefixes: list[str],
    session: requests.Session,
    config: DownloadConfig,
    overwrite: bool = False,
) -> dict[str, tuple[int, int]]:
    """Download ``<prefix>_layout.pdf`` files from the bulk-layout page.

    Scrapes the layout page once (:func:`find_layout_links`), then for each requested
    prefix that has a layout, downloads it to ``raw/<prefix>/<prefix>_layout.pdf`` so
    the existing extract -> combine schema pipeline (which globs ``<prefix>_*.pdf``)
    can consume it. Prefixes with no layout on the page are logged and skipped.

    Args:
        prefixes: Prefixes to fetch layouts for.
        session: Requests session.
        config: Download configuration.
        overwrite: Re-download files that already exist (default: skip them).

    Returns:
        Mapping of ``prefix`` -> ``(successful_downloads, total_attempts)``. A prefix
        with no published layout yields ``(0, 0)``.
    """
    layouts = find_layout_links(session, config)
    results: dict[str, tuple[int, int]] = {}

    for prefix in prefixes:
        info = layouts.get(prefix)
        if info is None:
            logger.warning(f"No layout PDF published for prefix: {prefix}")
            results[prefix] = (0, 0)
            continue

        schema_folder = _get_prefix_schema_folder(prefix, config)
        file_path = schema_folder / f"{prefix}_layout.pdf"
        success = download_schema_file(
            info["href"], file_path, session, config, skip_existing=not overwrite
        )
        results[prefix] = (1 if success else 0, 1)

    total = sum(s for s, _ in results.values())
    logger.info(f"Layout schema download complete: {total}/{len(prefixes)} prefixes fetched")
    return results


# =============================================================================
# Helper Functions
# =============================================================================


def _get_prefix_data_folder(prefix: str, config: DownloadConfig) -> Path:
    """Get the appropriate download folder for a given prefix.

    Args:
        prefix: The data prefix
        config: Download configuration

    Returns:
        Path object for the prefix-specific download folder
    """
    base_path = Path(config.data_download_folder)

    if config.use_prefix_subfolders:
        # Create path: download_folder/prefix
        prefix_path = base_path / prefix
    else:
        # Use the base download folder
        prefix_path = base_path

    # Create the directory if it doesn't exist
    prefix_path.mkdir(parents=True, exist_ok=True)

    return prefix_path


def _get_prefix_schema_folder(prefix: str, config: DownloadConfig) -> Path:
    """Get the appropriate schema download folder for a given prefix.

    Args:
        prefix: The data prefix
        config: Download configuration

    Returns:
        Path object for the prefix-specific schema folder
    """
    base_path = config.schema_download_folder

    if config.use_prefix_subfolders:
        # Create path: schema_download_folder/raw/prefix
        schema_path = base_path / "raw" / prefix
    else:
        # Use the schema download folder with raw subfolder
        schema_path = base_path / "raw"

    # Create the directory if it doesn't exist
    schema_path.mkdir(parents=True, exist_ok=True)

    return schema_path


# =============================================================================
# Data File Downloads
# =============================================================================


def _atomic_fetch(
    url: str, local_path: Path, session: requests.Session, config: DownloadConfig
) -> bool:
    """Fetch one URL to local_path via core.download, carrying session cookies.

    Uses ``atomic_download`` (atomic ``.tmp`` rename, config-driven retries via
    ``retry_request``, never raises) instead of a hand-rolled stream/write loop.
    The authenticated session's cookies (and user-agent) are forwarded as
    request headers since ``atomic_download`` does not take a session.

    Returns:
        True on success, False on failure.
    """
    cookie_header = "; ".join(f"{c.name}={c.value}" for c in session.cookies)
    headers = {"Cookie": cookie_header, "user-agent": config.user_agent}
    result = atomic_download(
        url,
        local_path,
        headers=headers,
        timeout=config.request_timeout_s,
        chunk_size=config.stream_chunk_size,
        show_progress=False,
    )
    if result.status == DownloadStatus.SUCCESS:
        logger.info(f"Downloaded: {local_path} ({result.bytes_downloaded} bytes)")
        time.sleep(config.request_delay)
        return True
    logger.error(f"Failed to download {url}: {result.error_message}")
    return False


def download_data_file(
    url: str,
    local_path: str | Path,
    session: requests.Session,
    config: DownloadConfig,
    skip_existing: bool = True,
) -> bool:
    """Download a single data file.

    Args:
        url: URL to download from
        local_path: Local path to save the file
        session: Authenticated requests session
        config: Download configuration
        skip_existing: Skip download if file already exists

    Returns:
        True if file was downloaded, False if skipped or failed
    """
    local_path = Path(local_path)

    # Check if file exists
    if skip_existing and local_path.exists():
        logger.debug(f"File already exists, skipping: {local_path}")
        return False

    # Create directory if needed
    local_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Downloading: {url}")
    return _atomic_fetch(url, local_path, session, config)


def download_prefix_data(
    prefix: str,
    prefix_dict: dict,
    session: requests.Session,
    config: DownloadConfig,
    start_date: datetime.datetime | None = None,
    end_date: datetime.datetime | None = None,
    overwrite: bool = False,
) -> tuple[int, int]:
    """Download all data files for a specific prefix.

    Iterates through the date range specified in the prefix configuration
    and downloads each available file.

    Args:
        prefix: The data prefix to download
        prefix_dict: Dictionary with prefix configuration
        session: Authenticated requests session
        config: Download configuration
        start_date: Override start date (optional)
        end_date: Override end date (optional)
        overwrite: Re-download files that already exist (default: skip them)

    Returns:
        Tuple of (successful_downloads, total_attempts)
    """
    from dateutil.relativedelta import relativedelta

    from .utils import create_date_suffix, get_date_format

    if prefix not in prefix_dict:
        raise ValueError(f"Unknown prefix: {prefix}")

    prefix_config = prefix_dict[prefix]

    # Get data links from page for validation
    logger.info(f"Discovering available files for {prefix}...")
    data_links = find_data_links(prefix, session, config)

    # Determine date range
    if start_date is None:
        detected_format = get_date_format(prefix_config["min_date"])
        start_date = datetime.datetime.strptime(prefix_config["min_date"], detected_format)

    if end_date is None:
        if prefix_config["max_date"]:
            detected_format = get_date_format(prefix_config["max_date"])
            end_date = datetime.datetime.strptime(prefix_config["max_date"], detected_format)
        else:
            end_date = datetime.datetime.now()

    logger.info(f"Downloading {prefix} data from {start_date.date()} to {end_date.date()}")

    successful_downloads = 0
    total_attempts = 0
    current_date = start_date

    # Step size based on frequency
    step_months = {
        "monthly": 1,
        "quarterly": 3,
        "yearly": 12,
    }.get(prefix_config.get("frequency", "monthly"), 1)

    consecutive_misses = 0
    miss_threshold = config.consecutive_miss_exit_threshold

    # Get prefix folder
    prefix_folder = _get_prefix_data_folder(prefix, config)

    while current_date <= end_date:
        # Create file name
        date_suffix = create_date_suffix(
            current_date,
            prefix_config["date_format"],
            prefix_config["frequency"],
            firstlast="last",
        )

        file_name = f"{prefix}_{date_suffix}.{prefix_config['extension']}"
        file_url = f"{config.base_url}/{file_name}"
        local_path = prefix_folder / file_name

        # Check if file is linked on page
        if config.require_link_on_page:
            data_file = [x for x in data_links if file_name in x.get("href", "")]
            if not data_file:
                logger.debug(f"File {file_name} not found in data links; skipping")
                consecutive_misses += 1
                if miss_threshold and consecutive_misses >= miss_threshold:
                    logger.info(
                        f"Stopping early after {consecutive_misses} consecutive misses for {prefix}"
                    )
                    break
                current_date += relativedelta(months=step_months)
                continue

        # With overwrite, always re-download. Otherwise skip existing canonical
        # files, but still re-fetch ones that came from the bulk page (early
        # access) to replace them with the canonical version.
        skip = not overwrite
        if skip and local_path.exists():
            manifest = prefix_folder / ".bulk_downloads"
            if manifest.exists() and file_name in manifest.read_text():
                skip = False

        total_attempts += 1
        if download_data_file(file_url, local_path, session, config, skip_existing=skip):
            successful_downloads += 1
            consecutive_misses = 0
            # Remove from bulk manifest once overwritten by history version
            manifest = prefix_folder / ".bulk_downloads"
            if manifest.exists():
                lines = manifest.read_text().splitlines()
                lines = [line for line in lines if line != file_name]
                manifest.write_text("\n".join(lines) + "\n" if lines else "")

        # Move to next period
        current_date += relativedelta(months=step_months)

    logger.info(f"Completed {prefix}: {successful_downloads}/{total_attempts} files downloaded")
    return successful_downloads, total_attempts


def download_all_data(
    prefixes: list,
    prefix_dict: dict,
    session: requests.Session,
    config: DownloadConfig,
    start_date: datetime.datetime | None = None,
    end_date: datetime.datetime | None = None,
    overwrite: bool = False,
) -> dict[str, tuple[int, int]]:
    """Download data for all or specified prefixes.

    Args:
        prefixes: List of prefixes to download
        prefix_dict: Dictionary with prefix configurations
        session: Authenticated requests session
        config: Download configuration
        start_date: Override start date for all prefixes (optional)
        end_date: Override end date for all prefixes (optional)
        overwrite: Re-download files that already exist (default: skip them)

    Returns:
        Dictionary mapping prefix to (successful_downloads, total_attempts)
    """
    results = {}
    total_successful = 0
    total_attempts = 0

    logger.info(f"Starting download for {len(prefixes)} prefixes")

    for prefix in prefixes:
        try:
            successful, attempts = download_prefix_data(
                prefix,
                prefix_dict,
                session,
                config,
                start_date=start_date,
                end_date=end_date,
                overwrite=overwrite,
            )
            results[prefix] = (successful, attempts)
            total_successful += successful
            total_attempts += attempts
        except Exception as e:
            logger.error(f"Error downloading {prefix}: {e}")
            results[prefix] = (0, 0)

    logger.info(f"Download complete: {total_successful}/{total_attempts} files downloaded")

    return results


# =============================================================================
# Schema File Downloads
# =============================================================================


def download_schema_file(
    url: str,
    local_path: str | Path,
    session: requests.Session,
    config: DownloadConfig,
    skip_existing: bool = True,
) -> bool:
    """Download a single schema/PDF file.

    Args:
        url: URL to download from
        local_path: Local path to save the file
        session: Authenticated requests session
        config: Download configuration
        skip_existing: Skip download if file already exists

    Returns:
        True if file was downloaded, False if skipped or failed
    """
    local_path = Path(local_path)

    if skip_existing and local_path.exists():
        logger.debug(f"Schema file already exists, skipping: {local_path}")
        return False

    local_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Downloading schema: {url}")
    return _atomic_fetch(url, local_path, session, config)


def download_prefix_schemas(
    prefix: str,
    session: requests.Session,
    config: DownloadConfig,
    bad_text_filters: list | None = None,
    overwrite: bool = False,
) -> tuple[int, int]:
    """Download all schema/PDF files for a specific prefix.

    Args:
        prefix: The data prefix to download schemas for
        session: Authenticated requests session
        config: Download configuration
        bad_text_filters: Optional list of text filters to skip files
        overwrite: Re-download files that already exist (default: skip them)

    Returns:
        Tuple of (successful_downloads, total_attempts)
    """
    logger.info(f"Downloading schemas for prefix: {prefix}")

    # Get the schema folder for this prefix
    schema_folder = _get_prefix_schema_folder(prefix, config)

    # Find all schema links on the page
    schema_links = find_schema_links(prefix, session, config, bad_text_filters)

    successful_downloads = 0
    total_attempts = 0

    # Download each PDF
    for link_info in schema_links:
        href = link_info["href"]
        link_text = link_info["text"]

        # Sanitize the link text to create a valid filename
        sanitized_text = re.sub(r'[\\/*?:"<>|]', "_", link_text)
        filename = f"{prefix}_{sanitized_text}.pdf"
        file_path = schema_folder / filename

        # Download the PDF
        total_attempts += 1
        if download_schema_file(
            href, file_path, session, config, skip_existing=not overwrite
        ):
            successful_downloads += 1

    logger.info(
        f"Completed schema download for {prefix}: {successful_downloads}/{total_attempts} files downloaded"
    )
    return successful_downloads, total_attempts


def download_all_schemas(
    prefixes: list,
    session: requests.Session,
    config: DownloadConfig,
    overwrite: bool = False,
) -> dict[str, tuple[int, int]]:
    """Download schema/PDF files for all or specified prefixes.

    Args:
        prefixes: List of prefixes to download schemas for
        session: Authenticated requests session
        config: Download configuration
        overwrite: Re-download files that already exist (default: skip them)

    Returns:
        Dictionary mapping prefix to (successful_downloads, total_attempts)
    """
    results = {}
    total_successful = 0
    total_attempts = 0

    logger.info(f"Starting schema download for {len(prefixes)} prefixes")

    for prefix in prefixes:
        try:
            successful, attempts = download_prefix_schemas(
                prefix, session, config, overwrite=overwrite
            )
            results[prefix] = (successful, attempts)
            total_successful += successful
            total_attempts += attempts
        except Exception as e:
            logger.error(f"Error downloading schemas for {prefix}: {e}")
            results[prefix] = (0, 0)

    logger.info(f"Schema download complete: {total_successful}/{total_attempts} files downloaded")
    return results


# =============================================================================
# Bulk Page Downloads
# =============================================================================

# Files on the bulk page that are true daily snapshots (no historical value)
_DAILY_SNAPSHOT_FILES: set[str] = {
    "dailySFPS.zip",
    "dailySFS.zip",
    "dailyll_new.zip",
    "hdailyPS.txt",
    "hdailyS.txt",
    "hdailyll_new.zip",
    "mfpldaily3.zip",
    "platdailyPPS.txt",
    "platdailyPS.txt",
    "platdcoll.txt",
    "hplatdailyPS.txt",
    "hplatdailyS.txt",
    "Prospective_MIP.txt",
    "MIPPrelim.txt",
}

# Monthly files that appear on the bulk page without date suffixes.
# Maps bulk filename -> prefix used in the prefix dictionary.
_UNDATED_MONTHLY_FILES: dict[str, str] = {
    "dailyllmni.zip": "dailyllmni",
    "hdailyllmni.zip": "hdailyllmni",
    "mfpldailymni3.zip": "mfpldailymni3",
}


def _record_bulk_download(prefix_folder: Path, filename: str) -> None:
    """Append a filename to the bulk download manifest for a prefix folder."""
    manifest = prefix_folder / ".bulk_downloads"
    existing = set()
    if manifest.exists():
        existing = set(manifest.read_text().splitlines())
    if filename not in existing:
        with open(manifest, "a") as f:
            f.write(filename + "\n")


def scrape_bulk_page(
    session: requests.Session,
    config: DownloadConfig,
) -> list[dict]:
    """Scrape the GNMA bulk download page for available files.

    Args:
        session: Authenticated requests session
        config: Download configuration

    Returns:
        List of dicts with keys: filename, href, size, posted_date
    """
    url = GNMAConfig.BULK_URL
    try:
        response = session.get(url, timeout=config.request_timeout_s)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to access bulk page: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    links = soup.find_all("a", href=lambda h: h and "protectedfiledownload" in h)

    files = []
    for link in links:
        href = link.get("href", "")
        filename = link.get_text(strip=True)
        if not filename:
            continue
        if not href.startswith("http"):
            href = f"{GNMAConfig.BULK_URL}{href}"
        files.append({"filename": filename, "href": href})

    logger.info(f"Found {len(files)} files on bulk page")
    return files


def _parse_bulk_filename(filename: str) -> tuple[str, str | None, str]:
    """Parse a bulk page filename into (prefix, date_suffix, extension).

    Returns:
        Tuple of (prefix, date_suffix_or_None, extension)
    """
    # Handle files like "issrcutoff_20260331.txt" (8-digit date)
    match = re.match(r"^(.+?)_(\d{6,8})\.(\w+)$", filename)
    if match:
        return match.group(1), match.group(2), match.group(3)

    # No date suffix
    match = re.match(r"^(.+?)\.(\w+)$", filename)
    if match:
        return match.group(1), None, match.group(2)

    return filename, None, ""


def _determine_reporting_period(filename: str, local_path: Path) -> str | None:
    """Determine the YYYYMM reporting period for an undated monthly file.

    Peeks inside the downloaded file to extract the reporting period from
    the header record. Falls back to previous month if header can't be read.

    Args:
        filename: Original bulk page filename
        local_path: Path to the downloaded file

    Returns:
        YYYYMM string, or None if period cannot be determined
    """
    import io
    import zipfile

    try:
        with zipfile.ZipFile(local_path) as zf:
            inner_name = zf.namelist()[0]
            with zf.open(inner_name) as f:
                first_line = io.TextIOWrapper(f, encoding="utf-8").readline().strip()
                # Fixed-width header: date at positions ~24-30 (YYYYMM)
                date_match = re.search(r"(\d{6})", first_line)
                if date_match:
                    candidate = date_match.group(1)
                    year = int(candidate[:4])
                    month = int(candidate[4:6])
                    if 2010 <= year <= 2050 and 1 <= month <= 12:
                        return candidate
    except Exception as e:
        logger.debug(f"Could not read header from {filename}: {e}")

    # Fallback: previous month
    from dateutil.relativedelta import relativedelta

    prev = datetime.datetime.now() - relativedelta(months=1)
    return prev.strftime("%Y%m")


def download_bulk_data(
    session: requests.Session,
    config: DownloadConfig,
    skip_daily: bool = True,
) -> dict[str, int]:
    """Download data files from the GNMA bulk download page.

    Downloads all files from bulk.ginniemae.gov. Files without date suffixes
    (monthly new issuance files) are renamed with their reporting period.
    True daily snapshot files are skipped by default.

    Bulk-sourced files are saved to the same raw directories as history-page
    files so that subsequent history downloads can overwrite them.

    Args:
        session: Authenticated requests session
        config: Download configuration
        skip_daily: Skip true daily snapshot files (default True)

    Returns:
        Dict with keys: downloaded, skipped, failed, skipped_daily
    """
    stats = {"downloaded": 0, "skipped": 0, "failed": 0, "skipped_daily": 0}

    bulk_files = scrape_bulk_page(session, config)
    if not bulk_files:
        return stats

    for file_info in bulk_files:
        filename = file_info["filename"]
        href = file_info["href"]

        # Skip daily snapshots
        if skip_daily and filename in _DAILY_SNAPSHOT_FILES:
            logger.debug(f"Skipping daily snapshot: {filename}")
            stats["skipped_daily"] += 1
            continue

        prefix, date_suffix, ext = _parse_bulk_filename(filename)

        # For undated monthly files, download to temp first, then determine date
        if filename in _UNDATED_MONTHLY_FILES:
            dict_prefix = _UNDATED_MONTHLY_FILES[filename]
            prefix_folder = _get_prefix_data_folder(dict_prefix, config)
            temp_path = prefix_folder / filename

            if not download_data_file(href, temp_path, session, config, skip_existing=False):
                stats["failed"] += 1
                continue

            period = _determine_reporting_period(filename, temp_path)
            if period:
                final_name = f"{dict_prefix}_{period}.{ext}"
                final_path = prefix_folder / final_name
                if final_path.exists() and final_path != temp_path:
                    final_path.unlink()
                temp_path.rename(final_path)
                logger.info(f"Renamed {filename} -> {final_name} (period: {period})")
                _record_bulk_download(prefix_folder, final_name)
            stats["downloaded"] += 1
            continue

        # Dated files — download to the appropriate prefix folder
        if date_suffix is not None:
            prefix_folder = _get_prefix_data_folder(prefix, config)
            local_path = prefix_folder / filename
            if local_path.exists():
                logger.debug(f"Bulk file already exists, skipping: {filename}")
                stats["skipped"] += 1
                continue
            if download_data_file(href, local_path, session, config, skip_existing=True):
                stats["downloaded"] += 1
                _record_bulk_download(prefix_folder, filename)
            else:
                stats["failed"] += 1
            continue

        # Other undated files (WHFIT, mftermpools, etc.) — skip
        logger.debug(f"Skipping undated non-monthly file: {filename}")
        stats["skipped"] += 1

    return stats
