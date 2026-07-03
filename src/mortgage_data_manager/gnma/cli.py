"""GNMA CLI - Main entry point.

Typer-based command-line interface for GNMA data management.
Surfaces download, schemas, bronze, silver, and pipeline subcommand groups.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from mortgage_data_manager import __version__
from mortgage_data_manager.core.cli.download_options import (
    OverwriteOption,
    QuietOption,
    VerboseOption,
)
from mortgage_data_manager.core.cli.formatting import (
    console,
    print_error,
    print_info_table,
    print_section_header,
    print_success,
    print_summary,
)
from mortgage_data_manager.core.cli.validation import version_callback_factory
from mortgage_data_manager.core.logging import configure_logging
from mortgage_data_manager.gnma.config import (
    DownloadConfig,
    GNMAConfig,
    ProcessorConfig,
    SchemaReaderConfig,
)
from mortgage_data_manager.gnma.download import create_session, download_bulk_data
from mortgage_data_manager.gnma.pipeline import (
    run_download as core_download_workflow,
)
from mortgage_data_manager.gnma.pipeline import (
    run_pipeline as core_complete_workflow,
)
from mortgage_data_manager.gnma.pipeline import (
    run_processing as core_processing_workflow,
)
from mortgage_data_manager.gnma.pipeline import (
    run_schemas as core_schema_workflow,
)
from mortgage_data_manager.gnma.utils import (
    create_configs_from_env,
    validate_date_format,
    validate_prefixes,
    validate_record_types,
)

# ============================================================================
# Root app
# ============================================================================

app = typer.Typer(
    name="gnma",
    help="GNMA Data Manager - Download and process GNMA mortgage data",
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=False,
)

_version_callback = version_callback_factory("GNMA Data Manager", __version__)


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit"
        )
    ] = False,
):
    """[bold cyan]GNMA Data Manager[/bold cyan] - Process GNMA mortgage data.

    [yellow]Quick Start:[/yellow]

      # Download data and schemas
      $ mortgage-data gnma download all monthly

      # Process schemas
      $ mortgage-data gnma schemas pipeline monthly

      # Stage raw data to bronze parquet
      $ mortgage-data gnma bronze monthly

      # Transform bronze to silver using schemas
      $ mortgage-data gnma silver monthly

      # Run complete pipeline (download + bronze + silver)
      $ mortgage-data gnma pipeline full monthly

    [yellow]Available Commands:[/yellow]

      [cyan]download[/cyan]   - Download data files and schemas from GNMA
      [cyan]schemas[/cyan]    - Process schema files (PDFs, cleaning, standardization)
      [cyan]bronze[/cyan]     - Stage raw data to bronze parquet
      [cyan]silver[/cyan]     - Transform bronze to silver using schemas
      [cyan]pipeline[/cyan]   - Run multi-step workflows (end-to-end automation)

    [yellow]Common Prefixes:[/yellow]

      [cyan]monthly[/cyan]    - Monthly disclosure data
      [cyan]llmon1[/cyan]     - Loan-level performance data (MBS programs)
      [cyan]llmon2[/cyan]     - Loan-level performance data (HMBS programs)
      [cyan]hllmon1[/cyan]    - Historical loan-level data (MBS)
      [cyan]hllmon2[/cyan]    - Historical loan-level data (HMBS)

    Use [bold]gnma COMMAND --help[/bold] for more information on a command.
    """
    configure_logging(level="INFO")


# ============================================================================
# Shared processing helper (bronze + silver commands)
# ============================================================================

def _run_processing(
    prefixes: list[str] | None = None,
    include_staging: bool = True,
    include_transformation: bool = True,
    record_types: list[str] | None = None,
    config: ProcessorConfig | None = None,
) -> dict[str, Any]:
    """Run the processing workflow with progress reporting.

    Args:
        prefixes: List of prefixes to process (None for all)
        include_staging: Whether to run staging step
        include_transformation: Whether to run transformation step
        record_types: Optional list of record types to process
        config: Processor configuration

    Returns:
        Dictionary with processing results
    """
    console.print("[cyan]Starting processing workflow...[/cyan]\n")

    results = core_processing_workflow(
        prefixes=prefixes,
        include_staging=include_staging,
        include_transformation=include_transformation,
        record_types=record_types,
        config=config,
    )

    if include_staging:
        staging = results.get('staging_summary', {})
        print_success(f"Staged {staging.get('total_successful', 0)} files")
        if staging.get('total_failed', 0) > 0:
            print_error(f"Failed to stage {staging['total_failed']} files")

    if include_transformation:
        transform = results.get('transformation_summary', {})
        print_success(f"Transformed {transform.get('total_records', 0):,} records")
        if transform.get('total_failed', 0) > 0:
            print_error(f"Failed to transform {transform['total_failed']} files")

    return results


# ============================================================================
# Download subapp
# ============================================================================

download_app = typer.Typer(
    name="download",
    help="Download GNMA data files and schemas",
    no_args_is_help=True,
)
app.add_typer(download_app, name="download")


@download_app.command("data")
def download_data(
    prefixes: Annotated[
        list[str] | None, typer.Argument(help="Prefixes to download (leave empty for all)")
    ] = None,
    config_path: Annotated[
        str | None, typer.Option("--config", "-c", help="Path to config file with credentials")
    ] = None,
    min_date: Annotated[
        str | None, typer.Option("--min-date", help="Start date in YYYYMM format (e.g. 202501)")
    ] = None,
    max_date: Annotated[
        str | None, typer.Option("--max-date", help="End date in YYYYMM format (e.g. 202612)")
    ] = None,
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Download GNMA data files for specified prefixes.

    Downloads data files from the GNMA bulk download portal.
    Requires valid credentials (email and ID) in config.

    Files already present on disk are skipped (only new months are fetched);
    pass [cyan]--overwrite[/cyan] to re-download everything in range.

    [yellow]Examples:[/yellow]

      # Download data for monthly prefix
      $ mortgage-data gnma download data monthly

      # Download for multiple prefixes
      $ mortgage-data gnma download data llmon1 llmon2

      # Download from 2025 onward
      $ mortgage-data gnma download data llmon1 llmon2 --min-date 202501

      # Download all prefixes (new months only)
      $ mortgage-data gnma download data

      # Force a full re-download
      $ mortgage-data gnma download data --overwrite

    [yellow]Common prefixes:[/yellow] monthly, llmon1, llmon2, hllmon1, hllmon2
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    print_section_header("Download GNMA Data Files")

    # Validate prefixes
    if prefixes:
        validate_prefixes(prefixes)
        console.print(f"[cyan]Prefixes:[/cyan] {', '.join(prefixes)}\n")
    else:
        console.print("[cyan]Prefixes:[/cyan] all\n")

    if min_date:
        console.print(f"[cyan]Min date:[/cyan] {min_date}")
    if max_date:
        console.print(f"[cyan]Max date:[/cyan] {max_date}")

    # Load config from environment variables
    if config_path:
        # TODO: Implement config file loading
        console.print(f"[yellow]Loading config from:[/yellow] {config_path}")

    download_config, _, _ = create_configs_from_env()
    console.print("[green]Credentials loaded from environment[/green]\n")

    console.print("[cyan]Starting download workflow...[/cyan]\n")
    parsed_start = datetime.strptime(min_date, "%Y%m") if min_date else None
    parsed_end = datetime.strptime(max_date, "%Y%m") if max_date else None
    results = core_download_workflow(
        prefixes=prefixes,
        download_data=True,
        download_schemas=False,
        config=download_config,
        start_date=parsed_start,
        end_date=parsed_end,
        overwrite=overwrite,
    )
    summary = results.get("summary", {})
    print_success(f"Downloaded {summary.get('total_data_files', 0)} data files")

    # Show summary
    print_summary(
        "Download Complete",
        {
            "Total Files": summary.get("total_data_files", 0),
            "Total Attempts": summary.get("total_data_attempts", 0),
        },
        style="green",
    )


@download_app.command("schemas")
def download_schemas(
    prefixes: Annotated[
        list[str] | None,
        typer.Argument(help="Prefixes to download schemas for (leave empty for all)"),
    ] = None,
    config_path: Annotated[
        str | None, typer.Option("--config", "-c", help="Path to config file")
    ] = None,
    source: Annotated[
        str,
        typer.Option(
            "--source",
            help="Schema source: 'disclosure' (per-prefix history page), "
            "'layout' (master bulk-layout page), or 'both'.",
        ),
    ] = "disclosure",
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Download GNMA schema files (PDFs) for specified prefixes.

    Downloads schema definition PDFs from the GNMA website. Two sources exist:
    the per-prefix disclosure-history page ([cyan]disclosure[/cyan], the default)
    and the master bulk-layout page ([cyan]layout[/cyan]), which documents prefixes
    the disclosure page omits (platcoll, hnissuesPS/S, issrcutoff, SRF, FRR, ...).

    Files already present on disk are skipped; pass [cyan]--overwrite[/cyan]
    to re-download them.

    [yellow]Examples:[/yellow]

      # Download schemas for monthly prefix
      $ mortgage-data gnma download schemas monthly

      # Download all schemas (skipping existing)
      $ mortgage-data gnma download schemas

      # Pull layouts the disclosure page lacks
      $ mortgage-data gnma download schemas platcoll SRF FRR --source layout

      # Force a full re-download
      $ mortgage-data gnma download schemas --overwrite

    [yellow]Common prefixes:[/yellow] monthly, llmon1, llmon2, hllmon1, hllmon2
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    if source not in ("disclosure", "layout", "both"):
        print_error("--source must be one of: disclosure, layout, both")
        raise typer.Exit(code=1)

    print_section_header("Download GNMA Schema Files")

    # Validate prefixes
    if prefixes:
        validate_prefixes(prefixes)
        console.print(f"[cyan]Prefixes:[/cyan] {', '.join(prefixes)}\n")
    else:
        console.print("[cyan]Prefixes:[/cyan] all\n")
    console.print(f"[cyan]Source:[/cyan] {source}\n")

    # Load config from environment variables
    download_config, _, _ = create_configs_from_env()
    console.print("[green]Credentials loaded from environment[/green]\n")

    console.print("[cyan]Starting download workflow...[/cyan]\n")
    results = core_download_workflow(
        prefixes=prefixes,
        download_data=False,
        download_schemas=True,
        schema_source=source,
        config=download_config,
        overwrite=overwrite,
    )
    summary = results.get("summary", {})
    print_success(f"Downloaded {summary.get('total_schema_files', 0)} schema files")

    # Show summary
    print_summary(
        "Download Complete",
        {
            "Total Files": summary.get("total_schema_files", 0),
            "Total Attempts": summary.get("total_schema_attempts", 0),
        },
        style="green",
    )


@download_app.command("all")
def download_all(
    prefixes: Annotated[
        list[str] | None, typer.Argument(help="Prefixes to download (leave empty for all)")
    ] = None,
    config_path: Annotated[
        str | None, typer.Option("--config", "-c", help="Path to config file with credentials")
    ] = None,
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Download both data and schema files.

    Convenience command to download everything in one step.
    Requires valid credentials for data download.

    Files already present on disk are skipped; pass [cyan]--overwrite[/cyan]
    to re-download everything.

    [yellow]Examples:[/yellow]

      # Download everything for monthly prefix
      $ mortgage-data gnma download all monthly

      # Download everything for all prefixes (skipping existing)
      $ mortgage-data gnma download all

      # Force a full re-download
      $ mortgage-data gnma download all --overwrite
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    print_section_header("Download All GNMA Files")

    # Validate prefixes
    if prefixes:
        validate_prefixes(prefixes)
        console.print(f"[cyan]Prefixes:[/cyan] {', '.join(prefixes)}\n")
    else:
        console.print("[cyan]Prefixes:[/cyan] all\n")

    # Load config from environment variables
    download_config, _, _ = create_configs_from_env()
    console.print("[green]Credentials loaded from environment[/green]\n")

    console.print("[cyan]Starting download workflow...[/cyan]\n")
    results = core_download_workflow(
        prefixes=prefixes,
        download_data=True,
        download_schemas=True,
        config=download_config,
        overwrite=overwrite,
    )
    summary = results.get("summary", {})
    print_success(f"Downloaded {summary.get('total_data_files', 0)} data files")
    print_success(f"Downloaded {summary.get('total_schema_files', 0)} schema files")

    # Show comprehensive summary
    print_summary(
        "Download Complete",
        {
            "Data Files": summary.get("total_data_files", 0),
            "Schema Files": summary.get("total_schema_files", 0),
            "Total Attempts": summary.get("total_data_attempts", 0)
            + summary.get("total_schema_attempts", 0),
        },
        style="green",
    )


@download_app.command("bulk")
def download_bulk(
    include_daily: Annotated[
        bool,
        typer.Option("--include-daily", help="Include daily snapshot files (normally skipped)"),
    ] = False,
    config_path: Annotated[
        str | None, typer.Option("--config", "-c", help="Path to config file with credentials")
    ] = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Download files from the GNMA bulk download page.

    The bulk page (bulk.ginniemae.gov) publishes the latest files before
    they appear on the history page. This command downloads them early.

    Undated monthly files (e.g. dailyllmni.zip) are automatically renamed
    with their reporting period. Daily snapshot files are skipped by default.

    Files downloaded from bulk are tracked so that subsequent history-page
    downloads (via 'gnma download data') overwrite them with the canonical
    version.

    [yellow]Examples:[/yellow]

      # Download latest bulk files
      $ mortgage-data gnma download bulk

      # Include daily snapshot files
      $ mortgage-data gnma download bulk --include-daily
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    print_section_header("Download GNMA Bulk Page Files")

    download_config, _, _ = create_configs_from_env()
    console.print("[green]Credentials loaded from environment[/green]\n")

    session = create_session(download_config)
    stats = download_bulk_data(
        session=session,
        config=download_config,
        skip_daily=not include_daily,
    )

    print_summary(
        "Bulk Download Complete",
        {
            "Downloaded": stats["downloaded"],
            "Skipped (existing)": stats["skipped"],
            "Skipped (daily)": stats["skipped_daily"],
            "Failed": stats["failed"],
        },
        style="green",
    )


# ============================================================================
# Schemas subapp
# ============================================================================

schemas_app = typer.Typer(
    name="schemas",
    help="Process GNMA schema files (PDFs, cleaning, standardization)",
    no_args_is_help=True,
)
app.add_typer(schemas_app, name="schemas")


def _run_schemas(
    prefixes: list[str] | None = None,
    extract_from_pdfs: bool = False,
    combine_schemas: bool = True,
    count_field_names: bool = True,
    standardize_names: bool = True,
    run_analysis: bool = True,
    config: SchemaReaderConfig | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Run the schema workflow with progress reporting.

    Args:
        prefixes: List of prefixes to process (None for all)
        extract_from_pdfs: Whether to extract from PDFs first
        combine_schemas: Whether to combine cleaned schemas
        count_field_names: Whether to count field names
        standardize_names: Whether to standardize field names
        run_analysis: Whether to run temporal/format analysis
        config: Schema reader configuration
        output_dir: Output directory for analysis

    Returns:
        Dictionary with workflow results
    """
    console.print("[cyan]Starting schema workflow...[/cyan]\n")

    results = core_schema_workflow(
        prefixes=prefixes,
        extract_from_pdfs=extract_from_pdfs,
        combine_schemas_flag=combine_schemas,
        count_field_names=count_field_names,
        standardize_names=standardize_names,
        run_analysis=run_analysis,
        config=config,
        output_dir=output_dir,
    )

    summary = results.get('summary', {})
    print_success(f"Completed {summary.get('steps_completed', 0)} schema processing steps")

    if summary.get('unique_field_names'):
        console.print(f"  [cyan]Unique field names:[/cyan] {summary['unique_field_names']}")
    if summary.get('standardized_field_names'):
        console.print(f"  [cyan]Standardized names:[/cyan] {summary['standardized_field_names']}")

    return results


@schemas_app.command("extract")
def extract_schemas(
    prefixes: Annotated[
        list[str] | None,
        typer.Argument(help="Prefixes to extract (leave empty for all)")
    ] = None,
    augment_cobol: Annotated[
        bool,
        typer.Option("--augment-cobol/--no-augment-cobol", help="Augment with COBOL data types")
    ] = True,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Extract schema tables from PDF files.

    Extracts data dictionary tables from GNMA PDF schema files
    and saves them as clean CSV files.

    [yellow]Examples:[/yellow]

      # Extract schemas for monthly prefix
      $ mortgage-data gnma schemas extract monthly

      # Extract all schemas
      $ mortgage-data gnma schemas extract

      # Extract without COBOL augmentation
      $ mortgage-data gnma schemas extract monthly --no-augment-cobol
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    print_section_header("Extract GNMA Schemas from PDFs")

    if prefixes:
        validate_prefixes(prefixes)
        console.print(f"[cyan]Prefixes:[/cyan] {', '.join(prefixes)}\n")
    else:
        console.print("[cyan]Prefixes:[/cyan] all\n")

    config = SchemaReaderConfig()
    results = _run_schemas(
        prefixes=prefixes,
        extract_from_pdfs=True,
        combine_schemas=False,
        count_field_names=False,
        standardize_names=False,
        run_analysis=False,
        config=config
    )

    summary = results.get('summary', {})
    print_summary("Extraction Complete", {
        "Prefixes Processed": summary.get('prefixes_processed', 0),
        "Steps Completed": summary.get('steps_completed', 0),
    }, style="green")


@schemas_app.command("combine")
def combine_schemas(
    prefixes: Annotated[
        list[str] | None,
        typer.Argument(help="Prefixes to combine (leave empty for all)")
    ] = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Combine cleaned schema files by prefix.

    Combines multiple year/version schemas into single files per prefix.

    [yellow]Examples:[/yellow]

      # Combine schemas for monthly prefix
      $ mortgage-data gnma schemas combine monthly

      # Combine all schemas
      $ mortgage-data gnma schemas combine
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    print_section_header("Combine GNMA Schemas")

    if prefixes:
        validate_prefixes(prefixes)
        console.print(f"[cyan]Prefixes:[/cyan] {', '.join(prefixes)}\n")
    else:
        console.print("[cyan]Prefixes:[/cyan] all\n")

    config = SchemaReaderConfig()
    results = _run_schemas(
        prefixes=prefixes,
        extract_from_pdfs=False,
        combine_schemas=True,
        count_field_names=False,
        standardize_names=False,
        run_analysis=False,
        config=config
    )

    summary = results.get('summary', {})
    print_summary("Combination Complete", {
        "Prefixes Processed": summary.get('prefixes_processed', 0),
    }, style="green")


@schemas_app.command("standardize")
def standardize_schemas(
    output_dir: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output directory for results")
    ] = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Count and standardize field names across all schemas.

    Analyzes field names from all schemas, counts occurrences,
    and creates standardized naming conventions.

    [yellow]Examples:[/yellow]

      # Standardize with default output
      $ mortgage-data gnma schemas standardize

      # Custom output directory
      $ mortgage-data gnma schemas standardize -o /custom/path
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    print_section_header("Standardize GNMA Field Names")

    output_dir = (output_dir or GNMAConfig.OUTPUT_DIR).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    console.print(f"[cyan]Output directory:[/cyan] {output_dir}\n")

    config = SchemaReaderConfig()
    results = _run_schemas(
        prefixes=None,
        extract_from_pdfs=False,
        combine_schemas=False,
        count_field_names=True,
        standardize_names=True,
        run_analysis=False,
        config=config,
        output_dir=output_dir
    )

    summary = results.get('summary', {})
    print_summary("Standardization Complete", {
        "Unique Field Names": summary.get('unique_field_names', 0),
        "Standardized Names": summary.get('standardized_field_names', 0),
    }, style="green")


@schemas_app.command("analyze")
def analyze_schemas(
    output_dir: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output directory for analysis results")
    ] = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Analyze temporal coverage and formats of schemas.

    Runs temporal coverage analysis and format analysis
    across all combined schemas.

    [yellow]Examples:[/yellow]

      # Analyze schemas
      $ mortgage-data gnma schemas analyze

      # Custom output directory
      $ mortgage-data gnma schemas analyze -o /custom/path
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    print_section_header("Analyze GNMA Schemas")

    output_dir = (output_dir or GNMAConfig.OUTPUT_DIR).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    console.print(f"[cyan]Output directory:[/cyan] {output_dir}\n")

    config = SchemaReaderConfig()
    results = _run_schemas(
        prefixes=None,
        extract_from_pdfs=False,
        combine_schemas=False,
        count_field_names=False,
        standardize_names=False,
        run_analysis=True,
        config=config,
        output_dir=output_dir
    )

    summary = results.get('summary', {})
    print_summary("Analysis Complete", {
        "Steps Completed": summary.get('steps_completed', 0),
    }, style="green")


@schemas_app.command("pipeline")
def schema_pipeline(
    prefixes: Annotated[
        list[str] | None,
        typer.Argument(help="Prefixes to process (leave empty for all)")
    ] = None,
    extract: Annotated[
        bool,
        typer.Option("--extract/--no-extract", help="Extract from PDFs")
    ] = True,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output directory for analysis")
    ] = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Run complete schema processing pipeline.

    Executes all schema processing steps:
      1. Extract PDFs to CSV (optional)
      2. Combine schemas by prefix
      3. Count field names
      4. Standardize field names
      5. Run temporal/format analysis

    [yellow]Examples:[/yellow]

      # Complete pipeline for monthly prefix
      $ mortgage-data gnma schemas pipeline monthly

      # Pipeline without extraction (use existing CSVs)
      $ mortgage-data gnma schemas pipeline monthly --no-extract

      # All prefixes with extraction
      $ mortgage-data gnma schemas pipeline
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    print_section_header("GNMA Schema Processing Pipeline")

    if prefixes:
        validate_prefixes(prefixes)
        console.print(f"[cyan]Prefixes:[/cyan] {', '.join(prefixes)}")
    else:
        console.print("[cyan]Prefixes:[/cyan] all")

    output_dir = (output_dir or GNMAConfig.OUTPUT_DIR).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    console.print(f"[cyan]Output directory:[/cyan] {output_dir}")
    console.print(f"[cyan]Extract from PDFs:[/cyan] {extract}\n")

    config = SchemaReaderConfig()
    results = _run_schemas(
        prefixes=prefixes,
        extract_from_pdfs=extract,
        combine_schemas=True,
        count_field_names=True,
        standardize_names=True,
        run_analysis=True,
        config=config,
        output_dir=output_dir
    )

    summary = results.get('summary', {})
    print_summary("Schema Pipeline Complete", {
        "Prefixes Processed": summary.get('prefixes_processed', 0),
        "Steps Completed": summary.get('steps_completed', 0),
        "Unique Field Names": summary.get('unique_field_names', 0),
        "Standardized Names": summary.get('standardized_field_names', 0),
    }, style="green")


# ============================================================================
# Pipeline subapp
# ============================================================================

pipeline_app = typer.Typer(
    name="pipeline",
    help="Run multi-step workflows (end-to-end automation)",
    no_args_is_help=True,
)
app.add_typer(pipeline_app, name="pipeline")


def _run_complete(
    prefixes: list[str] | None = None,
    download_data: bool = True,
    process_data: bool = True,
    analyze_data: bool = True,
    start_date: str | None = None,
    end_date: str | None = None,
    download_config: DownloadConfig | None = None,
    processor_config: ProcessorConfig | None = None,
) -> dict[str, Any]:
    """Run the complete workflow with progress reporting.

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
    """
    console.print("[bold cyan]Starting complete workflow...[/bold cyan]\n")

    results = core_complete_workflow(
        prefixes=prefixes,
        download_data=download_data,
        process_data=process_data,
        analyze_data=analyze_data,
        start_date=start_date,
        end_date=end_date,
        download_config=download_config,
        processor_config=processor_config,
    )

    summary = results.get('summary', {})
    if summary.get('status') == 'completed':
        print_success("Complete workflow finished successfully")
        console.print(f"  [cyan]Steps:[/cyan] {', '.join(summary.get('steps_completed', []))}")
    else:
        print_error(f"Workflow failed at: {summary.get('status', 'unknown')}")

    return results


@pipeline_app.command("full")
def full_pipeline(
    prefixes: Annotated[
        list[str],
        typer.Argument(help="Prefixes to process")
    ],
    start_date: Annotated[
        str | None,
        typer.Option("--start-date", "-s", help="Start date for analysis (YYYYMM)")
    ] = None,
    end_date: Annotated[
        str | None,
        typer.Option("--end-date", "-e", help="End date for analysis (YYYYMM)")
    ] = None,
    skip_download: Annotated[
        bool,
        typer.Option("--skip-download", help="Skip download step")
    ] = False,
    skip_processing: Annotated[
        bool,
        typer.Option("--skip-processing", help="Skip processing step")
    ] = False,
    skip_analysis: Annotated[
        bool,
        typer.Option("--skip-analysis", help="Skip analysis step")
    ] = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Run complete end-to-end pipeline.

    Executes all steps:
      1. Download data and schemas
      2. Process data (staging and transformation)
      3. Analyze performance data (if applicable)

    [yellow]Examples:[/yellow]

      # Complete pipeline for monthly prefix
      $ mortgage-data gnma pipeline full monthly

      # Pipeline with date range for analysis
      $ mortgage-data gnma pipeline full llmon1 llmon2 --start-date 202301 --end-date 202312

      # Skip download (data already exists)
      $ mortgage-data gnma pipeline full monthly --skip-download

      # Skip analysis (just download and process)
      $ mortgage-data gnma pipeline full monthly --skip-analysis

    [yellow]Common prefixes:[/yellow] monthly, llmon1, llmon2, hllmon1, hllmon2
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    print_section_header("Complete GNMA Pipeline")

    validate_prefixes(prefixes, allow_all=False)
    if start_date:
        validate_date_format(start_date)
    if end_date:
        validate_date_format(end_date)

    console.print(f"[cyan]Prefixes:[/cyan] {', '.join(prefixes)}")
    console.print(f"[cyan]Download:[/cyan] {not skip_download}")
    console.print(f"[cyan]Process:[/cyan] {not skip_processing}")
    console.print(f"[cyan]Analyze:[/cyan] {not skip_analysis}")
    if start_date or end_date:
        console.print(f"[cyan]Date range:[/cyan] {start_date or 'earliest'} to {end_date or 'latest'}")
    console.print()

    download_config = DownloadConfig()
    processor_config = ProcessorConfig()

    results = _run_complete(
        prefixes=prefixes,
        download_data=not skip_download,
        process_data=not skip_processing,
        analyze_data=not skip_analysis,
        start_date=start_date,
        end_date=end_date,
        download_config=download_config,
        processor_config=processor_config
    )

    # Show comprehensive summary
    summary = results.get('summary', {})
    summary_data = {
        "Status": summary.get('status', 'unknown'),
        "Steps Completed": ', '.join(summary.get('steps_completed', [])),
    }

    # Add download stats if available
    if not skip_download and results.get('download_results'):
        dl_summary = results['download_results'].get('summary', {})
        summary_data["Downloaded Files"] = (
            dl_summary.get('total_data_files', 0) +
            dl_summary.get('total_schema_files', 0)
        )

    # Add processing stats if available
    if not skip_processing and results.get('processing_results'):
        proc_summary = results['processing_results']
        if 'staging_summary' in proc_summary:
            summary_data["Staged Files"] = proc_summary['staging_summary'].get('total_successful', 0)
        if 'transformation_summary' in proc_summary:
            summary_data["Transformed Records"] = f"{proc_summary['transformation_summary'].get('total_records', 0):,}"

    # Add analysis stats if available
    if not skip_analysis and results.get('analysis_results'):
        anal_summary = results['analysis_results'].get('summary', {})
        if anal_summary.get('status') == 'completed':
            summary_data["Records Analyzed"] = f"{anal_summary.get('total_records', 0):,}"

    print_summary("Pipeline Complete", summary_data, style="green")


@pipeline_app.command("data-only")
def data_only_pipeline(
    prefixes: Annotated[
        list[str],
        typer.Argument(help="Prefixes to process")
    ],
    skip_download: Annotated[
        bool,
        typer.Option("--skip-download", help="Skip download step")
    ] = False,
    skip_staging: Annotated[
        bool,
        typer.Option("--skip-staging", help="Skip staging step")
    ] = False,
    skip_transformation: Annotated[
        bool,
        typer.Option("--skip-transformation", help="Skip transformation step")
    ] = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Run data pipeline only (skip analysis).

    Executes:
      1. Download data and schemas (optional)
      2. Stage to parquet (optional)
      3. Transform with schemas (optional)

    [yellow]Examples:[/yellow]

      # Download and process data
      $ mortgage-data gnma pipeline data-only monthly

      # Process only (data already downloaded)
      $ mortgage-data gnma pipeline data-only monthly --skip-download

      # Transform only (already staged)
      $ mortgage-data gnma pipeline data-only monthly --skip-download --skip-staging
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    print_section_header("Data Pipeline (Download + Process)")

    validate_prefixes(prefixes, allow_all=False)

    console.print(f"[cyan]Prefixes:[/cyan] {', '.join(prefixes)}")
    console.print(f"[cyan]Download:[/cyan] {not skip_download}")
    console.print(f"[cyan]Staging:[/cyan] {not skip_staging}")
    console.print(f"[cyan]Transformation:[/cyan] {not skip_transformation}")
    console.print()

    download_config = DownloadConfig()
    processor_config = ProcessorConfig()

    results = _run_complete(
        prefixes=prefixes,
        download_data=not skip_download,
        process_data=not (skip_staging and skip_transformation),
        analyze_data=False,
        download_config=download_config,
        processor_config=processor_config
    )

    # Show summary
    summary = results.get('summary', {})
    summary_data = {
        "Status": summary.get('status', 'unknown'),
        "Steps Completed": ', '.join(summary.get('steps_completed', [])),
    }

    if not skip_download and results.get('download_results'):
        dl_summary = results['download_results'].get('summary', {})
        summary_data["Downloaded Files"] = (
            dl_summary.get('total_data_files', 0) +
            dl_summary.get('total_schema_files', 0)
        )

    if results.get('processing_results'):
        proc_summary = results['processing_results']
        if not skip_staging and 'staging_summary' in proc_summary:
            summary_data["Staged Files"] = proc_summary['staging_summary'].get('total_successful', 0)
        if not skip_transformation and 'transformation_summary' in proc_summary:
            summary_data["Transformed Records"] = f"{proc_summary['transformation_summary'].get('total_records', 0):,}"

    print_summary("Data Pipeline Complete", summary_data, style="green")


@pipeline_app.command("schemas-only")
def schemas_only_pipeline(
    prefixes: Annotated[
        list[str] | None,
        typer.Argument(help="Prefixes to process (leave empty for all)")
    ] = None,
    extract: Annotated[
        bool,
        typer.Option("--extract/--no-extract", help="Extract from PDFs")
    ] = True,
):
    """Run schema processing only.

    Executes:
      1. Extract PDFs (optional)
      2. Combine schemas
      3. Count and standardize field names
      4. Run temporal/format analysis

    This is equivalent to: gnma schemas pipeline

    [yellow]Examples:[/yellow]

      # Process schemas for monthly prefix
      $ mortgage-data gnma pipeline schemas-only monthly

      # Process all schemas
      $ mortgage-data gnma pipeline schemas-only

      # Process without extraction
      $ mortgage-data gnma pipeline schemas-only monthly --no-extract
    """
    # Delegate to schemas pipeline command
    schema_pipeline(prefixes=prefixes, extract=extract, output_dir=None)


# ============================================================================
# Bronze command (flat)
# ============================================================================

@app.command("bronze")
def bronze(
    prefixes: Annotated[
        list[str],
        typer.Argument(help="Prefixes to stage to bronze")
    ],
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """Stage GNMA raw data files to bronze parquet.

    Converts raw text/zip files to parquet format with text content preserved
    in a single column. This is the first step before schema-based silver
    transformation.

    [yellow]Examples:[/yellow]

      # Bronze for monthly prefix
      $ mortgage-data gnma bronze monthly

      # Bronze for multiple prefixes
      $ mortgage-data gnma bronze llmon1 llmon2

    [yellow]Common prefixes:[/yellow] monthly, llmon1, llmon2, hllmon1, hllmon2
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    print_section_header("Stage GNMA Data to Bronze Parquet")

    validate_prefixes(prefixes, allow_all=False)
    console.print(f"[cyan]Prefixes:[/cyan] {', '.join(prefixes)}\n")

    config = ProcessorConfig()
    results = _run_processing(
        prefixes=prefixes,
        include_staging=True,
        include_transformation=False,
        config=config,
    )

    summary = results.get("staging_summary", {})
    print_summary(
        "Bronze Complete",
        {
            "Successful": summary.get("total_successful", 0),
            "Failed": summary.get("total_failed", 0),
            "Skipped": summary.get("total_skipped", 0),
        },
        style="green",
    )


# ============================================================================
# Silver command (flat)
# ============================================================================

@app.command("silver")
def silver(
    prefixes: Annotated[
        list[str],
        typer.Argument(help="Prefixes to transform to silver")
    ],
    record_types: Annotated[
        list[str] | None,
        typer.Option("--record-types", "-r", help="Record types to process (H, L, P, I, T)")
    ] = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """Transform GNMA bronze parquet to silver using schemas.

    Applies schema-based parsing to staged bronze parquet files, converting
    text content into structured data with proper column names and dtypes.

    [yellow]Examples:[/yellow]

      # Silver for monthly prefix (all record types)
      $ mortgage-data gnma silver monthly

      # Silver with specific record types
      $ mortgage-data gnma silver llmon1 -r L P

      # Silver for multiple prefixes
      $ mortgage-data gnma silver llmon1 llmon2

    [yellow]Valid record types:[/yellow] H (Header), L (Loan), P (Pool), I (Issuer), T (Trailer)
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    print_section_header("Transform GNMA Bronze to Silver")

    validate_prefixes(prefixes, allow_all=False)
    if record_types:
        validate_record_types(record_types)
        console.print(f"[cyan]Record types:[/cyan] {', '.join(record_types)}")
    console.print(f"[cyan]Prefixes:[/cyan] {', '.join(prefixes)}\n")

    config = ProcessorConfig()
    results = _run_processing(
        prefixes=prefixes,
        include_staging=False,
        include_transformation=True,
        record_types=record_types,
        config=config,
    )

    summary = results.get("transformation_summary", {})
    print_summary(
        "Silver Complete",
        {
            "Total Files": summary.get("total_files", 0),
            "Successful": summary.get("total_successful", 0),
            "Failed": summary.get("total_failed", 0),
            "Total Records": f"{summary.get('total_records', 0):,}",
        },
        style="green",
    )


# ============================================================================
# Info command
# ============================================================================

@app.command()
def info() -> None:
    """Display GNMA configuration information.

    Shows the current configuration paths and settings for GNMA data.

    Examples:
      mortgage-data gnma info
      gnma info
    """
    print_info_table(
        "GNMA Configuration",
        {
            "Data Directory": GNMAConfig.GNMA_DATA_DIR,
            "Raw Directory": GNMAConfig.GNMA_RAW_DIR,
            "Bronze Directory": GNMAConfig.GNMA_BRONZE_DIR,
            "Silver Directory": GNMAConfig.GNMA_SILVER_DIR,
            "Schemas Directory": GNMAConfig.GNMA_SCHEMAS_DIR,
        },
    )

    # Show available commands
    console.print("\n[bold cyan]Available Commands:[/bold cyan]")
    console.print("  download    - Download data files and schemas from GNMA")
    console.print("  schemas     - Process schema files (PDFs, cleaning, standardization)")
    console.print("  bronze      - Stage raw data to bronze parquet")
    console.print("  silver      - Transform bronze to silver using schemas")
    console.print("  pipeline    - Run multi-step workflows")
    console.print("  info        - Show this configuration information")


def cli_main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    cli_main()
