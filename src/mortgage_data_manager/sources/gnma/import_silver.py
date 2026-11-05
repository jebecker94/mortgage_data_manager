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
from mortgage_data_manager.core.medallion import atomic_output, should_process_file

from .canonical import (
    CanonicalSchema,
    record_types_in_file,
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
    bronze_dir: str | Path,
    output_dir: str | Path,
    config: ProcessorConfig | None = None,
    record_types: list[str] | None = None,
    show_progress: bool = True,
) -> ProcessingResult:
    """Transform all bronze files for a prefix to silver via its canonical schema.

    Thin wrapper over `import_prefix_silver_canonical`. The schema is loaded from
    ``schemas/gnma/canonical/{prefix}.json``; a prefix without one cannot be built to
    silver (warns and returns an empty result).

    Args:
        prefix: GNMA file prefix to build.
        bronze_dir: Bronze root (staged parquet) to read the prefix's files from.
        output_dir: Silver root to write ``{prefix}/{record_type}/`` files under.
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
        prefix, bronze_dir, output_dir, canonical_dir, config, record_types, show_progress
    )


# =============================================================================
# Canonical-schema silver writers
# =============================================================================


class _NoRowsError(Exception):
    """Raised inside `atomic_output` so an empty result is discarded, not published."""


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
        Whether a file was written, and the record count written. ``(False, 0)`` when the
        record type is not disclosed for the file's period or has no rows in the file;
        no empty file is written, and one left by an earlier build is removed.

    Raises:
        ValueError: If the schema's ``layout_kind`` is unknown, or the record type is
            disclosed for the period but has no active fields.
    """
    if config is None:
        config = ProcessorConfig()
    ym = bronze_file.stem.split(f"{schema.prefix}_")[1]
    # Record type not disclosed in this period -> nothing to write (not a failure).
    if not schema.rt_present(record_type, ym):
        return False, 0

    if schema.layout_kind == "fixed_width":
        df = transform_fixed_width_canonical(bronze_file, schema, record_type, ym)
    elif schema.layout_kind == "delimited":
        df = transform_delimited_canonical(bronze_file, schema, record_type, ym)
    else:
        raise ValueError(f"Unknown layout_kind {schema.layout_kind!r} for {schema.prefix}")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with atomic_output(output_file) as tmp:
            df.sink_parquet(tmp)
            record_count = pl.scan_parquet(tmp).select(pl.len()).collect().item()
            if record_count == 0:
                raise _NoRowsError
    except _NoRowsError:
        output_file.unlink(missing_ok=True)
        return False, 0
    logger.debug("[canonical] %s -> %s (%s)", bronze_file.name, output_file.name, record_count)
    return True, record_count


def import_prefix_silver_canonical(
    prefix: str,
    bronze_dir: str | Path,
    output_dir: str | Path,
    canonical_dir: str | Path,
    config: ProcessorConfig | None = None,
    record_types: list[str] | None = None,
    show_progress: bool = True,
) -> ProcessingResult:
    """Transform all bronze files for a prefix to silver via its canonical schema.

    Output paths follow ``silver/{prefix}/{rt}/{prefix}_{ym}_{rt}.parquet``. A record type
    not disclosed for a period (its schema ``present`` windows) or with no rows in that
    month's file is skipped, not counted as a failure, and gets no file: one scan of
    each bronze file finds which of its pending record types it holds.

    Args:
        prefix: GNMA file prefix to build.
        bronze_dir: Bronze root to read the prefix's files from.
        output_dir: Silver root to write ``{prefix}/{record_type}/`` files under.
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
    bronze_dir = Path(bronze_dir)
    output_dir = Path(output_dir)
    parquet_files = list(bronze_dir.rglob(f"{prefix}_*.parquet"))
    if not parquet_files:
        logger.warning("No bronze parquet files found for prefix: %s", prefix)
        return result
    result.total_files = len(parquet_files)

    rts = record_types or schema.record_type_codes()
    invalid = [rt for rt in rts if rt not in schema.record_type_codes()]
    if invalid:
        logger.warning("Invalid record types for %s: %s", prefix, invalid)
        rts = [rt for rt in rts if rt in schema.record_type_codes()]

    for n, bronze_file in enumerate(parquet_files, 1):
        if show_progress and n % 10 == 0:
            logger.info("Progress: %s/%s bronze files", n, len(parquet_files))
        ym = bronze_file.stem.split(f"{prefix}_")[1]
        pending: list[tuple[str, Path]] = []
        for rt in rts:
            output_file = output_dir / prefix / rt / f"{prefix}_{ym}_{rt}.parquet"
            if should_process_file(output_file, overwrite=config.overwrite) and schema.rt_present(
                rt, ym
            ):
                pending.append((rt, output_file))
            else:
                result.add_skip(str(bronze_file))
        if not pending:
            continue

        try:
            held = record_types_in_file(bronze_file, schema, [rt for rt, _ in pending], ym)
        except Exception as e:  # noqa: BLE001 - counted as a failure; the batch carries on
            logger.error("[canonical] Error scanning %s: %s", bronze_file, e)
            for rt, _ in pending:
                result.add_failure(str(bronze_file), f"{rt}: {type(e).__name__}: {e}")
            continue

        for rt, output_file in pending:
            if rt not in held:
                output_file.unlink(missing_ok=True)  # an empty file from an earlier build
                result.add_skip(str(bronze_file))
                continue
            try:
                success, record_count = import_file_silver_canonical(
                    bronze_file, schema, rt, output_file, config
                )
            except Exception as e:  # noqa: BLE001 - counted as a failure; the batch carries on
                logger.error("[canonical] Error processing %s (%s): %s", bronze_file, rt, e)
                result.add_failure(str(bronze_file), f"{rt}: {type(e).__name__}: {e}")
                continue
            if success:
                result.add_success(str(bronze_file), str(output_file), record_count, rt)
            else:
                # The scan found rows for this RT, so an empty result is a decode defect.
                result.add_failure(str(bronze_file), f"{rt}: no rows decoded")

    logger.info(
        "Completed %s (canonical): %s/%s successful, %s total records",
        prefix,
        result.successful,
        result.total_attempted,
        result.total_records_processed,
    )
    return result
