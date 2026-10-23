"""Per-enterprise specification for the MBS<->FHFA matcher.

The Fannie Mae and Freddie Mac legs run one engine (``matching.py``). Everything
that differs between the enterprises is data: where the loan-level files live
and how their columns map onto the canonical matching schema (``inputs.py``),
and the option values below. The engine works in the canonical loan key
``loan_id`` and origination year ``origination_year`` and renames to the
enterprise's crosswalk key only when the crosswalk is cut.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import polars as pl

from mortgage_data_manager.matching.match_fhfa_mbs.rounds import Round

LOAN_ID = "loan_id"
FHFA_ID = "fhfa_record_id"

#: Fields the join does not key on, compared under a numeric tolerance instead.
#: ``(tolerance attribute, whether a null deviation scores as 0)``. The score
#: builder reads both; the filters read the tolerance.
SCORE_FIELDS: dict[str, tuple[str, bool]] = {
    "interest_rate": ("rate_tolerance", False),
    "loan_amount": ("amount_tolerance", False),
    "ltv": ("ltv_tolerance", True),
    "dti": ("dti_tolerance", True),
}

PoolFilter = Callable[[pl.DataFrame, int, bool], pl.DataFrame]
"""``(pool, acquisition_year, overwrite) -> pool``: a filter on the loans a round offers."""


@dataclass(frozen=True)
class MatchingTolerances:
    """Numeric slack on the fields the join does not key on.

    FHFA reports binned values: loan amount in $10k bins at the midpoint, LTV as an
    integer, DTI as a mix of bin codes and exact values (see
    `~mortgage_data_manager.matching.match_fhfa_mbs.matching.build_dti_match_expr`).
    The tolerances below are what remains once the binning is undone by the preparers.

    Attributes:
        rate_tolerance: Interest-rate slack in percentage points. FHFA rounds
            Freddie rates to hundredths, so 1bp is near-exact for both enterprises.
        amount_tolerance: Loan-amount slack in dollars. Zero for both enterprises --
            amounts are pre-rounded to bin midpoints and joined exactly -- but it
            still sets the amount term's score weight where that term is enabled.
        ltv_tolerance: LTV slack in percentage points.
        dti_tolerance: DTI slack for the exact range (36-49) and the bin-30 boundary.
        zip3_penalty: Score penalty for a ZIP3 disagreement, strictly smaller than
            one rate step or one LTV point so it splits score-tied twins without
            overriding a better candidate. Null on either side is neutral; 0 disables.
    """

    rate_tolerance: float = 0.01
    amount_tolerance: float = 0.0
    ltv_tolerance: float = 1.0
    dti_tolerance: float = 1.0
    zip3_penalty: float = 0.5

    def weight(self, score_field: str) -> float:
        """Score weight for one field: the reciprocal of its tolerance.

        A zero tolerance means the field is matched exactly, so any deviation is
        maximally bad; 1000 stands in for the infinite weight.

        Args:
            score_field: Name of the scored field.

        Returns:
            The field's score weight; 1000 stands in for an exact-match field.
        """
        tolerance = getattr(self, SCORE_FIELDS[score_field][0])
        return 1.0 / tolerance if tolerance > 0 else 1000.0


@dataclass(frozen=True)
class MatchOptions:
    """Everything about a matching pass that is not a numeric tolerance.

    Attributes:
        score_fields: Fields entering the match score, in the order they are summed.
            Order is load-bearing: floating-point addition is not associative and
            the sum decides ties. A field is compared against its ``_fhfa``
            counterpart.
        non_evidence_score_fields: Score fields that carry no information about
            whether a pair is true, and so may not decide one. A candidate is
            admitted only where the remaining -- evidence-bearing -- fields single it
            out for its FHFA record on their own and pick the same candidate the
            whole score does; where they tie, or prefer a different candidate, the
            record goes unmatched rather than being settled by these fields (see
            `~mortgage_data_manager.matching.match_fhfa_mbs.matching.admitted`). Empty
            for a score every field of which is evidence.
        tier_rate_epsilon: Rate deviation still counted as exact when assigning the
            quality tier. Zero demands a literal exact rate -- or FHFA's hundredths
            truncation of it, which the engine always accepts (see
            `~mortgage_data_manager.matching.match_fhfa_mbs.matching.quality_tier`).
        tier_on_amount: Add a top tier requiring the amount to sit exactly on the
            FHFA bin midpoint, shifting every other tier down by one.
        term_envelope: Term up-binning a round's envelope phase applies (`Round.term`),
            or None where terms are reported verbatim. FHFA reports a seller-dependent
            subset of standard Freddie terms at the security-product term; Fannie terms
            are preserved verbatim.
        pool_filter: The filter a round with ``pool_filter`` applies to the loans it
            offers, or None. Freddie keeps loans whose first performance period sits in
            the acquisition window.
        prior_year_cohorts: Origination cohorts to load alongside the acquisition year.
            Bounds every leg's ``max_lag``. Sources organised by acquisition already
            carry their earlier originations, and pass 1.
    """

    score_fields: tuple[str, ...]
    tier_rate_epsilon: float
    tier_on_amount: bool
    non_evidence_score_fields: tuple[str, ...] = ()
    term_envelope: Mapping[int, int] | None = None
    pool_filter: PoolFilter | None = None
    prior_year_cohorts: int = 1

    def evidence_score_fields(self) -> tuple[str, ...]:
        """The score fields that may decide a pair: ``score_fields`` less the non-evidence ones.

        Returns:
            The evidence-bearing score fields, in the score's summation order.
        """
        return tuple(f for f in self.score_fields if f not in self.non_evidence_score_fields)


@dataclass(frozen=True)
class GseSpec:
    """One enterprise: where its loan-level data lives and how it maps onto FHFA.

    Attributes:
        name: Short enterprise token, used in file names and log lines.
        id_column: Loan key on the written crosswalk. The engine works in
            ``loan_id`` and renames on the way out.
        enterprise_flag: FHFA ``enterprise_flag`` value (1 Fannie, 2 Freddie).
        enterprise_dtype: Dtype the written ``fhfa_enterprise`` column carries.
            Per enterprise because the two crosswalks have always disagreed
            (Int8 vs Int64); normalising it is a consumer-facing change of its own.
        load: Scan the enterprise's loan-level data for an acquisition year, with
            ``prior_years`` earlier origination cohorts alongside.
        prepare: Map that data onto the canonical matching schema.
        crosswalk_columns: Written column order, again per enterprise only because
            the two crosswalks have always disagreed.
        tolerances: Numeric slack.
        options: Everything else.
        rounds: The enterprise's rounds, in run order (``rounds.py``).
        max_borrowers: Borrower count both sides are top-coded at, or None to compare
            counts verbatim. The enterprise's ``prepare`` caps its own side; the FHFA
            side is capped in `matching.prepare_fhfa`.
    """

    name: str
    id_column: str
    enterprise_flag: int
    enterprise_dtype: type[pl.DataType]
    load: Callable[[int, int], pl.LazyFrame]
    prepare: Callable[[pl.LazyFrame], pl.LazyFrame]
    crosswalk_columns: tuple[str, ...]
    tolerances: MatchingTolerances
    options: MatchOptions
    rounds: tuple[Round, ...]
    max_borrowers: int | None = None
