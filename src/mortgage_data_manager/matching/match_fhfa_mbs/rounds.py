"""The FHFA-MBS matching rounds, one tuple per enterprise plus the ARM pass.

Each acquisition year runs its enterprise's rounds in the order listed, and each round sees
only the loans and FHFA records earlier rounds left. A round's ``match_round`` is its
position in its tuple (1-based). To add a round, copy an entry, change its fields and
re-run; nothing else needs editing. ``mortgage-data match fhfa-mbs info`` prints the
tuples.

Every enterprise round joins on the exact keys (``EXACT_KEYS`` in ``matching.py``), filters
under the enterprise's tolerances, scores, and keeps mutual best pairs, dropping ties. Every pair a
round finds retires its loan and its record, including a pair the round's gate then
rejects, so a rejected pair cannot resurface later. After the rounds, a loan claimed in two
acquisition years is dropped from both: the earlier round's claim is right only 72% of the
time on held-out census tract (798 contested FHLMC loans, 2018-2025).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Leg:
    """Which loan origination years a round offers to one FHFA origination-timing flag.

    Attributes:
        fhfa_flag: FHFA ``date_of_mortgage_note``: 1 for a loan originated in the
            acquisition year, 2 for one originated earlier.
        min_lag: Youngest origination offered, in years before the acquisition year.
        max_lag: Oldest origination offered, in years before the acquisition year; None
            for every origination the loaded pool holds.
    """

    fhfa_flag: int
    min_lag: int
    max_lag: int | None


@dataclass(frozen=True)
class Round:
    """One matching round. Every round states every field.

    Attributes:
        description: Label for the logs and ``info``.
        legs: The origination years offered to each FHFA flag. Each leg is matched on
            its own, so the flags must be distinct.
        term: ``"exact"`` joins on the term as reported. ``"envelope"`` re-offers only
            loans whose term the enterprise's ``term_envelope`` up-bins, at the
            product-envelope term. ``"both"`` runs the exact phase and then the envelope
            phase on its leftovers, within every pass.
        max_passes: 1 runs one pass. More re-runs the pass on its own leftovers until a
            pass finds nothing new or the limit is reached: a loan whose best record
            preferred another loan gets nothing on a single pass even when a compatible
            record remains.
        msa: Join on MSA. False neutralises it on both sides, with state still exact.
        occupancy_recode: Offer primary-residence loans to investor-coded FHFA records,
            and nothing else.
        pool_filter: Apply the enterprise's ``MatchOptions.pool_filter`` to the loans
            offered.
        require_zip3: Reject a pair whose ``zip3_consistent`` is False (null passes).
        max_tier: Reject a pair whose ``match_quality_tier`` exceeds this; None for no
            cap.
    """

    description: str
    legs: tuple[Leg, ...]
    term: Literal["exact", "envelope", "both"]
    max_passes: int
    msa: bool
    occupancy_recode: bool
    pool_filter: bool
    require_zip3: bool
    max_tier: int | None


#: The acquisition-year legs: flag 1 against the acquisition year's originations, flag 2
#: against the year before's.
ACQUISITION_LEGS: tuple[Leg, ...] = (
    Leg(fhfa_flag=1, min_lag=0, max_lag=0),
    Leg(fhfa_flag=2, min_lag=1, max_lag=1),
)

ROUNDS_FNMA: tuple[Round, ...] = (
    Round(
        description="base",
        legs=ACQUISITION_LEGS,
        term="exact",
        max_passes=1,
        msa=True,
        occupancy_recode=False,
        pool_filter=False,
        require_zip3=False,
        max_tier=None,
    ),
    Round(
        description="fixpoint",
        legs=ACQUISITION_LEGS,
        term="exact",
        max_passes=40,
        msa=True,
        occupancy_recode=False,
        pool_filter=False,
        require_zip3=True,
        max_tier=None,
    ),
    # FHFA's prior-year flag means "originated in any earlier year", which the base legs
    # never offer.
    Round(
        description="seasoned",
        legs=(Leg(fhfa_flag=2, min_lag=2, max_lag=None),),
        term="exact",
        max_passes=10,
        msa=True,
        occupancy_recode=False,
        pool_filter=False,
        require_zip3=True,
        max_tier=None,
    ),
    # The two sources can disagree on MSA for the same loan (delineation vintages, and a
    # seller-reported MSA that disagrees with the property's county).
    Round(
        description="msa-free",
        legs=(Leg(fhfa_flag=1, min_lag=0, max_lag=0), Leg(fhfa_flag=2, min_lag=1, max_lag=None)),
        term="exact",
        max_passes=1,
        msa=False,
        occupancy_recode=False,
        pool_filter=False,
        require_zip3=True,
        max_tier=None,
    ),
)

ROUNDS_FHLMC: tuple[Round, ...] = (
    Round(
        description="base",
        legs=ACQUISITION_LEGS,
        term="exact",
        max_passes=1,
        msa=True,
        occupancy_recode=False,
        pool_filter=False,
        require_zip3=False,
        max_tier=None,
    ),
    # Exact-term pairs claim records first; a joint pass loses ~0.3% of them to cross-term
    # ambiguity for the same recovery.
    Round(
        description="product-envelope term",
        legs=ACQUISITION_LEGS,
        term="envelope",
        max_passes=1,
        msa=True,
        occupancy_recode=False,
        pool_filter=False,
        require_zip3=False,
        max_tier=None,
    ),
    # From here on the pool is restricted to loans first reported in the acquisition
    # window, which removes the twins Freddie acquired a year or more earlier. Fixpoint
    # pairs at tier 3 are twins on held-out validation.
    Round(
        description="fixpoint",
        legs=ACQUISITION_LEGS,
        term="both",
        max_passes=40,
        msa=True,
        occupancy_recode=False,
        pool_filter=True,
        require_zip3=True,
        max_tier=2,
    ),
    # Bounded by the origination cohorts loaded (``FHLMC_PRIOR_COHORTS``).
    Round(
        description="seasoned",
        legs=(Leg(fhfa_flag=2, min_lag=2, max_lag=5),),
        term="both",
        max_passes=10,
        msa=True,
        occupancy_recode=False,
        pool_filter=True,
        require_zip3=True,
        max_tier=None,
    ),
    Round(
        description="msa-free",
        legs=(Leg(fhfa_flag=1, min_lag=0, max_lag=0), Leg(fhfa_flag=2, min_lag=1, max_lag=5)),
        term="both",
        max_passes=1,
        msa=False,
        occupancy_recode=False,
        pool_filter=True,
        require_zip3=True,
        max_tier=None,
    ),
    # A primary/investor coding difference, accepted only at the two highest tiers.
    Round(
        description="occupancy",
        legs=(Leg(fhfa_flag=1, min_lag=0, max_lag=0), Leg(fhfa_flag=2, min_lag=1, max_lag=5)),
        term="both",
        max_passes=1,
        msa=True,
        occupancy_recode=True,
        pool_filter=True,
        require_zip3=True,
        max_tier=2,
    ),
)


@dataclass(frozen=True)
class ArmRound:
    """One FHFA->UMBS ARM round, pooled over acquisition years and both enterprises.

    Attributes:
        description: Label for the logs and ``info``.
        keys: Columns joined exactly; the numeric fields are scored as in every round.
    """

    description: str
    keys: tuple[str, ...]


ROUNDS_ARM: tuple[ArmRound, ...] = (
    # MSA is left out: the disclosures carry no geography below the state.
    ArmRound(
        description="exact keys and origination year",
        keys=(
            "state_fips",
            "channel",
            "num_units",
            "num_borrowers",
            "fthb",
            "loan_purpose",
            "occupancy_code",
            "property_type",
            "term",
            "loan_amount_rounded",
            "origination_year",
        ),
    ),
)
