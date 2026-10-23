"""CRT-MBS Matching Module.

Links GSE credit-risk-transfer reference-pool disclosures to the SF
loan-level datasets, per GSE:

- **FHLMC** (``matching_fhlmc.py``): Freddie's STACR/ACIS CRT disclosure uses
  deal-scoped Loan Identifiers, so loans are pinned to SF Loan Sequence
  Numbers with a per-deal tiered exact join (1:1 both directions) on
  at-origination characteristics, confirmed against the SF performance panel
  (``UPB at Issuance`` cent-exact within ±1 month of the deal Period).
- **FNMA** (``matching_fnma.py``): deal membership is a deterministic
  extraction of ``Reference Pool ID`` / ``Deal Name`` streamed from the
  ``CAS_*.zip`` / ``CIRT_*.zip`` historical downloads in fnma raw (LPPUB
  bronze fallback). The extracted ``CRT Loan Identifier`` is a CRT-specific
  namespace that does NOT join to the LPPUB id — SF linkage needs a
  characteristics matcher mirroring the FHLMC side (future work).

Crosswalks land in ``crosswalk/crt_mbs/``. Basis: ``docs/matching/crt_mbs_matching.md``.

Example - FHLMC matching:
    >>> from mortgage_data_manager.matching.match_crt_mbs import run_matching_fhlmc
    >>> summary = run_matching_fhlmc()
"""

from mortgage_data_manager.matching.match_crt_mbs.config import CRTMBSMatchingConfig
from mortgage_data_manager.matching.match_crt_mbs.matching_fhlmc import (
    confirm_pairs,
    run_matching_fhlmc,
)
from mortgage_data_manager.matching.match_crt_mbs.matching_fnma import (
    extract_fnma_crt_from_zips,
    run_matching_fnma,
)

__all__ = [
    "CRTMBSMatchingConfig",
    "run_matching_fhlmc",
    "confirm_pairs",
    "run_matching_fnma",
    "extract_fnma_crt_from_zips",
]
