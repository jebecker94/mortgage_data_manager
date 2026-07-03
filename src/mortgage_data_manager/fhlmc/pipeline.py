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
) -> dict[str, Any]:
    """Run bronze layer loading workflow.

    Steps:
        1. Load schemas from Excel dictionary
        2. Import historical data (origination + performance) for specified years
        3. Import RPL data (reperforming) if requested

    Args:
        datasets: List of datasets to process ('origination', 'performance', 'reperforming')
        years: List of years to process
        overwrite: Whether to overwrite existing files
        raw_dir: Raw data directory (default: config.RAW_DIR)
        bronze_dir: Bronze output directory (default: config.BRONZE_DIR)

    Returns:
        Dictionary with workflow results:
        - loaded: Number of successful loads
        - failed: Number of failures
        - datasets_processed: List of datasets that were processed
    """
    from mortgage_data_manager.fhlmc.config import FHLMCConfig
    from mortgage_data_manager.fhlmc.import_bronze import (
        import_all_historical_data,
        import_rpl_data,
    )
    from mortgage_data_manager.fhlmc.schemas import load_all_schemas

    raw = raw_dir or FHLMCConfig.FHLMC_RAW_DIR
    bronze = bronze_dir or FHLMCConfig.FHLMC_BRONZE_DIR

    results = {
        'loaded': 0,
        'failed': 0,
        'datasets_processed': [],
    }

    # Ensure bronze directories exist
    bronze.mkdir(parents=True, exist_ok=True)
    (bronze / 'origination').mkdir(parents=True, exist_ok=True)
    (bronze / 'performance').mkdir(parents=True, exist_ok=True)
    (bronze / 'reperforming').mkdir(parents=True, exist_ok=True)

    # Load schemas
    logger.info("Loading schemas from Excel dictionary")
    try:
        schemas = load_all_schemas(FHLMCConfig.FHLMC_SCHEMA_FILE)
    except Exception as e:
        logger.error(f"Failed to load schemas: {e}")
        results['failed'] += 1
        return results

    # Process historical data (origination and performance)
    if 'origination' in datasets or 'performance' in datasets:
        logger.info(f"Importing historical data for years {min(years)}-{max(years)}")
        try:
            import_all_historical_data(
                schemas=schemas,
                raw_dir=raw,
                years=years,
                overwrite=overwrite,
            )
            if 'origination' in datasets:
                results['loaded'] += len(years)
                results['datasets_processed'].append('origination')
            if 'performance' in datasets:
                results['loaded'] += len(years)
                results['datasets_processed'].append('performance')
        except Exception as e:
            logger.error(f"Failed to import historical data: {e}")
            results['failed'] += len(years)

    # Process RPL data
    if 'reperforming' in datasets:
        logger.info("Importing RPL matching data")
        try:
            import_rpl_data(schemas)
            results['loaded'] += 1
            results['datasets_processed'].append('reperforming')
        except Exception as e:
            logger.error(f"Failed to import RPL data: {e}")
            results['failed'] += 1

    return results


def run_pipeline(
    datasets: list[str],
    years: list[int],
    overwrite: bool = False,
) -> dict[str, Any]:
    """Run complete end-to-end pipeline.

    Steps:
        1. Load schemas
        2. Load data to bronze layer
        3. (Future) Transform data to silver layer

    Args:
        datasets: List of datasets to process
        years: List of years to process
        overwrite: Overwrite existing files

    Returns:
        Dictionary with combined workflow results
    """
    results = {
        'bronze': {},
        'silver': {},  # Placeholder for future silver layer
    }

    # Bronze loading
    logger.info("Phase 1: Loading data to bronze layer")
    results['bronze'] = run_bronze(
        datasets=datasets,
        years=years,
        overwrite=overwrite
    )

    # Silver transformation (future)
    # logger.info("Phase 2: Transforming data to silver layer")
    # results['silver'] = run_silver(...)

    return results
