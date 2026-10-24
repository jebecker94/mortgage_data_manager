"""Common medallion architecture utilities.

This module provides utilities for working with the medallion architecture
(raw → bronze → silver → gold) used across all subpackages.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl

if TYPE_CHECKING:
    from polars._typing import ParquetCompression


@contextmanager
def atomic_output(path: Path) -> Iterator[Path]:
    """Yield a sibling temp path to write to; publish it under ``path`` on success.

    A writer killed mid-write must never leave a partial file under the final
    name: ``should_process_file`` would skip it forever after, so the partial
    becomes permanent silently. Writing to ``<name>.tmp`` in the same
    directory and renaming on success makes the final name appear only when the
    file is complete — the rename is atomic on a same-filesystem sibling. On
    failure the temp file is removed; a stale temp from a killed run is removed
    on the next attempt. Single-writer per output path, like every builder here.

    Args:
        path: Final destination path to publish to on success.

    Yields:
        The sibling temp path to write to.

    Example:
        >>> with atomic_output(save_file) as tmp:
        ...     lf.sink_parquet(tmp)
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    try:
        yield tmp
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.rename(path)


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


def clear_partitions(root: Path, partitions: Iterable[str | Path]) -> int:
    """Delete the partition subtrees about to be rebuilt, and nothing else.

    The one home of the partition-scoped overwrite rule: every target must lie
    strictly below ``root``, so an overwrite can never wipe the output root and
    with it the partitions outside the requested range.

    Args:
        root: The partitioned output directory (e.g. a silver dataset folder).
        partitions: Partition paths relative to ``root`` (``"activity_year=2020"``,
            ``"Year=2010/Month=5"``) or absolute paths beneath it.

    Returns:
        The number of partition directories removed.

    Raises:
        ValueError: If a target is ``root`` itself or lies outside it.
    """
    base = root.resolve()
    removed = 0
    for partition in partitions:
        target = (root / partition).resolve()
        if target == base or not target.is_relative_to(base):
            raise ValueError(f"{target} is not a partition below {root}")
        if target.is_dir():
            shutil.rmtree(target)
            removed += 1
    return removed


def recreate_dir(path: Path) -> Path:
    """Delete ``path`` if present and create it empty, for outputs rebuilt in full.

    Args:
        path: A directory the caller rewrites from scratch (never a silver root
            built by year range; use `clear_partitions` there).

    Returns:
        ``path``, now an empty directory.
    """
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def write_hive_partitioned(
    lf: pl.LazyFrame,
    output_dir: Path,
    partition_cols: list[str],
    compression: ParquetCompression = "snappy",
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

    # Sink into a hidden sibling temp tree, then move each produced file into
    # place with an atomic per-file rename. A run killed mid-sink therefore
    # leaves only the temp tree (removed on the next attempt) — never a
    # partial parquet under a final partition path, which existence-based
    # skip logic would otherwise treat as complete forever after. A kill
    # mid-move can leave some partitions published and others absent, but
    # every published file is complete, so a re-run rebuilds only what is
    # genuinely missing. Single writer per output_dir, like every builder.
    tmp_dir = output_dir.parent / f".{output_dir.name}.tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)

    # pl.PartitionBy (key=/include_key=) is the current partitioned-sink API; it
    # replaced pl.PartitionByKey (removed in polars 1.42) and the sink_parquet
    # partition_by= kwarg. include_key=True keeps the partition columns inside each
    # file; False strips them to the directory path only. Both produce the standard
    # ``col=value/`` hive layout.
    try:
        lf.sink_parquet(
            pl.PartitionBy(str(tmp_dir), key=partition_cols, include_key=include_key),
            compression=compression,
            mkdir=True,
            **kwargs,
        )
        for produced in sorted(tmp_dir.rglob("*.parquet")):
            final = output_dir / produced.relative_to(tmp_dir)
            final.parent.mkdir(parents=True, exist_ok=True)
            produced.rename(final)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
