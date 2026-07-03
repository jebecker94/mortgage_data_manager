"""Round configurations for post-2018 seller-purchaser matching.

This module defines the configuration for all 10 matching rounds used in the
post-2018 HMDA seller-purchaser matching workflow. Each round is specified as
a RoundConfig instance with all parameters explicitly defined.

Round Summary:
- Rounds 1-2: Strict matches with exact geography, one-to-one
- Rounds 3-4: Relaxed matches allowing one-to-many for secondary sales
- Round 5: Same-year matches with loan amount tolerance
- Rounds 6-7: Matches excluding portfolio/NA originations
- Rounds 8-10: Constrained matches using prior round relationships
"""

from __future__ import annotations

from .round_config import (
    DataFilter,
    MatchColumnsConfig,
    RoundConfig,
    SpecialConstraints,
    ToleranceConfig,
)

# =============================================================================
# Round 1: Same-year exact matches with strict tolerances
# =============================================================================
ROUND_1 = RoundConfig(
    round_number=1,
    description="Same-year exact matches with strict tolerances",
    match_columns=MatchColumnsConfig(
        loan_type=True,
        loan_amount=True,
        census_tract=True,
        occupancy_type=True,
        loan_purpose=True,
        activity_year=True,
    ),
    year_constraint="same",  # Process year-by-year
    tolerances=ToleranceConfig(
        income=1000,
        interest_rate=0.0625,
    ),
    weak_tolerances=ToleranceConfig(
        interest_rate=0.01,
    ),
    post_tolerances=ToleranceConfig(
        conforming_loan_limit=0,
        construction_method=0,
        discount_points=5,
        income=1000,
        interest_rate=0.0625,
        intro_rate_period=6,
        lender_credits=5,
        lien_status=0,
        loan_term=12,
        open_end_line_of_credit=0,
        origination_charges=5,
        property_value=20000,
        total_units=0,
        applicant_age_above_62=0,
        co_applicant_age_above_62=0,
    ),
    fee_strategy="strict",
    one_to_one=True,
    use_demographics=True,
    use_drop_cleanup=True,
    # Early round workflow flags
    demographics_after_uniques=True,
    weak_tolerances_before_fees=True,
    use_numeric_matches_post_unique=False,
)

# =============================================================================
# Round 2: Cross-year matches with strict tolerances
# =============================================================================
ROUND_2 = RoundConfig(
    round_number=2,
    description="Cross-year matches with strict tolerances",
    match_columns=MatchColumnsConfig(
        loan_type=True,
        loan_amount=True,
        census_tract=True,
        occupancy_type=True,
        loan_purpose=True,
    ),
    year_constraint="cross",
    tolerances=ToleranceConfig(
        income=1000,
        interest_rate=0.0625,
    ),
    weak_tolerances=ToleranceConfig(
        interest_rate=0.01,
    ),
    post_tolerances=ToleranceConfig(
        conforming_loan_limit=0,
        construction_method=0,
        discount_points=5,
        income=1000,
        interest_rate=0.01,
        intro_rate_period=6,
        lender_credits=5,
        lien_status=0,
        loan_term=12,
        open_end_line_of_credit=0,
        origination_charges=5,
        property_value=20000,
        total_units=0,
        applicant_age_above_62=0,
        co_applicant_age_above_62=0,
    ),
    fee_strategy="strict",
    one_to_one=True,
    use_demographics=True,
    use_drop_cleanup=True,
    # Early round workflow flags
    demographics_after_uniques=True,
    weak_tolerances_before_fees=True,
    use_numeric_matches_post_unique=False,
)

# =============================================================================
# Round 3: Cross-year with relaxed uniqueness (allows secondary sales)
# =============================================================================
ROUND_3 = RoundConfig(
    round_number=3,
    description="Cross-year matches with relaxed uniqueness for secondary sales",
    match_columns=MatchColumnsConfig(
        loan_type=True,
        loan_amount=True,
        census_tract=True,
        occupancy_type=True,
        loan_purpose=True,
    ),
    year_constraint="cross",
    tolerances=ToleranceConfig(
        income=1000,
        interest_rate=0.0625,
        conforming_loan_limit=0,
        construction_method=0,
        intro_rate_period=6,
        lien_status=0,
        open_end_line_of_credit=0,
        total_units=0,
    ),
    weak_tolerances=ToleranceConfig(
        interest_rate=0.01,
    ),
    post_tolerances=ToleranceConfig(
        income=1000,
        interest_rate=0.0625,
        loan_term=12,
        property_value=20000,
    ),
    fee_strategy="generous",
    one_to_one=False,
    use_demographics=True,
)

# =============================================================================
# Round 4: Match without loan purpose; use i_Purchase indicator
# =============================================================================
ROUND_4 = RoundConfig(
    round_number=4,
    description="Match without loan purpose match, using i_Purchase indicator",
    match_columns=MatchColumnsConfig(
        loan_type=True,
        loan_amount=True,
        census_tract=True,
        occupancy_type=True,
        i_purchase=True,
    ),
    use_i_purchase=True,
    year_constraint="cross",
    tolerances=ToleranceConfig(
        income=1000,
        interest_rate=0.0625,
        conforming_loan_limit=0,
        construction_method=0,
        intro_rate_period=6,
        lien_status=0,
        open_end_line_of_credit=0,
        total_units=0,
    ),
    weak_tolerances=ToleranceConfig(
        interest_rate=0.01,
    ),
    post_tolerances=ToleranceConfig(
        income=1000,
        interest_rate=0.0625,
        loan_term=12,
        property_value=20000,
    ),
    fee_strategy="generous",
    one_to_one=False,
    use_demographics=True,
    filter_refi_types=True,
)

# =============================================================================
# Round 5: Same-year matches allowing slight loan amount mismatches
# =============================================================================
ROUND_5 = RoundConfig(
    round_number=5,
    description="Same-year matches with loan amount tolerance",
    match_columns=MatchColumnsConfig(
        loan_type=True,
        loan_amount=False,  # Note: loan_amount NOT in match columns
        census_tract=True,
        occupancy_type=True,
        i_purchase=True,
        activity_year=True,
    ),
    use_i_purchase=True,
    year_constraint="same",  # Implied by activity_year in match columns
    tolerances=ToleranceConfig(
        loan_amount=10000,
        income=1000,
        interest_rate=0.0625,
    ),
    weak_tolerances=ToleranceConfig(
        interest_rate=0.01,
    ),
    post_tolerances=ToleranceConfig(
        conforming_loan_limit=0,
        construction_method=0,
        discount_points=5,
        intro_rate_period=6,
        lender_credits=5,
        lien_status=0,
        loan_term=12,
        open_end_line_of_credit=0,
        origination_charges=5,
        property_value=20000,
        total_units=0,
        applicant_age_above_62=0,
        co_applicant_age_above_62=0,
    ),
    fee_strategy="strict",
    one_to_one=True,
    use_demographics=True,
    use_drop_cleanup=True,
    require_loan_amount_gte=True,
    purchaser_type_p_allowed=[0, 1, 2, 3, 4],
    # Early round workflow flags
    demographics_after_uniques=True,
    weak_tolerances_before_fees=True,
    use_numeric_matches_post_unique=False,
)

# =============================================================================
# Round 6: Match excluding portfolio and NA originations
# =============================================================================
ROUND_6 = RoundConfig(
    round_number=6,
    description="Match excluding portfolio/NA originations with looser tolerances",
    data_filter=DataFilter(
        filter_gse_sales=True,
        purchaser_type_exclude=[0, 9],
        drop_zeros=True,
    ),
    match_columns=MatchColumnsConfig(
        loan_type=True,
        loan_amount=True,
        census_tract=True,
        occupancy_type=True,
        i_purchase=True,
    ),
    use_i_purchase=True,
    year_constraint="cross",
    tolerances=ToleranceConfig(
        interest_rate=0.0625,
        conforming_loan_limit=0,
        construction_method=0,
        intro_rate_period=6,
        lien_status=0,
        open_end_line_of_credit=0,
        total_units=0,
    ),
    post_tolerances=ToleranceConfig(
        income=2000,
        interest_rate=0.01,
        loan_term=12,
        property_value=30000,
    ),
    fee_strategy="generous",
    one_to_one=False,
    use_demographics=True,
    filter_refi_types=True,
)

# =============================================================================
# Round 7: Minimal filters with strict fee requirements
# =============================================================================
ROUND_7 = RoundConfig(
    round_number=7,
    description="Minimal filters with fee match requirement",
    data_filter=DataFilter(
        filter_gse_sales=True,
        purchaser_type_exclude=[0, 9],
        drop_zeros=True,
    ),
    match_columns=MatchColumnsConfig(
        loan_type=True,
        loan_amount=True,
        census_tract=True,
        occupancy_type=True,
        i_purchase=True,
    ),
    use_i_purchase=True,
    year_constraint="cross",
    tolerances=ToleranceConfig(
        interest_rate=0.0625,
        total_units=0,
    ),
    post_tolerances=ToleranceConfig(
        interest_rate=0.01,
        loan_term=12,
    ),
    fee_strategy="none",  # Fee matching applied but not filtered until after uniques
    one_to_one=True,
    use_demographics=False,  # No demographics matching in round 7
    filter_refi_types=True,
    number_fee_matches_min=1,  # Require at least 1 fee match after uniques
)

# =============================================================================
# Round 8: Purchaser-type constrained matches
# =============================================================================
ROUND_8 = RoundConfig(
    round_number=8,
    description="Match with purchaser-type constraints from prior relationships",
    match_columns=MatchColumnsConfig(
        loan_type=True,
        loan_amount=True,
        census_tract=True,
        occupancy_type=True,
        i_purchase=True,
    ),
    use_i_purchase=True,
    year_constraint="cross",
    tolerances=ToleranceConfig(
        interest_rate=0.0625,
        conforming_loan_limit=0,
        construction_method=0,
        intro_rate_period=6,
        lien_status=0,
        open_end_line_of_credit=0,
        total_units=0,
    ),
    post_tolerances=ToleranceConfig(
        income=1000,
        interest_rate=0.01,
        loan_term=0,
        property_value=10000,
    ),
    fee_strategy="generous",
    one_to_one=False,
    use_demographics=True,
    filter_refi_types=True,
    use_quality_filters=True,
    special_constraints=SpecialConstraints(
        use_purchaser_types=True,
        purchaser_types_source_round=7,
        allow_purchaser_type_zero=True,
    ),
)

# =============================================================================
# All rounds in execution order
# =============================================================================
POST2018_ROUNDS = [
    ROUND_1,
    ROUND_2,
    ROUND_3,
    ROUND_4,
    ROUND_5,
    ROUND_6,
    ROUND_7,
    ROUND_8,
]

__all__ = [
    "ROUND_1",
    "ROUND_2",
    "ROUND_3",
    "ROUND_4",
    "ROUND_5",
    "ROUND_6",
    "ROUND_7",
    "ROUND_8",
    "POST2018_ROUNDS",
]
