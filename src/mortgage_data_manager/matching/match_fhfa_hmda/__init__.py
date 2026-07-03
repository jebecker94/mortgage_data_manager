"""FHFA-HMDA matching workflow.

This module provides functionality for matching HMDA loan applications
to FHFA (Federal Housing Finance Agency) GSE loan data.

Config-driven five-round matching strategy (post-2018):
    - Round 1: Census tract + purchaser + demographics + DTI + loan purpose (same year, pt 1,3)
    - Round 2: Census tract + DTI + loan purpose, all PT (same year, no demographics)
    - Round 3: Cross-year + DTI, all PT + moderate quality filter
    - Round 4: No rate match + moderate filters (same year)
    - Round 5: Cross-vintage tract bridge for HMDA<=2021 ↔ FHFA>=2022 (2010↔2020 Census redraw)

Configuration Dataclasses:
    - PreMergeFilters: Controls HMDA pool filtering (purchaser types)
    - MergeConfig: Controls join keys and cross-year behavior
    - PostMergeFilters: Controls rate/term/demographic filters
    - PostUniqueFilters: Optional tighter tolerances after 1:1 constraint
    - MatchRoundConfig: Complete round configuration

Main Classes:
    - FHFAHMDAMatcher: Config-driven matching orchestrator
    - MatchResult: Result dataclass with matched data and statistics
    - FHFAHMDAMatchingConfig: Configuration class for paths and settings

Round Definitions:
    - ROUND_1, ROUND_2, ROUND_3, ROUND_4, ROUND_5: Pre-configured round objects
    - MATCH_ROUNDS: List of all rounds in execution order
"""

from .config import FHFAHMDAMatchingConfig
from .match_post2018 import (
    FHFAHMDAMatcher,
    MatchResult,
    run_matching,
)
from .round_config import (
    MATCH_ROUNDS,
    MAX_YEAR,
    MIN_YEAR,
    ROUND_1,
    ROUND_2,
    ROUND_3,
    ROUND_4,
    ROUND_5,
    MatchRoundConfig,
    MergeConfig,
    PostMergeFilters,
    PostUniqueFilters,
    PreMergeFilters,
)

__all__ = [
    # Config class
    "FHFAHMDAMatchingConfig",
    # Dataclasses
    "PreMergeFilters",
    "MergeConfig",
    "PostMergeFilters",
    "PostUniqueFilters",
    "MatchRoundConfig",
    # Round definitions
    "MATCH_ROUNDS",
    "ROUND_1",
    "ROUND_2",
    "ROUND_3",
    "ROUND_4",
    "ROUND_5",
    # Constants
    "MIN_YEAR",
    "MAX_YEAR",
    # Main classes
    "FHFAHMDAMatcher",
    "MatchResult",
    # Functions
    "run_matching",
]
