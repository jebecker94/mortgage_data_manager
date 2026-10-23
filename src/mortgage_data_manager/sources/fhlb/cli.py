"""FHLB CLI - Main entry point.

Typer-based command-line interface for FHLB data management. Flat canonical
verbs (``download``/``bronze``/``silver``/``pipeline``/``info``); the dataset
axis is the shared ``--datasets/-t`` option, driven by the registries in
`.config`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

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
    report_download_results,
)
from mortgage_data_manager.core.cli.formatting import console
from mortgage_data_manager.core.cli.validation import version_callback_factory
from mortgage_data_manager.core.download import summarize_results
from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.sources.fhlb.config import (
    BRONZE_DATASETS,
    DOWNLOAD_DATASETS,
    SILVER_DATASETS,
    FHLBConfig,
)
from mortgage_data_manager.sources.fhlb.pipeline import (
    run_bronze,
    run_download,
    run_pipeline,
    run_silver,
)

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


def _log_level(verbose: bool, quiet: bool) -> Literal["DEBUG", "INFO", "WARNING"]:
    return "DEBUG" if verbose else ("WARNING" if quiet else "INFO")


MinYearOption = Annotated[
    int | None, typer.Option("--min-year", help="Earliest year (default: per-dataset)")
]
MaxYearOption = Annotated[
    int | None, typer.Option("--max-year", help="Latest year (default: per-dataset)")
]


@app.command()
def download(
    datasets: Annotated[
        list[str] | None,
        typer.Option(
            "--datasets",
            "-t",
            help=f"Datasets to download: {', '.join(DOWNLOAD_DATASETS)} (repeatable; default all)",
        ),
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", "-o", help="Output directory (single dataset only)"),
    ] = None,
    overwrite: OverwriteOption = False,
    pause: PauseOption = None,
    timeout: TimeoutOption = None,
    retries: RetriesOption = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Download raw FHLB artifacts.

    Datasets (default all):
      - [cyan]ama[/cyan]: Acquired Member Assets loan-level CSVs
      - [cyan]members[/cyan]: quarterly member-institution Excel files
      - [cyan]dictionaries[/cyan]: AMA data-dictionary PDFs/Excel

    Each dataset lands in its own configured directory; [cyan]--output-dir[/cyan]
    overrides that and so requires exactly one dataset.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data fhlb download
      mortgage-data fhlb download -t ama -o /custom/path
      mortgage-data fhlb download -t members -t dictionaries --overwrite
    """
    configure_logging(level=_log_level(verbose, quiet))
    try:
        results = run_download(
            datasets,
            output_dir=output_dir,
            overwrite=overwrite,
            pause=pause,
            timeout=timeout,
            retries=retries,
            show_progress=not quiet,
        )
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]Error downloading FHLB data: {e}[/red]")
        logger.exception("Download error")
        raise typer.Exit(code=1)

    for dataset, dataset_results in results.items():
        summary = summarize_results(dataset_results)
        console.print(f"  {dataset}: {summary.get('success', 0)} downloaded")
    report_download_results([r for rs in results.values() for r in rs])


@app.command()
def bronze(
    datasets: Annotated[
        list[str] | None,
        typer.Option(
            "--datasets",
            "-t",
            help=f"Datasets to import: {', '.join(BRONZE_DATASETS)} (repeatable; default all)",
        ),
    ] = None,
    min_year: MinYearOption = None,
    max_year: MaxYearOption = None,
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Import raw FHLB artifacts to the bronze layer.

    Datasets (default all):
      - [cyan]ama[/cyan]: AMA CSVs -> parquet with standardized column names
      - [cyan]members[/cyan]: member Excel files -> one parquet per quarter

    AMA and members have different published year ranges, so omitting
    [cyan]--min-year[/cyan]/[cyan]--max-year[/cyan] resolves each independently.

    [yellow]Examples:[/yellow]

      $ mortgage-data fhlb bronze
      $ mortgage-data fhlb bronze -t ama --min-year 2020 --max-year 2024
      $ mortgage-data fhlb bronze --overwrite
    """
    configure_logging(level=_log_level(verbose, quiet))
    try:
        results = run_bronze(datasets, min_year=min_year, max_year=max_year, overwrite=overwrite)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]Error importing to bronze: {e}[/red]")
        logger.exception("Bronze import error")
        raise typer.Exit(code=1)

    console.print("\n[green]Bronze import complete[/green]")
    for dataset, counts in results.items():
        console.print(f"  {dataset}: loaded {counts['loaded']}, skipped {counts['skipped']}")


@app.command()
def silver(
    datasets: Annotated[
        list[str] | None,
        typer.Option(
            "--datasets",
            "-t",
            help=f"Datasets to transform: {', '.join(SILVER_DATASETS)} (repeatable; default all)",
        ),
    ] = None,
    min_year: MinYearOption = None,
    max_year: MaxYearOption = None,
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Transform FHLB bronze data to the silver layer.

    Applies data quality transformations including:
    - Normalizing percentage values across schema versions
    - Fixing income amount units
    - Standardizing indicator variable encoding
    - Cleaning census tract identifiers
    - Replacing sentinel missing values with nulls

    [yellow]Examples:[/yellow]

      $ mortgage-data fhlb silver
      $ mortgage-data fhlb silver --min-year 2020 --max-year 2024
      $ mortgage-data fhlb silver --overwrite
    """
    configure_logging(level=_log_level(verbose, quiet))
    try:
        results = run_silver(datasets, min_year=min_year, max_year=max_year, overwrite=overwrite)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]Error transforming to silver: {e}[/red]")
        logger.exception("Silver transformation error")
        raise typer.Exit(code=1)

    console.print("\n[green]Silver transformation complete[/green]")
    for dataset, counts in results.items():
        console.print(f"  {dataset}: transformed {counts['loaded']}, skipped {counts['skipped']}")


@app.command()
def pipeline(
    min_year: MinYearOption = None,
    max_year: MaxYearOption = None,
    skip_download: Annotated[
        bool, typer.Option("--skip-download", help="Skip download step (use existing raw files)")
    ] = False,
    skip_bronze: Annotated[
        bool, typer.Option("--skip-bronze", help="Skip bronze loading step")
    ] = False,
    skip_silver: Annotated[
        bool, typer.Option("--skip-silver", help="Skip silver transformation step")
    ] = False,
    overwrite: OverwriteOption = False,
    pause: PauseOption = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Run the complete FHLB AMA pipeline (download -> bronze -> silver).

    AMA is FHLB's end-to-end flow; members data has no silver stage, so build it
    with [cyan]fhlb bronze -t members[/cyan].

    [yellow]Examples:[/yellow]

      $ mortgage-data fhlb pipeline
      $ mortgage-data fhlb pipeline --min-year 2020 --max-year 2024
      $ mortgage-data fhlb pipeline --skip-download
      $ mortgage-data fhlb pipeline --skip-download --skip-silver
    """
    configure_logging(level=_log_level(verbose, quiet))
    default_min, default_max = FHLBConfig.default_years("ama")
    min_yr, max_yr = min_year or default_min, max_year or default_max
    years = list(range(min_yr, max_yr + 1))

    console.print("[bold cyan]FHLB AMA Pipeline[/bold cyan]")
    console.print(f"Processing years: {min_yr}-{max_yr}")
    console.print(
        f"Steps: {'download' if not skip_download else '[dim]skip download[/dim]'} -> "
        f"{'bronze' if not skip_bronze else '[dim]skip bronze[/dim]'} -> "
        f"{'silver' if not skip_silver else '[dim]skip silver[/dim]'}\n"
    )

    try:
        results = run_pipeline(
            years=years,
            skip_download=skip_download,
            skip_bronze=skip_bronze,
            skip_silver=skip_silver,
            overwrite=overwrite,
            pause=pause,
        )
    except Exception as e:
        console.print(f"[red]Pipeline failed: {e}[/red]")
        logger.exception("Pipeline error")
        raise typer.Exit(code=1)

    table = Table(title="Pipeline Results", show_header=True, header_style="bold cyan")
    table.add_column("Step", style="cyan")
    table.add_column("Processed", style="green")
    table.add_column("Skipped", style="yellow")
    if not skip_download:
        table.add_row("Download", str(results["files_downloaded"]), "-")
    if not skip_bronze:
        table.add_row("Bronze", str(results["bronze_loaded"]), str(results["bronze_skipped"]))
    if not skip_silver:
        table.add_row("Silver", str(results["silver_transformed"]), str(results["silver_skipped"]))
    console.print(table)
    console.print("\n[bold green]Pipeline complete![/bold green]")


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
