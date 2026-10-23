"""The FHLB-GNMA matching rounds.

The rounds run in the order listed, and each sees only the GNMA and FHLB loans earlier
rounds left unmatched. A round's ``match_round`` is its position in the tuple (1-based). To
add a round, copy an entry, change its fields and re-run; nothing else needs editing.

Every round joins on its exact keys, keeps candidates inside its candidate tolerances and a
compatible loan purpose (GNMA purchase never pairs with an FHLB refinance, nor the reverse),
takes the mutual best on a tolerance-normalized score, drops every tie, and then applies its
post-match tolerances. What a round chooses is in `MatchRound`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tolerances:
    """Largest gap a pair may show on a field the join does not key on.

    LTV and DTI pass when either side is missing. In the post-match stage the rate gap must
    be strictly below its tolerance; every other gap may equal it.

    Attributes:
        rate: Note-rate gap in percentage points.
        balance: Original balance gap in dollars.
        ltv: LTV gap in percentage points.
        dti: DTI gap in percentage points.
        term: Term gap in months; None does not compare term.
    """

    rate: float
    balance: float
    ltv: float
    dti: float
    term: int | None = None


@dataclass(frozen=True)
class MatchRound:
    """One matching round.

    Attributes:
        description: Label for the logs and ``info``.
        keys: ``(GNMA column, FHLB column)`` pairs joined exactly.
        candidate: Tolerances that admit a candidate pair; they also scale the score.
        post: Stricter tolerances applied to the resolved pairs; None applies none.
    """

    description: str
    keys: tuple[tuple[str, str], ...]
    candidate: Tolerances
    post: Tolerances | None

    def __post_init__(self) -> None:
        """Reject post-match tolerances looser than the candidate ones."""
        if self.post is None:
            return
        for field in ("rate", "balance", "ltv", "dti"):
            if getattr(self.post, field) > getattr(self.candidate, field):
                raise ValueError(f"post-match {field} tolerance exceeds the candidate one")


#: State, agency (FHA, VA, RD), property units, borrower count and origination year.
KEYS: tuple[tuple[str, str], ...] = (
    ("state_fips", "state_fips"),
    ("agency_code", "mortgage_type"),
    ("property_units", "property_units"),
    ("num_borrowers", "num_borrowers"),
    ("origination_year", "note_year"),
)

ROUNDS: tuple[MatchRound, ...] = (
    MatchRound(
        description="exact keys, rate/balance/LTV/DTI/term under tolerance",
        keys=KEYS,
        candidate=Tolerances(rate=0.25, balance=2_500, ltv=1.1, dti=1.1, term=12),
        post=Tolerances(rate=0.125, balance=1_000, ltv=1.0, dti=1.0),
    ),
)
