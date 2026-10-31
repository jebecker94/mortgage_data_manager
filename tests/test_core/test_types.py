"""Unit tests for the shared silver-type helpers in ``core.types``.

These cover stable pure logic: geographic zero-pad recovery, sentinel
null-mapping, fixed-point rescaling, the declarative
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


def test_pad_geo_drops_float_suffix() -> None:
    # Float-stored FIPS (1.0) must become "01", not "1.0".
    assert _col([1.0, 56.0, None], pl.Float64, pad_geo(pl.col("x"), 2)) == ["01", "56", None]


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
    assert _col(["Y", "n", "U", None], pl.String, yn_to_bool(pl.col("x"))) == [
        True,
        False,
        None,
        None,
    ]


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
    enforce_schema(
        pl.Schema({"a": pl.Int8, "new": pl.String}), target, name="t"
    )  # extra: warn only


def test_optional_target_strict_raises_on_unexpected_fill() -> None:
    # A renamed or dropped source column used to ship as a silently 100%-null
    # target column. A spec that declares optional_target makes that fatal.
    lf = pl.LazyFrame({"a": [1, 2]})
    strict = SilverSpec(
        casts={"a": pl.Int8},
        target=pl.Schema({"a": pl.Int8, "dropped": pl.Float64}),
        optional_target=(),
    )
    with pytest.raises(SchemaMismatchError, match="dropped"):
        apply_silver_types(lf, strict)


def test_optional_target_allows_declared_fills_only() -> None:
    lf = pl.LazyFrame({"a": [1, 2]})
    target = pl.Schema({"a": pl.Int8, "era_col": pl.Float64})
    strict = SilverSpec(casts={"a": pl.Int8}, target=target, optional_target=("era_col",))
    out = apply_silver_types(lf, strict).collect()
    assert out["era_col"].null_count() == 2  # declared-optional fill still works
    # None (legacy permissive) keeps filling anything, as the conform test above relies on
    permissive = SilverSpec(casts={"a": pl.Int8}, target=target)
    assert apply_silver_types(lf, permissive).collect()["era_col"].null_count() == 2


def test_spec_from_columns_validates_optional_target_names() -> None:
    from mortgage_data_manager.core.types import spec_from_columns

    with pytest.raises(ValueError, match="optional_target"):
        spec_from_columns([("a", pl.Int8)], optional_target=("typo",))
    spec = spec_from_columns([("a", pl.Int8), ("b", pl.Float64)], optional_target=("b",))
    assert spec.optional_target == ("b",)


def test_null_sentinels_handles_string_typed_numeric_columns():
    # Sentinel masking runs BEFORE the numeric casts, so a column a spec declares numeric
    # can still arrive as String (UMBS multifamily ships the WA credit-score columns as
    # text in 79 of 86 monthly files). `is_in` RAISES on that mismatch rather than
    # returning False, which took down the whole silver build for that kind.
    lf = pl.LazyFrame({"s": ["9999", " 999 ", "712", None]})
    out = lf.select(null_sentinels(pl.col("s"), (999, 9999), pl.String).alias("s")).collect()["s"]
    assert out.to_list() == [None, None, "712", None]  # padded sentinel masked too


def test_null_sentinels_numeric_path_is_unchanged():
    lf = pl.LazyFrame({"n": [9999, 999, 712, None]})
    out = lf.select(null_sentinels(pl.col("n"), (999, 9999), pl.Int64).alias("n")).collect()["n"]
    assert out.to_list() == [None, None, 712, None]
    # dtype omitted -> same behavior for numeric columns (back-compat with existing specs)
    out2 = lf.select(null_sentinels(pl.col("n"), (999, 9999)).alias("n")).collect()["n"]
    assert out2.to_list() == [None, None, 712, None]
