"""Regression tests for canonical token decoding: exact fixed point, blank strings as null."""

from __future__ import annotations

import polars as pl

from mortgage_data_manager.sources.gnma.canonical import _decode_expr


def _decode(tokens: list[str | None], field: dict) -> list:
    return (
        pl.DataFrame({"t": tokens})
        .select(_decode_expr(pl.col("t"), field).alias("v"))["v"]
        .to_list()
    )


def test_fixed_point_is_the_exact_decimal() -> None:
    field = {
        "id": "rate",
        "arrow_type": "float64",
        "decode": {"kind": "fixed_point", "scale": 1000},
    }
    # Dividing 5999.0 by 1000 in polars gives 5.9990000000000006.
    assert _decode(["05999", "07350", "-01250", "00000", "", None], field) == [
        5.999,
        7.35,
        -1.25,
        0.0,
        None,
        None,
    ]


def test_fixed_point_large_balance() -> None:
    field = {"id": "upb", "arrow_type": "float64", "decode": {"kind": "fixed_point", "scale": 100}}
    assert _decode(["000123456789012", "000000009699"], field) == [1234567890.12, 96.99]


def test_blank_string_is_null() -> None:
    field = {"id": "flag", "arrow_type": "string"}
    assert _decode(["Y", "", None], field) == ["Y", None, None]
