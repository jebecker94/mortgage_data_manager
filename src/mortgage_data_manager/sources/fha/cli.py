"""FHA CLI - Main entry point.

Typer-based command-line interface for FHA data management.
"""

from __future__ import annotations

from typing import Annotated, Literal

import typer

from mortgage_data_manager import __version__
from mortgage_data_manager.core.cli.download_options import (
    OutputDirOption,
    OverwriteOption,
    PauseOption,
    QuietOption,
    RetriesOption,
    TimeoutOption,
    VerboseOption,
    report_download_results,
    resolve_output_dir,
)
from mortgage_data_manager.core.cli.formatting import console, print_info_table
from mortgage_data_manager.core.cli.validation import version_callback_factory
from mortgage_data_manager.core.download import DownloadResult
from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.sources.fha.config import FHAConfig
from mortgage_data_manager.sources.fha.download import (
    DEFAULT_OUTPUT_DIRS,
    DOWNLOADERS,
)
from mortgage_data_manager.sources.fha.pipeline import (
    DEFAULT_MAX_YEAR,
    DEFAULT_MIN_YEAR,
    run_bronze,
    run_pipeline,
    run_silver,
)

# Create Typer app
app = typer.Typer(
    name="fha",
    help="FHA data management commands",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

logger = get_logger(__name__)

_version_callback = version_callback_factory("FHA Data Manager", __version__)


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
) -> None:
    """[bold cyan]FHA Data Manager[/bold cyan].

    Tools for downloading and processing HUD's Federal Housing Administration (FHA) snapshot data.
    """
    pass  # Logging configured per-command


# ============================================================================
# Download Command
# ============================================================================


@app.command()
def download(
    dataset: Literal["single-family", "hecm", "both"] = typer.Argument(
        ...,
        help="Dataset to download: single-family, hecm, or both",
    ),
    output_dir: OutputDirOption = None,
    no_zip: bool = typer.Option(
        False,
        "--no-zip",
        help="Skip downloading .zip archives",
    ),
    overwrite: OverwriteOption = False,
    pause: PauseOption = None,
    timeout: TimeoutOption = None,
    retries: RetriesOption = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """Download FHA snapshot data files from HUD.gov.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data fha download single-family
      mortgage-data fha download hecm --pause 10
      mortgage-data fha download both --overwrite --verbose
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    try:
        include_zip = not no_zip
        datasets = ["single-family", "hecm"] if dataset == "both" else [dataset]

        all_results: list[DownloadResult] = []

        for ds in datasets:
            console.print(f"\n[cyan]Downloading {ds.upper()} snapshots...[/cyan]")

            all_results.extend(
                DOWNLOADERS[ds](
                    resolve_output_dir(output_dir, DEFAULT_OUTPUT_DIRS[ds]),
                    include_zip=include_zip,
                    overwrite=overwrite,
                    pause=pause,
                    timeout=timeout,
                    retries=retries,
                    show_progress=not quiet,
                )
            )

        report_download_results(all_results)

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Download failed: {e}[/red]")
        logger.exception("Download error")
        raise typer.Exit(code=1)


# ============================================================================
# Import Command
# ============================================================================


def _report_counts(label: str, counts: dict[str, int]) -> None:
    """Render a stage's dataset counts and exit non-zero unless every one succeeded.

    The library reports failures rather than raising, so this is the only place
    that turns a partial failure into a non-zero exit code.
    """
    console.print(
        f"\n[bold]{label.capitalize()}:[/bold] {counts['successful']} succeeded, "
        f"{counts['failed']} failed of {counts['processed']} dataset(s)"
    )
    if counts["failed"] or not counts["successful"]:
        console.print(f"[yellow]⚠ {label.capitalize()} did not complete cleanly[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"[green]✓ {label.capitalize()} completed successfully![/green]")


@app.command()
def bronze(
    dataset: Literal["single-family", "hecm", "both"] = typer.Argument(
        ...,
        help="Dataset to convert: single-family, hecm, or both",
    ),
    overwrite: OverwriteOption = False,
) -> None:
    """Convert FHA raw snapshots to the bronze layer (Parquet).

    [bold cyan]Examples:[/bold cyan]

      mortgage-data fha bronze single-family
      mortgage-data fha bronze hecm --overwrite
      mortgage-data fha bronze both
    """
    configure_logging(level="INFO")

    datasets: list[str] | None = None if dataset == "both" else [dataset]
    _report_counts("bronze", run_bronze(datasets, overwrite=overwrite))


@app.command()
def silver(
    dataset: Literal["single-family", "hecm", "both"] = typer.Argument(
        ...,
        help="Dataset to clean: single-family, hecm, or both",
    ),
    min_year: int = typer.Option(
        DEFAULT_MIN_YEAR,
        "--min-year",
        help="Minimum year to process",
    ),
    max_year: int = typer.Option(
        DEFAULT_MAX_YEAR,
        "--max-year",
        help="Maximum year to process",
    ),
    overwrite: OverwriteOption = False,
) -> None:
    """Clean FHA bronze snapshots into the hive-partitioned silver layer.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data fha silver single-family
      mortgage-data fha silver hecm --min-year 2015 --max-year 2023
      mortgage-data fha silver both --overwrite
    """
    configure_logging(level="INFO")

    datasets: list[str] | None = None if dataset == "both" else [dataset]

    _report_counts(
        "silver",
        run_silver(
            datasets,
            overwrite=overwrite,
            min_year=min_year,
            max_year=max_year,
        ),
    )


# ============================================================================
# Pipeline Command
# ============================================================================


@app.command()
def pipeline(
    dataset: Literal["single-family", "hecm", "both"] = typer.Argument(
        ...,
        help="Dataset to process: single-family, hecm, or both",
    ),
    skip_download: bool = typer.Option(
        False,
        "--skip-download",
        help="Skip download step (use existing files)",
    ),
    skip_import: bool = typer.Option(
        False,
        "--skip-import",
        help="Skip import step (download only)",
    ),
    no_zip: bool = typer.Option(
        False,
        "--no-zip",
        help="Skip downloading .zip archives",
    ),
    overwrite: OverwriteOption = False,
    pause: PauseOption = None,
    min_year: int = typer.Option(
        DEFAULT_MIN_YEAR,
        "--min-year",
        help="Minimum year to process",
    ),
    max_year: int = typer.Option(
        DEFAULT_MAX_YEAR,
        "--max-year",
        help="Maximum year to process",
    ),
) -> None:
    """Run complete download + import pipeline for FHA data.

    This command combines download and import steps into a single workflow.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data fha pipeline single-family
      mortgage-data fha pipeline both --overwrite --min-year 2020
      mortgage-data fha pipeline hecm --skip-download
    """
    configure_logging(level="INFO")

    try:
        console.print(f"[cyan]Running FHA pipeline for {dataset.upper()}[/cyan]")

        counts = run_pipeline(
            dataset=dataset,
            skip_download=skip_download,
            skip_import=skip_import,
            pause=pause,
            include_zip=not no_zip,
            overwrite=overwrite,
            min_year=min_year,
            max_year=max_year,
        )

    except Exception as e:
        console.print(f"[red]Pipeline failed: {e}[/red]")
        logger.exception("Pipeline error")
        raise typer.Exit(code=1) from e

    _report_counts("pipeline", counts)


# ============================================================================
# Lender List Commands
# ============================================================================

lender_list_app = typer.Typer(
    name="lender-list",
    help="HUD FHA-approved lender list (roster of mortgagee IDs)",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(lender_list_app)


@lender_list_app.command("download")
def lender_list_download(
    states: str | None = typer.Option(
        None,
        "--states",
        help="Comma-separated state codes (default: all states and territories)",
    ),
    snapshot_date: str | None = typer.Option(
        None,
        "--snapshot-date",
        help="Snapshot label in YYYYMMDD form (default: today)",
    ),
    overwrite: OverwriteOption = False,
    pause: PauseOption = None,
    timeout: TimeoutOption = None,
    retries: RetriesOption = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """Download raw lender list HTML from HUD's Lender List Search.

    One request per state; results are saved under a dated snapshot
    directory in raw/lender_list/.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data fha lender-list download
      mortgage-data fha lender-list download --states CA,TX --overwrite
    """
    from mortgage_data_manager.sources.fha.lender_list import (
        LENDER_LIST_STATES,
        download_lender_list,
    )

    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    state_list = [s.strip() for s in states.split(",")] if states else list(LENDER_LIST_STATES)
    try:
        saved = download_lender_list(
            states=state_list,
            snapshot_date=snapshot_date,
            pause=pause,
            timeout=timeout,
            retries=retries,
            overwrite=overwrite,
        )
    except Exception as e:
        console.print(f"[red]Lender list download failed: {e}[/red]")
        logger.exception("Lender list download error")
        raise typer.Exit(code=1)

    failed = len(state_list) - len(saved)
    if failed:
        console.print(f"[yellow]Saved {len(saved)} state files; {failed} states failed[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"[green]Saved {len(saved)} state files[/green]")


@lender_list_app.command("bronze")
def lender_list_bronze(
    snapshot_date: str | None = typer.Option(
        None,
        "--snapshot-date",
        help="Snapshot label (YYYYMMDD) to build (default: latest)",
    ),
    overwrite: OverwriteOption = False,
) -> None:
    """Parse raw lender list HTML into a bronze parquet file.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data fha lender-list bronze
      mortgage-data fha lender-list bronze --snapshot-date 20260603 --overwrite
    """
    from mortgage_data_manager.sources.fha.lender_list import build_bronze_lender_list

    configure_logging(level="INFO")
    try:
        out_path = build_bronze_lender_list(snapshot_date=snapshot_date, overwrite=overwrite)
    except Exception as e:
        console.print(f"[red]Bronze build failed: {e}[/red]")
        logger.exception("Lender list bronze error")
        raise typer.Exit(code=1)
    console.print(f"[green]Bronze lender list written to {out_path}[/green]")


@lender_list_app.command("pipeline")
def lender_list_pipeline(
    overwrite: OverwriteOption = False,
    pause: PauseOption = None,
) -> None:
    """Download today's lender list and build the bronze parquet.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data fha lender-list pipeline
    """
    from mortgage_data_manager.sources.fha.lender_list import (
        build_bronze_lender_list,
        download_lender_list,
    )

    configure_logging(level="INFO")
    try:
        download_lender_list(pause=pause, overwrite=overwrite)
        out_path = build_bronze_lender_list(overwrite=overwrite)
    except Exception as e:
        console.print(f"[red]Lender list pipeline failed: {e}[/red]")
        logger.exception("Lender list pipeline error")
        raise typer.Exit(code=1)
    console.print(f"[green]Lender list pipeline complete: {out_path}[/green]")


# ============================================================================
# Info Command
# ============================================================================


@app.command()
def info() -> None:
    """Display FHA configuration information.

    Shows the current configuration paths and settings for FHA data.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data fha info
    """
    print_info_table(
        "FHA Configuration",
        {
            "Data Directory": FHAConfig.FHA_DATA_DIR,
            "Raw Directory": FHAConfig.FHA_RAW_DIR,
            "Bronze Directory": FHAConfig.FHA_BRONZE_DIR,
            "Silver Directory": FHAConfig.FHA_SILVER_DIR,
        },
    )

    # Show available commands
    console.print("\n[bold cyan]Available Commands:[/bold cyan]")
    console.print("  download    - Download FHA snapshot data from HUD.gov")
    console.print("  bronze      - Import raw snapshots to bronze parquet")
    console.print("  silver      - Build cleaned silver layer from bronze")
    console.print("  pipeline    - Run complete download + import pipeline")
    console.print("  lender-list - Scrape the HUD FHA-approved lender list")
    console.print("  info        - Show this configuration information")


def cli_main() -> None:
    """Entry point for standalone FHA CLI."""
    app()


if __name__ == "__main__":
    cli_main()
