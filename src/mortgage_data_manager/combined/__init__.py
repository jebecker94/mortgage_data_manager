"""Combined cross-source master datasets.

A top-level subpackage (sibling to ``matching``) that assembles a small, curated
set of cross-source master datasets from the per-source silver layers:
``loan_issuance``, ``loan_performance``, ``loan_terminations``, ``loan_outcomes``,
``mbs_issuance`` and ``mbs_performance``. Vendor-style aggregation catalogs (such as
security-level state-concentration summaries) are out of scope.

See ``docs/api/combined.md`` for grains, row counts, and the event taxonomy.
"""

from mortgage_data_manager.combined import builders
from mortgage_data_manager.combined.config import (
    DATASET_MAP,
    VALID_DATASETS,
    CombinedConfig,
)

__all__ = [
    "CombinedConfig",
    "DATASET_MAP",
    "VALID_DATASETS",
    "builders",
]
