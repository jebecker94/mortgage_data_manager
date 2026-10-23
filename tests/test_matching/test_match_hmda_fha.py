"""Unit tests for the deterministic dedup/filter helpers in the FHA<->HMDA post-2018 matcher.

These are silent-corruption regression guards. The 1:1 collision rule and
``_apply_gates`` encode match-quality decisions where a bug produces a
plausible-but-wrong crosswalk that no type checker catches:

* ``resolve.drop_ambiguous`` (formerly the local ``_keep_unique_matches``) is the 1:1
  collision rule — it must retain a candidate only when its ``FHA_Index`` AND ``HMDAIndex``
  each appear exactly once, and must leave no scratch columns behind. The fixture here pins
  that rule against this matcher's own key names.
* ``_apply_gates`` enforces loan-purpose, rate-tolerance and (per round) sponsor/channel
  consistency between the FHA and HMDA sides — each a directional drop rule that is easy to
  invert. Fixed-versus-ARM agreement is deliberately NOT a rule (HMDA ``intro_rate_period``
  carries buydown periods for some filers; see the function docstring), so the fixture pins
  that rows whose rate-type flags disagree survive.

Expected values below were pinned to observed live output, not to the docstrings.
"""

from __future__ import annotations

from dataclasses import replace

import polars as pl

from mortgage_data_manager.matching.match_hmda_fha.matching_post2018 import _apply_gates
from mortgage_data_manager.matching.match_hmda_fha.rounds import ROUNDS_POST2018
from mortgage_data_manager.matching.resolve import drop_ambiguous

_STRICT = replace(ROUNDS_POST2018[0], rate_tolerance=0.005, sponsor_filter=True)


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
    out = drop_ambiguous(df, "FHA_Index", "HMDAIndex")

    assert out["FHA_Index"].to_list() == [1, 4]
    assert out["HMDAIndex"].to_list() == [10, 40]
    # A non-key passenger column rides along untouched...
    assert out["extra"].to_list() == ["a", "e"]
    # ...and the resolver's scratch columns must NOT leak into the output.
    assert out.columns == ["FHA_Index", "HMDAIndex", "extra"]


def _common_filter_fixture() -> pl.DataFrame:
    """Passing FRM/ARM rows, one row failing each drop rule, two rate-type disagreements (kept)."""
    return pl.DataFrame(
        {
            "row_id": [
                "good_frm",
                "bad_lp1",  # loan_purpose 1 + FHA-refi label -> inconsistent purchase/refi
                "bad_lp2",  # loan_purpose 31/32 + 'Purchase' label -> inconsistent
                "bad_rate",  # rate off by 5pp -> exceeds tolerance
                "bad_spons2",  # submission 2 (correspondent) but Sponsor Number null
                "bad_spons1",  # submission 1 (retail) but Sponsor Number present
                "keep_arm_frm",  # FHA Fixed Rate, HMDA ARM indicator = 1 -> NOT a drop rule
                "keep_arm_arm",  # FHA Adjustable Rate, HMDA ARM indicator = 0 -> NOT a drop rule
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
            "Product Type": ["Fixed Rate"] * 7 + ["Adjustable Rate"] * 2,
            "1(ARM)": [0, 0, 0, 0, 0, 0, 1, 0, 1],
        }
    )


def test_apply_gates_drops_every_inconsistency() -> None:
    out = _apply_gates(_common_filter_fixture(), _STRICT)

    # The consistent rows survive, and so do the rate-type disagreements.
    assert out["row_id"].to_list() == ["good_frm", "keep_arm_frm", "keep_arm_arm", "good_arm"]
    # The gates add no columns.
    assert out.columns == _common_filter_fixture().columns


def test_apply_gates_without_sponsor_filter_keeps_sponsor_mismatches() -> None:
    # A round with sponsor_filter off (a scored round scores it instead) keeps both
    # sponsor/channel mismatches while every other gate still fires.
    out = _apply_gates(_common_filter_fixture(), replace(_STRICT, sponsor_filter=False))
    assert out["row_id"].to_list() == [
        "good_frm",
        "bad_spons2",
        "bad_spons1",
        "keep_arm_frm",
        "keep_arm_arm",
        "good_arm",
    ]


def test_post2018_hmda_side_counts_each_loan_once():
    # Regression: the prepared HMDA pool carries one row per (loan, ZIP), and the
    # post-2018 validation used it as the loan universe, so a loan whose tract touches
    # several ZIPs counted once per ZIP (18.5M rows for 8.0M loans).
    from mortgage_data_manager.matching.match_hmda_fha.config import (
        HMDAFHAMatchingConfig as Config,
    )
    from mortgage_data_manager.matching.match_hmda_fha.validation import sides, yearly_rates

    year = Config.MIN_YEAR

    crosswalk = pl.LazyFrame({"FHA_Index": ["f1"], "HMDAIndex": ["h1"]})
    fha = pl.LazyFrame({"FHA_Index": ["f1", "f2"], "Year": [year, year]})
    hmda = pl.LazyFrame(
        {
            "HMDAIndex": ["h1", "h1", "h2"],  # h1 fans out across two ZIPs
            "activity_year": [year] * 3,
            "loan_amount": [205_000] * 3,
            "ZIP": ["10001", "10002", "10003"],
        }
    )
    row = yearly_rates(sides(crosswalk, fha, hmda)).row(0, named=True)
    assert (row["hmda_total"], row["hmda_matched"]) == (2, 1)
    assert (row["fha_total"], row["fha_matched"]) == (2, 1)
