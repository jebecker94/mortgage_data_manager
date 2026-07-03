"""FHA-GNMA Matching Workflow.

This module provides functionality for matching FHA (Federal Housing Administration)
endorsement records with GNMA (Ginnie Mae) loan-level disclosure data to create
loan-level crosswalks.

Since GNMA data lacks FHA Case Numbers, matching relies on loan characteristics:
- State
- Origination year and month
- Loan amount (with truncation handling - GNMA truncates to thousands)
- Interest rate
- Loan purpose (Purchase vs Refinance)

The matching process involves:
1. Preparing FHA endorsement data with standardized variables
2. Preparing GNMA dailyllmni data (filtered to FHA loans, Agency='F')
3. Multi-round matching:
   - Round 1: Strict tolerances for high-confidence matches
   - Round 2: Relaxed tolerances on remaining unmatched records
4. Mutual best-match selection to ensure 1:1 matching

Example usage:
    >>> from mortgage_data_manager.matching.match_fha_gnma import run_fha_gnma_matching
    >>> output_file = run_fha_gnma_matching(min_year=2015, max_year=2024)

Key findings:
- Match rates vary dramatically by state size (correlation r = -0.907)
- Smaller states achieve 80%+ match rates; large states like TX achieve ~15%
- Overall: 35.8% FHA match rate, 33.7% GNMA match rate (2015-2024)
"""

from .config import (
    FHA_SILVER_DIR,
    GNMA_SILVER_DIR,
    INTERMEDIATE_DIR,
    MAX_YEAR,
    MIN_YEAR,
    OUTPUT_DIR,
    PILOT_STATE,
    ROUND1_TOLERANCES,
    ROUND2_TOLERANCES,
    FHAGNMAMatchingConfig,
    MatchingTolerances,
    ensure_directories,
)
from .matching import (
    create_crosswalk,
    match_fha_gnma_round,
    run_multi_round_matching,
    run_multi_round_matching_by_year,
    save_match_details,
)
from .prepare_fha import (
    load_fha_silver_data,
    prepare_fha_for_matching,
    run_fha_preparation,
)
from .prepare_gnma import (
    load_gnma_silver_data,
    prepare_gnma_for_matching,
    run_gnma_preparation,
)
from .run_pipeline import run_fha_gnma_matching, validate_matches
from .validation import plot_match_rate_by_state, run_validation

__all__ = [
    # Config
    "FHAGNMAMatchingConfig",
    "MatchingTolerances",
    "ROUND1_TOLERANCES",
    "ROUND2_TOLERANCES",
    "FHA_SILVER_DIR",
    "GNMA_SILVER_DIR",
    "OUTPUT_DIR",
    "INTERMEDIATE_DIR",
    "MIN_YEAR",
    "MAX_YEAR",
    "PILOT_STATE",
    "ensure_directories",
    # FHA preparation
    "load_fha_silver_data",
    "prepare_fha_for_matching",
    "run_fha_preparation",
    # GNMA preparation
    "load_gnma_silver_data",
    "prepare_gnma_for_matching",
    "run_gnma_preparation",
    # Matching
    "match_fha_gnma_round",
    "run_multi_round_matching",
    "run_multi_round_matching_by_year",
    "create_crosswalk",
    "save_match_details",
    # Pipeline
    "run_fha_gnma_matching",
    "validate_matches",
    # Validation
    "plot_match_rate_by_state",
    "run_validation",
]
