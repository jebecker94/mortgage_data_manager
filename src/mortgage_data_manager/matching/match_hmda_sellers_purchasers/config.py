"""Paths and year ranges for HMDA sellers/purchasers matching.

`HMDASellersPurchasersMatchingConfig` inherits `MatchingConfig` and is used as a class,
never instantiated. The post-2018 rounds live beside their engine in `matching_post2018`.
"""

from __future__ import annotations

from pathlib import Path

from decouple import config as decouple_config

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.matching.config import MatchingConfig
from mortgage_data_manager.sources.hmda.config import HMDAConfig


class HMDASellersPurchasersMatchingConfig(MatchingConfig):
    """HMDA Sellers/Purchasers matching specific configuration."""

    # Input data directories (from HMDA subpackage)
    HMDA_SILVER_DIR_POST2018: Path = Path(
        decouple_config(
            "HMDA_SILVER_DIR",
            default=str(
                MortgageDataConfig.get_subpackage_data_dir("hmda") / "silver" / "loans" / "post2018"
            ),
        )
    )
    # The pre-2018 era directory is named for the years it covers
    # (silver/loans/period_2007_2017); ask HMDAConfig rather than spelling it.
    HMDA_SILVER_DIR_PRE2018: Path = Path(
        decouple_config(
            "HMDA_SILVER_DIR_PRE2018",
            default=str(HMDAConfig.get_dataset_dir("silver", "loans", "period_2007_2017")),
        )
    )

    # Sellers/Purchasers matching output directories
    HMDA_SELLERS_PURCHASERS_MATCHING_DIR: Path = MatchingConfig.get_matching_type_dir(
        "hmda_sellers_purchasers"
    )
    HMDA_SELLERS_PURCHASERS_OUTPUT_DIR: Path = HMDA_SELLERS_PURCHASERS_MATCHING_DIR / "output"
    HMDA_SELLERS_PURCHASERS_INTERMEDIATE_DIR: Path = (
        HMDA_SELLERS_PURCHASERS_MATCHING_DIR / "intermediate"
    )

    # Crosswalk output directory (crosswalk/hmda_sellers_purchasers/)
    HMDA_SELLERS_PURCHASERS_CROSSWALK_DIR: Path = MatchingConfig.get_crosswalk_type_dir(
        "hmda_sellers_purchasers"
    )

    # Year ranges for matching
    MIN_YEAR_POST2018: int = int(
        decouple_config("HMDA_SELLERS_PURCHASERS_MIN_YEAR_POST2018", default="2018")
    )
    MAX_YEAR_POST2018: int = int(
        decouple_config("HMDA_SELLERS_PURCHASERS_MAX_YEAR_POST2018", default="2025")
    )
    MIN_YEAR_PRE2018: int = int(
        decouple_config("HMDA_SELLERS_PURCHASERS_MIN_YEAR_PRE2018", default="2007")
    )
    MAX_YEAR_PRE2018: int = int(
        decouple_config("HMDA_SELLERS_PURCHASERS_MAX_YEAR_PRE2018", default="2017")
    )

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary Sellers/Purchasers matching directories if they don't exist."""
        dirs = [
            cls.HMDA_SELLERS_PURCHASERS_MATCHING_DIR,
            cls.HMDA_SELLERS_PURCHASERS_OUTPUT_DIR,
            cls.HMDA_SELLERS_PURCHASERS_INTERMEDIATE_DIR,
            cls.HMDA_SELLERS_PURCHASERS_CROSSWALK_DIR,
        ]
        for dir_path in dirs:
            dir_path.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_crosswalk_dir(cls, era: str) -> Path:
        """One era's crosswalk, a dataset hive-partitioned by ``match_round``.

        Args:
            era: ``"post2018"`` or ``"pre2018"``.

        Returns:
            ``crosswalk/hmda_sellers_purchasers/hmda_sellers_purchasers_crosswalk_{era}/``.
        """
        return (
            cls.HMDA_SELLERS_PURCHASERS_CROSSWALK_DIR / f"hmda_sellers_purchasers_crosswalk_{era}"
        )

    @classmethod
    def get_output_dir_for_round(cls, match_round: int, post2018: bool = True) -> Path:
        """Get output directory for a specific matching round.

        Args:
            match_round: The matching round number (1-8 for post2018, 1-2 for pre2018)
            post2018: Whether this is for post-2018 or pre-2018 matching

        Returns:
            Path to the output directory for the specified round
        """
        era = "post2018" if post2018 else "pre2018"
        return cls.get_crosswalk_dir(era) / f"match_round={match_round}"
