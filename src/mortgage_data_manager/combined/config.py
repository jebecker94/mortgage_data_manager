"""Configuration and registry for the combined cross-source master datasets.

`DATASET_MAP` lists the six masters in build order. Each entry gives the row
``grain``, the per-source ``sources`` the builder reads, and ``depends_on``: the
combined masters it reads, which must be built first.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig

#: Registry of combined master datasets, in build order.
DATASET_MAP: Final[dict[str, dict[str, str | list[str]]]] = {
    "loan_issuance": {
        "description": "Master loan-level issuance/origination",
        "grain": "loan",
        "sources": ["gnma", "umbs", "fnma", "fhlmc"],
        "depends_on": [],
    },
    "loan_performance": {
        "description": "Master loan-level performance panel (loan x month)",
        "grain": "loan_month",
        "sources": ["gnma", "umbs", "fnma", "fhlmc"],
        "depends_on": [],
    },
    "loan_terminations": {
        "description": "Loan termination events with the GSE loss waterfall (one per loan)",
        "grain": "loan_event",
        "sources": ["gnma", "fnma", "fhlmc"],
        "depends_on": [],
    },
    "loan_outcomes": {
        "description": "Loan-grain outcome summary (observation window, delinquency, modification, terminal event)",
        "grain": "loan",
        "sources": ["gnma", "fhlmc"],
        "depends_on": ["loan_issuance", "loan_performance", "loan_terminations"],
    },
    "mbs_issuance": {
        "description": "Master MBS/pool issuance",
        "grain": "security",
        "sources": ["gnma", "umbs"],
        "depends_on": [],
    },
    "mbs_performance": {
        "description": "Master MBS/pool performance panel (pool x month)",
        "grain": "security_month",
        "sources": ["gnma", "umbs"],
        "depends_on": [],
    },
}

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
        """Output path for a combined master dataset.

        Args:
            dataset: Combined master dataset name.

        Returns:
            The output path for that dataset.
        """
        return cls.COMBINED_DATA_DIR / dataset
