"""FNMA pipeline - high-level orchestration.

Composes bronze → silver for the FNMA loan-performance datasets. There is no
``run_download`` step: Fannie's Data Dynamics platform requires manual
registration and forbids automated download, so ``fnma download`` prints
instructions rather than fetching anything (see `.cli`).

Dataset selection runs on two axes, because bronze and silver do not decompose
the same way — bronze ingests raw artifact families (``sf``, ``exclusions``,
``harp``, ``multifamily``) while silver builds derived tables (``issuances``,
``performance``, ``harp``, ``multifamily``). Both are registered in
`.config`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.sources.fnma.config import resolve_datasets

logger = get_logger(__name__)


def run_bronze(
    datasets: list[str] | None = None,
    *,
    overwrite: bool = False,
    min_year: int | None = None,
    max_year: int | None = None,
    quarters: list[str] | None = None,
    tmp_dir: Path | None = None,
) -> dict[str, int]:
    """Bronze step: raw zips → typed parquet.

    Args:
        datasets: Subset of the bronze datasets. ``None`` = the default set
            (everything except ``performance_all``, which is opt-in).
        overwrite: Rebuild bronze parquet that already exists.
        min_year: Lowest year to import. ``performance_all`` only.
        max_year: Highest year to import. ``performance_all`` only.
        quarters: Explicit quarter stems to import. ``performance_all`` only.
        tmp_dir: Extraction scratch space. ``performance_all`` and ``exclusions``
            only; defaults to the data volume rather than the boot disk.

    Returns:
        Counts: ``processed``, ``successful``, ``failed``.
    """
    from mortgage_data_manager.sources.fnma.import_bronze import (
        import_harp,
        import_multifamily,
        run_bronze_import,
        run_exclusions_import,
        run_performance_all_import,
    )

    selected = resolve_datasets(datasets, stage="bronze")
    builders: dict[str, Callable[..., dict[str, int]]] = {
        "sf": run_bronze_import,
        "exclusions": run_exclusions_import,
        "performance_all": run_performance_all_import,
        "harp": import_harp,
        "multifamily": import_multifamily,
    }
    # The year/quarter window only means anything for the bulk archive; the other
    # builders take their scope from which raw files exist.
    windowed: dict[str, Any] = {
        "min_year": min_year,
        "max_year": max_year,
        "quarters": quarters,
        "tmp_dir": tmp_dir,
    }
    counts = {"processed": 0, "successful": 0, "failed": 0}
    for dataset in selected:
        logger.info("FNMA bronze: %s", dataset)
        if dataset == "performance_all":
            extra = windowed
        elif dataset == "exclusions":
            extra = {"tmp_dir": tmp_dir}
        else:
            extra = {}
        result = builders[dataset](overwrite=overwrite, **extra)
        for key in counts:
            counts[key] += result[key]
    return counts


def run_silver(
    datasets: list[str] | None = None,
    *,
    overwrite: bool = False,
) -> dict[str, int]:
    """Silver step: bronze parquet → the derived silver tables.

    Args:
        datasets: Subset of the silver datasets. ``None`` = all.
        overwrite: Rebuild silver outputs that already exist.

    Returns:
        Counts: ``built`` (datasets built) and ``processed`` (files written
        across them).
    """
    from mortgage_data_manager.sources.fnma.import_silver import (
        build_silver,
        build_silver_harp,
        build_silver_multifamily,
        build_silver_performance,
    )

    selected = resolve_datasets(datasets, stage="silver")
    builders = {
        "issuances": build_silver,
        "performance": build_silver_performance,
        "harp": build_silver_harp,
        "multifamily": build_silver_multifamily,
    }
    counts = {"built": 0, "processed": 0}
    for dataset in selected:
        logger.info("FNMA silver: %s", dataset)
        result = builders[dataset](overwrite=overwrite)
        counts["built"] += 1
        counts["processed"] += result.get("processed", 0)
    return counts


def run_pipeline(
    *,
    bronze_datasets: list[str] | None = None,
    silver_datasets: list[str] | None = None,
    overwrite: bool = False,
    skip_bronze: bool = False,
    skip_silver: bool = False,
) -> dict[str, Any]:
    """Run the FNMA end-to-end pipeline (bronze → silver).

    Silver runs even when bronze reported failures, since previously-built
    bronze parquet may still cover the requested quarters.

    Args:
        bronze_datasets: Subset of the bronze datasets. ``None`` = all.
        silver_datasets: Subset of the silver datasets. ``None`` = all.
        overwrite: Overwrite/rebuild existing outputs at every step.
        skip_bronze: Skip the bronze step (use existing bronze parquet).
        skip_silver: Skip the silver step.

    Returns:
        Dictionary with per-step result counts.
    """
    results: dict[str, Any] = {}
    if not skip_bronze:
        logger.info("Step 1: Building FNMA bronze layer")
        results["bronze"] = run_bronze(bronze_datasets, overwrite=overwrite)
    if not skip_silver:
        logger.info("Step 2: Building FNMA silver layer")
        results["silver"] = run_silver(silver_datasets, overwrite=overwrite)
    return results
