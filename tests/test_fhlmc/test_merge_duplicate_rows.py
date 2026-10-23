"""Regression tests for merging a loan published in both FHLMC releases."""

from __future__ import annotations

import polars as pl
import pytest

from mortgage_data_manager.sources.fhlmc.import_silver import merge_duplicate_rows


def _merge(df: pl.DataFrame, *, exclude: bool) -> pl.DataFrame:
    return merge_duplicate_rows(df.lazy(), "id", exclude_conflicts=exclude).collect().sort("id")


@pytest.mark.parametrize("exclude", [False, True])
def test_single_rows_pass_through(exclude: bool) -> None:
    df = pl.DataFrame({"id": ["a", "b"], "x": [1, None], "s": ["p", "q"]})
    assert _merge(df, exclude=exclude).equals(df.sort("id"))


@pytest.mark.parametrize("exclude", [False, True])
def test_a_null_on_one_copy_is_filled_from_the_other(exclude: bool) -> None:
    df = pl.DataFrame({"id": ["a", "a"], "x": [1, None], "s": [None, "q"]})
    assert _merge(df, exclude=exclude).to_dicts() == [{"id": "a", "x": 1, "s": "q"}]


@pytest.mark.parametrize("exclude", [False, True])
def test_identical_copies_collapse(exclude: bool) -> None:
    df = pl.DataFrame({"id": ["a", "a"], "x": [1, 1], "s": ["q", "q"]})
    assert _merge(df, exclude=exclude).to_dicts() == [{"id": "a", "x": 1, "s": "q"}]


def test_a_disputed_field_is_nulled_and_the_key_kept() -> None:
    df = pl.DataFrame({"id": ["a", "a", "b"], "x": [1, 2, 3], "s": ["q", "q", "r"]})
    assert _merge(df, exclude=False).to_dicts() == [
        {"id": "a", "x": None, "s": "q"},
        {"id": "b", "x": 3, "s": "r"},
    ]


def test_a_disputed_field_drops_the_key_when_excluding() -> None:
    df = pl.DataFrame({"id": ["a", "a", "b"], "x": [1, 2, 3], "s": ["q", "q", "r"]})
    assert _merge(df, exclude=True)["id"].to_list() == ["b"]


def test_schema_and_all_null_fields_survive() -> None:
    df = pl.DataFrame(
        {"id": ["a", "a"], "x": [None, None], "s": ["q", "q"]},
        schema={"id": pl.String, "x": pl.Int64, "s": pl.String},
    )
    out = _merge(df, exclude=False)
    assert out.schema == df.schema
    assert out["x"].to_list() == [None]
