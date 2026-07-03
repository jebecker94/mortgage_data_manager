"""FHLB CLI - Main entry point.

Typer-based command-line interface for FHLB data management.
Surfaces download, bronze, silver, and pipeline subcommand groups.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from mortgage_data_manager import __version__
from mortgage_data_manager.core.cli.download_options import (
    OverwriteOption,
    PauseOption,
    QuietOption,
    RetriesOption,
    TimeoutOption,
    VerboseOption,
)
from mortgage_data_manager.core.cli.formatting import console
from mortgage_data_manager.core.cli.validation import version_callback_factory
from mortgage_data_manager.core.download import summarize_results
from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.fhlb.config import FHLBConfig
from mortgage_data_manager.fhlb.download import (
    download_ama_data,
    download_ama_dictionaries,
    download_members_data,
)
from mortgage_data_manager.fhlb.import_bronze import (
    import_ama_bronze,
    import_members_bronze,
)
from mortgage_data_manager.fhlb.import_silver import import_ama_silver
from mortgage_data_manager.fhlb.pipeline import run_pipeline

# ============================================================================
# Root app
# ============================================================================

app = typer.Typer(
    name="fhlb",
    help="FHLB data management commands",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

logger = get_logger(__name__)

_version_callback = version_callback_factory("FHLB Data Manager", __version__)


@app.callback()
def callback(
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
    """[bold cyan]FHLB Data Manager[/bold cyan].

    Tools for processing Federal Home Loan Bank (FHLB) data including:
    - Member institution data (Excel files)
    - AMA (Acquired Member Assets) loan-level data (CSV files)
    """
    configure_logging(level="INFO")


def _report_results(results: list, quiet: bool = False) -> None:
    """Report download results and exit with appropriate code."""
    summary = summarize_results(results)
    success_count = summary.get("success", 0)
    skipped_count = summary.get("skipped_exists", 0)
    failed_count = summary.get("failed", 0)

    if failed_count > 0:
        console.print(
            f"[yellow]Download completed with errors: "
            f"{success_count} downloaded, {skipped_count} skipped, {failed_count} failed[/yellow]"
        )
        raise typer.Exit(code=1)
    else:
        console.print(
            f"[green]Download complete: {success_count} downloaded, {skipped_count} skipped[/green]"
        )


# ============================================================================
# Download subapp
# ============================================================================

download_app = typer.Typer(
    name="download",
    help="Download FHLB data files (AMA, members, dictionaries)",
    no_args_is_help=True,
)
app.add_typer(download_app, name="download")


@download_app.command("data")
def download_data(
    output_dir: Path | None = typer.Option(
        None, "--output", "-o", help="Output directory for downloaded files"
    ),
    overwrite: OverwriteOption = False,
    pause: PauseOption = None,
    timeout: TimeoutOption = None,
    retries: RetriesOption = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Download FHLB AMA data files.

    Downloads AMA (Acquired Member Assets) CSV files from the FHFA
    public use database website.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data fhlb download data
      mortgage-data fhlb download data -o /custom/path
      mortgage-data fhlb download data --overwrite --verbose
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    raw_dir = output_dir or FHLBConfig.FHLB_RAW_AMA_DIR

    console.print("[cyan]Downloading FHLB AMA data files...[/cyan]")
    console.print(f"Output: {raw_dir}\n")

    try:
        results = download_ama_data(
            output_dir=raw_dir,
            overwrite=overwrite,
            pause=pause,
            timeout=timeout,
            retries=retries,
            show_progress=not quiet,
        )
        _report_results(results, quiet)
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error downloading FHLB data: {e}[/red]")
        logger.exception("Download error")
        raise typer.Exit(code=1)


@download_app.command("dictionaries")
def download_dictionaries(
    output_dir: Path | None = typer.Option(
        None, "--output", "-o", help="Output directory"
    ),
    overwrite: OverwriteOption = False,
    pause: PauseOption = None,
    timeout: TimeoutOption = None,
    retries: RetriesOption = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Download FHLB AMA data dictionary files.

    Downloads PDF and Excel dictionary files that describe the
    AMA data schema.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data fhlb download dictionaries
      mortgage-data fhlb download dictionaries -o /custom/path
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    dict_dir = output_dir or (FHLBConfig.FHLB_DATA_DIR / "dictionaries")

    console.print("[cyan]Downloading FHLB dictionaries...[/cyan]")
    console.print(f"Output: {dict_dir}\n")

    try:
        results = download_ama_dictionaries(
            output_dir=dict_dir,
            overwrite=overwrite,
            pause=pause,
            timeout=timeout,
            retries=retries,
            show_progress=not quiet,
        )
        _report_results(results, quiet)
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error downloading FHLB dictionaries: {e}[/red]")
        logger.exception("Download error")
        raise typer.Exit(code=1)


@download_app.command("members")
def download_members(
    output_dir: Path | None = typer.Option(
        None, "--output", "-o", help="Output directory for downloaded files"
    ),
    overwrite: OverwriteOption = False,
    pause: PauseOption = None,
    timeout: TimeoutOption = None,
    retries: RetriesOption = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Download FHLB membership data files.

    Downloads quarterly FHLB member institution Excel files from the FHFA
    FHLB membership data page.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data fhlb download members
      mortgage-data fhlb download members -o /custom/path
      mortgage-data fhlb download members --overwrite
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    raw_dir = output_dir or FHLBConfig.FHLB_RAW_MEMBERS_DIR

    console.print("[cyan]Downloading FHLB membership data files...[/cyan]")
    console.print(f"Output: {raw_dir}\n")

    try:
        results = download_members_data(
            output_dir=raw_dir,
            overwrite=overwrite,
            pause=pause,
            timeout=timeout,
            retries=retries,
            show_progress=not quiet,
        )
        _report_results(results, quiet)
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error downloading FHLB membership data: {e}[/red]")
        logger.exception("Download error")
        raise typer.Exit(code=1)


@download_app.command("all")
def download_all(
    ama_dir: Path | None = typer.Option(
        None, "--ama-dir", help="Directory for AMA data files"
    ),
    members_dir: Path | None = typer.Option(
        None, "--members-dir", help="Directory for members data files"
    ),
    dict_dir: Path | None = typer.Option(
        None, "--dict-dir", help="Directory for dictionary files"
    ),
    overwrite: OverwriteOption = False,
    pause: PauseOption = None,
    timeout: TimeoutOption = None,
    retries: RetriesOption = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Download all FHLB data files.

    Downloads AMA data, membership data, and dictionaries in one step.

    [bold cyan]Example:[/bold cyan]

      mortgage-data fhlb download all
      mortgage-data fhlb download all --overwrite --verbose
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    console.print("[bold cyan]Download All FHLB Files[/bold cyan]")
    console.print("[dim]Downloading AMA data, membership data, and dictionaries...[/dim]\n")

    total_success = 0
    total_skipped = 0
    total_failed = 0

    try:
        # Download AMA data
        console.print("[cyan]1. Downloading AMA data...[/cyan]")
        ama_results = download_ama_data(
            output_dir=ama_dir,
            overwrite=overwrite,
            pause=pause,
            timeout=timeout,
            retries=retries,
            show_progress=not quiet,
        )
        ama_summary = summarize_results(ama_results)
        total_success += ama_summary.get("success", 0)
        total_skipped += ama_summary.get("skipped_exists", 0)
        total_failed += ama_summary.get("failed", 0)

        # Download members data
        console.print("\n[cyan]2. Downloading membership data...[/cyan]")
        members_results = download_members_data(
            output_dir=members_dir,
            overwrite=overwrite,
            pause=pause,
            timeout=timeout,
            retries=retries,
            show_progress=not quiet,
        )
        members_summary = summarize_results(members_results)
        total_success += members_summary.get("success", 0)
        total_skipped += members_summary.get("skipped_exists", 0)
        total_failed += members_summary.get("failed", 0)

        # Download dictionaries
        console.print("\n[cyan]3. Downloading dictionaries...[/cyan]")
        dict_results = download_ama_dictionaries(
            output_dir=dict_dir,
            overwrite=overwrite,
            pause=pause,
            timeout=timeout,
            retries=retries,
            show_progress=not quiet,
        )
        dict_summary = summarize_results(dict_results)
        total_success += dict_summary.get("success", 0)
        total_skipped += dict_summary.get("skipped_exists", 0)
        total_failed += dict_summary.get("failed", 0)

        # Final summary
        console.print("\n[bold]Summary:[/bold]")
        console.print(f"  AMA data files: {ama_summary.get('success', 0)} downloaded")
        console.print(f"  Members data files: {members_summary.get('success', 0)} downloaded")
        console.print(f"  Dictionary files: {dict_summary.get('success', 0)} downloaded")

        if total_failed > 0:
            console.print(
                f"\n[yellow]Download completed with errors: "
                f"{total_success} downloaded, {total_skipped} skipped, {total_failed} failed[/yellow]"
            )
            raise typer.Exit(code=1)
        else:
            console.print(
                f"\n[bold green]All downloads complete: "
                f"{total_success} downloaded, {total_skipped} skipped[/bold green]"
            )

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logger.exception("Download error")
        raise typer.Exit(code=1)


# ============================================================================
# Bronze subapp
# ============================================================================

bronze_app = typer.Typer(
    name="bronze",
    help="Import FHLB data to bronze layer",
    no_args_is_help=True,
)
app.add_typer(bronze_app, name="bronze")


@bronze_app.command("ama")
def bronze_ama(
    min_year: Annotated[
        int | None,
        typer.Option("--min-year", help=f"Minimum year (default: {FHLBConfig.FHLB_DEFAULT_AMA_MIN_YEAR})")
    ] = None,
    max_year: Annotated[
        int | None,
        typer.Option("--max-year", help=f"Maximum year (default: {FHLBConfig.FHLB_DEFAULT_AMA_MAX_YEAR})")
    ] = None,
    raw_dir: Annotated[
        Path | None,
        typer.Option("--raw-dir", help="Raw data directory")
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output directory for bronze parquet files")
    ] = None,
    overwrite: OverwriteOption = False,
):
    """Import FHLB AMA data to bronze layer.

    Reads raw CSV files and converts them to Parquet format with
    standardized column names.

    [yellow]Examples:[/yellow]

      # Import all available years
      $ mortgage-data fhlb bronze ama

      # Import specific year range
      $ mortgage-data fhlb bronze ama --min-year 2020 --max-year 2024

      # Force overwrite existing files
      $ mortgage-data fhlb bronze ama --overwrite
    """
    # Set defaults
    min_yr = min_year or FHLBConfig.FHLB_DEFAULT_AMA_MIN_YEAR
    max_yr = max_year or FHLBConfig.FHLB_DEFAULT_AMA_MAX_YEAR
    years = list(range(min_yr, max_yr + 1))

    raw = raw_dir or FHLBConfig.FHLB_RAW_AMA_DIR
    output = output_dir or (FHLBConfig.FHLB_BRONZE_DIR / "ama")

    console.print(f"[cyan]Importing FHLB AMA data to bronze ({min_yr}-{max_yr})...[/cyan]")
    console.print(f"Raw directory: {raw}")
    console.print(f"Output directory: {output}\n")

    try:
        results = import_ama_bronze(
            years=years,
            raw_dir=raw,
            output_dir=output,
            overwrite=overwrite
        )
        console.print("\n[green]Bronze import complete[/green]")
        console.print(f"  Loaded: {results['loaded']} year(s)")
        console.print(f"  Skipped: {results['skipped']} year(s)")
    except Exception as e:
        console.print(f"[red]Error importing to bronze: {e}[/red]")
        logger.exception("Bronze import error")
        raise typer.Exit(code=1)


@bronze_app.command("members")
def bronze_members(
    min_year: Annotated[
        int | None,
        typer.Option("--min-year", help=f"Minimum year (default: {FHLBConfig.FHLB_DEFAULT_MIN_YEAR})")
    ] = None,
    max_year: Annotated[
        int | None,
        typer.Option("--max-year", help=f"Maximum year (default: {FHLBConfig.FHLB_DEFAULT_MAX_YEAR})")
    ] = None,
    raw_dir: Annotated[
        Path | None,
        typer.Option("--raw-dir", help="Raw data directory with Excel files")
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output directory for bronze parquet files")
    ] = None,
    overwrite: OverwriteOption = False,
):
    """Import FHLB member data to bronze layer.

    Reads raw Excel files containing FHLB member institution data and
    converts them to individual Parquet files per quarter.

    Looks for files matching pattern: FHLB_Members*{year}*.xls*

    [yellow]Examples:[/yellow]

      # Import all available years
      $ mortgage-data fhlb bronze members

      # Import specific year range
      $ mortgage-data fhlb bronze members --min-year 2015 --max-year 2023

      # Force overwrite existing files
      $ mortgage-data fhlb bronze members --overwrite
    """
    # Set defaults
    min_yr = min_year or FHLBConfig.FHLB_DEFAULT_MIN_YEAR
    max_yr = max_year or FHLBConfig.FHLB_DEFAULT_MAX_YEAR
    years = list(range(min_yr, max_yr + 1))

    raw = raw_dir or FHLBConfig.FHLB_RAW_MEMBERS_DIR
    output = output_dir or (FHLBConfig.FHLB_BRONZE_DIR / "members")

    console.print(f"[cyan]Importing FHLB member data to bronze ({min_yr}-{max_yr})...[/cyan]")
    console.print(f"Raw directory: {raw}")
    console.print(f"Output directory: {output}\n")

    try:
        results = import_members_bronze(
            years=years,
            raw_dir=raw,
            output_dir=output,
            overwrite=overwrite
        )
        console.print("\n[green]Bronze import complete[/green]")
        console.print(f"  Loaded: {results['loaded']} file(s)")
        console.print(f"  Skipped: {results['skipped']} file(s)")
    except Exception as e:
        console.print(f"[red]Error importing to bronze: {e}[/red]")
        logger.exception("Bronze import error")
        raise typer.Exit(code=1)


# ============================================================================
# Silver subapp
# ============================================================================

silver_app = typer.Typer(
    name="silver",
    help="Transform FHLB data to silver layer",
    no_args_is_help=True,
)
app.add_typer(silver_app, name="silver")


@silver_app.command("ama")
def silver_ama(
    min_year: Annotated[
        int | None,
        typer.Option("--min-year", help=f"Minimum year (default: {FHLBConfig.FHLB_DEFAULT_AMA_MIN_YEAR})")
    ] = None,
    max_year: Annotated[
        int | None,
        typer.Option("--max-year", help=f"Maximum year (default: {FHLBConfig.FHLB_DEFAULT_AMA_MAX_YEAR})")
    ] = None,
    bronze_dir: Annotated[
        Path | None,
        typer.Option("--bronze-dir", help="Bronze data directory")
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output directory for silver parquet files")
    ] = None,
    overwrite: OverwriteOption = False,
):
    """Transform FHLB AMA data to silver layer.

    Applies data quality transformations including:
    - Normalizing percentage values across schema versions
    - Fixing income amount units
    - Standardizing indicator variable encoding
    - Cleaning census tract identifiers
    - Replacing sentinel missing values with nulls

    [yellow]Examples:[/yellow]

      # Transform all available years
      $ mortgage-data fhlb silver ama

      # Transform specific year range
      $ mortgage-data fhlb silver ama --min-year 2020 --max-year 2024

      # Force overwrite existing files
      $ mortgage-data fhlb silver ama --overwrite
    """
    # Set defaults
    min_yr = min_year or FHLBConfig.FHLB_DEFAULT_AMA_MIN_YEAR
    max_yr = max_year or FHLBConfig.FHLB_DEFAULT_AMA_MAX_YEAR
    years = list(range(min_yr, max_yr + 1))

    bronze = bronze_dir or (FHLBConfig.FHLB_BRONZE_DIR / "ama")
    output = output_dir or (FHLBConfig.FHLB_SILVER_DIR / "ama")

    console.print(f"[cyan]Transforming FHLB AMA data to silver ({min_yr}-{max_yr})...[/cyan]")
    console.print(f"Bronze directory: {bronze}")
    console.print(f"Output directory: {output}\n")

    try:
        results = import_ama_silver(
            years=years,
            bronze_dir=bronze,
            output_dir=output,
            overwrite=overwrite
        )
        console.print("\n[green]Silver transformation complete[/green]")
        console.print(f"  Transformed: {results['loaded']} year(s)")
        console.print(f"  Skipped: {results['skipped']} year(s)")
    except Exception as e:
        console.print(f"[red]Error transforming to silver: {e}[/red]")
        logger.exception("Silver transformation error")
        raise typer.Exit(code=1)


# ============================================================================
# Pipeline subapp
# ============================================================================

pipeline_app = typer.Typer(
    name="pipeline",
    help="Run complete FHLB data pipelines",
    no_args_is_help=True,
)
app.add_typer(pipeline_app, name="pipeline")


@pipeline_app.command("ama")
def pipeline_ama(
    min_year: Annotated[
        int | None,
        typer.Option("--min-year", help=f"Minimum year (default: {FHLBConfig.FHLB_DEFAULT_AMA_MIN_YEAR})")
    ] = None,
    max_year: Annotated[
        int | None,
        typer.Option("--max-year", help=f"Maximum year (default: {FHLBConfig.FHLB_DEFAULT_AMA_MAX_YEAR})")
    ] = None,
    skip_download: Annotated[
        bool,
        typer.Option("--skip-download", help="Skip download step (use existing raw files)")
    ] = False,
    skip_bronze: Annotated[
        bool,
        typer.Option("--skip-bronze", help="Skip bronze loading step")
    ] = False,
    skip_silver: Annotated[
        bool,
        typer.Option("--skip-silver", help="Skip silver transformation step")
    ] = False,
    overwrite: OverwriteOption = False,
    pause: PauseOption = None,
):
    """Run complete FHLB AMA data pipeline.

    Executes all processing steps:
    1. Download AMA CSV files from FHFA website
    2. Import to bronze layer (CSV -> Parquet with standardized columns)
    3. Transform to silver layer (data quality transformations)

    [yellow]Examples:[/yellow]

      # Run complete pipeline for all years
      $ mortgage-data fhlb pipeline ama

      # Run for specific year range
      $ mortgage-data fhlb pipeline ama --min-year 2020 --max-year 2024

      # Skip download (use existing raw files)
      $ mortgage-data fhlb pipeline ama --skip-download

      # Only run bronze step
      $ mortgage-data fhlb pipeline ama --skip-download --skip-silver

      # Force overwrite existing files
      $ mortgage-data fhlb pipeline ama --overwrite
    """
    # Set defaults
    min_yr = min_year or FHLBConfig.FHLB_DEFAULT_AMA_MIN_YEAR
    max_yr = max_year or FHLBConfig.FHLB_DEFAULT_AMA_MAX_YEAR
    years = list(range(min_yr, max_yr + 1))

    console.print("[bold cyan]FHLB AMA Pipeline[/bold cyan]")
    console.print(f"Processing years: {min_yr}-{max_yr}")
    console.print(f"Steps: {'download' if not skip_download else '[dim]skip download[/dim]'} -> "
                  f"{'bronze' if not skip_bronze else '[dim]skip bronze[/dim]'} -> "
                  f"{'silver' if not skip_silver else '[dim]skip silver[/dim]'}\n")

    try:
        results = run_pipeline(
            years=years,
            skip_download=skip_download,
            skip_bronze=skip_bronze,
            skip_silver=skip_silver,
            overwrite=overwrite,
            pause=pause
        )

        # Display results table
        table = Table(title="Pipeline Results", show_header=True, header_style="bold cyan")
        table.add_column("Step", style="cyan")
        table.add_column("Processed", style="green")
        table.add_column("Skipped", style="yellow")

        if not skip_download:
            table.add_row("Download", str(results['files_downloaded']), "-")
        if not skip_bronze:
            table.add_row("Bronze", str(results['bronze_loaded']), str(results['bronze_skipped']))
        if not skip_silver:
            table.add_row("Silver", str(results['silver_transformed']), str(results['silver_skipped']))

        console.print(table)
        console.print("\n[bold green]Pipeline complete![/bold green]")

    except Exception as e:
        console.print(f"[red]Pipeline failed: {e}[/red]")
        logger.exception("Pipeline error")
        raise typer.Exit(code=1)


# ============================================================================
# Top-level commands
# ============================================================================

@app.command()
def info():
    """Display FHLB configuration information.

    Shows the current configuration paths and settings for FHLB data.

    Examples:
      mortgage-data fhlb info
      fhlb info
    """
    table = Table(title="FHLB Configuration", show_header=True, header_style="bold cyan")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("Data Directory", str(FHLBConfig.FHLB_DATA_DIR))
    table.add_row("Raw Directory", str(FHLBConfig.FHLB_RAW_DIR))
    table.add_row("  Members Directory", str(FHLBConfig.FHLB_RAW_MEMBERS_DIR))
    table.add_row("  AMA Directory", str(FHLBConfig.FHLB_RAW_AMA_DIR))
    table.add_row("Bronze Directory", str(FHLBConfig.FHLB_BRONZE_DIR))
    table.add_row("Silver Directory", str(FHLBConfig.FHLB_SILVER_DIR))
    table.add_row("Gold Directory", str(FHLBConfig.FHLB_GOLD_DIR))
    table.add_row("", "")
    table.add_row("[bold]Member Data[/bold]", "")
    table.add_row("  Default Min Year", str(FHLBConfig.FHLB_DEFAULT_MIN_YEAR))
    table.add_row("  Default Max Year", str(FHLBConfig.FHLB_DEFAULT_MAX_YEAR))
    table.add_row("[bold]AMA Data[/bold]", "")
    table.add_row("  Default Min Year", str(FHLBConfig.FHLB_DEFAULT_AMA_MIN_YEAR))
    table.add_row("  Default Max Year", str(FHLBConfig.FHLB_DEFAULT_AMA_MAX_YEAR))

    console.print(table)


def cli_main():
    """Entry point for standalone FHLB CLI."""
    app()


if __name__ == "__main__":
    cli_main()
