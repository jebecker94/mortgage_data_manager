"""The direct HMDA-GSE matching rounds.

Every round joins one activity year's HMDA loans to GSE targets on the characteristic keys
(ZIP3, $10k amount bin, 3-decimal rate, term, coarse purpose, occupancy), optionally with
the GSE seller name reached through the lender bridge, filters on the origination window
and keeps the pairs that are the strictly unique best score on both sides at or above the
round's floor. Ties go unmatched.

The rounds run in the order listed, and each round sees only the HMDA loans and targets
earlier rounds claimed in no activity year, so an earlier round wins every loan or target
two rounds would contest. A round's ``match_round`` is its position in the tuple
(1-based). To add a round, copy an entry, change its fields and re-run; nothing else
needs editing. ``mortgage-data match hmda-gse info`` prints the tuple.

The lender bridge is an input to the rounds, built before round 1: names HMDA's own
filings give each LEI, plus the names lenders deliver under, learned from the pairs of
`BRIDGE_PASS` (a nameless round over the full pool whose pairs teach the bridge and are
otherwise discarded). ``run --saved-bridge`` reads a saved bridge instead of building one.

Do not add a round that coarsens the geography to state over the leftovers. Measured on
2021 it adds 130,752 pairs at 0.5589 chain concordance and 0.7496 held-out borrower
agreement, against 0.9864 / 0.9948 for the ZIP3 round: on the leftovers the true partner
has usually been claimed already, so it is no longer there to make a wrong candidate tie,
and state-sized cells give that room to bite.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Round:
    """One direct matching round. Every round states every field.

    Attributes:
        description: Label for the logs and ``info``.
        name_pinned: Add the GSE seller name to the keys, reached from the HMDA loan's LEI
            through the lender bridge. The pair records the name in ``seller_norm``; an
            unpinned pair has none, and the master's admission rule asks more of it
            (``matching_direct.admitted_row``).
        score_floor: Lowest characteristic score accepted, of 7 (CLTV up to 4, DTI 2,
            units 1).
        cell_cap: ``None`` decides every cell. A number decides only cells whose (HMDA
            loans) x (target loans) is at most the cap, counted on the full pool rather
            than on the leftovers. Counted on the leftovers, every earlier claim shrinks
            cells under the cap, so a copied round keeps adding pairs (+131,629 measured
            for a copy of the nameless round, against +3,315 counted on the full pool).
    """

    description: str
    name_pinned: bool
    score_floor: int
    cell_cap: int | None


ROUNDS: tuple[Round, ...] = (
    # Name evidence is in the key, so a weak characteristic score is enough.
    Round(description="name-pinned", name_pinned=True, score_floor=3, cell_cap=None),
    # Characteristics alone decide only small cells, at a higher floor: at a floor of 5,
    # pairs on loans HMDA reports as sold to a GSE agreed with the chain only 66% of the
    # time (2022, 782 pairs).
    Round(description="nameless", name_pinned=False, score_floor=6, cell_cap=25),
)

#: The pass whose pairs teach the bridge, run on the full pool before round 1. On loans
#: HMDA reports as sold to Fannie Mae or Freddie Mac the originator is the delivering
#: seller, so a nameless pair there shows the name the lender delivers under.
BRIDGE_PASS = Round(description="bridge learning", name_pinned=False, score_floor=6, cell_cap=25)
