"""Geocoding toolkit for mortgage_data_manager.

A subpackage-agnostic geocoding API built on the free Census Geocoder. Backed
by a global on-disk cache keyed by normalized-address hash, so every address
is geocoded at most once across the project.
"""

from mortgage_data_manager.core.geocoding.api import (
    INPUT_COLUMNS,
    OUTPUT_COLUMNS,
    geocode_addresses,
)
from mortgage_data_manager.core.geocoding.cache import (
    cache_path,
    load_cache,
    purge_cache,
)

__all__ = [
    "INPUT_COLUMNS",
    "OUTPUT_COLUMNS",
    "cache_path",
    "geocode_addresses",
    "load_cache",
    "purge_cache",
]
