"""FHFA Data Manager - Tools for managing public data from FHFA."""

from mortgage_data_manager.fhfa import import_bronze, import_silver, schemas
from mortgage_data_manager.fhfa.config import (
    BRONZE_DIR,
    DATA_DIR,
    PROJECT_DIR,
    RAW_DIR,
    SCHEMAS_DIR,
    SILVER_DIR,
    ImportOptions,
    PathsConfig,
    ensure_parent_dir,
)

__all__ = [
    # Version
    # Path constants
    'PROJECT_DIR',
    'DATA_DIR',
    'RAW_DIR',
    'BRONZE_DIR',
    'SILVER_DIR',
    'SCHEMAS_DIR',
    # Configuration
    'PathsConfig',
    'ImportOptions',
    'ensure_parent_dir',
    # Loaders
    'import_bronze',
    'import_silver',
    # Schema handling
    'schemas',
]

