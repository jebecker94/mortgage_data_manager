"""Allocation utilities for HUD crosswalk data.

Provides functions to allocate values between geographies using crosswalk ratios:
- Disaggregate ZIP-level data to tracts/counties
- Aggregate tract/county data to ZIPs
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import polars as pl

from mortgage_data_manager.hud.load import get_crosswalk_for_date

RatioType = Literal["res_ratio", "bus_ratio", "oth_ratio", "tot_ratio"]


def allocate_to_tracts(
    zip_data: pl.DataFrame | pl.LazyFrame,
    value_columns: str | list[str],
    year: int,
    quarter: int,
    ratio_col: RatioType = "res_ratio",
    zip_col: str = "zip",
    data_dir: Path | None = None,
) -> pl.DataFrame:
    """Allocate ZIP-level values to census tracts using crosswalk ratios.

    Takes data at the ZIP code level and distributes it to census tracts
    based on the proportion of addresses in each tract.

    Args:
        zip_data: DataFrame containing ZIP-level data.
        value_columns: Column name(s) containing values to allocate.
        year: Year of crosswalk to use.
        quarter: Quarter of crosswalk to use.
        ratio_col: Which ratio to use for allocation.
        zip_col: Name of the ZIP code column in zip_data.
        data_dir: Base directory for crosswalk data.

    Returns:
        DataFrame with values allocated to tracts.

    Examples:
        >>> zip_pop = pl.DataFrame({"zip": ["10001", "10002"], "population": [1000, 2000]})
        >>> tract_pop = allocate_to_tracts(zip_pop, "population", 2020, 1)
    """
    if isinstance(value_columns, str):
        value_columns = [value_columns]

    if isinstance(zip_data, pl.LazyFrame):
        zip_data = zip_data.collect()

    crosswalk = get_crosswalk_for_date("ZIP_TRACT", year, quarter, data_dir).collect()

    zip_data = zip_data.with_columns(pl.col(zip_col).cast(pl.Utf8).str.zfill(5))

    result = crosswalk.join(
        zip_data,
        left_on="zip",
        right_on=zip_col,
        how="inner",
    )

    for col in value_columns:
        result = result.with_columns((pl.col(col) * pl.col(ratio_col)).alias(col))

    keep_cols = ["geoid"] + value_columns
    other_cols = [c for c in zip_data.columns if c != zip_col and c not in value_columns]
    keep_cols.extend(other_cols)

    result = result.select(keep_cols).rename({"geoid": "tract"})

    return result


def allocate_to_zips(
    tract_data: pl.DataFrame | pl.LazyFrame,
    value_columns: str | list[str],
    year: int,
    quarter: int,
    ratio_col: RatioType = "res_ratio",
    tract_col: str = "tract",
    data_dir: Path | None = None,
) -> pl.DataFrame:
    """Allocate census tract values to ZIP codes using crosswalk ratios.

    Takes data at the tract level and distributes it to ZIP codes based
    on the proportion of the tract in each ZIP.

    Args:
        tract_data: DataFrame containing tract-level data.
        value_columns: Column name(s) containing values to allocate.
        year: Year of crosswalk to use.
        quarter: Quarter of crosswalk to use.
        ratio_col: Which ratio to use for allocation.
        tract_col: Name of the tract column in tract_data.
        data_dir: Base directory for crosswalk data.

    Returns:
        DataFrame with values allocated to ZIPs.

    Examples:
        >>> tract_demo = pl.DataFrame({
        ...     "tract": ["36061000100", "36061000200"],
        ...     "median_income": [50000, 75000]
        ... })
        >>> zip_demo = allocate_to_zips(tract_demo, "median_income", 2020, 1)
    """
    if isinstance(value_columns, str):
        value_columns = [value_columns]

    if isinstance(tract_data, pl.LazyFrame):
        tract_data = tract_data.collect()

    crosswalk = get_crosswalk_for_date("TRACT_ZIP", year, quarter, data_dir).collect()

    tract_data = tract_data.with_columns(pl.col(tract_col).cast(pl.Utf8).str.zfill(11))

    result = crosswalk.join(
        tract_data,
        left_on="tract",
        right_on=tract_col,
        how="inner",
    )

    for col in value_columns:
        result = result.with_columns((pl.col(col) * pl.col(ratio_col)).alias(col))

    keep_cols = ["geoid"] + value_columns
    result = result.select(keep_cols).rename({"geoid": "zip"})

    return result


def aggregate_to_zips(
    tract_data: pl.DataFrame | pl.LazyFrame,
    value_columns: str | list[str],
    year: int,
    quarter: int,
    ratio_col: RatioType = "res_ratio",
    tract_col: str = "tract",
    agg_method: Literal["sum", "mean", "weighted_mean"] = "sum",
    data_dir: Path | None = None,
) -> pl.DataFrame:
    """Aggregate tract-level data to ZIP codes.

    Unlike allocate_to_zips, this function aggregates multiple tracts
    into each ZIP, combining their values.

    Args:
        tract_data: DataFrame containing tract-level data.
        value_columns: Column name(s) containing values to aggregate.
        year: Year of crosswalk to use.
        quarter: Quarter of crosswalk to use.
        ratio_col: Which ratio to use for weighting.
        tract_col: Name of the tract column in tract_data.
        agg_method: Aggregation method ("sum", "mean", or "weighted_mean").
        data_dir: Base directory for crosswalk data.

    Returns:
        DataFrame with one row per ZIP, containing aggregated values.

    Examples:
        >>> tract_pop = pl.DataFrame({
        ...     "tract": ["36061000100", "36061000200"],
        ...     "population": [5000, 3000]
        ... })
        >>> zip_pop = aggregate_to_zips(tract_pop, "population", 2020, 1, agg_method="sum")
    """
    if isinstance(value_columns, str):
        value_columns = [value_columns]

    allocated = allocate_to_zips(
        tract_data, value_columns, year, quarter, ratio_col, tract_col, data_dir
    )

    if agg_method == "sum":
        agg_exprs = [pl.col(c).sum().alias(c) for c in value_columns]
    elif agg_method == "mean":
        agg_exprs = [pl.col(c).mean().alias(c) for c in value_columns]
    elif agg_method == "weighted_mean":
        # Allocation already weighted, so sum gives the weighted mean
        agg_exprs = [pl.col(c).sum().alias(c) for c in value_columns]
    else:
        raise ValueError(f"Unknown aggregation method: {agg_method}")

    result = allocated.group_by("zip").agg(agg_exprs)

    return result


def aggregate_to_tracts(
    zip_data: pl.DataFrame | pl.LazyFrame,
    value_columns: str | list[str],
    year: int,
    quarter: int,
    ratio_col: RatioType = "res_ratio",
    zip_col: str = "zip",
    agg_method: Literal["sum", "mean", "weighted_mean"] = "sum",
    data_dir: Path | None = None,
) -> pl.DataFrame:
    """Aggregate ZIP-level data to census tracts.

    Args:
        zip_data: DataFrame containing ZIP-level data.
        value_columns: Column name(s) containing values to aggregate.
        year: Year of crosswalk to use.
        quarter: Quarter of crosswalk to use.
        ratio_col: Which ratio to use for weighting.
        zip_col: Name of the ZIP code column in zip_data.
        agg_method: Aggregation method ("sum", "mean", or "weighted_mean").
        data_dir: Base directory for crosswalk data.

    Returns:
        DataFrame with one row per tract, containing aggregated values.

    Examples:
        >>> zip_emp = pl.DataFrame({
        ...     "zip": ["10001", "10002"],
        ...     "employment": [5000, 3000]
        ... })
        >>> tract_emp = aggregate_to_tracts(zip_emp, "employment", 2020, 1)
    """
    if isinstance(value_columns, str):
        value_columns = [value_columns]

    allocated = allocate_to_tracts(
        zip_data, value_columns, year, quarter, ratio_col, zip_col, data_dir
    )

    if agg_method == "sum":
        agg_exprs = [pl.col(c).sum().alias(c) for c in value_columns]
    elif agg_method == "mean":
        agg_exprs = [pl.col(c).mean().alias(c) for c in value_columns]
    elif agg_method == "weighted_mean":
        agg_exprs = [pl.col(c).sum().alias(c) for c in value_columns]
    else:
        raise ValueError(f"Unknown aggregation method: {agg_method}")

    result = allocated.group_by("tract").agg(agg_exprs)

    return result
