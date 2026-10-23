#!/usr/bin/env python3
"""Silver-layer import for GNMA via the canonical schemas.

Transform bronze parquet into typed, structured silver via the canonical schema
(``schemas/gnma/canonical/{prefix}.json``), the only silver path.

Functions:
    - import_prefix_silver: transform all bronze files for a prefix to silver
    - import_prefix_silver_canonical / import_file_silver_canonical: canonical writers
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file

from .canonical import (
    CanonicalSchema,
    transform_delimited_canonical,
    transform_fixed_width_canonical,
)
from .config import ProcessorConfig
from .utils import ProcessingResult

logger = get_logger(__name__)

__all__ = [
    "import_prefix_silver",
    "import_prefix_silver_canonical",
    "import_file_silver_canonical",
]


def import_prefix_silver(
    prefix: str,
    raw_folder: str | Path,
    clean_folder: str | Path,
    config: ProcessorConfig | None = None,
    record_types: list[str] | None = None,
    show_progress: bool = True,
) -> ProcessingResult:
    """Transform all bronze files for a prefix to silver via its canonical schema.

    Thin wrapper over `import_prefix_silver_canonical`. ``raw_folder`` is the bronze
    layer (staged parquet) and ``clean_folder`` the silver output (historical parameter
    names). The schema is loaded from ``schemas/gnma/canonical/{prefix}.json``; a prefix
    without one cannot be built to silver (warns and returns an empty result).

    Args:
        prefix: GNMA file prefix to build.
        raw_folder: The prefix's bronze layer (staged parquet).
        clean_folder: The silver output directory.
        config: Processor configuration; defaults to a fresh ``ProcessorConfig``.
        record_types: Record types to build, or None for every declared type.
        show_progress: Render a progress bar while transforming.

    Returns:
        Per-file processing counts, empty when the prefix has no canonical schema.
    """
    if config is None:
        config = ProcessorConfig()
    canonical_dir = Path(config.schemas_folder) / "canonical"
    if not CanonicalSchema.exists(prefix, canonical_dir):
        logger.warning(
            "No canonical schema for %s (%s/%s.json); cannot build silver.",
            prefix,
            canonical_dir,
            prefix,
        )
        return ProcessingResult()
    return import_prefix_silver_canonical(
        prefix, raw_folder, clean_folder, canonical_dir, config, record_types, show_progress
    )


# =============================================================================
# Canonical-schema silver writers
# =============================================================================


def import_file_silver_canonical(
    bronze_file: Path,
    schema: CanonicalSchema,
    record_type: str,
    output_file: Path,
    config: ProcessorConfig | None = None,
) -> tuple[bool, int]:
    """Transform one bronze file for one record type to silver via the canonical schema.

    Deterministic: resolves the active fields for the file's as-of period, slices
    (fixed-width) or splits (delimited), applies ``decode`` + ``arrow_type``, and sinks
    typed, snake_case-``id``-named parquet.

    Args:
        bronze_file: Path of the bronze parquet to transform.
        schema: The loaded canonical schema.
        record_type: Record-type code to extract.
        output_file: Destination silver parquet path.
        config: Processor configuration; defaults to a fresh ``ProcessorConfig``.

    Returns:
        Whether a file was written, and the record count written.
    """
    if config is None:
        config = ProcessorConfig()
    try:
        ym = bronze_file.stem.split(f"{schema.prefix}_")[1]
        # Record type not disclosed in this period -> nothing to write (not a failure).
        if not schema.rt_present(record_type, ym):
            return False, 0
        if not schema.active_fields(record_type, ym):
            return False, 0

        if schema.layout_kind == "fixed_width":
            df = transform_fixed_width_canonical(bronze_file, schema, record_type, ym)
        elif schema.layout_kind == "delimited":
            df = transform_delimited_canonical(bronze_file, schema, record_type, ym)
        else:
            logger.error("Unknown layout_kind %s for %s", schema.layout_kind, schema.prefix)
            return False, 0

        output_file.parent.mkdir(parents=True, exist_ok=True)
        df.sink_parquet(output_file)
        record_count = pl.scan_parquet(output_file).select(pl.len()).collect().item()
        logger.debug("[canonical] %s -> %s (%s)", bronze_file.name, output_file.name, record_count)
        return True, record_count
    except Exception as e:  # noqa: BLE001 - mirror legacy: report, keep the batch going
        logger.error("[canonical] Error processing %s (%s): %s", bronze_file, record_type, e)
        return False, 0


def import_prefix_silver_canonical(
    prefix: str,
    bronze_folder: str | Path,
    silver_folder: str | Path,
    canonical_dir: str | Path,
    config: ProcessorConfig | None = None,
    record_types: list[str] | None = None,
    show_progress: bool = True,
) -> ProcessingResult:
    """Transform all bronze files for a prefix to silver via its canonical schema.

    Output paths follow ``silver/{prefix}/{rt}/{prefix}_{ym}_{rt}.parquet``. Absent record
    types for a given period are skipped (via ``present``), not counted as failures.

    Args:
        prefix: GNMA file prefix to build.
        bronze_folder: Root of the prefix's bronze layer.
        silver_folder: The silver output directory.
        canonical_dir: Directory holding the canonical schema JSON files.
        config: Processor configuration; defaults to a fresh ``ProcessorConfig``.
        record_types: Record types to build, or None for every declared type.
        show_progress: Render a progress bar while transforming.

    Returns:
        Per-file processing counts across every record type built.
    """
    if config is None:
        config = ProcessorConfig()

    schema = CanonicalSchema.load(prefix, canonical_dir)
    logger.info("Transforming prefix to silver via canonical schema: %s", prefix)

    result = ProcessingResult()
    bronze_folder = Path(bronze_folder)
    silver_folder = Path(silver_folder)
    parquet_files = list(bronze_folder.rglob(f"{prefix}_*.parquet"))
    if not parquet_files:
        logger.warning("No bronze parquet files found for prefix: %s", prefix)
        return result
    result.total_files = len(parquet_files)

    rts = record_types or schema.record_type_codes()
    invalid = [rt for rt in rts if rt not in schema.record_type_codes()]
    if invalid:
        logger.warning("Invalid record types for %s: %s", prefix, invalid)
        rts = [rt for rt in rts if rt in schema.record_type_codes()]

    total_operations = len(parquet_files) * len(rts)
    operation_count = 0
    for bronze_file in parquet_files:
        ym = bronze_file.stem.split(f"{prefix}_")[1]
        for rt in rts:
            operation_count += 1
            if show_progress and operation_count % 50 == 0:
                logger.info("Progress: %s/%s operations", operation_count, total_operations)

            output_file = silver_folder / prefix / rt / f"{prefix}_{ym}_{rt}.parquet"
            if not should_process_file(output_file, overwrite=config.overwrite):
                result.add_skip(str(bronze_file))
                continue

            success, record_count = import_file_silver_canonical(
                bronze_file, schema, rt, output_file, config
            )
            if success:
                result.add_success(str(bronze_file), str(output_file), record_count, rt)
            elif schema.rt_present(rt, ym):
                # Only a real failure if the RT should exist this period.
                result.add_failure(str(bronze_file), f"Failed record type {rt}")
            else:
                result.add_skip(str(bronze_file))

    logger.info(
        "Completed %s (canonical): %s/%s successful, %s total records",
        prefix,
        result.successful,
        result.total_attempted,
        result.total_records_processed,
    )
    return result
