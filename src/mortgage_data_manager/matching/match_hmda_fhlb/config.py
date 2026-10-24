"""Configuration settings for HMDA-FHLB matching.

This module provides configuration for matching HMDA (Home Mortgage Disclosure Act)
data with FHLB (Federal Home Loan Bank) AMA data.
"""

from __future__ import annotations

from pathlib import Path

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.reference.avery import avery_file_path
from mortgage_data_manager.matching.config import MatchingConfig

logger = get_logger(__name__)


class HMDAFHLBMatchingConfig(MatchingConfig):
    """Configuration class for HMDA-FHLB matching workflow."""

    # Matching directories
    HMDA_FHLB_MATCHING_DIR: Path = MatchingConfig.get_matching_type_dir("hmda_fhlb")
    HMDA_FHLB_CROSSWALK_DIR: Path = MatchingConfig.get_crosswalk_type_dir("hmda_fhlb")

    # Input files: the Philadelphia Fed's HMDA Lender File (Avery), reference data under
    # REFERENCE_DIR (see core.reference.avery).
    AVERY_FILE_PATH: Path = avery_file_path("post2018")
    AVERY_PRE2018_FILE_PATH: Path = avery_file_path("pre2018")

    # Dependency checks
    _HAS_DATA_MANAGERS: bool = False
    _hmda_config = None
    _fhlb_config = None

    @classmethod
    def _check_dependencies(cls) -> None:
        """Check if HMDA and FHLB configs are available and cache them."""
        if cls._hmda_config is None and cls._fhlb_config is None:
            try:
                from mortgage_data_manager.sources.fhlb import config as fhlb_conf
                from mortgage_data_manager.sources.hmda import config as hmda_conf

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
            ImportError: If mortgage_data_manager.sources.hmda is not available.
        """
        cls._check_dependencies()
        if not cls._HAS_DATA_MANAGERS:
            raise ImportError(
                "mortgage_data_manager.sources.hmda not found. Cannot access HMDA data paths."
            )
        return cls._hmda_config

    @classmethod
    def get_fhlb_config(cls):
        """Get FHLB configuration module.

        Returns:
            FHLB config module with FHLBConfig class and other utilities.

        Raises:
            ImportError: If mortgage_data_manager.sources.fhlb is not available.
        """
        cls._check_dependencies()
        if not cls._HAS_DATA_MANAGERS:
            raise ImportError(
                "mortgage_data_manager.sources.fhlb not found. Cannot access FHLB data paths."
            )
        return cls._fhlb_config

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary HMDA-FHLB matching directories if they don't exist."""
        cls.HMDA_FHLB_MATCHING_DIR.mkdir(parents=True, exist_ok=True)
        cls.HMDA_FHLB_CROSSWALK_DIR.mkdir(parents=True, exist_ok=True)


#: HMDA columns kept by both post-2018 HMDA loaders (FHLB members, and every originator for
#: round 3). ``HMDAIndex`` is the native string ID, preserved for stable crosswalk keys.
HMDA_POST2018_COLS: list[str] = [
    "HMDAIndex",
    "activity_year",
    "lei",
    "census_tract",
    "loan_amount",
    "loan_type",
    "total_units",
    "occupancy_type",
    "loan_purpose",
    "income",
    "loan_term",
    "interest_rate",
    "applicant_sex",
    "co_applicant_sex",
    "combined_loan_to_value_ratio",
    "debt_to_income_ratio",
    "purchaser_type",
]


# Check dependencies on import
HMDAFHLBMatchingConfig._check_dependencies()
