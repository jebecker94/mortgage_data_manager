"""The HMDA-FHFA matching rounds, both eras.

Each era runs its rounds in the order listed, and each round sees only the loans earlier
rounds left unclaimed (a pre-2018 round with ``claimed="admissions"`` instead resolves over
every candidate and refuses admissions that touch a claim). A round's ``match_round`` is
its position in the tuple (1-based). To add a round, copy an entry, change its fields and
re-run; nothing else needs editing. ``mortgage-data match hmda-fhfa info`` prints both.

Post-2018 (`ROUNDS_POST2018`), run by ``matching_post2018.py``. Every round joins on
census tract, loan amount, loan type and occupancy, plus what its fields add:

1. GSE-sold HMDA loans (purchaser type 1, 3), purchaser and rate keyed, same year.
2. Every purchaser type, rate keyed, same year, no demographic check.
3. Every purchaser type, rate keyed, FHFA year >= HMDA year; tighter rate/term after 1:1.
4. Every purchaser type, no rate key, same year, moderate rate/term tolerances.
5. Round 3 through the Census 2010<->2020 tract relationship, only on year pairs that
   cross the 2021/2022 redraw.

2007-2017 (`ROUNDS_PRE2018`), run by ``matching_pre2018.py``: same-year exact income, a
one-year lag with AMFI-deflated income, and a tie-break over the first round's contested
cells.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# =============================================================================
# Post-2018
# =============================================================================

#: HMDA purchaser-type pools: not sold (0), GSE (1 Fannie, 3 Freddie), secondary (5+).
Pool = Literal["pt0", "gse", "secondary"]
ALL_PURCHASERS: tuple[Pool, ...] = ("pt0", "gse", "secondary")


@dataclass(frozen=True)
class Round:
    """One post-2018 matching round. Every round states every field.

    Attributes:
        description: Log label.
        pool: HMDA purchaser types searched (see `Pool`).
        purchaser_key: Join on purchaser type (HMDA 1/3 against FHFA's enterprise).
        cross_year: Pair HMDA[Y] with FHFA[Y..last] and resolve 1:1 over the pooled pairs;
            otherwise same year only, resolved per year.
        rate_key: Join on the 12.5 bp rate bucket.
        vintage_bridge: Re-key FHFA's 2020-vintage tract to its 2010-vintage candidates;
            runs only on year pairs that cross the 2021/2022 redraw.
        rate_tol: Largest |rate difference| after the join, in percentage points.
        term_tol: Largest |term difference| after the join, in months (FHFA's Freddie
            term conventions applied).
        current_year_orig: Require FHFA ``date_of_mortgage_note == 1`` (originated in the
            acquisition year).
        purpose: ``strict`` (purchase, refinance and cash-out must agree), ``coarse`` (a
            purchase never pairs with a refinance) or ``off``.
        prior_year_for_gse: A cross-year pair with a GSE purchaser needs a prior-year
            origination.
        cross_year_for_pt0: A purchaser-type-0 pair needs cross-year plus a prior-year
            origination.
        demographics: Age bucket, age 62+ and sex must not contradict.
        dti_tol: DTI slack inside the exact 36-49 band (the DTI gate runs in every round).
        dti_skip_when_unique: Keep a DTI-failing pair that is unique on both sides and not
            contradicted by discount points.
        post_unique: ``(rate, term)`` tolerances applied after the 1:1 step, or None.
    """

    description: str
    pool: tuple[Pool, ...]
    purchaser_key: bool
    cross_year: bool
    rate_key: bool
    vintage_bridge: bool
    rate_tol: float
    term_tol: int
    current_year_orig: bool
    purpose: Literal["strict", "coarse", "off"]
    prior_year_for_gse: bool
    cross_year_for_pt0: bool
    demographics: bool
    dti_tol: int
    dti_skip_when_unique: bool
    post_unique: tuple[float, int] | None


ROUNDS_POST2018: tuple[Round, ...] = (
    Round(
        description="Census tract + purchaser + demographics + DTI + loan purpose (same year)",
        pool=("gse",),
        purchaser_key=True,
        cross_year=False,
        rate_key=True,
        vintage_bridge=False,
        rate_tol=0.05,
        term_tol=30,
        current_year_orig=True,
        purpose="strict",
        prior_year_for_gse=False,
        cross_year_for_pt0=False,
        demographics=True,
        dti_tol=2,
        dti_skip_when_unique=True,
        post_unique=None,
    ),
    Round(
        description="Census tract + DTI + loan purpose, all PT (same year, no demographics)",
        pool=ALL_PURCHASERS,
        purchaser_key=False,
        cross_year=False,
        rate_key=True,
        vintage_bridge=False,
        rate_tol=0.05,
        term_tol=30,
        current_year_orig=True,
        purpose="strict",
        prior_year_for_gse=False,
        cross_year_for_pt0=False,
        demographics=False,
        dti_tol=2,
        dti_skip_when_unique=True,
        post_unique=None,
    ),
    Round(
        description="Cross-year + DTI, all PT + moderate quality filter",
        pool=ALL_PURCHASERS,
        purchaser_key=False,
        cross_year=True,
        rate_key=True,
        vintage_bridge=False,
        rate_tol=0.05,
        term_tol=30,
        current_year_orig=False,
        purpose="coarse",
        prior_year_for_gse=True,
        cross_year_for_pt0=True,
        demographics=True,
        dti_tol=2,
        dti_skip_when_unique=True,
        post_unique=(0.01, 6),
    ),
    Round(
        description="No rate match + moderate filters (same year)",
        pool=ALL_PURCHASERS,
        purchaser_key=False,
        cross_year=False,
        rate_key=False,
        vintage_bridge=False,
        rate_tol=0.20,
        term_tol=12,
        current_year_orig=True,
        purpose="coarse",
        prior_year_for_gse=False,
        cross_year_for_pt0=True,
        demographics=True,
        dti_tol=2,
        dti_skip_when_unique=False,  # no rate key: DTI must stay binding
        post_unique=None,
    ),
    Round(
        description="Cross-vintage bridge at 2021/2022 boundary via Census tract relationship",
        pool=ALL_PURCHASERS,
        purchaser_key=False,
        cross_year=True,
        rate_key=True,
        vintage_bridge=True,
        rate_tol=0.05,
        term_tol=30,
        current_year_orig=False,
        purpose="coarse",
        prior_year_for_gse=True,
        cross_year_for_pt0=True,
        demographics=True,
        dti_tol=2,
        dti_skip_when_unique=True,
        # Redrawn tracts hold larger candidate sets and wider rate disagreement.
        post_unique=(0.125, 12),
    ),
)


# =============================================================================
# 2007-2017
# =============================================================================


@dataclass(frozen=True)
class Pre2018Round:
    """One pre-2018 matching round, as data for `HMDAFHFAPre2018Matcher.run_round`.

    Attributes:
        description: Log label.
        lag: HMDA[Y - lag] meets FHFA[Y].
        income: ``"exact"`` joins on income (both sides are $1k-granular dollars);
            ``"amfi"`` joins without it and requires FHFA income, deflated to the
            origination year by the HUD AMFI ratio and rounded to $1k, within ±$1k.
        barred_purchasers: HMDA purchaser types that cannot be in this round's pool.
        claimed: How earlier rounds' pairs are honored. ``"pool"`` removes their
            records from both pools before the join, so a claimed competitor no longer
            blocks a pair; ``"admissions"`` resolves over the full candidate set and
            then refuses any admission touching a claimed record.
        resolve: How candidate pairs reduce to admitted pairs, one-to-one on both keys:
            ``"unique_cells"`` keeps pairs whose FHFA record and HMDA loan each have one
            candidate; ``"tiebreak"`` re-scores the contested cells on fields the join
            never keys on and admits strict unique bests (``matching_pre2018.py``).
        vintage_bridge: Rerun the round on its own residual through the Census
            2000↔2010 tract relationship when the year pair crosses the redraw.
    """

    description: str
    lag: int
    income: Literal["exact", "amfi"]
    barred_purchasers: tuple[int, ...]
    claimed: Literal["pool", "admissions"]
    resolve: Literal["unique_cells", "tiebreak"]
    vintage_bridge: bool = False


ROUNDS_PRE2018: tuple[Pre2018Round, ...] = (
    # Same year, exact income: HMDA[Y] x FHFA[Y] on tract, loan type and exact income;
    # unique cells only. pt=0 ("not sold in calendar year") is barred because it
    # contradicts a same-year GSE acquisition; the cleaned HMDA frame already drops Ginnie
    # (2) and Farmer Mac (4). Admitting pt=0 adds about 46k pairs a year whose demographic
    # agreement runs 1-2 points below the rest of the round.
    Pre2018Round(
        description="same-year, exact income",
        lag=0,
        income="exact",
        barred_purchasers=(0,),
        claimed="pool",
        resolve="unique_cells",
    ),
    # One-year lag, AMFI-deflated income: HMDA[Y-1] x FHFA[Y]. FHFA income is
    # AMFI-inflated for prior-year originations (2016 SF File C dictionary, field 11).
    # Bars pt 1 and 3: a direct GSE sale belongs to FHFA[Y-1]. Deflating by AMFI rather
    # than Local Area Median Income raised the round from 825,849 to 2,114,008 pairs on
    # the 2010-2017 identity join while raising held-out race / ethnicity / sex /
    # borrower-count agreement in every year (aggregate race 98.6% -> 99.1% against a
    # 99.3% round 1 ceiling). Claims leave the pools before the 1:1 step: a loan is sold
    # once, round 1 is the stronger claim, and removing a claimed competitor re-admits
    # pairs. At 2011/2012 the sides sit on different tract vintages and the identity join
    # keeps ~71% of tracts; the bridge pass adds 65,369 pairs to 2012.
    Pre2018Round(
        description="1-year lag, AMFI-deflated income",
        lag=1,
        income="amfi",
        barred_purchasers=(1, 3),
        claimed="pool",
        resolve="unique_cells",
        vintage_bridge=True,
    ),
    # Contested-cell tie-break: round 1's candidates again, resolved on held-out fields;
    # an existing claim is never overridden.
    Pre2018Round(
        description="ambiguous-cell demographic tie-break",
        lag=0,
        income="exact",
        barred_purchasers=(0,),
        claimed="admissions",
        resolve="tiebreak",
    ),
)
