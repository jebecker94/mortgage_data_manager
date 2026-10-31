"""Level-payment amortization and prepayment-speed primitives shared across layers.

Units: SMM is a fraction (0-1); CPR is a percent (``6.0`` is 6 CPR), the way issuers
and the market quote it; annual note rates are percents (``4.5``).
"""

from __future__ import annotations

import polars as pl


def scheduled_principal(
    upb: pl.Expr,
    annual_rate_pct: pl.Expr,
    remaining_months: pl.Expr,
    *,
    interest_only: pl.Expr | None = None,
) -> pl.Expr:
    """Scheduled principal for a level-payment loan or pool over one month.

    The principal part of the level payment, ``UPB * r / ((1+r)^n - 1)``, with ``r``
    the monthly rate and ``n`` the remaining months. A zero or negative rate pays
    straight-line ``UPB / n``, the formula's limit as ``r`` goes to 0, and a term at or
    past maturity schedules the whole balance. Null when any input is null, since a
    guessed schedule would hand the difference to prepayment.

    Args:
        upb: Beginning-of-month unpaid balance.
        annual_rate_pct: Annual interest rate in percent (e.g. ``4.5``).
        remaining_months: Remaining months to maturity.
        interest_only: Optional boolean expression flagging IO rows, which schedule 0.

    Returns:
        A Polars expression for the month's scheduled principal (>= 0).
    """
    r = annual_rate_pct / 1200.0
    n = remaining_months
    growth = (1.0 + r) ** n
    sched = (
        pl.when(upb.is_null() | r.is_null() | n.is_null())
        .then(pl.lit(None, dtype=pl.Float64))
        .when((r > 0) & (n > 0) & (growth > 1.0))
        .then(upb * r / (growth - 1.0))
        .when(n > 0)
        .then(upb / n)
        .otherwise(upb)
        .clip(lower_bound=0.0)
    )
    if interest_only is not None:
        sched = pl.when(interest_only).then(0.0).otherwise(sched)
    return sched


def smm_to_cpr(smm: pl.Expr | float) -> pl.Expr:
    """Annualize SMM into a Conditional Prepayment Rate: ``100 * (1 - (1 - SMM)**12)``.

    Args:
        smm: Single Monthly Mortality (0-1).

    Returns:
        A Polars expression for CPR, in percent.
    """
    s = smm if isinstance(smm, pl.Expr) else pl.lit(smm)
    return 100.0 * (1.0 - (1.0 - s) ** 12)
