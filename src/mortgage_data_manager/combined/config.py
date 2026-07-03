"""Configuration for the combined (cross-source master) datasets.

This subpackage assembles a SMALL, deliberate set of cross-source master
datasets from the per-source silver layers — NOT the exhaustive aggregation
catalog a commercial vendor like eMBS publishes (e.g. security-level state-
concentration summaries by month; explicitly out of scope for this package).

Target master datasets (the registry below):

- ``loan_issuance``    - master loan-level issuance/origination
- ``loan_performance`` - master loan-level performance panel (loan × month)
- ``mbs_issuance``     - master MBS/pool issuance
- ``mbs_performance``  - master MBS/pool performance panel (pool × month)
- ``issuer``           - issuer master (identity crosswalk across sources)

Plus one derived dataset that is NOTED here but built by a sibling subpackage:

- ``msr_transfers``    - MSR (mortgage servicing rights) transfers. Detection
  now lives in-package in :func:`analytics.transfers.detect_servicer_transfers`
  (folded in from Jonathan's former standalone ``msr_transfers`` project), not
  in ``combined``. Registered here for cross-reference only; this subpackage
  does not build it. See ``builders.msr_transfers_note``.

The ``sources`` field lists the per-source silver inputs each master is
expected to draw on — a wiring sketch, refined as builders are implemented.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig

#: Registry of combined master datasets. ``grain`` is the row grain; ``sources``
#: are the per-source silver inputs the builder will draw on (sketch);
#: ``external`` marks datasets not built by THIS subpackage (produced elsewhere
#: — a separate project, or a sibling subpackage like ``analytics``).
DATASET_MAP: Final[dict[str, dict[str, object]]] = {
    "loan_issuance": {
        "description": "Master loan-level issuance/origination",
        "grain": "loan",
        "sources": ["hmda", "fha", "gnma", "umbs", "fnma", "fhlmc"],
        "external": False,
    },
    "loan_performance": {
        "description": "Master loan-level performance panel (loan x month)",
        "grain": "loan_month",
        "sources": ["gnma", "umbs", "fnma", "fhlmc"],
        "external": False,
    },
    "loan_terminations": {
        "description": "GSE loan termination / loss-waterfall events (one row per event)",
        "grain": "loan_event",
        "sources": ["fnma", "fhlmc"],
        "external": False,
    },
    "mbs_issuance": {
        "description": "Master MBS/pool issuance",
        "grain": "security",
        "sources": ["gnma", "umbs", "fhfa"],
        "external": False,
    },
    "mbs_performance": {
        "description": "Master MBS/pool performance panel (pool x month)",
        "grain": "security_month",
        "sources": ["gnma", "umbs"],
        "external": False,
    },
    "issuer": {
        "description": "Issuer master / identity crosswalk across sources",
        "grain": "issuer",
        "sources": ["gnma", "fhfa", "fdic", "ncua", "hmda"],
        "external": False,
    },
    "msr_transfers": {
        "description": "MSR transfers (built in-package by analytics.transfers; not by combined)",
        "grain": "transfer",
        "sources": ["gnma", "umbs"],
        "external": True,
    },
}

#: Master datasets this package actually builds (excludes external ones).
BUILDABLE_DATASETS: Final[list[str]] = [
    name for name, meta in DATASET_MAP.items() if not meta["external"]
]
VALID_DATASETS: Final[list[str]] = list(DATASET_MAP.keys())


class CombinedConfig(MortgageDataConfig):
    """Configuration for the combined master datasets.

    Outputs land under ``data/combined/`` (one parquet/tree per master dataset).
    """

    SUBPACKAGE: str = "combined"

    COMBINED_DATA_DIR: Path = Path(
        config(
            "COMBINED_DATA_DIR",
            default=str(MortgageDataConfig.get_subpackage_data_dir("combined")),
        )
    )

    @classmethod
    def ensure_directories(cls) -> None:
        """Create the combined output directory if it doesn't exist."""
        cls.COMBINED_DATA_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def dataset_path(cls, dataset: str) -> Path:
        """Output path for a combined master dataset."""
        return cls.COMBINED_DATA_DIR / dataset
