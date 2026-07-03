"""Configuration for FHA-HMDA matching workflow.

This module provides configuration management for the FHA-HMDA matching subpackage,
inheriting from the base MortgageDataConfig and MatchingConfig.
"""

from __future__ import annotations

from pathlib import Path

from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.matching.config import MatchingConfig


class FHAHMDAMatchingConfig(MatchingConfig):
    """FHA-HMDA matching specific configuration."""

    # Input data directories (from other subpackages)
    FHA_SILVER_DIR: Path = Path(
        config(
            "FHA_SILVER_DIR",
            default=str(MortgageDataConfig.get_subpackage_data_dir("fha") / "silver" / "single_family"),
        )
    )
    HMDA_SILVER_DIR: Path = Path(
        config(
            "HMDA_SILVER_DIR",
            default=str(MortgageDataConfig.get_subpackage_data_dir("hmda") / "silver" / "loans" / "post2018"),
        )
    )

    # FHA-HMDA matching output directories
    FHA_HMDA_MATCHING_DIR: Path = MatchingConfig.get_matching_type_dir("fha_hmda")
    FHA_HMDA_OUTPUT_DIR: Path = FHA_HMDA_MATCHING_DIR / "output"
    FHA_HMDA_INTERMEDIATE_DIR: Path = FHA_HMDA_MATCHING_DIR / "intermediate"
    FHA_HMDA_CROSSWALK_DIR: Path = FHA_HMDA_MATCHING_DIR / "crosswalks"

    # Crosswalk output directory (crosswalk/fha_hmda/)
    FHA_HMDA_CROSSWALK_OUTPUT_DIR: Path = MatchingConfig.get_crosswalk_type_dir("fha_hmda")

    # HUD ZIP-Tract crosswalk source (from hud subpackage)
    # Points to the bronze per-quarter parquet that load_crosswalk() reads from.
    # Set HUD_CROSSWALK_DIR environment variable to override.
    HUD_CROSSWALK_DIR: Path = Path(
        config(
            "HUD_CROSSWALK_DIR",
            default=str(MortgageDataConfig.get_subpackage_data_dir("hud") / "bronze"),
        )
    )

    # Year range for matching
    MIN_YEAR: int = int(config("FHA_HMDA_MIN_YEAR", default="2018"))
    MAX_YEAR: int = int(config("FHA_HMDA_MAX_YEAR", default="2025"))

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary FHA-HMDA matching directories if they don't exist."""
        dirs = [
            cls.FHA_HMDA_MATCHING_DIR,
            cls.FHA_HMDA_OUTPUT_DIR,
            cls.FHA_HMDA_INTERMEDIATE_DIR,
            cls.FHA_HMDA_CROSSWALK_DIR,
            cls.FHA_HMDA_CROSSWALK_OUTPUT_DIR,
        ]
        for dir_path in dirs:
            dir_path.mkdir(parents=True, exist_ok=True)


# Module-level exports for backward compatibility
PROJECT_DIR: Path = FHAHMDAMatchingConfig.PROJECT_DIR
DATA_DIR: Path = FHAHMDAMatchingConfig.DATA_DIR
FHA_SILVER_DIR: Path = FHAHMDAMatchingConfig.FHA_SILVER_DIR
HMDA_SILVER_DIR: Path = FHAHMDAMatchingConfig.HMDA_SILVER_DIR
OUTPUT_DIR: Path = FHAHMDAMatchingConfig.FHA_HMDA_OUTPUT_DIR
INTERMEDIATE_DIR: Path = FHAHMDAMatchingConfig.FHA_HMDA_INTERMEDIATE_DIR
CROSSWALK_DIR: Path = FHAHMDAMatchingConfig.FHA_HMDA_CROSSWALK_DIR
CROSSWALK_OUTPUT_DIR: Path = FHAHMDAMatchingConfig.FHA_HMDA_CROSSWALK_OUTPUT_DIR
HUD_CROSSWALK_DIR: Path = FHAHMDAMatchingConfig.HUD_CROSSWALK_DIR
MIN_YEAR: int = FHAHMDAMatchingConfig.MIN_YEAR
MAX_YEAR: int = FHAHMDAMatchingConfig.MAX_YEAR
