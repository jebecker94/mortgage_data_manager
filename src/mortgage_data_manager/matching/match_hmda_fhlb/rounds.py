"""The HMDA-FHLB matching rounds.

Each era runs its rounds in the order listed, and each round sees only the loans earlier
rounds left unclaimed. A round's ``match_round`` is its position in the tuple (1-based).
To add a round, copy an entry, change its fields and re-run; nothing else needs editing.

The screen (which fields are compared, and how) is fixed per era, because the pre-2018 AMA
file has its own schema. What a round chooses is in `MatchRound`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tolerances:
    """Largest gap a candidate pair may show on a field the join does not key on.

    None means the round does not compare that field. The pre-2018 screen compares income
    only.

    Attributes:
        income: Income slack in dollars.
        ltv: Base CLTV slack in points (a fee-adjusted pass is always allowed too).
        dti: DTI slack in points, applied inside HMDA's exact 36-49 band only.
        rate: Note-rate slack in percentage points.
        term: Term slack in months.
        amount: Loan-amount slack in dollars, for a round that does not key on it.
    """

    income: float
    ltv: float | None = None
    dti: float | None = None
    rate: float | None = None
    term: float | None = None
    amount: float | None = None


@dataclass(frozen=True)
class MatchRound:
    """One matching round.

    Attributes:
        description: Label for the logs and ``info``.
        keys: ``(HMDA column, FHLB column)`` pairs joined exactly.
        tolerances: Slack on the fields the join does not key on.
        open_pool: Search every HMDA originator, not only FHLB members (post-2018 only).
    """

    description: str
    keys: tuple[tuple[str, str], ...]
    tolerances: Tolerances
    open_pool: bool = False


# =============================================================================
# Post-2018
# =============================================================================

#: Year, tract, $10k amount bin, loan type, units and occupancy.
KEYS_POST2018: tuple[tuple[str, str], ...] = (
    ("activity_year", "NoteDate"),
    ("census_tract", "Census Tract String"),
    ("loan_amount", "Round Loan Amount"),
    ("loan_type", "Loan Type"),
    ("total_units", "PropertyUnitCount"),
    ("occupancy_type", "PropertyUsageType"),
)
KEYS_POST2018_NO_AMOUNT = tuple(k for k in KEYS_POST2018 if k[0] != "loan_amount")

STRICT = Tolerances(income=2_500, ltv=1.05, dti=1, rate=0.0625, term=12)

ROUNDS_POST2018: tuple[MatchRound, ...] = (
    MatchRound(
        description="FHLB members, amount keyed",
        keys=KEYS_POST2018,
        tolerances=STRICT,
    ),
    # The amount moves out of the key and is compared under a tolerance; wider income, LTV
    # and DTI tolerances.
    MatchRound(
        description="FHLB members, amount under tolerance",
        keys=KEYS_POST2018_NO_AMOUNT,
        tolerances=Tolerances(income=5_000, ltv=2.05, dti=2, rate=0.0625, term=12, amount=15_000),
    ),
    # Round 1's criteria against every HMDA originator, which finds correspondent
    # originators that sell through a member.
    MatchRound(
        description="open: every HMDA originator",
        keys=KEYS_POST2018,
        tolerances=STRICT,
        open_pool=True,
    ),
)

# =============================================================================
# Pre-2018
# =============================================================================

ROUNDS_PRE2018: tuple[MatchRound, ...] = (
    MatchRound(
        description="year, tract and amount keyed",
        keys=(
            ("activity_year", "data_year"),
            ("census_tract", "census_tract"),
            ("loan_amount", "Round Loan Amount"),
        ),
        tolerances=Tolerances(income=1_000),
    ),
)
