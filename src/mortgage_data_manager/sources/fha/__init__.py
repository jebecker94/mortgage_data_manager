"""Public package interface for FHA Data Manager utilities."""

from __future__ import annotations

from .config import FHAConfig
from .download import (
    HECM_SNAPSHOT_URL,
    SINGLE_FAMILY_SNAPSHOT_URL,
    download_hecm_snapshots,
    download_single_family_snapshots,
    find_month_in_string,
    find_years_in_string,
    process_zip_file,
    standardize_filename,
)
from .import_bronze import (
    clean_hecm_sheets,
    convert_fha_hecm_snapshots,
    convert_fha_sf_snapshots,
)
from .import_silver import build_silver_snapshots
from .pipeline import (
    run_bronze,
    run_import,
    run_pipeline,
    run_silver,
)
from .utils import (
    add_county_fips,
    standardize_county_names,
)

__all__ = [
    # Configuration
    "FHAConfig",
    # Download functionality
    "find_month_in_string",
    "find_years_in_string",
    "process_zip_file",
    "standardize_filename",
    # Download CLI
    "HECM_SNAPSHOT_URL",
    "SINGLE_FAMILY_SNAPSHOT_URL",
    "download_hecm_snapshots",
    "download_single_family_snapshots",
    # Pipeline stage entries
    "run_bronze",
    "run_silver",
    "run_import",
    "run_pipeline",
    # Bronze layer (raw -> parquet)
    "clean_hecm_sheets",
    "convert_fha_hecm_snapshots",
    "convert_fha_sf_snapshots",
    # Silver layer (parquet -> enriched/partitioned)
    "build_silver_snapshots",
    # Enrichment utilities
    "add_county_fips",
    "standardize_county_names",
]
