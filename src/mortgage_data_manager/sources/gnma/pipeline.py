#!/usr/bin/env python3
"""GNMA pipeline - high-level orchestration.

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

"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from mortgage_data_manager.core.download import DownloadResult, DownloadStatus
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import atomic_output

from .config import DownloadConfig, ProcessorConfig
from .download import (
    create_session,
    download_all_data,
    download_all_schemas,
    download_layout_schemas,
)
from .performance import create_upb_time_series, get_summary_statistics, load_performance_data
from .utils import create_configs_from_env, default_prefixes, load_prefix_dictionary

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
        ``data_results`` and ``schema_results`` (lists of DownloadResult) plus a
        ``summary`` of downloaded / attempted / failed counts for each.

    Example:
        >>> config = DownloadConfig(email_value="...", answer_value="...", user_agent="...")
        >>> results = run_download(['monthly', 'llmon1'], config=config)
        >>> summary = results["summary"]
        >>> print(f"Downloaded {summary['total_data_files']} data files")
    """
    if config is None:
        config, _ = create_configs_from_env()

    # Load prefix dictionary
    prefix_dict = load_prefix_dictionary(config.prefix_file)

    # Determine prefixes to process
    if prefixes is None:
        prefixes = default_prefixes(prefix_dict)

    logger.info("Starting download pipeline for %s prefixes", len(prefixes))
    logger.info("Prefixes: %s", ", ".join(prefixes))

    # Set up session
    session = create_session(config)

    data_results: list[DownloadResult] = []
    schema_results: list[DownloadResult] = []

    # Download data files
    if download_data:
        logger.info("Downloading data files...")
        data_results = download_all_data(
            prefixes,
            prefix_dict,
            session,
            config,
            start_date=start_date,
            end_date=end_date,
            overwrite=overwrite,
        )

    # Download schema files
    if download_schemas:
        if schema_source not in ("disclosure", "layout", "both"):
            raise ValueError(
                f"schema_source must be 'disclosure', 'layout', or 'both', got {schema_source!r}"
            )
        logger.info("Downloading schema files (source=%s)...", schema_source)
        if schema_source in ("disclosure", "both"):
            schema_results += download_all_schemas(prefixes, session, config, overwrite=overwrite)
        if schema_source in ("layout", "both"):
            schema_results += download_layout_schemas(
                prefixes, session, config, overwrite=overwrite
            )

    def count(results: list[DownloadResult], status: DownloadStatus) -> int:
        return sum(1 for r in results if r.status == status)

    summary = {
        "total_data_files": count(data_results, DownloadStatus.SUCCESS),
        "total_data_attempts": len(data_results),
        "total_data_failed": count(data_results, DownloadStatus.FAILED),
        "total_schema_files": count(schema_results, DownloadStatus.SUCCESS),
        "total_schema_attempts": len(schema_results),
        "total_schema_failed": count(schema_results, DownloadStatus.FAILED),
    }
    logger.info(
        "Download pipeline complete: %s data files, %s schema files",
        summary["total_data_files"],
        summary["total_schema_files"],
    )
    return {
        "prefixes": prefixes,
        "data_results": data_results,
        "schema_results": schema_results,
        "summary": summary,
    }


def run_analysis(
    start_date: str | None = None,
    end_date: str | None = None,
    aggregation_levels: list[str] | None = None,
    config: ProcessorConfig | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run the analysis pipeline over the single-family loan-level portfolio.

    Reads the portfolio through `scan_portfolio` (``llmon1``/``llmon2`` before 2026-07,
    ``llmon`` from it), computes summary statistics and UPB time series at the requested
    aggregation levels, and optionally writes each series to CSV.

    Args:
        start_date: Start reporting month in YYYYMM format
        end_date: End reporting month in YYYYMM format
        aggregation_levels: List of levels ('total', 'pool', 'issuer', 'loan')
        config: Processor configuration (its ``silver_folder`` is read)
        output_dir: Directory to write each series to as CSV; None writes nothing

    Returns:
        Dictionary with analysis results; ``summary["status"]`` is ``"no_data"`` when no
        portfolio month falls in the range.

    Example:
        >>> results = run_analysis(
        ...     start_date='202301',
        ...     end_date='202312',
        ...     aggregation_levels=['total', 'pool']
        ... )
        >>> print(results['summary'])
    """
    if config is None:
        config = ProcessorConfig()

    if aggregation_levels is None:
        aggregation_levels = ["total"]

    logger.info("Starting analysis pipeline for the loan-level portfolio")
    if start_date:
        logger.info("Date range: %s to %s", start_date, end_date or "present")

    results: dict[str, Any] = {
        "start_date": start_date,
        "end_date": end_date,
        "data": None,
        "statistics": {},
        "time_series": {},
        "summary": {},
    }

    try:
        lf = load_performance_data(
            start_date=start_date, end_date=end_date, silver_dir=config.silver_folder
        )
    except FileNotFoundError as e:
        logger.warning("No data loaded - workflow cannot continue: %s", e)
        results["summary"]["status"] = "no_data"
        return results

    results["data"] = lf
    stats = get_summary_statistics(lf)
    results["statistics"] = stats
    results["summary"]["total_records"] = stats.get("total_loans", 0)

    logger.info("Creating time series at %s aggregation level(s)...", len(aggregation_levels))
    for level in aggregation_levels:
        ts = create_upb_time_series(lf, aggregation_level=level)
        results["time_series"][level] = ts
        if output_dir and not ts.empty:
            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)
            output_path = out / f"upb_timeseries_{level}_{datetime.now():%Y%m%d_%H%M%S}.csv"
            with atomic_output(output_path) as tmp:
                ts.to_csv(tmp, index=False)
            logger.info("  - Saved to %s", output_path)

    results["summary"]["status"] = "completed"
    results["summary"]["aggregation_levels"] = aggregation_levels
    logger.info("Analysis pipeline complete")
    return results


def run_processing(
    prefixes: list[str] | None = None,
    include_staging: bool = True,
    include_transformation: bool = True,
    record_types: list[str] | None = None,
    config: ProcessorConfig | None = None,
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
        Dictionary with processing results and summary. ``summary["status"]`` is
        ``"completed"`` only when no file failed; a run where some file failed to stage
        or transform finishes every prefix and reports ``"completed_with_failures"``.

    Example:
        >>> config = ProcessorConfig()
        >>> results = run_processing(['llmon1', 'monthly'], config=config)
        >>> staged = results["staging_summary"]
        >>> print(f"Staged {staged['total_successful']} files")
        >>> transformed = results["transformation_summary"]
        >>> print(f"Transformed {transformed['total_records']} records")
    """
    from .import_bronze import import_prefix_bronze
    from .import_silver import import_prefix_silver

    if config is None:
        config = ProcessorConfig()

    # Load prefix dictionary
    prefix_dict = load_prefix_dictionary(config.prefix_file)

    # Determine prefixes to process
    if prefixes is None:
        prefixes = default_prefixes(prefix_dict)

    logger.info("Starting processing pipeline for %s prefixes", len(prefixes))
    logger.info("Prefixes: %s", ", ".join(prefixes))

    results: dict[str, Any] = {
        "prefixes": prefixes,
        "staging_results": {},
        "transformation_results": {},
        "staging_summary": {"total_successful": 0, "total_failed": 0, "total_skipped": 0},
        "transformation_summary": {
            "total_files": 0,
            "total_successful": 0,
            "total_failed": 0,
            "total_records": 0,
        },
        "summary": {"status": "in_progress", "steps_completed": []},
    }

    # Step 1: Staging (raw files to parquet)
    if include_staging:
        logger.info("Step 1: Staging raw files to parquet...")
        try:
            for i, prefix in enumerate(prefixes, 1):
                logger.info("  [%s/%s] Staging %s...", i, len(prefixes), prefix)
                staging_result = import_prefix_bronze(
                    prefix=prefix,
                    raw_dir=config.raw_folder,
                    output_dir=None,
                    config=config,
                    show_progress=False,
                )
                results["staging_results"][prefix] = {
                    "successful": staging_result.successful,
                    "failed": staging_result.failed,
                    "skipped": staging_result.skipped,
                    "total": staging_result.total,
                    "errors": staging_result.errors,
                }
                results["staging_summary"]["total_successful"] += staging_result.successful
                results["staging_summary"]["total_failed"] += staging_result.failed
                results["staging_summary"]["total_skipped"] += staging_result.skipped

            results["summary"]["steps_completed"].append("staging")
            logger.info(
                "✓ Staging complete: %s files staged",
                results["staging_summary"]["total_successful"],
            )
        except Exception as e:
            logger.error("✗ Staging failed: %s", e)
            results["summary"]["status"] = "failed_at_staging"
            return results

    # Step 2: Transformation (parquet to structured data)
    if include_transformation:
        logger.info("Step 2: Transforming parquet to structured data...")
        try:
            for i, prefix in enumerate(prefixes, 1):
                logger.info("  [%s/%s] Transforming %s...", i, len(prefixes), prefix)
                transform_result = import_prefix_silver(
                    prefix=prefix,
                    bronze_dir=config.bronze_folder,
                    output_dir=config.silver_folder,
                    config=config,
                    record_types=record_types,
                    show_progress=False,
                )
                results["transformation_results"][prefix] = {
                    "total_files": transform_result.total_files,
                    "successful": transform_result.successful,
                    "failed": transform_result.failed,
                    "skipped": transform_result.skipped,
                    "total_records": transform_result.total_records_processed,
                    "records_by_type": transform_result.records_by_type,
                    "errors": transform_result.errors,
                }
                results["transformation_summary"]["total_files"] += transform_result.total_files
                results["transformation_summary"]["total_successful"] += transform_result.successful
                results["transformation_summary"]["total_failed"] += transform_result.failed
                results["transformation_summary"]["total_records"] += (
                    transform_result.total_records_processed
                )

            results["summary"]["steps_completed"].append("transformation")
            logger.info(
                "✓ Transformation complete: %s records processed",
                f"{results['transformation_summary']['total_records']:,}",
            )
        except Exception as e:
            logger.error("✗ Transformation failed: %s", e)
            results["summary"]["status"] = "failed_at_transformation"
            return results

    n_failed = (
        results["staging_summary"]["total_failed"]
        + results["transformation_summary"]["total_failed"]
    )
    results["summary"]["status"] = "completed" if n_failed == 0 else "completed_with_failures"
    logger.info(
        "Processing pipeline complete: %s (%s file failure(s))",
        ", ".join(results["summary"]["steps_completed"]),
        n_failed,
    )

    return results


def run_pipeline(
    prefixes: list[str] | None = None,
    download_data: bool = True,
    process_data: bool = True,
    analyze_data: bool = True,
    start_date: str | None = None,
    end_date: str | None = None,
    download_config: DownloadConfig | None = None,
    processor_config: ProcessorConfig | None = None,
) -> dict[str, Any]:
    """Run the complete end-to-end pipeline.

    Steps, each skippable by its flag:
        1. Download data and schemas
        2. Process data (staging and transformation)
        3. Analyze performance data

    Args:
        prefixes: List of prefixes to process
        download_data: Whether to download data
        process_data: Whether to process data
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
        download_config, _ = create_configs_from_env()

    if processor_config is None:
        processor_config = ProcessorConfig()

    logger.info("=" * 60)
    logger.info("STARTING COMPLETE PIPELINE")
    logger.info("=" * 60)

    results: dict[str, Any] = {
        "prefixes": prefixes,
        "download_results": None,
        "processing_results": None,
        "analysis_results": None,
        "summary": {"status": "in_progress", "steps_completed": []},
    }

    # Step 1: Download
    if download_data:
        logger.info("\nSTEP 1: DOWNLOAD")
        logger.info("-" * 60)
        try:
            download_results = run_download(
                prefixes=prefixes, download_data=True, download_schemas=True, config=download_config
            )
            results["download_results"] = download_results
            results["summary"]["steps_completed"].append("download")
            logger.info("✓ Download step completed")
        except Exception as e:
            logger.error("✗ Download step failed: %s", e)
            results["summary"]["status"] = "failed_at_download"
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
                config=processor_config,
            )
            results["processing_results"] = processing_results
            if processing_results["summary"]["status"] != "completed":
                logger.error("✗ Processing step: %s", processing_results["summary"]["status"])
                results["summary"]["status"] = "failed_at_processing"
                return results
            results["summary"]["steps_completed"].append("processing")
            logger.info("✓ Processing step completed")
        except Exception as e:
            logger.error("✗ Processing step failed: %s", e)
            results["summary"]["status"] = "failed_at_processing"
            return results

    # Step 3: Analyze
    if analyze_data:
        logger.info("\nSTEP 3: ANALYZE PERFORMANCE DATA")
        logger.info("-" * 60)
        try:
            analysis_results = run_analysis(
                start_date=start_date,
                end_date=end_date,
                aggregation_levels=["total", "pool"],
                config=processor_config,
            )
            results["analysis_results"] = analysis_results
            results["summary"]["steps_completed"].append("analysis")
            logger.info("✓ Analysis step completed")
        except Exception as e:
            logger.error("✗ Analysis step failed: %s", e)
            results["summary"]["status"] = "failed_at_analysis"
            return results

    results["summary"]["status"] = "completed"

    logger.info("\n%s", "=" * 60)
    logger.info("PIPELINE COMPLETED")
    logger.info("=" * 60)
    logger.info("Steps completed: %s", ", ".join(results["summary"]["steps_completed"]))

    return results
