"""Unit tests for the closed-form rate/prepayment primitives in ``analytics.calculated``.

These are deterministic financial identities (SMM/CPR/CDR conversions, level-payment
scheduled principal, security-level net SMM) with exact known answers, so per the project's
testing policy they are worth pinning: a scale slip (percent vs decimal, ``/12`` vs ``/1200``)
or an off-by-one in the amortization formula silently corrupts every prepayment/servicing
number downstream without tripping mypy or the schema check.
"""

from __future__ import annotations

import polars as pl
import pytest

from mortgage_data_manager.analytics.calculated import (
    conditional_default_rate,
    scheduled_principal,
    security_smm,
    single_monthly_mortality,
    smm_to_cpr,
)


def _e(expr: pl.Expr) -> float:
    return pl.select(expr).item()


def test_single_monthly_mortality_is_one_minus_actual_over_scheduled():
    # 5% of the scheduled balance prepaid this month; factor_prev is unused by the closed form.
    assert _e(single_monthly_mortality(0.95, 0.96, 1.0)) == pytest.approx(0.05)


def test_smm_to_cpr_annualizes_by_twelfth_power():
    assert _e(smm_to_cpr(0.01)) == pytest.approx(0.11361512828387077)
    assert _e(smm_to_cpr(0.0)) == 0.0


def test_conditional_default_rate_annualizes_monthly_default_share():
    # MDR = 1/100, so CDR = 1 - (1 - 0.01) ** 12.
    assert _e(conditional_default_rate(1.0, 100.0)) == pytest.approx(0.11361512828387077)


@pytest.mark.parametrize(
    ("upb", "rate_pct", "months", "expected"),
    [
        (360000.0, 0.0, 360, 1000.0),           # zero-rate -> straight-line UPB / n
        (100000.0, 6.0, 360, 99.5505251527569),  # standard amortizing first-month principal
    ],
)
def test_scheduled_principal_closed_form(upb, rate_pct, months, expected):
    got = _e(scheduled_principal(pl.lit(upb), pl.lit(rate_pct), pl.lit(months)))
    assert got == pytest.approx(expected)


def test_scheduled_principal_interest_only_is_zero():
    got = _e(
        scheduled_principal(pl.lit(100000.0), pl.lit(6.0), pl.lit(360), interest_only=pl.lit(True))
    )
    assert got == 0.0


def test_security_smm_drops_terminal_month_and_zeros_pure_amortization():
    # rate 0 -> scheduled principal is straight-line UPB/n; if next UPB falls by exactly that
    # amount there is no voluntary prepay, so SMM must be 0 and the denom is the scheduled bal.
    nxt = 100000.0 - 100000.0 / 360.0
    panel = pl.DataFrame(
        {
            "cusip": ["A", "A"],
            "period": [1, 2],
            "current_upb": [100000.0, nxt],
            "current_rate": [0.0, 0.0],
            "remaining_months": [360, 359],
        }
    ).lazy()
    out = security_smm(panel).collect()
    # The last month has no next-month UPB, so it is dropped.
    assert out.height == 1
    assert out["period"].to_list() == [1]
    assert out["smm"][0] == pytest.approx(0.0, abs=1e-9)
    assert out["smm_denom"][0] == pytest.approx(nxt)


def test_security_smm_flags_real_prepay_as_bounded_positive():
    # Next UPB drops by far more than scheduled principal -> SMM in (0, 1).
    panel = pl.DataFrame(
        {
            "cusip": ["B", "B", "B"],
            "period": [1, 2, 3],
            "current_upb": [100000.0, 90000.0, 80000.0],
            "current_rate": [4.0, 4.0, 4.0],
            "remaining_months": [360, 359, 358],
        }
    ).lazy()
    out = security_smm(panel).collect()
    assert out.height == 2  # terminal month dropped
    assert (out["smm"] > 0).all()
    assert (out["smm"] <= 1).all()
