"""FHFA pipeline orchestration.

High-level workflow functions that orchestrate multi-step operations
(schemas → bronze → silver) by composing core functions in the correct order.

These are consumed by the CLI but also callable programmatically.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.fhfa.config import BRONZE_DIR, RAW_DIR, SCHEMAS_DIR, SILVER_DIR
from mortgage_data_manager.fhfa.download import download_hud_gse_archive
from mortgage_data_manager.fhfa.import_bronze import (
    HUD_ERA_DATASETS,
    HUD_ERA_MAX_YEAR,
    HUD_ERA_MIN_YEAR,
    build_bronze_mf_hud_era,
    save_to_bronze,
)
from mortgage_data_manager.fhfa.import_silver import save_to_silver
from mortgage_data_manager.fhfa.schemas import (
    build_all_master_dictionaries,
    convert_excel_dictionary,
    extract_dictionary_tables_for_year,
    extract_enterprise_pudb_pdfs,
)

logger = get_logger(__name__)


def run_schemas(
    min_year: int = 2008,
    max_year: int = 2024,
    skip_extract: bool = False,
    skip_convert: bool = False,
    overwrite: bool = False,
    dictionary_root: Path | None = None,
) -> dict[str, Any]:
    """Run complete schema processing pipeline.

    Steps:
        1. Extract PDFs from enterprise PUDB zips (optional)
        2. Convert PDFs to CSV for years < 2024 (optional)
        3. Convert 2024 Excel dictionary
        4. Build master dictionaries for all datasets

    Args:
        min_year: Minimum year for master dictionaries
        max_year: Maximum year for master dictionaries
        skip_extract: Skip PDF extraction step
        skip_convert: Skip PDF/Excel conversion step
        overwrite: Replace existing files
        dictionary_root: Dictionary root directory (default: SCHEMAS_DIR)

    Returns:
        Dictionary with pipeline results:
        - pdfs_extracted: Number of PDFs extracted
        - years_converted: Number of years converted
        - masters_built: Number of master dictionaries built
        - datasets: List of datasets in master dictionaries
    """
    dict_root = dictionary_root or SCHEMAS_DIR
    results = {
        'pdfs_extracted': 0,
        'years_converted': 0,
        'masters_built': 0,
        'datasets': [],
    }

    # Step 1: Extract PDFs from zips
    if not skip_extract:
        logger.info("Step 1: Extracting PDFs from enterprise PUDB zips")
        try:
            pdfs = extract_enterprise_pudb_pdfs(
                raw_dir=RAW_DIR,
                dictionary_root=dict_root
            )
            results['pdfs_extracted'] = len(pdfs)
            logger.info(f"Extracted {len(pdfs)} PDF files")
        except Exception as e:
            logger.error(f"PDF extraction failed: {e}")

    # Step 2: Convert PDFs and Excel to CSV
    if not skip_convert:
        logger.info("Step 2: Converting dictionaries to CSV/Parquet")

        # Convert PDFs for years < 2024
        fhfa_dict_root = dict_root / 'fhfa'
        if fhfa_dict_root.exists():
            year_dirs = [d for d in fhfa_dict_root.iterdir() if d.is_dir() and d.name.isdigit()]
            years_to_convert = sorted([int(d.name) for d in year_dirs if int(d.name) < 2024])

            for year in years_to_convert:
                try:
                    extract_dictionary_tables_for_year(
                        year=year,
                        paths_project_dir=dict_root.parent,
                        overwrite=overwrite,
                        dictionary_root=dict_root,
                        output_formats=('csv',)
                    )
                    results['years_converted'] += 1
                except Exception as e:
                    logger.error(f"Failed to convert year {year}: {e}")

        # Convert 2024 Excel dictionary
        excel_path = dict_root / 'fhfa' / '2024-enterprise-pudb-data-dictionary.xlsx'
        if excel_path.exists():
            try:
                convert_excel_dictionary(
                    excel_path,
                    year=2024,
                    output_dir=dict_root / 'fhfa' / '2024',
                    overwrite=overwrite
                )
                results['years_converted'] += 1
                logger.info("Converted 2024 Excel dictionary")
            except Exception as e:
                logger.error(f"Failed to convert Excel: {e}")

    # Step 3: Build master dictionaries
    logger.info("Step 3: Building master dictionaries")
    try:
        masters = build_all_master_dictionaries(
            min_year=min_year,
            max_year=max_year,
            dictionary_root=dict_root,
            overwrite=overwrite
        )
        results['masters_built'] = len(masters)
        results['datasets'] = list(masters.keys())
        logger.info(f"Built {len(masters)} master dictionaries")
    except Exception as e:
        logger.error(f"Failed to build masters: {e}")

    return results


def run_data_pipeline(
    datasets: list[str],
    years: list[int],
    skip_bronze: bool = False,
    skip_silver: bool = False,
    overwrite: bool = False,
    raw_dir: Path | None = None,
    bronze_dir: Path | None = None,
    silver_dir: Path | None = None,
) -> dict[str, Any]:
    """Run data processing pipeline (bronze + silver).

    Steps:
        1. Load data to bronze layer (optional)
        2. Transform data to silver layer (optional)

    Note: FHLB AMA data processing has been moved to the fhlb subpackage.
    Use 'mortgage-data fhlb pipeline ama' for FHLB data processing.

    Args:
        datasets: List of datasets to process
        years: List of years to process
        skip_bronze: Skip bronze loading step
        skip_silver: Skip silver transformation step
        overwrite: Overwrite existing files
        raw_dir: Raw data directory (default: RAW_DIR)
        bronze_dir: Bronze directory (default: BRONZE_DIR)
        silver_dir: Silver directory (default: SILVER_DIR)

    Returns:
        Dictionary with workflow results:
        - bronze_loaded: Number of file-year combinations loaded to bronze
        - silver_transformed: Number of file-year combinations transformed to silver
        - failed_bronze: Number of failures in bronze loading
        - failed_silver: Number of failures in silver transformation
    """
    raw = raw_dir or RAW_DIR
    bronze = bronze_dir or BRONZE_DIR
    silver = silver_dir or SILVER_DIR

    results = {
        'bronze_loaded': 0,
        'silver_transformed': 0,
        'failed_bronze': 0,
        'failed_silver': 0,
    }

    # Step 1: Load to bronze
    if not skip_bronze:
        logger.info("Step 1: Loading data to bronze layer")
        for dataset in datasets:
            try:
                save_to_bronze(
                    dataset=dataset,
                    years=years,
                    raw_dir=raw,
                    output_dir=bronze,
                    overwrite=overwrite
                )
                results['bronze_loaded'] += len(years)
                logger.info(f"Loaded {dataset} to bronze")
            except Exception as e:
                results['failed_bronze'] += len(years)
                logger.error(f"Failed to load {dataset} to bronze: {e}")

    # Step 2: Transform to silver
    if not skip_silver:
        logger.info("Step 2: Transforming data to silver layer")
        for dataset in datasets:
            try:
                save_to_silver(
                    dataset=dataset,
                    years=years,
                    bronze_dir=bronze / dataset,
                    silver_dir=silver / dataset,
                    overwrite=overwrite
                )
                results['silver_transformed'] += len(years)
                logger.info(f"Transformed {dataset} to silver")
            except Exception as e:
                results['failed_silver'] += len(years)
                logger.error(f"Failed to transform {dataset} to silver: {e}")

    return results


def run_hud_era_backfill(
    datasets: list[str] | None = None,
    years: list[int] | None = None,
    *,
    skip_download: bool = False,
    skip_bronze: bool = False,
    skip_silver: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Run the HUD-era (1993-2007) multifamily backfill end to end.

    Orchestrates download -> bronze -> silver for the pre-FHFA GSE PUDB
    multifamily files. Bronze lands in the shared ``bronze/{dataset}/`` namespace
    and silver is produced by the same harmonizing transform as the modern data,
    so the backfill simply extends the existing ``mf_c``/``mf_property_b``/
    ``mf_unit_b`` series back to 1993.

    Args:
        datasets: Subset of the multifamily datasets. Defaults to all three.
        years: Years to process. Defaults to the full 1993-2007 span.
        skip_download: Skip fetching archive zips (use already-staged raw files).
        skip_bronze: Skip bronze building.
        skip_silver: Skip silver harmonization.
        overwrite: Overwrite existing files at every stage.

    Returns:
        Dictionary with ``downloaded``, ``bronze_written`` and
        ``silver_transformed`` counts.
    """
    datasets = datasets or list(HUD_ERA_DATASETS)
    years = years or list(range(HUD_ERA_MIN_YEAR, HUD_ERA_MAX_YEAR + 1))

    results: dict[str, Any] = {
        'downloaded': 0,
        'bronze_written': 0,
        'silver_transformed': 0,
    }

    if not skip_download:
        logger.info("Step 1: Downloading HUD-era GSE archive zips")
        dl = download_hud_gse_archive(years=years, overwrite=overwrite)
        results['downloaded'] = sum(
            1 for r in dl if r.status.value == 'success'
        )

    if not skip_bronze:
        logger.info("Step 2: Building HUD-era bronze")
        results['bronze_written'] = build_bronze_mf_hud_era(
            datasets=datasets, years=years, overwrite=overwrite
        )

    if not skip_silver:
        logger.info("Step 3: Harmonizing HUD-era silver")
        for dataset in datasets:
            try:
                save_to_silver(dataset=dataset, years=years, overwrite=overwrite)
                results['silver_transformed'] += 1
            except Exception as e:
                logger.error("Failed HUD-era silver for %s: %s", dataset, e)

    return results


def run_pipeline(
    datasets: list[str],
    years: list[int],
    process_schemas: bool = True,
    schema_min_year: int = 2008,
    schema_max_year: int = 2024,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Run complete end-to-end pipeline.

    Steps:
        1. Process schemas (optional)
        2. Load data to bronze layer
        3. Transform data to silver layer

    Args:
        datasets: List of datasets to process
        years: List of years to process
        process_schemas: Whether to process schemas first
        schema_min_year: Minimum year for schema processing
        schema_max_year: Maximum year for schema processing
        overwrite: Overwrite existing files

    Returns:
        Dictionary with combined results from both schemas and data pipelines
    """
    results = {
        'schemas': {},
        'data': {},
    }

    # Step 1: Process schemas
    if process_schemas:
        logger.info("Phase 1: Processing schemas")
        results['schemas'] = run_schemas(
            min_year=schema_min_year,
            max_year=schema_max_year,
            overwrite=overwrite
        )

    # Step 2: Process data
    logger.info("Phase 2: Processing data")
    results['data'] = run_data_pipeline(
        datasets=datasets,
        years=years,
        overwrite=overwrite
    )

    return results
