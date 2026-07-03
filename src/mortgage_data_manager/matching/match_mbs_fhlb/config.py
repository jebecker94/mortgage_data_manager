"""Configuration settings for MBS-FHLB matching.

This module provides configuration for matching MBS (GNMA, FNMA) loan-level data
with FHLB AMA (Acquired Member Assets) data.
"""

from __future__ import annotations

from pathlib import Path

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.matching.config import MatchingConfig

logger = get_logger(__name__)


class MBSFHLBConfig(MatchingConfig):
    """Configuration for MBS-FHLB matching workflow."""

    # Source data availability (used as CLI request-range defaults)
    GNMA_START_YEAR: int = 2009
    GNMA_END_YEAR: int = 2024
    FNMA_START_YEAR: int = 2019
    FNMA_END_YEAR: int = 2024

    # Matching directories
    MBS_FHLB_MATCHING_DIR: Path = MatchingConfig.get_matching_type_dir("mbs_fhlb")
    MBS_FHLB_INTERMEDIATE_DIR: Path = MBS_FHLB_MATCHING_DIR / "intermediate"
    MBS_FHLB_OUTPUT_DIR: Path = MBS_FHLB_MATCHING_DIR / "output"
    MBS_FHLB_CROSSWALK_OUTPUT_DIR: Path = MatchingConfig.get_crosswalk_type_dir("mbs_fhlb")

    # Dependency checks
    _HAS_GNMA: bool = False
    _HAS_FNMA: bool = False
    _HAS_UMBS: bool = False
    _HAS_FHLB: bool = False
    _gnma_config = None
    _fnma_config = None
    _umbs_config = None
    _fhlb_config = None

    @classmethod
    def _check_gnma(cls) -> None:
        """Check if GNMA config is available and cache it."""
        if cls._gnma_config is None:
            try:
                from mortgage_data_manager.gnma.config import GNMAConfig

                cls._gnma_config = GNMAConfig
                cls._HAS_GNMA = True
            except ImportError:
                cls._HAS_GNMA = False
                logger.warning("mortgage_data_manager.gnma not found. GNMA data paths unavailable")

    @classmethod
    def _check_fnma(cls) -> None:
        """Check if FNMA config is available and cache it."""
        if cls._fnma_config is None:
            try:
                from mortgage_data_manager.fnma.config import FNMAConfig

                cls._fnma_config = FNMAConfig
                cls._HAS_FNMA = True
            except ImportError:
                cls._HAS_FNMA = False
                logger.warning(
                    "mortgage_data_manager.fnma config not found. FNMA data paths unavailable"
                )

    @classmethod
    def _check_umbs(cls) -> None:
        """Check if UMBS config is available and cache it."""
        if cls._umbs_config is None:
            try:
                from mortgage_data_manager.umbs.config import UMBSConfig

                cls._umbs_config = UMBSConfig
                cls._HAS_UMBS = True
            except ImportError:
                cls._HAS_UMBS = False
                logger.warning(
                    "mortgage_data_manager.umbs config not found. UMBS data paths unavailable"
                )

    @classmethod
    def _check_fhlb(cls) -> None:
        """Check if FHLB config is available and cache it."""
        if cls._fhlb_config is None:
            try:
                from mortgage_data_manager.fhlb.config import FHLBConfig

                cls._fhlb_config = FHLBConfig
                cls._HAS_FHLB = True
            except ImportError:
                cls._HAS_FHLB = False
                logger.warning("mortgage_data_manager.fhlb not found. FHLB data paths unavailable")

    @classmethod
    def get_gnma_config(cls):
        """Get GNMA config class for data paths.

        Returns:
            GNMAConfig class with path constants.

        Raises:
            ImportError: If mortgage_data_manager.gnma is not available.
        """
        cls._check_gnma()
        if not cls._HAS_GNMA:
            raise ImportError(
                "mortgage_data_manager.gnma not found. Cannot access GNMA data paths."
            )
        return cls._gnma_config

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
    def get_umbs_config(cls):
        """Get UMBS config class for data paths.

        Returns:
            UMBSConfig class with path constants.

        Raises:
            ImportError: If mortgage_data_manager.umbs is not available.
        """
        cls._check_umbs()
        if not cls._HAS_UMBS:
            raise ImportError(
                "mortgage_data_manager.umbs not found. Cannot access UMBS data paths."
            )
        return cls._umbs_config

    @classmethod
    def get_fhlb_config(cls):
        """Get FHLB config class for data paths.

        Returns:
            FHLBConfig class with path constants.

        Raises:
            ImportError: If mortgage_data_manager.fhlb is not available.
        """
        cls._check_fhlb()
        if not cls._HAS_FHLB:
            raise ImportError(
                "mortgage_data_manager.fhlb not found. Cannot access FHLB data paths."
            )
        return cls._fhlb_config

    @classmethod
    def get_gnma_silver_dir(cls) -> Path:
        """Get GNMA silver dailyllmni loan-level data directory.

        Returns:
            Path to GNMA silver dailyllmni L directory.

        Raises:
            ImportError: If mortgage_data_manager.gnma is not available.
        """
        gnma_config = cls.get_gnma_config()
        return gnma_config.GNMA_SILVER_DIR / "dailyllmni" / "L"

    @classmethod
    def get_fhlb_ama_silver_dir(cls) -> Path:
        """Get FHLB AMA silver data directory.

        Returns:
            Path to FHLB AMA silver directory.

        Raises:
            ImportError: If mortgage_data_manager.fhlb is not available.
        """
        fhlb_config = cls.get_fhlb_config()
        return fhlb_config.FHLB_SILVER_DIR / "ama"

    @classmethod
    def get_umbs_fnma_illd_bronze_dir(cls) -> Path:
        """Get UMBS FNMA ILLD bronze data directory.

        Returns path to FNMA Investor Loan-Level Disclosure (ILLD) parquet files
        in the UMBS bronze layer: data/umbs/bronze/FNMA/FNM_ILLD/

        Returns:
            Path to UMBS FNMA ILLD bronze directory.

        Raises:
            ImportError: If mortgage_data_manager.umbs is not available.
        """
        umbs_config = cls.get_umbs_config()
        return umbs_config.UMBS_BRONZE_DIR / "FNMA" / "FNM_ILLD"

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary MBS-FHLB matching directories if they don't exist."""
        cls.MBS_FHLB_MATCHING_DIR.mkdir(parents=True, exist_ok=True)
        cls.MBS_FHLB_INTERMEDIATE_DIR.mkdir(parents=True, exist_ok=True)
        cls.MBS_FHLB_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        cls.MBS_FHLB_CROSSWALK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# Backward compatibility exports
PROJECT_ROOT: Path = MBSFHLBConfig.PROJECT_DIR
DATA_DIR: Path = MBSFHLBConfig.MBS_FHLB_MATCHING_DIR
INTERMEDIATE_DIR: Path = MBSFHLBConfig.MBS_FHLB_INTERMEDIATE_DIR
OUTPUT_DIR: Path = MBSFHLBConfig.MBS_FHLB_OUTPUT_DIR
CROSSWALK_OUTPUT_DIR: Path = MBSFHLBConfig.MBS_FHLB_CROSSWALK_OUTPUT_DIR


# Backward compatibility for dependency checks and helper functions
def ensure_directories() -> None:
    """Create necessary directories for matching output.

    Deprecated: Use MBSFHLBConfig.ensure_directories() instead.
    """
    MBSFHLBConfig.ensure_directories()


def get_gnma_config():
    """Get GNMA config for data paths.

    Deprecated: Use MBSFHLBConfig.get_gnma_config() instead.
    """
    return MBSFHLBConfig.get_gnma_config()


def get_fnma_config():
    """Get FNMA config for data paths.

    Deprecated: Use MBSFHLBConfig.get_fnma_config() instead.
    """
    return MBSFHLBConfig.get_fnma_config()


def get_fhlb_config():
    """Get FHLB config for data paths.

    Deprecated: Use MBSFHLBConfig.get_fhlb_config() instead.
    """
    return MBSFHLBConfig.get_fhlb_config()


def get_gnma_silver_dir() -> Path:
    """Get GNMA silver dailyllmni loan-level data directory.

    Deprecated: Use MBSFHLBConfig.get_gnma_silver_dir() instead.
    """
    return MBSFHLBConfig.get_gnma_silver_dir()


def get_fhlb_ama_silver_dir() -> Path:
    """Get FHLB AMA silver data directory.

    Deprecated: Use MBSFHLBConfig.get_fhlb_ama_silver_dir() instead.
    """
    return MBSFHLBConfig.get_fhlb_ama_silver_dir()


def get_umbs_config():
    """Get UMBS config for data paths.

    Deprecated: Use MBSFHLBConfig.get_umbs_config() instead.
    """
    return MBSFHLBConfig.get_umbs_config()


def get_umbs_fnma_illd_bronze_dir() -> Path:
    """Get UMBS FNMA ILLD bronze data directory.

    Deprecated: Use MBSFHLBConfig.get_umbs_fnma_illd_bronze_dir() instead.
    """
    return MBSFHLBConfig.get_umbs_fnma_illd_bronze_dir()


# Check dependencies on import
MBSFHLBConfig._check_gnma()
MBSFHLBConfig._check_fnma()
MBSFHLBConfig._check_umbs()
MBSFHLBConfig._check_fhlb()

# Legacy module-level variables for backward compatibility
HAS_GNMA = MBSFHLBConfig._HAS_GNMA
HAS_FNMA = MBSFHLBConfig._HAS_FNMA
HAS_UMBS = MBSFHLBConfig._HAS_UMBS
HAS_FHLB = MBSFHLBConfig._HAS_FHLB

# Legacy config class references (set to None if not available, matching old behavior)
GNMAConfig = MBSFHLBConfig._gnma_config
FNMAConfig = MBSFHLBConfig._fnma_config
UMBSConfig = MBSFHLBConfig._umbs_config
FHLBConfig = MBSFHLBConfig._fhlb_config
