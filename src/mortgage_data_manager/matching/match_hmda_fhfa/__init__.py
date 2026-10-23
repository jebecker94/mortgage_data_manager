"""HMDA-FHFA matching workflow: HMDA originations linked to FHFA PUDB acquisitions.

Two eras, one module each, both with one round engine and the rounds written as data:

- `matching_post2018`: five rounds (`ROUNDS_POST2018`; same-year GSE, same-year all
  purchasers, cross-year, no-rate, cross-vintage bridge), run by `HMDAFHFAMatcher`.
- `matching_pre2018`: three rounds (same-year exact income, one-year lag with AMFI
  deflation, contested-cell tie-break), run by `HMDAFHFAPre2018Matcher`.

The rounds of both eras are in ``rounds.py``; `mortgage-data match hmda-fhfa info` prints them.
"""

from mortgage_data_manager.matching.match_hmda_fhfa.config import HMDAFHFAMatchingConfig
from mortgage_data_manager.matching.match_hmda_fhfa.matching_post2018 import (
    HMDAFHFAMatcher,
    MatchResult,
    run_matching,
)
from mortgage_data_manager.matching.match_hmda_fhfa.matching_pre2018 import (
    HMDAFHFAPre2018Matcher,
    run_matching_pre2018,
)
from mortgage_data_manager.matching.match_hmda_fhfa.rounds import (
    ROUNDS_POST2018,
    ROUNDS_PRE2018,
    Round,
)

__all__ = [
    "HMDAFHFAMatchingConfig",
    "ROUNDS_POST2018",
    "ROUNDS_PRE2018",
    "Round",
    "HMDAFHFAMatcher",
    "MatchResult",
    "HMDAFHFAPre2018Matcher",
    "run_matching",
    "run_matching_pre2018",
]
