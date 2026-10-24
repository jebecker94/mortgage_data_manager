"""HMDA-GSE matching: HMDA conventional originations linked to GSE loan-level records.

Two routes to the same FNMA and FHLMC targets:

- The chain (``matching_chain``): HMDA -> FHFA -> MBS -> UMBS, composed per enterprise
  from the hmda_fhfa, fhfa_mbs and mbs_umbs crosswalks.
- The direct match (``matching_direct``): HMDA straight to the combined loan-issuance
  spine, with no FHFA leg and no chain input. ``lender_names`` supplies every name an
  LEI is known by on the HMDA side, which it aligns to GSE seller names.

``validation`` reports the conventional edge both compose; ``lender_seller_crosswalk``
documents lenders whose HMDA and GSE seller names differ.

    mortgage-data match hmda-gse run [--route chain|direct|all]
"""

from mortgage_data_manager.matching.match_hmda_gse.config import HMDAGSEMatchingConfig
from mortgage_data_manager.matching.match_hmda_gse.lender_seller_crosswalk import (
    build_lender_seller_crosswalk,
    load_enriched_crosswalks,
)
from mortgage_data_manager.matching.match_hmda_gse.matching_chain import run_matching_chain
from mortgage_data_manager.matching.match_hmda_gse.matching_direct import run_matching_direct
from mortgage_data_manager.matching.match_hmda_gse.validation import (
    compute_hmda_yearly_match_rates,
    compute_umbs_monthly_match_rates,
    load_chain_crosswalk,
    load_conventional_edge,
    load_direct_increment,
    load_hmda_retail_gse_loans,
    load_umbs_data,
    run_validation,
)

__all__ = [
    "HMDAGSEMatchingConfig",
    # GSE crosswalk functions
    "build_lender_seller_crosswalk",
    "load_enriched_crosswalks",
    # Matching
    "run_matching_chain",
    "run_matching_direct",
    # Validation functions
    "run_validation",
    "compute_hmda_yearly_match_rates",
    "compute_umbs_monthly_match_rates",
    "load_chain_crosswalk",
    "load_conventional_edge",
    "load_direct_increment",
    "load_hmda_retail_gse_loans",
    "load_umbs_data",
]
