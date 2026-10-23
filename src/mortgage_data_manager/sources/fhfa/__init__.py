"""Federal Housing Finance Agency (FHFA) public-use data processing."""

from mortgage_data_manager.sources.fhfa import import_bronze, import_silver, schemas
from mortgage_data_manager.sources.fhfa.config import (
    BRONZE_DIR,
    DATA_DIR,
    PROJECT_DIR,
    RAW_DIR,
    SCHEMAS_DIR,
    SILVER_DIR,
    FHFAConfig,
    ImportOptions,
    PathsConfig,
    ensure_parent_dir,
)

__all__ = [
    # Version
    # Path constants
    "PROJECT_DIR",
    "DATA_DIR",
    "RAW_DIR",
    "BRONZE_DIR",
    "SILVER_DIR",
    "SCHEMAS_DIR",
    # Configuration
    "FHFAConfig",
    "PathsConfig",
    "ImportOptions",
    "ensure_parent_dir",
    # Loaders
    "import_bronze",
    "import_silver",
    # Schema handling
    "schemas",
]
