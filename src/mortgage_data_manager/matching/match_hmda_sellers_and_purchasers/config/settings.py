"""Configuration management for HMDA Sellers/Purchasers matching.

This module provides configuration management for the HMDA Sellers/Purchasers
matching subpackage, including both the legacy Config class and the new
SellersAndPurchasersMatchingConfig class that inherits from MatchingConfig.
"""

from __future__ import annotations

from pathlib import Path

from decouple import config as decouple_config

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.matching.config import MatchingConfig


class SellersAndPurchasersMatchingConfig(MatchingConfig):
    """HMDA Sellers/Purchasers matching specific configuration."""

    # Input data directories (from HMDA subpackage)
    HMDA_SILVER_DIR_POST2018: Path = Path(
        decouple_config(
            "HMDA_SILVER_DIR",
            default=str(MortgageDataConfig.get_subpackage_data_dir("hmda") / "silver" / "loans" / "post2018"),
        )
    )
    HMDA_SILVER_DIR_PRE2018: Path = Path(
        decouple_config(
            "HMDA_SILVER_DIR_PRE2018",
            default=str(MortgageDataConfig.get_subpackage_data_dir("hmda") / "silver" / "loans" / "pre2018"),
        )
    )

    # Sellers/Purchasers matching output directories
    SELLERS_PURCHASERS_MATCHING_DIR: Path = MatchingConfig.get_matching_type_dir("sellers_purchasers")
    SELLERS_PURCHASERS_OUTPUT_DIR: Path = SELLERS_PURCHASERS_MATCHING_DIR / "output"
    SELLERS_PURCHASERS_INTERMEDIATE_DIR: Path = SELLERS_PURCHASERS_MATCHING_DIR / "intermediate"

    # Crosswalk output directory (crosswalk/sellers_purchasers/)
    SELLERS_PURCHASERS_CROSSWALK_OUTPUT_DIR: Path = MatchingConfig.get_crosswalk_type_dir("sellers_purchasers")

    # Year ranges for matching
    MIN_YEAR_POST2018: int = int(decouple_config("SELLERS_PURCHASERS_MIN_YEAR_POST2018", default="2018"))
    MAX_YEAR_POST2018: int = int(decouple_config("SELLERS_PURCHASERS_MAX_YEAR_POST2018", default="2025"))
    MIN_YEAR_PRE2018: int = int(decouple_config("SELLERS_PURCHASERS_MIN_YEAR_PRE2018", default="2007"))
    MAX_YEAR_PRE2018: int = int(decouple_config("SELLERS_PURCHASERS_MAX_YEAR_PRE2018", default="2017"))

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary Sellers/Purchasers matching directories if they don't exist."""
        dirs = [
            cls.SELLERS_PURCHASERS_MATCHING_DIR,
            cls.SELLERS_PURCHASERS_OUTPUT_DIR,
            cls.SELLERS_PURCHASERS_INTERMEDIATE_DIR,
            cls.SELLERS_PURCHASERS_CROSSWALK_OUTPUT_DIR,
        ]
        for dir_path in dirs:
            dir_path.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_output_dir_for_round(cls, match_round: int, post2018: bool = True) -> Path:
        """Get output directory for a specific matching round.

        Args:
            match_round: The matching round number (1-8 for post2018, 1-2 for pre2018)
            post2018: Whether this is for post-2018 or pre-2018 matching

        Returns:
            Path to the output directory for the specified round
        """
        period = "post2018" if post2018 else "pre2018"
        return cls.SELLERS_PURCHASERS_CROSSWALK_OUTPUT_DIR / period / f"MatchRound={match_round}"


# Module-level exports for the new config (backward compatibility)
PROJECT_DIR: Path = SellersAndPurchasersMatchingConfig.PROJECT_DIR
HMDA_SILVER_DIR_POST2018: Path = SellersAndPurchasersMatchingConfig.HMDA_SILVER_DIR_POST2018
HMDA_SILVER_DIR_PRE2018: Path = SellersAndPurchasersMatchingConfig.HMDA_SILVER_DIR_PRE2018
OUTPUT_DIR: Path = SellersAndPurchasersMatchingConfig.SELLERS_PURCHASERS_OUTPUT_DIR
INTERMEDIATE_DIR: Path = SellersAndPurchasersMatchingConfig.SELLERS_PURCHASERS_INTERMEDIATE_DIR
CROSSWALK_OUTPUT_DIR: Path = SellersAndPurchasersMatchingConfig.SELLERS_PURCHASERS_CROSSWALK_OUTPUT_DIR
MIN_YEAR_POST2018: int = SellersAndPurchasersMatchingConfig.MIN_YEAR_POST2018
MAX_YEAR_POST2018: int = SellersAndPurchasersMatchingConfig.MAX_YEAR_POST2018
MIN_YEAR_PRE2018: int = SellersAndPurchasersMatchingConfig.MIN_YEAR_PRE2018
MAX_YEAR_PRE2018: int = SellersAndPurchasersMatchingConfig.MAX_YEAR_PRE2018


# ============================================================================
# Legacy Config class (for backward compatibility with existing scripts)
# ============================================================================


class Config:
    """Configuration settings for HMDA matching workflows."""

    def __init__(
        self,
        data_dir: Path | str | None = None,
        match_data_dir: Path | str | None = None,
        file_suffix: str = "",
    ):
        """Initialize configuration.

        Args:
            data_dir: Directory containing HMDA delivery folders and file_list_hmda.csv manifest.
                If not provided, will look for DATA_DIR environment variable or default to
                ``data/hmda/post2018`` within the project.
            match_data_dir: Directory for storing match outputs. Defaults to a sibling ``matches`` directory
                that mirrors the structure beneath ``data/hmda``.
            file_suffix: Suffix to append to output filenames.
        """
        if data_dir is None:
            data_dir = decouple_config("DATA_DIR", default=None)
            if data_dir is None:
                data_dir = Path("data") / "hmda" / "post2018"

        self.data_dir = Path(data_dir)

        if match_data_dir is None:
            match_data_dir = self._infer_match_dir_from_data(self.data_dir)

        self.match_data_dir = Path(match_data_dir)
        self.file_suffix = file_suffix

    @staticmethod
    def _infer_match_dir_from_data(data_dir: Path) -> Path:
        """Infer the default matches directory based on the HMDA data location."""
        data_parts = data_dir.parts
        if "hmda" in data_parts:
            hmda_idx = data_parts.index("hmda")
            base_parts = data_parts[:hmda_idx]
            remainder_parts = data_parts[hmda_idx + 1 :]
            base_path = Path(*base_parts) if base_parts else Path(".")
            matches_path = base_path / "matches"
            if remainder_parts:
                matches_path = matches_path.joinpath(*remainder_parts)
            return matches_path
        return data_dir.parent / "matches"


# Global default config instance
_config: Config | None = None


def get_config() -> Config:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = Config()
    return _config


def set_config(config: Config) -> None:
    """Set the global configuration instance."""
    global _config
    _config = config


# For backward compatibility with existing scripts
DATA_DIR = get_config().data_dir
