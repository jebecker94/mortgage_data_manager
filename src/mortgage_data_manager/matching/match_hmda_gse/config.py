"""Configuration for the HMDA-GSE matching workflow.

The workflow links HMDA conventional originations to the GSE loan-level records by two
routes: the chain (HMDA → FHFA → MBS → UMBS, composed from the upstream crosswalks) and
the direct match against the combined loan-issuance spine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.matching.config import MatchingConfig


class HMDAGSEMatchingConfig(MatchingConfig):
    """HMDA-GSE matching specific configuration."""

    # Input data directories — read upstream crosswalks from crosswalk/ directory
    HMDA_FHFA_CROSSWALK: Path = Path(
        config(
            "HMDA_FHFA_CROSSWALK",
            default=str(
                MatchingConfig.get_crosswalk_type_dir("hmda_fhfa")
                / "hmda_fhfa_crosswalk_post2018.parquet"
            ),
        )
    )

    FHFA_MBS_OUTPUT_DIR: Path = Path(
        config(
            "FHFA_MBS_OUTPUT_DIR",
            default=str(MatchingConfig.get_crosswalk_type_dir("fhfa_mbs")),
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
            default=str(MortgageDataConfig.get_subpackage_data_dir("hmda") / "bronze" / "panel"),
        )
    )

    UMBS_BRONZE_DIR: Path = Path(
        config(
            "UMBS_BRONZE_DIR",
            default=str(MortgageDataConfig.get_subpackage_data_dir("umbs") / "bronze"),
        )
    )

    # Output directories
    HMDA_GSE_MATCHING_DIR: Path = MatchingConfig.get_matching_type_dir("hmda_gse")
    HMDA_GSE_OUTPUT_DIR: Path = HMDA_GSE_MATCHING_DIR / "output"

    # Crosswalk output directory (crosswalk/hmda_gse/)
    HMDA_GSE_CROSSWALK_DIR: Path = MatchingConfig.get_crosswalk_type_dir("hmda_gse")

    # Lender/Seller crosswalk output paths
    LENDER_SELLER_CROSSWALK_PATH: Path = (
        HMDA_GSE_OUTPUT_DIR / "hmda_gse_lender_seller_crosswalk.parquet"
    )

    # Year range for matching. Min year is 2018: HMDA LEI begins 2018, and the chain reaches
    # 2018 originations via the fhfa_mbs_2018 crosswalk + the pre-2019 UMBS snapshot.
    MIN_YEAR: int = int(config("HMDA_GSE_MIN_YEAR", default="2018"))
    MAX_YEAR: int = int(config("HMDA_GSE_MAX_YEAR", default="2025"))

    # Combined tri-agency loan-issuance spine: the target side of the direct
    # HMDA<->issuance matcher (matching_direct.py). Read via the config rather
    # than importing CombinedConfig, keeping the dependency one-way.
    COMBINED_LOAN_ISSUANCE_DIR: Path = Path(
        config(
            "COMBINED_LOAN_ISSUANCE_DIR",
            default=str(MortgageDataConfig.get_subpackage_data_dir("combined") / "loan_issuance"),
        )
    )

    # Deprecated: production reads the release each crosswalk's HMDAIndex names
    # (``hmda.load.scan_hmda_for_index``) or the best available on disk, so this
    # table no longer has to track new HMDA releases. Kept for external scripts
    # that import it.
    FILE_TYPE_BY_YEAR: dict[int, str] = {
        2018: "a",
        2019: "a",
        2020: "a",
        2021: "a",
        2022: "a",  # three-year published 2026-06 (was "b")
        2023: "b",
        2024: "b",  # one-year published 2026-06 (was "c")
        2025: "c",
    }

    # Agency configuration
    AGENCY_CONFIG: dict[str, dict] = {
        "fnma": {
            "enterprise_flag": 1,
            "purchaser_type": 1,
            "loan_id_col": "fnma_loan_id",
            "fhfa_mbs_pattern": "fhfa_mbs_crosswalk_fnma_{year}.parquet",
            "mbs_umbs_file": "mbs_umbs_crosswalk_fnma.parquet",
            "umbs_illd_dir": "FNMA/FNM_ILLD",
        },
        "fhlmc": {
            "enterprise_flag": 2,
            "purchaser_type": 3,
            "loan_id_col": "fhlmc_loan_id",
            "fhfa_mbs_pattern": "fhfa_mbs_crosswalk_fhlmc_{year}.parquet",
            "mbs_umbs_file": "mbs_umbs_crosswalk_fhlmc.parquet",
            "umbs_illd_dir": "FHLMC/FRE_ILLD",
        },
    }

    @classmethod
    def get_fhfa_mbs_path(cls, agency: Literal["fnma", "fhlmc"], year: int) -> Path:
        """Get path to FHFA-MBS crosswalk for a given agency and year.

        Args:
            agency: Which agency to address.
            year: Activity year.

        Returns:
            The FHFA-MBS crosswalk path for that agency and year.
        """
        pattern = cls.AGENCY_CONFIG[agency]["fhfa_mbs_pattern"]
        return cast(Path, cls.FHFA_MBS_OUTPUT_DIR / pattern.format(year=year))

    @classmethod
    def get_mbs_umbs_path(cls, agency: Literal["fnma", "fhlmc"]) -> Path:
        """Get path to MBS-UMBS crosswalk for a given agency.

        Args:
            agency: Which agency to address.

        Returns:
            The MBS-UMBS crosswalk path for that agency.
        """
        return cast(Path, cls.MBS_UMBS_OUTPUT_DIR / cls.AGENCY_CONFIG[agency]["mbs_umbs_file"])

    @classmethod
    def get_panel_path(cls, year: int) -> Path:
        """Get path to HMDA panel for a given year (or closest available).

        Args:
            year: Activity year.

        Returns:
            The HMDA panel path for that year, or the closest available.
        """
        panel_year = min(year, 2023)
        panel_path = cls.HMDA_PANEL_DIR / f"{panel_year}_public_panel.parquet"
        if not panel_path.exists():
            panel_path = cls.HMDA_PANEL_DIR / "2023_public_panel.parquet"
        return panel_path

    @classmethod
    def get_umbs_illd_dir(cls, agency: Literal["fnma", "fhlmc"]) -> Path:
        """Get path to UMBS ILLD directory for a given agency.

        Args:
            agency: Which agency to address.

        Returns:
            The UMBS ILLD directory for that agency.
        """
        return cast(Path, cls.UMBS_BRONZE_DIR / cls.AGENCY_CONFIG[agency]["umbs_illd_dir"])

    @classmethod
    def get_chain_crosswalk_path(
        cls, agency: Literal["fnma", "fhlmc"], enriched: bool = False
    ) -> Path:
        """Path to one enterprise's chain crosswalk (HMDA → FHFA → MBS → UMBS).

        The crosswalk goes to ``crosswalk/hmda_gse/``; the enriched variant stays in
        ``data/matching/hmda_gse/output/``.

        Args:
            agency: Which enterprise to address.
            enriched: Address the enriched variant instead.

        Returns:
            The chain crosswalk path.
        """
        suffix = "_enriched" if enriched else ""
        output_dir = cls.HMDA_GSE_OUTPUT_DIR if enriched else cls.HMDA_GSE_CROSSWALK_DIR
        return output_dir / f"hmda_gse_crosswalk_chain_{agency}{suffix}.parquet"

    @classmethod
    def get_direct_crosswalk_path(cls, year: int) -> Path:
        """Get crosswalk path for one year of the direct HMDA<->issuance matcher.

        One file per activity year (as ``fhfa_mbs`` does) so a single year can be
        rebuilt without touching the others.

        Args:
            year: HMDA activity year.

        Returns:
            Path under ``crosswalk/hmda_gse/``
        """
        return cls.HMDA_GSE_CROSSWALK_DIR / f"hmda_gse_crosswalk_direct_{year}.parquet"

    @classmethod
    def get_bridge_path(cls) -> Path:
        """The direct matcher's lender bridge (LEI -> GSE seller names), as last built.

        Saved beside the crosswalks so a run can reuse it (``run --saved-bridge``)
        instead of rebuilding it.

        Returns:
            Path under ``crosswalk/hmda_gse/``.
        """
        return cls.HMDA_GSE_CROSSWALK_DIR / "hmda_gse_lender_bridge.parquet"

    @classmethod
    def ensure_directories(cls) -> None:
        """Create output directories if they don't exist."""
        cls.HMDA_GSE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        cls.HMDA_GSE_CROSSWALK_DIR.mkdir(parents=True, exist_ok=True)
