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
            matching_type: Type of matching (e.g., "hmda_gse", "hmda_fha")

        Returns:
            Path to matching type directory
        """
        return cls.MATCHING_DATA_DIR / matching_type

    @classmethod
    def get_crosswalk_type_dir(cls, matching_type: str) -> Path:
        """Get crosswalk output directory for a specific matching workflow.

        Args:
            matching_type: Type of matching (e.g., "hmda_gse", "hmda_fha")

        Returns:
            Path to crosswalk output directory (crosswalk/{matching_type}/)
        """
        return cls.CROSSWALK_DIR / matching_type

    @classmethod
    def ensure_directories(cls) -> None:
        """Create the shared matching cache directory if it doesn't exist."""
        cls.MATCHING_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def ensure_matching_type_dirs(cls, matching_type: str) -> None:
        """Create one workflow's intermediate and crosswalk directories.

        Args:
            matching_type: Workflow identifier (the ``matching/{workflow}`` and
                ``crosswalk/{workflow}`` directory name).
        """
        cls.ensure_directories()
        cls.get_matching_type_dir(matching_type).mkdir(parents=True, exist_ok=True)
        cls.get_crosswalk_type_dir(matching_type).mkdir(parents=True, exist_ok=True)
