"""Silver layer import: Enrich bronze data and create hive-partitioned output."""

from __future__ import annotations

import datetime
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import write_hive_partitioned
from mortgage_data_manager.core.types import SilverSpec, apply_silver_types, enforce_schema

from .config import HECM_SILVER, SINGLE_FAMILY_SILVER
from .utils import add_county_fips

type SnapshotType = Literal["single_family", "hecm"]

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
    add_fips: bool,
    add_date: bool,
) -> pl.LazyFrame:
    """Apply shared export transformations to a collection of snapshot frames."""
    if not frames:
        msg = "No snapshot frames provided for export"
        raise ValueError(msg)

    df = pl.concat(frames, how="diagonal_relaxed")

    for column in ["Originating Mortgagee", "Sponsor Name"]:
        df = df.with_columns(
            pl.when(pl.col(column).is_null())
            .then(pl.lit(""))
            .otherwise(pl.col(column))
            .alias(column)
        )
        df = df.with_columns(
            pl.when(pl.col(column).is_in(["nan", "None"]))
            .then(pl.lit(""))
            .otherwise(pl.col(column))
            .alias(column)
        )

    if add_fips:
        df = add_county_fips(df)

    if add_date:
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

    if file_type == "single_family" and add_date and "Date" in df.collect_schema().names():
        df = df.with_columns(
            pl.when(pl.col("Date") == datetime.datetime(2014, 8, 1))
            .then(pl.lit(""))
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


def save_clean_snapshots_to_db(
    data_folder: Path,
    save_folder: Path,
    min_year: int = 2010,
    max_year: int = 2025,
    file_type: SnapshotType = "single_family",
    add_fips: bool = True,
    add_date: bool = True,
) -> None:
    """Saves cleaned snapshots to a database.

    Args:
        data_folder: Location containing the cleaned parquet monthly snapshots.
        save_folder: Destination directory for the hive-partitioned parquet database.
        min_year: Inclusive range of years to scan when gathering monthly files.
        max_year: Inclusive range of years to scan when gathering monthly files.
        file_type: Indicates which schema adjustments to apply during export.
        add_fips: When ``True`` (default) county FIPS codes are appended to the output.
        add_date: When ``True`` (default) a ``Date`` column is synthesized from year and
            month fields.

    Returns:
        None.

    Examples:
        Build the on-disk database after converting raw files:

        >>> from pathlib import Path
        >>> clean_sf = Path("data/clean/single_family")
        >>> db_sf = Path("data/database/single_family")
        >>> save_clean_snapshots_to_db(clean_sf, db_sf, file_type="single_family")

        Restrict the exported range to recent years and skip FIPS enrichment for a
        faster exploratory build:

        >>> save_clean_snapshots_to_db(
        ...     clean_sf,
        ...     db_sf,
        ...     min_year=2020,
        ...     max_year=2024,
        ...     add_fips=False,
        ... )

    """
    # Get Files and Combine
    frames: list[pl.LazyFrame] = []
    for year in range(min_year, max_year + 1):
        files = sorted(data_folder.glob(f"fha_*snapshot*{year}*.parquet"))
        for file in files:
            frames.append(pl.scan_parquet(str(file)))

    if not frames:
        logger.info(
            "No cleaned snapshots found in %s for the requested range; skipping export.",
            data_folder,
        )
        return

    df = _prepare_snapshot_export(
        frames,
        file_type=file_type,
        add_fips=add_fips,
        add_date=add_date,
    )

    enforce_schema(
        df.collect_schema(),
        _SILVER_SPECS[file_type].target,
        name=f"fha/{file_type}",
    )

    write_hive_partitioned(
        df,
        save_folder,
        partition_cols=["Year", "Month"],
        include_key=True,
    )


def update_clean_snapshots_to_db(
    data_folder: Path,
    save_folder: Path,
    min_year: int = 2010,
    max_year: int = 2025,
    file_type: SnapshotType = "single_family",
    add_fips: bool = True,
    add_date: bool = True,
) -> list[tuple[int, int]]:
    """Append newly cleaned snapshots to the hive-partitioned parquet database.

    Only snapshots whose ``Year`` and ``Month`` partitions are not already present
    in ``save_folder`` will be processed. The function returns the list of
    ``(year, month)`` pairs that were appended.
    """
    existing_partitions = _existing_partitions(save_folder)
    logger.info(
        "Detected %d existing partitions in %s.",
        len(existing_partitions),
        save_folder,
    )

    appended_partitions: list[tuple[int, int]] = []
    frames: list[pl.LazyFrame] = []

    for file in sorted(data_folder.glob("*.parquet")):
        period = _infer_snapshot_period(file)
        if period is None:
            logger.debug("Skipping unrecognised snapshot filename: %s", file.name)
            continue

        year, month = period
        if year < min_year or year > max_year:
            continue
        if (year, month) in existing_partitions:
            continue

        logger.info(
            "Queueing snapshot %s for incremental append (Year=%d, Month=%d)",
            file.name,
            year,
            month,
        )
        frames.append(pl.scan_parquet(str(file)))
        appended_partitions.append((year, month))

    if not frames:
        logger.info(
            "No new cleaned snapshots found between %d and %d for %s.",
            min_year,
            max_year,
            data_folder,
        )
        return []

    df = _prepare_snapshot_export(
        frames,
        file_type=file_type,
        add_fips=add_fips,
        add_date=add_date,
    )

    enforce_schema(
        df.collect_schema(),
        _SILVER_SPECS[file_type].target,
        name=f"fha/{file_type}",
    )

    write_hive_partitioned(
        df,
        save_folder,
        partition_cols=["Year", "Month"],
        include_key=True,
    )

    logger.info(
        "Incremental update complete for %s; appended %d partitions.",
        save_folder,
        len(appended_partitions),
    )

    return appended_partitions
