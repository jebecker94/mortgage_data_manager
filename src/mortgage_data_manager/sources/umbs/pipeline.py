"""UMBS pipeline - high-level orchestration.

Composes bronze → silver for the UMBS disclosure feeds. There is no
``run_download`` step: both GSE portals require manual registration and forbid
automated download, so ``umbs download`` prints instructions rather than
fetching anything (see `.cli`).

Bronze is two importers over complementary folder sets — the pipe-delimited
folders that ship a header row (ILLD, IS, MF, GN_MEGA, ...) and the headerless
multi-record-type folders (MS, ISS, RISS, ESF_MSS, ...). The same ``gse`` /
``folders`` filters route to both, and their counts are summed here so callers
see one bronze result rather than two.

Selection uses UMBS's own domain axes (``gse``, ``folders``, ``kinds``) rather
than a generic dataset registry, per the package naming conventions.
"""

from __future__ import annotations

from typing import Any

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.sources.umbs.config import UMBSConfig

logger = get_logger(__name__)


def run_bronze(
    *,
    overwrite: bool = False,
    gse: str | None = None,
    folders: list[str] | None = None,
) -> dict[str, int]:
    """Bronze step: raw zips → parquet, preserving the GSE/folder structure.

    Runs both the headered and the multi-record-type importers over their
    complementary folder sets and sums the result.

    Args:
        overwrite: Rebuild bronze parquet that already exists.
        gse: Restrict to a single GSE ('FNMA' or 'FHLMC').
        folders: Restrict to specific bronze folder names.

    Returns:
        Counts: ``processed``, ``successful``, ``failed``.
    """
    from mortgage_data_manager.sources.umbs.import_bronze import (
        run_bronze_import,
        run_multi_rt_bronze_import,
    )

    # Pipe-delimited-with-headers folders (ILLD, IS, MF, GN_MEGA, ...).
    headered = run_bronze_import(
        raw_dir=UMBSConfig.UMBS_RAW_DIR,
        bronze_dir=UMBSConfig.UMBS_BRONZE_DIR,
        overwrite=overwrite,
        gse=gse,
        folders=folders,
    )
    # Headerless multi-record-type folders (MS, ISS, RISS, ESF_MSS, ...);
    # complementary folder sets, so the same filters route correctly.
    multi_rt = run_multi_rt_bronze_import(
        raw_dir=UMBSConfig.UMBS_RAW_DIR,
        bronze_dir=UMBSConfig.UMBS_BRONZE_DIR,
        overwrite=overwrite,
        gse=gse,
        folders=folders,
    )
    return {key: headered[key] + multi_rt[key] for key in ("processed", "successful", "failed")}


def run_silver(
    *,
    overwrite: bool = False,
    gse: str | None = None,
    kinds: list[str] | None = None,
) -> dict[str, dict[str, int]]:
    """Silver step: merge each (original, correction) bronze pair into one dataset.

    Args:
        overwrite: Rebuild silver datasets that already exist.
        gse: Restrict to a single GSE ('FNMA' or 'FHLMC').
        kinds: Restrict to canonical kind names (ILLD, IS, ...).

    Returns:
        Per-pair counts, keyed by ``GSE/kind``. Correction-pair results carry
        ``wrote``/``skipped_existing``/``missing_pair``/``failed``; performance-kind
        results (MLLD/FU/MF/GN_MEGA) omit ``missing_pair``.
    """
    from mortgage_data_manager.sources.umbs.import_silver import run_silver_import

    return run_silver_import(
        bronze_dir=UMBSConfig.UMBS_BRONZE_DIR,
        silver_dir=UMBSConfig.UMBS_SILVER_DIR,
        overwrite=overwrite,
        gse=gse,
        kinds=kinds,
    )


def run_pipeline(
    *,
    overwrite: bool = False,
    gse: str | None = None,
    folders: list[str] | None = None,
    kinds: list[str] | None = None,
    skip_bronze: bool = False,
    skip_silver: bool = False,
) -> dict[str, Any]:
    """Run the UMBS end-to-end pipeline (bronze → silver).

    Args:
        overwrite: Overwrite/rebuild existing outputs at every step.
        gse: Restrict both steps to a single GSE.
        folders: Restrict bronze to specific folder names.
        kinds: Restrict silver to canonical kind names.
        skip_bronze: Skip the bronze step (use existing bronze parquet).
        skip_silver: Skip the silver step.

    Returns:
        Dictionary with per-step results: ``bronze`` counts and ``silver``
        per-pair counts.
    """
    results: dict[str, Any] = {}
    if not skip_bronze:
        logger.info("Step 1: Building UMBS bronze layer")
        results["bronze"] = run_bronze(overwrite=overwrite, gse=gse, folders=folders)
    if not skip_silver:
        logger.info("Step 2: Building UMBS silver layer")
        results["silver"] = run_silver(overwrite=overwrite, gse=gse, kinds=kinds)
    return results
