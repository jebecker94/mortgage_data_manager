"""Configuration for Federal Home Loan Bank (FHLB) data.

This module provides the FHLBConfig class that inherits from MortgageDataConfig
and adds FHLB-specific configuration including paths and constants.

Environment Variables:
    FHLB_DATA_DIR: Root FHLB data directory (default: {DATA_DIR}/fhlb)
    FHLB_RAW_DIR: Raw FHLB data directory (default: {FHLB_DATA_DIR}/raw)
    FHLB_BRONZE_DIR: Bronze FHLB data directory (default: {FHLB_DATA_DIR}/bronze)
    FHLB_SILVER_DIR: Silver FHLB data directory (default: {FHLB_DATA_DIR}/silver)
    FHLB_GOLD_DIR: Gold FHLB data directory (default: {FHLB_DATA_DIR}/gold)

Example:
    >>> from mortgage_data_manager.fhlb.config import FHLBConfig
    >>> FHLBConfig.ensure_directories()
    >>> print(FHLBConfig.FHLB_RAW_DIR)
    /Users/username/mortgage_data_manager/data/fhlb/raw
"""

from __future__ import annotations

from pathlib import Path

from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig


class FHLBConfig(MortgageDataConfig):
    """Configuration for Federal Home Loan Bank data.

    This class inherits from MortgageDataConfig and provides FHLB-specific
    configuration including medallion directories and data processing constants.
    """

    # Data directories
    FHLB_DATA_DIR: Path = Path(
        config(
            "FHLB_DATA_DIR",
            default=str(MortgageDataConfig.get_subpackage_data_dir("fhlb"))
        )
    )

    FHLB_RAW_DIR: Path = Path(
        config("FHLB_RAW_DIR", default=str(FHLB_DATA_DIR / "raw"))
    )

    FHLB_BRONZE_DIR: Path = Path(
        config("FHLB_BRONZE_DIR", default=str(FHLB_DATA_DIR / "bronze"))
    )

    FHLB_SILVER_DIR: Path = Path(
        config("FHLB_SILVER_DIR", default=str(FHLB_DATA_DIR / "silver"))
    )

    FHLB_GOLD_DIR: Path = Path(
        config("FHLB_GOLD_DIR", default=str(FHLB_DATA_DIR / "gold"))
    )

    # Schema directory
    FHLB_SCHEMAS_DIR: Path = Path(
        config("FHLB_SCHEMAS_DIR", default=str(MortgageDataConfig.SCHEMAS_DIR / "fhlb"))
    )

    # Raw subdirectories
    FHLB_RAW_MEMBERS_DIR: Path = FHLB_RAW_DIR / "members"
    FHLB_RAW_AMA_DIR: Path = FHLB_RAW_DIR / "ama"

    # Data processing defaults for members
    FHLB_DEFAULT_MIN_YEAR: int = 2009
    FHLB_DEFAULT_MAX_YEAR: int = 2025

    # AMA-specific year range (AMA data available from 2009-2024)
    FHLB_DEFAULT_AMA_MIN_YEAR: int = 2009
    FHLB_DEFAULT_AMA_MAX_YEAR: int = 2024

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary FHLB directories.

        Creates the raw, bronze, and silver directories for FHLB data
        following the medallion architecture pattern. Also creates subdirectories
        for member data and AMA data.

        Example:
            >>> FHLBConfig.ensure_directories()
            >>> # Creates data/fhlb/raw, data/fhlb/bronze, data/fhlb/silver
            >>> # Also creates data/fhlb/raw/members, data/fhlb/raw/ama
        """
        cls.FHLB_RAW_DIR.mkdir(parents=True, exist_ok=True)
        cls.FHLB_BRONZE_DIR.mkdir(parents=True, exist_ok=True)
        cls.FHLB_SILVER_DIR.mkdir(parents=True, exist_ok=True)

        # Create subdirectories
        cls.FHLB_RAW_MEMBERS_DIR.mkdir(parents=True, exist_ok=True)
        cls.FHLB_RAW_AMA_DIR.mkdir(parents=True, exist_ok=True)
