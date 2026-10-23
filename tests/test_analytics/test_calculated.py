"""Unit tests for the closed-form rate/prepayment primitives in ``analytics.calculated``.

These are deterministic financial identities (SMM/CPR/CDR conversions, level-payment
scheduled principal, security-level net SMM, balance-weighted rollups, roll rates) with exact
known answers, so per the project's
testing policy they are worth pinning: a scale slip (percent vs decimal, ``/12`` vs ``/1200``)
or an off-by-one in the amortization formula silently corrupts every prepayment/servicing
number downstream without tripping mypy or the schema check.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from mortgage_data_manager.analytics.calculated import (
    conditional_default_rate,
    delinquency_transition_rates,
    remaining_principal_balance,
    scheduled_principal,
    security_smm,
    single_monthly_mortality,
    smm_to_cpr,
    weighted_averages,
    weighted_net_cpr,
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
        (360000.0, 0.0, 360, 1000.0),  # zero-rate -> straight-line UPB / n
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


def test_remaining_principal_balance_sums_and_counts_by_group():
    lf = pl.LazyFrame({"g": ["a", "a", "b"], "current_upb": [100.0, 50.0, 25.0]})
    assert remaining_principal_balance(lf, by=["g"]).collect().rows() == [
        ("a", 150.0, 2),
        ("b", 25.0, 1),
    ]


def test_weighted_averages_drop_the_balance_of_loans_missing_the_value():
    lf = pl.LazyFrame(
        {
            "g": ["a", "a", "a", "b"],
            "fico": [700, 800, None, None],
            "current_upb": [100.0, 300.0, 600.0, 50.0],
        }
    )
    out = weighted_averages(lf, by=["g"], value_cols=["fico"]).collect()
    # (700*100 + 800*300) / 400: the 600 of balance with no FICO is not averaged in as zero.
    assert out["wa_fico"][0] == pytest.approx(775.0)
    assert out["wa_fico"][1] is None
    assert out["total_balance"].to_list() == [1000.0, 50.0]


def test_delinquency_transition_rates_pair_only_consecutive_observed_months():
    d = date
    lf = pl.LazyFrame(
        {
            "loan_id": ["A"] * 4 + ["B"] * 3,
            "period": [d(2020, 1, 1), d(2020, 2, 1), d(2020, 3, 1), d(2020, 5, 1)]
            + [d(2020, 1, 1), d(2020, 2, 1), d(2020, 3, 1)],
            "dlq_status": [0, 0, 1, 3, 0, 1, None],
        }
    ).reverse()  # input order must not matter
    # A: 0->0, 0->1, then Mar->May is a gap (dropped). B: 0->1, then 1->null (dropped).
    assert delinquency_transition_rates(lf).collect().rows() == [
        (0, 0, 1, pytest.approx(1 / 3)),
        (0, 1, 2, pytest.approx(2 / 3)),
    ]


def test_weighted_net_cpr_balance_weights_smm_then_annualizes():
    lf = pl.LazyFrame({"period": [1, 1], "smm": [0.01, 0.03], "smm_denom": [300.0, 100.0]})
    out = weighted_net_cpr(lf, by=["period"]).collect()
    assert out["weighted_smm"][0] == pytest.approx(0.015)
    assert out["net_cpr"][0] == pytest.approx(1 - 0.985**12)
