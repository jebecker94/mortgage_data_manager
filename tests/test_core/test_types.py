"""Unit tests for the shared silver-type helpers in ``core.types``.

These cover stable pure logic: geographic zero-pad recovery, sentinel
null-mapping, fixed-point rescaling, smallest-int sizing, the declarative
:class:`SilverSpec` applier (including conform-to-target), and the
:func:`enforce_schema` regression guard. The ZIP-recovery cases are regression
tests for the integer-stored-geo leading-zero-loss bug found in the silver audit.
"""

from __future__ import annotations

import polars as pl
import pytest

from mortgage_data_manager.core.types import (
    SchemaMismatchError,
    SilverSpec,
    apply_silver_types,
    enforce_schema,
    fixed_point,
    null_sentinels,
    pad_geo,
    smallest_int,
    to_state_fips,
    to_zip,
    yn_to_bool,
)


def _col(values: list, dtype: pl.DataType, expr: pl.Expr) -> list:
    """Evaluate ``expr`` over a one-column frame and return the result list."""
    return pl.DataFrame({"x": pl.Series(values, dtype=dtype)}).select(expr).to_series().to_list()


def test_to_zip_recovers_leading_zeros_from_int() -> None:
    # 602 (PR 00602) and 5770 (MA 05770) lost their leading zeros at integer storage.
    assert _col([602, 5770, 12345, None], pl.Int32, to_zip(pl.col("x"))) == [
        "00602",
        "05770",
        "12345",
        None,
    ]


def test_to_state_fips_drops_float_suffix() -> None:
    # Float-stored FIPS (1.0) must become "01", not "1.0".
    assert _col([1.0, 56.0, None], pl.Float64, to_state_fips(pl.col("x"))) == ["01", "56", None]


def test_pad_geo_preserves_non_numeric_codes() -> None:
    # A genuinely non-numeric code is left intact (no silent data loss).
    assert _col(["ABCDE", "1"], pl.String, pad_geo(pl.col("x"), 5)) == ["ABCDE", "00001"]


def test_null_sentinels() -> None:
    assert _col([0, 5, 9999], pl.Int64, null_sentinels(pl.col("x"), (0, 9999))) == [None, 5, None]


def test_fixed_point_rescales_and_nulls_blanks() -> None:
    out = _col(["06500", "", "00100"], pl.String, fixed_point(pl.col("x"), 1000))
    assert out[0] == pytest.approx(6.5)
    assert out[1] is None
    assert out[2] == pytest.approx(0.1)


def test_yn_to_bool() -> None:
    assert _col(["Y", "n", "U", None], pl.String, yn_to_bool(pl.col("x"))) == [True, False, None, None]


@pytest.mark.parametrize(
    ("lo", "hi", "expected"),
    [
        (0, 100, pl.Int8),
        (1, 32, pl.Int8),
        (0, 1111, pl.Int16),
        (-99999, 72, pl.Int32),  # sentinel underflows Int16 -> forces Int32
        (0, 3_000_000_000, pl.Int64),
    ],
)
def test_smallest_int(lo: int, hi: int, expected: type[pl.DataType]) -> None:
    assert smallest_int(lo, hi) is expected


def test_apply_silver_types_conforms_to_target() -> None:
    lf = pl.LazyFrame(
        {
            "zip": pl.Series([602, 5770], dtype=pl.Int32),
            "code": pl.Series([1, 2], dtype=pl.Int64),
            "id": pl.Series([123, 456], dtype=pl.Int64),
            "prev": pl.Series([0, 5], dtype=pl.Int64),
            "kind": pl.Series(["A", "B"], dtype=pl.String),
        }
    )
    spec = SilverSpec(
        zip5=("zip",),
        casts={"code": pl.Int8},
        identifiers=("id", "prev"),
        sentinels={"prev": (0,)},
        enums={"kind": pl.Enum(["A", "B"])},
        target=pl.Schema(
            {
                "zip": pl.String,
                "code": pl.Int8,
                "id": pl.String,
                "prev": pl.String,
                "kind": pl.Enum(["A", "B"]),
                "missing": pl.Float64,  # absent from source -> auto-filled as typed null
            }
        ),
    )
    out = apply_silver_types(lf, spec).collect()

    # canonical order matches the target exactly
    assert out.columns == ["zip", "code", "id", "prev", "kind", "missing"]
    assert dict(out.schema) == dict(spec.target)
    row = out.row(0, named=True)
    assert row["zip"] == "00602"  # leading zero recovered
    assert row["id"] == "123"  # identifier -> Utf8
    assert row["prev"] is None  # sentinel 0 -> null, then Utf8
    assert out["missing"].null_count() == 2  # auto-filled


def test_enforce_schema_raises_on_dtype_mismatch() -> None:
    actual = pl.Schema({"a": pl.Int64, "b": pl.String})
    target = pl.Schema({"a": pl.Int8, "b": pl.String})
    with pytest.raises(SchemaMismatchError):
        enforce_schema(actual, target, name="t")


def test_enforce_schema_passes_on_match_and_tolerates_extra() -> None:
    target = pl.Schema({"a": pl.Int8})
    enforce_schema(pl.Schema({"a": pl.Int8}), target, name="t")  # exact match: no raise
    enforce_schema(pl.Schema({"a": pl.Int8, "new": pl.String}), target, name="t")  # extra: warn only
