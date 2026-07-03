"""Unit tests for the packed/fixed-point date decoders in ``core.dates``.

Each turns an int-or-string encoded column into a ``pl.Date``, recovering lost leading
zeros and nulling zero/blank sentinels. The load-bearing hazard: ``month_to_date``
(month-first ``MMYYYY``) and ``ccyymm_to_date`` (year-first ``YYYYMM``) are visually
identical ``zfill(6)`` decoders — swapping them silently shifts every GSE/GNMA date column
— so they are tested as a contrasting pair.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from mortgage_data_manager.core.dates import (
    ccyymm_to_date,
    mmddyy_to_date,
    mmddyyyy_to_date,
    month_to_date,
    yyyymmdd_to_date,
)


def _col(values: list, dtype: pl.DataType, expr: pl.Expr) -> list:
    """Evaluate ``expr`` over a one-column frame and return the result list."""
    return pl.DataFrame({"x": pl.Series(values, dtype=dtype)}).select(expr).to_series().to_list()


def test_month_to_date_recovers_leading_zero_and_nulls_sentinel():
    # 92025 lost its leading zero (September 2025); 0 is a null sentinel.
    assert _col([62026, 92025, 0], pl.Int64, month_to_date(pl.col("x"))) == [
        date(2026, 6, 1),
        date(2025, 9, 1),
        None,
    ]
    assert _col(["082025"], pl.String, month_to_date(pl.col("x"))) == [date(2025, 8, 1)]


def test_ccyymm_to_date_is_year_first():
    assert _col(["202512", "202401", "0"], pl.String, ccyymm_to_date(pl.col("x"))) == [
        date(2025, 12, 1),
        date(2024, 1, 1),
        None,
    ]


def test_month_and_ccyymm_do_not_parse_each_others_encoding():
    # The contrast is the whole point: a YYYYMM value must NOT parse as MMYYYY, and an
    # MMYYYY value must NOT parse as YYYYMM. Either would be a silent date corruption.
    assert _col(["202512"], pl.String, month_to_date(pl.col("x"))) == [None]
    assert _col([62026], pl.Int64, ccyymm_to_date(pl.col("x"))) == [None]


def test_mmddyyyy_to_date():
    assert _col([5012026, 12312025, 0], pl.Int64, mmddyyyy_to_date(pl.col("x"))) == [
        date(2026, 5, 1),
        date(2025, 12, 31),
        None,
    ]


def test_yyyymmdd_to_date():
    assert _col([20251231, 0], pl.Int64, yyyymmdd_to_date(pl.col("x"))) == [
        date(2025, 12, 31),
        None,
    ]


def test_mmddyy_to_date_two_digit_year_pivot():
    # strptime century pivot: 70 -> 1970, 69 -> 2069.
    assert _col([10170, 10169], pl.Int64, mmddyy_to_date(pl.col("x"))) == [
        date(1970, 1, 1),
        date(2069, 1, 1),
    ]
