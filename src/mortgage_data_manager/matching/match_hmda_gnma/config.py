"""Configuration for the HMDA-GNMA matching workflow (FHA and VA/USDA loans)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.matching.config import MatchingConfig
from mortgage_data_manager.sources.fha.config import FHAConfig

#: The two loan populations this workflow matches, one crosswalk each.
LoanType = Literal["fha", "va_usda"]


class HMDAGNMAMatchingConfig(MatchingConfig):
    """HMDA-GNMA matching configuration."""

    # --- inputs --------------------------------------------------------------
    #: The post-2018 HMDA-FHA crosswalk: the FHA matcher reads each FHA-matched HMDA
    #: loan's exact amount and endorsement month through it.
    HMDA_FHA_CROSSWALK_PATH: Path = Path(
        config(
            "HMDA_FHA_CROSSWALK",
            default=str(
                MatchingConfig.get_crosswalk_type_dir("hmda_fha")
                / "hmda_fha_crosswalk_post2018.parquet"
            ),
        )
    )

    # The FHA single-family silver *dataset* dir, one level below the silver root
    # that FHAConfig's FHA_SILVER_DIR names. It therefore reads its own key --
    # sharing FHA_SILVER_DIR with FHAConfig meant one key with two incompatible
    # meanings, so setting it in .env necessarily broke one side. The default
    # derives from FHAConfig, so overriding FHA_SILVER_DIR still moves this path
    # with it; FHA_SF_SILVER_DIR pins the dataset dir alone.
    FHA_SILVER_DIR: Path = Path(
        config("FHA_SF_SILVER_DIR", default=str(FHAConfig.FHA_SILVER_DIR / "single_family"))
    )

    # GNMA loans and issuer names are read through `sources.gnma.load`, which follows
    # GNMAConfig's silver root across the 2026 feed switch.

    HMDA_SILVER_DIR: Path = Path(
        config(
            "HMDA_SILVER_DIR",
            default=str(
                MortgageDataConfig.get_subpackage_data_dir("hmda") / "silver" / "loans" / "post2018"
            ),
        )
    )

    # --- outputs -------------------------------------------------------------
    HMDA_GNMA_MATCHING_DIR: Path = MatchingConfig.get_matching_type_dir("hmda_gnma")
    HMDA_GNMA_CROSSWALK_DIR: Path = MatchingConfig.get_crosswalk_type_dir("hmda_gnma")

    # --- parameters (validated end-to-end against real data) -----------------
    # Own year range: unlike the GSE chain (bounded by the latest hmda_fhfa year), the
    # GNMA edge reaches every HMDA year with published FHA + GNMA data.
    MIN_YEAR: int = int(config("HMDA_GNMA_MIN_YEAR", default="2018"))
    MAX_YEAR: int = int(config("HMDA_GNMA_MAX_YEAR", default="2025"))
    FHA_MIN_SCORE: int = int(config("HMDA_GNMA_MIN_SCORE", default="11"))
    # VA/USDA: validated floor and the candidate-cell size cap (larger cells cannot
    # resolve 1:1 and blow up the join). The floor is 9 rather than 7 because the
    # purpose-evidence term adds up to 3 points to the same score: 9 holds the bar
    # where 7 held it before the term existed.
    VA_USDA_MIN_SCORE: int = int(config("HMDA_GNMA_VA_USDA_MIN_SCORE", default="9"))
    # Round 3 (the cells round 1 dropped at the cap) sub-blocks on purpose x units x
    # borrowers, so units (3) and borrowers (2) are guaranteed for every pair it emits:
    # a total floor of 13 is a floor of 8 on the evidence that is NOT already in its key.
    # Yield and triangulation at 13 against the looser 11 are reported in
    # docs/matching/hmda_gnma_matching.md and refreshed on each rebuild.
    VA_USDA_CAPPED_MIN_SCORE: int = int(config("HMDA_GNMA_VA_USDA_CAPPED_MIN_SCORE", default="13"))
    CELL_CAP: int = int(config("HMDA_GNMA_CELL_CAP", default="400"))
    DYAD_MIN_COUNT: int = int(config("HMDA_GNMA_DYAD_MIN_COUNT", default="5"))
    # GNMA CLTV includes the financed upfront MIP (1.75% of base loan);
    # HMDA CLTV is the base ratio for the great majority of filers.
    MIP_FACTOR: float = 1.0175

    @classmethod
    def get_crosswalk_path(cls, loan_type: LoanType) -> Path:
        """The final crosswalk for one loan population.

        Args:
            loan_type: ``"fha"`` or ``"va_usda"``.

        Returns:
            ``crosswalk/hmda_gnma/hmda_gnma_crosswalk_{loan_type}.parquet``.
        """
        return cls.HMDA_GNMA_CROSSWALK_DIR / f"hmda_gnma_crosswalk_{loan_type}.parquet"

    @classmethod
    def ensure_directories(cls) -> None:
        """Create the workflow's own directories if they don't exist."""
        cls.HMDA_GNMA_MATCHING_DIR.mkdir(parents=True, exist_ok=True)
        cls.HMDA_GNMA_CROSSWALK_DIR.mkdir(parents=True, exist_ok=True)
