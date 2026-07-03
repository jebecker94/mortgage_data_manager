"""Unit tests for the closed-form servicing-PV and PSA prepayment math in ``analytics.servicing``.

Deterministic given the origination terms plus fixed assumptions (no model, no fitted data), so
the outputs are exact and worth locking down. The regression seams these guard: the PSA ramp
constants (30-month ramp to 6% terminal CPR), the CPR<->SMM annualization, the level-payment
balance factor (percent ``/1200`` vs monthly-decimal scaling), and the expected/realized
servicing-fee present-value strip.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from mortgage_data_manager.analytics.servicing import (
    _scheduled_balance_factor_np,
    cpr_to_smm,
    expected_servicing_pv,
    psa_cpr,
    realized_servicing_pv,
    scheduled_balance_factor_expr,
)


def test_psa_cpr_ramps_to_terminal_then_holds():
    # 100% PSA: linear 0 -> 6% over the first 30 months, flat thereafter.
    assert psa_cpr(np.array([15, 30, 60])).tolist() == [0.03, 0.06, 0.06]
    assert psa_cpr(np.array([30]), 2.0)[0] == pytest.approx(0.12)  # 200% PSA scales the curve


def test_cpr_to_smm_matches_known_value():
    assert cpr_to_smm(np.array([0.06]))[0] == pytest.approx(0.005143012831822946)


@pytest.mark.parametrize(
    ("monthly_rate", "term", "age", "expected"),
    [
        (0.004, 360, 0, 1.0),     # age 0 -> full balance remaining
        (0.004, 360, 360, 0.0),   # age >= term -> paid off
        (0.0, 360, 180, 0.5),     # zero-rate degenerate -> straight-line
    ],
)
def test_scheduled_balance_factor_np(monthly_rate, term, age, expected):
    got = _scheduled_balance_factor_np(np.array([monthly_rate]), np.array([term]), age)[0]
    assert got == pytest.approx(expected)


def test_scheduled_balance_factor_expr_matches_numpy():
    # The polars expr takes rate in PERCENT (divides by 1200 internally); the numpy version
    # takes an already-monthly-decimal rate. Parity across a grid is the cheapest guard on
    # exactly that percent-vs-decimal scale trap.
    rates_pct = [3.0, 4.5, 6.0]
    terms = [360, 180, 240]
    ages = [12, 120, 240]  # last row hits age == term -> 0.0
    df = pl.DataFrame({"rate_pct": rates_pct, "term": terms, "age": ages})
    got = df.select(scheduled_balance_factor_expr("rate_pct", "term", "age").alias("f"))["f"]
    want = [
        _scheduled_balance_factor_np(np.array([rp / 1200.0]), np.array([t]), a)[0]
        for rp, t, a in zip(rates_pct, terms, ages, strict=True)
    ]
    for g, w in zip(got.to_list(), want, strict=True):
        assert g == pytest.approx(w, abs=1e-9)


def test_expected_servicing_pv_invariants_and_level():
    loans = pl.DataFrame({"orig_upb": [100000.0], "orig_rate": [4.0], "orig_term": [360]})
    out = expected_servicing_pv(loans)
    pv = out["expected_servicing_pv"][0]
    bps = out["expected_servicing_bps"][0]
    # bps is exactly the PV expressed in bps of original UPB.
    assert bps == pytest.approx(pv / 100000.0 * 10000.0)
    # 25bp fee / 10% discount / 100% PSA on a 30yr 4% loan sits well inside (100, 200) bps.
    assert 100.0 < bps < 200.0
    assert bps == pytest.approx(142.2756, abs=1e-3)


def test_expected_servicing_pv_zero_fee_is_zero():
    loans = pl.DataFrame({"orig_upb": [100000.0], "orig_rate": [4.0], "orig_term": [360]})
    out = expected_servicing_pv(loans, servicing_fee=0.0)
    assert out["expected_servicing_pv"][0] == 0.0


def test_realized_servicing_pv_terminated_loan():
    panel = pl.DataFrame(
        {
            "loan_id": ["L", "L", "L"],
            "loan_age": [1, 2, 3],
            "current_upb": [99900.0, 99800.0, 0.0],  # terminal month legitimately zero
            "zero_bal_code": [None, None, "01"],
            "orig_upb": [100000.0, 100000.0, 100000.0],
            "orig_rate": [4.0, 4.0, 4.0],
            "orig_term": [360, 360, 360],
        }
    ).lazy()
    out = realized_servicing_pv(panel).collect().row(0, named=True)
    assert out["months_observed"] == 3
    assert out["last_age"] == 3
    assert out["is_terminated"]  # a zero-balance code was observed
    assert out["realized_servicing_pv"] == pytest.approx(61.4115, abs=1e-2)
    assert out["realized_servicing_bps"] == pytest.approx(
        out["realized_servicing_pv"] / 100000.0 * 10000.0
    )
