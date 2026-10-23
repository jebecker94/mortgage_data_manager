"""Regression tests for the pre-2018 HMDA ↔ FHA lender bridge's pure logic.

Three things earn a test here; the rest of the builder is validated against real
data and reconciled with its measured counts.

1. :func:`hmda_respondent_key`. One regex, and getting it wrong loses all of 2017
   silently — ``file_type=d`` writes ``0000013303`` / ``63-1005978`` where
   ``file_type=a`` writes ``13303`` / ``631005978``, so an un-normalized pooled
   analysis carries only 113 of 1,658 respondent keys from 2016 into 2017.
2. :func:`_check_provenance`. The prototype shipped a single ``route`` column
   that recorded which row survived deduplication rather than where the evidence
   came from, so every *agreeing* lender (70.2% of FHA volume) was labelled
   ``name`` and a ``route == 'learned'`` filter would have reported 42.0%
   instead of 76.4% — a 34pp error. These pin its replacement.
3. :func:`_modal`. A tied plurality must yield no value. The earlier version
   broke the tie on the value itself, which admitted ``+state`` bridge entries on
   an alphabetical coin flip.
"""

from __future__ import annotations

import polars as pl
import pytest

from mortgage_data_manager.matching.match_hmda_fha.lender_bridge import (
    _check_provenance,
    _modal,
    hmda_respondent_key,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0000013303", "13303"),  # file_type=d zero-pads
        ("13303", "13303"),  # file_type=a does not -- and the key is idempotent
        ("63-1005978", "631005978"),  # d hyphenates tax ids
        ("631005978", "631005978"),
        ("0000000000", ""),  # degenerate: an all-zero id normalizes away
        ("00-0", ""),
        ("0000C1234", "C1234"),  # alphanumeric ids keep their letters
    ],
)
def test_hmda_respondent_key(raw: str, expected: str) -> None:
    got = pl.DataFrame({"respondent_id": [raw]}).select(hmda_respondent_key())
    assert got.columns == ["resp_key"]
    assert got.item() == expected


def _bridge(**overrides: list[object]) -> pl.DataFrame:
    base: dict[str, list[object]] = {
        "in_name_route": [True, True, False],
        "in_learned_route": [False, True, True],
        "provenance": ["name_only", "agree", "learned_only"],
    }
    return pl.DataFrame({**base, **overrides})


def test_check_provenance_accepts_every_valid_combination() -> None:
    _check_provenance(_bridge())


def test_check_provenance_rejects_a_row_in_no_route() -> None:
    frame = _bridge(
        in_name_route=[True, True, False],
        in_learned_route=[False, True, False],
        provenance=["name_only", "agree", "name_only"],
    )
    with pytest.raises(ValueError, match="provenance invariant"):
        _check_provenance(frame)


def test_check_provenance_rejects_agree_without_both_routes() -> None:
    with pytest.raises(ValueError, match="provenance invariant"):
        _check_provenance(_bridge(provenance=["agree", "agree", "learned_only"]))


def test_check_provenance_rejects_nulls() -> None:
    with pytest.raises(ValueError, match="provenance invariant"):
        _check_provenance(_bridge(provenance=["name_only", "agree", None]))
    with pytest.raises(ValueError, match="provenance invariant"):
        _check_provenance(_bridge(in_learned_route=[False, True, None]))


def test_modal_returns_the_strict_plurality() -> None:
    rows = pl.DataFrame(
        {"fha_id_type": ["originating"] * 3, "fha_id": ["00001"] * 3, "state": ["TX", "TX", "NY"]}
    )
    assert _modal(rows, "state").to_dicts() == [
        {"fha_id_type": "originating", "fha_id": "00001", "state": "TX"}
    ]


def test_modal_is_null_on_a_tied_plurality() -> None:
    rows = pl.DataFrame(
        {
            "fha_id_type": ["sponsor"] * 4 + ["originating"],
            "fha_id": ["76690"] * 4 + ["24079"],
            "state": ["NY", "TX", "TX", "NY", "KS"],
        }
    )
    assert _modal(rows, "state").sort("fha_id").to_dicts() == [
        {"fha_id_type": "originating", "fha_id": "24079", "state": "KS"},
        {"fha_id_type": "sponsor", "fha_id": "76690", "state": None},
    ]
