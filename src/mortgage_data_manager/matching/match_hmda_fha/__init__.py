"""HMDA-FHA Matching Workflow.

This module provides functionality for matching FHA (Federal Housing Administration)
mortgage data with HMDA (Home Mortgage Disclosure Act) data to create loan-level
crosswalks. It covers two eras with different keys, because pre-2018 HMDA carries
no interest rate:

- **post2018** (the default era): rate-keyed, the rounds in ``rounds.py``.
- **pre2018** (2010-2017): ZIP + amount@$1k + purpose inside each round's endorsement
  window, the rounds in ``rounds.py``: 1:1 on the base key, then cleared by the
  FHA-lender/HMDA-respondent bridge, then margin tiers on the leftovers.

Example usage:
    >>> from mortgage_data_manager.matching.match_hmda_fha import run_matching
    >>> output_file = run_matching(min_year=2018, max_year=2024)
    >>> from mortgage_data_manager.matching.match_hmda_fha import (
    ...     run_matching_pre2018,
    ... )
    >>> output_file = run_matching_pre2018(min_year=2010, max_year=2017)
"""

from mortgage_data_manager.matching.match_hmda_fha.config import HMDAFHAMatchingConfig
from mortgage_data_manager.matching.match_hmda_fha.matching_post2018 import (
    build_zip_tract_crosswalk,
    get_fha_files,
    get_zip_tract_for_year,
    hmda_tract_vintage,
    prepare_fha,
    prepare_hmda,
    run_matching,
)
from mortgage_data_manager.matching.match_hmda_fha.matching_pre2018 import (
    amount_key,
    endorsement_window,
    hmda_fha_origination_counts,
    prepare_fha_pre2018,
    prepare_hmda_pre2018,
    run_matching_pre2018,
    run_rounds_pre2018,
)
from mortgage_data_manager.matching.match_hmda_fha.rounds import (
    ROUNDS_POST2018,
    ROUNDS_PRE2018,
    Pre2018Round,
    Round,
)
from mortgage_data_manager.matching.match_hmda_fha.validation import (
    VALIDATION_OUTPUT_DIR,
    run_validation,
)

__all__ = [
    "HMDAFHAMatchingConfig",
    "ROUNDS_POST2018",
    "Round",
    "run_matching",
    "prepare_fha",
    "prepare_hmda",
    "get_fha_files",
    "get_zip_tract_for_year",
    "hmda_tract_vintage",
    "build_zip_tract_crosswalk",
    # Pre-2018 era
    "ROUNDS_PRE2018",
    "Pre2018Round",
    "run_matching_pre2018",
    "run_rounds_pre2018",
    "prepare_fha_pre2018",
    "prepare_hmda_pre2018",
    "hmda_fha_origination_counts",
    "amount_key",
    "endorsement_window",
    # Validation
    "run_validation",
    "VALIDATION_OUTPUT_DIR",
]
