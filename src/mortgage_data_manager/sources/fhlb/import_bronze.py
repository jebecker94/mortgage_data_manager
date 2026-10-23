"""FHLB bronze layer data imports.

This module handles importing raw FHLB data files to the bronze layer:
- AMA (Acquired Member Assets) loan-level data
- FHLB member institution data
"""

from __future__ import annotations

import re
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.sources.fhlb.canonical import (
    CanonicalSchema,
    transform_named_canonical,
    validate_against_raw,
)
from mortgage_data_manager.sources.fhlb.config import FHLBConfig

logger = get_logger(__name__)


def import_ama_bronze(
    years: list[int], raw_dir: Path, output_dir: Path, overwrite: bool = False
) -> dict[str, int]:
    """Import FHLB AMA (Acquired Member Asset) data to bronze layer.

    Schema-driven: each annual PUDB export is parsed through the canonical AMA schema
    (``schemas/fhlb/canonical/ama.json``), which addresses every field by the header it
    carries in that vintage and pins its dtype — the source is read entirely as text and
    the schema is the only authority on typing, so all vintages land the same layout.
    Each vintage is validated against the schema before it is built; one that stops
    conforming (a new or renamed column) fails loudly instead of being null-filled.

    Handles multiple filename patterns:
    - 2009-2014: ama_pudb_export_1231YY.csv
    - 2015-2022: YYYY_pudb_export_1231YY.csv or YYYY_pudb_export.csv
    - 2023-2024: YYYY-pudb.csv

    Args:
        years: Years to load (e.g., [2009, 2010, 2011])
        raw_dir: Directory containing raw AMA CSV files
        output_dir: Output directory for bronze parquet files
        overwrite: Whether to overwrite existing files

    Returns:
        Summary with 'loaded' and 'skipped' counts
    """
    schema = CanonicalSchema.load("ama", FHLBConfig.FHLB_SCHEMAS_DIR / "canonical")

    loaded = 0
    skipped = 0
    output_dir.mkdir(parents=True, exist_ok=True)

    for year in years:
        year_suffix = str(year)[2:]  # last 2 digits (e.g., "09" for 2009)
        candidates = [
            raw_dir / f"ama_pudb_export_1231{year_suffix}.csv",
            raw_dir / f"{year}_pudb_export_1231{year_suffix}.csv",
            raw_dir / f"{year}_pudb_export.csv",
            raw_dir / f"{year}-pudb.csv",
        ]
        input_file = next((p for p in candidates if p.exists()), None)
        if input_file is None:
            logger.warning("File not found for year %s (tried multiple patterns)", year)
            skipped += 1
            continue

        output_file = output_dir / f"fhlb_ama_{year}.parquet"

        if not should_process_file(output_file, overwrite):
            logger.info("Skipping %s - file exists (use --overwrite to rebuild)", year)
            skipped += 1
            continue

        as_of = str(year)
        problems = validate_against_raw(schema, {as_of: input_file})
        if problems:
            for problem in problems:
                logger.error("Canonical schema validation failed: %s", problem)
            skipped += 1
            continue

        logger.info("Loading FHLB AMA data for %s from %s", year, input_file.name)

        try:
            df = (
                transform_named_canonical(input_file, schema, as_of)
                .with_columns(pl.lit(year, dtype=pl.Int64).alias("data_year"))
                .collect()
            )
            df.write_parquet(output_file)

            logger.info("Saved %s records to %s", f"{len(df):,}", output_file.name)
            loaded += 1

        except Exception as e:
            logger.error("Failed to process %s: %s", year, e)
            skipped += 1

    return {"loaded": loaded, "skipped": skipped}


def _members_year_quarter(filename: str) -> tuple[int, int] | None:
    """Parse ``(year, quarter)`` from a raw members workbook filename.

    Three published patterns: ``Q<q><yyyy>`` (FHLB_Members_Q12020.xlsx),
    ``MMDDYYYY`` (FHLB-Members-09302018.xlsx) and ``MMDDYY`` (FHLB_Members_033114.xls).

    Returns:
        The ``(year, quarter)`` pair, or None when no pattern matches.
    """
    name = filename.upper()
    m = re.search(r"Q(\d)(\d{4})", name)
    if m:
        return int(m.group(2)), int(m.group(1))
    m = re.search(r"[-_](\d{2})\d{2}(\d{4})\.", name)
    if m:
        return int(m.group(2)), (int(m.group(1)) - 1) // 3 + 1
    m = re.search(r"[-_](\d{2})\d{2}(\d{2})\.", name)
    if m:
        yy = int(m.group(2))
        return (2000 + yy if yy < 50 else 1900 + yy), (int(m.group(1)) - 1) // 3 + 1
    return None


def import_members_bronze(
    years: list[int], raw_dir: Path, output_dir: Path, overwrite: bool = False
) -> dict[str, int]:
    """Import FHLB member data to bronze layer.

    Schema-driven: each quarterly workbook is parsed through the canonical members
    schema (``schemas/fhlb/canonical/members.json``), which addresses every field by
    the header it carries in that vintage and pins its dtype — so all quarters land
    the same typed layout despite the 2009Q4-present header drift. Each vintage is
    validated against the schema before it is built; one that stops conforming (a new
    or renamed column) fails loudly instead of being null-filled.

    Args:
        years: Years to process (e.g., [2009, 2010, 2011])
        raw_dir: Directory containing raw FHLB member Excel files
        output_dir: Output directory for bronze parquet files
        overwrite: Whether to overwrite existing files

    Returns:
        Summary with 'loaded' and 'skipped' counts
    """
    schema = CanonicalSchema.load("members", FHLBConfig.FHLB_SCHEMAS_DIR / "canonical")

    loaded = 0
    skipped = 0
    output_dir.mkdir(parents=True, exist_ok=True)

    files: list[tuple[int, int, Path]] = []
    for path in sorted(raw_dir.glob("*.xls*")):
        vintage = _members_year_quarter(path.name)
        if vintage is None:
            logger.warning("Could not parse year/quarter from filename %s", path.name)
            skipped += 1
        elif vintage[0] in years:
            files.append((*vintage, path))

    if not files:
        logger.warning(
            "No FHLB Members files found in %s for years %s-%s", raw_dir, min(years), max(years)
        )
        return {"loaded": 0, "skipped": skipped}

    for year, quarter, path in files:
        output_file = output_dir / f"fhlb_members_{year}_q{quarter}.parquet"

        if not should_process_file(output_file, overwrite):
            logger.info("Skipping %s - file exists (use --overwrite to rebuild)", path.name)
            skipped += 1
            continue

        as_of = f"{year}Q{quarter}"
        problems = validate_against_raw(schema, {as_of: path})
        if problems:
            for problem in problems:
                logger.error("Canonical schema validation failed: %s", problem)
            skipped += 1
            continue

        logger.info("Loading FHLB members from %s", path.name)

        try:
            df = (
                transform_named_canonical(path, schema, as_of)
                .with_columns(
                    pl.lit(path.name).alias("SOURCE FILE"),
                    pl.lit(year, dtype=pl.Int64).alias("YEAR"),
                    pl.lit(quarter, dtype=pl.Int64).alias("QUARTER"),
                )
                .collect()
            )
            df.write_parquet(output_file)

            logger.info("Saved %s records to %s", f"{len(df):,}", output_file.name)
            loaded += 1

        except Exception as e:
            logger.error("Failed to process %s: %s", path.name, e)
            skipped += 1

    return {"loaded": loaded, "skipped": skipped}
