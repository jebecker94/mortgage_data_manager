"""Configuration settings for MBS-UMBS matching.

This module provides configuration for matching MBS loan-level data to UMBS
issuance data for FNMA and FHLMC.
"""

from __future__ import annotations

from pathlib import Path

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.matching.config import MatchingConfig

logger = get_logger(__name__)


class MBSUMBSConfig(MatchingConfig):
    """Configuration for MBS-UMBS matching workflow."""

    # Matching directories
    MBS_UMBS_MATCHING_DIR: Path = MatchingConfig.get_matching_type_dir("mbs_umbs")
    MBS_UMBS_OUTPUT_DIR: Path = MBS_UMBS_MATCHING_DIR / "output"
    MBS_UMBS_CROSSWALK_OUTPUT_DIR: Path = MatchingConfig.get_crosswalk_type_dir("mbs_umbs")

    # UMBS bronze data directory
    UMBS_BRONZE_DIR: Path = MortgageDataConfig.get_subpackage_data_dir("umbs") / "bronze"

    # Dependency checks
    _HAS_FHLMC: bool = False
    _HAS_FNMA: bool = False
    _fhlmc_config = None
    _fnma_config = None

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
    def get_fhlmc_config(cls):
        """Get FHLMC config class for data paths.

        Returns:
            FHLMCConfig class with path constants like FHLMC_BRONZE_ORIGINATION, FHLMC_RAW_DIR, etc.

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
    def get_umbs_bronze_dir(cls) -> Path:
        """Get UMBS bronze directory path.

        Returns:
            Path to the UMBS bronze data directory.
            UMBS bronze data is organized as: bronze/{GSE}/{folder}/
            For FHLMC UMBS data: bronze/FHLMC/FRE_ILLD/
        """
        return cls.UMBS_BRONZE_DIR

    # The first month of UMBS issuance disclosure (ILLD/FRE_ILLD). Loans originated before
    # this never appear in ILLD, but those still outstanding on this date appear in the first
    # MONTHLY snapshot (FNM_MLLD / FU) — see get_umbs_snapshot_file.
    UMBS_ISSUANCE_START: str = "2019-06-01"

    # First monthly loan-level snapshot folders per GSE (the pre-2019 recovery source).
    _SNAPSHOT_FOLDER: dict[str, str] = {"fnma": "FNMA/FNM_MLLD", "fhlmc": "FHLMC/FU"}

    @classmethod
    def get_umbs_snapshot_file(cls, gse: str) -> Path | None:
        """Get the first monthly UMBS snapshot file for a GSE (pre-2019 recovery source).

        The first monthly loan-level disclosure file (FNM_MLLD_201906 for Fannie, fu190606
        for Freddie) is a snapshot of every loan outstanding in a UMBS-eligible pool as of
        June 2019. It carries the same at-origination static fields as ILLD, so it recovers
        loans issued before ILLD coverage began (`UMBS_ISSUANCE_START`).

        Args:
            gse: ``"fnma"`` or ``"fhlmc"``.

        Returns:
            Path to the earliest monthly snapshot parquet, or ``None`` if the folder is
            absent or empty.
        """
        folder = cls._SNAPSHOT_FOLDER.get(gse.lower())
        if folder is None:
            return None
        snapshot_dir = cls.UMBS_BRONZE_DIR / folder
        if not snapshot_dir.exists():
            return None
        files = sorted(snapshot_dir.glob("*.parquet"))
        return files[0] if files else None

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary MBS-UMBS matching directories if they don't exist."""
        cls.MBS_UMBS_MATCHING_DIR.mkdir(parents=True, exist_ok=True)
        cls.MBS_UMBS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        cls.MBS_UMBS_CROSSWALK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# Backward compatibility exports
PROJECT_ROOT: Path = MBSUMBSConfig.PROJECT_DIR
DATA_DIR: Path = MBSUMBSConfig.MBS_UMBS_MATCHING_DIR
OUTPUT_DIR: Path = MBSUMBSConfig.MBS_UMBS_OUTPUT_DIR
CROSSWALK_OUTPUT_DIR: Path = MBSUMBSConfig.MBS_UMBS_CROSSWALK_OUTPUT_DIR
UMBS_BRONZE_DIR: Path = MBSUMBSConfig.UMBS_BRONZE_DIR


# Backward compatibility for dependency checks
def get_fhlmc_config() -> type:
    """Get FHLMC config class for data paths.

    Returns the FHLMC config class which has class-level path constants.
    Access paths via: config.FHLMC_BRONZE_ORIGINATION, config.FHLMC_RAW_DIR, etc.

    Deprecated: Use MBSUMBSConfig.get_fhlmc_config() instead.
    """
    return MBSUMBSConfig.get_fhlmc_config()


def get_fnma_config() -> type:
    """Get FNMA config for data paths.

    Deprecated: Use MBSUMBSConfig.get_fnma_config() instead.
    """
    return MBSUMBSConfig.get_fnma_config()


def get_umbs_bronze_dir() -> Path:
    """Get UMBS bronze directory path.

    Deprecated: Use MBSUMBSConfig.get_umbs_bronze_dir() instead.
    """
    return MBSUMBSConfig.get_umbs_bronze_dir()


# Check dependencies on import
MBSUMBSConfig._check_fhlmc()
MBSUMBSConfig._check_fnma()
HAS_FHLMC = MBSUMBSConfig._HAS_FHLMC
HAS_FNMA = MBSUMBSConfig._HAS_FNMA
