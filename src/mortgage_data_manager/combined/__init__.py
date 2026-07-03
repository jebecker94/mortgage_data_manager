"""Combined cross-source master datasets.

A top-level subpackage (sibling to ``matching`` and ``analytics``) that
assembles a small, curated set of cross-source master datasets from the
per-source silver layers: ``loan_issuance``, ``loan_performance``,
``mbs_issuance``, ``mbs_performance``, and ``issuer``. Deliberately NOT the
exhaustive vendor-style aggregation catalog (e.g. eMBS security-level state-
concentration summaries) — that scope is out.

One registered dataset, ``msr_transfers``, is produced by a SEPARATE project
and is noted/registered (``external=True``) but NOT built here. All builders
are PLACEHOLDER stubs for now.
"""

from mortgage_data_manager.combined import builders
from mortgage_data_manager.combined.config import (
    BUILDABLE_DATASETS,
    DATASET_MAP,
    VALID_DATASETS,
    CombinedConfig,
)

__all__ = [
    "CombinedConfig",
    "DATASET_MAP",
    "VALID_DATASETS",
    "BUILDABLE_DATASETS",
    "builders",
]
