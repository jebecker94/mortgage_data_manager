"""Unit tests for the UMBS silver sentinel null-map (``umbs.config._sentinels_for``).

The map is derived from the concept in each column name rather than an explicit column
list, so a future disclosure-format vintage (the Dec-2025 ``VS4`` split added six score
columns) is covered without a second registry to keep in sync. That generality is exactly
what makes it worth pinning: the rule must catch every score/ratio column AND must not
reach the two look-alikes where masking would silently corrupt a real value.
"""

from __future__ import annotations

import polars as pl

from mortgage_data_manager.sources.umbs.config import UMBS_SILVER_SPECS, _sentinels_for


def test_score_and_ratio_columns_get_their_own_sentinel_sets():
    cols = [
        ("Classic FICO", pl.Int16),
        ("Updated VS4", pl.Int16),
        ("Borrower Credit Score", pl.Int16),
        ("Combined Loan-To-Value (CLTV)", pl.Int16),
        ("Origination Debt-To-Income Ratio", pl.Int16),
        ("Estimated Loan-To-Value (ELTV)", pl.Int16),
    ]
    got = _sentinels_for(cols)
    assert got["Classic FICO"] == (7777, 9999)
    assert got["Updated VS4"] == (7777, 9999)
    assert got["Borrower Credit Score"] == (7777, 9999)
    assert got["Combined Loan-To-Value (CLTV)"] == (999,)
    assert got["Origination Debt-To-Income Ratio"] == (999,)
    assert got["Estimated Loan-To-Value (ELTV)"] == (999,)


def test_mip_and_capitalized_amount_are_never_masked():
    # Mortgage Insurance Percent DOES carry a 999 "not available", but the mbs_umbs matcher
    # normalizes MI nulls to 0.0 (= uninsured) in inputs.py — masking here would convert
    # "not disclosed" into "no MI". Total Capitalized Amount is a dollar figure whose
    # distribution runs straight through 999 (observed neighbours at 998.16 / 1003.40), so
    # 999 is a value, not a sentinel. Both must stay out of the map.
    cols = [
        ("Mortgage Insurance Percent", pl.Float32),
        ("Total Capitalized Amount", pl.Float32),
    ]
    assert _sentinels_for(cols) == {}

    for spec in UMBS_SILVER_SPECS.values():
        assert "Mortgage Insurance Percent" not in spec.sentinels
        assert "Total Capitalized Amount" not in spec.sentinels


def test_non_numeric_roles_are_skipped():
    # String/date/bool/enum roles never carry these numeric sentinels; masking them would
    # be a no-op at best and a type error at worst.
    cols = [
        ("Property State", "str"),
        ("First Payment Date", "date_m"),
        ("First Time Home Buyer Indicator", "bool"),
        ("Loan Purpose", pl.Enum(["P", "R", "C"])),
    ]
    assert _sentinels_for(cols) == {}


def test_loan_level_and_pool_level_specs_are_both_covered():
    # ILLD/MLLD/FU share the loan-level spec; IS is pool-level (WA-prefixed aggregates).
    # A pool whose every loan is sentinel shows the sentinel in its weighted average, so
    # both grains need the map.
    assert "Debt-To-Income (DTI)" in UMBS_SILVER_SPECS["ILLD"].sentinels
    assert "WA Debt-To-Income (DTI)" in UMBS_SILVER_SPECS["IS"].sentinels
