"""Load HMDA silver by activity year and release vintage.

HMDA silver is hive-partitioned ``activity_year=YYYY/file_type=X/`` (the pre-2018
panel is partitioned by year alone). A ``file_type`` is one CFPB release of the
same year -- post-2018 ``a`` three-year, ``b`` one-year, ``c`` snapshot, ``e``
modified LAR; pre-2018 ``a`` and ``d`` -- and the releases of one year carry
different schemas, so a read must take exactly one of them. Every ``HMDAIndex``
embeds the release it was numbered under (``2020a_000000001``), which is what lets
a consumer of a crosswalk read the same release the crosswalk was built on.

Two ways to choose the release:

- **best available** -- the most mature release on disk for the year, per
  `FILE_TYPE_PREFERENCE`. What a matcher building from scratch wants.
- **pinned to an index** -- `scan_hmda_for_index` reads exactly the
  partitions a set of ``HMDAIndex`` values came from. What a consumer of a
  crosswalk wants: a different release renumbers every index, so a
  hand-maintained year-to-release table that falls behind joins to nothing.

Partition keys are also stored inside every file, so scans read the file list
directly rather than parsing them from the path.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.sources.hmda.config import (
    HMDA_INDEX_COLUMN,
    HMDAConfig,
    HMDADataset,
    HMDAPeriod,
)
from mortgage_data_manager.sources.hmda.utils import hmda_index_file_type, hmda_index_year

logger = get_logger(__name__)

#: Release vintages per era, most mature first. Keyed by era because the two
#: eras' letters never share a year, and one flat tuple would have to rank ``d``
#: against ``e`` -- an ordering nothing on disk ever needs.
FILE_TYPE_PREFERENCE: dict[HMDAPeriod, tuple[str, ...]] = {
    "post2018": ("a", "b", "c", "e"),
    "period_2007_2017": ("a", "d"),
}


def _period_dir(period: HMDAPeriod, dataset: HMDADataset, root: Path | None) -> Path:
    return HMDAConfig.get_dataset_dir("silver", dataset, period) if root is None else Path(root)


def best_file_type(
    period: HMDAPeriod, year: int, *, dataset: HMDADataset = "loans", root: Path | None = None
) -> str | None:
    """The most mature release present for ``year``.

    Args:
        period: Silver era subfolder.
        year: Activity year.
        dataset: Dataset family.
        root: Period directory to read; defaults to the configured silver layout.

    Returns:
        The release code, or ``None`` when the year is not release-partitioned.

    Raises:
        FileNotFoundError: If the year directory is absent.
        ValueError: If the year has release partitions but none is listed in
            `FILE_TYPE_PREFERENCE` -- a new release letter must be
            classified before it is read.
    """
    year_dir = _period_dir(period, dataset, root) / f"activity_year={year}"
    if not year_dir.is_dir():
        raise FileNotFoundError(f"No HMDA {dataset}/{period} silver for {year}: {year_dir}")
    present = {p.name.split("=", 1)[1] for p in year_dir.glob("file_type=*") if p.is_dir()}
    if not present:
        return None
    preferred = FILE_TYPE_PREFERENCE.get(period, ())
    chosen = next((t for t in preferred if t in present), None)
    if chosen is None:
        raise ValueError(
            f"{year_dir} holds file_type {sorted(present)}, none of which is in "
            f"FILE_TYPE_PREFERENCE[{period!r}] = {preferred}; classify the release first"
        )
    return chosen


def scan_hmda(
    period: HMDAPeriod,
    year: int,
    *,
    dataset: HMDADataset = "loans",
    file_type: str | None = None,
    root: Path | None = None,
) -> pl.LazyFrame:
    """Scan one activity year of HMDA silver from a single release partition.

    Every parquet file in the partition is read. A partition normally holds one
    file, but a rebuild can split it, and reading only the first silently drops
    the rest.

    Args:
        period: Silver era subfolder.
        year: Activity year.
        dataset: Dataset family.
        file_type: Release to read; ``None`` takes the best available.
        root: Period directory to read; defaults to the configured silver layout.

    Returns:
        A lazy frame over every parquet in the chosen release partition.

    Raises:
        FileNotFoundError: If the year, or the requested release, has no parquet files.
    """
    year_dir = _period_dir(period, dataset, root) / f"activity_year={year}"
    if file_type is None:
        file_type = best_file_type(period, year, dataset=dataset, root=root)
    partition = year_dir if file_type is None else year_dir / f"file_type={file_type}"
    files = sorted(partition.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No parquet files under {partition}")
    return pl.scan_parquet(files, hive_partitioning=False)


def scan_hmda_years(
    period: HMDAPeriod,
    min_year: int | None = None,
    max_year: int | None = None,
    *,
    dataset: HMDADataset = "loans",
    file_type: str | None = None,
    root: Path | None = None,
) -> pl.LazyFrame:
    """Scan a span of activity years, one release partition each, aligned by name.

    With both bounds given every year in the span is attempted and absent ones
    are skipped with a warning; with either bound open the span is whatever is on
    disk. Frames concatenate ``diagonal_relaxed`` so a release that adds, drops
    or reorders columns unions with null fill instead of raising.

    Args:
        period: Silver era subfolder.
        min_year: First activity year, inclusive.
        max_year: Last activity year, inclusive.
        dataset: Dataset family.
        file_type: Release to read for every year; ``None`` takes each year's best.
        root: Period directory to read; defaults to the configured silver layout.

    Returns:
        A lazy frame over the span, concatenated ``diagonal_relaxed``.

    Raises:
        FileNotFoundError: If no year in the span has data.
    """
    base = _period_dir(period, dataset, root)
    if min_year is not None and max_year is not None:
        years = list(range(min_year, max_year + 1))
    else:
        present = sorted(
            int(p.name.split("=", 1)[1]) for p in base.glob("activity_year=*") if p.is_dir()
        )
        years = [
            y
            for y in present
            if (min_year is None or y >= min_year) and (max_year is None or y <= max_year)
        ]
    frames = []
    for year in years:
        try:
            frames.append(scan_hmda(period, year, dataset=dataset, file_type=file_type, root=root))
        except FileNotFoundError as exc:
            logger.warning("Skipping HMDA %s/%s %s: %s", dataset, period, year, exc)
    if not frames:
        raise FileNotFoundError(f"No HMDA {dataset}/{period} silver for {min_year}-{max_year}")
    return pl.concat(frames, how="diagonal_relaxed")


def scan_hmda_for_index(
    index: pl.DataFrame | pl.LazyFrame | pl.Series,
    period: HMDAPeriod = "post2018",
    *,
    dataset: HMDADataset = "loans",
    root: Path | None = None,
) -> pl.LazyFrame:
    """Scan exactly the release partitions a set of ``HMDAIndex`` values came from.

    Returns whole partitions, not the indexed rows; filter or join downstream.

    Args:
        index: Frame carrying ``HMDAIndex``, or the column itself.
        period: Silver era subfolder.
        dataset: Dataset family.
        root: Period directory to read; defaults to the configured silver layout.

    Returns:
        A lazy frame over the whole partitions the index names, not the indexed
        rows; filter or join downstream.

    Raises:
        ValueError: If ``index`` names no release.
        FileNotFoundError: If a release the index names is no longer on disk.
    """
    if isinstance(index, pl.Series):
        index = index.to_frame(HMDA_INDEX_COLUMN)
    releases = (
        index.lazy()
        .select(hmda_index_year().alias("year"), hmda_index_file_type().alias("file_type"))
        .drop_nulls()
        .unique()
        .sort("year", "file_type")
        .collect()
    )
    if releases.is_empty():
        raise ValueError("index carries no HMDAIndex values")
    return pl.concat(
        [
            scan_hmda(period, year, dataset=dataset, file_type=file_type, root=root)
            for year, file_type in releases.iter_rows()
        ],
        how="diagonal_relaxed",
    )


__all__ = [
    "FILE_TYPE_PREFERENCE",
    "best_file_type",
    "scan_hmda",
    "scan_hmda_for_index",
    "scan_hmda_years",
]
