"""Configuration for HUD USPS ZIP Code Crosswalk data.

This module provides the USPSCrosswalkConfig class that inherits from MortgageDataConfig
and adds USPS-crosswalk-specific configuration including crosswalk type definitions,
census vintage boundaries, and geographic code specifications.

Layers: this source stops at **bronze** — it has no silver. The published files
need no cleaning beyond the schema enforcement bronze already applies (the only
drift across 2010-2025 is ``city``/``state`` appearing from 2021, absorbed by
``load.py``), so a silver layer would only restate bronze. Read across quarters
with ``load_crosswalk()`` / ``load_crosswalk_by_vintage()``; derived assets are
promoted to ``core.reference`` (e.g. ``tract_zip3``).

Environment Variables:
    USPS_CROSSWALK_DATA_DIR: Root USPS crosswalk data directory (default: {DATA_DIR}/usps_crosswalk)
    USPS_CROSSWALK_RAW_DIR: Raw USPS crosswalk data directory (default: {USPS_CROSSWALK_DATA_DIR}/raw)
    USPS_CROSSWALK_BRONZE_DIR: Bronze USPS crosswalk data directory (default: {USPS_CROSSWALK_DATA_DIR}/bronze)
    USPS_CROSSWALK_API_KEY: Bearer token for HUD API access

Example:
    >>> from mortgage_data_manager.sources.usps_crosswalk.config import USPSCrosswalkConfig
    >>> USPSCrosswalkConfig.ensure_directories()
    >>> print(USPSCrosswalkConfig.USPS_CROSSWALK_RAW_DIR)
    /Users/username/mortgage_data_manager/data/usps_crosswalk/raw
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig

# =============================================================================
# Crosswalk Type Definitions
# =============================================================================


@dataclass(frozen=True)
class CrosswalkType:
    """Definition of a USPS crosswalk type."""

    type_id: int
    name: str
    start_year: int
    start_quarter: int
    source_geo: str
    target_geo: str


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


# =============================================================================
# Quarter arithmetic
# =============================================================================

#: Quarters HUD runs behind the current one. Observed 2026-08-01 (in Q3): 2026
#: Q1 was published and Q2 still 404'd.
PUBLICATION_LAG_QUARTERS = 2


def _index(year: int, quarter: int) -> int:
    """Quarters since year 0, so ranges cross year boundaries without special cases."""
    return year * 4 + quarter - 1


def _unindex(index: int) -> tuple[int, int]:
    return index // 4, index % 4 + 1


def current_quarter(now: datetime | None = None) -> tuple[int, int]:
    """The calendar quarter containing ``now``.

    Args:
        now: Reference time; defaults to the current local time.

    Returns:
        The ``(year, quarter)`` containing ``now``.
    """
    now = now or datetime.now()
    return now.year, (now.month - 1) // 3 + 1


def latest_expected_quarter(now: datetime | None = None) -> tuple[int, int]:
    """Most recent quarter HUD is likely to have *published* by ``now``.

    Downloading probes optimistically through `current_quarter` — that is
    how a new quarter gets discovered. Validation uses this lagged horizon
    instead, so it does not report a quarter as missing before HUD ships it.

    Args:
        now: Reference time; defaults to the current local time.

    Returns:
        The most recent ``(year, quarter)`` HUD is likely to have published.
    """
    return _unindex(_index(*current_quarter(now)) - PUBLICATION_LAG_QUARTERS)


def quarters_for(
    crosswalk_type: str,
    through: tuple[int, int],
    since: tuple[int, int] | None = None,
) -> list[tuple[int, int]]:
    """Every ``(year, quarter)`` this crosswalk type should cover.

    Runs from the type's own first published quarter (types 4/9 start 2017 Q4,
    11/12 start 2018 Q2) through ``through`` inclusive, optionally clipped to
    start at ``since``.

    Args:
        crosswalk_type: Crosswalk type name.
        through: Inclusive upper bound as ``(year, quarter)``.
        since: Inclusive lower bound, overriding the type's own first quarter.

    Returns:
        Every ``(year, quarter)`` the type should cover, in order.
    """
    crosswalk = CROSSWALK_TYPES_BY_NAME[crosswalk_type]
    start = _index(crosswalk.start_year, crosswalk.start_quarter)
    if since is not None:
        start = max(start, _index(*since))
    return [_unindex(i) for i in range(start, _index(*through) + 1)]


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

# Columns bronze zero-pads, and the width to pad to.
#
# Deliberately omits the source columns of CBSA_DIV_ZIP and COUNTY_SUB_ZIP.
# They were listed here as "cbsadiv"/"countysub", which never matched the real
# column names ("cbsa_div"/"county_sub"), so neither has ever been padded — and
# padding them now would do damage rather than repair:
#   - cbsa_div values are all 5 chars already (division codes carry no leading
#     zeros), so padding is a no-op.
#   - county_sub has 125 short values across the series, all in outlying
#     territories (69 = N. Mariana Is., 60 = American Samoa, 64/68/70 =
#     FSM/Marshalls/Palau). They are truncated upstream, not missing leading
#     zeros: "64".zfill(10) is "0000000064", which reads as state 00, a
#     different geography. Leave them as published.
GEO_CODE_LENGTHS: dict[str, int] = {
    "zip": 5,
    "tract": 11,  # 2-digit state + 3-digit county + 6-digit tract
    "county": 5,  # 2-digit state + 3-digit county
    "cbsa": 5,
    "cd": 4,  # 2-digit state + 2-digit district
}

# Ratio columns (should be floats)
RATIO_COLUMNS: list[str] = [
    "res_ratio",
    "bus_ratio",
    "oth_ratio",
    "tot_ratio",
]


# =============================================================================
# USPS Crosswalk Configuration Class
# =============================================================================


class USPSCrosswalkConfig(MortgageDataConfig):
    """Configuration for HUD USPS ZIP Code Crosswalk data.

    This class inherits from MortgageDataConfig and provides USPS-crosswalk-specific
    configuration including medallion directories and API settings.
    """

    # Data directories
    USPS_CROSSWALK_DATA_DIR: Path = Path(
        config(
            "USPS_CROSSWALK_DATA_DIR",
            default=str(MortgageDataConfig.get_subpackage_data_dir("usps_crosswalk")),
        )
    )

    USPS_CROSSWALK_RAW_DIR: Path = Path(
        config("USPS_CROSSWALK_RAW_DIR", default=str(USPS_CROSSWALK_DATA_DIR / "raw"))
    )

    USPS_CROSSWALK_BRONZE_DIR: Path = Path(
        config("USPS_CROSSWALK_BRONZE_DIR", default=str(USPS_CROSSWALK_DATA_DIR / "bronze"))
    )

    # API configuration
    USPS_CROSSWALK_API_BASE_URL: str = "https://www.huduser.gov/hudapi/public/usps"
    USPS_CROSSWALK_API_KEY: str = config("USPS_CROSSWALK_API_KEY", default="")
    USPS_CROSSWALK_REQUEST_DELAY: float = 1.0  # Seconds between API requests

    @classmethod
    def raw_path(cls, crosswalk_type: str, year: int, quarter: int) -> Path:
        """Raw JSON for one type-quarter (the untouched API response).

        Args:
            crosswalk_type: Crosswalk type name.
            year: Four-digit year.
            quarter: Quarter number, 1-4.

        Returns:
            The raw JSON path for that type-quarter.
        """
        return (
            cls.USPS_CROSSWALK_RAW_DIR / crosswalk_type / f"{crosswalk_type}_{year}_Q{quarter}.json"
        )

    @classmethod
    def bronze_path(cls, crosswalk_type: str, year: int, quarter: int) -> Path:
        """Bronze parquet for one type-quarter.

        Args:
            crosswalk_type: Crosswalk type name.
            year: Four-digit year.
            quarter: Quarter number, 1-4.

        Returns:
            The bronze parquet path for that type-quarter.
        """
        return (
            cls.USPS_CROSSWALK_BRONZE_DIR
            / crosswalk_type
            / f"{crosswalk_type}_{year}_Q{quarter}.parquet"
        )

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary USPS crosswalk directories.

        Creates the raw and bronze directories for USPS crosswalk data. This
        source has no silver layer — see the module docstring.

        Example:
            >>> USPSCrosswalkConfig.ensure_directories()
        """
        cls.USPS_CROSSWALK_RAW_DIR.mkdir(parents=True, exist_ok=True)
        cls.USPS_CROSSWALK_BRONZE_DIR.mkdir(parents=True, exist_ok=True)
