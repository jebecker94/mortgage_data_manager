"""FHA-HMDA Matching Workflow.

This module provides functionality for matching FHA (Federal Housing Administration)
mortgage data with HMDA (Home Mortgage Disclosure Act) data to create loan-level
crosswalks.

The matching process involves:
1. Preparing FHA data with standardized variables
2. Preparing HMDA data with ZIP-tract crosswalk
3. Round 1 matching (strict criteria)
4. Round 2 matching (looser criteria for remaining loans)

Example usage:
    >>> from mortgage_data_manager.matching.match_fha_hmda import run_fha_hmda_matching
    >>> output_file = run_fha_hmda_matching(min_year=2018, max_year=2024)
"""

from .config import FHAHMDAMatchingConfig
from .match_hmda_fha_post2018 import (
    ROUND1_CONFIG,
    ROUND2_CONFIG,
    MatchConfig,
    build_zip_tract_crosswalk,
    combine_crosswalks,
    get_crosswalk_for_year,
    get_fha_files,
    get_hmda_files,
    match_fha_hmda,
    prepare_fha_merge_data,
    prepare_hmda_merge_data,
    run_fha_hmda_matching,
    run_matching,
)
from .validation import (
    VALIDATION_OUTPUT_DIR,
    run_validation,
)

__all__ = [
    "FHAHMDAMatchingConfig",
    "MatchConfig",
    "ROUND1_CONFIG",
    "ROUND2_CONFIG",
    "run_fha_hmda_matching",
    "run_matching",
    "match_fha_hmda",
    "prepare_fha_merge_data",
    "prepare_hmda_merge_data",
    "combine_crosswalks",
    "get_hmda_files",
    "get_fha_files",
    "get_crosswalk_for_year",
    "build_zip_tract_crosswalk",
    # Validation
    "run_validation",
    "VALIDATION_OUTPUT_DIR",
]
