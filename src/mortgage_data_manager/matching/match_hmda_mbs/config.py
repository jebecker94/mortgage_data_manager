"""Configuration for HMDA-MBS matching workflow.

This module provides configuration management for the HMDA-MBS matching subpackage,
which builds master crosswalks linking HMDA → FHFA → MBS → UMBS.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.matching.config import MatchingConfig


class HMDAMBSMatchingConfig(MatchingConfig):
    """HMDA-MBS matching specific configuration."""

    # Input data directories — read upstream crosswalks from crosswalk/ directory
    FHFA_HMDA_CROSSWALK: Path = Path(
        config(
            "FHFA_HMDA_CROSSWALK",
            default=str(
                MatchingConfig.get_crosswalk_type_dir("fhfa_hmda")
                / "fhfa_hmda_crosswalk_2018_2024.parquet"
            ),
        )
    )

    # FHA-related input paths
    HMDA_FHA_MERGED_PATH: Path = Path(
        config(
            "HMDA_FHA_MERGED_PATH",
            default=str(
                MatchingConfig.get_crosswalk_type_dir("fha_hmda")
                / "fha_hmda_crosswalk_2018_2024.parquet"
            ),
        )
    )

    FHA_GNMA_CROSSWALK_PATH: Path = Path(
        config(
            "FHA_GNMA_CROSSWALK_PATH",
            default=str(
                MatchingConfig.get_crosswalk_type_dir("fha_gnma")
                / "fha_gnma_crosswalk_2015_2024.parquet"
            ),
        )
    )

    GNMA_SILVER_DIR: Path = Path(
        config(
            "GNMA_SILVER_DIR",
            default=str(
                MortgageDataConfig.get_subpackage_data_dir("gnma") / "silver" / "dailyllmni" / "L"
            ),
        )
    )

    MBS_FHFA_OUTPUT_DIR: Path = Path(
        config(
            "MBS_FHFA_OUTPUT_DIR",
            default=str(MatchingConfig.get_crosswalk_type_dir("mbs_fhfa")),
        )
    )

    MBS_UMBS_OUTPUT_DIR: Path = Path(
        config(
            "MBS_UMBS_OUTPUT_DIR",
            default=str(MatchingConfig.get_crosswalk_type_dir("mbs_umbs")),
        )
    )

    HMDA_SILVER_DIR: Path = Path(
        config(
            "HMDA_SILVER_DIR",
            default=str(
                MortgageDataConfig.get_subpackage_data_dir("hmda") / "silver" / "loans" / "post2018"
            ),
        )
    )

    HMDA_PANEL_DIR: Path = Path(
        config(
            "HMDA_PANEL_DIR",
            default=str(
                MortgageDataConfig.get_subpackage_data_dir("hmda") / "bronze" / "panel" / "post2018"
            ),
        )
    )

    UMBS_BRONZE_DIR: Path = Path(
        config(
            "UMBS_BRONZE_DIR",
            default=str(MortgageDataConfig.get_subpackage_data_dir("umbs") / "bronze"),
        )
    )

    # Output directories
    HMDA_MBS_MATCHING_DIR: Path = MatchingConfig.get_matching_type_dir("hmda_mbs")
    HMDA_MBS_OUTPUT_DIR: Path = HMDA_MBS_MATCHING_DIR / "output"

    # Crosswalk output directory (crosswalk/hmda_mbs/)
    HMDA_MBS_CROSSWALK_OUTPUT_DIR: Path = MatchingConfig.get_crosswalk_type_dir("hmda_mbs")

    # Lender/Seller crosswalk output paths
    LENDER_SELLER_CROSSWALK_PATH: Path = HMDA_MBS_OUTPUT_DIR / "lender_seller_crosswalk.parquet"
    ANNOTATIONS_PATH: Path = (
        HMDA_MBS_MATCHING_DIR / "annotations" / "lender_seller_annotations.yaml"
    )

    # Year range for matching. Min year is 2018: HMDA LEI begins 2018, and the chain reaches
    # 2018 originations via the mbs_fhfa_2018 crosswalk + the pre-2019 UMBS snapshot.
    MIN_YEAR: int = int(config("HMDA_MBS_MIN_YEAR", default="2018"))
    MAX_YEAR: int = int(config("HMDA_MBS_MAX_YEAR", default="2024"))

    # Direct match output directory
    DIRECT_MATCH_OUTPUT_DIR: Path = HMDA_MBS_OUTPUT_DIR / "direct_match"

    # Seller-purchaser crosswalk for Phase 3 correspondent chain matching
    SELLER_PURCHASER_CROSSWALK_DIR: Path = Path(
        config(
            "SELLER_PURCHASER_CROSSWALK_DIR",
            default=str(MatchingConfig.get_crosswalk_type_dir("sellers_purchasers") / "post2018"),
        )
    )

    # Conforming loan limits by year (high-cost areas)
    CONFORMING_LIMITS: dict[int, int] = {
        2020: 765_600,
        2021: 822_375,
        2022: 970_800,
        2023: 1_089_300,
        2024: 1_149_825,
    }

    # Matching round tolerances for direct matching
    DIRECT_MATCH_ROUNDS: list[dict] = [
        {"round": 1, "rate_tol": 0.125, "term_tol": 6, "strict_occupancy": True},
        {"round": 2, "rate_tol": 0.375, "term_tol": 12, "strict_occupancy": False},
    ]

    # File type mapping: the HMDA file_type prefix the (dynamic a>b>c) FHFA-HMDA
    # crosswalk uses for each year. Must track the most-mature vintage available
    # per year — update as newer HMDA releases publish, or these joins silently
    # miss (denominator/crosswalk prefixes diverge -> 0% match rate for the year).
    FILE_TYPE_BY_YEAR: dict[int, str] = {
        2018: "a",
        2019: "a",
        2020: "a",
        2021: "a",
        2022: "a",  # three-year published 2026-06 (was "b")
        2023: "b",
        2024: "b",  # one-year published 2026-06 (was "c")
    }

    # Agency configuration
    AGENCY_CONFIG: dict[str, dict] = {
        "fnma": {
            "enterprise_flag": 1,
            "purchaser_type": 1,
            "loan_id_col": "fnma_loan_id",
            "mbs_fhfa_pattern": "fnma_fhfa_crosswalk_{year}.parquet",
            "mbs_umbs_file": "fnma_crosswalk.parquet",
            "umbs_illd_dir": "FNMA/FNM_ILLD",
        },
        "fhlmc": {
            "enterprise_flag": 2,
            "purchaser_type": 3,
            "loan_id_col": "fhlmc_loan_id",
            "mbs_fhfa_pattern": "fhlmc_fhfa_crosswalk_{year}.parquet",
            "mbs_umbs_file": "fhlmc_crosswalk.parquet",
            "umbs_illd_dir": "FHLMC/FRE_ILLD",
        },
    }

    @classmethod
    def get_mbs_fhfa_path(cls, agency: Literal["fnma", "fhlmc"], year: int) -> Path:
        """Get path to MBS-FHFA crosswalk for a given agency and year."""
        pattern = cls.AGENCY_CONFIG[agency]["mbs_fhfa_pattern"]
        return cls.MBS_FHFA_OUTPUT_DIR / pattern.format(year=year)

    @classmethod
    def get_mbs_umbs_path(cls, agency: Literal["fnma", "fhlmc"]) -> Path:
        """Get path to MBS-UMBS crosswalk for a given agency."""
        return cls.MBS_UMBS_OUTPUT_DIR / cls.AGENCY_CONFIG[agency]["mbs_umbs_file"]

    @classmethod
    def get_hmda_path(cls, year: int) -> str:
        """Get glob pattern for HMDA silver data for a given year."""
        return str(cls.HMDA_SILVER_DIR / f"activity_year={year}/**/*.parquet")

    @classmethod
    def get_panel_path(cls, year: int) -> Path:
        """Get path to HMDA panel for a given year (or closest available)."""
        panel_year = min(year, 2023)
        panel_path = cls.HMDA_PANEL_DIR / f"{panel_year}_public_panel.parquet"
        if not panel_path.exists():
            panel_path = cls.HMDA_PANEL_DIR / "2023_public_panel.parquet"
        return panel_path

    @classmethod
    def get_umbs_illd_dir(cls, agency: Literal["fnma", "fhlmc"]) -> Path:
        """Get path to UMBS ILLD directory for a given agency."""
        return cls.UMBS_BRONZE_DIR / cls.AGENCY_CONFIG[agency]["umbs_illd_dir"]

    @classmethod
    def get_output_path(cls, agency: Literal["fnma", "fhlmc"], enriched: bool = False) -> Path:
        """Get output path for master crosswalk.

        Base crosswalks go to crosswalk/hmda_mbs/, enriched variants stay in data/matching/.
        """
        suffix = "_enriched" if enriched else ""
        output_dir = cls.HMDA_MBS_OUTPUT_DIR if enriched else cls.HMDA_MBS_CROSSWALK_OUTPUT_DIR
        return output_dir / f"master_crosswalk_{agency}{suffix}.parquet"

    @classmethod
    def get_direct_match_output_path(cls, agency: Literal["fnma", "fhlmc"]) -> Path:
        """Get output path for direct match crosswalk."""
        return cls.DIRECT_MATCH_OUTPUT_DIR / f"direct_match_{agency}.parquet"

    @classmethod
    def ensure_directories(cls) -> None:
        """Create output directories if they don't exist."""
        cls.HMDA_MBS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        cls.DIRECT_MATCH_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        cls.HMDA_MBS_CROSSWALK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
