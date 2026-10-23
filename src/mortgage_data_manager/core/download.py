"""Shared download utilities for mortgage data manager.

This module provides standardized download functionality with retry logic,
atomic file operations, batch processing, and the raw download ledger: every
fetch — success or failure — is appended to the per-source ledger in
``core.download_ledger`` with its SHA-256 and sniffed content type. Skips (the
file is already on disk) are not recorded.

Example:
    >>> from mortgage_data_manager.core.download import atomic_download, DownloadResult
    >>> result = atomic_download(
    ...     "https://example.com/data.zip", Path("data/raw/data.zip"), source="example"
    ... )
    >>> print(f"Downloaded {result.bytes_downloaded} bytes ({result.sniffed_type})")
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Literal, TypedDict, cast

import requests
from rich.progress import DownloadColumn, Progress, TransferSpeedColumn

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.download_ledger import (
    AcquisitionMethod,
    DownloadRecord,
    StreamInspector,
    append_record,
    relative_destination,
)
from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)

DownloadMode = Literal["skip", "overwrite", "if_newer", "if_size_diff"]


class _HttpMeta(TypedDict):
    """Response metadata carried from the request into ``DownloadResult``."""

    http_status: int
    final_url: str
    etag: str | None
    last_modified: str | None
    content_type: str | None
    attempts: int


class DownloadStatus(Enum):
    """Status codes for download operations."""

    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"
    SKIPPED_EXISTS = "skipped_exists"
    SKIPPED_SIZE_MATCH = "skipped_size_match"
    SKIPPED_NOT_MODIFIED = "skipped_not_modified"
    SKIPPED_NOT_PUBLISHED = "skipped_not_published"
    REGISTERED = "registered"


SKIPPED_STATUSES: frozenset[DownloadStatus] = frozenset(
    {
        DownloadStatus.SKIPPED,
        DownloadStatus.SKIPPED_EXISTS,
        DownloadStatus.SKIPPED_SIZE_MATCH,
        DownloadStatus.SKIPPED_NOT_MODIFIED,
        DownloadStatus.SKIPPED_NOT_PUBLISHED,
    }
)


@dataclass
class DownloadResult:
    """Result of a download operation.

    Attributes:
        path: Destination path of the downloaded file
        status: Status of the download operation
        bytes_downloaded: Number of bytes downloaded (0 if skipped)
        duration_seconds: Time taken for the download
        error_message: Error message if download failed
        url: Source URL
        sha256: Hex digest of the bytes written (None if skipped/failed early)
        sniffed_type: Content type from the first bytes (``zip``, ``html``, …)
        http_status: Final HTTP status code, including a failed request's error status
        final_url: URL after redirects
        etag: ``ETag`` response header
        last_modified: ``Last-Modified`` response header, verbatim
        content_type: ``Content-Type`` response header
        attempts: HTTP attempts made, including retries
    """

    path: Path
    status: DownloadStatus
    bytes_downloaded: int = 0
    duration_seconds: float = 0.0
    error_message: str | None = None
    url: str = ""
    sha256: str | None = None
    sniffed_type: str | None = None
    http_status: int | None = None
    final_url: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    content_type: str | None = None
    attempts: int | None = None


class DownloadError(Exception):
    """Exception raised when a download operation fails after all retries."""

    def __init__(self, url: str, message: str, original_error: Exception | None = None):
        self.url = url
        self.message = message
        self.original_error = original_error
        super().__init__(f"Failed to download {url}: {message}")


def _retry_request(
    url: str,
    *,
    method: str = "GET",
    retries: int | None = None,
    timeout: int | None = None,
    backoff_factor: float | None = None,
    headers: dict[str, str] | None = None,
    stream: bool = False,
) -> tuple[requests.Response, int]:
    """Make a request with retries; return the response and the attempts used."""
    retries = retries if retries is not None else MortgageDataConfig.DOWNLOAD_RETRIES
    timeout = timeout if timeout is not None else MortgageDataConfig.DOWNLOAD_TIMEOUT
    backoff_factor = (
        backoff_factor if backoff_factor is not None else MortgageDataConfig.DOWNLOAD_BACKOFF_FACTOR
    )

    request_headers = {"User-Agent": MortgageDataConfig.USER_AGENT}
    if headers:
        request_headers.update(headers)

    last_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            response = requests.request(
                method,
                url,
                headers=request_headers,
                timeout=timeout,
                stream=stream,
            )
            response.raise_for_status()
            return response, attempt + 1
        except requests.exceptions.RequestException as e:
            last_error = e
            if attempt < retries:
                wait_time = backoff_factor**attempt
                logger.warning(
                    "Request failed (attempt %s/%s): %s. Retrying in %.1fs...",
                    attempt + 1,
                    retries + 1,
                    e,
                    wait_time,
                )
                time.sleep(wait_time)
            else:
                logger.error("Request failed after %s attempts: %s", retries + 1, e)

    raise DownloadError(url, str(last_error), last_error)


def retry_request(
    url: str,
    *,
    method: str = "GET",
    retries: int | None = None,
    timeout: int | None = None,
    backoff_factor: float | None = None,
    headers: dict[str, str] | None = None,
    stream: bool = False,
) -> requests.Response:
    """Make an HTTP request with retry logic and exponential backoff.

    Args:
        url: URL to request
        method: HTTP method (GET, HEAD, etc.)
        retries: Number of retry attempts (default from config)
        timeout: Request timeout in seconds (default from config)
        backoff_factor: Multiplier for exponential backoff (default from config)
        headers: Additional HTTP headers
        stream: If True, don't download response body immediately

    Returns:
        Response object

    Raises:
        DownloadError: If all retry attempts fail
    """
    response, _attempts = _retry_request(
        url,
        method=method,
        retries=retries,
        timeout=timeout,
        backoff_factor=backoff_factor,
        headers=headers,
        stream=stream,
    )
    return response


# -----------------------------------------------------------------------------
# Ledger plumbing
# -----------------------------------------------------------------------------


def _record(
    result: DownloadResult,
    *,
    source: str,
    dataset: str | None,
    raw_dir: Path | None,
    started_at: str,
    method: AcquisitionMethod = "http",
) -> None:
    """Append the ledger record for a fetched or failed result (skips are not recorded).

    Never raises. ``method`` names how the bytes were fetched (``http``, ``api_json``).
    """
    if result.status in SKIPPED_STATUSES:
        return
    try:
        append_record(
            DownloadRecord(
                source=source,
                destination=relative_destination(result.path, raw_dir),
                method=method,
                status=result.status.value,
                dataset=dataset,
                url=result.url or None,
                final_url=result.final_url,
                http_status=result.http_status,
                bytes=result.bytes_downloaded if result.sha256 else None,
                content_length=None,
                sha256=result.sha256,
                sniffed_type=result.sniffed_type,
                etag=result.etag,
                last_modified=result.last_modified,
                content_type=result.content_type,
                started_at=started_at,
                duration_s=result.duration_seconds,
                attempts=result.attempts,
                error=result.error_message,
            )
        )
    except Exception as exc:  # noqa: BLE001 - the ledger must never break a download
        logger.error("Could not append download ledger record for %s: %s", result.path, exc)


def _now() -> str:
    return datetime.now(UTC).isoformat()


# -----------------------------------------------------------------------------
# Downloads
# -----------------------------------------------------------------------------


def atomic_download(
    url: str,
    destination: Path,
    *,
    chunk_size: int | None = None,
    timeout: int | None = None,
    retries: int | None = None,
    verify_size: bool = True,
    show_progress: bool = True,
    headers: dict[str, str] | None = None,
    source: str,
    dataset: str | None = None,
    expect: Iterable[str] | None = None,
    raw_dir: Path | None = None,
) -> DownloadResult:
    """Download a file atomically (write to temp file, then rename) and ledger it.

    The download is atomic: if it fails partway through, no partial file
    will be left at the destination path. The bytes are hashed and sniffed as
    they stream, and one ledger record is appended whatever the outcome.

    Args:
        url: URL to download from
        destination: Path to save the downloaded file
        chunk_size: Size of chunks for streaming download (default from config)
        timeout: Request timeout in seconds (default from config)
        retries: Number of retry attempts (default from config)
        verify_size: If True, verify downloaded size matches Content-Length
        show_progress: If True, show download progress bar
        headers: Additional HTTP headers
        source: Subpackage the file belongs to (``hmda``, ``gnma``, …); names
            the ledger the record goes to.
        dataset: Optional dataset tag for the ledger record
        expect: Acceptable sniffed content types (e.g. ``{"zip"}``). A body of
            any other type — typically an HTML login or error page saved under a
            data filename — is deleted and reported as ``FAILED``. ``None``
            records the type without checking it.
        raw_dir: Root the ledger's ``destination`` is expressed relative to
            (default: inferred from ``MORTGAGE_DATA_DIR``)

    Returns:
        DownloadResult with status and metadata

    Example:
        >>> result = atomic_download(
        ...     "https://example.com/data.zip", Path("data/raw/data.zip"),
        ...     source="example", expect={"zip"},
        ... )
        >>> if result.status == DownloadStatus.SUCCESS:
        ...     print(f"Downloaded {result.bytes_downloaded} bytes")
    """
    chunk_size = chunk_size if chunk_size is not None else MortgageDataConfig.DOWNLOAD_CHUNK_SIZE
    timeout = timeout if timeout is not None else MortgageDataConfig.DOWNLOAD_TIMEOUT
    retries = retries if retries is not None else MortgageDataConfig.DOWNLOAD_RETRIES
    expected = set(expect) if expect is not None else None

    started_at = _now()
    start_time = time.time()
    temp_path = destination.with_suffix(destination.suffix + ".tmp")
    result: DownloadResult

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)

        response, attempts = _retry_request(
            url,
            retries=retries,
            timeout=timeout,
            headers=headers,
            stream=True,
        )
        http_meta: _HttpMeta = {
            "http_status": response.status_code,
            "final_url": response.url,
            "etag": response.headers.get("etag"),
            "last_modified": response.headers.get("last-modified"),
            "content_type": response.headers.get("content-type"),
            "attempts": attempts,
        }

        total_size = int(response.headers.get("content-length", 0))
        # When Content-Encoding is present (gzip/deflate/br), Content-Length
        # reports the compressed transfer size, but iter_content() yields
        # decompressed bytes. Skip size verification in this case.
        content_encoded = bool(response.headers.get("content-encoding"))
        inspector = StreamInspector()

        if show_progress and total_size > 0:
            with Progress(
                *Progress.get_default_columns(),
                DownloadColumn(),
                TransferSpeedColumn(),
            ) as progress:
                task = progress.add_task(f"Downloading {destination.name}", total=total_size)
                with open(temp_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=chunk_size):
                        if chunk:
                            f.write(chunk)
                            inspector.update(chunk)
                            progress.update(task, advance=len(chunk))
        else:
            with open(temp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        inspector.update(chunk)

        bytes_downloaded = inspector.bytes
        sniffed = inspector.sniffed_type
        digest = inspector.sha256

        if (
            verify_size
            and total_size > 0
            and not content_encoded
            and bytes_downloaded != total_size
        ):
            temp_path.unlink(missing_ok=True)
            result = DownloadResult(
                path=destination,
                status=DownloadStatus.FAILED,
                bytes_downloaded=bytes_downloaded,
                duration_seconds=time.time() - start_time,
                error_message=f"Size mismatch: expected {total_size}, got {bytes_downloaded}",
                url=url,
                sha256=digest,
                sniffed_type=sniffed,
                **http_meta,
            )
        elif expected is not None and sniffed not in expected:
            temp_path.unlink(missing_ok=True)
            result = DownloadResult(
                path=destination,
                status=DownloadStatus.FAILED,
                bytes_downloaded=bytes_downloaded,
                duration_seconds=time.time() - start_time,
                error_message=(
                    f"Unexpected content type {sniffed!r}; expected one of {sorted(expected)}"
                ),
                url=url,
                sha256=digest,
                sniffed_type=sniffed,
                **http_meta,
            )
            logger.error(
                "%s returned a %s body instead of %s — not saved",
                url,
                sniffed,
                "/".join(sorted(expected)),
            )
        else:
            temp_path.rename(destination)
            result = DownloadResult(
                path=destination,
                status=DownloadStatus.SUCCESS,
                bytes_downloaded=bytes_downloaded,
                duration_seconds=time.time() - start_time,
                url=url,
                sha256=digest,
                sniffed_type=sniffed,
                **http_meta,
            )

    except Exception as e:  # noqa: BLE001 - reported through DownloadResult, never raised
        temp_path.unlink(missing_ok=True)
        cause = e.original_error if isinstance(e, DownloadError) else e
        result = DownloadResult(
            path=destination,
            status=DownloadStatus.FAILED,
            duration_seconds=time.time() - start_time,
            error_message=str(e),
            url=url,
            http_status=getattr(getattr(cause, "response", None), "status_code", None),
        )

    _record(result, source=source, dataset=dataset, raw_dir=raw_dir, started_at=started_at)
    return result


def conditional_download(
    url: str,
    destination: Path,
    *,
    mode: DownloadMode = "skip",
    chunk_size: int | None = None,
    timeout: int | None = None,
    retries: int | None = None,
    show_progress: bool = True,
    headers: dict[str, str] | None = None,
    source: str,
    dataset: str | None = None,
    expect: Iterable[str] | None = None,
    raw_dir: Path | None = None,
) -> DownloadResult:
    """Download a file with conditional logic based on existing files.

    Skips are ledgered too: "we did not re-fetch because X" is an acquisition
    decision.

    Args:
        url: URL to download from
        destination: Path to save the downloaded file
        mode: Conditional download mode:
            - "skip": Skip if file exists (default)
            - "overwrite": Always download, overwriting existing
            - "if_newer": Download if server file is newer (uses Last-Modified)
            - "if_size_diff": Download if size differs from existing file
        chunk_size: Size of chunks for streaming download
        timeout: Request timeout in seconds
        retries: Number of retry attempts
        show_progress: If True, show download progress bar
        headers: Additional HTTP headers
        source: Subpackage the file belongs to (see `atomic_download`)
        dataset: Optional dataset tag for the ledger record
        expect: Acceptable sniffed content types (see `atomic_download`)
        raw_dir: Root the ledger's ``destination`` is relative to

    Returns:
        DownloadResult with status and metadata
    """
    started_at = _now()

    def skipped(status: DownloadStatus) -> DownloadResult:
        result = DownloadResult(path=destination, status=status, url=url)
        _record(
            result,
            source=source,
            dataset=dataset,
            raw_dir=raw_dir,
            started_at=started_at,
        )
        return result

    if mode == "skip" and destination.exists():
        logger.debug("Skipping existing file: %s", destination)
        return skipped(DownloadStatus.SKIPPED_EXISTS)

    if mode == "if_size_diff" and destination.exists():
        try:
            response = retry_request(
                url, method="HEAD", timeout=timeout, retries=retries, headers=headers
            )
            remote_size = int(response.headers.get("content-length", 0))
            local_size = destination.stat().st_size

            if remote_size > 0 and remote_size == local_size:
                logger.debug("Skipping file (size matches): %s", destination)
                return skipped(DownloadStatus.SKIPPED_SIZE_MATCH)
        except Exception as e:  # noqa: BLE001 - a failed probe falls through to the download
            logger.warning("Could not check remote size, proceeding with download: %s", e)

    if mode == "if_newer" and destination.exists():
        try:
            response = retry_request(
                url, method="HEAD", timeout=timeout, retries=retries, headers=headers
            )
            last_modified = response.headers.get("last-modified")

            if last_modified:
                from email.utils import parsedate_to_datetime

                remote_time = parsedate_to_datetime(last_modified).timestamp()
                local_time = destination.stat().st_mtime

                if remote_time <= local_time:
                    logger.debug("Skipping file (not modified): %s", destination)
                    return skipped(DownloadStatus.SKIPPED_NOT_MODIFIED)
        except Exception as e:  # noqa: BLE001 - a failed probe falls through to the download
            logger.warning("Could not check last-modified, proceeding with download: %s", e)

    return atomic_download(
        url,
        destination,
        chunk_size=chunk_size,
        timeout=timeout,
        retries=retries,
        show_progress=show_progress,
        headers=headers,
        source=source,
        dataset=dataset,
        expect=expect,
        raw_dir=raw_dir,
    )


@dataclass
class DownloadTask:
    """A single download task for batch processing.

    Attributes:
        url: URL to download from
        destination: Path to save the downloaded file
        headers: Optional additional HTTP headers for this specific download
        dataset: Optional per-task dataset tag for the ledger (overrides the
            batch-level ``dataset``)
    """

    url: str
    destination: Path
    headers: dict[str, str] | None = None
    dataset: str | None = None


def batch_download(
    downloads: list[DownloadTask] | list[tuple[str, Path]],
    *,
    mode: DownloadMode = "skip",
    pause_seconds: float | None = None,
    chunk_size: int | None = None,
    timeout: int | None = None,
    retries: int | None = None,
    show_progress: bool = True,
    source: str,
    dataset: str | None = None,
    expect: Iterable[str] | None = None,
    raw_dir: Path | None = None,
) -> list[DownloadResult]:
    """Download multiple files with optional pause between requests.

    Args:
        downloads: List of downloads, either as DownloadTask objects or (url, path) tuples
        mode: Conditional download mode (see conditional_download)
        pause_seconds: Seconds to pause between downloads (default from config)
        chunk_size: Size of chunks for streaming download
        timeout: Request timeout in seconds
        retries: Number of retry attempts
        show_progress: If True, show download progress bar
        source: Subpackage the files belong to (see `atomic_download`)
        dataset: Dataset tag for every task lacking its own
        expect: Acceptable sniffed content types (see `atomic_download`)
        raw_dir: Root the ledger's ``destination`` is relative to

    Returns:
        List of DownloadResult objects, one per download

    Example:
        >>> downloads = [
        ...     ("https://example.com/file1.zip", Path("data/file1.zip")),
        ...     ("https://example.com/file2.zip", Path("data/file2.zip")),
        ... ]
        >>> results = batch_download(downloads, pause_seconds=2.0, source="example")
        >>> success_count = sum(1 for r in results if r.status == DownloadStatus.SUCCESS)
    """
    pause_seconds = (
        pause_seconds if pause_seconds is not None else MortgageDataConfig.DOWNLOAD_PAUSE
    )

    results: list[DownloadResult] = []

    for i, download in enumerate(downloads):
        if isinstance(download, tuple):
            url, destination = download
            task_headers = None
            task_dataset = dataset
        else:
            task = cast(DownloadTask, download)
            url = task.url
            destination = task.destination
            task_headers = task.headers
            task_dataset = task.dataset or dataset

        logger.info("Downloading [%s/%s]: %s", i + 1, len(downloads), destination.name)

        result = conditional_download(
            url,
            destination,
            mode=mode,
            chunk_size=chunk_size,
            timeout=timeout,
            retries=retries,
            show_progress=show_progress,
            headers=task_headers,
            source=source,
            dataset=task_dataset,
            expect=expect,
            raw_dir=raw_dir,
        )
        results.append(result)

        if result.status == DownloadStatus.SUCCESS:
            logger.info(
                "Downloaded: %s (%s bytes, %s)",
                destination.name,
                f"{result.bytes_downloaded:,}",
                result.sniffed_type,
            )
        elif result.status in SKIPPED_STATUSES:
            logger.info("Skipped: %s (%s)", destination.name, result.status.value)
        else:
            logger.error("Failed: %s - %s", destination.name, result.error_message)

        # Pause between downloads (but not after the last one, and only after real traffic)
        if pause_seconds > 0 and i < len(downloads) - 1 and result.status == DownloadStatus.SUCCESS:
            logger.debug("Pausing %ss before next download...", pause_seconds)
            time.sleep(pause_seconds)

    return results


def summarize_results(results: list[DownloadResult]) -> dict[str, int]:
    """Summarize download results by status.

    Args:
        results: List of DownloadResult objects

    Returns:
        Dictionary mapping status values to counts

    Example:
        >>> summary = summarize_results(results)
        >>> print(f"Success: {summary.get('success', 0)}, Failed: {summary.get('failed', 0)}")
    """
    summary: dict[str, int] = {}
    for result in results:
        status = result.status.value
        summary[status] = summary.get(status, 0) + 1
    return summary
