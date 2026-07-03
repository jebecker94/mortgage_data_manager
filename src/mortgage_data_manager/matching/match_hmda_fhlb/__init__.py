"""HMDA-FHLB Matching Package.

This package provides tools for matching HMDA (Home Mortgage Disclosure Act) loan
records to FHLB (Federal Home Loan Bank) acquired member asset data.

The matching process identifies loans that were originated by FHLB member institutions
and subsequently sold or pledged to FHLBs.

Example Usage
-------------
>>> from mortgage_data_manager.matching.match_hmda_fhlb import run_pipeline
>>> run_pipeline(max_year=2024)

>>> from mortgage_data_manager.matching.match_hmda_fhlb import MatchHMDAFHLBConfig
>>> print(MatchHMDAFHLBConfig.DATA_DIR)
"""

from .config import (
    DATA_DIR,
    MATCH_SETTINGS,
    MatchHMDAFHLBConfig,
)
from .workflows import (
    run_cleaning_only,
    run_matching_only,
    run_pipeline,
    run_post2018_pipeline,
    run_pre2018_pipeline,
)

__all__ = [
    # Configuration
    "MatchHMDAFHLBConfig",
    "DATA_DIR",
    "MATCH_SETTINGS",
    # Workflows
    "run_pipeline",
    "run_pre2018_pipeline",
    "run_post2018_pipeline",
    "run_cleaning_only",
    "run_matching_only",
]
