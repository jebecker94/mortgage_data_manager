"""Load and filter USPS crosswalk data using Polars lazy evaluation.

Provides functions to lazily load the per-quarter bronze parquet and filter it
by year and quarter ranges. Bronze is the analysis layer for this source (there
is no silver), so these loaders are the normal way to read it.

``missing_columns="insert"`` / ``extra_columns="ignore"`` absorb the one schema
change in the series: ``city`` and ``state`` appear from 2021 onward.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.sources.usps_crosswalk.config import (
    VALID_CROSSWALK_NAMES,
    CensusVintage,
    USPSCrosswalkConfig,
    get_vintage_year_range,
)


def _scan_quarters(crosswalk_dir: Path) -> pl.LazyFrame:
    """Scan every per-quarter parquet for one crosswalk type."""
    parquet_files = list(crosswalk_dir.glob("*.parquet"))
    if not parquet_files:
        raise ValueError(f"No Parquet files found in {crosswalk_dir}")

    return pl.scan_parquet(
        parquet_files,
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
        data_dir: Base directory for raw data. Defaults to USPS_CROSSWALK_BRONZE_DIR.

    Returns:
        Lazy frame containing the filtered crosswalk data.

    Raises:
        ValueError: If crosswalk_type is invalid or no files are found.

    Example:
        >>> lf = load_crosswalk("ZIP_TRACT", min_year=2015, max_year=2020)
        >>> df = lf.collect()
    """
    if crosswalk_type not in VALID_CROSSWALK_NAMES:
        raise ValueError(
            f"Invalid crosswalk type: {crosswalk_type}. Valid types: {VALID_CROSSWALK_NAMES}"
        )

    if data_dir is None:
        data_dir = USPSCrosswalkConfig.USPS_CROSSWALK_BRONZE_DIR

    crosswalk_dir = data_dir / crosswalk_type
    if not crosswalk_dir.exists():
        raise ValueError(f"Directory not found: {crosswalk_dir}")

    lf = _scan_quarters(crosswalk_dir)

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
    """Load one quarter's crosswalk data.

    Reads that quarter's file directly rather than scanning the whole type and
    filtering, so it stays cheap as the series grows.

    Args:
        crosswalk_type: Name of the crosswalk type.
        year: Year to load.
        quarter: Quarter to load (1-4).
        data_dir: Bronze root. Defaults to ``USPS_CROSSWALK_BRONZE_DIR``.

    Returns:
        Lazy frame containing the crosswalk data for that quarter.

    Raises:
        ValueError: If ``quarter`` is not 1-4, the type is unknown, or that
            quarter has not been built.

    Example:
        >>> lf = get_crosswalk_for_date("ZIP_TRACT", 2020, 1)
        >>> df = lf.collect()
    """
    if crosswalk_type not in VALID_CROSSWALK_NAMES:
        raise ValueError(
            f"Invalid crosswalk type: {crosswalk_type}. Valid types: {VALID_CROSSWALK_NAMES}"
        )
    if quarter not in (1, 2, 3, 4):
        raise ValueError(f"Quarter must be 1-4, got {quarter}")

    path = (
        USPSCrosswalkConfig.bronze_path(crosswalk_type, year, quarter)
        if data_dir is None
        else data_dir / crosswalk_type / f"{crosswalk_type}_{year}_Q{quarter}.parquet"
    )
    if not path.exists():
        raise ValueError(f"Not built: {path}")
    return pl.scan_parquet(path)


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

    Example:
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
