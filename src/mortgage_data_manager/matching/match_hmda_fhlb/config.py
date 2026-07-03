"""Configuration settings for HMDA-FHLB matching.

This module provides configuration for matching HMDA (Home Mortgage Disclosure Act)
data with FHLB (Federal Home Loan Bank) AMA data.
"""

from __future__ import annotations

from pathlib import Path

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.matching.config import MatchingConfig

logger = get_logger(__name__)


class MatchHMDAFHLBConfig(MatchingConfig):
    """Configuration class for HMDA-FHLB matching workflow."""

    # Matching directories
    HMDA_FHLB_MATCHING_DIR: Path = MatchingConfig.get_matching_type_dir("hmda_fhlb")
    HMDA_FHLB_CROSSWALK_OUTPUT_DIR: Path = MatchingConfig.get_crosswalk_type_dir("hmda_fhlb")

    # Input Files - Avery lender panel files
    AVERY_FILE_PATH: Path = HMDA_FHLB_MATCHING_DIR / "hmda-2018-present.xlsx"
    AVERY_PRE2018_FILE_PATH: Path = HMDA_FHLB_MATCHING_DIR / "hmda-1990-2017.xlsx"

    # Matching Thresholds
    MATCH_SETTINGS: dict = {
        "income_tol": {
            "round1": 2500,
            "round2": 5000,
            "pre2018": 5000
        },
        "ltv_tol": {
            "round1": 1.05,
            "round2": 2.05
        },
        "dti_tol": {
            "round1": 1,
            "round2": 2
        },
        "rate_tol": 0.0625,
        "term_tol": 12,
        "amount_tol": 15000  # Round 2 only
    }

    # Dependency checks
    _HAS_DATA_MANAGERS: bool = False
    _hmda_config = None
    _fhlb_config = None

    @classmethod
    def _check_dependencies(cls) -> None:
        """Check if HMDA and FHLB configs are available and cache them."""
        if cls._hmda_config is None and cls._fhlb_config is None:
            try:
                from mortgage_data_manager.fhlb import config as fhlb_conf
                from mortgage_data_manager.hmda import config as hmda_conf

                cls._hmda_config = hmda_conf
                cls._fhlb_config = fhlb_conf
                cls._HAS_DATA_MANAGERS = True
            except ImportError:
                cls._HAS_DATA_MANAGERS = False
                logger.warning("Data managers (hmda, fhlb) not found. Cleaning steps will fail")

    @classmethod
    def get_hmda_config(cls):
        """Get HMDA configuration module.

        Returns:
            HMDA config module with HMDAConfig class and other utilities.

        Raises:
            ImportError: If mortgage_data_manager.hmda is not available.
        """
        cls._check_dependencies()
        if not cls._HAS_DATA_MANAGERS:
            raise ImportError(
                "mortgage_data_manager.hmda not found. Cannot access HMDA data paths."
            )
        return cls._hmda_config

    @classmethod
    def get_fhlb_config(cls):
        """Get FHLB configuration module.

        Returns:
            FHLB config module with FHLBConfig class and other utilities.

        Raises:
            ImportError: If mortgage_data_manager.fhlb is not available.
        """
        cls._check_dependencies()
        if not cls._HAS_DATA_MANAGERS:
            raise ImportError(
                "mortgage_data_manager.fhlb not found. Cannot access FHLB data paths."
            )
        return cls._fhlb_config

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary HMDA-FHLB matching directories if they don't exist."""
        cls.HMDA_FHLB_MATCHING_DIR.mkdir(parents=True, exist_ok=True)
        cls.HMDA_FHLB_CROSSWALK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# Backward compatibility exports
PROJECT_ROOT: Path = MatchHMDAFHLBConfig.PROJECT_DIR
DATA_DIR: Path = MatchHMDAFHLBConfig.HMDA_FHLB_MATCHING_DIR
AVERY_FILE_PATH: Path = MatchHMDAFHLBConfig.AVERY_FILE_PATH
AVERY_PRE2018_FILE_PATH: Path = MatchHMDAFHLBConfig.AVERY_PRE2018_FILE_PATH
MATCH_SETTINGS: dict = MatchHMDAFHLBConfig.MATCH_SETTINGS
CROSSWALK_OUTPUT_DIR: Path = MatchHMDAFHLBConfig.HMDA_FHLB_CROSSWALK_OUTPUT_DIR


# Backward compatibility for dependency checks
def get_hmda_manager_config():
    """Get HMDA configuration module.

    Deprecated: Use MatchHMDAFHLBConfig.get_hmda_config() instead.
    """
    return MatchHMDAFHLBConfig.get_hmda_config()


def get_fhlb_manager_config():
    """Get FHLB configuration module.

    Deprecated: Use MatchHMDAFHLBConfig.get_fhlb_config() instead.
    """
    return MatchHMDAFHLBConfig.get_fhlb_config()


# Check dependencies on import
MatchHMDAFHLBConfig._check_dependencies()
HAS_DATA_MANAGERS = MatchHMDAFHLBConfig._HAS_DATA_MANAGERS
