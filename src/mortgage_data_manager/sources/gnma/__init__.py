#!/usr/bin/env python3
"""Ginnie Mae (GNMA) disclosure data processing.

A modular package for downloading, processing, and analyzing GNMA
(Government National Mortgage Association) data files.

This package follows the project's medallion architecture:
    - raw: Downloaded ZIP/TXT files from GNMA bulk data portal
    - bronze: Staged parquet files with text_content column
    - silver: Transformed, structured data built from the canonical schema

Configuration:
    The GNMAConfig class inherits from MortgageDataConfig and provides
    GNMA-specific paths and constants. All paths can be overridden via
    environment variables (GNMA_DATA_DIR, GNMA_RAW_DIR, etc.).

Modules:
    - download: Data and layout-PDF downloading to raw layer
    - canonical: Canonical JSON schemas (arrow_type + decode) + bronze validator
    - import_bronze: Data staging (raw → bronze)
    - import_silver: Data transformation (bronze → silver) via the canonical schema
    - performance: Performance data loading and analysis from silver layer
    - config: GNMAConfig class and configuration dataclasses
    - utils: Shared utilities, result classes, and exceptions

Example:
    >>> from mortgage_data_manager.sources.gnma import GNMAConfig
    >>> print(GNMAConfig.GNMA_RAW_DIR)     # Downloaded files
    >>> print(GNMAConfig.GNMA_BRONZE_DIR)  # Staged parquet
    >>> print(GNMAConfig.GNMA_SILVER_DIR)  # Transformed data

"""

# Import main configuration classes
from .config import DownloadConfig, GNMAConfig, ProcessorConfig

# Import key functions from submodules
from .download import (
    create_session,
    download_all_data,
    download_all_schemas,
    download_data_file,
    download_prefix_data,
    download_prefix_schemas,
    download_schema_file,
    find_data_links,
    find_schema_links,
)

# Import bronze layer functions (raw → bronze)
from .import_bronze import (
    import_all_bronze,
    import_prefix_bronze,
    import_txt_to_parquet,
    import_zip_to_parquet,
)

# Import silver layer functions (bronze → silver, via the canonical schema)
from .import_silver import (
    import_file_silver_canonical,
    import_prefix_silver,
    import_prefix_silver_canonical,
)
from .performance import create_upb_time_series, get_summary_statistics, load_performance_data
from .pipeline import (
    print_pipeline_summary,
    run_analysis,
    run_download,
    run_pipeline,
    run_processing,
)

# Import utils: result classes, exceptions, and utility functions
from .utils import (
    ConversionResult,
    DateFormatError,
    DownloadError,
    ProcessingResult,
    create_default_configs,
    load_prefix_dictionary,
)

__all__ = [
    # Configuration
    "GNMAConfig",
    "DownloadConfig",
    "ProcessorConfig",
    # Result classes
    "ConversionResult",
    "ProcessingResult",
    # Exceptions
    "DateFormatError",
    "DownloadError",
    # Download functions
    "create_session",
    "download_data_file",
    "download_prefix_data",
    "download_all_data",
    "download_schema_file",
    "download_prefix_schemas",
    "download_all_schemas",
    "find_data_links",
    "find_schema_links",
    # Performance functions
    "load_performance_data",
    "create_upb_time_series",
    "get_summary_statistics",
    # Pipeline functions
    "run_download",
    "run_analysis",
    "run_processing",
    "run_pipeline",
    "print_pipeline_summary",
    # Utils functions
    "load_prefix_dictionary",
    "create_default_configs",
    # Bronze layer functions (raw → bronze)
    "import_zip_to_parquet",
    "import_txt_to_parquet",
    "import_prefix_bronze",
    "import_all_bronze",
    # Silver layer functions (bronze → silver, canonical)
    "import_prefix_silver",
    "import_prefix_silver_canonical",
    "import_file_silver_canonical",
]
