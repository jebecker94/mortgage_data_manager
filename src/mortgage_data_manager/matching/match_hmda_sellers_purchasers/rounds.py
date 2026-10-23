"""The HMDA seller-purchaser matching rounds, both eras.

Each era runs its rounds in the order listed, and each round sees only the records earlier
rounds left unmatched. A round's ``match_round`` is its position in the tuple (1-based). To
add a round, copy an entry, change its fields and re-run; nothing else needs editing.

Post-2018 (`ROUNDS_POST2018`), run by ``matching_post2018.py``:

1. Same year, exact geography, strict tolerances, one-to-one.
2. Cross-year version of round 1 (seller year <= purchaser year), tighter post-unique rate.
3. Cross-year, relaxed uniqueness so secondary sales can fan out.
4. Round 3 without the loan-purpose key, using the purchase indicator instead.
5. Same year with the amount out of the key and under a $10k tolerance.
6. Excludes portfolio / NA originations, looser income and property-value checks.
7. Minimal tolerances, but at least one exact fee match after resolution.
8. Seller->purchaser pairs whose round 1-7 matches carry one dominant purchaser type.

2007-2017 (`ROUNDS_PRE2018`), run by ``matching_pre2018.py``: a same-year round on a binned
amount, then a pooled round on the exact amount across the year boundary. Its amounts are in
dollars, as HMDA silver reports them; the tolerances were set in the raw files' thousands,
hence `DOLLARS_PER_THOUSAND`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

import polars as pl

# =============================================================================
# Post-2018
# =============================================================================


@dataclass(frozen=True)
class Tolerances:
    """Largest ``|seller - purchaser|`` gap allowed per column; None skips the column.

    A tolerance of 0 is an exact-match requirement, not "no filter".
    """

    income: float | None = None
    interest_rate: float | None = None
    loan_amount: float | None = None
    property_value: float | None = None
    loan_term: float | None = None
    conforming_loan_limit: float | None = None
    construction_method: float | None = None
    discount_points: float | None = None
    intro_rate_period: float | None = None
    lender_credits: float | None = None
    lien_status: float | None = None
    open_end_line_of_credit: float | None = None
    origination_charges: float | None = None
    total_units: float | None = None
    applicant_age_above_62: float | None = None
    co_applicant_age_above_62: float | None = None

    def to_dict(self) -> dict[str, float]:
        """The tolerances that are set (0 included), keyed by column, in field order.

        Returns:
            The set tolerances, keyed by field name.
        """
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass(frozen=True)
class Round:
    """One post-2018 matching round.

    Attributes:
        description: Label for the logs.
        match_columns: Columns joined exactly; ``i_Purchase`` (the purchase indicator) is
            derived first when listed.
        years: ``"same"`` runs year by year; ``"cross"`` pools years and keeps seller
            year <= purchaser year.
        tolerances: First-pass tolerances, applied right after the join.
        weak: Best-match tolerances, applied before or after the fee step (`early_order`).
        post: Tolerances applied after resolution.
        fees: ``"strict"`` needs an exact same-column fee match, ``"generous"`` any
            cross-column fee match, ``"none"`` skips the fee filter.
        one_to_one: Strict 1:1; otherwise a seller may fan out to secondary sales.
        demographics: Apply the demographic consistency filters.
        refi_types: Drop pairs whose refinance types disagree.
        quality_filters: Keep only the best-quality candidates where better ones exist.
        drop_cleanup: Apply the drop-observation cleanup after resolution.
        early_order: The early rounds' step order: demographics after resolution, weak
            tolerances before the fee step, and plain (not post-unique) post tolerances.
        loan_amount_gte: Require seller amount >= purchaser amount.
        purchaser_types_p: Purchaser-side purchaser types allowed after resolution.
        min_fee_matches: Minimum exact fee matches required after resolution.
        purchaser_type_exclude: Keep purchases plus originations not of these types.
        drop_zeros: Drop zero-valued numeric sentinels before the join.
        purchaser_types_from: Restrict to seller->purchaser LEI pairs whose matches up to
            this round carry one dominant purchaser type (``purchaser_type_s == 0`` passes).
    """

    description: str
    match_columns: tuple[str, ...]
    years: Literal["same", "cross"] = "cross"
    tolerances: Tolerances = field(default_factory=Tolerances)
    weak: Tolerances | None = None
    post: Tolerances | None = None
    fees: Literal["strict", "generous", "none"] = "generous"
    one_to_one: bool = True
    demographics: bool = True
    refi_types: bool = False
    quality_filters: bool = False
    drop_cleanup: bool = False
    early_order: bool = False
    loan_amount_gte: bool = False
    purchaser_types_p: tuple[int, ...] | None = None
    min_fee_matches: int | None = None
    purchaser_type_exclude: tuple[int, ...] | None = None
    drop_zeros: bool = False
    purchaser_types_from: int | None = None


_KEYS = ("loan_type", "loan_amount", "census_tract", "occupancy_type")
_KEYS_PURPOSE = (*_KEYS, "loan_purpose")
_KEYS_INDICATOR = (*_KEYS, "i_Purchase")

_FIRST = Tolerances(income=1000, interest_rate=0.0625)
_FIRST_PRODUCT = Tolerances(
    income=1000,
    interest_rate=0.0625,
    conforming_loan_limit=0,
    construction_method=0,
    intro_rate_period=6,
    lien_status=0,
    open_end_line_of_credit=0,
    total_units=0,
)
_WEAK = Tolerances(interest_rate=0.01)
_POST_STRICT = Tolerances(
    income=1000,
    interest_rate=0.0625,
    property_value=20000,
    loan_term=12,
    conforming_loan_limit=0,
    construction_method=0,
    discount_points=5,
    intro_rate_period=6,
    lender_credits=5,
    lien_status=0,
    open_end_line_of_credit=0,
    origination_charges=5,
    total_units=0,
    applicant_age_above_62=0,
    co_applicant_age_above_62=0,
)
_POST_LOOSE = Tolerances(income=1000, interest_rate=0.0625, property_value=20000, loan_term=12)

ROUNDS_POST2018: tuple[Round, ...] = (
    Round(
        description="Same-year exact matches with strict tolerances",
        match_columns=(*_KEYS_PURPOSE, "activity_year"),
        years="same",
        tolerances=_FIRST,
        weak=_WEAK,
        post=_POST_STRICT,
        fees="strict",
        drop_cleanup=True,
        early_order=True,
    ),
    Round(
        description="Cross-year matches with strict tolerances",
        match_columns=_KEYS_PURPOSE,
        tolerances=_FIRST,
        weak=_WEAK,
        post=replace(_POST_STRICT, interest_rate=0.01),
        fees="strict",
        drop_cleanup=True,
        early_order=True,
    ),
    Round(
        description="Cross-year matches with relaxed uniqueness for secondary sales",
        match_columns=_KEYS_PURPOSE,
        tolerances=_FIRST_PRODUCT,
        weak=_WEAK,
        post=_POST_LOOSE,
        one_to_one=False,
    ),
    Round(
        description="Match without loan purpose match, using i_Purchase indicator",
        match_columns=_KEYS_INDICATOR,
        tolerances=_FIRST_PRODUCT,
        weak=_WEAK,
        post=_POST_LOOSE,
        one_to_one=False,
        refi_types=True,
    ),
    Round(
        description="Same-year matches with loan amount tolerance",
        match_columns=(
            "loan_type",
            "census_tract",
            "occupancy_type",
            "i_Purchase",
            "activity_year",
        ),
        years="same",
        tolerances=replace(_FIRST, loan_amount=10000),
        weak=_WEAK,
        post=replace(_POST_STRICT, income=None, interest_rate=None),
        fees="strict",
        drop_cleanup=True,
        early_order=True,
        loan_amount_gte=True,
        purchaser_types_p=(0, 1, 2, 3, 4),
    ),
    Round(
        description="Match excluding portfolio/NA originations with looser tolerances",
        match_columns=_KEYS_INDICATOR,
        tolerances=replace(_FIRST_PRODUCT, income=None),
        post=Tolerances(income=2000, interest_rate=0.01, property_value=30000, loan_term=12),
        one_to_one=False,
        refi_types=True,
        purchaser_type_exclude=(0, 9),
        drop_zeros=True,
    ),
    Round(
        description="Minimal filters with fee match requirement",
        match_columns=_KEYS_INDICATOR,
        tolerances=Tolerances(interest_rate=0.0625, total_units=0),
        post=Tolerances(interest_rate=0.01, loan_term=12),
        fees="none",  # fee matches are counted, then required after resolution
        demographics=False,
        refi_types=True,
        min_fee_matches=1,
        purchaser_type_exclude=(0, 9),
        drop_zeros=True,
    ),
    Round(
        description="Match with purchaser-type constraints from prior relationships",
        match_columns=_KEYS_INDICATOR,
        tolerances=replace(_FIRST_PRODUCT, income=None),
        post=Tolerances(income=1000, interest_rate=0.01, property_value=10000, loan_term=0),
        one_to_one=False,
        refi_types=True,
        quality_filters=True,
        purchaser_types_from=7,
    ),
)


# =============================================================================
# 2007-2017
# =============================================================================

#: Silver reports dollars where the raw files reported thousands.
DOLLARS_PER_THOUSAND = 1_000


@dataclass(frozen=True, eq=False)
class EraRound:
    """One 2007-2017 matching round, as data the engine runs.

    Attributes:
        match_columns: Exact join keys between sellers and purchasers.
        pooled: False matches each activity year alone; True pools the requested
            years and admits a purchase in the origination year or the next.
        bin_loan_amount: Build the $10k ``LoanAmountBin`` key and offer sellers in
            a bin's lower half to the bin below as well.
        income_tolerance: Income tolerance (dollars) before uniqueness.
        income_tolerance_tight: Income tolerance (dollars) after uniqueness.
        filters: The round's own post-uniqueness filters.
    """

    match_columns: tuple[str, ...]
    pooled: bool
    bin_loan_amount: bool
    income_tolerance: int
    income_tolerance_tight: int
    filters: tuple[pl.Expr, ...]


_SAME_YEAR = pl.col("activity_year_s") == pl.col("activity_year_p")
_NOT_SOLD = pl.col("purchaser_type_s") == 0

ROUNDS_PRE2018: tuple[EraRound, ...] = (
    # Same year, $10k amount bin; sellers in a bin's lower half are offered to the bin below.
    EraRound(
        match_columns=(
            "loan_type",
            "census_tract",
            "occupancy_type",
            "loan_purpose",
            "LoanAmountBin",
        ),
        pooled=False,
        bin_loan_amount=True,
        income_tolerance=5 * DOLLARS_PER_THOUSAND,
        income_tolerance_tight=0,
        filters=(
            (pl.col("loan_amount_s") - pl.col("loan_amount_p")).is_in(
                [0, 1 * DOLLARS_PER_THOUSAND]
            ),
            pl.col("purchaser_type_s") > 0,  # drop likely balance-sheet loans
            pl.col("property_type_s") == pl.col("property_type_p"),
        ),
    ),
    # Pooled years, exact amount and property type; the purchase may fall in the
    # origination year or the next.
    EraRound(
        match_columns=(
            "loan_type",
            "loan_amount",
            "census_tract",
            "occupancy_type",
            "property_type",
            "loan_purpose",
        ),  # fmt: skip
        pooled=True,
        bin_loan_amount=False,
        income_tolerance=5 * DOLLARS_PER_THOUSAND,
        income_tolerance_tight=1 * DOLLARS_PER_THOUSAND,
        # A same-year pair must report a sale; a next-year pair must not.
        filters=(_SAME_YEAR | _NOT_SOLD, ~_SAME_YEAR | ~_NOT_SOLD),
    ),
)
