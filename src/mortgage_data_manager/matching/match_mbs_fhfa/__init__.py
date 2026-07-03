"""MBS-FHFA Matching Module.

Matches MBS loan-level data (FHLMC, FNMA) with FHFA acquisition data.

FNMA-FHFA matching: ~91% FNMA / ~89% FHFA (2019-2024), uses exact rate matching.
FHLMC-FHFA matching: ~77% FHLMC / ~78% FHFA (2019-2024), uses near-exact rate
    matching (FHFA rounds Freddie Mac rates to hundredths).

Both modules use lazy evaluation (LazyFrames) for memory efficiency.
"""

from mortgage_data_manager.matching.match_mbs_fhfa.config import (
    DATA_DIR,
    OUTPUT_DIR,
    MBSFHFAConfig,
    get_fhfa_config,
    get_fhlmc_config,
    get_fnma_config,
)
from mortgage_data_manager.matching.match_mbs_fhfa.matching_fhlmc import (
    FHLMC_DEFAULT_TOLERANCES,
    get_md_to_cbsa,
    is_bin_edge,
    match_fhfa_fhlmc,
    round_to_fhfa_bin,
    run_fhlmc_fhfa_matching,
    run_fhlmc_fhfa_matching_multi_year,
)
from mortgage_data_manager.matching.match_mbs_fhfa.matching_fnma import (
    DEFAULT_TOLERANCES,
    MatchingTolerances,
    build_dti_match_expr,
    match_fhfa_fnma,
    run_fnma_fhfa_matching,
)

__all__ = [
    # Config
    "DATA_DIR",
    "OUTPUT_DIR",
    "MBSFHFAConfig",
    "get_fhlmc_config",
    "get_fnma_config",
    "get_fhfa_config",
    # FHLMC matching
    "match_fhfa_fhlmc",
    "run_fhlmc_fhfa_matching",
    "run_fhlmc_fhfa_matching_multi_year",
    "FHLMC_DEFAULT_TOLERANCES",
    "get_md_to_cbsa",
    "round_to_fhfa_bin",
    "is_bin_edge",
    # FNMA matching
    "match_fhfa_fnma",
    "run_fnma_fhfa_matching",
    "MatchingTolerances",
    "DEFAULT_TOLERANCES",
    "build_dti_match_expr",
]
