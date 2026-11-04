"""Build entry points for the combined master datasets.

Each master is assembled in its own module from the per-source silver layers:
``loan_issuance``, ``loan_performance`` (with ``loan_terminations``), ``loan_outcomes``,
``mbs_issuance`` and ``mbs_performance``. `build_datasets` runs a selection of them in
registry order (``config.DATASET_MAP``).
"""

from __future__ import annotations

from mortgage_data_manager.combined.config import (
    DATASET_MAP,
    VALID_DATASETS,
    CombinedConfig,
)
from mortgage_data_manager.combined.loan_issuance import build_loan_issuance
from mortgage_data_manager.combined.loan_outcomes import build_loan_outcomes
from mortgage_data_manager.combined.loan_performance import (
    build_loan_performance,
    build_loan_terminations,
)
from mortgage_data_manager.combined.mbs_issuance import build_mbs_issuance
from mortgage_data_manager.combined.mbs_performance import build_mbs_performance
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file

logger = get_logger(__name__)


#: Builder dispatch for the datasets this package actually builds.
BUILDERS = {
    "loan_issuance": build_loan_issuance,
    "loan_performance": build_loan_performance,
    "loan_terminations": build_loan_terminations,
    "loan_outcomes": build_loan_outcomes,
    "mbs_issuance": build_mbs_issuance,
    "mbs_performance": build_mbs_performance,
}


def build_datasets(datasets: list[str] | None = None, *, overwrite: bool = False) -> list[str]:
    """Build the requested combined master datasets, in registry order.

    Registry order puts every master after the ones it reads (``depends_on``), so a
    request is reordered rather than run as typed. A master that is skipped because
    it exists is checked against its inputs: if one was rebuilt more recently, a
    warning says the master is stale.

    Args:
        datasets: Subset of ``VALID_DATASETS``. ``None`` = all.
        overwrite: Rebuild existing outputs.

    Returns:
        The datasets actually built (skipped ones are left out).

    Raises:
        ValueError: If an unknown dataset is requested.
    """
    selected = set(VALID_DATASETS if datasets is None else datasets)
    unknown = sorted(selected - set(VALID_DATASETS))
    if unknown:
        raise ValueError(f"Unknown combined datasets: {unknown}; valid: {VALID_DATASETS}")
    built: list[str] = []
    for dataset in (d for d in VALID_DATASETS if d in selected):
        path = CombinedConfig.dataset_path(dataset)
        if not should_process_file(path, overwrite=overwrite):
            newer = [
                dep
                for dep in DATASET_MAP[dataset]["depends_on"]
                if (dep_path := CombinedConfig.dataset_path(dep)).exists()
                and dep_path.stat().st_mtime > path.stat().st_mtime
            ]
            if newer:
                logger.warning(
                    "%s is older than %s; rebuild it with overwrite.", dataset, ", ".join(newer)
                )
            logger.info("%s already built at %s; skipping (use overwrite).", dataset, path)
            continue
        logger.info("Building combined master: %s", dataset)
        BUILDERS[dataset](overwrite=overwrite)
        built.append(dataset)
    return built
