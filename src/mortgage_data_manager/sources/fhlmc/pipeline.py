"""FHLMC pipeline orchestration.

Orchestrates the download -> bronze flow by calling the core import
functions in sequence. Mirrors the fha/fhfa pipeline.py modules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)


def run_bronze(
    datasets: list[str],
    years: list[int],
    overwrite: bool = False,
    raw_dir: Path | None = None,
    bronze_dir: Path | None = None,
    zip_names: list[str] | None = None,
) -> dict[str, Any]:
    """Run bronze layer loading workflow.

    Steps:
        1. Load schemas from the layout CSVs
        2. Import historical data (origination + performance) for specified years
        3. Import RPL data (reperforming) if requested

    Args:
        datasets: List of datasets to process ('origination', 'performance', 'reperforming')
        years: List of years to process
        overwrite: Whether to overwrite existing files
        raw_dir: Raw data directory (default: ``FHLMCConfig.FHLMC_RAW_DIR``)
        bronze_dir: Bronze output directory (default: ``FHLMCConfig.FHLMC_BRONZE_DIR``)
        zip_names: If provided, only walk top-level raw zips with these exact
            names. Lets a caller target one bundle (e.g. the non-standard
            ``historical_data_excl_*`` zip) instead of every historical zip in
            the raw directory.

    Returns:
        Dictionary with workflow results:
        - loaded: Number of successful loads
        - failed: Number of failures
        - datasets_processed: List of datasets that were processed
    """
    from mortgage_data_manager.sources.fhlmc.config import FHLMCConfig
    from mortgage_data_manager.sources.fhlmc.import_bronze import (
        import_all_historical_data,
        import_rpl_data,
    )
    from mortgage_data_manager.sources.fhlmc.schemas import load_standard_schemas

    raw = raw_dir or FHLMCConfig.FHLMC_RAW_DIR
    bronze = bronze_dir or FHLMCConfig.FHLMC_BRONZE_DIR

    loaded = 0
    failed = 0
    datasets_processed: list[str] = []

    # Ensure bronze directories exist
    bronze.mkdir(parents=True, exist_ok=True)
    (bronze / "origination").mkdir(parents=True, exist_ok=True)
    (bronze / "performance").mkdir(parents=True, exist_ok=True)
    (bronze / "reperforming").mkdir(parents=True, exist_ok=True)

    # Load schemas for both standard-dataset layout eras: the reissued R47
    # standard set and the pre-R47 non-standard bundle.
    logger.info("Loading schemas from the layout CSVs (pre-R47 + R47)")
    try:
        schemas = load_standard_schemas(
            FHLMCConfig.FHLMC_SCHEMA_FILE,
            FHLMCConfig.FHLMC_SCHEMA_FILE_R47,
        )
    except Exception as e:
        logger.error("Failed to load schemas: %s", e)
        return {"loaded": loaded, "failed": failed + 1, "datasets_processed": datasets_processed}

    # Process historical data (origination and performance). Only the historical
    # datasets are forwarded as the leaf-level filter — 'reperforming' is a
    # separate importer below, and passing it through would match no leaf.
    historical = [d for d in datasets if d in ("origination", "performance")]
    if historical:
        logger.info("Importing historical data for years %s-%s", min(years), max(years))
        try:
            import_all_historical_data(
                schemas=schemas,
                raw_dir=raw,
                years=years,
                overwrite=overwrite,
                datasets=historical,
                zip_names=zip_names,
            )
            if "origination" in datasets:
                loaded += len(years)
                datasets_processed.append("origination")
            if "performance" in datasets:
                loaded += len(years)
                datasets_processed.append("performance")
        except Exception as e:
            logger.error("Failed to import historical data: %s", e)
            failed += len(years)

    # Process RPL data
    if "reperforming" in datasets:
        logger.info("Importing RPL matching data")
        try:
            import_rpl_data(schemas)
            loaded += 1
            datasets_processed.append("reperforming")
        except Exception as e:
            logger.error("Failed to import RPL data: %s", e)
            failed += 1

    return {"loaded": loaded, "failed": failed, "datasets_processed": datasets_processed}


def run_crt(
    overwrite: bool = False,
    datasets: list[str] | None = None,
) -> dict[str, Any]:
    """Run the CRT disclosure pipeline: bronze (all parts) then silver (lld).

    Args:
        overwrite: Whether to overwrite existing bronze/silver outputs.
        datasets: CRT parts for the bronze stage (default: all). The silver
            stage runs only when 'lld' is included.

    Returns:
        Dictionary with 'bronze' and 'silver' count dicts.
    """
    from mortgage_data_manager.sources.fhlmc.config import FHLMCConfig
    from mortgage_data_manager.sources.fhlmc.import_bronze import import_crt_data
    from mortgage_data_manager.sources.fhlmc.import_silver import import_crt_to_silver

    selected = datasets or FHLMCConfig.CRT_DATASETS

    logger.info("Phase 1: CRT bronze")
    bronze_counts = import_crt_data(overwrite=overwrite, datasets=selected)

    silver_counts: dict[str, int] = {}
    if "lld" in selected:
        logger.info("Phase 2: CRT silver")
        silver_counts = import_crt_to_silver(overwrite=overwrite)

    return {"bronze": bronze_counts, "silver": silver_counts}


def run_silver(
    datasets: list[str] | None = None,
    *,
    years: list[int] | None = None,
    overwrite: bool = False,
) -> dict[str, dict[str, int]]:
    """Silver step: bronze parquet → cleaned, hive-partitioned silver.

    Args:
        datasets: Subset of ``origination``/``performance``/``crt``. ``None``
            = all three. ``reperforming`` has no silver builder and is ignored.
        years: Origination vintages to build. ``None`` = every vintage found.
            Only the origination builder takes a year filter.
        overwrite: Rebuild silver outputs that already exist.

    Returns:
        Per-dataset counts, keyed by dataset name.

    Raises:
        ValueError: If a dataset has no silver builder.
    """
    from mortgage_data_manager.sources.fhlmc.import_silver import (
        import_crt_to_silver,
        import_origination_to_silver,
        import_performance_to_silver,
    )

    valid = ["origination", "performance", "crt"]
    selected = [d for d in (datasets or valid) if d != "reperforming"]
    unknown = [d for d in selected if d not in valid]
    if unknown:
        raise ValueError(f"No FHLMC silver builder for: {unknown}; valid: {valid}")

    results: dict[str, dict[str, int]] = {}
    for dataset in selected:
        logger.info("FHLMC silver: %s", dataset)
        if dataset == "origination":
            results[dataset] = import_origination_to_silver(years=years, overwrite=overwrite)
        elif dataset == "performance":
            results[dataset] = import_performance_to_silver(overwrite=overwrite)
        else:
            results[dataset] = import_crt_to_silver(overwrite=overwrite)
    return results


def run_pipeline(
    datasets: list[str],
    years: list[int],
    overwrite: bool = False,
    skip_bronze: bool = False,
    skip_silver: bool = False,
) -> dict[str, Any]:
    """Run the complete end-to-end pipeline (bronze → silver).

    Args:
        datasets: List of datasets to process
        years: List of years to process
        overwrite: Overwrite existing files
        skip_bronze: Skip the bronze step (use existing bronze parquet)
        skip_silver: Skip the silver step

    Returns:
        Dictionary with combined workflow results
    """
    results: dict[str, Any] = {"bronze": {}, "silver": {}}

    if not skip_bronze:
        logger.info("Phase 1: Loading data to bronze layer")
        results["bronze"] = run_bronze(datasets=datasets, years=years, overwrite=overwrite)

    if not skip_silver:
        logger.info("Phase 2: Transforming data to silver layer")
        results["silver"] = run_silver(datasets=datasets, years=years, overwrite=overwrite)

    return results
