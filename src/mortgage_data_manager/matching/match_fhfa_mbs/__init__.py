"""FHFA-MBS matching: GSE loan-level disclosures against FHFA acquisitions.

One engine (``matching``) matches either enterprise's loan-level data against
FHFA's single-family census-tract file; a per-enterprise ``GseSpec``
(``inputs``) carries where the data lives, how it maps onto the canonical
matching schema, which options that enterprise runs, and its rounds
(``rounds``). Crosswalks are
written one per acquisition year, resolved to one acquisition year per loan by
the multi-year entry point.

FNMA-FHFA: ~94.8% FNMA / ~93.0% FHFA (2019-2024), exact rate matching.
FHLMC-FHFA: ~94.5% FHLMC / ~94.2% FHFA (2018-2024), near-exact rate matching
    (FHFA rounds Freddie rates to hundredths) plus the FHFA term conventions
    (ceiling binning + product-envelope phase; ``matching/terms.py``).

FHFA->UMBS ARM (``matching_arm``): FHFA ARM acquisitions, which the SF files stop
carrying after their non-standard releases end, against the UMBS ARM
disclosures (Fannie ILLD, Freddie FU). Its rounds (``ROUNDS_ARM``) are pooled over
acquisition years, both enterprises in one crosswalk keyed on the UMBS loan id.
"""

from mortgage_data_manager.matching.match_fhfa_mbs.config import FHFAMBSMatchingConfig
from mortgage_data_manager.matching.match_fhfa_mbs.inputs import (
    FHLMC,
    FNMA,
    SPECS,
    get_md_to_cbsa,
    specs,
)
from mortgage_data_manager.matching.match_fhfa_mbs.matching import (
    admitted,
    build_dti_match_expr,
    match_pass,
    match_round,
    match_year,
    run_matching,
    run_year,
)
from mortgage_data_manager.matching.match_fhfa_mbs.matching_arm import (
    FHLMC_ARM,
    FNMA_ARM,
    UMBS_ARM_SOURCES,
    UmbsArmSource,
    match_umbs_arms,
    run_matching_arm,
)
from mortgage_data_manager.matching.match_fhfa_mbs.rounds import (
    ROUNDS_ARM,
    ROUNDS_FHLMC,
    ROUNDS_FNMA,
    ArmRound,
    Leg,
    Round,
)
from mortgage_data_manager.matching.match_fhfa_mbs.spec import (
    GseSpec,
    MatchingTolerances,
    MatchOptions,
)
from mortgage_data_manager.matching.terms import is_bin_edge, round_to_fhfa_bin

__all__ = [
    # Config
    "FHFAMBSMatchingConfig",
    # Specs
    "FHLMC",
    "FNMA",
    "SPECS",
    "GseSpec",
    "MatchOptions",
    "MatchingTolerances",
    "specs",
    # Rounds
    "ROUNDS_FHLMC",
    "ROUNDS_FNMA",
    "Leg",
    "Round",
    # Engine
    "admitted",
    "build_dti_match_expr",
    "get_md_to_cbsa",
    "is_bin_edge",
    "match_pass",
    "match_round",
    "match_year",
    "round_to_fhfa_bin",
    "run_matching",
    "run_year",
    # FHFA->UMBS ARM crosswalk
    "ROUNDS_ARM",
    "ArmRound",
    "FHLMC_ARM",
    "FNMA_ARM",
    "UMBS_ARM_SOURCES",
    "UmbsArmSource",
    "match_umbs_arms",
    "run_matching_arm",
]
