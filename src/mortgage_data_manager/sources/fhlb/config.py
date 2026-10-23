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
    >>> from mortgage_data_manager.sources.fhlb.config import FHLBConfig
    >>> FHLBConfig.ensure_directories()
    >>> print(FHLBConfig.FHLB_RAW_DIR)
    /Users/username/mortgage_data_manager/data/fhlb/raw
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal

from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig


class FHLBConfig(MortgageDataConfig):
    """Configuration for Federal Home Loan Bank data.

    This class inherits from MortgageDataConfig and provides FHLB-specific
    configuration including medallion directories and data processing constants.
    """

    # Data directories
    FHLB_DATA_DIR: Path = Path(
        config("FHLB_DATA_DIR", default=str(MortgageDataConfig.get_subpackage_data_dir("fhlb")))
    )

    FHLB_RAW_DIR: Path = Path(config("FHLB_RAW_DIR", default=str(FHLB_DATA_DIR / "raw")))

    FHLB_BRONZE_DIR: Path = Path(config("FHLB_BRONZE_DIR", default=str(FHLB_DATA_DIR / "bronze")))

    FHLB_SILVER_DIR: Path = Path(config("FHLB_SILVER_DIR", default=str(FHLB_DATA_DIR / "silver")))

    FHLB_GOLD_DIR: Path = Path(config("FHLB_GOLD_DIR", default=str(FHLB_DATA_DIR / "gold")))

    # Schema directory
    FHLB_SCHEMAS_DIR: Path = Path(
        config("FHLB_SCHEMAS_DIR", default=str(MortgageDataConfig.SCHEMAS_DIR / "fhlb"))
    )

    # Raw subdirectories
    FHLB_RAW_MEMBERS_DIR: Path = FHLB_RAW_DIR / "members"
    FHLB_RAW_AMA_DIR: Path = FHLB_RAW_DIR / "ama"

    # Data processing defaults for members
    FHLB_DEFAULT_MIN_YEAR: int = 2009
    FHLB_DEFAULT_MAX_YEAR: int = 2026

    # AMA-specific year range (AMA data available from 2009-2025)
    FHLB_DEFAULT_AMA_MIN_YEAR: int = 2009
    FHLB_DEFAULT_AMA_MAX_YEAR: int = 2025

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

    @classmethod
    def default_years(cls, dataset: str) -> tuple[int, int]:
        """Default ``(min_year, max_year)`` for a dataset.

        AMA and members have different published ranges, so a request that spans
        both resolves each independently rather than forcing one range on both.

        Args:
            dataset: A key of `BRONZE_DATASETS`.

        Returns:
            The dataset's inclusive default year bounds.
        """
        if dataset == "ama":
            return cls.FHLB_DEFAULT_AMA_MIN_YEAR, cls.FHLB_DEFAULT_AMA_MAX_YEAR
        return cls.FHLB_DEFAULT_MIN_YEAR, cls.FHLB_DEFAULT_MAX_YEAR


# ----------------------------------------------------------------------------- #
# Dataset registries
# ----------------------------------------------------------------------------- #
# Each medallion stage covers a different set: dictionaries are downloaded but
# never built, and only AMA reaches silver.

#: Download datasets -> what each fetches.
DOWNLOAD_DATASETS: Final[dict[str, str]] = {
    "ama": "Acquired Member Assets loan-level CSVs (FHFA public-use database).",
    "members": "Quarterly FHLB member-institution Excel files.",
    "dictionaries": "AMA data-dictionary PDFs/Excel (schema documentation only).",
}

#: Bronze datasets -> what each imports. Dictionaries are documentation, not data.
BRONZE_DATASETS: Final[dict[str, str]] = {
    "ama": "AMA CSVs -> parquet with standardized column names.",
    "members": "Member Excel files -> one parquet per quarter.",
}

#: Silver datasets -> what each transforms. Members has no silver builder yet.
SILVER_DATASETS: Final[dict[str, str]] = {
    "ama": "AMA data-quality transformations (percent/income units, tracts, sentinels).",
}

_STAGE_REGISTRIES: Final[dict[str, dict[str, str]]] = {
    "download": DOWNLOAD_DATASETS,
    "bronze": BRONZE_DATASETS,
    "silver": SILVER_DATASETS,
}

#: Sorted list views (single source: the registries above).
VALID_DOWNLOAD_DATASETS: Final[list[str]] = list(DOWNLOAD_DATASETS)


def resolve_datasets(
    datasets: list[str] | None,
    *,
    stage: Literal["download", "bronze", "silver"],
) -> list[str]:
    """Default to every dataset for a stage; validate any explicit subset.

    Args:
        datasets: Dataset identifiers, or ``None`` for all of the stage's registry.
        stage: Which registry to resolve against.

    Returns:
        Validated list of dataset identifiers.

    Raises:
        ValueError: If an unknown dataset identifier is given.
    """
    registry = _STAGE_REGISTRIES[stage]
    if datasets is None:
        return list(registry)
    unknown = [d for d in datasets if d not in registry]
    if unknown:
        raise ValueError(f"Unknown FHLB {stage} datasets: {unknown}; valid: {list(registry)}")
    return list(datasets)
