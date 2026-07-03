"""HMDA Seller-Purchaser Matching Tools.

This package provides utilities for building seller-purchaser crosswalks
from Home Mortgage Disclosure Act (HMDA) data, supporting both legacy
(pre-2018) and expanded (post-2018) HMDA file formats.
"""

# Import main configuration from config directory
from .config import (
    HMDA_SILVER_DIR_POST2018,
    HMDA_SILVER_DIR_PRE2018,
    INTERMEDIATE_DIR,
    OUTPUT_DIR,
    # Legacy config (backward compatibility)
    Config,
    SellersAndPurchasersMatchingConfig,
    get_config,
)
from .match_post2018 import (
    POST2018_ROUNDS,
    run_all_rounds,
    run_matching_round,
    run_round,
)

__all__ = [
    # Config classes
    "SellersAndPurchasersMatchingConfig",
    "Config",
    "get_config",
    # Config paths
    "HMDA_SILVER_DIR_POST2018",
    "HMDA_SILVER_DIR_PRE2018",
    "OUTPUT_DIR",
    "INTERMEDIATE_DIR",
    # Workflow functions
    "run_all_rounds",
    "run_matching_round",
    "run_round",
    # Round configurations
    "POST2018_ROUNDS",
]
