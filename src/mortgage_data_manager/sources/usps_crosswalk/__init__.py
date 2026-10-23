"""USPS ZIP Code crosswalk data.

This subpackage provides tools for downloading, processing, and using the
USPS ZIP Code Crosswalk files (published by HUD), which map between ZIP
codes and various Census geographies (tracts, counties, CBSAs, etc.).
"""

from mortgage_data_manager.sources.usps_crosswalk.config import (
    CROSSWALK_TYPES,
    CROSSWALK_TYPES_BY_NAME,
    VALID_CROSSWALK_NAMES,
    CensusVintage,
    CrosswalkType,
    USPSCrosswalkConfig,
    get_census_vintage,
    get_vintage_year_range,
)
from mortgage_data_manager.sources.usps_crosswalk.load import (
    get_crosswalk_for_date,
    load_crosswalk,
    load_crosswalk_by_vintage,
)

__all__ = [
    # Config
    "USPSCrosswalkConfig",
    "CROSSWALK_TYPES",
    "CROSSWALK_TYPES_BY_NAME",
    "VALID_CROSSWALK_NAMES",
    "CensusVintage",
    "CrosswalkType",
    "get_census_vintage",
    "get_vintage_year_range",
    # Loading
    "get_crosswalk_for_date",
    "load_crosswalk",
    "load_crosswalk_by_vintage",
]
