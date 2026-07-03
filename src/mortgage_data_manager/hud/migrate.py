"""Migration between flat and Hive-partitioned storage formats.

Converts between:
    Flat:  data/raw/ZIP_TRACT/ZIP_TRACT_2021_Q1.parquet
    Hive:  data/raw/ZIP_TRACT/year=2021/quarter=1/data.parquet

Hive partitioning enables partition pruning in Polars/DuckDB for faster queries.
"""

from __future__ import annotations

import re
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.hud.config import VALID_CROSSWALK_NAMES, HUDConfig

logger = get_logger(__name__)


def detect_storage_format(crosswalk_dir: Path) -> str:
    """Detect whether a directory uses flat or Hive partitioning.

    Returns:
        "flat", "hive", "empty", or "mixed".
    """
    has_flat = False
    has_hive = False

    flat_pattern = re.compile(r"^[A-Z_]+_\d{4}_Q[1-4]\.parquet$")
    for f in crosswalk_dir.glob("*.parquet"):
        if flat_pattern.match(f.name):
            has_flat = True
            break

    for d in crosswalk_dir.glob("year=*/quarter=*/"):
        if list(d.glob("*.parquet")):
            has_hive = True
            break

    if has_flat and has_hive:
        return "mixed"
    elif has_flat:
        return "flat"
    elif has_hive:
        return "hive"
    else:
        return "empty"


def migrate_crosswalk_type(
    crosswalk_type: str,
    input_dir: Path | None = None,
    output_dir: Path | None = None,
    dry_run: bool = False,
    keep_original: bool = True,
) -> dict[str, int | str]:
    """Migrate a crosswalk type from flat to Hive partitioning.

    Args:
        crosswalk_type: Name of the crosswalk type to migrate.
        input_dir: Source directory for flat files. Defaults to HUD_BRONZE_DIR.
        output_dir: Destination directory for Hive files. Defaults to same as input.
        dry_run: If True, only report what would be done.
        keep_original: If True, keep original flat files after migration.

    Returns:
        Statistics about the migration.
    """
    if input_dir is None:
        input_dir = HUDConfig.HUD_BRONZE_DIR
    if output_dir is None:
        output_dir = input_dir

    source_dir = input_dir / crosswalk_type
    dest_dir = output_dir / crosswalk_type

    if not source_dir.exists():
        return {"error": f"Directory not found: {source_dir}"}

    current_format = detect_storage_format(source_dir)
    if current_format == "empty":
        return {"status": "empty", "migrated": 0}

    if input_dir == output_dir:
        if current_format == "hive":
            return {"status": "already_hive", "migrated": 0}
        elif current_format == "mixed":
            return {"error": "Mixed formats detected - manual intervention required"}

    pattern = re.compile(rf"^{crosswalk_type}_(\d{{4}})_Q([1-4])\.parquet$")
    files_to_migrate = []

    for f in source_dir.glob("*.parquet"):
        match = pattern.match(f.name)
        if match:
            year = int(match.group(1))
            quarter = int(match.group(2))
            files_to_migrate.append((f, year, quarter))

    stats: dict[str, int | str] = {
        "status": "success" if not dry_run else "dry_run",
        "migrated": 0,
        "skipped": 0,
        "errors": 0,
    }

    for filepath, year, quarter in files_to_migrate:
        hive_dir = dest_dir / f"year={year}" / f"quarter={quarter}"
        hive_file = hive_dir / "data.parquet"

        if hive_file.exists():
            stats["skipped"] = int(stats["skipped"]) + 1
            continue

        if dry_run:
            logger.info("Would migrate: %s -> %s/data.parquet", filepath.name, hive_dir)
            stats["migrated"] = int(stats["migrated"]) + 1
            continue

        try:
            df = pl.read_parquet(filepath)

            cols_to_drop = [c for c in ["year", "quarter"] if c in df.columns]
            if cols_to_drop:
                df = df.drop(cols_to_drop)

            hive_dir.mkdir(parents=True, exist_ok=True)
            df.write_parquet(hive_file)

            if not keep_original:
                filepath.unlink()

            stats["migrated"] = int(stats["migrated"]) + 1

        except Exception as e:
            logger.error("Error migrating %s: %s", filepath.name, e)
            stats["errors"] = int(stats["errors"]) + 1

    return stats


def migrate_all(
    input_dir: Path | None = None,
    output_dir: Path | None = None,
    dry_run: bool = False,
    keep_original: bool = True,
) -> dict[str, dict]:
    """Migrate all crosswalk types to Hive partitioning.

    Args:
        input_dir: Source directory for flat files. Defaults to HUD_BRONZE_DIR.
        output_dir: Destination directory for Hive files. Defaults to same as input.
        dry_run: If True, only report what would be done.
        keep_original: If True, keep original flat files.

    Returns:
        Migration results for each crosswalk type.
    """
    if input_dir is None:
        input_dir = HUDConfig.HUD_BRONZE_DIR
    if output_dir is None:
        output_dir = input_dir

    results = {}

    for crosswalk_type in VALID_CROSSWALK_NAMES:
        crosswalk_dir = input_dir / crosswalk_type
        if crosswalk_dir.exists():
            logger.info("Migrating %s...", crosswalk_type)
            results[crosswalk_type] = migrate_crosswalk_type(
                crosswalk_type, input_dir, output_dir, dry_run, keep_original
            )
            logger.info("  %s", results[crosswalk_type])

    return results


def rollback_crosswalk_type(
    crosswalk_type: str,
    data_dir: Path | None = None,
    dry_run: bool = False,
) -> dict[str, int | str]:
    """Rollback from Hive partitioning to flat files.

    Args:
        crosswalk_type: Name of the crosswalk type to rollback.
        data_dir: Base directory for raw data.
        dry_run: If True, only report what would be done.

    Returns:
        Statistics about the rollback.
    """
    if data_dir is None:
        data_dir = HUDConfig.HUD_BRONZE_DIR

    crosswalk_dir = data_dir / crosswalk_type
    if not crosswalk_dir.exists():
        return {"error": f"Directory not found: {crosswalk_dir}"}

    current_format = detect_storage_format(crosswalk_dir)
    if current_format == "flat":
        return {"status": "already_flat", "rolled_back": 0}
    elif current_format == "empty":
        return {"status": "empty", "rolled_back": 0}

    stats: dict[str, int | str] = {
        "status": "success" if not dry_run else "dry_run",
        "rolled_back": 0,
        "errors": 0,
    }

    year_pattern = re.compile(r"^year=(\d{4})$")
    quarter_pattern = re.compile(r"^quarter=([1-4])$")

    for year_dir in crosswalk_dir.iterdir():
        if not year_dir.is_dir():
            continue
        year_match = year_pattern.match(year_dir.name)
        if not year_match:
            continue
        year = int(year_match.group(1))

        for quarter_dir in year_dir.iterdir():
            if not quarter_dir.is_dir():
                continue
            quarter_match = quarter_pattern.match(quarter_dir.name)
            if not quarter_match:
                continue
            quarter = int(quarter_match.group(1))

            hive_file = quarter_dir / "data.parquet"
            if not hive_file.exists():
                continue

            flat_file = crosswalk_dir / f"{crosswalk_type}_{year}_Q{quarter}.parquet"

            if dry_run:
                logger.info("Would rollback: %s -> %s", hive_file, flat_file.name)
                stats["rolled_back"] = int(stats["rolled_back"]) + 1
                continue

            try:
                df = pl.read_parquet(hive_file)
                df = df.with_columns(
                    [
                        pl.lit(year).alias("year"),
                        pl.lit(quarter).alias("quarter"),
                    ]
                )

                df.write_parquet(flat_file)

                hive_file.unlink()
                quarter_dir.rmdir()

                stats["rolled_back"] = int(stats["rolled_back"]) + 1

            except Exception as e:
                logger.error("Error rolling back %s: %s", hive_file, e)
                stats["errors"] = int(stats["errors"]) + 1

        try:
            year_dir.rmdir()
        except OSError:
            pass

    return stats
