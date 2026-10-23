"""Configuration for HMDA-FHA matching workflow.

This module provides configuration management for the HMDA-FHA matching subpackage,
inheriting from the base MortgageDataConfig and MatchingConfig.

The workflow covers two eras with different inputs, keys and rebuild cadences.
The unprefixed settings (``MIN_YEAR``/``MAX_YEAR``, ``HMDA_SILVER_DIR``) are the
**post-2018** era's, which is the default era; the pre-2018 era's are prefixed
``PRE2018_``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.matching.config import MatchingConfig
from mortgage_data_manager.sources.fha.config import FHAConfig
from mortgage_data_manager.sources.hmda.config import HMDAConfig

Era = Literal["post2018", "pre2018"]


class HMDAFHAMatchingConfig(MatchingConfig):
    """HMDA-FHA matching specific configuration.

    ``MIN_YEAR``/``MAX_YEAR`` are the *requested* year range of the default
    (post-2018) era. The pre-2018 era carries its own requested range in
    ``PRE2018_MIN_YEAR``/``PRE2018_MAX_YEAR``; use
    `year_range_for_era` to resolve either.
    """

    # Input data directories (from other subpackages).
    #
    # This is the FHA single-family silver *dataset* dir, one level below the
    # silver root that FHAConfig's FHA_SILVER_DIR names. It therefore reads its
    # own key -- sharing FHA_SILVER_DIR with FHAConfig meant one key with two
    # incompatible meanings, so setting it in .env necessarily broke one side.
    # The default derives from FHAConfig, so overriding FHA_SILVER_DIR still
    # moves this path with it; FHA_SF_SILVER_DIR pins the dataset dir alone.
    FHA_SILVER_DIR: Path = Path(
        config("FHA_SF_SILVER_DIR", default=str(FHAConfig.FHA_SILVER_DIR / "single_family"))
    )
    HMDA_SILVER_DIR: Path = Path(
        config(
            "HMDA_SILVER_DIR",
            default=str(
                MortgageDataConfig.get_subpackage_data_dir("hmda") / "silver" / "loans" / "post2018"
            ),
        )
    )

    # HMDA-FHA matching output directories
    HMDA_FHA_MATCHING_DIR: Path = MatchingConfig.get_matching_type_dir("hmda_fha")
    HMDA_FHA_OUTPUT_DIR: Path = HMDA_FHA_MATCHING_DIR / "output"
    HMDA_FHA_INTERMEDIATE_DIR: Path = HMDA_FHA_MATCHING_DIR / "intermediate"
    HMDA_FHA_ZIP_TRACT_DIR: Path = HMDA_FHA_MATCHING_DIR / "zip_tract"
    # The pre-2018 lender bridge (``lender_bridge``); its writer mkdirs on demand.
    HMDA_FHA_BRIDGE_DIR: Path = HMDA_FHA_MATCHING_DIR / "bridge"

    # Crosswalk output directory (crosswalk/hmda_fha/)
    HMDA_FHA_CROSSWALK_DIR: Path = MatchingConfig.get_crosswalk_type_dir("hmda_fha")

    # USPS ZIP-Tract crosswalk source (from usps_crosswalk subpackage)
    # Points to the bronze per-quarter parquet that load_crosswalk() reads from.
    # Set USPS_CROSSWALK_DIR environment variable to override.
    USPS_CROSSWALK_DIR: Path = Path(
        config(
            "USPS_CROSSWALK_DIR",
            default=str(MortgageDataConfig.get_subpackage_data_dir("usps_crosswalk") / "bronze"),
        )
    )

    # Year range for matching (post-2018 era -- the default era; see the class
    # docstring). Requested-range names under the three-way year-naming law.
    MIN_YEAR: int = int(config("HMDA_FHA_MIN_YEAR", default="2018"))
    MAX_YEAR: int = int(config("HMDA_FHA_MAX_YEAR", default="2025"))

    # --- pre-2018 era: requested range ------------------------------------
    # These are *requested* bounds, not source-existence bounds, because the CLI
    # overrides them -- even though they happen to coincide with the sources
    # (FHA silver's first endorsement is 2010-05; HMDA period_2007_2017 ends at
    # 2017). Source-existence constants, if ever wanted, take START_YEAR/END_YEAR.
    PRE2018_MIN_YEAR: int = int(config("HMDA_FHA_PRE2018_MIN_YEAR", default="2010"))
    PRE2018_MAX_YEAR: int = int(config("HMDA_FHA_PRE2018_MAX_YEAR", default="2017"))

    # --- pre-2018 era: match keys -----------------------------------------
    # Pre-2018 HMDA loan_amount is ALREADY in dollars and 100% exact thousands.
    # Post-2018's $10k-midpoint rounding destroys a factor of ten of real
    # precision here (county + $10k -> 0.8% clean 1:1; ZIP + $1k -> 26.7%).
    # NEVER reuse the post-2018 "Loan Amount Round" expression for this era.
    PRE2018_AMOUNT_GRANULARITY: int = 1000

    # --- pre-2018 era: input directories ----------------------------------
    # HMDA_SILVER_DIR (above) points at post2018 and is NOT re-pointed.
    HMDA_PRE2018_SILVER_DIR: Path = HMDAConfig.get_dataset_dir(
        "silver", "loans", "period_2007_2017"
    )
    HMDA_PRE2018_PANEL_DIR: Path = HMDAConfig.get_dataset_dir("silver", "panel", "period_2007_2017")

    # The shared respondent-key expression lives in
    # ``match_hmda_fha.lender_bridge`` (hmda_respondent_key). FHA lender keys
    # are normalized in silver and read directly. Release (file_type) selection
    # for either era belongs to ``hmda.load``.

    @classmethod
    def year_range_for_era(cls, era: Era) -> tuple[int, int]:
        """Resolve the requested year range for one era.

        Typer cannot make one option's default depend on another, so the CLI
        parses ``--min-year``/``--max-year`` as ``None`` and resolves them here.

        Args:
            era: ``"post2018"`` (the default era) or ``"pre2018"``.

        Returns:
            The inclusive ``(min_year, max_year)`` for that era.

        Raises:
            ValueError: If ``era`` is not one of the two known eras.
        """
        if era == "post2018":
            return cls.MIN_YEAR, cls.MAX_YEAR
        if era == "pre2018":
            return cls.PRE2018_MIN_YEAR, cls.PRE2018_MAX_YEAR
        raise ValueError(f"Unknown era: {era!r}. Expected 'post2018' or 'pre2018'.")

    @classmethod
    def crosswalk_name(
        cls, era: Era, min_year: int | None = None, max_year: int | None = None
    ) -> str:
        """The final crosswalk's file name for one era.

        Args:
            era: ``"post2018"`` or ``"pre2018"``.
            min_year: First year the run covered; with ``max_year``, a range other than
                the era's own adds a ``_{min}_{max}`` suffix, so a partial run never
                overwrites the full crosswalk.
            max_year: Last year the run covered.

        Returns:
            ``hmda_fha_crosswalk_{era}[_{min}_{max}].parquet``.
        """
        name = f"hmda_fha_crosswalk_{era}"
        if min_year is not None and max_year is not None:
            if (min_year, max_year) != cls.year_range_for_era(era):
                name += f"_{min_year}_{max_year}"
        return f"{name}.parquet"

    @classmethod
    def get_crosswalk_path(cls, era: Era) -> Path:
        """The era's final crosswalk under ``crosswalk/hmda_fha/``.

        Args:
            era: ``"post2018"`` or ``"pre2018"``.

        Returns:
            The crosswalk path.
        """
        return cls.HMDA_FHA_CROSSWALK_DIR / cls.crosswalk_name(era)

    @classmethod
    def get_bridge_path(cls, name: str) -> Path:
        """A pre-2018 lender-bridge artifact under ``HMDA_FHA_BRIDGE_DIR``.

        Args:
            name: Artifact stem (e.g., ``"hmda_fha_lender_bridge_pre2018"``).

        Returns:
            The artifact's parquet path.
        """
        return cls.HMDA_FHA_BRIDGE_DIR / f"{name}.parquet"

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary HMDA-FHA matching directories if they don't exist."""
        dirs = [
            cls.HMDA_FHA_MATCHING_DIR,
            cls.HMDA_FHA_OUTPUT_DIR,
            cls.HMDA_FHA_INTERMEDIATE_DIR,
            cls.HMDA_FHA_ZIP_TRACT_DIR,
            cls.HMDA_FHA_CROSSWALK_DIR,
        ]
        for dir_path in dirs:
            dir_path.mkdir(parents=True, exist_ok=True)
