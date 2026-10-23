"""Round mechanics for the FHFA-MBS engine: claims, the acceptance gate, passes, years.

The rounds themselves are data (``rounds.py``); these are the steps the engine builds them
from. `settle` retires every pair a round found from both pools and keeps the pairs that
pass `recovery_gate`, so a rejected pair cannot resurface in a later round.
`iterate_to_fixpoint` re-runs a pass on its own leftovers. `resolve_cross_year` drops the
loans two acquisition years both claim.

A ZIP3 mismatch marks a wrong pair in the fixpoint, seasoned, MSA-free and occupancy
rounds (tract precision 0.13-0.39, against 0.91-0.97 for consistent pairs), which is why
the gate can require ``zip3_consistent``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.matching.match_fhfa_mbs.spec import FHFA_ID

logger = get_logger(__name__)

ROUND_DTYPE = pl.UInt8

Matcher = Callable[[pl.DataFrame, pl.DataFrame], pl.DataFrame]
"""One matching pass over materialized pools, returning the matched pairs."""


def tag_round(pairs: pl.DataFrame, match_round: int) -> pl.DataFrame:
    """Set ``match_round`` on every pair.

    Args:
        pairs: The matched pairs.
        match_round: Round number to stamp on the pairs.

    Returns:
        The pairs with ``match_round`` set.
    """
    return pairs.with_columns(pl.lit(match_round, dtype=ROUND_DTYPE).alias("match_round"))


def remove_claimed(
    mbs: pl.DataFrame,
    fhfa: pl.DataFrame,
    pairs: pl.DataFrame,
    mbs_id: str,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Drop both sides of every pair from the pools.

    Args:
        mbs: The MBS-side pool.
        fhfa: The FHFA-side pool.
        pairs: The matched pairs.
        mbs_id: Name of the MBS-side identifier column.

    Returns:
        The two pools with every claimed row dropped.
    """
    return (
        mbs.join(pairs.select(mbs_id).unique(), on=mbs_id, how="anti"),
        fhfa.join(pairs.select(FHFA_ID).unique(), on=FHFA_ID, how="anti"),
    )


def iterate_to_fixpoint(
    match: Matcher,
    mbs: pl.DataFrame,
    fhfa: pl.DataFrame,
    mbs_id: str,
    max_iter: int = 40,
) -> pl.DataFrame:
    """Re-run ``match`` on leftovers until a pass yields no new pair.

    Args:
        match: One matching pass over the two pools.
        mbs: Loan-level pool.
        fhfa: FHFA record pool.
        mbs_id: Loan id column on the loan-level side.
        max_iter: Upper bound on passes; convergence normally takes fewer than ten.

    Returns:
        Pairs from every pass, in pass order, ungated.
    """
    passes: list[pl.DataFrame] = []
    for _ in range(max_iter):
        pairs = match(mbs, fhfa)
        passes.append(pairs)  # an empty first pass still carries the schema
        if pairs.is_empty():
            break
        mbs, fhfa = remove_claimed(mbs, fhfa, pairs, mbs_id)
    else:
        logger.warning("Fixpoint not reached after %d passes", max_iter)
    logger.debug("Fixpoint pass sizes: %s", [len(p) for p in passes if not p.is_empty()])
    return pl.concat(passes, how="diagonal_relaxed")


def recovery_gate(require_zip3: bool, max_tier: int | None = None) -> pl.Expr:
    """Acceptance predicate for recovered pairs.

    Args:
        require_zip3: Reject pairs whose ``zip3_consistent`` is False; null passes.
        max_tier: Reject pairs whose ``match_quality_tier`` exceeds this; None for no cap.

    Returns:
        Boolean expression over a pairs frame.
    """
    gate = pl.lit(value=True)
    if require_zip3:
        gate = gate & pl.col("zip3_consistent").fill_null(value=True)
    if max_tier is not None:
        gate = gate & (pl.col("match_quality_tier") <= max_tier)
    return gate


def settle(
    mbs: pl.DataFrame,
    fhfa: pl.DataFrame,
    pairs: pl.DataFrame,
    mbs_id: str,
    gate: pl.Expr,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Retire every pair's records from both pools and keep the pairs passing the gate.

    A leftover whose best recovery candidate fails the quality gate is retired,
    not re-offered: both records leave the pools whether or not the pair is kept,
    so a rejected pair cannot resurface in a later round.

    Args:
        mbs: Loan-level pool.
        fhfa: FHFA record pool.
        pairs: One round's pairs.
        mbs_id: Loan id column on the loan-level side.
        gate: Acceptance predicate (``recovery_gate``).

    Returns:
        The accepted pairs and the two leftover pools.
    """
    accepted = pairs.filter(gate)
    mbs, fhfa = remove_claimed(mbs, fhfa, pairs, mbs_id)
    return accepted, mbs, fhfa


def without_msa(pool: pl.DataFrame) -> pl.DataFrame:
    """Neutralize the MSA key (both preparers code non-metro as 0).

    Args:
        pool: The pool to transform.

    Returns:
        The pool with the MSA key neutralized.
    """
    return pool.with_columns(pl.lit(0, dtype=pl.Int64).alias("msa"))


def resolve_cross_year(
    crosswalks: Mapping[int, pl.DataFrame],
    mbs_id: str,
) -> tuple[dict[int, pl.DataFrame], int]:
    """Drop every loan that the per-year crosswalks assign more than once.

    A loan is acquired once, so FHFA records in two acquisition years claiming the
    same loan exclude each other. The duplicates are the December/January year-end
    straddle, where nothing in the data identifies the true acquisition year
    (round, quality tier and FHFA's same/prior-year flag are each at chance, and
    ZIP3 agreement plus first-payment-year distance pick the right year only 94% of
    the time). The claims are a tie, so neither is kept: the loan and every record
    that claimed it stay unmatched.

    Args:
        crosswalks: Per-year crosswalk frames keyed by acquisition year; each must
            carry ``mbs_id``.
        mbs_id: Loan id column on the loan-level side.

    Returns:
        The resolved frames (original row order) and the number of rows removed.
    """
    contested = (
        pl.concat([df.select(mbs_id) for df in crosswalks.values()])
        .group_by(mbs_id)
        .len()
        .filter(pl.col("len") > 1)
        .get_column(mbs_id)
    )
    resolved = {
        year: df.filter(~pl.col(mbs_id).is_in(contested.implode()))
        for year, df in crosswalks.items()
    }
    removed = sum(len(df) for df in crosswalks.values()) - sum(len(df) for df in resolved.values())
    return resolved, removed
