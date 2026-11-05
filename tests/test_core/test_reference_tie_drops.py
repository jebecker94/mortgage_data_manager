"""Regression tests: tied ZIP3 candidates are dropped, never ordered by ZIP3."""

from __future__ import annotations

import polars as pl

from mortgage_data_manager.core.reference.tract_zip3 import _top_candidates


def _shares(rows: list[tuple[str, str, float]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=["census_tract", "zip3", "share"], orient="row").with_columns(
        w=pl.col("share")
    )


def test_tract_zip3_runner_up_tie_is_dropped() -> None:
    kept = _top_candidates(_shares([("t1", "100", 0.4), ("t1", "200", 0.3), ("t1", "300", 0.3)]))
    assert kept["zip3"].to_list() == ["100"]


def test_tract_zip3_two_way_top_tie_keeps_both() -> None:
    kept = _top_candidates(_shares([("t1", "100", 0.5), ("t1", "200", 0.5)]))
    assert sorted(kept["zip3"].to_list()) == ["100", "200"]


def test_tract_zip3_three_way_top_tie_keeps_none() -> None:
    kept = _top_candidates(
        _shares([("t1", "100", 1 / 3), ("t1", "200", 1 / 3), ("t1", "300", 1 / 3)])
    )
    assert kept.is_empty()


def test_tract_zip3_runner_up_needs_min_share() -> None:
    kept = _top_candidates(_shares([("t1", "100", 0.9), ("t1", "200", 0.1)]))
    assert kept["zip3"].to_list() == ["100"]
