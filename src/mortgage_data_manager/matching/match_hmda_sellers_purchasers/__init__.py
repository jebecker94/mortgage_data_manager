"""HMDA Seller-Purchaser Matching Tools.

This package provides utilities for building seller-purchaser crosswalks
from Home Mortgage Disclosure Act (HMDA) data, supporting both legacy
(pre-2018) and expanded (post-2018) HMDA file formats.
"""

from mortgage_data_manager.matching.match_hmda_sellers_purchasers.config import (
    HMDASellersPurchasersMatchingConfig,
)
from mortgage_data_manager.matching.match_hmda_sellers_purchasers.matching_post2018 import (
    run_matching,
    run_round,
)
from mortgage_data_manager.matching.match_hmda_sellers_purchasers.matching_pre2018 import (
    run_matching_pre2018,
    run_round_pre2018,
)
from mortgage_data_manager.matching.match_hmda_sellers_purchasers.rounds import (
    ROUNDS_POST2018,
    ROUNDS_PRE2018,
)

__all__ = [
    # Config classes
    "HMDASellersPurchasersMatchingConfig",
    # Workflow functions
    "run_matching",
    "run_matching_pre2018",
    "run_round",
    "run_round_pre2018",
    # Round configurations
    "ROUNDS_POST2018",
    "ROUNDS_PRE2018",
]
