"""Unit tests for the deterministic match-key / dedup logic in the HMDA<->MBS matcher.

These guard three silent-corruption seams where a bug produces a plausible-but-wrong
crosswalk that no type checker catches:

* ``normalize_name`` (``build_master_crosswalk``) collapses lender names to a join key.
  Its order of operations is load-bearing: abbreviation expansion (``co``->``company``,
  ``natl``->``national``) must run *before* iterative suffix stripping, bare ``na`` /
  ``n a`` must be dropped, and ``national`` alone must survive (only the two-word
  ``national association`` is a strippable suffix). A regression here silently merges
  or splits lenders.
* ``_prepare_data_for_matching`` (``direct_match``) builds the blocking keys: the
  occupancy enum remap (1->O/2->S/3->I/else null), the quarter-point ``rate_bucket``,
  and the $10k ``umbs_loan_amount_rounded`` which uses a **+5000 bin-midpoint** offset
  (a multiple of 10k must map to +5000, not to itself) so it lines up with HMDA's
  mid-bin amounts.
* ``_deduplicate_matches`` (``fha_matching``) enforces strict 1:1 mutual-best: highest
  ``total_score`` wins each ``gnma_loan_id`` *and* each ``HMDAIndex`` at a fixpoint. A
  bug here would let a lower-scored duplicate survive.

Every expected value below was observed by running the target on the fixture, not guessed.
"""

from __future__ import annotations

import polars as pl
import pytest

from mortgage_data_manager.matching.match_hmda_mbs.build_master_crosswalk import normalize_name
from mortgage_data_manager.matching.match_hmda_mbs.direct_match import _prepare_data_for_matching
from mortgage_data_manager.matching.match_hmda_mbs.fha_matching import _deduplicate_matches


# --------------------------------------------------------------------------- #
# normalize_name — the lender-name join key                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ""),  # None guard
        ("", ""),  # empty guard
        ("The Quicken Loans, Inc.", "quicken loans"),  # 'the ' strip + inc->incorporated suffix
        ("Wells Fargo Bank, N.A.", "wells fargo"),  # 'n a' dropped, 'bank' suffix stripped
        ("JPMorgan Chase Bank, National Association", "jpmorgan chase"),  # 2-word suffix + bank
        ("ABC Co", "abc"),  # co->company then company stripped
        ("Some Corp", "some"),  # corp->corporation then corporation stripped
        ("NA", ""),  # bare 'na' collapses to empty
        ("Rocket Mortgage LLC", "rocket"),  # llc + mortgage both stripped iteratively
        ("Guaranteed Rate", "guaranteed rate"),  # no tokens to touch -> unchanged
    ],
)
def test_normalize_name_pins_observed_outputs(raw: str | None, expected: str) -> None:
    assert normalize_name(raw) == expected


def test_normalize_name_natl_expands_but_national_is_not_a_bare_suffix() -> None:
    # 'natl' -> 'national', but only the two-word 'national association' is a strippable
    # suffix, so a trailing lone 'national' must survive (otherwise 'First Natl' would
    # collapse to just 'first' and over-merge distinct lenders).
    assert normalize_name("First Natl") == "first national"


# --------------------------------------------------------------------------- #
# _prepare_data_for_matching — the blocking keys                               #
# --------------------------------------------------------------------------- #
def test_prepare_occupancy_map_and_rate_bucket() -> None:
    hmda = pl.DataFrame(
        {
            "occupancy_type": [1, 2, 3, 4, None],
            "interest_rate": [3.00, 3.10, 3.20, 3.125, 6.875],
        }
    )
    umbs = pl.DataFrame(
        {
            "umbs_loan_amount": [310000],
            "umbs_interest_rate": [3.20],
            "umbs_seller_name": ["rocket mortgage"],
        }
    )
    lender_seller = pl.DataFrame({"lei": ["L1"], "seller_name": ["rocket"]})

    hmda_out, _, _ = _prepare_data_for_matching(hmda, umbs, lender_seller)

    # Occupancy enum: 1->O, 2->S, 3->I, everything else (4, null) -> null.
    assert hmda_out["hmda_occupancy_mapped"].to_list() == ["O", "S", "I", None, None]

    # Quarter-point bucket = (rate/0.25).round()*0.25. 3.10 rounds down to 3.0, 3.20 up
    # to 3.25; 3.125 and 6.875 sit on a half-bin and go to the even neighbor (3.0, 7.0).
    assert hmda_out["rate_bucket"].to_list() == pytest.approx([3.0, 3.0, 3.25, 3.0, 7.0])


@pytest.mark.parametrize(
    ("amount", "expected_rounded"),
    [
        (310000, 315000),  # exact multiple of 10k -> +5000 midpoint, NOT itself
        (312000, 315000),  # floors to 310k bin then +5000
        (309999, 305000),  # floors down to the 300k bin then +5000
        (100000, 105000),  # low-value case
    ],
)
def test_prepare_umbs_amount_rounded_uses_bin_midpoint(amount: int, expected_rounded: int) -> None:
    hmda = pl.DataFrame({"occupancy_type": [1], "interest_rate": [3.0]})
    umbs = pl.DataFrame(
        {
            "umbs_loan_amount": [amount],
            "umbs_interest_rate": [3.0],
            "umbs_seller_name": ["wells fargo"],
        }
    )
    lender_seller = pl.DataFrame({"lei": ["L1"], "seller_name": ["wells"]})

    _, umbs_out, ls_out = _prepare_data_for_matching(hmda, umbs, lender_seller)

    assert umbs_out["umbs_loan_amount_rounded"].to_list() == [expected_rounded]
    # Result must stay integer so it hard-joins to HMDA's Int loan_amount bins.
    assert umbs_out["umbs_loan_amount_rounded"].dtype == pl.Int64
    # Seller names are uppercased on both the UMBS and lender-seller sides for the join.
    assert umbs_out["umbs_seller_name_upper"].to_list() == ["WELLS FARGO"]
    assert ls_out["seller_name_upper"].to_list() == ["WELLS"]


# --------------------------------------------------------------------------- #
# _deduplicate_matches — strict 1:1 mutual-best collision rule                 #
# --------------------------------------------------------------------------- #
def test_deduplicate_matches_higher_score_wins_gnma_collision() -> None:
    # Two HMDA loans (H1, H2) both claim the same GNMA loan G1. The higher-scored
    # pair (H1,G1)=8 must win the collision and the loser (H2,G1)=6 must be dropped.
    matches = pl.DataFrame(
        {
            "HMDAIndex": ["H1", "H2"],
            "gnma_loan_id": ["G1", "G1"],
            "total_score": [8, 6],
            "extra": ["a", "b"],
        }
    )

    out = _deduplicate_matches(matches)

    assert out.height == 1
    row = out.row(0, named=True)
    assert (row["HMDAIndex"], row["gnma_loan_id"], row["total_score"]) == ("H1", "G1", 8)


def test_deduplicate_matches_resolves_independent_collisions_to_strict_1to1() -> None:
    # Two independent gnma collisions (G1: H1@9 vs H2@6; G2: H3@7 vs H4@4). All four
    # HMDAIndex values are distinct, so the fixpoint is order-independent: each gnma id
    # is claimed by its highest-scored HMDA loan, yielding a strict-1:1 two-row result.
    matches = pl.DataFrame(
        {
            "HMDAIndex": ["H1", "H2", "H3", "H4"],
            "gnma_loan_id": ["G1", "G1", "G2", "G2"],
            "total_score": [9, 6, 7, 4],
            "extra": ["a", "b", "c", "d"],
        }
    )

    out = _deduplicate_matches(matches)

    assert out.height == 2
    survivors = {(r["HMDAIndex"], r["gnma_loan_id"]) for r in out.iter_rows(named=True)}
    assert survivors == {("H1", "G1"), ("H3", "G2")}
    # Strict 1:1 is the load-bearing invariant: neither id may repeat in the output.
    assert out["HMDAIndex"].n_unique() == out.height
    assert out["gnma_loan_id"].n_unique() == out.height


def test_deduplicate_matches_empty_passthrough() -> None:
    # Empty input returns unchanged (no crash on the group_by/unique fixpoint loop).
    assert _deduplicate_matches(pl.DataFrame()).shape == (0, 0)
