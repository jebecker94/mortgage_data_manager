"""The MBS-UMBS matching rounds, shared by both GSEs.

The rounds run in the order listed, and each sees only the loans earlier rounds left
unpaired. A round's ``match_round`` is its position in `ROUNDS` (1-based). To add a round,
copy an entry, change its fields and re-run; nothing else needs editing.

Every round joins on the exact keys (``JOIN_KEYS`` in ``matching.py``) and applies the shared
tolerances (LTV, CLTV, DTI within 1; term within 6 months; borrowers, units, channel,
occupancy, first-time buyer and property type exact). What a round chooses is in `UmbsRound`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UmbsRound:
    """One round's filter set beyond the exact join keys and the shared tolerances.

    Attributes:
        label: How the round is logged.
        mip: Require an exact mortgage insurance percentage match.
        credit_score: Require the spec's ``credit_score_condition``.
        exact_attribute: Resolve contested candidates on core-attribute exactness
            instead of abandoning them; skipped when ``exact_attribute_round`` is False.
    """

    label: str
    mip: bool = True
    credit_score: bool = True
    exact_attribute: bool = False


ROUNDS: tuple[UmbsRound, ...] = (
    UmbsRound("MIP + CS"),
    UmbsRound("no MIP, with CS", mip=False),
    # The VantageScore-4 / Classic-FICO score-type transition: for FPD-2025+ vintages MBS
    # and UMBS disclose different score types for the same loan. MIP stays strict to limit
    # false positives; with MIP nulls harmonized to 0.0 it covers uninsured loans too.
    UmbsRound("no CS, with MIP", credit_score=False),
    # Rounds 1-3 abandon a loan whose candidates all pass the filter set but are non-unique
    # on the keys (`drop_ambiguous`). Most of those groups are not ties: base-pass true
    # pairs agree *exactly* on LTV, CLTV, DTI and Term (FNMA 100.0 / 100.0 / 99.96 /
    # 100.0%, FHLMC 100.0 / 99.99 / 100.0 / 100.0%), so the +/-1 and +/-6 tolerance bands
    # are essentially never used by a real pair and a candidate that disagrees on one of
    # the four is a twin with probability 0.0004 (FNMA) / 0.0001 (FHLMC). Keeping only a
    # winner that is exact and the strictly unique best on both sides therefore resolves
    # a margin, not a tie: a group with two exact candidates, or whose best candidate
    # still mismatches, contributes nothing. Validated against a vintage-matched control
    # on held-out fields -- FNMA seller/servicer/maturity at 94-100%, FHLMC
    # seller/maturity at 93-101% -- with an amount-displaced placebo at 0.09% of yield
    # (FNMA) and no pairs at all (FHLMC).
    UmbsRound("exact on LTV/CLTV/DTI/Term", exact_attribute=True),
)
