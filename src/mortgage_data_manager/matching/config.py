"""Matching workflows configuration.

This module provides configuration management for the Matching subpackage,
inheriting from the base MortgageDataConfig.
"""

from __future__ import annotations

from pathlib import Path

from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig


class MatchingConfig(MortgageDataConfig):
    """Matching-specific configuration, inheriting from MortgageDataConfig."""

    # Matching data directories
    MATCHING_DATA_DIR: Path = Path(
        config(
            "MATCHING_DATA_DIR",
            default=str(MortgageDataConfig.get_subpackage_data_dir("matching")),
        )
    )
    MATCHING_CACHE_DIR: Path = Path(
        config("MATCHING_CACHE_DIR", default=str(MATCHING_DATA_DIR / "cache"))
    )

    @classmethod
    def get_matching_type_dir(cls, matching_type: str) -> Path:
        """Get data directory for a specific matching workflow.

        Args:
            matching_type: Type of matching (e.g., "hmda_mbs", "fha_hmda")

        Returns:
            Path to matching type directory
        """
        return cls.MATCHING_DATA_DIR / matching_type

    @classmethod
    def get_crosswalk_type_dir(cls, matching_type: str) -> Path:
        """Get crosswalk output directory for a specific matching workflow.

        Args:
            matching_type: Type of matching (e.g., "hmda_mbs", "fha_hmda")

        Returns:
            Path to crosswalk output directory (crosswalk/{matching_type}/)
        """
        return cls.CROSSWALK_DIR / matching_type

    @classmethod
    def ensure_directories(cls, matching_type: str | None = None) -> None:
        """Create necessary matching directories if they don't exist.

        Args:
            matching_type: Optional matching type. If provided, only create that type's dirs.
        """
        cls.MATCHING_CACHE_DIR.mkdir(parents=True, exist_ok=True)

        if matching_type:
            type_dir = cls.get_matching_type_dir(matching_type)
            type_dir.mkdir(parents=True, exist_ok=True)
            crosswalk_dir = cls.get_crosswalk_type_dir(matching_type)
            crosswalk_dir.mkdir(parents=True, exist_ok=True)


# Module-level exports for backward compatibility
PROJECT_DIR: Path = MatchingConfig.PROJECT_DIR
DATA_DIR: Path = MatchingConfig.MATCHING_DATA_DIR
CACHE_DIR: Path = MatchingConfig.MATCHING_CACHE_DIR
CROSSWALK_DIR: Path = MatchingConfig.CROSSWALK_DIR
