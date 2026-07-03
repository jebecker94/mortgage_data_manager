"""Configuration for FHFA-HMDA matching rounds.

This module defines the config-driven three-round matching strategy:
- Round 1: Census tract + purchaser + demographics (same year, pt 1,3)
- Round 2: Census tract only (same year, pt 1,3)
- Round 3: Cross-year matching for all purchaser types with post-unique filter

Key design decisions:
- Pre-merge filters: Applied to HMDA pool before join (purchaser type filtering)
- Merge config: Controls join keys (purchaser, census tract, same-year vs cross-year)
- Post-merge filters: Applied after join, before uniqueness (rate, term, demographics)
- Post-unique filters: Optional tighter tolerances applied after 1:1 constraint

Hardcoded elements (not configurable):
- Merge keys: census_tract, loan_amount, loan_type, NoteRate125, occupancy_type
  (plus optional purchaser)
- NoteRate125 bucketing: (rate * 8).round() * 125
- Cross-year direction: Forward only (FHFA year >= HMDA year)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PreMergeFilters:
    """Filters applied BEFORE join (to HMDA pool).

    Controls which HMDA loans are eligible for matching based on purchaser_type:
    - pt=0: Not sold at origination
    - pt=1,3: GSE (Fannie Mae, Freddie Mac)
    - pt>=5: Secondary market (banks, credit unions, etc.)
    """

    include_pt0: bool = False  # purchaser_type=0 (not sold at origination)
    include_gse: bool = True  # purchaser_type in [1, 3] (Fannie/Freddie)
    include_secondary: bool = False  # purchaser_type >= 5 (banks, CUs, etc.)


@dataclass
class MergeConfig:
    """Join configuration.

    Controls how HMDA and FHFA records are matched:
    - use_purchaser: Whether to include purchaser_type in join keys
    - same_year_only: Whether to match within same year only (vs cross-year)
    - use_fuzzy_rates: Whether to use floor/ceil rate bins instead of exact rate
    - use_rate: Whether to include interest rate in join keys at all
    - vintage_bridge: Whether to translate FHFA's 2020-vintage tract back to
      its 2010-vintage candidates via the Census Bureau tract relationship
      file. Only fires on year pairs that cross the 2021/2022 boundary.
    """

    use_purchaser: bool = True  # Include purchaser_type/PurchaserType in join
    same_year_only: bool = True  # HMDA year == FHFA year (if False: FHFA >= HMDA)
    use_fuzzy_rates: bool = False  # Use floor/ceil rate bins instead of exact NoteRate125
    use_rate: bool = True  # Include rate in join keys (False = match without rate)
    vintage_bridge: bool = False  # Translate FHFA tract via 2010↔2020 Census relationship file


@dataclass
class PostMergeFilters:
    """Filters applied AFTER join, BEFORE uniqueness.

    These are the base tolerance filters applied during matching:
    - rate_tolerance: Maximum difference in interest rate (percentage points)
    - term_tolerance: Maximum difference in loan term (months)
    - require_demographics: Whether age/sex must match
    - require_current_year_orig: Whether date_of_mortgage_note must equal 1 (current year origination)
    - filter_loan_purpose: Whether to prevent purchase/refinance mismatches
    - filter_dti: Whether to validate DTI consistency between datasets

    DTI Encoding (identical in both datasets):
    - Binned codes: 10 (<20%), 20 (20-30%), 30 (30-36%), 50 (50-60%), 60 (>60%)
    - Numeric values: 36-49 reported as actual integers
    - Missing codes: 99 (FHFA), NA/Exempt (HMDA)
    """

    rate_tolerance: float = 0.05  # |rate_diff| <= tolerance
    term_tolerance: int = 30  # |term_diff| <= tolerance (months)
    require_demographics: bool = True  # age bucket, age_62+, sex must match
    require_current_year_orig: bool = True  # date_of_mortgage_note == 1 (current year origination)
    filter_loan_purpose: bool = False  # Prevent Purchase↔Refi/CashOut mismatches (allows Refi↔CashOut)
    filter_loan_purpose_strict: bool = False  # Require exact purpose match (Refi ≠ CashOut)
    require_prior_year_for_gse: bool = False  # For cross-year: GSE must have date_of_mortgage_note=2
    require_cross_year_for_pt0: bool = False  # pt=0 requires cross-year + date_of_mortgage_note=2
    filter_dti: bool = False  # Validate DTI consistency
    dti_tolerance: int = 2  # Tolerance for numeric DTI range (36-49)


@dataclass
class PostUniqueFilters:
    """Filters applied AFTER uniqueness constraint (optional tightening).

    These are tighter tolerances applied after the 1:1 constraint is enforced,
    used to prune lower-quality matches from cross-year rounds.
    """

    rate_tolerance: float = 0.01  # Tighter than post-merge
    term_tolerance: int = 6  # Tighter than post-merge


@dataclass
class MatchRoundConfig:
    """Complete configuration for a single match round.

    Each round specifies:
    - round_number: Identifier for the round
    - description: Human-readable description
    - pre_merge: Filters for HMDA pool (purchaser types)
    - merge: Join configuration (keys, same-year vs cross-year)
    - post_merge: Filters after join (rate, term, demographics)
    - post_unique: Optional tighter filters after 1:1 constraint
    """

    round_number: int
    description: str
    pre_merge: PreMergeFilters = field(default_factory=PreMergeFilters)
    merge: MergeConfig = field(default_factory=MergeConfig)
    post_merge: PostMergeFilters = field(default_factory=PostMergeFilters)
    post_unique: PostUniqueFilters | None = None  # Optional final tightening


# Round 1: Strict GSE Match (Same Year)
# Census tract + purchaser + demographics (same year, pt 1,3)
ROUND_1 = MatchRoundConfig(
    round_number=1,
    description="Census tract + purchaser + demographics + DTI + loan purpose (same year)",
    pre_merge=PreMergeFilters(include_gse=True),  # pt 1,3 only
    merge=MergeConfig(use_purchaser=True, same_year_only=True),
    post_merge=PostMergeFilters(require_demographics=True, filter_dti=True, filter_loan_purpose_strict=True),
)

# Round 2: Relaxed Match (Same Year, No Demographics, All Purchasers)
# Census tract only, all purchaser types (same year)
ROUND_2 = MatchRoundConfig(
    round_number=2,
    description="Census tract + DTI + loan purpose, all PT (same year, no demographics)",
    pre_merge=PreMergeFilters(include_pt0=True, include_gse=True, include_secondary=True),
    merge=MergeConfig(use_purchaser=False, same_year_only=True),
    post_merge=PostMergeFilters(require_demographics=False, filter_dti=True, filter_loan_purpose_strict=True),
)

# Round 3: Cross-Year All Purchasers + Moderate Filter
# Cross-year, all purchaser types + moderate quality filter
# Note: require_current_year_orig=False allows prior-year originations (date_of_mortgage_note=2)
# Filters: require_prior_year_for_gse prevents false GSE matches on same-year originations
#          require_cross_year_for_pt0 ensures pt=0 only matches cross-year with prior-year orig
#          filter_loan_purpose removes Purchase↔Refi/CashOut mismatches
ROUND_3 = MatchRoundConfig(
    round_number=3,
    description="Cross-year + DTI, all PT + moderate quality filter",
    pre_merge=PreMergeFilters(include_pt0=True, include_gse=True, include_secondary=True),
    merge=MergeConfig(use_purchaser=False, same_year_only=False),  # FHFA >= HMDA
    post_merge=PostMergeFilters(
        require_demographics=True, require_current_year_orig=False, filter_dti=True,
        require_prior_year_for_gse=True, require_cross_year_for_pt0=True, filter_loan_purpose=True
    ),
    post_unique=PostUniqueFilters(rate_tolerance=0.01, term_tolerance=6),
)

# Round 4: No Rate Matching with Moderate Filters (Same Year)
# Combined fuzzy rate and no-rate strategy - matches without interest rate
# Uses tight filters on other variables to ensure quality
# Catches loans with missing/wrong/fuzzy rate but valid census tract, amount, etc.
# Note: require_cross_year_for_pt0 filters out pt=0 since this is same-year only
ROUND_4 = MatchRoundConfig(
    round_number=4,
    description="No rate match + moderate filters (same year)",
    pre_merge=PreMergeFilters(include_pt0=True, include_gse=True, include_secondary=True),
    merge=MergeConfig(use_purchaser=False, same_year_only=True, use_rate=False),
    post_merge=PostMergeFilters(
        rate_tolerance=0.20,  # Moderate rate tolerance (tighter than old R5's 0.25)
        term_tolerance=12,    # Moderate term tolerance (looser than old R5's 6)
        require_demographics=True,
        require_current_year_orig=True,
        require_cross_year_for_pt0=True,
        filter_dti=True,
        filter_loan_purpose=True,
    ),
)

# Round 5: Cross-Vintage Tract Bridge (2021/2022 boundary only)
# Identical to Round 3 in spirit but joins on the Census 2010↔2020 tract
# relationship file's translated GEOID, so HMDA <=2021 (2010 vintage) can
# reach FHFA >=2022 (2020 vintage). Skipped for pairs that don't cross the
# vintage boundary. Uses a looser post_unique rate tolerance because bridged
# matches sit in redrawn tracts where key-set ambiguity is higher and rate
# disagreement of up to ~0.125pp is common (see
# investigation_fhfa_hmda_vintage_bridge_2026-05-15).
ROUND_5 = MatchRoundConfig(
    round_number=5,
    description="Cross-vintage bridge at 2021/2022 boundary via Census tract relationship",
    pre_merge=PreMergeFilters(include_pt0=True, include_gse=True, include_secondary=True),
    merge=MergeConfig(use_purchaser=False, same_year_only=False, vintage_bridge=True),
    post_merge=PostMergeFilters(
        require_demographics=True,
        require_current_year_orig=False,
        filter_dti=True,
        require_prior_year_for_gse=True,
        require_cross_year_for_pt0=True,
        filter_loan_purpose=True,
    ),
    post_unique=PostUniqueFilters(rate_tolerance=0.125, term_tolerance=12),
)


# All rounds in execution order
MATCH_ROUNDS = [ROUND_1, ROUND_2, ROUND_3, ROUND_4, ROUND_5]

# Year range for matching
MIN_YEAR = 2018
MAX_YEAR = 2024


# Missing value indicators (used by filters)
HMDA_MISSING_VALUES = {
    "numeric": -99999,
    "string": "",
    "categorical_na": [8888, 9999],
}
