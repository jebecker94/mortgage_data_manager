"""GNMA CLI - Main entry point.

Typer-based command-line interface for GNMA data management.
Surfaces download, bronze, silver, and pipeline subcommand groups.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

import typer

from mortgage_data_manager import __version__
from mortgage_data_manager.core.cli.download_options import (
    OverwriteOption,
    QuietOption,
    VerboseOption,
    report_download_results,
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
from mortgage_data_manager.core.download import DownloadResult
from mortgage_data_manager.core.logging import configure_logging
from mortgage_data_manager.sources.gnma.config import (
    DEFAULT_DOWNLOAD_DATASETS,
    DOWNLOAD_DATASETS,
    GNMAConfig,
    ProcessorConfig,
    resolve_download_datasets,
)
from mortgage_data_manager.sources.gnma.download import create_session, download_bulk_data
from mortgage_data_manager.sources.gnma.pipeline import (
    run_download as core_download_workflow,
)
from mortgage_data_manager.sources.gnma.pipeline import (
    run_pipeline as core_complete_workflow,
)
from mortgage_data_manager.sources.gnma.pipeline import (
    run_processing as core_processing_workflow,
)
from mortgage_data_manager.sources.gnma.utils import (
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
            help="Show version and exit",
        ),
    ] = False,
):
    """[bold cyan]GNMA Data Manager[/bold cyan] - Process GNMA mortgage data.

    [yellow]Quick Start:[/yellow]

      # Download data and schemas
      $ mortgage-data gnma download monthly

      # Stage raw data to bronze parquet
      $ mortgage-data gnma bronze monthly

      # Transform bronze to silver using schemas
      $ mortgage-data gnma silver monthly

      # Run complete pipeline (download + bronze + silver)
      $ mortgage-data gnma pipeline monthly

    [yellow]Available Commands:[/yellow]

      [cyan]download[/cyan]   - Download data files and schemas from GNMA
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
        staging = results.get("staging_summary", {})
        print_success(f"Staged {staging.get('total_successful', 0)} files")
        if staging.get("total_failed", 0) > 0:
            print_error(f"Failed to stage {staging['total_failed']} files")

    if include_transformation:
        transform = results.get("transformation_summary", {})
        print_success(f"Transformed {transform.get('total_records', 0):,} records")
        if transform.get("total_failed", 0) > 0:
            print_error(f"Failed to transform {transform['total_failed']} files")

    return results


# ============================================================================
# Download
# ============================================================================


@app.command()
def download(
    prefixes: Annotated[
        list[str] | None, typer.Argument(help="Prefixes to download (leave empty for all)")
    ] = None,
    datasets: Annotated[
        list[str] | None,
        typer.Option(
            "--datasets",
            "-t",
            help=(
                f"What to fetch: {', '.join(DOWNLOAD_DATASETS)} "
                f"(repeatable; default {', '.join(DEFAULT_DOWNLOAD_DATASETS)})"
            ),
        ),
    ] = None,
    source: Annotated[
        str,
        typer.Option(
            "--source",
            help="Schema source: 'disclosure' (per-prefix history page), "
            "'layout' (master bulk-layout page), or 'both'.",
        ),
    ] = "disclosure",
    min_date: Annotated[
        str | None, typer.Option("--min-date", help="Start date in YYYYMM format (e.g. 202501)")
    ] = None,
    max_date: Annotated[
        str | None, typer.Option("--max-date", help="End date in YYYYMM format (e.g. 202612)")
    ] = None,
    include_daily: Annotated[
        bool,
        typer.Option("--include-daily", help="Bulk only: include daily snapshot files"),
    ] = False,
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Download GNMA data files, schema PDFs, or bulk-page files.

    Datasets (default [cyan]data[/cyan] + [cyan]schemas[/cyan]):
      - [cyan]data[/cyan]: data files from the disclosure history page
      - [cyan]schemas[/cyan]: schema definition PDFs. Two sources exist — the
        per-prefix disclosure-history page ([cyan]--source disclosure[/cyan],
        the default) and the master bulk-layout page ([cyan]--source layout[/cyan]),
        which documents prefixes the disclosure page omits (platcoll,
        hnissuesPS/S, issrcutoff, SRF, FRR, ...)
      - [cyan]bulk[/cyan]: the bulk page (bulk.ginniemae.gov), which publishes
        the latest files before they reach the history page. Opt-in, so it is
        NOT part of the default set. Undated monthly files (e.g. dailyllmni.zip)
        are renamed with their reporting period; daily snapshots are skipped
        unless [cyan]--include-daily[/cyan]. Bulk downloads are tracked so a later
        history-page download overwrites them with the canonical version.

    Requires valid credentials (GNMA_EMAIL and GNMA_ANSWER). Files already on
    disk are skipped; pass [cyan]--overwrite[/cyan] to re-download.

    [yellow]Examples:[/yellow]

      # Data + schemas for one prefix
      $ mortgage-data gnma download monthly

      # Data only, from 2025 onward
      $ mortgage-data gnma download llmon1 llmon2 -t data --min-date 202501

      # Pull layouts the disclosure page lacks
      $ mortgage-data gnma download platcoll SRF FRR -t schemas --source layout

      # Fetch this month's files early from the bulk page
      $ mortgage-data gnma download -t bulk

    [yellow]Common prefixes:[/yellow] monthly, llmon1, llmon2, hllmon1, hllmon2
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    if source not in ("disclosure", "layout", "both"):
        print_error("--source must be one of: disclosure, layout, both")
        raise typer.Exit(code=1)
    try:
        selected = resolve_download_datasets(datasets)
    except ValueError as e:
        print_error(str(e))
        raise typer.Exit(code=1)

    print_section_header("Download GNMA Files")

    if prefixes:
        validate_prefixes(prefixes)
        console.print(f"[cyan]Prefixes:[/cyan] {', '.join(prefixes)}")
    else:
        console.print("[cyan]Prefixes:[/cyan] all")
    console.print(f"[cyan]Datasets:[/cyan] {', '.join(selected)}")
    if "schemas" in selected:
        console.print(f"[cyan]Schema source:[/cyan] {source}")
    if min_date:
        console.print(f"[cyan]Min date:[/cyan] {min_date}")
    if max_date:
        console.print(f"[cyan]Max date:[/cyan] {max_date}")
    console.print()

    download_config, _ = create_configs_from_env()
    console.print("[green]Credentials loaded from environment[/green]\n")

    all_results: list[DownloadResult] = []

    if "data" in selected or "schemas" in selected:
        console.print("[cyan]Starting download workflow...[/cyan]\n")
        parsed_start = datetime.strptime(min_date, "%Y%m") if min_date else None
        parsed_end = datetime.strptime(max_date, "%Y%m") if max_date else None
        results = core_download_workflow(
            prefixes=prefixes,
            download_data="data" in selected,
            download_schemas="schemas" in selected,
            schema_source=source,
            config=download_config,
            start_date=parsed_start,
            end_date=parsed_end,
            overwrite=overwrite,
        )
        summary = results["summary"]
        if "data" in selected:
            console.print(f"  data: {summary['total_data_files']} downloaded")
        if "schemas" in selected:
            console.print(f"  schemas: {summary['total_schema_files']} downloaded")
        all_results += results["data_results"] + results["schema_results"]

    if "bulk" in selected:
        console.print("[cyan]Starting bulk-page download...[/cyan]\n")
        session = create_session(download_config)
        bulk = download_bulk_data(
            session=session,
            config=download_config,
            skip_daily=not include_daily,
        )
        console.print(f"  bulk: {sum(r.status.value == 'success' for r in bulk)} downloaded")
        all_results += bulk

    report_download_results(all_results)


# ============================================================================
# Pipeline
# ============================================================================


@app.command()
def pipeline(
    prefixes: Annotated[list[str], typer.Argument(help="Prefixes to process")],
    start_date: Annotated[
        str | None, typer.Option("--start-date", "-s", help="Start date for analysis (YYYYMM)")
    ] = None,
    end_date: Annotated[
        str | None, typer.Option("--end-date", "-e", help="End date for analysis (YYYYMM)")
    ] = None,
    skip_download: Annotated[
        bool, typer.Option("--skip-download", help="Skip download step")
    ] = False,
    skip_processing: Annotated[
        bool, typer.Option("--skip-processing", help="Skip processing step (staging + transform)")
    ] = False,
    skip_analysis: Annotated[
        bool, typer.Option("--skip-analysis", help="Skip analysis step")
    ] = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Run the end-to-end GNMA pipeline.

    Executes, in order:
      1. Download data and schemas
      2. Process (stage to parquet, then transform with schemas)
      3. Analyze performance data (where applicable)

    Each step can be skipped. For the download+process path without analysis,
    pass [cyan]--skip-analysis[/cyan].

    [yellow]Examples:[/yellow]

      # Complete pipeline for one prefix
      $ mortgage-data gnma pipeline monthly

      # With a date range for analysis
      $ mortgage-data gnma pipeline llmon1 llmon2 --start-date 202301 --end-date 202312

      # Data only (download + process, no analysis)
      $ mortgage-data gnma pipeline monthly --skip-analysis

      # Process only (data already downloaded)
      $ mortgage-data gnma pipeline monthly --skip-download --skip-analysis

    [yellow]Common prefixes:[/yellow] monthly, llmon1, llmon2, hllmon1, hllmon2
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    print_section_header("GNMA Pipeline")

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
        console.print(
            f"[cyan]Date range:[/cyan] {start_date or 'earliest'} to {end_date or 'latest'}"
        )
    console.print()

    console.print("[bold cyan]Starting complete workflow...[/bold cyan]\n")
    download_config, _ = create_configs_from_env()
    results = core_complete_workflow(
        prefixes=prefixes,
        download_data=not skip_download,
        process_data=not skip_processing,
        analyze_data=not skip_analysis,
        start_date=start_date,
        end_date=end_date,
        download_config=download_config,
        processor_config=ProcessorConfig(),
    )

    summary = results.get("summary", {})
    status = summary.get("status", "unknown")
    summary_data: dict[str, Any] = {
        "Status": status,
        "Steps Completed": ", ".join(summary.get("steps_completed", [])),
    }
    if not skip_download and results.get("download_results"):
        dl = results["download_results"].get("summary", {})
        summary_data["Downloaded Files"] = dl.get("total_data_files", 0) + dl.get(
            "total_schema_files", 0
        )
    if not skip_processing and results.get("processing_results"):
        proc = results["processing_results"]
        if "staging_summary" in proc:
            summary_data["Staged Files"] = proc["staging_summary"].get("total_successful", 0)
        if "transformation_summary" in proc:
            summary_data["Transformed Records"] = (
                f"{proc['transformation_summary'].get('total_records', 0):,}"
            )
    if not skip_analysis and results.get("analysis_results"):
        anal = results["analysis_results"].get("summary", {})
        if anal.get("status") == "completed":
            summary_data["Records Analyzed"] = f"{anal.get('total_records', 0):,}"

    # run_pipeline reports a failed step by setting summary.status to
    # 'failed_at_<step>' rather than raising, so the CLI has to check it —
    # otherwise a failed run prints a green summary and exits 0.
    succeeded = status == "completed"
    print_summary(
        "Pipeline Complete" if succeeded else "Pipeline Failed",
        summary_data,
        style="green" if succeeded else "red",
    )
    if not succeeded:
        print_error(f"Workflow failed at: {status}")
        raise typer.Exit(code=1)


# ============================================================================
# Bronze command (flat)
# ============================================================================


@app.command("bronze")
def bronze(
    prefixes: Annotated[list[str], typer.Argument(help="Prefixes to stage to bronze")],
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
    prefixes: Annotated[list[str], typer.Argument(help="Prefixes to transform to silver")],
    record_types: Annotated[
        list[str] | None,
        typer.Option("--record-types", "-r", help="Record types to process (H, L, P, I, T)"),
    ] = None,
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """Transform GNMA bronze parquet to silver using schemas.

    Applies schema-based parsing to staged bronze parquet files, converting
    text content into structured data with proper column names and dtypes.

    Existing silver files are skipped; [cyan]--overwrite[/cyan] rebuilds them. The
    rebuild is scoped to the [cyan]{prefix}/{record_type}[/cyan] files it writes —
    no other silver output is touched.

    [yellow]Examples:[/yellow]

      # Silver for monthly prefix (all record types)
      $ mortgage-data gnma silver monthly

      # Silver with specific record types
      $ mortgage-data gnma silver llmon1 -r L P

      # Rebuild one prefix after a schema change
      $ mortgage-data gnma silver platcoll --overwrite

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

    config = ProcessorConfig(overwrite=overwrite)
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
    console.print("  bronze      - Stage raw data to bronze parquet")
    console.print("  silver      - Transform bronze to silver via the canonical schema")
    console.print("  pipeline    - Run multi-step workflows")
    console.print("  info        - Show this configuration information")


def cli_main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    cli_main()
