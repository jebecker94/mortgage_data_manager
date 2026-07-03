"""Matching workflows for linking records across mortgage datasets.

This package provides tools for matching and linking records across different
mortgage data sources:
- HMDA to MBS matching
- HMDA to FHA matching
- HMDA to FHLB matching
- HMDA to FHFA matching
- MBS to FHLB matching
- MBS to FHFA matching
- HMDA sellers and purchasers matching
- FNMA to HMDA via FHLB matching
"""

from __future__ import annotations

from mortgage_data_manager.matching.config import MatchingConfig

__all__ = ["MatchingConfig"]
