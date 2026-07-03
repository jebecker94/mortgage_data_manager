"""Common I/O operations for mortgage data manager.

This module provides utilities for downloading files, extracting archives,
and working with various file formats.

Note:
    download_file and extract_zip are re-exported from core.download and
    core.extract for backward compatibility.
"""

from __future__ import annotations

from pathlib import Path

# Re-export download functions for backward compatibility
from mortgage_data_manager.core.download import DownloadStatus, atomic_download


def download_file(
    url: str,
    destination: Path,
    chunk_size: int = 8192,
    verify_ssl: bool = True,
    show_progress: bool = True,
) -> None:
    """Download file from URL with progress tracking.

    .. deprecated::
        Use :func:`atomic_download` from :mod:`core.download` instead.
        This function is provided for backward compatibility.

    Args:
        url: URL to download from
        destination: Path to save the downloaded file
        chunk_size: Size of chunks for streaming download (bytes)
        verify_ssl: If True, verify SSL certificates (ignored in new implementation)
        show_progress: If True, show download progress bar

    Raises:
        requests.HTTPError: If the download fails
        IOError: If unable to write to destination

    Example:
        >>> download_file(
        ...     "https://example.com/data.zip",
        ...     Path("data/raw/data.zip")
        ... )
    """
    result = atomic_download(
        url,
        destination,
        chunk_size=chunk_size,
        show_progress=show_progress,
    )
    if result.status == DownloadStatus.FAILED:
        raise OSError(result.error_message)


def detect_delimiter(file_path: Path, num_lines: int = 5) -> str:
    """Detect delimiter in delimited text file.

    Args:
        file_path: Path to the file
        num_lines: Number of lines to sample for detection

    Returns:
        The detected delimiter character

    Raises:
        csv.Error: If unable to detect delimiter
        IOError: If unable to read file

    Example:
        >>> delimiter = detect_delimiter(Path("data/raw/file.txt"))
        >>> print(f"Detected delimiter: '{delimiter}'")
    """
    import csv

    with open(file_path) as f:
        sample = ''.join([f.readline() for _ in range(num_lines)])

    sniffer = csv.Sniffer()
    dialect = sniffer.sniff(sample)
    return dialect.delimiter


def get_file_size_mb(file_path: Path) -> float:
    """Get file size in megabytes.

    Args:
        file_path: Path to the file

    Returns:
        File size in megabytes

    Example:
        >>> size = get_file_size_mb(Path("data/raw/large_file.parquet"))
        >>> print(f"File size: {size:.2f} MB")
    """
    return file_path.stat().st_size / (1024 * 1024)


def ensure_parent_dir(file_path: Path) -> None:
    """Ensure parent directory of a file exists.

    Args:
        file_path: Path to a file

    Example:
        >>> ensure_parent_dir(Path("data/bronze/hmda/2020.parquet"))
        >>> # Creates data/bronze/hmda/ if it doesn't exist
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)


def list_files_by_pattern(
    directory: Path,
    pattern: str = "*.parquet",
    recursive: bool = True,
) -> list[Path]:
    """List files matching a pattern in a directory.

    Args:
        directory: Directory to search
        pattern: Glob pattern to match (e.g., "*.parquet", "**/*.csv")
        recursive: If True, search recursively

    Returns:
        List of matching file paths

    Example:
        >>> files = list_files_by_pattern(
        ...     Path("data/bronze"),
        ...     pattern="*.parquet",
        ...     recursive=True
        ... )
        >>> print(f"Found {len(files)} parquet files")
    """
    if recursive and not pattern.startswith("**"):
        pattern = f"**/{pattern}"

    return sorted(directory.glob(pattern))


def read_text_file(
    file_path: Path,
    encoding: str = "utf-8",
    errors: str = "strict",
) -> str:
    """Read text file contents.

    Args:
        file_path: Path to the text file
        encoding: Text encoding (default: utf-8)
        errors: How to handle encoding errors ('strict', 'ignore', 'replace')

    Returns:
        File contents as string

    Example:
        >>> content = read_text_file(Path("docs/README.md"))
    """
    with open(file_path, encoding=encoding, errors=errors) as f:
        return f.read()


def write_text_file(
    file_path: Path,
    content: str,
    encoding: str = "utf-8",
) -> None:
    """Write text content to file.

    Args:
        file_path: Path to write to
        content: Text content to write
        encoding: Text encoding (default: utf-8)

    Example:
        >>> write_text_file(
        ...     Path("output/report.txt"),
        ...     "Analysis complete!"
        ... )
    """
    ensure_parent_dir(file_path)

    with open(file_path, 'w', encoding=encoding) as f:
        f.write(content)


def copy_file(source: Path, destination: Path, overwrite: bool = False) -> bool:
    """Copy a file from source to destination.

    Args:
        source: Source file path
        destination: Destination file path
        overwrite: If True, overwrite existing destination file

    Returns:
        True if file was copied, False if skipped (file exists and overwrite=False)

    Example:
        >>> copied = copy_file(
        ...     Path("data/raw/source.csv"),
        ...     Path("data/backup/source.csv"),
        ...     overwrite=False
        ... )
    """
    import shutil

    if not overwrite and destination.exists():
        return False

    ensure_parent_dir(destination)
    shutil.copy2(source, destination)
    return True
