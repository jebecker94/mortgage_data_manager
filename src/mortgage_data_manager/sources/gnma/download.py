"""Download utilities for Ginnie Mae (GNMA) disclosure files.

Functions for downloading GNMA data and schema files from the web.

This module handles:
    - Session setup with GNMA bearer-token authentication (user-profiles API)
    - File discovery via the disclosure-api / bulk-content JSON APIs
    - Downloading data files (ZIP archives) to the raw layer
    - Downloading schema files (PDFs) for data documentation

The July 2026 ginniemae.gov rebuild replaced the scraped SharePoint pages
(``disclosurehistoryfiles.aspx``, ``bulk.ginniemae.gov`` link lists) with JSON
APIs, and the ``GMProfileInfo`` cookie with a ``gm_up_token`` bearer token
obtained by answering the profile's secret question (``GNMA_ANSWER``).

Public API:
    - create_session: Create authenticated session with a GNMA profile token
    - find_data_links: List history data files for a prefix (disclosure API)
    - find_schema_links: List schema/PDF files for a prefix (bulk-content API)
    - download_data_file: Download a single data file
    - download_prefix_data: Download all data files for a prefix
    - download_all_data: Download data for multiple prefixes
    - download_schema_file: Download a single schema/PDF file
    - download_prefix_schemas: Download all schema files for a prefix
    - download_all_schemas: Download schemas for multiple prefixes

"""

from __future__ import annotations

import dataclasses
import datetime
import re
import time
from pathlib import Path
from typing import cast

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from mortgage_data_manager.core.download import (
    DownloadResult,
    DownloadStatus,
    conditional_download,
)
from mortgage_data_manager.core.download_ledger import (
    DownloadRecord,
    append_record,
    relative_destination,
)
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


def _load_cached_token(config: DownloadConfig) -> str | None:
    """Read a previously issued profile token from the cache file, if any."""
    try:
        token = config.token_cache_file.read_text().strip()
    except OSError:
        return None
    return token or None


def _store_cached_token(config: DownloadConfig, token: str) -> None:
    """Persist the profile token so later runs skip the login round-trip."""
    try:
        config.token_cache_file.parent.mkdir(parents=True, exist_ok=True)
        config.token_cache_file.write_text(token)
        config.token_cache_file.chmod(0o600)
    except OSError as e:
        logger.warning("Could not cache GNMA profile token: %s", e)


def _token_is_valid(token: str, session: requests.Session, config: DownloadConfig) -> bool:
    """Check a profile token against the user-profiles API (200 = still valid)."""
    try:
        response = session.get(
            f"{config.user_profile_api_url}/profile",
            headers={"Authorization": f"Bearer {token}"},
            timeout=config.request_timeout_s,
            allow_redirects=False,
        )
    except requests.exceptions.RequestException:
        return False
    return response.status_code == 200


def _login(session: requests.Session, config: DownloadConfig) -> str:
    """Obtain a profile token via the lookup + verify-answer flow.

    Returns:
        The profile token for the authenticated session.

    Raises:
        ValueError: If credentials are missing, the profile does not exist, or
            the secret-question answer is rejected.
    """
    if not config.email_value:
        raise ValueError("GNMA_EMAIL is required for Ginnie Mae authentication")

    lookup = session.post(
        f"{config.user_profile_api_url}/auth/lookup",
        json={"email": config.email_value},
        timeout=config.request_timeout_s,
    )
    lookup.raise_for_status()
    profile = lookup.json()
    if not profile.get("found"):
        raise ValueError(
            f"No Ginnie Mae download profile found for {config.email_value}; "
            f"register at {config.site_base_url}/disclosure/download-login"
        )

    question = profile.get("secretQuestion")
    if not config.answer_value:
        raise ValueError(
            "GNMA_ANSWER is not set. Add it to .env with the answer to your "
            f"profile's secret question: {question!r}"
        )

    response = session.post(
        f"{config.user_profile_api_url}/auth/verify-answer",
        json={
            "email": config.email_value,
            "answer": config.answer_value,
            "disclaimerAccepted": True,
        },
        timeout=config.request_timeout_s,
    )
    if response.status_code in (400, 401, 403):
        raise ValueError(
            f"Ginnie Mae rejected the GNMA_ANSWER for secret question {question!r} "
            f"(HTTP {response.status_code})"
        )
    response.raise_for_status()
    token = response.json().get("token")
    if not token:
        raise ValueError("Ginnie Mae login succeeded but returned no profile token")

    logger.info("Authenticated with the Ginnie Mae user-profiles API")
    return cast(str, token)


def create_session(config: DownloadConfig) -> requests.Session:
    """Create and configure a requests session with authentication.

    Obtains a ``gm_up_token`` profile token (reusing a cached one when still
    valid — tokens last ~90 days) and attaches it as both a Bearer header and a
    cookie, plus a retry strategy for transient failures.

    Args:
        config: DownloadConfig with authentication details

    Returns:
        Configured requests.Session instance

    Raises:
        ValueError: If authentication fails (see `_login`).
    """
    session = requests.Session()
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

    token = _load_cached_token(config)
    if token is not None and not _token_is_valid(token, session, config):
        logger.info("Cached GNMA profile token is no longer valid; re-authenticating")
        token = None
    if token is None:
        token = _login(session, config)
        _store_cached_token(config, token)

    # Bearer header for the JSON APIs; cookie for the download redirect flow.
    session.headers["Authorization"] = f"Bearer {token}"
    session.cookies.set(
        name=config.cookie_name,
        value=token,
        domain=config.cookie_domain,
        path=config.cookie_path,
        secure=True,
    )

    return session


# =============================================================================
# Discovery Functions
# =============================================================================


# Directory listings cover every prefix in one response, so they are fetched
# once per process and shared across the per-prefix find_* calls.
_directory_listing_cache: dict[str, list[dict]] = {}


def fetch_directory_listing(
    directory: str,
    session: requests.Session,
    config: DownloadConfig,
    use_cache: bool = True,
) -> list[dict]:
    """Fetch the file listing for a disclosure directory (no auth required).

    One ``GET {disclosure_api_url}/files?directory=...`` call returns every
    file in the directory (name, size, created/modified timestamps), so the
    result is cached for the life of the process.

    Args:
        directory: Directory name on the disclosure server (e.g.
            ``data_history_cons``, ``data_bulk``)
        session: Requests session
        config: Download configuration
        use_cache: Reuse a previously fetched listing (default True)

    Returns:
        Flat list of file-info dictionaries; empty on request failure.
    """
    if use_cache and directory in _directory_listing_cache:
        return _directory_listing_cache[directory]

    try:
        response = session.get(
            f"{config.disclosure_api_url}/files",
            params={"directory": directory},
            timeout=config.request_timeout_s,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.exceptions.RequestException, ValueError) as e:
        logger.error("Failed to list disclosure directory %s: %s", directory, e)
        return []

    time.sleep(config.request_delay)

    files = [f for group in payload for f in group.get("files", [])]
    _directory_listing_cache[directory] = files
    logger.info("Listed %s files in disclosure directory %s", len(files), directory)
    return files


def find_data_links(prefix: str, session: requests.Session, config: DownloadConfig) -> list[dict]:
    """Find all history data files for a prefix via the disclosure files API.

    Filters the (cached) ``data_history_cons`` directory listing down to the
    given prefix. PDFs are excluded (those are schemas).

    Args:
        prefix: File prefix to search for
        session: Requests session (listing needs no auth)
        config: Download configuration

    Returns:
        List of dictionaries with link information (href, text, prefix)
    """
    files = fetch_directory_listing(GNMAConfig.HISTORY_DIRECTORY, session, config)

    links_info = []
    for file_info in files:
        file_name = file_info.get("fileName") or ""
        if not file_name.startswith(prefix) or file_name.lower().endswith(".pdf"):
            continue
        links_info.append(
            {
                "href": f"{config.base_url}/{file_name}",
                "text": file_name,
                "prefix": prefix,
                "file_size": file_info.get("fileSize"),
                "file_modified": file_info.get("fileModified"),
            }
        )

    logger.info("Found %s data links for %s", len(links_info), prefix)
    return links_info


_layouts_listing_cache: list[dict] = []


def _fetch_layouts_listing(session: requests.Session, config: DownloadConfig) -> list[dict]:
    """Fetch the layouts/sample-files listing (cached; no auth required).

    One ``GET {bulk_content_api_url}/layouts-sample-files`` call returns every
    published layout PDF and sample file across all categories.

    Returns:
        The listing entries, or an empty list when the request fails.
    """
    if _layouts_listing_cache:
        return _layouts_listing_cache

    try:
        response = session.get(
            f"{config.bulk_content_api_url}/layouts-sample-files",
            timeout=config.request_timeout_s,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.exceptions.RequestException, ValueError) as e:
        logger.error("Failed to list layouts/sample files: %s", e)
        return []

    time.sleep(config.request_delay)

    entries = [entry for category in payload.values() for entry in category]
    _layouts_listing_cache.extend(entries)
    logger.info("Listed %s layout/sample files", len(entries))
    return _layouts_listing_cache


def find_schema_links(
    prefix: str,
    session: requests.Session,
    config: DownloadConfig,
    bad_text_filters: list | None = None,
) -> list[dict]:
    """Find all schema/PDF files for a prefix via the layouts listing API.

    Filters the (cached) layouts/sample-files listing down to the given
    prefix's PDFs. Files matching bad_text_filters are excluded.

    Args:
        prefix: File prefix to search for
        session: Requests session (listing needs no auth)
        config: Download configuration
        bad_text_filters: Optional list of text patterns to filter out

    Returns:
        List of dictionaries with link information (href, text, filename, prefix)
    """
    if bad_text_filters is None:
        bad_text_filters = config.bad_text_filters or []

    # Set default filters if none provided
    if not bad_text_filters:
        bad_text_filters = ["Supplemental Loan Level Forbearance File"]

    entries = _fetch_layouts_listing(session, config)

    links_info = []
    for entry in entries:
        file_name = entry.get("fileName") or ""
        if not file_name.startswith(f"{prefix}_") or not file_name.lower().endswith(".pdf"):
            continue

        title = (entry.get("title") or "").strip()
        if any(bad_text in title for bad_text in bad_text_filters):
            logger.debug("Skipping filtered file: %s", title)
            continue

        media_path = entry.get("field_media_file") or ""
        href = media_path if media_path.startswith("http") else config.site_base_url + media_path
        links_info.append({"href": href, "text": title, "filename": file_name, "prefix": prefix})

    logger.info("Found %s schema links for %s", len(links_info), prefix)
    return links_info


def find_layout_links(session: requests.Session, config: DownloadConfig) -> dict[str, dict]:
    """List per-prefix layout PDFs from the layouts/sample-files API.

    Unlike `find_schema_links` (one prefix at a time), this returns every
    ``<prefix>_layout.pdf`` in the listing keyed by prefix.

    Args:
        session: Requests session (listing needs no auth).
        config: Download configuration.

    Returns:
        Mapping of ``prefix`` -> ``{"href": absolute_url}``. Empty on request
        failure.
    """
    suffix = "_layout.pdf"
    layouts: dict[str, dict] = {}
    for entry in _fetch_layouts_listing(session, config):
        file_name = entry.get("fileName") or ""
        if not file_name.endswith(suffix):
            continue
        prefix = file_name[: -len(suffix)]
        media_path = entry.get("field_media_file") or ""
        href = media_path if media_path.startswith("http") else config.site_base_url + media_path
        # First occurrence wins (listing carries each layout once).
        layouts.setdefault(prefix, {"href": href})

    logger.info("Found %s layout PDFs in the layouts listing", len(layouts))
    return layouts


def download_layout_schemas(
    prefixes: list[str],
    session: requests.Session,
    config: DownloadConfig,
    overwrite: bool = False,
) -> list[DownloadResult]:
    """Download ``<prefix>_layout.pdf`` files from the bulk-layout page.

    Scrapes the layout page once (`find_layout_links`), then for each requested
    prefix that has a layout, downloads it to ``raw/<prefix>/<prefix>_layout.pdf`` so
    the existing extract -> combine schema pipeline (which globs ``<prefix>_*.pdf``)
    can consume it. Prefixes with no layout on the page are logged and skipped.

    Args:
        prefixes: Prefixes to fetch layouts for.
        session: Requests session.
        config: Download configuration.
        overwrite: Re-download files that already exist (default: skip them).

    Returns:
        One DownloadResult per prefix that has a published layout.
    """
    layouts = find_layout_links(session, config)
    results: list[DownloadResult] = []
    for prefix in prefixes:
        info = layouts.get(prefix)
        if info is None:
            logger.warning("No layout PDF published for prefix: %s", prefix)
            continue
        file_path = _get_prefix_schema_folder(prefix, config) / f"{prefix}_layout.pdf"
        results.append(download_schema_file(info["href"], file_path, session, config, overwrite))
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


_DATA_TYPES: frozenset[str] = frozenset({"zip", "text"})
_SCHEMA_TYPES: frozenset[str] = frozenset({"pdf", "zip", "xls", "text"})


def _fetch(
    url: str,
    local_path: Path,
    session: requests.Session,
    config: DownloadConfig,
    *,
    overwrite: bool,
    expect: frozenset[str] = _DATA_TYPES,
) -> DownloadResult:
    """Fetch one URL to local_path via core.download, carrying session auth.

    ``conditional_download`` owns the skip-if-exists decision and ledgers skips as
    well as fetches; the body goes through ``atomic_download`` (atomic ``.tmp``
    rename, config-driven retries). The authenticated session's cookies,
    Authorization header, and user-agent are forwarded as request headers since
    core.download does not take a session. ``expect`` guards the body type: an
    unauthenticated request 302-redirects to the HTML login page, which would
    otherwise be saved under the data filename.
    """
    local_path.parent.mkdir(parents=True, exist_ok=True)
    cookie_header = "; ".join(f"{c.name}={c.value}" for c in session.cookies)
    headers = {"Cookie": cookie_header, "user-agent": config.user_agent}
    authorization = session.headers.get("Authorization")
    if authorization:
        headers["Authorization"] = str(authorization)
    result = conditional_download(
        url,
        local_path,
        mode="overwrite" if overwrite else "skip",
        headers=headers,
        timeout=config.request_timeout_s,
        chunk_size=config.stream_chunk_size,
        show_progress=False,
        source="gnma",
        dataset=local_path.parent.name,
        expect=expect,
        raw_dir=GNMAConfig.GNMA_RAW_DIR,
    )
    if result.status == DownloadStatus.SUCCESS:
        logger.info("Downloaded: %s (%s bytes)", local_path, result.bytes_downloaded)
        time.sleep(config.request_delay)
    elif result.status == DownloadStatus.FAILED:
        if result.sniffed_type == "html":
            logger.error(
                "Download of %s returned an HTML page instead of data — "
                "authentication failed; check GNMA_EMAIL/GNMA_ANSWER",
                url,
            )
        else:
            logger.error("Failed to download %s: %s", url, result.error_message)
    else:
        logger.debug("Skipped (exists): %s", local_path)
    return result


def download_data_file(
    url: str,
    local_path: str | Path,
    session: requests.Session,
    config: DownloadConfig,
    overwrite: bool = False,
) -> DownloadResult:
    """Download a single data file.

    Args:
        url: URL to download from
        local_path: Local path to save the file
        session: Authenticated requests session
        config: Download configuration
        overwrite: Re-download a file that already exists

    Returns:
        The DownloadResult (success, skipped_exists or failed).
    """
    return _fetch(url, Path(local_path), session, config, overwrite=overwrite)


def download_prefix_data(
    prefix: str,
    prefix_dict: dict,
    session: requests.Session,
    config: DownloadConfig,
    start_date: datetime.datetime | None = None,
    end_date: datetime.datetime | None = None,
    overwrite: bool = False,
) -> list[DownloadResult]:
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
        One DownloadResult per file attempted.

    Raises:
        ValueError: If ``prefix`` is not in ``prefix_dict``.
    """
    from dateutil.relativedelta import relativedelta

    from .utils import create_date_suffix, get_date_format

    if prefix not in prefix_dict:
        raise ValueError(f"Unknown prefix: {prefix}")

    prefix_config = prefix_dict[prefix]

    # Get data links from page for validation
    logger.info("Discovering available files for %s...", prefix)
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

    logger.info("Downloading %s data from %s to %s", prefix, start_date.date(), end_date.date())

    results: list[DownloadResult] = []
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
    manifest = prefix_folder / ".bulk_downloads"

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
                logger.debug("File %s not found in data links; skipping", file_name)
                consecutive_misses += 1
                if miss_threshold and consecutive_misses >= miss_threshold:
                    logger.info(
                        "Stopping early after %s consecutive misses for %s",
                        consecutive_misses,
                        prefix,
                    )
                    break
                current_date += relativedelta(months=step_months)
                continue

        # With overwrite, always re-download. Otherwise skip existing canonical
        # files, but still re-fetch ones that came from the bulk page (early
        # access) to replace them with the canonical version.
        from_bulk = manifest.exists() and file_name in manifest.read_text().splitlines()
        result = download_data_file(
            file_url, local_path, session, config, overwrite=overwrite or from_bulk
        )
        results.append(result)
        if result.status == DownloadStatus.SUCCESS:
            consecutive_misses = 0
            if from_bulk:  # the history version replaced the early-access copy
                lines = [line for line in manifest.read_text().splitlines() if line != file_name]
                manifest.write_text("\n".join(lines) + "\n" if lines else "")

        # Move to next period
        current_date += relativedelta(months=step_months)

    logger.info("Completed %s: %s file(s) attempted", prefix, len(results))
    return results


def download_all_data(
    prefixes: list,
    prefix_dict: dict,
    session: requests.Session,
    config: DownloadConfig,
    start_date: datetime.datetime | None = None,
    end_date: datetime.datetime | None = None,
    overwrite: bool = False,
) -> list[DownloadResult]:
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
        One DownloadResult per file attempted, across all prefixes. A prefix whose
        discovery raises is logged and contributes no results.
    """
    logger.info("Starting download for %s prefixes", len(prefixes))
    results: list[DownloadResult] = []
    for prefix in prefixes:
        try:
            results += download_prefix_data(
                prefix,
                prefix_dict,
                session,
                config,
                start_date=start_date,
                end_date=end_date,
                overwrite=overwrite,
            )
        except Exception as e:
            logger.error("Error downloading %s: %s", prefix, e)
    return results


# =============================================================================
# Schema File Downloads
# =============================================================================


def download_schema_file(
    url: str,
    local_path: str | Path,
    session: requests.Session,
    config: DownloadConfig,
    overwrite: bool = False,
) -> DownloadResult:
    """Download a single schema/PDF file.

    Args:
        url: URL to download from
        local_path: Local path to save the file
        session: Authenticated requests session
        config: Download configuration
        overwrite: Re-download a file that already exists

    Returns:
        The DownloadResult (success, skipped_exists or failed).
    """
    return _fetch(url, Path(local_path), session, config, overwrite=overwrite, expect=_SCHEMA_TYPES)


def download_prefix_schemas(
    prefix: str,
    session: requests.Session,
    config: DownloadConfig,
    bad_text_filters: list | None = None,
    overwrite: bool = False,
) -> list[DownloadResult]:
    """Download all schema/PDF files for a specific prefix.

    Args:
        prefix: The data prefix to download schemas for
        session: Authenticated requests session
        config: Download configuration
        bad_text_filters: Optional list of text filters to skip files
        overwrite: Re-download files that already exist (default: skip them)

    Returns:
        One DownloadResult per schema file listed for the prefix.
    """
    logger.info("Downloading schemas for prefix: %s", prefix)
    schema_folder = _get_prefix_schema_folder(prefix, config)
    results: list[DownloadResult] = []
    for link_info in find_schema_links(prefix, session, config, bad_text_filters):
        # Prefer the listing's own filename (already prefix-scoped, e.g.
        # ``llmon2_layout.pdf``); fall back to a sanitized title.
        filename = link_info.get("filename")
        if not filename:
            sanitized_text = re.sub(r'[\\/*?:"<>|]', "_", link_info["text"])
            filename = f"{prefix}_{sanitized_text}.pdf"
        results.append(
            download_schema_file(
                link_info["href"], schema_folder / filename, session, config, overwrite
            )
        )
    return results


def download_all_schemas(
    prefixes: list,
    session: requests.Session,
    config: DownloadConfig,
    overwrite: bool = False,
) -> list[DownloadResult]:
    """Download schema/PDF files for all or specified prefixes.

    Args:
        prefixes: List of prefixes to download schemas for
        session: Authenticated requests session
        config: Download configuration
        overwrite: Re-download files that already exist (default: skip them)

    Returns:
        One DownloadResult per schema file, across all prefixes. A prefix whose
        discovery raises is logged and contributes no results.
    """
    logger.info("Starting schema download for %s prefixes", len(prefixes))
    results: list[DownloadResult] = []
    for prefix in prefixes:
        try:
            results += download_prefix_schemas(prefix, session, config, overwrite=overwrite)
        except Exception as e:
            logger.error("Error downloading schemas for %s: %s", prefix, e)
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
    """List the GNMA bulk download page files via the bulk-content API.

    Args:
        session: Requests session (listing needs no auth)
        config: Download configuration

    Returns:
        List of dicts with keys: filename, href
    """
    try:
        response = session.get(
            f"{config.bulk_content_api_url}/disclosure-data",
            timeout=config.request_timeout_s,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.exceptions.RequestException, ValueError) as e:
        logger.error("Failed to access bulk page listing: %s", e)
        return []

    files = []
    for entries in payload.values():
        for entry in entries:
            filename = entry.get("fileName")
            if not filename:
                continue
            directory = entry.get("directoryName") or GNMAConfig.BULK_DIRECTORY
            files.append(
                {
                    "filename": filename,
                    "href": f"{GNMAConfig.DOWNLOAD_URL}?dlfile={directory}/{filename}",
                }
            )

    logger.info("Found %s files on bulk page", len(files))
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
        logger.debug("Could not read header from %s: %s", filename, e)

    # Fallback: previous month
    from dateutil.relativedelta import relativedelta

    prev = datetime.datetime.now() - relativedelta(months=1)
    return cast(str, prev.strftime("%Y%m"))


def download_bulk_data(
    session: requests.Session,
    config: DownloadConfig,
    skip_daily: bool = True,
) -> list[DownloadResult]:
    """Download data files from the GNMA bulk download page.

    Downloads all files from bulk.ginniemae.gov. Files without date suffixes
    (monthly new issuance files) are renamed with their reporting period.
    True daily snapshot files are skipped by default, as are undated files that
    are not monthly (WHFIT, mftermpools, ...); neither is fetched.

    Bulk-sourced files are saved to the same raw directories as history-page
    files so that subsequent history downloads can overwrite them.

    Args:
        session: Authenticated requests session
        config: Download configuration
        skip_daily: Skip true daily snapshot files (default True)

    Returns:
        One DownloadResult per file fetched or skipped as existing. For a renamed
        undated monthly file the result's path is the renamed file.
    """
    results: list[DownloadResult] = []
    not_fetched = 0
    for file_info in scrape_bulk_page(session, config):
        filename = file_info["filename"]
        href = file_info["href"]

        # Skip daily snapshots
        if skip_daily and filename in _DAILY_SNAPSHOT_FILES:
            logger.debug("Skipping daily snapshot: %s", filename)
            not_fetched += 1
            continue

        prefix, date_suffix, ext = _parse_bulk_filename(filename)

        # For undated monthly files, download to temp first, then determine date
        if filename in _UNDATED_MONTHLY_FILES:
            dict_prefix = _UNDATED_MONTHLY_FILES[filename]
            prefix_folder = _get_prefix_data_folder(dict_prefix, config)
            temp_path = prefix_folder / filename
            result = download_data_file(href, temp_path, session, config, overwrite=True)
            if result.status == DownloadStatus.SUCCESS:
                period = _determine_reporting_period(filename, temp_path)
                if period:
                    final_name = f"{dict_prefix}_{period}.{ext}"
                    final_path = prefix_folder / final_name
                    if final_path.exists() and final_path != temp_path:
                        final_path.unlink()
                    temp_path.rename(final_path)
                    logger.info("Renamed %s -> %s (period: %s)", filename, final_name, period)
                    _record_bulk_download(prefix_folder, final_name)
                    result = dataclasses.replace(result, path=final_path)
                    # The fetch was ledgered under the undated name; ledger the file
                    # that actually stays on disk too.
                    append_record(
                        DownloadRecord(
                            source="gnma",
                            destination=relative_destination(final_path, GNMAConfig.GNMA_RAW_DIR),
                            method="http",
                            status=DownloadStatus.SUCCESS.value,
                            dataset=dict_prefix,
                            url=result.url,
                            bytes=result.bytes_downloaded,
                            sha256=result.sha256,
                            sniffed_type=result.sniffed_type,
                            note=f"renamed from {filename} (reporting period {period})",
                        )
                    )
            results.append(result)
            continue

        # Dated files — download to the appropriate prefix folder
        if date_suffix is not None:
            prefix_folder = _get_prefix_data_folder(prefix, config)
            result = download_data_file(href, prefix_folder / filename, session, config)
            if result.status == DownloadStatus.SUCCESS:
                _record_bulk_download(prefix_folder, filename)
            results.append(result)
            continue

        # Other undated files (WHFIT, mftermpools, etc.) — skip
        logger.debug("Skipping undated non-monthly file: %s", filename)
        not_fetched += 1

    logger.info("Bulk page: %s file(s) not fetched (daily or undated non-monthly)", not_fetched)
    return results
