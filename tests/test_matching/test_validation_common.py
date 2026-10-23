"""Unit tests for the shared validation layer's one-row-per-loan guard on ``Side``.

Every validation rate counts universe rows, so a universe with a loan on two rows
double-counts it in both the denominator and the numerator. Two validators shipped that
way (hmda_fhlb's $10k-boundary candidate copies, hmda_fha's per-ZIP pool) before
``Side`` began rejecting such universes at construction.
"""

from __future__ import annotations

import polars as pl
import pytest

from mortgage_data_manager.matching.validation_common import Side, matched_ids


def _side(universe: pl.DataFrame, on: str | list[str] = "id") -> Side:
    return Side(universe, matched_ids(universe.head(1), on), on, "x")


def test_side_accepts_unique_universe():
    _side(pl.DataFrame({"id": [1, 2, 3]}))


def test_side_rejects_duplicated_key():
    with pytest.raises(ValueError, match="3 keyed rows but 2 distinct"):
        _side(pl.DataFrame({"id": [1, 1, 2], "zip": [10, 11, 12]}))


def test_side_checks_composite_key():
    # Unique on (year, id) even though id repeats across years.
    _side(pl.DataFrame({"year": [1, 2], "id": [7, 7]}), ["year", "id"])
    with pytest.raises(ValueError):
        _side(pl.DataFrame({"year": [1, 1], "id": [7, 7]}), ["year", "id"])


def test_side_ignores_null_keys():
    # Null-keyed rows cannot join to a match; each is one unmatched loan.
    _side(pl.DataFrame({"id": [None, None, 1]}, schema={"id": pl.Int64}))


def test_prepared_rechecks_fan_out():
    side = _side(pl.DataFrame({"id": [1, 2]}))
    zips = pl.LazyFrame({"id": [1, 1, 2], "zip": [10, 11, 12]})
    with pytest.raises(ValueError):
        side.prepared(lambda lf: lf.join(zips, on="id"))
