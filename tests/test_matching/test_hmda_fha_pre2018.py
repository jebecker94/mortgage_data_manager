"""Regression tests for the pre-2018 HMDA ↔ FHA matcher's pure logic.

Four things earn a test here; the data preparation, the ZIP fan, the bridge
filter, the recovery round and the orchestration are validated by running
against real data and reconciling with the measured per-year counts,
which is a stronger check than any dummy-data test.

1. **The endorsement-window predicate.** The whole yield hinges on it and an
   off-by-one is invisible in the output — the wrong window simply matches
   fewer loans.
2. **The amount key.** Pre-2018 HMDA amounts are already dollars and exact
   thousands; applying post-2018's $10k-midpoint rounding here takes the share
   of loans in a clean 1:1 cell from 26.7% to 0.8%.
3. **The 1:1 gate.** The "bridged output is a strict superset of the baseline"
   invariant holds only because the gate is exactly drop-all-ambiguous, so the
   rule is pinned.
4. **The margin round's window.** It reopens exactly the January the base
   window trims and nothing more; a longer tail adds pairs no better than chance.
"""

from __future__ import annotations

import polars as pl
import pytest

from mortgage_data_manager.matching.match_hmda_fha.matching_pre2018 import (
    amount_key,
    endorsement_window,
)
from mortgage_data_manager.matching.match_hmda_fha.rounds import ROUNDS_PRE2018
from mortgage_data_manager.matching.resolve import drop_ambiguous


def _all_months(first_year: int, last_year: int) -> pl.DataFrame:
    """Every (Year, Month) pair in an inclusive year range."""
    return pl.DataFrame(
        {
            "Year": [y for y in range(first_year, last_year + 1) for _ in range(12)],
            "Month": [m for _ in range(first_year, last_year + 1) for m in range(1, 13)],
        }
    )


def test_endorsement_window_is_feb_through_jan() -> None:
    """(1, 12) is exactly Feb Y through Jan Y+1."""
    admitted = _all_months(2013, 2017).filter(endorsement_window(2015, (1, 12)))
    assert admitted.height == 12
    assert list(zip(admitted["Year"], admitted["Month"], strict=True)) == [
        *[(2015, m) for m in range(2, 13)],
        (2016, 1),
    ]


def test_endorsement_window_zero_to_eleven_is_the_calendar_year() -> None:
    """The unshifted window is the activity year itself."""
    admitted = _all_months(2013, 2017).filter(endorsement_window(2015, (0, 11)))
    assert admitted.height == 12
    assert admitted["Year"].unique().to_list() == [2015]


def test_shipped_rounds_use_the_base_window_then_reopen_january() -> None:
    """The 1:1 rounds use Feb Y - Jan Y+1; the margin round adds January Y and nothing more."""
    months = _all_months(2013, 2017)
    base_windows = {r.window for r in ROUNDS_PRE2018 if not r.tiers}
    margin_windows = {r.window for r in ROUNDS_PRE2018 if r.tiers}
    assert base_windows == {(1, 12)}
    assert margin_windows == {(0, 12)}
    base = months.filter(endorsement_window(2015, (1, 12)))
    margin = months.filter(endorsement_window(2015, (0, 12)))
    extra = margin.join(base, on=["Year", "Month"], how="anti")
    assert list(zip(extra["Year"], extra["Month"], strict=True)) == [(2015, 1)]
    assert base.join(margin, on=["Year", "Month"], how="anti").is_empty()


@pytest.mark.parametrize(
    ("dollars", "expected"),
    [
        (237000, 237),  # NOT 235000: the post-2018 $10k midpoint is never applied
        (100000, 100),
        (0, 0),
        (417500, 418),  # exact-half rounds away from zero, as polars round() does
    ],
)
def test_amount_key_is_thousands(dollars: int, expected: int) -> None:
    df = pl.DataFrame({"loan_amount": [dollars]}).with_columns(amount_key("loan_amount"))
    assert df["amount_key"].item() == expected


def test_amount_key_separates_loans_the_post2018_bucket_would_merge() -> None:
    """$231k and $239k share a $10k midpoint bucket but must not share a key."""
    df = pl.DataFrame({"loan_amount": [231000, 239000]}).with_columns(amount_key("loan_amount"))
    assert df["amount_key"].to_list() == [231, 239]


def test_keep_unique_matches_is_drop_all_ambiguous() -> None:
    """Only pairs unique on BOTH indices survive; ambiguous cells are dropped whole."""
    pairs = pl.DataFrame(
        {
            "HMDAIndex": ["h1", "h2", "h2", "h3", "h4"],
            "FHA_Index": ["f1", "f2", "f3", "f4", "f4"],
        }
    )
    kept = drop_ambiguous(pairs, "HMDAIndex", "FHA_Index")
    assert kept.columns == ["HMDAIndex", "FHA_Index"]
    assert kept.rows() == [("h1", "f1")]
