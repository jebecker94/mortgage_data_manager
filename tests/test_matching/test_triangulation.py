"""Tests for the aggregator ↔ issuer triangulation score (match_hmda_gnma/triangulation.py)."""

from __future__ import annotations

import polars as pl

from mortgage_data_manager.matching.match_hmda_gnma.triangulation import triangulate


def _fixtures() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    sp = pl.DataFrame(
        {
            "HMDAIndex_s": ["h1", "h2", "h3", "h4"],
            "lei_s": ["L_ORIG1", "L_ORIG2", "L_ORIG3", "L_ORIG4"],
            "lei_p": ["L_AGG", "L_AGG", "L_OTHER", None],
        }
    )
    issuer = pl.DataFrame(
        {"issuer_id": ["1001", "2002"], "issuer_name": ["BIG AGGREGATOR LLC", "OTHER BANK NA"]}
    )
    panel = pl.DataFrame(
        {
            "lei": ["L_ORIG1", "L_ORIG2", "L_ORIG3", "L_ORIG4", "L_AGG", "L_OTHER"],
            "name": [
                "Small Originator Inc",
                "Big Aggregator, LLC",  # originator that shares the issuer's name
                "Third Lender",
                "Fourth Lender",
                "Big Aggregator LLC",
                "Other Bank, N.A.",
            ],
        }
    )
    return sp, issuer, panel


def test_aggregator_rate_counts_purchaser_name_agreement() -> None:
    sp, issuer, panel = _fixtures()
    pairs = pl.DataFrame(
        {
            "HMDAIndex": ["h1", "h2", "h3", "h9"],
            "issuer_id": ["1001", "1001", "1001", "1001"],
        }
    )
    out = triangulate(pairs, sp, issuer, panel, "x")
    assert out["set"] == "x"
    assert out["n"] == 4
    # h9 has no sellers/purchasers record and is not testable
    assert out["n_testable"] == 3
    # h1, h2 -> L_AGG ("Big Aggregator LLC") == issuer 1001; h3 -> L_OTHER != issuer
    assert out["aggregator"] == round(2 / 3, 4)
    # only h2's originator name agrees with the issuer
    assert out["originator_ctrl"] == round(1 / 3, 4)


def test_issuer_id_dtype_is_normalised_and_empty_sets_return_none() -> None:
    sp, issuer, panel = _fixtures()
    pairs = pl.DataFrame({"HMDAIndex": ["h1"], "issuer_id": [1001]})
    out = triangulate(pairs, sp, issuer, panel)
    assert out["aggregator"] == 1.0

    empty = pl.DataFrame({"HMDAIndex": ["h9"], "issuer_id": ["1001"]})
    out = triangulate(empty, sp, issuer, panel)
    assert out["n_testable"] == 0
    assert out["aggregator"] is None
    assert out["originator_ctrl"] is None


def test_missing_purchaser_lei_scores_zero_not_null() -> None:
    sp, issuer, panel = _fixtures()
    pairs = pl.DataFrame({"HMDAIndex": ["h4"], "issuer_id": ["2002"]})
    out = triangulate(pairs, sp, issuer, panel)
    assert out["n_testable"] == 1
    assert out["aggregator"] == 0.0
