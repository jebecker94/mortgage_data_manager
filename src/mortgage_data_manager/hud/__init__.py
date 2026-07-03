"""HUD (Department of Housing and Urban Development) crosswalk data.

This subpackage provides tools for downloading, processing, and using
HUD USPS ZIP Code Crosswalk files, which map between ZIP codes and
various Census geographies (tracts, counties, CBSAs, etc.).
"""

from mortgage_data_manager.hud.allocate import (
    aggregate_to_tracts,
    aggregate_to_zips,
    allocate_to_tracts,
    allocate_to_zips,
)
from mortgage_data_manager.hud.config import (
    CROSSWALK_TYPES,
    CROSSWALK_TYPES_BY_NAME,
    VALID_CROSSWALK_NAMES,
    CensusVintage,
    CrosswalkType,
    HUDConfig,
    get_census_vintage,
    get_crosswalk_type,
    get_vintage_year_range,
    is_available,
)
from mortgage_data_manager.hud.load import (
    get_crosswalk_for_date,
    get_vintage_for_crosswalk,
    load_crosswalk,
    load_crosswalk_by_vintage,
)

__all__ = [
    # Config
    "HUDConfig",
    "CROSSWALK_TYPES",
    "CROSSWALK_TYPES_BY_NAME",
    "VALID_CROSSWALK_NAMES",
    "CensusVintage",
    "CrosswalkType",
    "get_census_vintage",
    "get_crosswalk_type",
    "get_vintage_year_range",
    "is_available",
    # Loading
    "get_crosswalk_for_date",
    "get_vintage_for_crosswalk",
    "load_crosswalk",
    "load_crosswalk_by_vintage",
    # Allocation
    "aggregate_to_tracts",
    "aggregate_to_zips",
    "allocate_to_tracts",
    "allocate_to_zips",
]
