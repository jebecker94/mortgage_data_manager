"""FNMA Data Manager - Modular Package.

A modular package for downloading, processing, and analyzing FNMA
(Fannie Mae) loan performance data files.

This package follows the project's medallion architecture:
    - raw: Downloaded ZIP files from Fannie Mae
    - bronze: Staged parquet files with proper schema
    - silver: Transformed, structured data (issuances, terminations)

Configuration:
    The FNMAConfig class inherits from MortgageDataConfig and provides
    FNMA-specific paths and constants. All paths can be overridden via
    environment variables (FNMA_DATA_DIR, FNMA_RAW_DIR, etc.).

Example:
    >>> from mortgage_data_manager.fnma import FNMAConfig
    >>> print(FNMAConfig.FNMA_RAW_DIR)     # Downloaded files
    >>> print(FNMAConfig.FNMA_BRONZE_DIR)  # Staged parquet
    >>> print(FNMAConfig.FNMA_SILVER_DIR)  # Transformed data
"""

# Import main configuration class
from .config import FNMAConfig

__all__ = [
    'FNMAConfig',
]
