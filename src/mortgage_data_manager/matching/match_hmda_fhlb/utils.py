"""Utility functions for FHLB matching.

Ported from match_fhfa_hmda project to make this script standalone.
"""

from __future__ import annotations

import polars as pl

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

HMDA_MISSING_VALUES = {
    'numeric': -99999,
    'string': '',
    'categorical_na': [8888, 9999]
}

# -----------------------------------------------------------------------------
# Census Tract Utilities
# -----------------------------------------------------------------------------

def create_census_tract_string(
    df: pl.LazyFrame,
    state_col: str,
    county_col: str,
    tract_col: str,
    output_col: str = "census_tract"
) -> pl.LazyFrame:
    """Create standardized 11-character census tract string from components."""
    return df.with_columns([
        (
            pl.col(state_col).cast(pl.UInt64) * 10**9 +
            pl.col(county_col).cast(pl.UInt64) * 10**6 +
            pl.col(tract_col).cast(pl.UInt64)
        )
        .cast(pl.Utf8)
        .str.zfill(11)
        .alias(output_col)
    ])

def create_census_tract_from_fips(
    df: pl.LazyFrame,
    state_col: str = "FIPS State Numeric Code",
    county_col: str = "FIPS County Code",
    tract_col: str = "Census Tract Identifier",
    output_col: str = "Census Tract String"
) -> pl.LazyFrame:
    """Create census tract string from FHFA FIPS codes."""
    return create_census_tract_string(
        df, state_col, county_col, tract_col, output_col
    )

# -----------------------------------------------------------------------------
# Missing Value Utilities
# -----------------------------------------------------------------------------

def replace_missing_values(
    df: pl.LazyFrame,
    numeric_cols: list[str],
    string_cols: list[str] | None = None,
    categorical_cols: list[str] | None = None
) -> pl.LazyFrame:
    """Replace HMDA missing value indicators with null."""
    expressions = []

    # Handle numeric columns
    for col in numeric_cols:
        if col in df.columns:
            expressions.append(
                pl.when(pl.col(col) == HMDA_MISSING_VALUES['numeric'])
                .then(None)
                .otherwise(pl.col(col))
                .alias(col)
            )

    # Handle string columns
    if string_cols:
        for col in string_cols:
            if col in df.columns:
                expressions.append(
                    pl.when(pl.col(col) == HMDA_MISSING_VALUES['string'])
                    .then(None)
                    .otherwise(pl.col(col))
                    .alias(col)
                )

    # Handle categorical columns
    if categorical_cols:
        for col in categorical_cols:
            if col in df.columns:
                expressions.append(
                    pl.when(pl.col(col).is_in(HMDA_MISSING_VALUES['categorical_na']))
                    .then(None)
                    .otherwise(pl.col(col))
                    .alias(col)
                )

    return df.with_columns(expressions) if expressions else df


def clean_hmda_missing_values(df: pl.LazyFrame) -> pl.LazyFrame:
    """Clean all HMDA missing values in a standardized way."""
    numeric_cols = [
        'loan_term', 'interest_rate', 'intro_rate_period',
        'debt_to_income_ratio', 'combined_loan_to_value_ratio',
        'discount_points', 'income', 'property_value'
    ]

    categorical_cols = [
        'applicant_age', 'co_applicant_age', 'applicant_sex',
        'co_applicant_sex', 'applicant_ethnicity_1', 'co_applicant_ethnicity_1',
        'applicant_race_1', 'co_applicant_race_1'
    ]

    return replace_missing_values(
        df,
        numeric_cols=numeric_cols,
        categorical_cols=categorical_cols
    )
