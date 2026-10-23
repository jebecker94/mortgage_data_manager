"""Unit tests for the deterministic match-key / dedup logic in the HMDA<->MBS matcher.

These guard two silent-corruption seams where a bug produces a plausible-but-wrong
crosswalk that no type checker catches:

* ``normalize_name`` (``lender_seller_crosswalk``) collapses lender names to a join key.
  Its order of operations is load-bearing: abbreviation expansion (``co``->``company``,
  ``natl``->``national``) must run *before* iterative suffix stripping, bare ``na`` /
  ``n a`` must be dropped, and ``national`` alone must survive (only the two-word
  ``national association`` is a strippable suffix). A regression here silently merges
  or splits lenders.
* ``_unique_best_1to1`` (``gnma_matching``) enforces strict 1:1: a pair survives only when
  it is the *strictly-unique* best on BOTH sides, then a fixpoint loop guarantees neither id
  repeats. Ties are dropped rather than broken arbitrarily — a bug here would let a
  lower-scored duplicate survive, or invent a 1:1 pick where the data cannot support one.

Every expected value below was observed by running the target on the fixture, not guessed.
"""

from __future__ import annotations

import polars as pl
import pytest

from mortgage_data_manager.matching.match_hmda_gnma.matching_fha import _unique_best_1to1
from mortgage_data_manager.matching.match_hmda_gse.lender_seller_crosswalk import normalize_name


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
# _unique_best_1to1 — strict 1:1 unique-best collision rule                     #
# --------------------------------------------------------------------------- #
def test_unique_best_higher_score_wins_gnma_collision() -> None:
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

    out = _unique_best_1to1(matches)

    assert out.height == 1
    row = out.row(0, named=True)
    assert (row["HMDAIndex"], row["gnma_loan_id"], row["total_score"]) == ("H1", "G1", 8)


def test_unique_best_resolves_independent_collisions_to_strict_1to1() -> None:
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

    out = _unique_best_1to1(matches)

    assert out.height == 2
    survivors = {(r["HMDAIndex"], r["gnma_loan_id"]) for r in out.iter_rows(named=True)}
    assert survivors == {("H1", "G1"), ("H3", "G2")}
    # Strict 1:1 is the load-bearing invariant: neither id may repeat in the output.
    assert out["HMDAIndex"].n_unique() == out.height
    assert out["gnma_loan_id"].n_unique() == out.height


@pytest.mark.parametrize(
    ("hmda", "gnma"),
    [
        (["H1", "H2"], ["G1", "G1"]),  # two HMDA loans tie for the same GNMA loan
        (["H1", "H1"], ["G1", "G2"]),  # one HMDA loan ties across two GNMA loans
    ],
)
def test_unique_best_drops_ties_rather_than_picking_arbitrarily(hmda, gnma) -> None:
    # The strictly-unique rule is what keeps the crosswalk honest: when the top score is
    # shared, no pair is the unique best on that side, so the whole cell is dropped. Relaxing
    # this to "keep first" would manufacture 1:1 pairs the data cannot distinguish.
    matches = pl.DataFrame(
        {"HMDAIndex": hmda, "gnma_loan_id": gnma, "total_score": [8, 8], "extra": ["a", "b"]}
    )

    assert _unique_best_1to1(matches).height == 0


def test_unique_best_empty_passthrough() -> None:
    # A round can score zero accepted candidates; the typed-empty frame must survive the
    # window functions and the fixpoint loop without raising.
    empty = pl.DataFrame(
        schema={
            "HMDAIndex": pl.Utf8,
            "gnma_loan_id": pl.Utf8,
            "total_score": pl.Int64,
            "extra": pl.Utf8,
        }
    )

    assert _unique_best_1to1(empty).height == 0


# --------------------------------------------------------------------------- #
# admitted — score floor or absent (not contradicted) CLTV/DTI evidence       #
# --------------------------------------------------------------------------- #
def _scored(**over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "s_units": 3,
        "s_borr": 2,
        "s_month": 1,
        "s_dyad": 2,
        "s_cltv": 0,
        "s_dti": 0,
        "cltv_absent": True,
        "dti_absent": True,
    }
    row.update(over)
    row["total_score"] = sum(
        int(row[c]) for c in ("s_units", "s_borr", "s_month", "s_dyad", "s_cltv", "s_dti")
    )  # type: ignore[call-overload]
    return row


@pytest.mark.parametrize(
    ("row", "admit"),
    [
        (_scored(), True),  # streamline: everything present agrees, CLTV and DTI missing
        (_scored(cltv_absent=False), False),  # CLTV present and disagreeing blocks it
        (_scored(dti_absent=False), False),  # DTI present and disagreeing blocks it
        (_scored(s_dyad=0), False),  # every present component must score in full
        (_scored(s_month=0), False),
        (_scored(s_cltv=4, cltv_absent=False, s_dti=3, dti_absent=False), True),  # score floor
    ],
)
def test_admitted_score_floor_or_absent_evidence(row: dict[str, object], admit: bool) -> None:
    from mortgage_data_manager.matching.match_hmda_gnma.matching_fha import admitted

    assert pl.DataFrame([row]).select(admitted(11)).item() is admit
