"""Unit tests for servicer-name normalization and parent consolidation.

Stable pure logic (no data ingestion), so it is worth testing per the project's
testing policy: the normalization heuristic and the curated parent map are the
one piece of the transfer pipeline that encodes judgment, and a regression here
silently mis-classifies rebrands vs. true transfers.
"""

from __future__ import annotations

import polars as pl

from mortgage_data_manager.analytics.servicer_names import (
    add_servicer_parents,
    build_servicer_parent_crosswalk,
    normalize_servicer_name,
)


def _norm(name: str) -> str:
    return pl.DataFrame({"n": [name]}).select(normalize_servicer_name("n")).item()


def test_normalization_collapses_punctuation_and_suffix_variants():
    # Same entity, different punctuation/suffix spelling -> identical key.
    assert _norm("Amerihome Mortgage Company, LLC") == _norm("AMERIHOME MORTGAGE COMPANY LLC")
    assert _norm("Quicken Loans, LLC") == "QUICKENLOANS"
    assert _norm("JPMorgan Chase Bank, N.A.") == "JPMORGANCHASEBANK"


def test_normalization_strips_leading_the_and_standardizes_ampersand():
    assert _norm("The Money Source Inc.") == "MONEYSOURCE"
    assert _norm("M&T Bank") == _norm("M and T Bank")


def test_normalization_peels_stacked_suffixes():
    # "COMPANY LLC" is two stacked suffixes; both must be removed.
    assert _norm("Acme Mortgage Company LLC") == "ACMEMORTGAGE"


def test_add_servicer_parents_flags_intercompany_rebrand():
    df = pl.DataFrame(
        {
            "servicer_from": ["NEWREZ LLC", "WELLS FARGO BANK, N.A."],
            "servicer_to": ["NEW RESIDENTIAL MORTGAGE LLC", "ROCKET MORTGAGE, LLC"],
        }
    )
    out = add_servicer_parents(df)
    # NewRez and New Residential map to the same Rithm parent -> intercompany.
    assert out["intercompany"].to_list() == [True, False]
    assert out["parent_from"].to_list() == ["Rithm (NewRez)", "Wells Fargo"]
    assert out["sector_from"].to_list() == ["nonbank", "bank"]


def test_crosswalk_marks_manual_vs_fallback():
    df = pl.DataFrame(
        {
            "servicer_from": ["Quicken Loans, LLC", "Obscure Lender LLC"],
            "servicer_to": ["Rocket Mortgage, LLC", "Some Random Bank, N.A."],
        }
    )
    xw = build_servicer_parent_crosswalk(df)
    mapped = dict(zip(xw["raw_name"], xw["manually_mapped"], strict=True))
    assert mapped["Quicken Loans, LLC"] is True
    assert mapped["Obscure Lender LLC"] is False
    # Keyword fallback: "Bank" / "N.A." -> bank sector even when unmapped.
    sector = dict(zip(xw["raw_name"], xw["sector"], strict=True))
    assert sector["Some Random Bank, N.A."] == "bank"
    assert sector["Obscure Lender LLC"] == "nonbank"
