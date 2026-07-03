"""Unit tests for the match-key construction and dedup rules of the FHA<->GNMA matcher.

GNMA carries no FHA case number, so the crosswalk is built by blocking on a handful of
derived keys — loan amount bucketed to thousands, interest rate bucketed to 0.125%, purchase
flag — then keeping mutual-best 1:1 pairs. Every one of those derivations is a silent-corruption
seam: a wrong scale or the wrong rounding mode yields a plausible-but-wrong crosswalk that no
type checker catches. The load-bearing asymmetries pinned here are:

* FHA amounts are ``FLOOR(loan_amount / 1000)`` while GNMA amounts are ``ROUND(loan_amount / 1000)``
  — the two sides intentionally disagree on the boundary case (e.g. $200,999 -> FHA 200, GNMA 201),
  mirroring GNMA's real-world truncation of disclosed balances.
* GNMA rescales ``Original Principal Balance`` from cents (``/100``) and the interest rate by
  ``/1000`` (3750 -> 3.75%), **not** ``/100`` — the ``/1000`` trap is easy to get wrong.
* the tie-drop dedup keeps a pair only when both IDs appear exactly once, so a 2-FHA collision onto
  a single GNMA loan must produce zero matches, never an arbitrary 1:1 pick.
"""

from __future__ import annotations

import polars as pl
import pytest

from mortgage_data_manager.matching.match_fha_gnma.config import ROUND1_TOLERANCES
from mortgage_data_manager.matching.match_fha_gnma.matching import match_fha_gnma_round
from mortgage_data_manager.matching.match_fha_gnma.prepare_fha import prepare_fha_for_matching
from mortgage_data_manager.matching.match_fha_gnma.prepare_gnma import prepare_gnma_for_matching


def _fha_input(
    amount: float, rate: float, purpose: str, product: str, index: str = "F1"
) -> pl.DataFrame:
    """One-row FHA silver frame carrying exactly the columns prepare_fha reads."""
    return pl.DataFrame(
        {
            "FHA_Index": [index],
            "Property State": ["DC"],
            "Property Zip": ["20001"],
            "Year": [2020],
            "Month": [3],
            "Mortgage Amount": [amount],
            "Interest Rate": [rate],
            "Loan Purpose": [purpose],
            "Product Type": [product],
        }
    )


def _gnma_input(opb: int, rate_raw: int, purpose: int, index: str = "G1") -> pl.DataFrame:
    """One-row GNMA silver frame carrying exactly the columns prepare_gnma reads."""
    return pl.DataFrame(
        {
            "Disclosure Sequence Number": [index],
            "State": ["DC"],
            "Loan Origination Date": ["20200315"],
            "Loan Interest Rate": [rate_raw],
            "Original Principal Balance": [opb],
            "Loan Purpose": [purpose],
        }
    )


def _prepared_side(id_col: str, id_val: str, amount: float, rate: float) -> pl.DataFrame:
    """A minimal already-prepared frame with every key match_fha_gnma_round joins on."""
    return pl.DataFrame(
        {
            id_col: [id_val],
            "state": ["DC"],
            "origination_year": [2020],
            "origination_month": [3],
            "is_purchase": [1],
            "loan_amount_thousands": [200],
            "rate_bucket": [30],
            "loan_amount": [amount],
            "interest_rate": [rate],
        }
    )


def test_prepare_fha_builds_floor_amount_and_rounded_rate_keys() -> None:
    # $200,999 must FLOOR to 200 (truncation), not round to 201; rate 4.20% rounds to bucket 34.
    df = pl.concat(
        [
            _fha_input(200999.0, 3.75, "Purchase", "Fixed Rate", "F1"),
            _fha_input(315999.0, 4.20, "Refi_FHA", "Adjustable Rate", "F2"),
            _fha_input(200500.0, 3.75, "Refi_Conv_Curr", "Fixed Rate", "F3"),
        ],
        how="diagonal_relaxed",
    )
    out = prepare_fha_for_matching(df)

    assert out["loan_amount_thousands"].to_list() == [200, 315, 200]
    assert out["rate_bucket"].to_list() == [30, 34, 30]
    assert out["loan_amount_thousands"].dtype == pl.Int64
    assert out["rate_bucket"].dtype == pl.Int32


@pytest.mark.parametrize(
    ("purpose", "product", "expected_is_purchase", "expected_is_arm"),
    [
        ("Purchase", "Fixed Rate", 1, 0),
        ("Refi_FHA", "Adjustable Rate", 0, 1),
        ("Refi_Conv_Curr", "Fixed Rate", 0, 0),
    ],
)
def test_prepare_fha_purchase_and_arm_indicators(
    purpose: str, product: str, expected_is_purchase: int, expected_is_arm: int
) -> None:
    # is_purchase keys off the exact "Purchase" string; is_arm keys off "Adjustable Rate".
    out = prepare_fha_for_matching(_fha_input(200500.0, 3.75, purpose, product))
    assert out["is_purchase"].to_list() == [expected_is_purchase]
    assert out["is_arm"].to_list() == [expected_is_arm]


def test_prepare_gnma_rescales_cents_and_rate_by_1000() -> None:
    # OPB is cents (/100 -> dollars); interest rate is /1000 (3750 -> 3.75), NOT /100.
    out = prepare_gnma_for_matching(
        pl.concat(
            [
                _gnma_input(20099900, 3750, 1, "G1"),
                _gnma_input(31500000, 4200, 2, "G2"),
            ],
            how="diagonal_relaxed",
        )
    )
    assert out["loan_amount"].to_list() == pytest.approx([200999.0, 315000.0])
    assert out["interest_rate"].to_list() == pytest.approx([3.75, 4.20])
    assert out["rate_bucket"].to_list() == [30, 34]
    # GNMA Loan Purpose 1 == Purchase; anything else -> 0.
    assert out["is_purchase"].to_list() == [1, 0]
    assert out["origination_year"].to_list() == [2020, 2020]


def test_fha_floor_vs_gnma_round_amount_key_disagree_on_boundary() -> None:
    # The load-bearing asymmetry: the SAME $200,999 nominal amount buckets to 200 on the FHA side
    # (FLOOR) but 201 on the GNMA side (ROUND). A regression that unified these would silently
    # shift which loans can ever block together.
    fha_key = prepare_fha_for_matching(
        _fha_input(200999.0, 3.75, "Purchase", "Fixed Rate")
    )["loan_amount_thousands"].item()
    gnma_key = prepare_gnma_for_matching(
        _gnma_input(20099900, 3750, 1)
    )["loan_amount_thousands"].item()

    assert fha_key == 200
    assert gnma_key == 201


def test_match_round_yields_single_mutual_best_pair() -> None:
    # A genuine block: same state/year/month/purpose/amount-bucket/rate-bucket -> exactly one 1:1
    # pair. Score = amount_diff/999 + rate_diff/0.125 = (200500-200000)/999 + 0.
    fha = _prepared_side("FHA_Index", "F1", amount=200500.0, rate=3.75)
    gnma = _prepared_side("gnma_loan_id", "G1", amount=200000.0, rate=3.75)

    matched = match_fha_gnma_round(fha, gnma, ROUND1_TOLERANCES)

    assert len(matched) == 1
    row = matched.row(0, named=True)
    assert row["FHA_Index"] == "F1"
    assert row["gnma_loan_id"] == "G1"
    assert row["rank_in_fha"] == 1
    assert row["rank_in_gnma"] == 1
    assert row["match_score"] == pytest.approx(500 / 999)


def test_match_round_drops_ambiguous_ties() -> None:
    # Two FHA loans identical on every key both block onto one GNMA loan with the same score.
    # The tie-drop rule keeps a pair only when both IDs are unique, so the collision must resolve
    # to ZERO matches — never an arbitrary 1:1 pick that would fabricate a crosswalk edge.
    fha = pl.concat(
        [
            _prepared_side("FHA_Index", "F1", amount=200500.0, rate=3.75),
            _prepared_side("FHA_Index", "F2", amount=200500.0, rate=3.75),
        ],
        how="diagonal_relaxed",
    )
    gnma = _prepared_side("gnma_loan_id", "G1", amount=200000.0, rate=3.75)

    matched = match_fha_gnma_round(fha, gnma, ROUND1_TOLERANCES)

    assert len(matched) == 0
