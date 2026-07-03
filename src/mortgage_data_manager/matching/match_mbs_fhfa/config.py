"""Configuration settings for MBS-FHFA matching.

This module provides configuration for matching MBS loan-level data with FHFA
(Federal Housing Finance Agency) data.
"""

from __future__ import annotations

from pathlib import Path

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.matching.config import MatchingConfig

logger = get_logger(__name__)


class MBSFHFAConfig(MatchingConfig):
    """Configuration for MBS-FHFA matching workflow."""

    # Source data availability (used as CLI request-range defaults). 2018 is matchable: FHFA
    # sf_c and the GSE issuance/origination extracts both cover it (the FNMA preparer normalizes
    # the pre-2019 MSA header). Earlier years need larger schema work and are out of scope.
    FNMA_START_YEAR: int = 2018
    FNMA_END_YEAR: int = 2024
    FHLMC_START_YEAR: int = 2018
    FHLMC_END_YEAR: int = 2024

    # Matching directories
    MBS_FHFA_MATCHING_DIR: Path = MatchingConfig.get_matching_type_dir("mbs_fhfa")
    MBS_FHFA_OUTPUT_DIR: Path = MBS_FHFA_MATCHING_DIR / "output"
    MBS_FHFA_CROSSWALK_OUTPUT_DIR: Path = MatchingConfig.get_crosswalk_type_dir("mbs_fhfa")

    # Dependency checks
    _HAS_FHLMC: bool = False
    _HAS_FNMA: bool = False
    _HAS_FHFA: bool = False
    _fhlmc_config = None
    _fnma_config = None
    _fhfa_config = None

    @classmethod
    def _check_fhlmc(cls) -> None:
        """Check if FHLMC config is available and cache it."""
        if cls._fhlmc_config is None:
            try:
                from mortgage_data_manager.fhlmc import config as fhlmc_conf

                cls._fhlmc_config = fhlmc_conf.FHLMCConfig
                cls._HAS_FHLMC = True
            except ImportError:
                cls._HAS_FHLMC = False
                logger.warning(
                    "mortgage_data_manager.fhlmc not found. FHLMC data paths unavailable."
                )

    @classmethod
    def _check_fnma(cls) -> None:
        """Check if FNMA config is available and cache it."""
        if cls._fnma_config is None:
            try:
                from mortgage_data_manager.fnma import config as fnma_conf

                cls._fnma_config = fnma_conf.FNMAConfig
                cls._HAS_FNMA = True
            except ImportError:
                cls._HAS_FNMA = False
                logger.warning(
                    "mortgage_data_manager.fnma config not found. FNMA data paths unavailable."
                )

    @classmethod
    def _check_fhfa(cls) -> None:
        """Check if FHFA config is available and cache it."""
        if cls._fhfa_config is None:
            try:
                from mortgage_data_manager.fhfa import config as fhfa_conf

                cls._fhfa_config = fhfa_conf.FHFAConfig
                cls._HAS_FHFA = True
            except ImportError:
                cls._HAS_FHFA = False
                logger.warning(
                    "mortgage_data_manager.fhfa config not found. FHFA data paths unavailable."
                )

    @classmethod
    def get_fhlmc_config(cls):
        """Get FHLMC config class for data paths.

        Returns:
            FHLMCConfig class with path constants.

        Raises:
            ImportError: If mortgage_data_manager.fhlmc is not available.
        """
        cls._check_fhlmc()
        if not cls._HAS_FHLMC:
            raise ImportError(
                "mortgage_data_manager.fhlmc not found. Cannot access FHLMC data paths."
            )
        return cls._fhlmc_config

    @classmethod
    def get_fnma_config(cls):
        """Get FNMA config class for data paths.

        Returns:
            FNMAConfig class with path constants.

        Raises:
            ImportError: If mortgage_data_manager.fnma is not available.
        """
        cls._check_fnma()
        if not cls._HAS_FNMA:
            raise ImportError(
                "mortgage_data_manager.fnma config not found. Cannot access FNMA data paths."
            )
        return cls._fnma_config

    @classmethod
    def get_fhfa_config(cls):
        """Get FHFA config class for data paths.

        Returns:
            FHFAConfig class with path constants.

        Raises:
            ImportError: If mortgage_data_manager.fhfa is not available.
        """
        cls._check_fhfa()
        if not cls._HAS_FHFA:
            raise ImportError(
                "mortgage_data_manager.fhfa config not found. Cannot access FHFA data paths."
            )
        return cls._fhfa_config

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary MBS-FHFA matching directories if they don't exist."""
        cls.MBS_FHFA_MATCHING_DIR.mkdir(parents=True, exist_ok=True)
        cls.MBS_FHFA_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        cls.MBS_FHFA_CROSSWALK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# Backward compatibility exports
PROJECT_ROOT: Path = MBSFHFAConfig.PROJECT_DIR
DATA_DIR: Path = MBSFHFAConfig.MBS_FHFA_MATCHING_DIR
OUTPUT_DIR: Path = MBSFHFAConfig.MBS_FHFA_OUTPUT_DIR
CROSSWALK_OUTPUT_DIR: Path = MBSFHFAConfig.MBS_FHFA_CROSSWALK_OUTPUT_DIR


# Backward compatibility for dependency checks
def get_fhlmc_config():
    """Get FHLMC config for data paths.

    Deprecated: Use MBSFHFAConfig.get_fhlmc_config() instead.
    """
    return MBSFHFAConfig.get_fhlmc_config()


def get_fnma_config():
    """Get FNMA config for data paths.

    Deprecated: Use MBSFHFAConfig.get_fnma_config() instead.
    """
    return MBSFHFAConfig.get_fnma_config()


def get_fhfa_config():
    """Get FHFA config for data paths.

    Deprecated: Use MBSFHFAConfig.get_fhfa_config() instead.
    """
    return MBSFHFAConfig.get_fhfa_config()


# Check dependencies on import
MBSFHFAConfig._check_fhlmc()
MBSFHFAConfig._check_fnma()
MBSFHFAConfig._check_fhfa()
HAS_FHLMC = MBSFHFAConfig._HAS_FHLMC
HAS_FNMA = MBSFHFAConfig._HAS_FNMA
HAS_FHFA = MBSFHFAConfig._HAS_FHFA
