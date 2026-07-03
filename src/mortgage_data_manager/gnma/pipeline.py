#!/usr/bin/env python3
"""GNMA Data Manager - Pipeline.

Orchestration functions for running complete GNMA data processing pipelines.

This module provides high-level pipeline functions that combine multiple
operations from different modules into cohesive pipelines.

Typical Pipeline:
    1. Download raw data and schema files (to raw layer)
    2. Extract and process schema files
    3. Stage raw data to parquet (raw → bronze)
    4. Transform data using schemas (bronze → silver)
    5. Load and analyze performance data (from silver)

Functions:
    - run_download: Download data and schemas for prefixes
    - run_analysis: Load and analyze performance data
    - run_pipeline: Full end-to-end pipeline
    - run_processing: Stage and transform data
    - run_schemas: Extract and combine schemas

"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from mortgage_data_manager.core.logging import get_logger

from .config import DownloadConfig, GNMAConfig, ProcessorConfig, SchemaReaderConfig
from .download import (
    create_session,
    download_all_data,
    download_all_schemas,
    download_layout_schemas,
)
from .performance import create_upb_time_series, get_summary_statistics, load_performance_data
from .utils import default_prefixes, load_prefix_dictionary

logger = get_logger(__name__)


def run_download(
    prefixes: list[str] | None = None,
    download_data: bool = True,
    download_schemas: bool = True,
    schema_source: str = "disclosure",
    config: DownloadConfig | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Run the download pipeline for specified prefixes.

    Downloads both data files and schema files for the specified prefixes.
    Files already present on disk are skipped unless ``overwrite=True``.

    Args:
        prefixes: List of prefixes to download (None for all)
        download_data: Whether to download data files
        download_schemas: Whether to download schema files
        schema_source: Where to pull schema PDFs from when ``download_schemas`` is
            set: ``"disclosure"`` (per-prefix disclosurehistoryfiles scrape — the
            historical default), ``"layout"`` (the master bulk-layout page, which
            covers prefixes the disclosure scrape omits), or ``"both"``.
        config: Download configuration (None for default)
        start_date: Override start date for downloads (optional)
        end_date: Override end date for downloads (optional)
        overwrite: Re-download files that already exist (default: skip them)

    Returns:
        Dictionary with download results and summary

    Example:
        >>> config = DownloadConfig(email_value="...", id_value="...", user_agent="...")
        >>> results = run_download(['monthly', 'llmon1'], config=config)
        >>> print(f"Downloaded {results['summary']['total_data_files']} data files")
    """
    if config is None:
        config = DownloadConfig()

    # Load prefix dictionary
    prefix_dict = load_prefix_dictionary(config.prefix_file)

    # Determine prefixes to process
    if prefixes is None:
        prefixes = default_prefixes(prefix_dict)

    logger.info(f"Starting download pipeline for {len(prefixes)} prefixes")
    logger.info(f"Prefixes: {', '.join(prefixes)}")

    # Set up session
    session = create_session(config)

    results: dict[str, Any] = {
        'prefixes': prefixes,
        'data_results': {},
        'schema_results': {},
        'summary': {
            'total_data_files': 0,
            'total_data_attempts': 0,
            'total_schema_files': 0,
            'total_schema_attempts': 0,
        }
    }

    # Download data files
    if download_data:
        logger.info("Downloading data files...")
        data_results = download_all_data(
            prefixes, prefix_dict, session, config,
            start_date=start_date, end_date=end_date, overwrite=overwrite,
        )
        results['data_results'] = data_results

        # Calculate totals
        for prefix, (success, total) in data_results.items():
            results['summary']['total_data_files'] += success
            results['summary']['total_data_attempts'] += total

    # Download schema files
    if download_schemas:
        if schema_source not in ("disclosure", "layout", "both"):
            raise ValueError(
                f"schema_source must be 'disclosure', 'layout', or 'both', got {schema_source!r}"
            )
        logger.info(f"Downloading schema files (source={schema_source})...")
        schema_results: dict[str, tuple[int, int]] = {}

        if schema_source in ("disclosure", "both"):
            for prefix, counts in download_all_schemas(
                prefixes, session, config, overwrite=overwrite
            ).items():
                schema_results[prefix] = counts

        if schema_source in ("layout", "both"):
            for prefix, (success, total) in download_layout_schemas(
                prefixes, session, config, overwrite=overwrite
            ).items():
                prev_s, prev_t = schema_results.get(prefix, (0, 0))
                schema_results[prefix] = (prev_s + success, prev_t + total)

        results['schema_results'] = schema_results

        # Calculate totals
        for prefix, (success, total) in schema_results.items():
            results['summary']['total_schema_files'] += success
            results['summary']['total_schema_attempts'] += total

    logger.info(f"Download pipeline complete: "
                f"{results['summary']['total_data_files']} data files, "
                f"{results['summary']['total_schema_files']} schema files")

    return results


def run_analysis(
    prefixes: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    aggregation_levels: list[str] | None = None,
    config: ProcessorConfig | None = None,
    output_folder: Path | None = None
) -> dict[str, Any]:
    """Run the analysis pipeline for performance data.

    Loads performance data and creates time series at various aggregation levels.
    Optionally saves results to files.

    Args:
        prefixes: List of prefixes to analyze (default: ['llmon1', 'llmon2'])
        start_date: Start date in YYYYMM format
        end_date: End date in YYYYMM format
        aggregation_levels: List of levels ('total', 'pool', 'issuer', 'loan')
        config: Processor configuration
        output_folder: Optional folder to save results

    Returns:
        Dictionary with analysis results

    Example:
        >>> results = run_analysis(
        ...     prefixes=['llmon1', 'llmon2'],
        ...     start_date='202301',
        ...     end_date='202312',
        ...     aggregation_levels=['total', 'pool']
        ... )
        >>> print(results['summary'])
    """
    if config is None:
        config = ProcessorConfig()

    if prefixes is None:
        prefixes = ['llmon1', 'llmon2']

    if aggregation_levels is None:
        aggregation_levels = ['total']

    logger.info(f"Starting analysis pipeline for prefixes: {', '.join(prefixes)}")
    if start_date:
        logger.info(f"Date range: {start_date} to {end_date or 'present'}")

    results: dict[str, Any] = {
        'prefixes': prefixes,
        'start_date': start_date,
        'end_date': end_date,
        'data': None,
        'statistics': {},
        'time_series': {},
        'summary': {}
    }

    # Load performance data
    logger.info("Loading performance data...")
    df = load_performance_data(
        prefixes=prefixes,
        data_folder=config.clean_folder,
        start_date=start_date,
        end_date=end_date,
        record_type='L',
        config=config
    )

    if df.is_empty():
        logger.warning("No data loaded - workflow cannot continue")
        results['summary']['status'] = 'no_data'
        return results

    results['data'] = df
    results['summary']['total_records'] = len(df)

    # Get summary statistics
    logger.info("Calculating summary statistics...")
    stats = get_summary_statistics(df)
    results['statistics'] = stats

    # Create time series at different aggregation levels
    logger.info(f"Creating time series at {len(aggregation_levels)} aggregation level(s)...")
    for level in aggregation_levels:
        logger.info(f"  - Creating {level} level time series")
        ts = create_upb_time_series(df, aggregation_level=level)
        results['time_series'][level] = ts

        # Save to file if output folder specified
        if output_folder and not ts.empty:
            output_folder = Path(output_folder)
            output_folder.mkdir(parents=True, exist_ok=True)

            filename = f"upb_timeseries_{level}_{datetime.now():%Y%m%d_%H%M%S}.csv"
            output_path = output_folder / filename
            ts.to_csv(output_path, index=False)
            logger.info(f"  - Saved to {output_path}")

    results['summary']['status'] = 'completed'
    results['summary']['aggregation_levels'] = aggregation_levels

    logger.info("Analysis pipeline complete")

    return results


def run_processing(
    prefixes: list[str] | None = None,
    include_staging: bool = True,
    include_transformation: bool = True,
    record_types: list[str] | None = None,
    config: ProcessorConfig | None = None
) -> dict[str, Any]:
    """Run the processing pipeline for specified prefixes.

    Orchestrates staging (raw to parquet) and transformation (parquet to structured)
    for the specified prefixes.

    Args:
        prefixes: List of prefixes to process (None for all)
        include_staging: Whether to run staging step
        include_transformation: Whether to run transformation step
        record_types: Optional list of record types to process
        config: Processor configuration (None for default)

    Returns:
        Dictionary with processing results and summary

    Example:
        >>> config = ProcessorConfig()
        >>> results = run_processing(['llmon1', 'monthly'], config=config)
        >>> print(f"Staged {results['staging_summary']['total_successful']} files")
        >>> print(f"Transformed {results['transformation_summary']['total_records']} records")
    """
    from .import_bronze import create_prefix_subfolders, import_prefix_bronze
    from .import_silver import import_prefix_silver

    if config is None:
        config = ProcessorConfig()

    # Load prefix dictionary
    prefix_dict = load_prefix_dictionary(config.prefix_file)

    # Determine prefixes to process
    if prefixes is None:
        prefixes = default_prefixes(prefix_dict)

    logger.info(f"Starting processing pipeline for {len(prefixes)} prefixes")
    logger.info(f"Prefixes: {', '.join(prefixes)}")

    results: dict[str, Any] = {
        'prefixes': prefixes,
        'staging_results': {},
        'transformation_results': {},
        'staging_summary': {
            'total_successful': 0,
            'total_failed': 0,
            'total_skipped': 0
        },
        'transformation_summary': {
            'total_files': 0,
            'total_successful': 0,
            'total_failed': 0,
            'total_records': 0
        },
        'summary': {
            'status': 'in_progress',
            'steps_completed': []
        }
    }

    # Step 1: Staging (raw files to parquet)
    if include_staging:
        logger.info("Step 1: Staging raw files to parquet...")
        try:
            for i, prefix in enumerate(prefixes, 1):
                logger.info(f"  [{i}/{len(prefixes)}] Staging {prefix}...")
                staging_result = import_prefix_bronze(
                    prefix=prefix,
                    base_folder=config.raw_folder,
                    output_folder=None,
                    config=config,
                    show_progress=False
                )
                results['staging_results'][prefix] = {
                    'successful': staging_result.successful,
                    'failed': staging_result.failed,
                    'skipped': staging_result.skipped,
                    'total': staging_result.total
                }
                results['staging_summary']['total_successful'] += staging_result.successful
                results['staging_summary']['total_failed'] += staging_result.failed
                results['staging_summary']['total_skipped'] += staging_result.skipped

            results['summary']['steps_completed'].append('staging')
            logger.info(f"✓ Staging complete: {results['staging_summary']['total_successful']} files staged")
        except Exception as e:
            logger.error(f"✗ Staging failed: {e}")
            results['summary']['status'] = 'failed_at_staging'
            return results

    # Create prefix subfolders if needed
    if include_transformation:
        try:
            logger.info("Creating prefix subfolders...")
            create_prefix_subfolders(prefixes=prefixes, config=config)
        except Exception as e:
            logger.warning(f"Error creating prefix subfolders: {e}")

    # Step 2: Transformation (parquet to structured data)
    if include_transformation:
        logger.info("Step 2: Transforming parquet to structured data...")
        try:
            for i, prefix in enumerate(prefixes, 1):
                logger.info(f"  [{i}/{len(prefixes)}] Transforming {prefix}...")
                transform_result = import_prefix_silver(
                    prefix=prefix,
                    raw_folder=config.bronze_folder,  # Read from bronze (staged parquet)
                    clean_folder=config.silver_folder,  # Write to silver
                    schema_folder=config.schema_folder,
                    config=config,
                    record_types=record_types,
                    show_progress=False
                )
                results['transformation_results'][prefix] = {
                    'total_files': transform_result.total_files,
                    'successful': transform_result.successful,
                    'failed': transform_result.failed,
                    'skipped': transform_result.skipped,
                    'total_records': transform_result.total_records_processed,
                    'records_by_type': transform_result.records_by_type
                }
                results['transformation_summary']['total_files'] += transform_result.total_files
                results['transformation_summary']['total_successful'] += transform_result.successful
                results['transformation_summary']['total_failed'] += transform_result.failed
                results['transformation_summary']['total_records'] += transform_result.total_records_processed

            results['summary']['steps_completed'].append('transformation')
            logger.info(f"✓ Transformation complete: {results['transformation_summary']['total_records']:,} records processed")
        except Exception as e:
            logger.error(f"✗ Transformation failed: {e}")
            results['summary']['status'] = 'failed_at_transformation'
            return results

    results['summary']['status'] = 'completed'
    logger.info(f"Processing pipeline complete: {', '.join(results['summary']['steps_completed'])}")

    return results


def run_pipeline(
    prefixes: list[str] | None = None,
    download_data: bool = True,
    process_data: bool = True,
    analyze_data: bool = True,
    start_date: str | None = None,
    end_date: str | None = None,
    download_config: DownloadConfig | None = None,
    processor_config: ProcessorConfig | None = None
) -> dict[str, Any]:
    """Run the complete end-to-end pipeline.

    Note: This function is partially implemented. It will be completed once
    the schema and processing modules are fully migrated.

    Current implementation:
        1. Download data and schemas
        2. Process data (TODO: requires processing module)
        3. Analyze performance data

    Args:
        prefixes: List of prefixes to process
        download_data: Whether to download data
        process_data: Whether to process data (TODO)
        analyze_data: Whether to analyze data
        start_date: Start date in YYYYMM format
        end_date: End date in YYYYMM format
        download_config: Download configuration
        processor_config: Processor configuration

    Returns:
        Dictionary with results from all workflow steps

    Example:
        >>> # Complete pipeline for monthly data
        >>> results = run_pipeline(
        ...     prefixes=['monthly'],
        ...     download_config=DownloadConfig(...),
        ...     processor_config=ProcessorConfig()
        ... )
        >>> print(results['summary'])
    """
    if download_config is None:
        download_config = DownloadConfig()

    if processor_config is None:
        processor_config = ProcessorConfig()

    logger.info("=" * 60)
    logger.info("STARTING COMPLETE PIPELINE")
    logger.info("=" * 60)

    results: dict[str, Any] = {
        'prefixes': prefixes,
        'download_results': None,
        'processing_results': None,
        'analysis_results': None,
        'summary': {
            'status': 'in_progress',
            'steps_completed': []
        }
    }

    # Step 1: Download
    if download_data:
        logger.info("\nSTEP 1: DOWNLOAD")
        logger.info("-" * 60)
        try:
            download_results = run_download(
                prefixes=prefixes,
                download_data=True,
                download_schemas=True,
                config=download_config
            )
            results['download_results'] = download_results
            results['summary']['steps_completed'].append('download')
            logger.info("✓ Download step completed")
        except Exception as e:
            logger.error(f"✗ Download step failed: {e}")
            results['summary']['status'] = 'failed_at_download'
            return results

    # Step 2: Process
    if process_data:
        logger.info("\nSTEP 2: PROCESS DATA")
        logger.info("-" * 60)
        try:
            processing_results = run_processing(
                prefixes=prefixes,
                include_staging=True,
                include_transformation=True,
                record_types=None,
                config=processor_config
            )
            results['processing_results'] = processing_results
            results['summary']['steps_completed'].append('processing')
            logger.info("✓ Processing step completed")
        except Exception as e:
            logger.error(f"✗ Processing step failed: {e}")
            results['summary']['status'] = 'failed_at_processing'
            return results

    # Step 3: Analyze
    if analyze_data:
        logger.info("\nSTEP 3: ANALYZE PERFORMANCE DATA")
        logger.info("-" * 60)
        try:
            # Only analyze performance prefixes
            perf_prefixes = [p for p in (prefixes or [])
                           if p in ['llmon1', 'llmon2', 'hllmon1', 'hllmon2']]

            if not perf_prefixes:
                perf_prefixes = ['llmon1', 'llmon2']

            analysis_results = run_analysis(
                prefixes=perf_prefixes,
                start_date=start_date,
                end_date=end_date,
                aggregation_levels=['total', 'pool'],
                config=processor_config
            )
            results['analysis_results'] = analysis_results
            results['summary']['steps_completed'].append('analysis')
            logger.info("✓ Analysis step completed")
        except Exception as e:
            logger.error(f"✗ Analysis step failed: {e}")
            results['summary']['status'] = 'failed_at_analysis'
            return results

    results['summary']['status'] = 'completed'

    logger.info("\n" + "=" * 60)
    logger.info("PIPELINE COMPLETED")
    logger.info("=" * 60)
    logger.info(f"Steps completed: {', '.join(results['summary']['steps_completed'])}")

    return results


def run_schemas(
    prefixes: list[str] | None = None,
    extract_from_pdfs: bool = False,
    combine_schemas_flag: bool = True,
    count_field_names: bool = True,
    standardize_names: bool = True,
    run_analysis: bool = True,
    config: SchemaReaderConfig | None = None,
    output_dir: Path | None = None
) -> dict[str, Any]:
    """Run the complete schema processing pipeline.

    This orchestrates the full process of turning raw PDF schema files into
    clean, combined, analyzed schema references:

    1. (Optional) Extract tables from PDF files
    2. Combine cleaned schemas by prefix
    3. Count field names across all schemas
    4. Standardize field names
    5. Run temporal coverage and format analysis

    Args:
        prefixes: List of prefixes to process (None for all)
        extract_from_pdfs: Whether to extract from PDFs first
        combine_schemas_flag: Whether to combine cleaned schemas
        count_field_names: Whether to count field names
        standardize_names: Whether to standardize field names
        run_analysis: Whether to run temporal/format analysis
        config: Schema reader configuration (None for default)
        output_dir: Output directory for analysis (None for default)

    Returns:
        Dictionary with pipeline results and statistics

    Example:
        >>> from gnma import run_schemas
        >>> results = run_schemas(
        ...     prefixes=['monthly', 'llmon1'],
        ...     extract_from_pdfs=True,
        ...     run_analysis=True
        ... )
        >>> print(f"Processed {results['summary']['prefixes_processed']} prefixes")
        >>> print(f"Found {results['summary']['unique_field_names']} unique field names")
    """
    from .schemas import (
        analyze_schema_formats,
        analyze_temporal_coverage,
        combine_all_schemas,
        count_field_names_from_schemas,
        extract_schemas_to_csv,
        standardize_field_names,
    )

    if config is None:
        config = SchemaReaderConfig()

    if output_dir is None:
        output_dir = GNMAConfig.GNMA_DATA_DIR / "schema_analysis"

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load prefix dictionary
    prefix_dict = load_prefix_dictionary(config.prefix_file)

    # Determine prefixes to process
    if prefixes is None:
        prefixes = default_prefixes(prefix_dict)

    logger.info(f"Starting schema pipeline for {len(prefixes)} prefixes")
    logger.info(f"Prefixes: {', '.join(prefixes)}")

    results: dict[str, Any] = {
        'prefixes': prefixes,
        'steps_completed': [],
        'extraction_results': {},
        'combination_results': {},
        'field_counts': None,
        'standardized_names': None,
        'temporal_coverage': None,
        'format_analysis': None,
        'summary': {
            'prefixes_processed': len(prefixes),
            'steps_completed': 0,
            'unique_field_names': 0,
            'standardized_field_names': 0,
        }
    }

    # Step 1: Extract from PDFs (optional)
    if extract_from_pdfs:
        logger.info("Step 1: Extracting schemas from PDF files...")
        try:
            extraction_results = extract_schemas_to_csv(
                prefixes=prefixes,
                prefix_dict=prefix_dict,
                config=config,
                augment_with_cobol=True
            )
            results['extraction_results'] = extraction_results
            results['steps_completed'].append('extraction')
            results['summary']['steps_completed'] += 1
            logger.info(f"✓ Extracted schemas for {len(extraction_results)} prefixes")
        except Exception as e:
            logger.error(f"✗ Extraction failed: {e}")
            results['extraction_error'] = str(e)
    else:
        logger.info("Step 1: Skipping PDF extraction (using existing cleaned CSVs)")

    # Step 2: Combine schemas
    if combine_schemas_flag:
        logger.info("Step 2: Combining cleaned schemas by prefix...")
        try:
            combination_results = combine_all_schemas(
                prefixes=prefixes,
                clean_dir=config.schemas_folder / 'clean',
                output_dir=config.schemas_folder / 'combined',
                verbose=config.verbose
            )
            results['combination_results'] = combination_results
            results['steps_completed'].append('combination')
            results['summary']['steps_completed'] += 1
            logger.info(f"✓ Combined schemas for {len(combination_results)} prefixes")
        except Exception as e:
            logger.error(f"✗ Combination failed: {e}")
            results['combination_error'] = str(e)

    # Step 3: Count field names
    if count_field_names:
        logger.info("Step 3: Counting field names across all schemas...")
        try:
            field_counts = count_field_names_from_schemas(
                clean_dir=config.schemas_folder / 'clean',
                output_dir=output_dir,
                save_results=True
            )
            results['field_counts'] = field_counts
            results['steps_completed'].append('field_counting')
            results['summary']['steps_completed'] += 1
            results['summary']['unique_field_names'] = len(field_counts)
            logger.info(f"✓ Counted {len(field_counts)} unique field names")
        except Exception as e:
            logger.error(f"✗ Field counting failed: {e}")
            results['field_counting_error'] = str(e)

    # Step 4: Standardize field names
    if standardize_names and results.get('field_counts') is not None:
        logger.info("Step 4: Standardizing field names...")
        try:
            standardized = standardize_field_names(
                input_df=results['field_counts'],
                output_dir=output_dir,
                save_results=True
            )
            results['standardized_names'] = standardized
            results['steps_completed'].append('standardization')
            results['summary']['steps_completed'] += 1
            results['summary']['standardized_field_names'] = standardized['Standardized_Field_Name'].nunique()

            num_changed = standardized['Changed'].sum()
            logger.info(f"✓ Standardized {len(standardized)} field names ({num_changed} changed)")
        except Exception as e:
            logger.error(f"✗ Standardization failed: {e}")
            results['standardization_error'] = str(e)
    elif standardize_names:
        logger.warning("Step 4: Skipping standardization (no field counts available)")

    # Step 5: Run analysis
    if run_analysis:
        logger.info("Step 5: Running schema analysis...")

        # Temporal coverage analysis
        try:
            temporal_coverage = analyze_temporal_coverage(
                combined_dir=config.schemas_folder / 'combined',
                output_dir=output_dir,
                save_analysis=True,
                verbose=config.verbose
            )
            results['temporal_coverage'] = temporal_coverage
            results['steps_completed'].append('temporal_analysis')
            results['summary']['steps_completed'] += 1
            logger.info(f"✓ Analyzed temporal coverage for {len(temporal_coverage)} prefixes")
        except Exception as e:
            logger.error(f"✗ Temporal coverage analysis failed: {e}")
            results['temporal_analysis_error'] = str(e)

        # Format analysis
        try:
            format_analysis = analyze_schema_formats(
                combined_dir=config.schemas_folder / 'combined',
                output_dir=output_dir,
                save_analysis=True,
                verbose=config.verbose
            )
            results['format_analysis'] = format_analysis
            results['steps_completed'].append('format_analysis')
            results['summary']['steps_completed'] += 1
            logger.info(f"✓ Analyzed schema formats for {len(format_analysis)} prefixes")
        except Exception as e:
            logger.error(f"✗ Format analysis failed: {e}")
            results['format_analysis_error'] = str(e)

    logger.info(f"Schema pipeline complete: {results['summary']['steps_completed']} steps completed")

    return results


def print_pipeline_summary(results: dict[str, Any]) -> None:
    """Log a formatted summary of pipeline results.

    Args:
        results: Results dictionary from any pipeline function
    """
    logger.info("Pipeline summary")

    # Download summary
    if 'download_results' in results and results['download_results']:
        dr = results['download_results']['summary']
        logger.info("Download:")
        logger.info(f"  Data files:   {dr['total_data_files']:>5} / {dr['total_data_attempts']}")
        logger.info(f"  Schema files: {dr['total_schema_files']:>5} / {dr['total_schema_attempts']}")

    # Analysis summary
    if 'analysis_results' in results and results['analysis_results']:
        ar = results['analysis_results']
        if ar.get('summary', {}).get('status') == 'completed':
            logger.info("Analysis:")
            logger.info(f"  Records loaded: {ar['summary']['total_records']:,}")
            if ar.get('statistics'):
                stats = ar['statistics']
                logger.info(f"  Total UPB: ${stats.get('total_upb', 0):,.2f}")
                logger.info(f"  Mean UPB: ${stats.get('mean_upb', 0):,.2f}")
                if 'unique_pools' in stats:
                    logger.info(f"  Unique pools: {stats['unique_pools']:,}")
        else:
            logger.info("Analysis: No data available")

    # Overall status
    if 'summary' in results:
        logger.info(f"Status: {results['summary'].get('status', 'unknown')}")
        if 'steps_completed' in results['summary']:
            logger.info(f"Steps completed: {', '.join(results['summary']['steps_completed'])}")

