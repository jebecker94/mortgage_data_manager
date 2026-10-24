"""Silver layer import: Enrich bronze data and create hive-partitioned output."""

from __future__ import annotations

import datetime
import re
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import clear_partitions, write_hive_partitioned
from mortgage_data_manager.core.types import SilverSpec, apply_silver_types, enforce_schema

from .config import HECM_SILVER, SINGLE_FAMILY_SILVER, SnapshotType
from .utils import add_county_fips

logger = get_logger(__name__)

_SNAPSHOT_FILENAME_PATTERN = re.compile(r"(\d{4})(\d{2})(\d{2})")

_SILVER_SPECS: dict[SnapshotType, SilverSpec] = {
    "single_family": SINGLE_FAMILY_SILVER,
    "hecm": HECM_SILVER,
}


def _prepare_snapshot_export(
    frames: Sequence[pl.LazyFrame],
    *,
    file_type: SnapshotType,
) -> pl.LazyFrame:
    """Apply shared export transformations to a collection of snapshot frames."""
    if not frames:
        msg = "No snapshot frames provided for export"
        raise ValueError(msg)

    df = pl.concat(frames, how="diagonal_relaxed")

    # Absence is null. The string spellings of a null ("nan", "None") and the
    # empty string collapse to it; a genuine null falls through the otherwise
    # branch (is_in() on a null yields null) and stays null. Storing "" here
    # defeated is_null()/null_count()/drop_nulls() and disagreed with the
    # adjacent Sponsor Number, which was already null for the same records.
    df = df.with_columns(
        [
            pl.when(pl.col(c).is_in(["nan", "None", ""]))
            .then(pl.lit(None, dtype=pl.String))
            .otherwise(pl.col(c))
            .alias(c)
            for c in ("Originating Mortgagee", "Sponsor Name")
        ]
    )

    # Not optional: add_county_fips is also the only caller of
    # standardize_county_names, so skipping it would leave Property County in
    # its raw per-vintage spellings ("DE KALB", "ST JOHN THE BAPTIST") as well
    # as FIPS unset -- a silently different column, written to the same
    # partition tree.
    df = add_county_fips(df)

    df = df.with_columns(
        pl.concat_str(
            [
                pl.col("Year").cast(pl.Utf8).str.zfill(4),
                pl.col("Month").cast(pl.Utf8).str.zfill(2),
            ],
            separator="-",
        )
        .str.to_datetime(format="%Y-%m", strict=False)
        .alias("Date")
    )

    if file_type == "hecm" and "Rate Type" in df.collect_schema().names():
        df = df.with_columns(
            pl.when(pl.col("Rate Type") == "Fixed")
            .then(pl.lit("Fixed Rate"))
            .otherwise(pl.col("Rate Type"))
            .alias("Rate Type")
        )

    if file_type == "single_family":
        # The 2014-08 snapshot's sponsor names are unusable; wipe them to null,
        # the same "absent" encoding as everywhere else in the column.
        df = df.with_columns(
            pl.when(pl.col("Date") == datetime.datetime(2014, 8, 1))
            .then(pl.lit(None, dtype=pl.String))
            .otherwise(pl.col("Sponsor Name"))
            .alias("Sponsor Name")
        )

    df = df.unique()
    df = df.drop_nulls(subset=["Year", "Month"])

    # Apply close-to-final silver dtypes (geo codes -> zero-padded Utf8, stable
    # code sets -> pl.Enum, identifiers -> Utf8, dates -> pl.Date). See the
    # SilverSpec in config.py and core/types.py.
    df = apply_silver_types(df, _SILVER_SPECS[file_type])

    return df


def _existing_partitions(save_folder: Path) -> set[tuple[int, int]]:
    """Return the set of year/month partitions already present in ``save_folder``."""
    partitions: set[tuple[int, int]] = set()
    if not save_folder.exists():
        return partitions

    for year_dir in save_folder.glob("Year=*"):
        try:
            year = int(year_dir.name.split("=", 1)[1])
        except (IndexError, ValueError):
            continue
        for month_dir in year_dir.glob("Month=*"):
            try:
                month = int(month_dir.name.split("=", 1)[1])
            except (IndexError, ValueError):
                continue
            partitions.add((year, month))

    return partitions


def _infer_snapshot_period(path: Path) -> tuple[int, int] | None:
    """Extract the ``(year, month)`` tuple from a cleaned snapshot filename."""
    match = _SNAPSHOT_FILENAME_PATTERN.search(path.stem)
    if not match:
        return None

    year, month, _ = (int(part) for part in match.groups())
    return year, month


def _snapshot_periods(path: Path) -> frozenset[tuple[int, int]]:
    """Return the endorsement ``(Year, Month)`` periods a bronze snapshot carries.

    This is the partition key, and it is not the snapshot's filename period:
    ``fha_sf_snapshot_20100401.parquet`` holds endorsement ``(2010, 5)``. Reading
    it from the data is what makes the incremental build idempotent. Cheap --
    two columns out of one parquet, so the whole corpus scans in well under a
    second.

    Returns:
        The distinct endorsement ``(Year, Month)`` periods the snapshot carries.
    """
    rows = pl.scan_parquet(str(path)).select("Year", "Month").drop_nulls().unique().collect()
    return frozenset((int(year), int(month)) for year, month in rows.iter_rows())


def build_silver_snapshots(
    data_folder: Path,
    save_folder: Path,
    *,
    file_type: SnapshotType = "single_family",
    min_year: int = 2010,
    max_year: int = 2025,
    overwrite: bool = False,
) -> list[tuple[int, int]]:
    """Build the hive-partitioned silver database from bronze monthly snapshots.

    Args:
        data_folder: Directory holding the bronze parquet monthly snapshots.
        save_folder: Destination directory for the hive-partitioned silver database.
        file_type: Which dataset's schema adjustments and `SilverSpec` to apply.
        min_year: Earliest snapshot-filename year to include, inclusive.
        max_year: Latest snapshot-filename year to include, inclusive.
        overwrite: Rebuild every endorsement period in range. When ``False``
            (default) only periods with no partition on disk are built, which
            makes an already-current run a genuine no-op.

    Returns:
        The endorsement ``(Year, Month)`` periods written, sorted.

    Example:
        Incremental build -- only periods missing from silver:

        >>> from pathlib import Path
        >>> build_silver_snapshots(
        ...     Path("data/fha/bronze/single_family"),
        ...     Path("data/fha/silver/single_family"),
        ...     file_type="single_family",
        ... )
        [(2025, 11)]

        Rebuild a narrowed range from scratch:

        >>> build_silver_snapshots(
        ...     Path("data/fha/bronze/hecm"),
        ...     Path("data/fha/silver/hecm"),
        ...     file_type="hecm",
        ...     min_year=2020,
        ...     max_year=2024,
        ...     overwrite=True,
        ... )
        [(2020, 1)]

    """
    # The year window selects *files* by their filename period; what gets built
    # is keyed on the endorsement (Year, Month) the rows carry. The two genuinely
    # diverge -- fha_sf_snapshot_20100401.parquet holds endorsement (2010, 5), so
    # partition Year=2010/Month=4 does not exist and never will. That divergence
    # is preserved, not "corrected": the snapshot's own period is what HUD
    # published, and the endorsement period is what the data means.
    produced: dict[Path, frozenset[tuple[int, int]]] = {}
    for file in sorted(data_folder.glob("*.parquet")):
        period = _infer_snapshot_period(file)
        if period is None:
            logger.debug("Skipping unrecognised snapshot filename: %s", file.name)
            continue
        if not min_year <= period[0] <= max_year:
            continue
        produced[file] = _snapshot_periods(file)

    snapshots_by_period: dict[tuple[int, int], list[Path]] = defaultdict(list)
    for file, file_periods in produced.items():
        for file_period in file_periods:
            snapshots_by_period[file_period].append(file)

    # Skipping on the endorsement period rather than the filename period is what
    # makes an already-current incremental run a no-op; keying it on the filename
    # re-selected the 2010-04 snapshot forever and rewrote (2010, 5) each time.
    existing = set() if overwrite else _existing_partitions(save_folder)
    if not overwrite:
        logger.info("Detected %d existing partitions in %s.", len(existing), save_folder)

    targets = {period for period in snapshots_by_period if period not in existing}

    # A partition is rewritten wholesale, so every snapshot feeding it has to be
    # in the build or the rewrite would silently drop rows. Grow the target set
    # until it is closed under "periods the selected snapshots carry". No FHA
    # vintage spans two endorsement periods today, so this settles on the first
    # pass -- it is here so that one which did could not corrupt a partition.
    selected: set[Path] = set()
    while True:
        selected = {file for period in targets for file in snapshots_by_period[period]}
        reached = {period for file in selected for period in produced[file]}
        if reached <= targets:
            break
        targets |= reached

    if not selected:
        logger.info(
            "No snapshots to build between %d and %d in %s.",
            min_year,
            max_year,
            data_folder,
        )
        return []

    periods = sorted(targets)
    for file in sorted(selected):
        logger.info("Queueing snapshot %s (endorsement %s)", file.name, sorted(produced[file]))

    df = _prepare_snapshot_export(
        [pl.scan_parquet(str(file)) for file in sorted(selected)], file_type=file_type
    )

    enforce_schema(
        df.collect_schema(),
        _SILVER_SPECS[file_type].target,
        name=f"fha/{file_type}",
    )

    # Partition-scoped: only the leaves about to be rewritten are removed, never
    # the dataset root. Deleting first (rather than relying on the sink's
    # deterministically named file clobbering the old one) is what makes a
    # partition that shrinks -- or that was written by an older, differently
    # naming sink -- come back with exactly the rows it should have.
    clear_partitions(save_folder, (f"Year={y}/Month={m}" for y, m in periods))

    write_hive_partitioned(
        df,
        save_folder,
        partition_cols=["Year", "Month"],
        include_key=True,
    )

    logger.info("Wrote %d partitions to %s.", len(periods), save_folder)
    return periods
