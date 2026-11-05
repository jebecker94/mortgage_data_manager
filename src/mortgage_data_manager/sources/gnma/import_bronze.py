#!/usr/bin/env python3
"""Bronze-layer import for GNMA: raw disclosure files to parquet.

Each raw file becomes one parquet with a single text column holding the file's lines
as published (line endings stripped). A file either converts whole or fails: a ZIP
member that cannot be read fails the archive rather than being left out, and nothing is
published under the final name until the write completes (`atomic_output`).

Functions:
    - import_zip_to_parquet: Convert ZIP file to parquet
    - import_txt_to_parquet: Convert TXT file to parquet
    - import_prefix_bronze: Import all files for a prefix to bronze

"""

from __future__ import annotations

import zipfile
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import atomic_output, should_process_file

from .config import GNMAConfig, ProcessorConfig
from .utils import ConversionResult

logger = get_logger(__name__)

__all__ = [
    "BronzeImportError",
    "import_zip_to_parquet",
    "import_txt_to_parquet",
    "import_prefix_bronze",
]


class BronzeImportError(Exception):
    """A raw GNMA file could not be converted to bronze in full."""


def _get_output_path(
    input_path: Path, output_dir: Path | None = None, raw_dir: Path | None = None
) -> Path:
    """Generate the output path for a converted file, preserving prefix subfolder.

    Args:
        input_path: Input file path
        output_dir: Bronze root to write under (defaults to the bronze layer)
        raw_dir: Raw root ``input_path`` lies under (for the prefix sub-directory)

    Returns:
        Path for output file in the bronze layer
    """
    output_dir = Path(output_dir) if output_dir is not None else GNMAConfig.GNMA_BRONZE_DIR
    name = input_path.with_suffix(".parquet").name
    # Keep the prefix sub-directory: raw/{prefix}/file.zip -> bronze/{prefix}/file.parquet
    if raw_dir is not None and input_path.is_relative_to(raw_dir):
        relative_parts = input_path.relative_to(raw_dir).parts
        if len(relative_parts) > 1:
            return output_dir / relative_parts[0] / name
    return output_dir / name


def _decode(line: bytes, encoding: str, fallback_encoding: str) -> str:
    """One raw line decoded (fallback encoding on failure), line ending stripped."""
    try:
        text = line.decode(encoding)
    except UnicodeDecodeError:
        # Raises in turn if the fallback cannot decode it either: a line is never dropped.
        text = line.decode(fallback_encoding)
    return text.rstrip("\r\n")


def _write_lines(lines: Iterable[bytes], output_path: Path, config: ProcessorConfig) -> int:
    """Stream decoded lines into one single-column parquet, atomically.

    Lines are written in row groups of ``config.chunk_size`` so memory stays bounded by
    one chunk however large the file.

    Returns:
        The number of lines written.

    Raises:
        BronzeImportError: If there are no lines to write.
    """
    column = config.text_column_name
    schema = pa.schema([(column, pa.large_string())])
    total = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_output(output_path) as tmp:
        with pq.ParquetWriter(tmp, schema, compression="zstd") as writer:
            buffer: list[str] = []
            for raw_line in lines:
                buffer.append(_decode(raw_line, config.encoding, config.fallback_encoding))
                if len(buffer) >= config.chunk_size:
                    writer.write_table(pa.table({column: buffer}, schema=schema))
                    total += len(buffer)
                    buffer = []
            if buffer:
                writer.write_table(pa.table({column: buffer}, schema=schema))
                total += len(buffer)
        if total == 0:
            raise BronzeImportError(f"No lines to write to {output_path.name}")
    return total


def import_zip_to_parquet(
    zip_file_path: str | Path,
    output_file_path: str | Path | None = None,
    config: ProcessorConfig | None = None,
    chunk_size: int | None = None,
) -> bool:
    """Convert a ZIP file's text members to one bronze parquet, streaming.

    Every text member (by ``config.supported_extensions``) is read in name order and
    appended; a member that fails to read fails the whole archive.

    Args:
        zip_file_path: Path to the ZIP file
        output_file_path: Optional path for output file
        config: Optional configuration
        chunk_size: Optional override for chunk size

    Returns:
        True once the file is written, or when it exists and ``config.overwrite`` is off.

    Raises:
        BronzeImportError: If the archive holds no text member, or no lines.
    """
    if config is None:
        config = ProcessorConfig()
    if chunk_size is not None:
        config = _with_chunk_size(config, chunk_size)

    zip_path = Path(zip_file_path)
    if output_file_path is None:
        output_path = _get_output_path(zip_path, config.bronze_folder, config.raw_folder)
    else:
        output_path = Path(output_file_path)

    if not should_process_file(output_path, overwrite=config.overwrite):
        logger.debug("Skipping existing file: %s", output_path)
        return True

    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        text_files = [
            name
            for name in zip_ref.namelist()
            if any(name.lower().endswith(ext) for ext in config.supported_extensions)
        ]
        if not text_files:
            raise BronzeImportError(f"No text files found in {zip_path}")

        def lines() -> Iterable[bytes]:
            for text_file in text_files:
                logger.debug("  Extracting: %s", text_file)
                with zip_ref.open(text_file) as member:
                    yield from member

        total = _write_lines(lines(), output_path, config)
    logger.debug("Saved %s lines to %s", total, output_path)
    return True


def import_txt_to_parquet(
    txt_file_path: str | Path,
    output_file_path: str | Path | None = None,
    config: ProcessorConfig | None = None,
    chunk_size: int | None = None,
) -> bool:
    """Convert a TXT file to one bronze parquet, streaming.

    Each line is decoded with ``config.encoding``, falling back to
    ``config.fallback_encoding`` for that line only.

    Args:
        txt_file_path: Path to the TXT file
        output_file_path: Optional path for output file
        config: Optional configuration
        chunk_size: Optional override for chunk size

    Returns:
        True once the file is written, or when it exists and ``config.overwrite`` is off.

    Raises:
        BronzeImportError: If the file holds no lines.
    """
    if config is None:
        config = ProcessorConfig()
    if chunk_size is not None:
        config = _with_chunk_size(config, chunk_size)

    txt_path = Path(txt_file_path)
    if output_file_path is None:
        output_path = _get_output_path(txt_path, config.bronze_folder, config.raw_folder)
    else:
        output_path = Path(output_file_path)

    if not should_process_file(output_path, overwrite=config.overwrite):
        logger.debug("Skipping existing file: %s", output_path)
        return True

    with open(txt_path, "rb") as handle:
        total = _write_lines(handle, output_path, config)
    logger.debug("Saved %s lines to %s", total, output_path)
    return True


def _with_chunk_size(config: ProcessorConfig, chunk_size: int) -> ProcessorConfig:
    """A copy of ``config`` with ``chunk_size`` replaced."""
    return replace(config, chunk_size=chunk_size)


def import_prefix_bronze(
    prefix: str,
    raw_dir: str | Path,
    output_dir: str | Path | None = None,
    config: ProcessorConfig | None = None,
    show_progress: bool = True,
) -> ConversionResult:
    """Import all raw files for a specific prefix to bronze layer.

    A file that fails is counted in ``failed`` with its error, and the batch carries on.

    Args:
        prefix: Data prefix (e.g., 'llmon1', 'monthly')
        raw_dir: Raw root holding the ``{prefix}/`` sub-directory of raw files
        output_dir: Bronze root to write under; defaults to ``config.bronze_folder``
        config: Optional configuration
        show_progress: Whether to show progress updates

    Returns:
        ConversionResult with detailed results; empty (``total`` 0) when the prefix's raw
        folder does not exist.
    """
    if config is None:
        config = ProcessorConfig()

    raw_dir = Path(raw_dir)
    result = ConversionResult()

    prefix_dir = raw_dir / prefix
    if not prefix_dir.exists():
        logger.warning("Prefix directory not found: %s", prefix_dir)
        return result

    out_root = Path(output_dir) if output_dir else config.bronze_folder
    zip_files = sorted(prefix_dir.glob(f"{prefix}_*.zip"))
    txt_files = sorted({*prefix_dir.glob(f"{prefix}_*.txt"), *prefix_dir.glob(f"{prefix}_*.TXT")})
    result.total = len(zip_files) + len(txt_files)
    jobs = [(f, import_zip_to_parquet) for f in zip_files] + [
        (f, import_txt_to_parquet) for f in txt_files
    ]
    for raw_file, convert in jobs:
        output_file = _get_output_path(raw_file, out_root, raw_dir)
        if not should_process_file(output_file, overwrite=config.overwrite):
            result.add_skip(str(raw_file))
            continue
        try:
            convert(raw_file, output_file, config)
        except Exception as e:  # noqa: BLE001 - counted as a failure; the batch carries on
            logger.error("Error converting %s: %s", raw_file, e)
            result.add_failure(str(raw_file), f"{type(e).__name__}: {e}")
        else:
            result.add_success(str(raw_file))

    if show_progress:
        logger.info(
            "Imported %s to bronze: %s/%s successful, %s failed",
            prefix,
            result.successful,
            result.total,
            result.failed,
        )

    return result
