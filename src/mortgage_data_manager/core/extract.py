"""Archive extraction utilities for mortgage data manager.

This module provides functions for extracting various archive formats
(zip, tar, gz, etc.) with consistent interfaces and error handling.

Example:
    >>> from mortgage_data_manager.core.extract import extract_zip, extract_archive
    >>> files = extract_zip(Path("data/raw/archive.zip"), Path("data/raw/extracted"))
    >>> print(f"Extracted {len(files)} files")
"""

from __future__ import annotations

import gzip
import shutil
import tarfile
import zipfile
from pathlib import Path

from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)


def extract_zip(
    zip_path: Path,
    destination: Path,
    remove_after: bool = False,
) -> list[Path]:
    """Extract zip file and return list of extracted files.

    Args:
        zip_path: Path to the zip file
        destination: Directory to extract files into
        remove_after: If True, delete the zip file after extraction

    Returns:
        List of paths to extracted files

    Raises:
        zipfile.BadZipFile: If the file is not a valid zip archive
        IOError: If unable to extract files

    Example:
        >>> extracted = extract_zip(
        ...     Path("data/raw/archive.zip"),
        ...     Path("data/raw/extracted")
        ... )
        >>> print(f"Extracted {len(extracted)} files")
    """
    destination.mkdir(parents=True, exist_ok=True)

    extracted_files = []

    logger.debug(f"Extracting {zip_path} to {destination}")

    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(destination)
        extracted_files = [destination / name for name in zip_ref.namelist()]

    logger.info(f"Extracted {len(extracted_files)} files from {zip_path.name}")

    if remove_after:
        zip_path.unlink()
        logger.debug(f"Removed archive: {zip_path}")

    return extracted_files


def extract_tar(
    tar_path: Path,
    destination: Path,
    remove_after: bool = False,
) -> list[Path]:
    """Extract tar file (including .tar.gz, .tgz) and return list of extracted files.

    Args:
        tar_path: Path to the tar file
        destination: Directory to extract files into
        remove_after: If True, delete the tar file after extraction

    Returns:
        List of paths to extracted files

    Raises:
        tarfile.TarError: If the file is not a valid tar archive
        IOError: If unable to extract files

    Example:
        >>> extracted = extract_tar(
        ...     Path("data/raw/archive.tar.gz"),
        ...     Path("data/raw/extracted")
        ... )
    """
    destination.mkdir(parents=True, exist_ok=True)

    extracted_files = []

    logger.debug(f"Extracting {tar_path} to {destination}")

    with tarfile.open(tar_path, "r:*") as tar_ref:
        # Filter to avoid path traversal attacks
        members = tar_ref.getmembers()
        safe_members = []
        for member in members:
            # Skip members with absolute paths or path traversal
            if member.name.startswith("/") or ".." in member.name:
                logger.warning(f"Skipping potentially unsafe member: {member.name}")
                continue
            safe_members.append(member)

        tar_ref.extractall(destination, members=safe_members)
        extracted_files = [destination / member.name for member in safe_members]

    logger.info(f"Extracted {len(extracted_files)} files from {tar_path.name}")

    if remove_after:
        tar_path.unlink()
        logger.debug(f"Removed archive: {tar_path}")

    return extracted_files


def extract_gzip(
    gzip_path: Path,
    destination: Path | None = None,
    remove_after: bool = False,
) -> Path:
    """Extract a single gzip file.

    Args:
        gzip_path: Path to the gzip file (.gz)
        destination: Output file path. If None, removes .gz extension from input
        remove_after: If True, delete the gzip file after extraction

    Returns:
        Path to the extracted file

    Raises:
        gzip.BadGzipFile: If the file is not a valid gzip archive
        IOError: If unable to extract file

    Example:
        >>> extracted = extract_gzip(Path("data/raw/file.csv.gz"))
        >>> # Returns: data/raw/file.csv
    """
    if destination is None:
        # Remove .gz extension
        if gzip_path.suffix.lower() == ".gz":
            destination = gzip_path.with_suffix("")
        else:
            destination = gzip_path.with_suffix(gzip_path.suffix + ".extracted")

    destination.parent.mkdir(parents=True, exist_ok=True)

    logger.debug(f"Extracting {gzip_path} to {destination}")

    with gzip.open(gzip_path, "rb") as f_in:
        with open(destination, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

    logger.info(f"Extracted {gzip_path.name} to {destination.name}")

    if remove_after:
        gzip_path.unlink()
        logger.debug(f"Removed archive: {gzip_path}")

    return destination


def extract_archive(
    archive_path: Path,
    destination: Path,
    remove_after: bool = False,
) -> list[Path]:
    """Extract an archive based on its file extension.

    Supports: .zip, .tar, .tar.gz, .tgz, .tar.bz2, .gz

    Args:
        archive_path: Path to the archive file
        destination: Directory to extract files into
        remove_after: If True, delete the archive after extraction

    Returns:
        List of paths to extracted files

    Raises:
        ValueError: If the archive format is not supported
        Various extraction errors depending on the format

    Example:
        >>> files = extract_archive(
        ...     Path("data/raw/archive.tar.gz"),
        ...     Path("data/raw/extracted")
        ... )
    """
    suffix = archive_path.suffix.lower()
    suffixes = [s.lower() for s in archive_path.suffixes]

    # Handle compound extensions like .tar.gz
    if ".tar.gz" in "".join(suffixes) or ".tgz" in suffixes:
        return extract_tar(archive_path, destination, remove_after)
    elif ".tar.bz2" in "".join(suffixes):
        return extract_tar(archive_path, destination, remove_after)
    elif ".tar" in suffixes:
        return extract_tar(archive_path, destination, remove_after)
    elif suffix == ".zip":
        return extract_zip(archive_path, destination, remove_after)
    elif suffix == ".gz":
        # Single gzip file
        extracted = extract_gzip(archive_path, destination / archive_path.stem, remove_after)
        return [extracted]
    else:
        raise ValueError(
            f"Unsupported archive format: {archive_path.suffix}. "
            f"Supported formats: .zip, .tar, .tar.gz, .tgz, .tar.bz2, .gz"
        )


def is_archive(path: Path) -> bool:
    """Check if a file is a supported archive format.

    Args:
        path: Path to check

    Returns:
        True if the file is a supported archive format

    Example:
        >>> is_archive(Path("data.zip"))
        True
        >>> is_archive(Path("data.csv"))
        False
    """
    suffix = path.suffix.lower()
    suffixes = [s.lower() for s in path.suffixes]

    archive_extensions = {".zip", ".tar", ".gz", ".tgz"}

    if suffix in archive_extensions or any(s in archive_extensions for s in suffixes):
        return True
    # .bz2 is only extractable as part of a .tar.bz2 compound extension
    # (extract_archive has no bare-bz2 path).
    return suffix == ".bz2" and ".tar" in suffixes
