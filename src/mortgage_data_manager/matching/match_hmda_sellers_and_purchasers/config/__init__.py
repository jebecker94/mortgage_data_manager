"""Configuration management for HMDA Sellers/Purchasers matching.

This module provides configuration management for the HMDA Sellers/Purchasers
matching subpackage, including:

- Directory and path configuration (SellersAndPurchasersMatchingConfig)
- Round configuration dataclasses (RoundConfig, ToleranceConfig, etc.)
- Pre-defined round configurations for post-2018 matching
"""

# Directory and path configuration
# Pre-defined round configurations
from .post2018_rounds import (
    POST2018_ROUNDS,
    ROUND_1,
    ROUND_2,
    ROUND_3,
    ROUND_4,
    ROUND_5,
    ROUND_6,
    ROUND_7,
    ROUND_8,
)

# Round configuration dataclasses
from .round_config import (
    DataFilter,
    MatchColumnsConfig,
    RoundConfig,
    SpecialConstraints,
    ToleranceConfig,
)
from .settings import (
    CROSSWALK_OUTPUT_DIR,
    HMDA_SILVER_DIR_POST2018,
    HMDA_SILVER_DIR_PRE2018,
    INTERMEDIATE_DIR,
    MAX_YEAR_POST2018,
    MAX_YEAR_PRE2018,
    MIN_YEAR_POST2018,
    MIN_YEAR_PRE2018,
    OUTPUT_DIR,
    # Module-level exports
    PROJECT_DIR,
    # Legacy config (backward compatibility)
    Config,
    # New config class
    SellersAndPurchasersMatchingConfig,
    get_config,
)

__all__ = [
    # Directory configuration class
    "SellersAndPurchasersMatchingConfig",
    # Module-level path exports
    "PROJECT_DIR",
    "HMDA_SILVER_DIR_POST2018",
    "HMDA_SILVER_DIR_PRE2018",
    "OUTPUT_DIR",
    "CROSSWALK_OUTPUT_DIR",
    "INTERMEDIATE_DIR",
    "MIN_YEAR_POST2018",
    "MAX_YEAR_POST2018",
    "MIN_YEAR_PRE2018",
    "MAX_YEAR_PRE2018",
    # Legacy config
    "Config",
    "get_config",
    # Round configuration dataclasses
    "DataFilter",
    "MatchColumnsConfig",
    "RoundConfig",
    "SpecialConstraints",
    "ToleranceConfig",
    # Pre-defined round configurations
    "POST2018_ROUNDS",
    "ROUND_1",
    "ROUND_2",
    "ROUND_3",
    "ROUND_4",
    "ROUND_5",
    "ROUND_6",
    "ROUND_7",
    "ROUND_8",
]
