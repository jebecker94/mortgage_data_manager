"""Configuration settings for FHLB-MBS matching.

This module provides configuration for matching MBS (GNMA) loan-level data
with FHLB AMA (Acquired Member Assets) data.
"""

from __future__ import annotations

from pathlib import Path

from mortgage_data_manager.matching.config import MatchingConfig
from mortgage_data_manager.sources.fhlb.config import FHLBConfig
from mortgage_data_manager.sources.gnma.config import GNMAConfig


class FHLBMBSMatchingConfig(MatchingConfig):
    """Configuration for FHLB-MBS matching workflow."""

    # Source data availability (used as CLI request-range defaults)
    GNMA_START_YEAR: int = 2009
    GNMA_END_YEAR: int = 2025

    # Matching directories
    FHLB_MBS_MATCHING_DIR: Path = MatchingConfig.get_matching_type_dir("fhlb_mbs")
    FHLB_MBS_INTERMEDIATE_DIR: Path = FHLB_MBS_MATCHING_DIR / "intermediate"
    FHLB_MBS_OUTPUT_DIR: Path = FHLB_MBS_MATCHING_DIR / "output"
    FHLB_MBS_CROSSWALK_DIR: Path = MatchingConfig.get_crosswalk_type_dir("fhlb_mbs")

    @classmethod
    def get_fhlb_config(cls) -> type[FHLBConfig]:
        """The FHLB config class, source of the AMA paths."""
        return FHLBConfig

    @classmethod
    def get_gnma_silver_dir(cls) -> Path:
        """The GNMA silver dailyllmni loan-level (``L``) directory."""
        return GNMAConfig.GNMA_SILVER_DIR / "dailyllmni" / "L"

    @classmethod
    def get_fhlb_ama_silver_dir(cls) -> Path:
        """The FHLB AMA silver directory."""
        return FHLBConfig.FHLB_SILVER_DIR / "ama"

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary FHLB-MBS matching directories if they don't exist."""
        cls.FHLB_MBS_MATCHING_DIR.mkdir(parents=True, exist_ok=True)
        cls.FHLB_MBS_INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
        cls.FHLB_MBS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        cls.FHLB_MBS_CROSSWALK_DIR.mkdir(parents=True, exist_ok=True)
