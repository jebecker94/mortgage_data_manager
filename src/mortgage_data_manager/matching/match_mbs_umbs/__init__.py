"""MBS-UMBS Matching Module.

Matches MBS loan-level disclosure data to UMBS issuance data, per-GSE:

- FNMA-UMBS matching: Links Fannie Mae silver/issuances to UMBS FNM_ILLD data
  (fnma_match.py), using a two-round process (with then without MIP).
- FHLMC-UMBS matching: Links Freddie Mac bronze/origination to UMBS FRE_ILLD data
  (fhlmc_match.py), using a three-round process that relaxes MIP then drops credit
  score to handle the VantageScore 4.0 / Classic FICO transition.

Shared helpers combine matched issuances and merge the per-GSE crosswalks
(utils.py), and validation utilities assess match quality (validation.py).

Example - FNMA matching:
    >>> from mortgage_data_manager.matching.match_mbs_umbs import match_fnma_mbs_umbs
    >>> df = match_fnma_mbs_umbs(mbs_dir, umbs_dir, crosswalk_file, variable_file)

Example - FHLMC matching:
    >>> from mortgage_data_manager.matching.match_mbs_umbs import match_fhlmc_mbs_umbs
    >>> df = match_fhlmc_mbs_umbs(mbs_dir, umbs_dir, crosswalk_file, variable_file)
"""

from mortgage_data_manager.matching.match_mbs_umbs.config import (
    CROSSWALK_OUTPUT_DIR,
    DATA_DIR,
    OUTPUT_DIR,
    MBSUMBSConfig,
    get_fhlmc_config,
    get_fnma_config,
    get_umbs_bronze_dir,
)
from mortgage_data_manager.matching.match_mbs_umbs.fhlmc_match import match_fhlmc_mbs_umbs
from mortgage_data_manager.matching.match_mbs_umbs.fnma_match import (
    explore_fnma_credit_scores,
    match_fnma_mbs_umbs,
)
from mortgage_data_manager.matching.match_mbs_umbs.utils import (
    combine_crosswalks,
    combine_issuances,
)
from mortgage_data_manager.matching.match_mbs_umbs.validation import run_validation

__all__ = [
    # Config
    "MBSUMBSConfig",
    "DATA_DIR",
    "OUTPUT_DIR",
    "CROSSWALK_OUTPUT_DIR",
    "get_fhlmc_config",
    "get_fnma_config",
    "get_umbs_bronze_dir",
    # FNMA matching
    "match_fnma_mbs_umbs",
    "explore_fnma_credit_scores",
    # FHLMC matching
    "match_fhlmc_mbs_umbs",
    # Shared utilities
    "combine_issuances",
    "combine_crosswalks",
    # Validation
    "run_validation",
]
