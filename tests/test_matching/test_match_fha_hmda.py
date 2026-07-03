"""Unit tests for the deterministic dedup/filter helpers in the FHA<->HMDA post-2018 matcher.

These are silent-corruption regression guards. ``_keep_unique_matches``,
``_drop_small_lenders`` and ``_apply_common_filters`` encode match-quality decisions where a
bug produces a plausible-but-wrong crosswalk that no type checker catches:

* ``_keep_unique_matches`` is the 1:1 collision rule — it must retain a candidate only when its
  ``FHA_Index`` AND ``HMDAIndex`` each appear exactly once, and must strip the ``CountLoan*``
  scratch columns so they never leak into the crosswalk.
* ``_drop_small_lenders`` thresholds on the total ROW count per ``Master Number`` (``pl.len``,
  not ``n_unique``); confusing the two would silently keep/drop the wrong lenders.
* ``_apply_common_filters`` enforces loan-purpose, rate-tolerance, sponsor/channel and ARM
  consistency between the FHA and HMDA sides — each a directional drop rule that is easy to
  invert.

Expected values below were pinned to observed live output, not to the docstrings.
"""

from __future__ import annotations

import polars as pl
import pytest

from mortgage_data_manager.matching.match_fha_hmda.match_hmda_fha_post2018 import (
    _apply_common_filters,
    _drop_small_lenders,
    _keep_unique_matches,
)


def test_keep_unique_matches_keeps_one_to_one_and_drops_helper_cols() -> None:
    # FHA_Index 2 appears twice (dup on FHA side); HMDAIndex 30 appears twice (dup on HMDA side).
    # Only rows unique on BOTH sides survive: (1,10) and (4,40). (3,30) dies on the HMDA dup.
    df = pl.DataFrame(
        {
            "FHA_Index": [1, 2, 2, 3, 4],
            "HMDAIndex": [10, 20, 30, 30, 40],
            "extra": ["a", "b", "c", "d", "e"],
        }
    )
    out = _keep_unique_matches(df)

    assert out["FHA_Index"].to_list() == [1, 4]
    assert out["HMDAIndex"].to_list() == [10, 40]
    # A non-key passenger column rides along untouched...
    assert out["extra"].to_list() == ["a", "e"]
    # ...but the CountLoan* scratch columns must NOT leak into the output.
    assert out.columns == ["FHA_Index", "HMDAIndex", "extra"]


def test_drop_small_lenders_counts_rows_not_unique() -> None:
    # 'B' has two rows but only ONE unique FHA_Index (9). The threshold is on pl.len (row count),
    # so 'B' survives at min_matches=2; keying on n_unique would wrongly drop it. 'C' (1 row) dies.
    df = pl.DataFrame(
        {
            "Master Number": ["A", "A", "B", "B", "C"],
            "FHA_Index": [1, 2, 9, 9, 6],
        }
    )
    out = _drop_small_lenders(df, min_matches=2)

    assert out["Master Number"].to_list() == ["A", "A", "B", "B"]
    assert out["Count Matches (Total)"].to_list() == [2, 2, 2, 2]


@pytest.mark.parametrize(
    ("min_matches", "expected"),
    [
        (2, ["A", "A", "A", "B", "B"]),  # A(3) and B(2) clear the bar; C(1) drops
        (3, ["A", "A", "A"]),  # threshold is inclusive (>=): B(2) now drops, A(3) stays
    ],
)
def test_drop_small_lenders_threshold_is_inclusive(
    min_matches: int, expected: list[str]
) -> None:
    df = pl.DataFrame(
        {
            "Master Number": ["A", "A", "A", "B", "B", "C"],
            "FHA_Index": [1, 2, 3, 4, 5, 6],
        }
    )
    out = _drop_small_lenders(df, min_matches=min_matches)
    assert out["Master Number"].to_list() == expected


def _common_filter_fixture() -> pl.DataFrame:
    """One clearly-passing FRM row, one passing ARM row, and one row failing each drop rule."""
    return pl.DataFrame(
        {
            "row_id": [
                "good_frm",
                "bad_lp1",  # loan_purpose 1 + FHA-refi label -> inconsistent purchase/refi
                "bad_lp2",  # loan_purpose 31/32 + 'Purchase' label -> inconsistent
                "bad_rate",  # rate off by 5pp -> exceeds tolerance
                "bad_spons2",  # submission 2 (correspondent) but Sponsor Number null
                "bad_spons1",  # submission 1 (retail) but Sponsor Number present
                "bad_arm_frm",  # FHA says FRM, HMDA ARM indicator = 1
                "bad_arm_arm",  # FHA says ARM, HMDA ARM indicator = 0
                "good_arm",
            ],
            "loan_purpose": [1, 1, 31, 1, 1, 1, 1, 1, 1],
            "Loan Purpose": [
                "Purchase",
                "Refi_FHA",
                "Purchase",
                "Purchase",
                "Purchase",
                "Purchase",
                "Purchase",
                "Purchase",
                "Purchase",
            ],
            "interest_rate": [0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05],
            "Interest Rate": [0.05, 0.05, 0.05, 0.10, 0.05, 0.05, 0.05, 0.05, 0.05],
            "submission_of_application": [1, 1, 1, 1, 2, 1, 1, 1, 2],
            "Sponsor Number": [None, None, None, None, None, "123", None, None, "999"],
            "Product Type": ["FRM", "FRM", "FRM", "FRM", "FRM", "FRM", "FRM", "ARM", "ARM"],
            "1(ARM)": [0, 0, 0, 0, 0, 0, 1, 0, 1],
        }
    )


def test_apply_common_filters_drops_every_inconsistency() -> None:
    out = _apply_common_filters(_common_filter_fixture(), rate_tolerance=0.005)

    # Exactly the two internally-consistent rows survive.
    assert out["row_id"].to_list() == ["good_frm", "good_arm"]
    # The temporary difference column is cleaned up after the rate filter runs.
    assert "Interest Rate Difference" not in out.columns


def test_apply_common_filters_none_tolerance_skips_only_the_rate_rule() -> None:
    # With rate_tolerance=None the rate check is skipped, so the rate-mismatched row now survives
    # while every other consistency rule still fires.
    out = _apply_common_filters(_common_filter_fixture(), rate_tolerance=None)
    assert out["row_id"].to_list() == ["good_frm", "bad_rate", "good_arm"]
