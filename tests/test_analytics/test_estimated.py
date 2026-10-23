"""Unit tests for the default-hazard and lifetime-loss estimators in ``analytics.estimated``.

The model fits themselves are validated against real data, not here. These
lock down the stable pure logic around them: the credit-event code set staying consistent
with the combined tri-agency taxonomy, the ``y``/``y_default`` outcome construction, the
prediction plumbing contract of :func:`forecast_default_probability`, and the closed-form
survival/EAD/LGD accumulation in :func:`estimate_lifetime_loss` (checked against
hand-computed values on a constant-hazard, zero-rate synthetic panel where every term has
an exact answer). Also a regression test for ``fit_model(target=...)`` colliding with the
panel's existing ``y`` column.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl
import pytest

from mortgage_data_manager.analytics.estimated import (
    estimate_lifetime_loss,
    fit_model,
    forecast_default_probability,
)
from mortgage_data_manager.analytics.panel import _fill_terminal_loan_age, outcome_exprs
from mortgage_data_manager.analytics.schema import CREDIT_EVENT_ZB_CODES
from mortgage_data_manager.combined.loan_performance_schema import ZBC_CREDIT_EVENT_CODES


class _ConstResult:
    """Stub statsmodels result whose predict returns a constant probability."""

    def __init__(self, p: float) -> None:
        self.p = p

    def predict(self, x: np.ndarray) -> np.ndarray:
        return np.full(x.shape[0], self.p)


def _const_bundle(p: float) -> dict:
    """A minimal logit-shaped bundle predicting a constant hazard ``p``."""
    return {
        "kind": "logit",
        "result": _ConstResult(p),
        "feature_list": ["x1"],
        "_std_means": np.zeros(1),
        "_std_stds": np.ones(1),
    }


def test_credit_event_codes_match_combined_taxonomy():
    # The analytics set is derived from the combined tri-agency taxonomy; both must
    # say {third-party sale, short sale/charge-off, REO} = {02, 03, 09}.
    assert CREDIT_EVENT_ZB_CODES == frozenset({2, 3, 9})
    assert CREDIT_EVENT_ZB_CODES == frozenset(int(c) for c in ZBC_CREDIT_EVENT_CODES)


def test_outcome_exprs_flag_prepay_and_credit_events():
    df = pl.DataFrame(
        {
            "zero_bal_code": [None, 1, 2, 3, 9, 6, 96, 16],
            "orig_date": [date(2020, 2, 1)] * 8,
        }
    ).with_columns(outcome_exprs())
    assert df["y"].to_list() == [0, 1, 0, 0, 0, 0, 0, 0]
    assert df["y_default"].to_list() == [0, 0, 1, 1, 1, 0, 0, 0]
    assert df["orig_quarter"].to_list() == ["2020Q1"] * 8


def test_fill_terminal_loan_age_only_touches_nulls():
    # FNMA blanks Loan Age on the zero-balance terminal row; the fill derives it as
    # months since first payment + 1 and must never alter a disclosed age.
    lf = pl.LazyFrame(
        {
            "loan_age": [7, None, None],
            "period": [date(2020, 6, 1), date(2020, 7, 1), date(2021, 1, 1)],
            "first_payment_date": [date(2019, 12, 1)] * 3,
        }
    )
    got = _fill_terminal_loan_age(lf).collect()["loan_age"].to_list()
    assert got == [7, 8, 14]


def test_forecast_default_probability_contract():
    # Sigmoid stub with known coefficients: predicted_mdr must equal
    # sigmoid(b0 + b1*x) row by row (features standardized with mean 0 / std 1).
    beta = np.array([-2.0, 1.0])

    class _SigmoidResult:
        def predict(self, x: np.ndarray) -> np.ndarray:
            return 1.0 / (1.0 + np.exp(-(x @ beta)))

    bundle = {
        "kind": "logit",
        "result": _SigmoidResult(),
        "feature_list": ["x1"],
        "_std_means": np.zeros(1),
        "_std_stds": np.ones(1),
    }
    panel = pl.DataFrame(
        {
            "loan_id": ["A", "B", "C"],
            "period": [date(2021, m, 1) for m in (1, 2, 3)],
            "x1": [-1.0, 0.0, 2.0],
        }
    ).lazy()
    out = forecast_default_probability(panel, model=bundle).collect()
    assert out.columns == ["loan_id", "period", "predicted_mdr"]
    want = 1.0 / (1.0 + np.exp(-(beta[0] + beta[1] * np.array([-1.0, 0.0, 2.0]))))
    assert out["predicted_mdr"].to_numpy() == pytest.approx(want)


def _loss_panel() -> pl.DataFrame:
    # One cohort, one loan observed at ages 1-4. Zero note rate makes the scheduled
    # balance factor straight-line ((term - age)/term), so every term is exact.
    return pl.DataFrame(
        {
            "loan_id": ["L1"] * 4,
            "period": [date(2020, m, 1) for m in (2, 3, 4, 5)],
            "orig_quarter": ["2020Q1"] * 4,
            "loan_age": [1, 2, 3, 4],
            "orig_rate": [0.0] * 4,
            "orig_term": [100] * 4,
            "orig_upb": [200_000.0] * 4,
            "x1": [0.0] * 4,
        }
    )


def test_estimate_lifetime_loss_matches_hand_computed_value():
    h, lgd = 0.1, 0.5
    out = estimate_lifetime_loss(_loss_panel().lazy(), model=_const_bundle(h), lgd=lgd).collect()
    assert out.height == 1
    row = out.row(0, named=True)
    # Survival to the start of ages 1-4 is 0.9^(age-1); EAD factor is (100-(age-1))/100.
    surv = [0.9 ** (a - 1) for a in range(1, 5)]
    ead = [(100 - (a - 1)) / 100 for a in range(1, 5)]
    want_loss = sum(s * h * e * lgd for s, e in zip(surv, ead, strict=True))
    assert row["cohort"] == "2020Q1"
    assert row["cum_default_rate"] == pytest.approx(1 - 0.9**4)
    assert row["cum_prepay_rate"] == pytest.approx(0.0)
    assert row["expected_loss_rate"] == pytest.approx(want_loss)
    assert row["expected_loss_per_loan"] == pytest.approx(want_loss * 200_000.0)
    assert row["n_loans"] == 1
    assert row["max_age_observed"] == 4


def test_estimate_lifetime_loss_competing_prepay_risk_lowers_default():
    h, smm = 0.1, 0.05
    base = estimate_lifetime_loss(_loss_panel().lazy(), model=_const_bundle(h)).collect()
    competing = estimate_lifetime_loss(
        _loss_panel().lazy(), model=_const_bundle(h), prepay_model=_const_bundle(smm)
    ).collect()
    # Prepayments remove balance before it can default: survival compounds at
    # (1 - h - smm), so cumulative default falls to h * sum (0.85)^(age-1).
    want = h * sum(0.85 ** (a - 1) for a in range(1, 5))
    assert competing["cum_default_rate"][0] == pytest.approx(want)
    assert competing["cum_default_rate"][0] < base["cum_default_rate"][0]
    assert competing["cum_prepay_rate"][0] == pytest.approx(want / 2)  # smm = h/2, same survival


def test_estimate_lifetime_loss_discounting_shrinks_loss():
    h = 0.1
    undiscounted = estimate_lifetime_loss(_loss_panel().lazy(), model=_const_bundle(h)).collect()
    discounted = estimate_lifetime_loss(
        _loss_panel().lazy(), model=_const_bundle(h), discount_rate=0.12
    ).collect()
    assert 0 < discounted["expected_loss_rate"][0] < undiscounted["expected_loss_rate"][0]


def test_fit_model_accepts_nondefault_target_alongside_existing_y():
    # Regression: the panel carries BOTH outcomes, so fit_model(target="y_default")
    # must drop the resident "y" before renaming (polars raises DuplicateError otherwise).
    pytest.importorskip("statsmodels")
    rng = np.random.default_rng(0)
    n = 400
    x = rng.normal(size=n)
    y_default = (rng.random(n) < 1.0 / (1.0 + np.exp(-x))).astype(int)
    panel = pl.DataFrame(
        {
            "loan_id": [f"L{i}" for i in range(n)],
            "split": ["train"] * n,
            "y": rng.integers(0, 2, size=n),
            "y_default": y_default,
            "x1": x,
        }
    )
    bundle = fit_model(
        panel, target="y_default", spec={"model": "logit", "features": ["x1"], "name": "t"}
    )
    assert bundle["kind"] == "logit"
    assert bundle["feature_list"] == ["x1"]
