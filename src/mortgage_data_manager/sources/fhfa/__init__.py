"""Federal Housing Finance Agency (FHFA) public-use data processing."""

from mortgage_data_manager.sources.fhfa import import_bronze, import_silver, schemas
from mortgage_data_manager.sources.fhfa.config import (
    FHFAConfig,
    ImportOptions,
)

__all__ = [
    # Configuration
    "FHFAConfig",
    "ImportOptions",
    # Loaders
    "import_bronze",
    "import_silver",
    # Schema handling
    "schemas",
]
