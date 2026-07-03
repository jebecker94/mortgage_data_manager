"""Configuration for FHFA-HMDA matching workflow.

This module provides configuration management for the FHFA-HMDA matching
workflow, inheriting from the base MatchingConfig.
"""

from __future__ import annotations

from pathlib import Path

from decouple import config

from mortgage_data_manager.matching.config import MatchingConfig

# Re-export core config classes for convenience
from .round_config import (
    HMDA_MISSING_VALUES,
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


class FHFAHMDAMatchingConfig(MatchingConfig):
    """FHFA-HMDA matching configuration, inheriting from MatchingConfig."""

    # FHFA-HMDA specific data directory
    FHFA_HMDA_MATCHING_DIR: Path = Path(
        config(
            "FHFA_HMDA_MATCHING_DIR",
            default=str(MatchingConfig.get_matching_type_dir("fhfa_hmda")),
        )
    )

    # Output directories for matching results
    FHFA_HMDA_OUTPUT_DIR: Path = Path(
        config(
            "FHFA_HMDA_OUTPUT_DIR",
            default=str(FHFA_HMDA_MATCHING_DIR / "output"),
        )
    )

    # Crosswalk output directory (crosswalk/fhfa_hmda/)
    FHFA_HMDA_CROSSWALK_DIR: Path = Path(
        config(
            "FHFA_HMDA_CROSSWALK_DIR",
            default=str(MatchingConfig.get_crosswalk_type_dir("fhfa_hmda")),
        )
    )

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary FHFA-HMDA matching directories if they don't exist."""
        for d in [cls.FHFA_HMDA_MATCHING_DIR, cls.FHFA_HMDA_OUTPUT_DIR, cls.FHFA_HMDA_CROSSWALK_DIR]:
            d.mkdir(parents=True, exist_ok=True)


__all__ = [
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
    "HMDA_MISSING_VALUES",
]
