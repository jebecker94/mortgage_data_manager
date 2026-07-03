"""HMDA Data Manager.

Tools for managing CFPB's Home Mortgage Disclosure Act (HMDA) data for research.

This package provides functionality for:
- Downloading HMDA data files from CFPB
- Importing and processing loan-level (LAR) data
- Importing panel and transmittal sheet data
- Creating summary statistics and reports
- Data cleaning and validation utilities

Main modules:
- core: Core data import and download functionality
- utils: Utility functions for data processing and cleaning

Example usage:
    >>> # Import and download functions
    >>> from mortgage_data_manager.hmda import download_hmda_files
    >>> from mortgage_data_manager.hmda import build_bronze_post2018, build_silver_post2018
    >>>
    >>> # Download data for recent years
    >>> download_hmda_files(range(2020, 2025))
    >>>
    >>> # Build bronze and silver layers
    >>> build_bronze_post2018("loans", min_year=2020, max_year=2024)
    >>> build_silver_post2018("loans", min_year=2020, max_year=2024)
    >>>
    >>> # Or run a full per-era import pipeline
    >>> from mortgage_data_manager.hmda import import_post2018, run_pipeline
    >>> import_post2018(min_year=2020, max_year=2024)
    >>> run_pipeline(eras=["post2018"])

Note:
    The HMDAIndex variable is automatically created for post2018 data to provide
    unique identifiers across HMDA releases. Format: YYYYt_######### where YYYY is
    the year, t is the file type code, and # is the zero-padded row number.

    For loan matching functionality (matching originations to purchases), see the
    separate hmda-matching project.
"""

from __future__ import annotations

__author__ = "Jonathan E. Becker"

# Import workflow functions for public API
from .config import HMDAConfig

# Import download functions
from .download import download_hmda_files

# Import core data processing functions
from .import_bronze import (
    build_bronze_period_2007_2017,
    build_bronze_post2018,
    build_bronze_pre2007,
)
from .import_silver import (
    build_silver_period_2007_2017,
    build_silver_post2018,
    build_silver_pre2007,
)
from .pipeline import (
    bronze_2007_2017,
    bronze_post2018,
    bronze_pre2007,
    import_2007_2017,
    import_post2018,
    import_pre2007,
    run_download,
    run_pipeline,
    silver_2007_2017,
    silver_post2018,
    silver_pre2007,
)

__all__ = [
    "__author__",
    # Configuration
    "HMDAConfig",
    # Pipeline functions
    "run_download",
    "bronze_post2018",
    "bronze_2007_2017",
    "bronze_pre2007",
    "silver_post2018",
    "silver_2007_2017",
    "silver_pre2007",
    "import_post2018",
    "import_2007_2017",
    "import_pre2007",
    "run_pipeline",
    # Core import functions
    "build_bronze_post2018",
    "build_silver_post2018",
    "build_bronze_period_2007_2017",
    "build_silver_period_2007_2017",
    "build_bronze_pre2007",
    "build_silver_pre2007",
    # Download functions
    "download_hmda_files",
]

