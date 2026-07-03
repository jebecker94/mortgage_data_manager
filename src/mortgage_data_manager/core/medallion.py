"""Common medallion architecture utilities.

This module provides utilities for working with the medallion architecture
(raw → bronze → silver → gold) used across all subpackages.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl


def should_process_file(
    output_path: Path,
    overwrite: bool = False,
) -> bool:
    """Check if a file should be processed based on existence and overwrite flag.

    Args:
        output_path: Path to the output file
        overwrite: If True, always process. If False, skip existing files.

    Returns:
        True if the file should be processed, False otherwise

    Example:
        >>> if should_process_file(output_path, overwrite=False):
        ...     process_data()
    """
    if overwrite:
        return True
    return not output_path.exists()


def write_hive_partitioned(
    lf: pl.LazyFrame,
    output_dir: Path,
    partition_cols: list[str],
    compression: str = "snappy",
    include_key: bool = False,
    **kwargs: Any,
) -> None:
    """Write LazyFrame as hive-partitioned parquet.

    Args:
        lf: Polars LazyFrame to write
        output_dir: Output directory for partitioned parquet files
        partition_cols: List of column names to partition by
        compression: Compression algorithm ('snappy', 'gzip', 'zstd', etc.)
        include_key: If True, retain the partition columns inside each parquet
            file. Required for downstream readers that scan with
            ``hive_partitioning=False`` and filter on the partition columns.
            If False (default), the partition columns are stripped from the
            data and only encoded in the directory path.
        **kwargs: Additional arguments passed to sink_parquet

    Example:
        >>> df = pl.LazyFrame({
        ...     "year": [2020, 2020, 2021],
        ...     "month": [1, 2, 1],
        ...     "value": [100, 200, 300]
        ... })
        >>> write_hive_partitioned(
        ...     df,
        ...     Path("data/silver"),
        ...     partition_cols=["year", "month"]
        ... )
        >>> # Creates: data/silver/year=2020/month=1/*.parquet
        >>> #          data/silver/year=2020/month=2/*.parquet
        >>> #          data/silver/year=2021/month=1/*.parquet
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # pl.PartitionBy (key=/include_key=) is the current partitioned-sink API; it
    # replaced pl.PartitionByKey (removed in polars 1.42) and the sink_parquet
    # partition_by= kwarg. include_key=True keeps the partition columns inside each
    # file; False strips them to the directory path only. Both produce the standard
    # ``col=value/`` hive layout.
    lf.sink_parquet(
        pl.PartitionBy(str(output_dir), key=partition_cols, include_key=include_key),
        compression=compression,
        mkdir=True,
        **kwargs,
    )


def read_medallion_layer(
    layer_dir: Path, glob_pattern: str = "**/*.parquet", **kwargs: Any
) -> pl.LazyFrame:
    """Read all parquet files from a medallion layer.

    Args:
        layer_dir: Path to the medallion layer directory
        glob_pattern: Glob pattern for matching files (default: all parquet)
        **kwargs: Additional arguments passed to scan_parquet

    Returns:
        Polars LazyFrame with data from all matching files

    Example:
        >>> df = read_medallion_layer(
        ...     Path("data/hmda/silver"),
        ...     glob_pattern="activity_year=2020/**/*.parquet"
        ... )
    """
    return pl.scan_parquet(layer_dir / glob_pattern, **kwargs)


def get_partitioned_path(
    base_dir: Path,
    partitions: dict[str, str | int],
) -> Path:
    """Get path with hive-style partitioning.

    Args:
        base_dir: Base directory
        partitions: Dictionary of partition column names to values

    Returns:
        Path with hive-style partitioning

    Example:
        >>> get_partitioned_path(
        ...     Path("data/silver"),
        ...     {"year": 2020, "month": 6}
        ... )
        PosixPath('data/silver/year=2020/month=6')
    """
    path = base_dir
    for col, value in partitions.items():
        path = path / f"{col}={value}"
    return path


def validate_medallion_layer(
    layer_dir: Path,
    expected_columns: list[str] | None = None,
    check_empty: bool = True,
) -> tuple[bool, str]:
    """Validate a medallion layer directory.

    Args:
        layer_dir: Path to the medallion layer directory
        expected_columns: If provided, check that all files have these columns
        check_empty: If True, validate that layer is not empty

    Returns:
        Tuple of (is_valid, message)

    Example:
        >>> is_valid, msg = validate_medallion_layer(
        ...     Path("data/hmda/silver"),
        ...     expected_columns=["lei", "loan_amount"]
        ... )
        >>> if not is_valid:
        ...     print(f"Validation failed: {msg}")
    """
    if not layer_dir.exists():
        return False, f"Layer directory does not exist: {layer_dir}"

    parquet_files = list(layer_dir.glob("**/*.parquet"))

    if check_empty and len(parquet_files) == 0:
        return False, f"Layer directory is empty: {layer_dir}"

    if expected_columns and parquet_files:
        # Check first file for expected columns
        try:
            df = pl.scan_parquet(parquet_files[0])
            actual_columns = set(df.columns)
            expected_set = set(expected_columns)
            missing = expected_set - actual_columns

            if missing:
                return False, f"Missing expected columns: {missing}"

        except Exception as e:
            return False, f"Error reading parquet file: {e}"

    return True, "Validation passed"


def count_records_in_layer(layer_dir: Path) -> int:
    """Count total records in a medallion layer.

    Args:
        layer_dir: Path to the medallion layer directory

    Returns:
        Total number of records across all parquet files

    Example:
        >>> count = count_records_in_layer(Path("data/hmda/silver"))
        >>> print(f"Total records: {count:,}")
    """
    try:
        lf = read_medallion_layer(layer_dir)
        return lf.select(pl.count()).collect().item()
    except Exception:
        return 0


def get_layer_file_stats(layer_dir: Path) -> dict[str, Any]:
    """Get statistics about files in a medallion layer.

    Args:
        layer_dir: Path to the medallion layer directory

    Returns:
        Dictionary with file statistics

    Example:
        >>> stats = get_layer_file_stats(Path("data/hmda/silver"))
        >>> print(f"Total files: {stats['num_files']}")
        >>> print(f"Total size: {stats['total_size_mb']:.2f} MB")
    """
    parquet_files = list(layer_dir.glob("**/*.parquet"))

    total_size = sum(f.stat().st_size for f in parquet_files)

    return {
        "num_files": len(parquet_files),
        "total_size_bytes": total_size,
        "total_size_mb": total_size / (1024 * 1024),
        "total_size_gb": total_size / (1024 * 1024 * 1024),
    }
