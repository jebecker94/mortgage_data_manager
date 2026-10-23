"""Configuration settings for FHFA-MBS matching.

This module provides configuration for matching MBS loan-level data with FHFA
(Federal Housing Finance Agency) data.
"""

from __future__ import annotations

from pathlib import Path

from mortgage_data_manager.matching.config import MatchingConfig
from mortgage_data_manager.sources.fhfa.config import FHFAConfig
from mortgage_data_manager.sources.fhlmc.config import FHLMCConfig
from mortgage_data_manager.sources.fnma.config import FNMAConfig


class FHFAMBSMatchingConfig(MatchingConfig):
    """Configuration for FHFA-MBS matching workflow."""

    # Source data availability (used as CLI request-range defaults). 2018 is matchable: FHFA
    # sf_c and the GSE issuance/origination extracts both cover it (the FNMA preparer normalizes
    # the pre-2019 MSA header). Earlier years need larger schema work and are out of scope.
    FNMA_START_YEAR: int = 2018
    FNMA_END_YEAR: int = 2025
    FHLMC_START_YEAR: int = 2018
    FHLMC_END_YEAR: int = 2025

    # Matching directories
    FHFA_MBS_MATCHING_DIR: Path = MatchingConfig.get_matching_type_dir("fhfa_mbs")
    FHFA_MBS_OUTPUT_DIR: Path = FHFA_MBS_MATCHING_DIR / "output"
    FHFA_MBS_CROSSWALK_DIR: Path = MatchingConfig.get_crosswalk_type_dir("fhfa_mbs")

    @classmethod
    def get_fhlmc_config(cls) -> type[FHLMCConfig]:
        """The FHLMC config class, source of the Freddie loan-level paths."""
        return FHLMCConfig

    @classmethod
    def get_fnma_config(cls) -> type[FNMAConfig]:
        """The FNMA config class, source of the Fannie loan-level paths."""
        return FNMAConfig

    @classmethod
    def get_fhfa_config(cls) -> type[FHFAConfig]:
        """The FHFA config class, source of the ``sf_c`` silver paths."""
        return FHFAConfig

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary FHFA-MBS matching directories if they don't exist."""
        cls.FHFA_MBS_MATCHING_DIR.mkdir(parents=True, exist_ok=True)
        cls.FHFA_MBS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        cls.FHFA_MBS_CROSSWALK_DIR.mkdir(parents=True, exist_ok=True)
