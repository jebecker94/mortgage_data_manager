"""Configuration dataclasses for seller-purchaser matching rounds.

This module defines the dataclass-based configuration system for the
seller-purchaser matching workflow. Each round's parameters are captured
in a RoundConfig instance, enabling centralized parameter management and
easier experimentation with tolerance values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ToleranceConfig:
    """Numeric tolerance specifications for matching.

    Each attribute represents a maximum allowed absolute difference between
    seller and purchaser values. None means no tolerance filter is applied
    for that column.
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
        """Return only non-None tolerances as dict."""
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class DataFilter:
    """Pre-merge data filtering configuration.

    Controls which records are included before the merge step.
    """

    filter_gse_sales: bool = True
    action_taken_filter: list[int] | None = None  # e.g., [6]
    purchaser_type_include: list[int] | None = None  # e.g., [8]
    purchaser_type_exclude: list[int] | None = None  # e.g., [0, 9]
    drop_zeros: bool = False


@dataclass
class MatchColumnsConfig:
    """Columns used for joining sellers and purchasers.

    Each boolean attribute indicates whether that column should be
    included in the join keys.
    """

    loan_type: bool = True
    loan_amount: bool = True
    census_tract: bool = False
    county_code: bool = False
    occupancy_type: bool = True
    loan_purpose: bool = False
    i_purchase: bool = False
    activity_year: bool = False

    def to_list(self) -> list[str]:
        """Return list of enabled match columns."""
        mapping = {
            "loan_type": "loan_type",
            "loan_amount": "loan_amount",
            "census_tract": "census_tract",
            "county_code": "county_code",
            "occupancy_type": "occupancy_type",
            "loan_purpose": "loan_purpose",
            "i_purchase": "i_Purchase",
            "activity_year": "activity_year",
        }
        return [mapping[k] for k, v in self.__dict__.items() if v]


@dataclass
class SpecialConstraints:
    """Special constraint configurations for later rounds.

    These constraints leverage information from prior matching rounds
    to inform the current round's matching behavior.
    """

    # Affiliate-based constraints (Round 8)
    use_affiliates: bool = False
    affiliates_source_round: int | None = None
    affiliates_strict: bool = False

    # Purchaser type constraints (Rounds 9-10)
    use_purchaser_types: bool = False
    purchaser_types_source_round: int | None = None
    allow_purchaser_type_zero: bool = True

    # LEI relationship constraints (Round 10)
    use_lei_relationships: bool = False
    lei_relationships_source_round: int | None = None


@dataclass
class RoundConfig:
    """Complete configuration for a single matching round.

    This dataclass captures all parameters needed to execute a matching round,
    making the round's behavior fully specified by its configuration.

    Attributes:
        round_number: The round identifier (1-10 for post-2018 matching).
        description: Human-readable description of what this round does.
        data_filter: Configuration for pre-merge data filtering.
        match_columns: Configuration for which columns to use in the join.
        use_i_purchase: Whether to add the i_Purchase indicator before matching.
        year_constraint: How to handle year matching:
            - "same": Process year-by-year (seller year == purchaser year)
            - "cross": Allow cross-year matches (seller year <= purchaser year)
            - "none": No year filtering
        tolerances: First-pass numeric tolerance filters.
        weak_tolerances: Weak numeric tolerances for best-match filtering.
        post_tolerances: Post-unique numeric tolerances.
        fee_strategy: Fee matching strategy:
            - "strict": Require at least one exact fee match
            - "generous": Allow any fee column to match any other
            - "none": Skip fee filtering
        one_to_one: If True, require strict one-to-one matches.
            If False, allow one-to-many for secondary sales.
        use_demographics: Whether to apply demographic matching filters.
        filter_refi_types: Whether to allow non-matching refinance types.
        use_quality_filters: Whether to apply quality-based match filtering.
        use_drop_cleanup: Whether to apply drop observation cleanup.
        special_constraints: Configuration for round-specific special constraints.
        require_loan_amount_gte: If True, require loan_amount_s >= loan_amount_p.
        purchaser_type_p_allowed: If set, filter to only these purchaser types on purchaser side.
        number_fee_matches_min: If set, require at least this many fee matches (post-unique).
        demographics_after_uniques: If True, apply demographics matching after keep_uniques.
            Early rounds (1, 2, 5) set this True.
        weak_tolerances_before_fees: If True, apply weak tolerances before fee matching.
            Early rounds (1, 2, 5) set this True.
        use_numeric_matches_post_unique: If False, use numeric_matches for post_tolerances instead of
            numeric_matches_post_unique. Early rounds (1, 2, 5) set this False.
    """

    round_number: int
    description: str

    # Data loading & filtering
    data_filter: DataFilter = field(default_factory=DataFilter)

    # Match columns
    match_columns: MatchColumnsConfig = field(default_factory=MatchColumnsConfig)
    use_i_purchase: bool = False

    # Year constraint
    year_constraint: Literal["same", "cross", "none"] = "cross"

    # First-pass numeric tolerances
    tolerances: ToleranceConfig = field(default_factory=ToleranceConfig)

    # Weak numeric tolerances (best-match filtering)
    weak_tolerances: ToleranceConfig | None = None

    # Post-unique numeric tolerances
    post_tolerances: ToleranceConfig | None = None

    # Fee matching
    fee_strategy: Literal["strict", "generous", "none"] = "generous"

    # Uniqueness
    one_to_one: bool = True

    # Optional filters
    use_demographics: bool = True
    filter_refi_types: bool = False
    use_quality_filters: bool = False
    use_drop_cleanup: bool = False

    # Special constraints
    special_constraints: SpecialConstraints = field(default_factory=SpecialConstraints)

    # Post-match filters
    require_loan_amount_gte: bool = False  # loan_amount_s >= loan_amount_p
    purchaser_type_p_allowed: list[int] | None = None  # e.g., [0,1,2,3,4]
    number_fee_matches_min: int | None = None  # Minimum fee matches required

    # Workflow ordering flags
    demographics_after_uniques: bool = False  # Early rounds set True
    weak_tolerances_before_fees: bool = False  # Early rounds set True
    use_numeric_matches_post_unique: bool = True  # Early rounds set False


__all__ = [
    "ToleranceConfig",
    "DataFilter",
    "MatchColumnsConfig",
    "SpecialConstraints",
    "RoundConfig",
]
