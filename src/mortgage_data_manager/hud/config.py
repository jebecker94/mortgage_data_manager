"""Configuration for HUD USPS ZIP Code Crosswalk data.

This module provides the HUDConfig class that inherits from MortgageDataConfig
and adds HUD-specific configuration including crosswalk type definitions,
census vintage boundaries, and geographic code specifications.

Environment Variables:
    HUD_DATA_DIR: Root HUD data directory (default: {DATA_DIR}/hud)
    HUD_RAW_DIR: Raw HUD data directory (default: {HUD_DATA_DIR}/raw)
    HUD_BRONZE_DIR: Bronze HUD data directory (default: {HUD_DATA_DIR}/bronze)
    HUD_API_KEY: Bearer token for HUD API access

Example:
    >>> from mortgage_data_manager.hud.config import HUDConfig
    >>> HUDConfig.ensure_directories()
    >>> print(HUDConfig.HUD_RAW_DIR)
    /Users/username/mortgage_data_manager/data/hud/raw
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig

# =============================================================================
# Crosswalk Type Definitions
# =============================================================================


@dataclass(frozen=True)
class CrosswalkType:
    """Definition of a HUD crosswalk type."""

    type_id: int
    name: str
    start_year: int
    start_quarter: int
    source_geo: str
    target_geo: str

    @property
    def inverse_name(self) -> str:
        """Get the name of the inverse crosswalk (e.g., ZIP_TRACT -> TRACT_ZIP)."""
        return f"{self.target_geo}_{self.source_geo}"


# All 12 crosswalk types with their availability dates and geography mappings
CROSSWALK_TYPES: dict[int, CrosswalkType] = {
    1: CrosswalkType(1, "ZIP_TRACT", 2010, 1, "ZIP", "TRACT"),
    2: CrosswalkType(2, "ZIP_COUNTY", 2010, 1, "ZIP", "COUNTY"),
    3: CrosswalkType(3, "ZIP_CBSA", 2010, 1, "ZIP", "CBSA"),
    4: CrosswalkType(4, "ZIP_CBSA_DIV", 2017, 4, "ZIP", "CBSA_DIV"),
    5: CrosswalkType(5, "ZIP_CD", 2010, 1, "ZIP", "CD"),
    6: CrosswalkType(6, "TRACT_ZIP", 2010, 1, "TRACT", "ZIP"),
    7: CrosswalkType(7, "COUNTY_ZIP", 2010, 1, "COUNTY", "ZIP"),
    8: CrosswalkType(8, "CBSA_ZIP", 2010, 1, "CBSA", "ZIP"),
    9: CrosswalkType(9, "CBSA_DIV_ZIP", 2017, 4, "CBSA_DIV", "ZIP"),
    10: CrosswalkType(10, "CD_ZIP", 2010, 1, "CD", "ZIP"),
    11: CrosswalkType(11, "ZIP_COUNTY_SUB", 2018, 2, "ZIP", "COUNTY_SUB"),
    12: CrosswalkType(12, "COUNTY_SUB_ZIP", 2018, 2, "COUNTY_SUB", "ZIP"),
}

# Lookup by name for convenience
CROSSWALK_TYPES_BY_NAME: dict[str, CrosswalkType] = {ct.name: ct for ct in CROSSWALK_TYPES.values()}

# List of valid crosswalk type names
VALID_CROSSWALK_NAMES: list[str] = list(CROSSWALK_TYPES_BY_NAME.keys())


def get_crosswalk_type(identifier: int | str) -> CrosswalkType:
    """Get a crosswalk type by ID or name.

    Args:
        identifier: Either the type ID (1-12) or name (e.g., "ZIP_TRACT")

    Returns:
        The matching CrosswalkType

    Raises:
        ValueError: If the identifier is not valid
    """
    if isinstance(identifier, int):
        if identifier not in CROSSWALK_TYPES:
            raise ValueError(f"Invalid crosswalk type ID: {identifier}. Valid IDs: 1-12")
        return CROSSWALK_TYPES[identifier]
    else:
        if identifier not in CROSSWALK_TYPES_BY_NAME:
            raise ValueError(
                f"Invalid crosswalk type name: {identifier}. Valid names: {VALID_CROSSWALK_NAMES}"
            )
        return CROSSWALK_TYPES_BY_NAME[identifier]


def is_available(crosswalk: CrosswalkType | str | int, year: int, quarter: int) -> bool:
    """Check if a crosswalk type is available for a given year/quarter.

    Args:
        crosswalk: The crosswalk type (or its name/ID)
        year: Year to check
        quarter: Quarter to check (1-4)

    Returns:
        True if the crosswalk is available for this period
    """
    if not isinstance(crosswalk, CrosswalkType):
        crosswalk = get_crosswalk_type(crosswalk)

    if year < crosswalk.start_year:
        return False
    if year == crosswalk.start_year and quarter < crosswalk.start_quarter:
        return False
    return True


# =============================================================================
# Census Geography Versions
# =============================================================================

CensusVintage = Literal[2000, 2010, 2020]


@dataclass(frozen=True)
class CensusVintageRange:
    """Time range for a census geography vintage."""

    vintage: CensusVintage
    start_year: int
    start_quarter: int
    end_year: int
    end_quarter: int


# Census geography version boundaries
# Based on when HUD crosswalks transitioned between decennial census geographies
CENSUS_VINTAGES: list[CensusVintageRange] = [
    CensusVintageRange(2000, 2010, 1, 2011, 4),
    CensusVintageRange(2010, 2012, 1, 2022, 4),
    CensusVintageRange(2020, 2023, 1, 2099, 4),  # Open-ended
]


def get_census_vintage(year: int, quarter: int) -> CensusVintage:
    """Get the census geography vintage for a given year/quarter.

    Args:
        year: Year
        quarter: Quarter (1-4)

    Returns:
        The census vintage (2000, 2010, or 2020)

    Raises:
        ValueError: If the year/quarter is before 2010 Q1
    """
    for vintage in CENSUS_VINTAGES:
        start = (vintage.start_year, vintage.start_quarter)
        end = (vintage.end_year, vintage.end_quarter)
        current = (year, quarter)

        if start <= current <= end:
            return vintage.vintage

    raise ValueError(f"No census vintage defined for {year} Q{quarter}")


def get_vintage_year_range(vintage: CensusVintage) -> tuple[int, int, int, int]:
    """Get the year/quarter range for a census vintage.

    Args:
        vintage: The census vintage (2000, 2010, or 2020)

    Returns:
        Tuple of (start_year, start_quarter, end_year, end_quarter)

    Raises:
        ValueError: If the vintage is not valid
    """
    for v in CENSUS_VINTAGES:
        if v.vintage == vintage:
            return (v.start_year, v.start_quarter, v.end_year, v.end_quarter)

    raise ValueError(f"Invalid census vintage: {vintage}")


# =============================================================================
# Geographic Code Specifications
# =============================================================================

# Expected lengths for geographic identifier strings (with leading zeros)
GEO_CODE_LENGTHS: dict[str, int] = {
    "zip": 5,
    "tract": 11,  # 2-digit state + 3-digit county + 6-digit tract
    "county": 5,  # 2-digit state + 3-digit county
    "cbsa": 5,
    "cbsadiv": 5,
    "cd": 4,  # 2-digit state + 2-digit district
    "countysub": 10,  # 2-digit state + 3-digit county + 5-digit subdivision
}

# Columns that should always be treated as strings
STRING_COLUMNS: list[str] = [
    "zip",
    "tract",
    "geoid",
    "county",
    "cbsa",
    "cbsadiv",
    "cd",
    "countysub",
    "city",
    "state",
]

# Ratio columns (should be floats)
RATIO_COLUMNS: list[str] = [
    "res_ratio",
    "bus_ratio",
    "oth_ratio",
    "tot_ratio",
]


# =============================================================================
# HUD Configuration Class
# =============================================================================


class HUDConfig(MortgageDataConfig):
    """Configuration for HUD USPS ZIP Code Crosswalk data.

    This class inherits from MortgageDataConfig and provides HUD-specific
    configuration including medallion directories and API settings.
    """

    # Data directories
    HUD_DATA_DIR: Path = Path(
        config(
            "HUD_DATA_DIR",
            default=str(MortgageDataConfig.get_subpackage_data_dir("hud")),
        )
    )

    HUD_RAW_DIR: Path = Path(config("HUD_RAW_DIR", default=str(HUD_DATA_DIR / "raw")))

    HUD_BRONZE_DIR: Path = Path(config("HUD_BRONZE_DIR", default=str(HUD_DATA_DIR / "bronze")))

    HUD_SILVER_DIR: Path = Path(config("HUD_SILVER_DIR", default=str(HUD_DATA_DIR / "silver")))

    # API configuration
    HUD_API_BASE_URL: str = "https://www.huduser.gov/hudapi/public/usps"
    HUD_API_KEY: str = config("HUD_API_KEY", default="")
    HUD_REQUEST_DELAY: float = 1.0  # Seconds between API requests

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary HUD directories.

        Creates the raw, bronze, and silver directories for HUD data.

        Example:
            >>> HUDConfig.ensure_directories()
        """
        cls.HUD_RAW_DIR.mkdir(parents=True, exist_ok=True)
        cls.HUD_BRONZE_DIR.mkdir(parents=True, exist_ok=True)
        cls.HUD_SILVER_DIR.mkdir(parents=True, exist_ok=True)
