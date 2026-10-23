"""The HMDA-FHA matching rounds, both eras.

Each era runs its rounds in the order listed, and each round sees only the HMDA loans and
FHA records earlier rounds left unmatched. A round's ``match_round`` is its position in the
tuple (1-based). To add a round, copy an entry, change its fields and re-run; nothing else
needs editing. A round reads what it learns (the post-2018 lender roster, the pre-2018 rate
offset) only from rounds before it, so adding a round never changes an earlier one.
``mortgage-data match hmda-fha info`` prints both tuples.

Post-2018 (`ROUNDS_POST2018`), run by ``matching_post2018.py``: candidates by geography
tier inside the endorsement window, then the purpose gate, the rate gate, the optional
sponsor and lender gates, and resolution.

2010-2017 (`ROUNDS_PRE2018`), run by ``matching_pre2018.py``. Pre-2018 HMDA has no interest
rate, so every round joins on ZIP (HMDA's tract fanned to its ZIPs), loan amount at $1k
and purpose inside its endorsement window, then applies the optional lender-bridge filter
and resolves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# =============================================================================
# Post-2018
# =============================================================================

#: A geography tier: which HMDA loans it covers and the keys it joins on (all of them on
#: state, $10k amount and 12.5bp rate).
#:
#: - ``zip``: loans with a ZIP; adds the ZIP.
#: - ``county_no_zip``: loans with no ZIP; FHA county must equal HMDA county (or either is
#:   null).
#: - ``county_fallback``: loans with no ``zip`` candidate in the round; adds the county.
#:   It must follow ``zip``.
GeoTier = Literal["zip", "county_no_zip", "county_fallback"]


@dataclass(frozen=True)
class Round:
    """One post-2018 matching round. Every round states every field.

    Attributes:
        description: Log label.
        geo: Geography tiers whose candidates the round pools (see `GeoTier`).
        county_check: On the ``zip`` tier, FHA county must equal HMDA county (or either
            is null).
        window: Inclusive range for FHA endorsement month minus January of the HMDA
            activity year: ``(-1, 13)`` is December of the year before through February of
            the year after.
        rate_tolerance: Largest |HMDA rate − FHA rate|, in percentage points.
        sponsor_filter: Require sponsor/submission consistency: an HMDA loan submitted
            through a third party has an FHA sponsor and a direct one has none.
        lender_filter: Keep only candidates whose (FHA mortgagee, HMDA LEI) pair passes the
            lender gate, which ranks lender pairs by how many candidates they share.
        min_score: ``None`` keeps the candidates unique on both sides. A number scores every
            candidate, drops those below it, and keeps the strictly unique best on both
            sides by score, then by endorsement timing; ties go unmatched. The score is
            lender identity (+2 aligned; −2 unaligned when HMDA's purchaser is Ginnie Mae
            or none), sponsor/submission consistency (+1), rate within 0.5bp (+1) and the
            ``zip`` tier (+1).
        learns_roster: Add the round's (HMDA LEI, FHA mortgagee or sponsor) pairs to the
            roster that later scored rounds count as lender-identity alignment.
    """

    description: str
    geo: tuple[GeoTier, ...]
    county_check: bool
    window: tuple[int, int]
    rate_tolerance: float
    sponsor_filter: bool
    lender_filter: bool
    min_score: int | None
    learns_roster: bool


ROUNDS_POST2018: tuple[Round, ...] = (
    Round(
        description="strict, lender pairs",
        geo=("zip", "county_no_zip"),
        county_check=True,
        window=(-1, 13),
        rate_tolerance=0.005,
        sponsor_filter=True,
        lender_filter=True,
        min_score=None,
        learns_roster=True,
    ),
    # Round 1's leftovers over a two-year window, resolved on lender identity rather than
    # dropped when contested.
    Round(
        description="scored identity recovery",
        geo=("zip", "county_fallback"),
        county_check=False,
        window=(-12, 23),
        rate_tolerance=0.025,
        sponsor_filter=False,
        lender_filter=False,
        min_score=2,
        learns_roster=False,
    ),
)


# =============================================================================
# 2010-2017
# =============================================================================

#: A margin tier: an evidence score whose winner must lead every rival, on both sides, by
#: the tier's margin. The evidence is built in ``matching_pre2018.py``.
#:
#: - ``county``: the one candidate in the HMDA loan's own county (margin 1).
#: - ``geo_rate``: county agreement, a dominant ZIP and rate agreement summed (margin 2).
#: - ``rate``: the FHA note rate against HMDA ``rate_spread`` net of the month's offset;
#:   the winner within 0.125pp and every rival 0.375pp further off.
MarginTier = Literal["county", "geo_rate", "rate"]


@dataclass(frozen=True)
class Pre2018Round:
    """One pre-2018 matching round. Every round states every field.

    Attributes:
        description: Log label.
        window: Inclusive range for FHA endorsement month minus January of the HMDA
            activity year: ``(1, 12)`` is February of the activity year through January
            of the next.
        bridge_filter: Before resolving, drop candidates the FHA-lender/HMDA-respondent
            bridge contradicts, on each side wherever that side has a bridge-consistent
            candidate. Remove-only: a loan or record the bridge knows nothing about keeps
            every candidate.
        tiers: ``()`` keeps the candidates unique on both sides. Otherwise each listed
            `MarginTier` is resolved on its own over the round's candidates, and a loan
            or record two tiers give different partners goes unmatched. Needs an earlier
            round with ``learns_rate_offset``.
        learns_rate_offset: Later rounds' rate evidence nets out the median FHA rate minus
            HMDA ``rate_spread`` per endorsement month, learned from this round's pairs.
    """

    description: str
    window: tuple[int, int]
    bridge_filter: bool
    tiers: tuple[MarginTier, ...]
    learns_rate_offset: bool


ROUNDS_PRE2018: tuple[Pre2018Round, ...] = (
    # Endorsement lags origination by about a month, so the pool is the activity year slid
    # forward a month: (1, 12) beats (0, 13) by 0.6-1.0pp in every year swept (2012, 2015,
    # 2016), and every wider window costs pairs to the 1:1 rule. These pairs are matched
    # with no rate evidence, which is why the rate offset is learned here.
    Pre2018Round(
        description="base key, 1:1",
        window=(1, 12),
        bridge_filter=False,
        tiers=(),
        learns_rate_offset=True,
    ),
    # The same pool with the lender bridge clearing contested cells: 32.8% -> 74.0% of HMDA
    # FHA originations together with round 1.
    Pre2018Round(
        description="base key, bridge-cleared",
        window=(1, 12),
        bridge_filter=True,
        tiers=(),
        learns_rate_offset=False,
    ),
    # The leftovers over the window reopened at January, where a loan the first rounds
    # could not place often has its partner; resolved on evidence rounds 1-2 never read.
    # Resolving by best score alone instead of by margin is measurably too loose, and so is
    # running the tiers as rounds of their own (geography-and-rate winners then cross a
    # county line four times in ten).
    Pre2018Round(
        description="margin recovery",
        window=(0, 12),
        bridge_filter=False,
        tiers=("county", "geo_rate", "rate"),
        learns_rate_offset=False,
    ),
)
