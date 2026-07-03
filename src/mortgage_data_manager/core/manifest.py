"""Master data manifest/catalog system.

This module provides a centralized manifest system that catalogs all data files
across all subpackages and medallion layers, providing visibility into data
coverage, file metadata, and completeness.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)

# Standard medallion subpackages (excludes matching/ workflow data)
# Mortgage subpackages (Bucket A). Regulatory sources (fca/fdic/ffiec/frb/ncua/
# sec/…) live in the mortgage-data-regulatory package and carry their own manifest.
STANDARD_SUBPACKAGES = [
    "fha",
    "fhfa",
    "fhlb",
    "fhlmc",
    "fnma",
    "gnma",
    "hmda",
    "umbs",
]

MEDALLION_LAYERS = ["raw", "bronze", "silver", "gold"]


class ManifestConfig(MortgageDataConfig):
    """Configuration for the manifest system.

    Extends the base MortgageDataConfig with manifest-specific paths.
    """

    MANIFEST_DIR: Path = MortgageDataConfig.DATA_DIR / "manifest"
    MANIFEST_FILE: Path = MANIFEST_DIR / "catalog.parquet"
    MANIFEST_METADATA_FILE: Path = MANIFEST_DIR / "catalog_metadata.json"


def get_parquet_record_count(path: Path) -> int | None:
    """Get record count from parquet file using PyArrow metadata.

    This is a fast operation that reads only the file metadata,
    not the actual data (sub-millisecond performance).

    Args:
        path: Path to a parquet file

    Returns:
        Number of rows in the file, or None if unable to read
    """
    try:
        import pyarrow.parquet as pq

        metadata = pq.read_metadata(path)
        return metadata.num_rows
    except Exception as e:
        logger.debug(f"Could not read parquet metadata from {path}: {e}")
        return None


def parse_hive_partitions(path: Path, base_dir: Path) -> dict[str, str] | None:
    """Parse Hive-style partition values from a file path.

    Args:
        path: Path to a file within a Hive-partitioned directory
        base_dir: Base directory of the partitioned data

    Returns:
        Dictionary of partition column names to values, or None if no partitions found

    Example:
        >>> parse_hive_partitions(
        ...     Path("data/hmda/silver/activity_year=2020/state=CA/data.parquet"),
        ...     Path("data/hmda/silver")
        ... )
        {"activity_year": "2020", "state": "CA"}
    """
    try:
        rel_path = path.relative_to(base_dir)
    except ValueError:
        return None

    partitions = {}
    partition_pattern = re.compile(r"^([^=]+)=(.+)$")

    for part in rel_path.parts[:-1]:  # Exclude filename
        match = partition_pattern.match(part)
        if match:
            partitions[match.group(1)] = match.group(2)

    return partitions if partitions else None


def human_readable_size(size_bytes: int) -> str:
    """Convert a file size in bytes to a human-friendly string."""
    if size_bytes == 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def discover_files_in_layer(
    layer_dir: Path,
    subpackage: str,
    layer: str,
    data_dir: Path,
) -> list[dict[str, Any]]:
    """Discover all files in a medallion layer directory.

    Args:
        layer_dir: Path to the layer directory
        subpackage: Name of the subpackage
        layer: Medallion layer name (raw, bronze, silver, gold)
        data_dir: Base data directory for relative path calculation

    Returns:
        List of file record dictionaries
    """
    if not layer_dir.exists():
        return []

    records = []
    scan_timestamp = datetime.now(UTC)

    for path in layer_dir.rglob("*"):
        if not path.is_file():
            continue

        try:
            stats = path.stat()
        except OSError as e:
            logger.warning(f"Could not stat file {path}: {e}")
            continue

        # Get record count for parquet files
        record_count = None
        if path.suffix.lower() == ".parquet":
            record_count = get_parquet_record_count(path)

        # Parse Hive partitions
        partitions = parse_hive_partitions(path, layer_dir)

        try:
            relative_path = str(path.relative_to(data_dir))
        except ValueError:
            relative_path = str(path)

        record = {
            "subpackage": subpackage,
            "medallion_layer": layer,
            "relative_path": relative_path,
            "absolute_path": str(path),
            "file_name": path.name,
            "file_extension": path.suffix.lower(),
            "file_size_bytes": stats.st_size,
            "file_size_mb": round(stats.st_size / (1024**2), 4),
            "record_count": record_count,
            "modified_timestamp": datetime.fromtimestamp(stats.st_mtime, tz=UTC),
            "partition_keys": json.dumps(partitions) if partitions else None,
            "scan_timestamp": scan_timestamp,
        }
        records.append(record)

    return records


def discover_all_files(
    data_dir: Path | None = None,
    subpackages: list[str] | None = None,
    layers: list[str] | None = None,
    include_record_counts: bool = True,
) -> list[dict[str, Any]]:
    """Discover all files across all subpackages and medallion layers.

    Args:
        data_dir: Base data directory. Defaults to ManifestConfig.DATA_DIR
        subpackages: List of subpackages to scan. Defaults to STANDARD_SUBPACKAGES
        layers: List of medallion layers to scan. Defaults to MEDALLION_LAYERS
        include_record_counts: Whether to include record counts for parquet files

    Returns:
        List of file record dictionaries
    """
    if data_dir is None:
        data_dir = ManifestConfig.DATA_DIR

    if subpackages is None:
        subpackages = STANDARD_SUBPACKAGES

    if layers is None:
        layers = MEDALLION_LAYERS

    all_records = []

    for subpackage in subpackages:
        subpackage_dir = data_dir / subpackage

        if not subpackage_dir.exists():
            logger.debug(f"Subpackage directory does not exist: {subpackage_dir}")
            continue

        for layer in layers:
            layer_dir = subpackage_dir / layer

            if not layer_dir.exists():
                logger.debug(f"Layer directory does not exist: {layer_dir}")
                continue

            logger.info(f"Scanning {subpackage}/{layer}...")
            records = discover_files_in_layer(layer_dir, subpackage, layer, data_dir)
            all_records.extend(records)
            logger.debug(f"Found {len(records)} files in {subpackage}/{layer}")

    return all_records


def build_manifest(
    data_dir: Path | None = None,
    subpackages: list[str] | None = None,
    include_record_counts: bool = True,
) -> pl.DataFrame:
    """Build a manifest DataFrame from all discovered files.

    Args:
        data_dir: Base data directory. Defaults to ManifestConfig.DATA_DIR
        subpackages: List of subpackages to scan. Defaults to STANDARD_SUBPACKAGES
        include_record_counts: Whether to include record counts for parquet files

    Returns:
        Polars DataFrame with manifest data
    """
    records = discover_all_files(
        data_dir=data_dir,
        subpackages=subpackages,
        include_record_counts=include_record_counts,
    )

    # Define explicit schema to handle mixed None/int values
    schema = {
        "subpackage": pl.Utf8,
        "medallion_layer": pl.Utf8,
        "relative_path": pl.Utf8,
        "absolute_path": pl.Utf8,
        "file_name": pl.Utf8,
        "file_extension": pl.Utf8,
        "file_size_bytes": pl.Int64,
        "file_size_mb": pl.Float64,
        "record_count": pl.Int64,
        "modified_timestamp": pl.Datetime("us", "UTC"),
        "partition_keys": pl.Utf8,
        "scan_timestamp": pl.Datetime("us", "UTC"),
    }

    if not records:
        # Return empty DataFrame with correct schema
        return pl.DataFrame(schema={k: v for k, v in schema.items()})

    df = pl.DataFrame(records, schema=schema)

    return df.sort(["subpackage", "medallion_layer", "relative_path"])


def save_manifest(df: pl.DataFrame, manifest_file: Path | None = None) -> Path:
    """Save manifest DataFrame to parquet file with metadata.

    Args:
        df: Manifest DataFrame
        manifest_file: Path to save manifest. Defaults to ManifestConfig.MANIFEST_FILE

    Returns:
        Path to saved manifest file
    """
    if manifest_file is None:
        manifest_file = ManifestConfig.MANIFEST_FILE

    manifest_file.parent.mkdir(parents=True, exist_ok=True)

    # Save parquet
    df.write_parquet(manifest_file)
    logger.info(f"Saved manifest to {manifest_file}")

    # Save metadata
    metadata = {
        "scan_timestamp": datetime.now(UTC).isoformat(),
        "total_files": len(df),
        "total_size_bytes": df["file_size_bytes"].sum(),
        "total_records": df["record_count"].sum() if df["record_count"].null_count() < len(df) else None,
        "subpackages_scanned": df["subpackage"].unique().to_list(),
        "version": "1.0",
    }

    metadata_file = manifest_file.parent / "catalog_metadata.json"
    with open(metadata_file, "w") as f:
        json.dump(metadata, f, indent=2, default=str)
    logger.info(f"Saved metadata to {metadata_file}")

    return manifest_file


def load_manifest(manifest_file: Path | None = None) -> pl.DataFrame | None:
    """Load manifest DataFrame from parquet file.

    Args:
        manifest_file: Path to manifest file. Defaults to ManifestConfig.MANIFEST_FILE

    Returns:
        Manifest DataFrame, or None if file doesn't exist
    """
    if manifest_file is None:
        manifest_file = ManifestConfig.MANIFEST_FILE

    if not manifest_file.exists():
        logger.warning(f"Manifest file does not exist: {manifest_file}")
        return None

    return pl.read_parquet(manifest_file)


def load_manifest_metadata(manifest_file: Path | None = None) -> dict[str, Any] | None:
    """Load manifest metadata from JSON file.

    Args:
        manifest_file: Path to manifest file. Defaults to ManifestConfig.MANIFEST_FILE

    Returns:
        Metadata dictionary, or None if file doesn't exist
    """
    if manifest_file is None:
        manifest_file = ManifestConfig.MANIFEST_FILE

    metadata_file = manifest_file.parent / "catalog_metadata.json"

    if not metadata_file.exists():
        return None

    with open(metadata_file) as f:
        return json.load(f)


def update_manifest_incremental(
    existing_df: pl.DataFrame | None = None,
    data_dir: Path | None = None,
) -> pl.DataFrame:
    """Update manifest incrementally based on file modification times.

    Only rescans files that have been modified since the last scan.

    Args:
        existing_df: Existing manifest DataFrame. If None, loads from default location.
        data_dir: Base data directory

    Returns:
        Updated manifest DataFrame
    """
    if existing_df is None:
        existing_df = load_manifest()

    if existing_df is None or len(existing_df) == 0:
        # No existing manifest, do full scan
        logger.info("No existing manifest, performing full scan")
        return build_manifest(data_dir=data_dir)

    if data_dir is None:
        data_dir = ManifestConfig.DATA_DIR

    # Check each file to see if it needs to be rescanned
    updated_records = []
    unchanged_count = 0

    for row in existing_df.iter_rows(named=True):
        path = Path(row["absolute_path"])

        if not path.exists():
            # File was deleted, skip it
            logger.debug(f"File no longer exists: {path}")
            continue

        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError:
            continue

        if mtime > row["scan_timestamp"]:
            # File was modified, rescan it
            logger.debug(f"Rescanning modified file: {path}")
            stats = path.stat()

            record_count = None
            if path.suffix.lower() == ".parquet":
                record_count = get_parquet_record_count(path)

            layer_dir = data_dir / row["subpackage"] / row["medallion_layer"]
            partitions = parse_hive_partitions(path, layer_dir)

            updated_record = {
                **row,
                "file_size_bytes": stats.st_size,
                "file_size_mb": round(stats.st_size / (1024**2), 4),
                "record_count": record_count,
                "modified_timestamp": mtime,
                "partition_keys": json.dumps(partitions) if partitions else None,
                "scan_timestamp": datetime.now(UTC),
            }
            updated_records.append(updated_record)
        else:
            # File unchanged, keep existing record
            updated_records.append(row)
            unchanged_count += 1

    logger.info(f"Kept {unchanged_count} unchanged files")

    # Check for new files
    all_current_paths = {Path(r["absolute_path"]) for r in updated_records}
    new_files = discover_all_files(data_dir=data_dir)
    new_file_records = [
        r for r in new_files if Path(r["absolute_path"]) not in all_current_paths
    ]

    if new_file_records:
        logger.info(f"Found {len(new_file_records)} new files")
        updated_records.extend(new_file_records)

    if not updated_records:
        return build_manifest(data_dir=data_dir)

    df = pl.DataFrame(updated_records)
    return df.sort(["subpackage", "medallion_layer", "relative_path"])


def generate_summary(df: pl.DataFrame) -> dict[str, Any]:
    """Generate summary statistics from manifest DataFrame.

    Args:
        df: Manifest DataFrame

    Returns:
        Dictionary with summary statistics
    """
    if len(df) == 0:
        return {
            "total_files": 0,
            "total_size_bytes": 0,
            "total_size_mb": 0.0,
            "total_size_gb": 0.0,
            "total_records": 0,
            "subpackage_summaries": {},
        }

    total_size = df["file_size_bytes"].sum()
    total_records = df["record_count"].sum()

    # Per-subpackage, per-layer summaries
    subpackage_summaries = {}

    for subpackage in df["subpackage"].unique().to_list():
        sub_df = df.filter(pl.col("subpackage") == subpackage)

        layer_stats = {}
        for layer in MEDALLION_LAYERS:
            layer_df = sub_df.filter(pl.col("medallion_layer") == layer)
            if len(layer_df) > 0:
                layer_stats[layer] = {
                    "file_count": len(layer_df),
                    "size_bytes": layer_df["file_size_bytes"].sum(),
                    "size_mb": round(layer_df["file_size_bytes"].sum() / (1024**2), 2),
                    "record_count": layer_df["record_count"].sum(),
                }
            else:
                layer_stats[layer] = {
                    "file_count": 0,
                    "size_bytes": 0,
                    "size_mb": 0.0,
                    "record_count": 0,
                }

        subpackage_summaries[subpackage] = {
            "total_files": len(sub_df),
            "total_size_bytes": sub_df["file_size_bytes"].sum(),
            "total_size_mb": round(sub_df["file_size_bytes"].sum() / (1024**2), 2),
            "total_size_gb": round(sub_df["file_size_bytes"].sum() / (1024**3), 2),
            "total_records": sub_df["record_count"].sum(),
            "layers": layer_stats,
        }

    return {
        "total_files": len(df),
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / (1024**2), 2),
        "total_size_gb": round(total_size / (1024**3), 2),
        "total_records": total_records,
        "subpackage_summaries": subpackage_summaries,
    }


def format_record_count(count: int | None) -> str:
    """Format a record count for display.

    Args:
        count: Record count or None

    Returns:
        Formatted string
    """
    if count is None or count == 0:
        return "-"
    return f"{count:,}"


def get_layer_status_indicator(file_count: int) -> tuple[str, str]:
    """Get status indicator for a layer.

    Args:
        file_count: Number of files in the layer

    Returns:
        Tuple of (indicator, style) for Rich formatting
    """
    if file_count > 0:
        return f"[green]\u2713[/green] {file_count}", "green"
    else:
        return "[dim]\u25cb 0[/dim]", "dim"
