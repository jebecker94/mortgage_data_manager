"""FHLB-MBS Matching Module.

Matches GNMA loan-level disclosure data to FHLB AMA (Acquired Member Assets)
acquisition data: links GNMA dailyllmni silver data to FHLB AMA acquisitions.

There is no FNMA side: AMA acquisitions do not appear in FNMA's loan-level
disclosures, so an FNMA-FHLB match yields essentially no pairs, and none whose
dollar balances agree.

Example:
    >>> from mortgage_data_manager.matching.match_fhlb_mbs import run_matching
    >>> df = run_matching(min_year=2015, max_year=2020)
    >>> print(f"Matched {len(df):,} loans")

The rounds, their keys and tolerances live in ``rounds.py``.
"""

from mortgage_data_manager.matching.match_fhlb_mbs.config import FHLBMBSMatchingConfig
from mortgage_data_manager.matching.match_fhlb_mbs.matching import (
    load_fhlb,
    load_gnma,
    match_fhlb_gnma,
    run_matching,
    run_rounds,
)
from mortgage_data_manager.matching.match_fhlb_mbs.rounds import ROUNDS, MatchRound, Tolerances

__all__ = [
    # Config
    "FHLBMBSMatchingConfig",
    # GNMA matching
    "run_matching",
    "load_gnma",
    "load_fhlb",
    "match_fhlb_gnma",
    "run_rounds",
    # Rounds
    "ROUNDS",
    "MatchRound",
    "Tolerances",
]
