"""The HMDA-GNMA matching rounds, one tuple per loan type (FHA, VA/USDA).

The rounds run in the order listed, and each sees only the HMDA rows and GNMA loans earlier
rounds left unmatched. A round's ``match_round`` is its position in the tuple (1-based). To
add a round, copy an entry, change its fields and re-run; nothing else needs editing.

Every round block-joins on its keys, scores the candidates and keeps the strictly unique
best score on both sides. FHA rounds (`FhaRound`) score in ``matching_fha.py``, and the whole
FHA tuple runs twice: the first pass, without the LEI-issuer dyad term, supplies the pairs
the dyad map is learned from. A new FHA round therefore also feeds the dyad map and can move
pairs in earlier rounds: a copy of round 2 appended adds 11,786 pairs, and in rounds 1-2
4,350 pairs drop out and 1,318 enter, every change through dyad credit. VA/USDA rounds
(`GnmaRound`) first drop key cells over ``CELL_CAP`` candidate pairs and score in
``matching_va_usda.py``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FhaRound:
    """One FHA round: its block keys, the score it needs, and the month window.

    Attributes:
        keys: Block keys, named the same on both sides (``run_direct_match`` in
            ``matching_fha.py`` builds them).
        min_accept: Score a candidate needs inside the round.
        require_term: Drop HMDA rows with no loan term.
        month_window: Inclusive range for FHA endorsement month minus GNMA origination
            month (a cross-year month index; it peaks at +1, the endorsement lag).
    """

    keys: tuple[str, ...]
    min_accept: int
    require_term: bool = False
    month_window: tuple[int, int] = (-1, 3)


ROUNDS_FHA: tuple[FhaRound, ...] = (
    # State, exact amount ($1k floor), exact 3-decimal rate (HMDA rate, FHA fallback),
    # purpose category and term.
    FhaRound(
        keys=("state_code", "amount_1k", "r3", "purpose_cat", "loan_term"),
        min_accept=3,
        require_term=True,
    ),
    # As round 1 with the rate in a 12.5bp bucket and no term.
    FhaRound(keys=("state_code", "amount_1k", "r_bucket", "purpose_cat"), min_accept=9),
)


@dataclass(frozen=True)
class GnmaRound:
    """One VA/USDA round: its block keys, the score it needs, and the cells it searches.

    Attributes:
        hmda_keys: Block keys on the HMDA side.
        gnma_keys: The same keys on the GNMA side, in the same order.
        min_accept: Score a candidate needs inside the round; ``None`` takes the run's
            capped-cell floor (``VA_USDA_CAPPED_MIN_SCORE``).
        require_term: Drop HMDA rows with no loan term.
        reopens: Search only the cells this earlier round (its 1-based position), keyed
            the same way, dropped at the cap.
        sub_keys: Extra block keys, by name in ``SUB_KEYS`` (``matching_va_usda.py``):
            ``purpose_block``, ``k_units``, ``k_borr``.
        by_year: Join one origination year at a time (a block key, so no cell spans
            years), which bounds the candidate frame.
    """

    hmda_keys: tuple[str, ...]
    gnma_keys: tuple[str, ...]
    min_accept: int | None
    require_term: bool = False
    reopens: int | None = None
    sub_keys: tuple[str, ...] = ()
    by_year: bool = False


#: Agency, state, $10k amount bin, exact 3-decimal rate, term and origination year. Also
#: the keys the strict seed matches behind the LEI-issuer dyads are blocked on.
STRICT_HMDA_KEYS = ("agency", "state_code", "loan_amount", "r3", "loan_term", "activity_year")
STRICT_GNMA_KEYS = ("agency", "gnma_state", "amt_bin", "r3", "gnma_term", "gnma_orig_year")

#: As above with the rate in a 12.5bp bucket and no term.
BUCKET_HMDA_KEYS = ("agency", "state_code", "loan_amount", "r_bucket", "activity_year")
BUCKET_GNMA_KEYS = ("agency", "gnma_state", "amt_bin", "r_bucket", "gnma_orig_year")

ROUNDS_VA_USDA: tuple[GnmaRound, ...] = (
    GnmaRound(STRICT_HMDA_KEYS, STRICT_GNMA_KEYS, min_accept=3, require_term=True),
    GnmaRound(BUCKET_HMDA_KEYS, BUCKET_GNMA_KEYS, min_accept=9),
    # The IRRRL-wave cells round 1 dropped at the cap, reopened with a finer key: purpose
    # x units x borrowers, the only published fields both sides carry that discriminate
    # (GNMA's loan-level ``msa`` and ``seller_issuer_id`` are blank in every vintage, and
    # HMDA has no counterpart to its FICO or first-time-buyer flags). Together they agree
    # on 98.55% of 1x1-cell seed pairs against 33.32% on an amount-shift placebo, and
    # bring half the capped HMDA rows back under the cap. The three keys are also score
    # components, so ``s_units = 3`` and ``s_borr = 2`` are granted to every candidate --
    # which is why it takes the higher capped-cell floor: 5 of those points are the key.
    GnmaRound(
        STRICT_HMDA_KEYS,
        STRICT_GNMA_KEYS,
        min_accept=None,
        require_term=True,
        reopens=1,
        sub_keys=("purpose_block", "k_units", "k_borr"),
        by_year=True,
    ),
)
