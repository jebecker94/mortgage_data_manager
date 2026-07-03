"""Loan-level mortgage servicing-right (MSR) revenue analytics.

Two deterministic, closed-form servicing-revenue measures on the GSE
single-family loans, both expressed as a present value at origination so they
are directly comparable:

- **Expected servicing PV** — the discounted value, *at origination*, of the
  servicing-fee stream the servicer expects to collect. The expected balance
  path is the scheduled (level-payment) amortization decayed by a prepayment
  assumption (PSA). No realized data and no fitted model enter, so given the
  origination terms + assumptions the value is exact. This is the
  forward-looking expectation a servicer would book the MSR against.
- **Realized servicing PV** — the discounted value, at origination, of the
  servicing fee actually earned along the loan's *observed* monthly balance
  path, summed to the last reported month. Right-censored (still-performing)
  loans carry servicing earned to date plus an ``is_terminated`` flag.

Both apply a flat annual servicing fee (25 bps by default) to the
beginning-of-month balance and discount monthly at a flat MSR rate (10% by
default). Because the PSA assumption is fixed (not estimated), both measures are
deterministic — hence this lives alongside :mod:`analytics.calculated` rather
than :mod:`analytics.estimated`.

Scope note: this models the gross servicing-*fee* revenue strip only. It does
not net out servicing cost, float, ancillary income, or recapture, and it stops
the strip at termination without modeling the credit/default timing of the
zero-balance event separately from voluntary prepayment.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)

#: Annual servicing fee applied to the outstanding balance (25 bps).
DEFAULT_SERVICING_FEE: float = 0.0025
#: Flat annual discount rate for present-valuing the servicing strip (MSR convention).
DEFAULT_ANNUAL_DISCOUNT: float = 0.10
#: Terminal CPR of the 100% PSA benchmark.
PSA_TERMINAL_CPR: float = 0.06
#: Age (months) over which 100% PSA ramps linearly to the terminal CPR.
PSA_RAMP_MONTHS: int = 30
#: Hard cap on the projection horizon (months) for the expected path.
MAX_TERM_MONTHS: int = 480


# ---------------------------------------------------------------------------
# Prepayment primitives (PSA)
# ---------------------------------------------------------------------------


def psa_cpr(age_months: np.ndarray, psa_multiple: float = 1.0) -> np.ndarray:
    """CPR under the PSA benchmark at the given loan ages.

    100% PSA ramps CPR linearly from 0 to :data:`PSA_TERMINAL_CPR` over the
    first :data:`PSA_RAMP_MONTHS` months, then holds flat. ``psa_multiple``
    scales the whole curve (e.g. ``1.5`` is 150% PSA).

    Args:
        age_months: Loan ages in months (1-based; age 1 is the first payment).
        psa_multiple: Multiple of the 100% PSA benchmark.

    Returns:
        Array of annualized CPRs (same shape as ``age_months``), clipped to <1.
    """
    ramp = np.minimum(age_months, PSA_RAMP_MONTHS) / PSA_RAMP_MONTHS
    return np.clip(psa_multiple * PSA_TERMINAL_CPR * ramp, 0.0, 0.999999)


def cpr_to_smm(cpr: np.ndarray) -> np.ndarray:
    """Convert an annualized CPR to a single monthly mortality: ``1-(1-CPR)**(1/12)``."""
    return 1.0 - (1.0 - cpr) ** (1.0 / 12.0)


def _scheduled_balance_factor_np(
    monthly_rate: np.ndarray, term: np.ndarray, age: int
) -> np.ndarray:
    """Remaining-balance factor B(age)/B(0) of a level-payment loan (numpy).

    Closed form ``((1+i)^N - (1+i)^age) / ((1+i)^N - 1)`` with a straight-line
    fallback for the zero-rate degenerate case. Ages beyond the term return 0.

    Args:
        monthly_rate: Per-loan monthly interest rate (decimal).
        term: Per-loan original term in months.
        age: Months elapsed since origination (scalar).

    Returns:
        Per-loan balance factor in [0, 1].
    """
    with np.errstate(over="ignore", invalid="ignore"):
        growth_n = (1.0 + monthly_rate) ** term
        growth_a = (1.0 + monthly_rate) ** age
        amort = np.where(
            monthly_rate > 0,
            (growth_n - growth_a) / (growth_n - 1.0),
            (term - age) / term,
        )
    factor = np.where(age >= term, 0.0, amort)
    return np.clip(factor, 0.0, 1.0)


def scheduled_balance_factor_expr(
    rate_pct_col: str, term_col: str, age_col: str
) -> pl.Expr:
    """Polars expr for the level-payment remaining-balance factor B(age)/B(0).

    Mirrors :func:`_scheduled_balance_factor_np` for use in lazy pipelines (the
    realized path uses it to impute beginning-of-month balance when the
    reported UPB is missing/zero on a still-performing month).

    Args:
        rate_pct_col: Column of annual note rates in percent (e.g. ``4.25``).
        term_col: Column of original terms in months.
        age_col: Column of loan ages in months.

    Returns:
        A Polars expression evaluating to the balance factor in [0, 1].
    """
    i = pl.col(rate_pct_col) / 1200.0
    n = pl.col(term_col)
    a = pl.col(age_col)
    growth_n = (1.0 + i) ** n
    growth_a = (1.0 + i) ** a
    amort = (
        pl.when(i > 0)
        .then((growth_n - growth_a) / (growth_n - 1.0))
        .otherwise((n - a) / n)
    )
    return pl.when(a >= n).then(0.0).otherwise(amort).clip(0.0, 1.0)


# ---------------------------------------------------------------------------
# Expected servicing PV at origination (PSA-decayed scheduled path)
# ---------------------------------------------------------------------------


def expected_servicing_pv(
    loans: pl.DataFrame,
    *,
    orig_upb_col: str = "orig_upb",
    rate_pct_col: str = "orig_rate",
    term_col: str = "orig_term",
    servicing_fee: float = DEFAULT_SERVICING_FEE,
    annual_discount: float = DEFAULT_ANNUAL_DISCOUNT,
    psa_multiple: float = 1.0,
) -> pl.DataFrame:
    """Per-loan expected discounted servicing revenue at origination.

    Projects each loan's expected beginning-of-month balance as the scheduled
    amortization factor times the cumulative PSA survival, accrues the monthly
    servicing fee on it, and discounts to origination at a flat monthly rate.

    The expected PV of a loan with original balance ``B0``, monthly note rate
    ``i``, term ``N`` is ``sum_{t=1..N} (sf/12) * B0 * sched(t-1) * surv(t-1) *
    (1+d/12)^-t`` where ``sched`` is the level-payment balance factor, ``surv``
    the cumulative ``(1-SMM)`` PSA survival, ``sf`` the annual fee, ``d`` the
    annual discount.

    Args:
        loans: One row per loan with the origination-terms columns below.
        orig_upb_col: Original UPB column (dollars).
        rate_pct_col: Original note-rate column (percent).
        term_col: Original term column (months).
        servicing_fee: Annual servicing fee (decimal, e.g. 0.0025 = 25 bps).
        annual_discount: Flat annual discount rate (decimal).
        psa_multiple: PSA speed multiple (1.0 = 100% PSA).

    Returns:
        ``loans`` with two added columns: ``expected_servicing_pv`` (dollars)
        and ``expected_servicing_bps`` (PV as bps of original UPB).
    """
    upb = loans[orig_upb_col].cast(pl.Float64).to_numpy()
    rate = loans[rate_pct_col].cast(pl.Float64).to_numpy()
    term = loans[term_col].cast(pl.Float64).to_numpy()

    valid = np.isfinite(upb) & np.isfinite(rate) & np.isfinite(term) & (upb > 0) & (term > 0)
    i = np.where(valid, rate / 1200.0, 0.0)
    n = np.where(valid, term, 0.0)
    max_n = int(np.nanmax(np.where(valid, term, 0))) if valid.any() else 0
    max_n = min(max_n, MAX_TERM_MONTHS)

    # Age-only PSA survival entering each month: surv_prev[t] = prod_{k<t}(1-SMM_k).
    ages = np.arange(1, max_n + 1, dtype=float)
    smm = cpr_to_smm(psa_cpr(ages, psa_multiple))
    surv_entering = np.concatenate([[1.0], np.cumprod(1.0 - smm)])  # index t-1 -> entering month t

    monthly_disc = 1.0 / (1.0 + annual_discount / 12.0)
    fee_m = servicing_fee / 12.0

    pv = np.zeros_like(upb)
    for t in range(1, max_n + 1):
        sched_prev = _scheduled_balance_factor_np(i, n, t - 1)
        beg_bal = upb * sched_prev * surv_entering[t - 1]
        cf = fee_m * beg_bal
        pv += np.where(t <= n, cf * (monthly_disc**t), 0.0)

    pv = np.where(valid, pv, np.nan)
    bps = np.where(valid & (upb > 0), pv / upb * 10000.0, np.nan)
    return loans.with_columns(
        pl.Series("expected_servicing_pv", pv),
        pl.Series("expected_servicing_bps", bps),
    )


# ---------------------------------------------------------------------------
# Realized servicing PV at origination (observed balance path)
# ---------------------------------------------------------------------------


def realized_servicing_pv(
    panel: pl.LazyFrame,
    *,
    loan_id_col: str = "loan_id",
    age_col: str = "loan_age",
    upb_col: str = "current_upb",
    zero_bal_col: str = "zero_bal_code",
    orig_upb_col: str = "orig_upb",
    rate_pct_col: str = "orig_rate",
    term_col: str = "orig_term",
    servicing_fee: float = DEFAULT_SERVICING_FEE,
    annual_discount: float = DEFAULT_ANNUAL_DISCOUNT,
) -> pl.LazyFrame:
    """Per-loan realized discounted servicing revenue from the observed path.

    For each loan-month the servicing fee accrues on the *beginning-of-month*
    balance (the prior month's reported UPB; the first month uses original UPB)
    and is discounted to origination by the loan age. Performing months whose
    UPB is missing/zero — the GSE onboarding-lag artifact, where the loan is
    alive but the servicer has not yet reported a balance — are imputed with the
    scheduled amortized balance so the strip is not understated. The terminal
    zero-balance month legitimately carries a zero balance and is left as is.

    Args:
        panel: Loan-month LazyFrame with the columns named below (origination
            terms must be broadcast onto every loan-month).
        loan_id_col: Loan identifier column.
        age_col: Loan-age (months since origination) column.
        upb_col: Reported current actual UPB column.
        zero_bal_col: Zero-balance code column (null while performing).
        orig_upb_col: Original UPB column.
        rate_pct_col: Original note-rate column (percent).
        term_col: Original term column (months).
        servicing_fee: Annual servicing fee (decimal).
        annual_discount: Flat annual discount rate (decimal).

    Returns:
        A LazyFrame with one row per loan: ``realized_servicing_pv`` (dollars),
        ``realized_servicing_bps`` (bps of original UPB), ``months_observed``,
        ``last_age``, and ``is_terminated`` (a zero-balance event was observed).
    """
    fee_m = servicing_fee / 12.0
    monthly_disc = 1.0 + annual_discount / 12.0

    lf = panel.sort([loan_id_col, age_col])
    sched_fill = (
        pl.col(orig_upb_col) * scheduled_balance_factor_expr(rate_pct_col, term_col, age_col)
    )
    # Effective balance for an alive month: reported UPB, else scheduled fill.
    eff_upb = (
        pl.when(pl.col(upb_col) > 0)
        .then(pl.col(upb_col))
        .when(pl.col(zero_bal_col).is_null())
        .then(sched_fill)
        .otherwise(pl.col(upb_col))
    )
    lf = lf.with_columns(eff_upb.alias("_eff_upb"))
    beg_bal = (
        pl.col("_eff_upb").shift(1).over(loan_id_col).fill_null(pl.col(orig_upb_col))
    )
    disc = monthly_disc ** (-pl.col(age_col).cast(pl.Float64))
    lf = lf.with_columns((fee_m * beg_bal * disc).alias("_cf_disc"))

    return (
        lf.group_by(loan_id_col)
        .agg(
            pl.col("_cf_disc").sum().alias("realized_servicing_pv"),
            pl.len().alias("months_observed"),
            pl.col(age_col).max().alias("last_age"),
            pl.col(zero_bal_col).is_not_null().any().alias("is_terminated"),
            pl.col(orig_upb_col).first().alias("_orig_upb"),
        )
        .with_columns(
            pl.when(pl.col("_orig_upb") > 0)
            .then(pl.col("realized_servicing_pv") / pl.col("_orig_upb") * 10000.0)
            .otherwise(None)
            .alias("realized_servicing_bps")
        )
        .drop("_orig_upb")
    )
