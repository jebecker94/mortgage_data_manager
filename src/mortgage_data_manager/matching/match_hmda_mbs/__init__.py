"""HMDA-MBS matching workflow.

This package provides tools for building master crosswalks linking
HMDA loans through FHFA, MBS, and UMBS data for FNMA, FHLMC, and GNMA.

For GSE (FNMA/FHLMC) matching:
    mortgage-data match hmda-mbs run [--agency fnma|fhlmc|both] [--enrich]

For GNMA (FHA loans) matching, this package provides two methods:
1. Two-crosswalk: HMDA → FHA → GNMA (34.5% coverage)
2. Direct matching: HMDA → GNMA using probabilistic record linkage (+16% coverage)

Combined HMDA-GNMA coverage: ~50.7% of HMDA FHA loans

Direct HMDA-UMBS matching for unmatched GSE-sold loans:
    mortgage-data match hmda-mbs direct --agency both
"""

from mortgage_data_manager.matching.match_hmda_mbs.config import HMDAMBSMatchingConfig
from mortgage_data_manager.matching.match_hmda_mbs.direct_match import (
    load_unmatched_hmda,
    load_unmatched_umbs,
    run_direct_matching,
    validate_direct_matches,
)
from mortgage_data_manager.matching.match_hmda_mbs.fha_matching import (
    build_lei_issuer_crosswalk,
    build_two_crosswalk_chain,
    direct_match_fha,
    run_fha_matching_pipeline,
)
from mortgage_data_manager.matching.match_hmda_mbs.lender_seller_crosswalk import (
    build_lender_seller_crosswalk,
    generate_markdown_report,
    load_enriched_crosswalks,
)
from mortgage_data_manager.matching.match_hmda_mbs.validation import (
    compute_hmda_yearly_match_rates,
    compute_umbs_monthly_match_rates,
    load_combined_crosswalks,
    load_hmda_retail_gse_loans,
    load_umbs_data,
    run_validation,
)

__all__ = [
    "HMDAMBSMatchingConfig",
    # GSE crosswalk functions
    "build_lender_seller_crosswalk",
    "generate_markdown_report",
    "load_enriched_crosswalks",
    # FHA/GNMA matching functions
    "build_two_crosswalk_chain",
    "build_lei_issuer_crosswalk",
    "direct_match_fha",
    "run_fha_matching_pipeline",
    # Direct HMDA-UMBS matching functions
    "run_direct_matching",
    "load_unmatched_hmda",
    "load_unmatched_umbs",
    "validate_direct_matches",
    # Validation functions
    "run_validation",
    "compute_hmda_yearly_match_rates",
    "compute_umbs_monthly_match_rates",
    "load_combined_crosswalks",
    "load_hmda_retail_gse_loans",
    "load_umbs_data",
]
