"""HMDA-GNMA matching: HMDA government originations linked to GNMA loan-level disclosures.

Two loan populations, one module and one crosswalk each:

- FHA (``matching_fha``): an FHA-enriched direct match -- each FHA-matched HMDA loan
  carries its FHA record's exact amount and endorsement month through the HMDA-FHA
  crosswalk -- scored on units, borrowers, MIP-aware CLTV, month, LEI-issuer dyads and DTI.
- VA/USDA (``matching_va_usda``): a scored direct match with no administrative bridge,
  rounds in ``rounds.py``.

``triangulation`` is the validation gauge for both crosswalks: agreement of the
aggregator's HMDA name with the GNMA pool issuer's (``hmda-gnma validate``).

Example:
    >>> from mortgage_data_manager.matching.match_hmda_gnma import run_matching_fha
    >>> counts = run_matching_fha(min_year=2020, max_year=2024)
"""

from mortgage_data_manager.matching.match_hmda_gnma.config import HMDAGNMAMatchingConfig
from mortgage_data_manager.matching.match_hmda_gnma.matching_fha import (
    build_lei_issuer_dyads,
    run_matching_fha,
)
from mortgage_data_manager.matching.match_hmda_gnma.matching_va_usda import (
    run_matching_va_usda,
)

__all__ = [
    "HMDAGNMAMatchingConfig",
    "build_lei_issuer_dyads",
    "run_matching_fha",
    "run_matching_va_usda",
]
