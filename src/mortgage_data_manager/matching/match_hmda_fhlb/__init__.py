"""HMDA-FHLB Matching Package.

This package provides tools for matching HMDA (Home Mortgage Disclosure Act) loan
records to FHLB (Federal Home Loan Bank) acquired member asset data.

The matching process identifies loans that were originated by FHLB member institutions
and subsequently sold or pledged to FHLBs.

Example Usage
-------------
>>> from mortgage_data_manager.matching.match_hmda_fhlb import run_matching
>>> run_matching(max_year=2024)

>>> from mortgage_data_manager.matching.match_hmda_fhlb import HMDAFHLBMatchingConfig
>>> print(HMDAFHLBMatchingConfig.HMDA_FHLB_MATCHING_DIR)
"""

from mortgage_data_manager.matching.match_hmda_fhlb.config import HMDAFHLBMatchingConfig
from mortgage_data_manager.matching.match_hmda_fhlb.pipeline import run_matching

__all__ = [
    # Configuration
    "HMDAFHLBMatchingConfig",
    # Matching
    "run_matching",
]
