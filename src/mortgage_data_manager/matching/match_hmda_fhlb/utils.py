"""Shared helpers for the HMDA-FHLB matching workflow."""

from __future__ import annotations

import polars as pl

__all__ = ["create_census_tract_from_fips", "create_census_tract_string"]


def create_census_tract_string(
    df: pl.LazyFrame,
    state_col: str,
    county_col: str,
    tract_col: str,
    output_col: str = "census_tract",
) -> pl.LazyFrame:
    """Create standardized 11-character census tract string from components.

    Args:
        df: The frame to annotate.
        state_col: Name of the state code column.
        county_col: Name of the county code column.
        tract_col: Name of the tract identifier column.
        output_col: Name of the column to write the tract string to.

    Returns:
        The frame with the 11-character tract string added.
    """
    return df.with_columns(
        [
            (
                pl.col(state_col).cast(pl.UInt64) * 10**9
                + pl.col(county_col).cast(pl.UInt64) * 10**6
                + pl.col(tract_col).cast(pl.UInt64)
            )
            .cast(pl.Utf8)
            .str.zfill(11)
            .alias(output_col)
        ]
    )


def create_census_tract_from_fips(
    df: pl.LazyFrame,
    state_col: str = "FIPS State Numeric Code",
    county_col: str = "FIPS County Code",
    tract_col: str = "Census Tract Identifier",
    output_col: str = "Census Tract String",
) -> pl.LazyFrame:
    """Create census tract string from FHFA FIPS codes.

    Args:
        df: The frame to annotate.
        state_col: Name of the state code column.
        county_col: Name of the county code column.
        tract_col: Name of the tract identifier column.
        output_col: Name of the column to write the tract string to.

    Returns:
        The frame with the tract string built from the FIPS components added.
    """
    return create_census_tract_string(df, state_col, county_col, tract_col, output_col)
