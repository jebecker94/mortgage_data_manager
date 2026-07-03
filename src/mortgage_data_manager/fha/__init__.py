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
from .import_silver import (
    save_clean_snapshots_to_db,
    update_clean_snapshots_to_db,
)
from .pipeline import (
    bronze_hecm,
    bronze_single_family,
    import_hecm_snapshots,
    import_single_family_snapshots,
    run_pipeline,
    silver_hecm,
    silver_single_family,
)
from .utils import (
    add_county_fips,
    build_county_fips_crosswalk,
    create_lender_id_to_name_crosswalk,
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
    # Import CLI
    "import_hecm_snapshots",
    "import_single_family_snapshots",
    # Granular bronze/silver step helpers
    "bronze_hecm",
    "bronze_single_family",
    "silver_hecm",
    "silver_single_family",
    # Workflow CLI
    "run_pipeline",
    # Bronze layer (raw -> parquet)
    "clean_hecm_sheets",
    "convert_fha_hecm_snapshots",
    "convert_fha_sf_snapshots",
    # Silver layer (parquet -> enriched/partitioned)
    "save_clean_snapshots_to_db",
    "update_clean_snapshots_to_db",
    # Enrichment utilities
    "add_county_fips",
    "build_county_fips_crosswalk",
    "create_lender_id_to_name_crosswalk",
    "standardize_county_names",
]
