"""MBS-UMBS Matching Module.

Matches MBS loan-level disclosure data to UMBS issuance data, per-GSE:

- FNMA-UMBS matching: Links Fannie Mae silver/issuances to UMBS FNM_ILLD data.
- FHLMC-UMBS matching: Links Freddie Mac silver/origination to UMBS FRE_ILLD data.

Both use a four-round process: relax MIP, then drop credit score to handle the
VantageScore 4.0 / Classic FICO transition, then resolve the leftovers' contested
candidate groups on exactness over LTV/CLTV/DTI/Term (ties dropped, never broken).
The rounds are one engine and one round table (matching.py), with a spec per GSE; the shared load
side (rename maps, date window, standardized frame loading, the core-exactness score)
lives in utils.py, dtype/encoding normalization in inputs.py,
run metrics and the drift/staleness guards in metrics.py, and validation utilities in
validation.py.

Example:
    >>> from mortgage_data_manager.matching.match_mbs_umbs import run_matching
    >>> df = run_matching("fnma")
"""

from mortgage_data_manager.matching.match_mbs_umbs.config import MBSUMBSMatchingConfig
from mortgage_data_manager.matching.match_mbs_umbs.matching import run_matching
from mortgage_data_manager.matching.match_mbs_umbs.utils import (
    build_rename_maps,
    compute_date_window,
    load_standardized_frame,
)
from mortgage_data_manager.matching.match_mbs_umbs.validation import run_validation

__all__ = [
    # Config
    "MBSUMBSMatchingConfig",
    # Matching
    "run_matching",
    # Shared utilities
    "build_rename_maps",
    "compute_date_window",
    "load_standardized_frame",
    # Validation
    "run_validation",
]
