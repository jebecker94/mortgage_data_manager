"""Missing value standardization utilities."""

from __future__ import annotations

import polars as pl

from .round_config import HMDA_MISSING_VALUES


def replace_missing_values(
    df: pl.LazyFrame,
    numeric_cols: list[str],
    string_cols: list[str] | None = None,
    categorical_cols: list[str] | None = None
) -> pl.LazyFrame:
    """Replace HMDA missing value indicators with null.

    HMDA uses several conventions for missing values:
    - Numeric: -99999
    - String: empty string ""
    - Categorical: 8888, 9999

    Args:
        df: Input dataframe.
        numeric_cols: List of numeric columns to process.
        string_cols: List of string columns to process.
        categorical_cols: List of categorical columns to process.

    Returns:
        DataFrame with missing values standardized to null.
    """
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
    """Clean all HMDA missing values in a standardized way.

    Args:
        df: HMDA dataframe.

    Returns:
        Cleaned dataframe with standardized nulls.
    """
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


def clean_fhfa_missing_values(df: pl.LazyFrame) -> pl.LazyFrame:
    """Clean FHFA missing values (FHFA typically uses proper nulls).

    Args:
        df: FHFA dataframe.

    Returns:
        Cleaned dataframe.
    """
    # FHFA data typically has proper null handling
    # But we can add specific cleaning if needed
    return df


def handle_fhfa_missing_codes(df: pl.LazyFrame) -> pl.LazyFrame:
    """Handle special FHFA missing value codes.

    Some FHFA fields use specific codes:
    - 999999999 for missing amounts
    - 9 for missing categorical values

    Args:
        df: FHFA dataframe.

    Returns:
        Cleaned dataframe.
    """
    expressions = []

    # Handle large missing values in amounts
    amount_cols = ['Note Amount', 'Property Value', "Borrower(s) Annual Income"]
    for col in amount_cols:
        if col in df.columns:
            expressions.append(
                pl.when(pl.col(col) == 999999999)
                .then(None)
                .otherwise(pl.col(col))
                .alias(col)
            )

    return df.with_columns(expressions) if expressions else df
