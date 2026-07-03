"""MBS-FHLB Matching Module.

Matches MBS loan-level disclosure data (GNMA, FNMA) to FHLB AMA
(Acquired Member Assets) acquisition data.

Implements:
- GNMA-FHLB matching: Links GNMA dailyllmni silver data to FHLB AMA acquisitions
- FNMA-FHLB matching: Tests mutual exclusivity hypothesis for UMBS ILLD vs FHLB AMA

Example - GNMA matching:
    >>> from mortgage_data_manager.matching.match_mbs_fhlb import run_gnma_fhlb_matching
    >>> df = run_gnma_fhlb_matching(min_year=2015, max_year=2020)
    >>> print(f"Matched {len(df):,} loans")

Example - FNMA matching (mutual exclusivity test):
    >>> from mortgage_data_manager.matching.match_mbs_fhlb import run_fnma_fhlb_matching
    >>> df = run_fnma_fhlb_matching(min_year=2019, max_year=2024)
    >>> print(f"Matched {len(df):,} loans")  # Expected: near-zero

Custom tolerances (GNMA):
    >>> from mortgage_data_manager.matching.match_mbs_fhlb import (
    ...     run_gnma_fhlb_matching, MatchingTolerances
    ... )
    >>> tolerances = MatchingTolerances(
    ...     rate_tolerance=0.5,        # Looser rate tolerance
    ...     balance_tolerance=5000,    # Looser balance tolerance
    ...     apply_post_match_filter=False,  # Skip post-filtering
    ... )
    >>> df = run_gnma_fhlb_matching(tolerances=tolerances)
"""

from mortgage_data_manager.matching.match_mbs_fhlb.config import (
    DATA_DIR,
    INTERMEDIATE_DIR,
    OUTPUT_DIR,
    MBSFHLBConfig,
    ensure_directories,
)
from mortgage_data_manager.matching.match_mbs_fhlb.fnma_match import (
    DEFAULT_FNMA_TOLERANCES,
    FNMAMatchingTolerances,
    load_fhlb_ama_conventional,
    load_fnma_illd_data,
    match_fnma_fhlb,
    run_fnma_fhlb_matching,
)
from mortgage_data_manager.matching.match_mbs_fhlb.gnma_match import (
    DEFAULT_TOLERANCES,
    MatchingTolerances,
    load_fhlb_ama_data,
    load_gnma_silver_data,
    match_gnma_fhlb,
    run_gnma_fhlb_matching,
)

__all__ = [
    # Config
    "DATA_DIR",
    "INTERMEDIATE_DIR",
    "OUTPUT_DIR",
    "MBSFHLBConfig",
    "ensure_directories",
    # GNMA matching
    "run_gnma_fhlb_matching",
    "load_gnma_silver_data",
    "load_fhlb_ama_data",
    "match_gnma_fhlb",
    "MatchingTolerances",
    "DEFAULT_TOLERANCES",
    # FNMA matching
    "run_fnma_fhlb_matching",
    "load_fnma_illd_data",
    "load_fhlb_ama_conventional",
    "match_fnma_fhlb",
    "FNMAMatchingTolerances",
    "DEFAULT_FNMA_TOLERANCES",
]
