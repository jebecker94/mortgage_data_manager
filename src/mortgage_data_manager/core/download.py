"""Shared download utilities for mortgage data manager.

This module provides standardized download functionality with retry logic,
atomic file operations, and batch processing capabilities.

Example:
    >>> from mortgage_data_manager.core.download import atomic_download, DownloadResult
    >>> result = atomic_download("https://example.com/data.zip", Path("data/raw/data.zip"))
    >>> print(f"Downloaded {result.bytes_downloaded} bytes")
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

import requests
from rich.progress import DownloadColumn, Progress, TransferSpeedColumn

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)


class DownloadStatus(Enum):
    """Status codes for download operations."""

    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"
    SKIPPED_EXISTS = "skipped_exists"
    SKIPPED_SIZE_MATCH = "skipped_size_match"
    SKIPPED_NOT_MODIFIED = "skipped_not_modified"


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
    """

    path: Path
    status: DownloadStatus
    bytes_downloaded: int = 0
    duration_seconds: float = 0.0
    error_message: str | None = None
    url: str = ""


class DownloadError(Exception):
    """Exception raised when a download operation fails after all retries."""

    def __init__(self, url: str, message: str, original_error: Exception | None = None):
        self.url = url
        self.message = message
        self.original_error = original_error
        super().__init__(f"Failed to download {url}: {message}")


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
    retries = retries if retries is not None else MortgageDataConfig.DOWNLOAD_RETRIES
    timeout = timeout if timeout is not None else MortgageDataConfig.DOWNLOAD_TIMEOUT
    backoff_factor = (
        backoff_factor
        if backoff_factor is not None
        else MortgageDataConfig.DOWNLOAD_BACKOFF_FACTOR
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
            return response
        except requests.exceptions.RequestException as e:
            last_error = e
            if attempt < retries:
                wait_time = backoff_factor ** attempt
                logger.warning(
                    f"Request failed (attempt {attempt + 1}/{retries + 1}): {e}. "
                    f"Retrying in {wait_time:.1f}s..."
                )
                time.sleep(wait_time)
            else:
                logger.error(f"Request failed after {retries + 1} attempts: {e}")

    raise DownloadError(url, str(last_error), last_error)


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
) -> DownloadResult:
    """Download a file atomically (write to temp file, then rename).

    The download is atomic: if it fails partway through, no partial file
    will be left at the destination path.

    Args:
        url: URL to download from
        destination: Path to save the downloaded file
        chunk_size: Size of chunks for streaming download (default from config)
        timeout: Request timeout in seconds (default from config)
        retries: Number of retry attempts (default from config)
        verify_size: If True, verify downloaded size matches Content-Length
        show_progress: If True, show download progress bar
        headers: Additional HTTP headers

    Returns:
        DownloadResult with status and metadata

    Example:
        >>> result = atomic_download(
        ...     "https://example.com/data.zip",
        ...     Path("data/raw/data.zip")
        ... )
        >>> if result.status == DownloadStatus.SUCCESS:
        ...     print(f"Downloaded {result.bytes_downloaded} bytes")
    """
    chunk_size = chunk_size if chunk_size is not None else MortgageDataConfig.DOWNLOAD_CHUNK_SIZE
    timeout = timeout if timeout is not None else MortgageDataConfig.DOWNLOAD_TIMEOUT
    retries = retries if retries is not None else MortgageDataConfig.DOWNLOAD_RETRIES

    start_time = time.time()
    temp_path = destination.with_suffix(destination.suffix + ".tmp")

    try:
        # Ensure parent directory exists
        destination.parent.mkdir(parents=True, exist_ok=True)

        # Make the request
        response = retry_request(
            url,
            retries=retries,
            timeout=timeout,
            headers=headers,
            stream=True,
        )

        total_size = int(response.headers.get("content-length", 0))
        # When Content-Encoding is present (gzip/deflate/br), Content-Length
        # reports the compressed transfer size, but iter_content() yields
        # decompressed bytes. Skip size verification in this case.
        content_encoded = bool(response.headers.get("content-encoding"))
        bytes_downloaded = 0

        # Download with progress bar
        if show_progress and total_size > 0:
            with Progress(
                *Progress.get_default_columns(),
                DownloadColumn(),
                TransferSpeedColumn(),
            ) as progress:
                task = progress.add_task(
                    f"Downloading {destination.name}",
                    total=total_size,
                )

                with open(temp_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=chunk_size):
                        if chunk:
                            f.write(chunk)
                            bytes_downloaded += len(chunk)
                            progress.update(task, advance=len(chunk))
        else:
            with open(temp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        bytes_downloaded += len(chunk)

        # Verify size if requested (skip when content was transfer-encoded)
        if verify_size and total_size > 0 and not content_encoded and bytes_downloaded != total_size:
            temp_path.unlink(missing_ok=True)
            return DownloadResult(
                path=destination,
                status=DownloadStatus.FAILED,
                bytes_downloaded=bytes_downloaded,
                duration_seconds=time.time() - start_time,
                error_message=f"Size mismatch: expected {total_size}, got {bytes_downloaded}",
                url=url,
            )

        # Atomic rename
        temp_path.rename(destination)

        return DownloadResult(
            path=destination,
            status=DownloadStatus.SUCCESS,
            bytes_downloaded=bytes_downloaded,
            duration_seconds=time.time() - start_time,
            url=url,
        )

    except DownloadError as e:
        temp_path.unlink(missing_ok=True)
        return DownloadResult(
            path=destination,
            status=DownloadStatus.FAILED,
            duration_seconds=time.time() - start_time,
            error_message=str(e),
            url=url,
        )
    except Exception as e:
        temp_path.unlink(missing_ok=True)
        return DownloadResult(
            path=destination,
            status=DownloadStatus.FAILED,
            duration_seconds=time.time() - start_time,
            error_message=str(e),
            url=url,
        )


def conditional_download(
    url: str,
    destination: Path,
    *,
    mode: Literal["skip", "overwrite", "if_newer", "if_size_diff"] = "skip",
    chunk_size: int | None = None,
    timeout: int | None = None,
    retries: int | None = None,
    show_progress: bool = True,
    headers: dict[str, str] | None = None,
) -> DownloadResult:
    """Download a file with conditional logic based on existing files.

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

    Returns:
        DownloadResult with status and metadata
    """
    # Handle skip mode
    if mode == "skip" and destination.exists():
        logger.debug(f"Skipping existing file: {destination}")
        return DownloadResult(
            path=destination,
            status=DownloadStatus.SKIPPED_EXISTS,
            url=url,
        )

    # Handle if_size_diff mode
    if mode == "if_size_diff" and destination.exists():
        try:
            response = retry_request(url, method="HEAD", timeout=timeout, retries=retries, headers=headers)
            remote_size = int(response.headers.get("content-length", 0))
            local_size = destination.stat().st_size

            if remote_size > 0 and remote_size == local_size:
                logger.debug(f"Skipping file (size matches): {destination}")
                return DownloadResult(
                    path=destination,
                    status=DownloadStatus.SKIPPED_SIZE_MATCH,
                    url=url,
                )
        except Exception as e:
            logger.warning(f"Could not check remote size, proceeding with download: {e}")

    # Handle if_newer mode
    if mode == "if_newer" and destination.exists():
        try:
            response = retry_request(url, method="HEAD", timeout=timeout, retries=retries, headers=headers)
            last_modified = response.headers.get("last-modified")

            if last_modified:
                from email.utils import parsedate_to_datetime

                remote_time = parsedate_to_datetime(last_modified).timestamp()
                local_time = destination.stat().st_mtime

                if remote_time <= local_time:
                    logger.debug(f"Skipping file (not modified): {destination}")
                    return DownloadResult(
                        path=destination,
                        status=DownloadStatus.SKIPPED_NOT_MODIFIED,
                        url=url,
                    )
        except Exception as e:
            logger.warning(f"Could not check last-modified, proceeding with download: {e}")

    # Proceed with download
    return atomic_download(
        url,
        destination,
        chunk_size=chunk_size,
        timeout=timeout,
        retries=retries,
        show_progress=show_progress,
        headers=headers,
    )


@dataclass
class DownloadTask:
    """A single download task for batch processing.

    Attributes:
        url: URL to download from
        destination: Path to save the downloaded file
        headers: Optional additional HTTP headers for this specific download
    """

    url: str
    destination: Path
    headers: dict[str, str] | None = None


def batch_download(
    downloads: list[DownloadTask] | list[tuple[str, Path]],
    *,
    mode: Literal["skip", "overwrite", "if_newer", "if_size_diff"] = "skip",
    pause_seconds: float | None = None,
    chunk_size: int | None = None,
    timeout: int | None = None,
    retries: int | None = None,
    show_progress: bool = True,
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

    Returns:
        List of DownloadResult objects, one per download

    Example:
        >>> downloads = [
        ...     ("https://example.com/file1.zip", Path("data/file1.zip")),
        ...     ("https://example.com/file2.zip", Path("data/file2.zip")),
        ... ]
        >>> results = batch_download(downloads, pause_seconds=2.0)
        >>> success_count = sum(1 for r in results if r.status == DownloadStatus.SUCCESS)
    """
    pause_seconds = pause_seconds if pause_seconds is not None else MortgageDataConfig.DOWNLOAD_PAUSE

    results: list[DownloadResult] = []

    for i, download in enumerate(downloads):
        # Convert tuple to DownloadTask if needed
        if isinstance(download, tuple):
            url, destination = download
            task_headers = None
        else:
            url = download.url
            destination = download.destination
            task_headers = download.headers

        logger.info(f"Downloading [{i + 1}/{len(downloads)}]: {destination.name}")

        result = conditional_download(
            url,
            destination,
            mode=mode,
            chunk_size=chunk_size,
            timeout=timeout,
            retries=retries,
            show_progress=show_progress,
            headers=task_headers,
        )
        results.append(result)

        # Log result
        if result.status == DownloadStatus.SUCCESS:
            logger.info(f"Downloaded: {destination.name} ({result.bytes_downloaded:,} bytes)")
        elif result.status in (
            DownloadStatus.SKIPPED,
            DownloadStatus.SKIPPED_EXISTS,
            DownloadStatus.SKIPPED_SIZE_MATCH,
            DownloadStatus.SKIPPED_NOT_MODIFIED,
        ):
            logger.info(f"Skipped: {destination.name} ({result.status.value})")
        else:
            logger.error(f"Failed: {destination.name} - {result.error_message}")

        # Pause between downloads (but not after the last one)
        if pause_seconds > 0 and i < len(downloads) - 1:
            # Only pause if we actually downloaded something
            if result.status == DownloadStatus.SUCCESS:
                logger.debug(f"Pausing {pause_seconds}s before next download...")
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
