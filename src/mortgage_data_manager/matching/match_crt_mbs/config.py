"""Configuration settings for CRT-MBS matching.

This module provides configuration for linking GSE credit-risk-transfer
reference-pool disclosures to the SF loan-level datasets, per GSE.
"""

from __future__ import annotations

from pathlib import Path

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.matching.config import MatchingConfig

logger = get_logger(__name__)


class CRTMBSMatchingConfig(MatchingConfig):
    """Configuration for the CRT-MBS matching workflow."""

    # Matching directories
    CRT_MBS_MATCHING_DIR: Path = MatchingConfig.get_matching_type_dir("crt_mbs")
    CRT_MBS_OUTPUT_DIR: Path = CRT_MBS_MATCHING_DIR / "output"
    CRT_MBS_CROSSWALK_DIR: Path = MatchingConfig.get_crosswalk_type_dir("crt_mbs")

    # Crosswalk outputs (one per GSE)
    FHLMC_CROSSWALK_FILE: Path = CRT_MBS_CROSSWALK_DIR / "crt_mbs_crosswalk_fhlmc.parquet"
    FNMA_CROSSWALK_FILE: Path = CRT_MBS_CROSSWALK_DIR / "crt_mbs_crosswalk_fnma.parquet"

    # Per-deal match summary (diagnostics; sits beside the crosswalk)
    FHLMC_DEAL_SUMMARY_FILE: Path = CRT_MBS_OUTPUT_DIR / "crt_mbs_deal_summary_fhlmc.parquet"

    # UPB confirmation tolerances: a pin is confirmed when the SF performance
    # panel has a month within +/-1 of the CRT Period whose Current Actual UPB
    # equals the CRT UPB at Issuance to the cent. (Measured 2026-07-21: most
    # loans carry a one-month reporting offset, and the cent-exact rate inside
    # the +/-1 window is 100.0%.)
    CONFIRM_MONTH_TOLERANCE: int = 1
    CONFIRM_UPB_TOLERANCE: float = 0.005

    @classmethod
    def get_fhlmc_config(cls):
        """Get the FHLMC config class for CRT / SF data paths."""
        from mortgage_data_manager.sources.fhlmc.config import FHLMCConfig

        return FHLMCConfig

    @classmethod
    def get_fnma_config(cls):
        """Get the FNMA config class for SF loan-performance data paths."""
        from mortgage_data_manager.sources.fnma.config import FNMAConfig

        return FNMAConfig

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary CRT-MBS matching directories if they don't exist."""
        cls.CRT_MBS_MATCHING_DIR.mkdir(parents=True, exist_ok=True)
        cls.CRT_MBS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        cls.CRT_MBS_CROSSWALK_DIR.mkdir(parents=True, exist_ok=True)
