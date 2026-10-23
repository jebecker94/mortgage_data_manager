#!/usr/bin/env python3
"""Bronze-layer import for GNMA: raw disclosure files to parquet.

Functions for converting raw data files to parquet format (raw → bronze layer).

Functions:
    - import_zip_to_parquet: Convert ZIP file to parquet
    - import_txt_to_parquet: Convert TXT file to parquet
    - import_prefix_bronze: Import all files for a prefix to bronze
    - import_all_bronze: Import all prefixes to bronze

"""

from __future__ import annotations

import zipfile
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file

from .config import GNMAConfig, ProcessorConfig
from .utils import ConversionResult, load_prefix_dictionary

logger = get_logger(__name__)

__all__ = [
    "import_zip_to_parquet",
    "import_txt_to_parquet",
    "import_prefix_bronze",
    "import_all_bronze",
]


def _get_output_path(
    input_path: Path, output_folder: Path | None = None, raw_folder: Path | None = None
) -> Path:
    """Generate the output path for a converted file, preserving prefix subfolder.

    Staged files are output to the bronze layer by default.

    Args:
        input_path: Input file path
        output_folder: Optional output folder (defaults to bronze layer)
        raw_folder: Optional raw folder (for determining relative structure)

    Returns:
        Path for output file in the bronze layer
    """
    if output_folder is None:
        # Default to bronze layer for staged parquet files
        output_folder = GNMAConfig.GNMA_BRONZE_DIR

    output_folder = Path(output_folder)

    # Maintain the same relative structure: data/gnma/bronze/{prefix}/file.parquet
    if raw_folder is not None:
        try:
            raw_folder = Path(raw_folder)
            relative_parts = input_path.relative_to(raw_folder).parts
            if len(relative_parts) >= 1:
                prefix_folder = output_folder / relative_parts[0]
                return prefix_folder / input_path.with_suffix(".parquet").name
        except Exception:
            pass

    return output_folder / input_path.with_suffix(".parquet").name


def import_zip_to_parquet(
    zip_file_path: str | Path,
    output_file_path: str | Path | None = None,
    config: ProcessorConfig | None = None,
    chunk_size: int | None = None,
) -> bool:
    """Convert a ZIP file containing text files to Parquet format using streaming.

    Args:
        zip_file_path: Path to the ZIP file
        output_file_path: Optional path for output file
        config: Optional configuration
        chunk_size: Optional override for chunk size

    Returns:
        True if conversion successful, False otherwise
    """
    if config is None:
        config = ProcessorConfig()

    zip_path = Path(zip_file_path)

    # Destination
    if output_file_path is None:
        output_path = _get_output_path(zip_path, config.bronze_folder, config.raw_folder)
    else:
        output_path = Path(output_file_path)

    # Skip existing using core medallion utility
    if not should_process_file(output_path, overwrite=config.overwrite):
        logger.debug("Skipping existing file: %s", output_path)
        return True

    text_column_name = config.text_column_name
    encoding = config.encoding
    fallback_encoding = config.fallback_encoding
    supported_extensions = config.supported_extensions
    chunk_size = chunk_size or config.chunk_size

    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            text_files = [
                name
                for name in zip_ref.namelist()
                if any(name.lower().endswith(ext) for ext in supported_extensions)
            ]
            if not text_files:
                logger.warning("No text files found in %s", zip_path)
                return False

            all_chunks = []
            total_rows = 0

            for text_file in text_files:
                logger.debug("  Extracting: %s", text_file)
                try:
                    with zip_ref.open(text_file) as file:
                        buffer = []
                        while True:
                            line = file.readline()
                            if not line:
                                break
                            try:
                                decoded_line = line.decode(encoding).rstrip("\r\n")
                            except UnicodeDecodeError:
                                try:
                                    decoded_line = line.decode(fallback_encoding).rstrip("\r\n")
                                except Exception:
                                    logger.warning("Skipping undecodable line in %s", text_file)
                                    continue
                            buffer.append(decoded_line)
                            if len(buffer) >= chunk_size:
                                df = pl.LazyFrame({text_column_name: buffer})
                                all_chunks.append(df)
                                total_rows += len(buffer)
                                buffer = []
                        if buffer:
                            df = pl.LazyFrame({text_column_name: buffer})
                            all_chunks.append(df)
                            total_rows += len(buffer)
                except Exception as e:
                    logger.error("Error processing %s: %s", text_file, e)
                    continue

            if all_chunks:
                try:
                    combined_df = pl.concat(all_chunks, how="diagonal")
                    combined_df.sink_parquet(output_path)
                    logger.debug("Saved %s lines to %s", total_rows, output_path)
                    return True
                except Exception as e:
                    logger.error("Error combining chunks for %s: %s", zip_path, e)
                    return False
            else:
                logger.warning("No content found in %s", zip_path)
                if output_path.exists():
                    output_path.unlink()
                return False

    except Exception as e:
        logger.error("Error converting %s: %s", zip_path, e)
        if output_path.exists():
            try:
                output_path.unlink()
            except Exception:
                pass
        return False


def import_txt_to_parquet(
    txt_file_path: str | Path,
    output_file_path: str | Path | None = None,
    config: ProcessorConfig | None = None,
    chunk_size: int | None = None,
) -> bool:
    """Convert a TXT file to Parquet format using streaming.

    Args:
        txt_file_path: Path to the TXT file
        output_file_path: Optional path for output file
        config: Optional configuration
        chunk_size: Optional override for chunk size

    Returns:
        True if conversion successful, False otherwise
    """
    if config is None:
        config = ProcessorConfig()

    txt_path = Path(txt_file_path)

    if output_file_path is None:
        output_path = _get_output_path(txt_path, config.bronze_folder, config.raw_folder)
    else:
        output_path = Path(output_file_path)

    if not should_process_file(output_path, overwrite=config.overwrite):
        logger.debug("Skipping existing file: %s", output_path)
        return True

    text_column_name = config.text_column_name
    encoding = config.encoding
    fallback_encoding = config.fallback_encoding
    chunk_size = chunk_size or config.chunk_size

    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        all_chunks = []
        total_rows = 0
        buffer = []
        with open(txt_path, encoding=encoding) as txt_file:
            while True:
                line = txt_file.readline()
                if not line:
                    break
                try:
                    line = line.rstrip("\r\n")
                    buffer.append(line)
                    if len(buffer) >= chunk_size:
                        df = pl.LazyFrame({text_column_name: buffer})
                        all_chunks.append(df)
                        total_rows += len(buffer)
                        buffer = []
                except Exception as e:
                    logger.warning("Error processing line in %s: %s", txt_path, e)
                    continue

        if buffer:
            df = pl.LazyFrame({text_column_name: buffer})
            all_chunks.append(df)
            total_rows += len(buffer)

        if all_chunks:
            try:
                combined_df = pl.concat(all_chunks, how="diagonal")
                combined_df.sink_parquet(output_path)
                logger.debug("Saved %s lines to %s", total_rows, output_path)
                return True
            except Exception as e:
                logger.error("Error combining chunks for %s: %s", txt_path, e)
                return False
        else:
            logger.warning("No content found in %s", txt_path)
            if output_path.exists():
                output_path.unlink()
            return False

    except UnicodeDecodeError:
        # Try with fallback encoding
        try:
            eager_chunks: list[pl.DataFrame] = []
            total_rows = 0
            buffer = []
            with open(txt_path, encoding=fallback_encoding) as txt_file:
                while True:
                    line = txt_file.readline()
                    if not line:
                        break
                    try:
                        line = line.rstrip("\r\n")
                        buffer.append(line)
                        if len(buffer) >= chunk_size:
                            chunk_df = pl.LazyFrame({text_column_name: buffer}).collect()
                            eager_chunks.append(chunk_df)
                            total_rows += len(buffer)
                            buffer = []
                    except Exception as e:
                        logger.warning("Error processing line in %s: %s", txt_path, e)
                        continue
            if buffer:
                chunk_df = pl.LazyFrame({text_column_name: buffer}).collect()
                eager_chunks.append(chunk_df)
                total_rows += len(buffer)
            if eager_chunks:
                try:
                    combined_eager = pl.concat(eager_chunks, how="diagonal")
                    combined_eager.write_parquet(output_path)
                    logger.debug("Saved %s lines to %s", total_rows, output_path)
                    return True
                except Exception as e:
                    logger.error("Error combining chunks for %s: %s", txt_path, e)
                    return False
            else:
                logger.warning("No content found in %s", txt_path)
                if output_path.exists():
                    output_path.unlink()
                return False
        except Exception as e:
            logger.error("Error converting %s with fallback encoding: %s", txt_path, e)
            if output_path.exists():
                try:
                    output_path.unlink()
                except Exception:
                    pass
            return False
    except Exception as e:
        logger.error("Error converting %s: %s", txt_path, e)
        if output_path.exists():
            try:
                output_path.unlink()
            except Exception:
                pass
        return False


def import_prefix_bronze(
    prefix: str,
    base_folder: str | Path,
    output_folder: str | Path | None = None,
    config: ProcessorConfig | None = None,
    show_progress: bool = True,
) -> ConversionResult:
    """Import all raw files for a specific prefix to bronze layer.

    Args:
        prefix: Data prefix (e.g., 'llmon1', 'monthly')
        base_folder: Folder containing raw files
        output_folder: Optional output folder
        config: Optional configuration
        show_progress: Whether to show progress updates

    Returns:
        ConversionResult with detailed results
    """
    if config is None:
        config = ProcessorConfig()

    base_folder = Path(base_folder)

    # Determine prefix folder
    prefix_folder = base_folder / prefix
    if not prefix_folder.exists():
        logger.error("Prefix folder not found: %s", prefix_folder)
        return ConversionResult()

    result = ConversionResult()

    # ZIP files
    zip_files = list(prefix_folder.glob(f"{prefix}_*.zip"))
    result.total = len(zip_files)
    for idx, zip_file in enumerate(zip_files, 1):
        if output_folder:
            output_file = _get_output_path(zip_file, Path(output_folder), base_folder)
        else:
            output_file = _get_output_path(zip_file, config.bronze_folder, base_folder)
        if not should_process_file(output_file, overwrite=config.overwrite):
            result.add_skip(str(zip_file))
            continue
        try:
            success = import_zip_to_parquet(zip_file, output_file, config)
            if success:
                result.add_success(str(zip_file))
            else:
                result.add_failure(str(zip_file), "Conversion returned False")
        except Exception as e:
            result.add_failure(str(zip_file), str(e))

    # TXT files
    txt_files = list(
        {*prefix_folder.glob(f"{prefix}_*.txt"), *prefix_folder.glob(f"{prefix}_*.TXT")}
    )
    result.total += len(txt_files)
    for txt_file in txt_files:
        if output_folder:
            output_file = _get_output_path(txt_file, Path(output_folder), base_folder)
        else:
            output_file = _get_output_path(txt_file, config.bronze_folder, base_folder)
        if not should_process_file(output_file, overwrite=config.overwrite):
            result.add_skip(str(txt_file))
            continue
        try:
            success = import_txt_to_parquet(txt_file, output_file, config)
            if success:
                result.add_success(str(txt_file))
            else:
                result.add_failure(str(txt_file), "Conversion returned False")
        except Exception as e:
            result.add_failure(str(txt_file), str(e))

    if show_progress:
        logger.info(
            "Imported %s to bronze: %s/%s successful", prefix, result.successful, result.total
        )

    return result


def import_all_bronze(
    prefixes: list[str] | None = None,
    base_folder: str | Path | None = None,
    config: ProcessorConfig | None = None,
    exclude_prefixes: list[str] | None = None,
) -> dict[str, ConversionResult]:
    """Import all prefixes to bronze layer.

    Args:
        prefixes: List of prefixes to import (None for all from dictionary)
        base_folder: Folder containing raw files
        config: Optional configuration
        exclude_prefixes: Optional list of prefixes to exclude

    Returns:
        Dictionary mapping prefix to ConversionResult
    """
    if config is None:
        config = ProcessorConfig()

    if base_folder is None:
        # Raw folder is the source of downloaded ZIP/TXT files
        base_folder = config.raw_folder

    base_folder = Path(base_folder)

    # Load prefix dictionary if prefixes not provided
    if prefixes is None:
        prefix_dict = load_prefix_dictionary(config.prefix_file)
        prefixes = list(prefix_dict.keys())

    if exclude_prefixes is None:
        exclude_prefixes = []

    results: dict[str, ConversionResult] = {}

    logger.info("Importing %s prefixes to bronze", len(prefixes))

    for prefix in prefixes:
        if prefix not in exclude_prefixes:
            logger.info("Processing prefix: %s", prefix)
            results[prefix] = import_prefix_bronze(prefix, base_folder, None, config)

    return results
