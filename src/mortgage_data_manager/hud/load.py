"""Load and filter HUD crosswalk data using Polars lazy evaluation.

Provides functions to lazily load crosswalk Parquet files and filter them
by year and quarter ranges. Supports both flat and Hive-partitioned storage.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.hud.config import (
    VALID_CROSSWALK_NAMES,
    CensusVintage,
    HUDConfig,
    get_census_vintage,
    get_vintage_year_range,
)


def _detect_storage_format(crosswalk_dir: Path) -> str:
    """Detect whether a directory uses flat or Hive partitioning.

    Returns:
        "flat", "hive", or "empty".
    """
    flat_files = list(crosswalk_dir.glob("*.parquet"))
    if flat_files:
        return "flat"

    hive_dirs = list(crosswalk_dir.glob("year=*/quarter=*/data.parquet"))
    if hive_dirs:
        return "hive"

    return "empty"


def _load_flat_format(crosswalk_dir: Path) -> pl.LazyFrame:
    """Load crosswalk data from flat Parquet files."""
    parquet_files = list(crosswalk_dir.glob("*.parquet"))
    if not parquet_files:
        raise ValueError(f"No Parquet files found in {crosswalk_dir}")

    return pl.scan_parquet(
        parquet_files,
        missing_columns="insert",
        extra_columns="ignore",
    )


def _load_hive_format(crosswalk_dir: Path) -> pl.LazyFrame:
    """Load crosswalk data from Hive-partitioned Parquet files."""
    parquet_files = list(crosswalk_dir.glob("year=*/quarter=*/data.parquet"))

    if not parquet_files:
        raise ValueError(f"No Hive-partitioned files found in {crosswalk_dir}")

    return pl.scan_parquet(
        parquet_files,
        hive_partitioning=True,
        missing_columns="insert",
        extra_columns="ignore",
    )


def load_crosswalk(
    crosswalk_type: str,
    min_year: int | None = None,
    min_quarter: int | None = None,
    max_year: int | None = None,
    max_quarter: int | None = None,
    data_dir: Path | None = None,
) -> pl.LazyFrame:
    """Lazily load crosswalk data and filter by year/quarter range.

    Args:
        crosswalk_type: Name of the crosswalk type (e.g., "ZIP_TRACT").
        min_year: Minimum year to include (inclusive).
        min_quarter: Minimum quarter (inclusive, applies to min_year).
        max_year: Maximum year to include (inclusive).
        max_quarter: Maximum quarter (inclusive, applies to max_year).
        data_dir: Base directory for raw data. Defaults to HUD_BRONZE_DIR.

    Returns:
        Lazy frame containing the filtered crosswalk data.

    Raises:
        ValueError: If crosswalk_type is invalid or no files are found.

    Examples:
        >>> lf = load_crosswalk("ZIP_TRACT", min_year=2015, max_year=2020)
        >>> df = lf.collect()
    """
    if crosswalk_type not in VALID_CROSSWALK_NAMES:
        raise ValueError(
            f"Invalid crosswalk type: {crosswalk_type}. Valid types: {VALID_CROSSWALK_NAMES}"
        )

    if data_dir is None:
        data_dir = HUDConfig.HUD_BRONZE_DIR

    crosswalk_dir = data_dir / crosswalk_type
    if not crosswalk_dir.exists():
        raise ValueError(f"Directory not found: {crosswalk_dir}")

    storage_format = _detect_storage_format(crosswalk_dir)
    if storage_format == "empty":
        raise ValueError(f"No Parquet files found in {crosswalk_dir}")
    elif storage_format == "hive":
        lf = _load_hive_format(crosswalk_dir)
    else:
        lf = _load_flat_format(crosswalk_dir)

    # Build filter conditions for year/quarter range
    filters = []

    if min_year is not None:
        if min_quarter is not None:
            filters.append(
                (pl.col("year") > min_year)
                | ((pl.col("year") == min_year) & (pl.col("quarter") >= min_quarter))
            )
        else:
            filters.append(pl.col("year") >= min_year)

    if max_year is not None:
        if max_quarter is not None:
            filters.append(
                (pl.col("year") < max_year)
                | ((pl.col("year") == max_year) & (pl.col("quarter") <= max_quarter))
            )
        else:
            filters.append(pl.col("year") <= max_year)

    for f in filters:
        lf = lf.filter(f)

    return lf


def get_crosswalk_for_date(
    crosswalk_type: str,
    year: int,
    quarter: int,
    data_dir: Path | None = None,
) -> pl.LazyFrame:
    """Load crosswalk data for a single year/quarter.

    Args:
        crosswalk_type: Name of the crosswalk type.
        year: Year to load.
        quarter: Quarter to load (1-4).
        data_dir: Base directory for raw data.

    Returns:
        Lazy frame containing the crosswalk data for that quarter.

    Raises:
        ValueError: If quarter is not 1-4.

    Examples:
        >>> lf = get_crosswalk_for_date("ZIP_TRACT", 2020, 1)
        >>> df = lf.collect()
    """
    if quarter not in (1, 2, 3, 4):
        raise ValueError(f"Quarter must be 1-4, got {quarter}")

    return load_crosswalk(
        crosswalk_type=crosswalk_type,
        min_year=year,
        min_quarter=quarter,
        max_year=year,
        max_quarter=quarter,
        data_dir=data_dir,
    )


def load_crosswalk_by_vintage(
    crosswalk_type: str,
    vintage: CensusVintage,
    data_dir: Path | None = None,
) -> pl.LazyFrame:
    """Load all crosswalk data for a specific census geography vintage.

    Prevents accidentally mixing data from different census geography
    versions (e.g., 2010 vs 2020 census tracts).

    Census vintage boundaries:
        - 2000: 2010 Q1 through 2011 Q4
        - 2010: 2012 Q1 through 2022 Q4
        - 2020: 2023 Q1 onwards

    Args:
        crosswalk_type: Name of the crosswalk type.
        vintage: Census geography vintage (2000, 2010, or 2020).
        data_dir: Base directory for raw data.

    Returns:
        Lazy frame containing all crosswalk data for that census vintage.

    Examples:
        >>> lf = load_crosswalk_by_vintage("ZIP_TRACT", 2010)
        >>> df = lf.collect()
    """
    start_year, start_quarter, end_year, end_quarter = get_vintage_year_range(vintage)

    return load_crosswalk(
        crosswalk_type=crosswalk_type,
        min_year=start_year,
        min_quarter=start_quarter,
        max_year=end_year,
        max_quarter=end_quarter,
        data_dir=data_dir,
    )


def get_vintage_for_crosswalk(year: int, quarter: int) -> CensusVintage:
    """Get the census geography vintage used for a given year/quarter.

    Args:
        year: Year of the crosswalk data.
        quarter: Quarter of the crosswalk data (1-4).

    Returns:
        The census vintage (2000, 2010, or 2020).

    Examples:
        >>> get_vintage_for_crosswalk(2015, 1)
        2010
        >>> get_vintage_for_crosswalk(2023, 3)
        2020
    """
    return get_census_vintage(year, quarter)
