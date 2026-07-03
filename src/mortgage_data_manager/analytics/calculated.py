"""Deterministic, closed-form analytics (no model, no forecast).

These metrics are computed directly from observed pool/loan data — given the
inputs the output is exact. Two families live here:

- **Rate primitives** — SMM/CPR/CDR conversions and balance-weighted rollups
  that work on any loan- or pool-level frame.
- **Security-level prepayment** — net SMM/CPR from month-over-month UPB
  transitions, with the scheduled-principal amortization used by the agency
  MBS disclosure analyses (trend, incentive S-curve, seasoning ramp). These
  are ported from the ``securities_exploration`` agency-MBS pool-factor study
  (FNMA ``FNM_MF`` / FHLMC ``FD`` Monthly Factor disclosure); they are
  grain-agnostic and operate on a security-by-month panel.

Contrast with :mod:`analytics.estimated`, which forecasts future quantities
and therefore requires a model.
"""

from __future__ import annotations

import polars as pl

# ---------------------------------------------------------------------------
# Rate primitives
# ---------------------------------------------------------------------------


def single_monthly_mortality(
    factor_curr: pl.Expr | float,
    factor_prev: pl.Expr | float,
    scheduled_factor: pl.Expr | float,
) -> pl.Expr:
    """Single Monthly Mortality (SMM) from period factors/balances.

    SMM = 1 - (actual current balance / scheduled current balance), i.e. the
    share of the *scheduled* balance that prepaid this period. ``factor_prev``
    is accepted for API symmetry (the scheduled factor already encodes the
    prior balance) and is not needed in the closed form.

    Args:
        factor_curr: Current-period pool factor (or balance).
        factor_prev: Prior-period pool factor (or balance).
        scheduled_factor: Scheduled (no-prepay) current-period factor/balance.

    Returns:
        A Polars expression for SMM.
    """
    curr = factor_curr if isinstance(factor_curr, pl.Expr) else pl.lit(factor_curr)
    sched = scheduled_factor if isinstance(scheduled_factor, pl.Expr) else pl.lit(scheduled_factor)
    return 1.0 - curr / sched


def smm_to_cpr(smm: pl.Expr | float) -> pl.Expr:
    """Annualize SMM into a Conditional Prepayment Rate: ``CPR = 1 - (1 - SMM)**12``.

    Args:
        smm: Single Monthly Mortality (0-1).

    Returns:
        A Polars expression for CPR.
    """
    s = smm if isinstance(smm, pl.Expr) else pl.lit(smm)
    return 1.0 - (1.0 - s) ** 12


def conditional_default_rate(
    default_balance: pl.Expr | float,
    performing_balance: pl.Expr | float,
) -> pl.Expr:
    """Annualized Conditional Default Rate (CDR) from realized defaults.

    Monthly default rate MDR = default_balance / performing_balance, annualized
    as ``CDR = 1 - (1 - MDR)**12``.

    Args:
        default_balance: Balance that defaulted this period.
        performing_balance: Performing balance at period start.

    Returns:
        A Polars expression for the annualized CDR.
    """
    dflt = default_balance if isinstance(default_balance, pl.Expr) else pl.lit(default_balance)
    perf = (
        performing_balance
        if isinstance(performing_balance, pl.Expr)
        else pl.lit(performing_balance)
    )
    return 1.0 - (1.0 - dflt / perf) ** 12


def remaining_principal_balance(
    lf: pl.LazyFrame, *, by: list[str], balance_col: str = "current_upb"
) -> pl.LazyFrame:
    """Aggregate Remaining Principal Balance (RPB) by the given keys.

    Args:
        lf: Loan/pool-level LazyFrame with a balance column.
        by: Grouping keys (e.g. coupon, vintage, agency, month).
        balance_col: Name of the outstanding-balance column.

    Returns:
        A LazyFrame of ``rpb`` and row count ``n`` by group, sorted by ``by``.
    """
    return lf.group_by(by).agg(pl.col(balance_col).sum().alias("rpb"), pl.len().alias("n")).sort(by)


def weighted_averages(
    lf: pl.LazyFrame,
    *,
    by: list[str],
    value_cols: list[str],
    weight_col: str = "current_upb",
) -> pl.LazyFrame:
    """Balance-weighted pool characteristics (WAC, WAM/WARM, WALA, ...).

    Args:
        lf: Loan-level LazyFrame with a weight column + characteristic columns.
        by: Grouping keys (e.g. pool, cohort, month).
        value_cols: Characteristic columns to balance-weight.
        weight_col: Weight (balance) column.

    Returns:
        A LazyFrame of balance-weighted averages ``wa_{col}`` plus
        ``total_balance`` by group.
    """
    aggs = [
        ((pl.col(c) * pl.col(weight_col)).sum() / pl.col(weight_col).sum()).alias(f"wa_{c}")
        for c in value_cols
    ]
    return lf.group_by(by).agg(*aggs, pl.col(weight_col).sum().alias("total_balance")).sort(by)


def delinquency_transition_rates(
    lf: pl.LazyFrame,
    *,
    by: list[str] | None = None,
    id_col: str = "loan_id",
    period_col: str = "period",
    status_col: str = "dlq_status",
) -> pl.LazyFrame:
    """Realized delinquency-state transition rates between adjacent months.

    Counts observed ``from_status -> to_status`` transitions across consecutive
    monthly snapshots per loan — empirical, not modeled.

    Args:
        lf: Loan-level performance panel (loan x month with a status column).
        by: Additional grouping keys (besides from/to status).
        id_col: Loan identifier column.
        period_col: Monthly period column.
        status_col: Delinquency-status column.

    Returns:
        A LazyFrame with ``from_status``, ``to_status``, any ``by`` keys, the
        transition ``count``, and the within-``from_status`` ``rate``.
    """
    by = by or []
    lf = lf.sort([id_col, period_col]).with_columns(
        pl.col(status_col).shift(1).over(id_col).alias("from_status")
    )
    lf = lf.filter(pl.col("from_status").is_not_null()).rename({status_col: "to_status"})
    group_keys = [*by, "from_status", "to_status"]
    denom_keys = [*by, "from_status"]
    return (
        lf.group_by(group_keys)
        .agg(pl.len().alias("count"))
        .with_columns((pl.col("count") / pl.col("count").sum().over(denom_keys)).alias("rate"))
        .sort(group_keys)
    )


# ---------------------------------------------------------------------------
# Security-level prepayment (ported from securities_exploration)
# ---------------------------------------------------------------------------


def scheduled_principal(
    upb: pl.Expr,
    annual_rate_pct: pl.Expr,
    remaining_months: pl.Expr,
    *,
    interest_only: pl.Expr | None = None,
) -> pl.Expr:
    """Scheduled principal for a level-payment loan/pool over one month.

    Uses the standard amortizing-payment formula
    ``P = UPB * r(1+r)^n / ((1+r)^n - 1)`` with ``r`` the monthly rate and
    ``n`` the remaining months, then ``scheduled = P - UPB * r``. Interest-only
    rows contribute zero. Degenerate cases (``r <= 0`` or ``n <= 0``) fall back
    to straight-line ``UPB / n``.

    Args:
        upb: Beginning-of-month unpaid balance.
        annual_rate_pct: Annual interest rate in percent (e.g. ``4.5``).
        remaining_months: Remaining months to maturity.
        interest_only: Optional boolean expression flagging IO rows.

    Returns:
        A Polars expression for the month's scheduled principal (>= 0).
    """
    r = (annual_rate_pct / 100.0) / 12.0
    n = remaining_months
    growth = (1.0 + r) ** n
    payment = (
        pl.when((r > 0) & (n > 0) & (growth > 1.0))
        .then(upb * (r * growth) / (growth - 1.0))
        .otherwise(upb / pl.when(n > 0).then(n).otherwise(1.0))
    )
    sched = (payment - upb * r).clip(lower_bound=0.0)
    if interest_only is not None:
        sched = pl.when(interest_only).then(0.0).otherwise(sched)
    return sched


def security_smm(
    panel: pl.LazyFrame,
    *,
    id_col: str = "cusip",
    period_col: str = "period",
    upb_col: str = "current_upb",
    rate_col: str = "current_rate",
    remaining_term_col: str = "remaining_months",
    io_col: str | None = None,
) -> pl.LazyFrame:
    """Per-security-month net SMM from month-over-month UPB transitions.

    For each security, compares this month's beginning balance to next month's
    balance, nets out scheduled principal, and expresses the voluntary paydown
    as SMM over the scheduled balance. The row's ``period`` is the *beginning*
    month of the transition.

    Args:
        panel: Long security-by-month panel (one row per security per month).
        id_col: Security identifier column (e.g. CUSIP).
        period_col: Monthly period column (sortable).
        upb_col: Current investor/security UPB column.
        rate_col: Current interest rate (percent) column.
        remaining_term_col: Remaining months to maturity column.
        io_col: Optional interest-only indicator column.

    Returns:
        A LazyFrame with ``id_col``, ``period_col``, ``smm``, the scheduled
        balance ``smm_denom`` (the aggregation weight), and ``upb_col``.
    """
    io_expr = pl.col(io_col) if io_col is not None else None
    lf = panel.sort([id_col, period_col]).with_columns(
        pl.col(upb_col).shift(-1).over(id_col).alias("_upb_next")
    )
    lf = lf.filter(pl.col("_upb_next").is_not_null() & (pl.col(upb_col) > 0))
    lf = lf.with_columns(
        scheduled_principal(
            pl.col(upb_col), pl.col(rate_col), pl.col(remaining_term_col), interest_only=io_expr
        ).alias("_sched_prin")
    )
    lf = lf.with_columns(
        (pl.col(upb_col) - pl.col("_upb_next") - pl.col("_sched_prin"))
        .clip(lower_bound=0.0)
        .alias("_prepay"),
        (pl.col(upb_col) - pl.col("_sched_prin")).alias("smm_denom"),
    )
    return lf.with_columns(
        pl.when(pl.col("smm_denom") > 0)
        .then(pl.col("_prepay") / pl.col("smm_denom"))
        .otherwise(0.0)
        .alias("smm")
    ).select([id_col, period_col, "smm", "smm_denom", upb_col])


def weighted_net_cpr(
    smm_panel: pl.LazyFrame,
    *,
    by: list[str],
    smm_col: str = "smm",
    weight_col: str = "smm_denom",
) -> pl.LazyFrame:
    """Balance-weighted net CPR by group from a per-security-month SMM panel.

    Aggregates SMM with a balance weight, then annualizes
    ``net_cpr = 1 - (1 - weighted_smm)**12``. With ``by=[period]`` this is the
    market trend; with an incentive-bin key it is the prepayment S-curve; with
    a loan-age key it is the seasoning ramp.

    Args:
        smm_panel: Output of :func:`security_smm` (joined to any grouping keys).
        by: Grouping keys.
        smm_col: SMM column.
        weight_col: Scheduled-balance weight column.

    Returns:
        A LazyFrame of ``weighted_smm``, ``total_balance``, and ``net_cpr`` by
        group.
    """
    return (
        smm_panel.group_by(by)
        .agg(
            ((pl.col(smm_col) * pl.col(weight_col)).sum() / pl.col(weight_col).sum()).alias(
                "weighted_smm"
            ),
            pl.col(weight_col).sum().alias("total_balance"),
        )
        .with_columns((1.0 - (1.0 - pl.col("weighted_smm")) ** 12).alias("net_cpr"))
        .sort(by)
    )
