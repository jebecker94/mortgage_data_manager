"""FHA CLI - Main entry point.

Typer-based command-line interface for FHA data management.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import typer

from mortgage_data_manager import __version__
from mortgage_data_manager.core.cli.download_options import (
    OverwriteOption,
    PauseOption,
    QuietOption,
    RetriesOption,
    TimeoutOption,
    VerboseOption,
)
from mortgage_data_manager.core.cli.formatting import console, print_info_table
from mortgage_data_manager.core.cli.validation import version_callback_factory
from mortgage_data_manager.core.download import summarize_results
from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.fha.config import FHAConfig
from mortgage_data_manager.fha.download import (
    download_hecm_snapshots,
    download_single_family_snapshots,
)
from mortgage_data_manager.fha.pipeline import (
    DEFAULT_MAX_YEAR,
    DEFAULT_MIN_YEAR,
    bronze_hecm,
    bronze_single_family,
    run_pipeline,
    silver_hecm,
    silver_single_family,
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
# Download Command
# ============================================================================


@app.command()
def download(
    dataset: Literal["single-family", "hecm", "both"] = typer.Argument(
        ...,
        help="Dataset to download: single-family, hecm, or both",
    ),
    destination: Path | None = typer.Option(
        None,
        "--destination",
        "-o",
        help="Destination folder for downloads (default: data/raw/{dataset})",
    ),
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

        all_results = []

        for ds in datasets:
            console.print(f"\n[cyan]Downloading {ds.upper()} snapshots...[/cyan]")

            kwargs = {
                "include_zip": include_zip,
                "overwrite": overwrite,
                "pause": pause,
                "timeout": timeout,
                "retries": retries,
                "show_progress": not quiet,
            }
            if destination:
                kwargs["destination"] = destination

            if ds == "single-family":
                results = download_single_family_snapshots(**kwargs)
            else:  # hecm
                results = download_hecm_snapshots(**kwargs)

            all_results.extend(results)

        _report_results(all_results, quiet)

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Download failed: {e}[/red]")
        logger.exception("Download error")
        raise typer.Exit(code=1)


# ============================================================================
# Import Command
# ============================================================================


def _run_step_for_datasets(
    datasets: list[str],
    label: str,
    runner_sf,
    runner_hecm,
) -> None:
    """Helper for bronze/silver: run a step across one or both FHA datasets."""
    results: dict[str, bool] = {}
    for ds in datasets:
        console.print(f"\n[cyan]Running {label} for {ds.upper()}[/cyan]")
        try:
            if ds == "single-family":
                runner_sf()
            else:
                runner_hecm()
            results[ds] = True
        except Exception:
            logger.exception(f"{label} failed for {ds}")
            results[ds] = False

    console.print(f"\n[bold]{label.capitalize()} Results:[/bold]")
    for ds, success in results.items():
        status = "[green]✓[/green]" if success else "[red]✗[/red]"
        console.print(f"  {status} {ds}")

    if not all(results.values()):
        console.print(f"\n[yellow]⚠ Some {label} runs failed[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"\n[green]✓ {label.capitalize()} completed successfully![/green]")


@app.command()
def bronze(
    dataset: Literal["single-family", "hecm", "both"] = typer.Argument(
        ...,
        help="Dataset to convert: single-family, hecm, or both",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Overwrite existing bronze files",
    ),
) -> None:
    """Convert FHA raw snapshots to the bronze layer (Parquet).

    [bold cyan]Examples:[/bold cyan]

      mortgage-data fha bronze single-family
      mortgage-data fha bronze hecm --overwrite
      mortgage-data fha bronze both
    """
    configure_logging(level="INFO")

    datasets = ["single-family", "hecm"] if dataset == "both" else [dataset]
    _run_step_for_datasets(
        datasets,
        label="bronze",
        runner_sf=lambda: bronze_single_family(overwrite=overwrite),
        runner_hecm=lambda: bronze_hecm(overwrite=overwrite),
    )


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
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Overwrite existing silver files",
    ),
    no_fips: bool = typer.Option(
        False,
        "--no-fips",
        help="Skip FIPS code enrichment",
    ),
    no_date: bool = typer.Option(
        False,
        "--no-date",
        help="Skip date column generation",
    ),
) -> None:
    """Clean FHA bronze snapshots into the hive-partitioned silver layer.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data fha silver single-family
      mortgage-data fha silver hecm --min-year 2015 --max-year 2023
      mortgage-data fha silver both --overwrite
    """
    configure_logging(level="INFO")

    add_fips = not no_fips
    add_date = not no_date
    datasets = ["single-family", "hecm"] if dataset == "both" else [dataset]

    _run_step_for_datasets(
        datasets,
        label="silver",
        runner_sf=lambda: silver_single_family(
            overwrite=overwrite,
            min_year=min_year,
            max_year=max_year,
            add_fips=add_fips,
            add_date=add_date,
        ),
        runner_hecm=lambda: silver_hecm(
            overwrite=overwrite,
            min_year=min_year,
            max_year=max_year,
            add_fips=add_fips,
            add_date=add_date,
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
    no_fips: bool = typer.Option(
        False,
        "--no-fips",
        help="Skip FIPS code enrichment",
    ),
    no_date: bool = typer.Option(
        False,
        "--no-date",
        help="Skip date column generation",
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

        run_pipeline(
            dataset=dataset,
            skip_download=skip_download,
            skip_import=skip_import,
            pause=pause,
            include_zip=not no_zip,
            overwrite=overwrite,
            min_year=min_year,
            max_year=max_year,
            add_fips=not no_fips,
            add_date=not no_date,
        )

        console.print("\n[green]✓ Pipeline completed successfully![/green]")

    except Exception as e:
        console.print(f"[red]Pipeline failed: {e}[/red]")
        logger.exception("Pipeline error")
        raise typer.Exit(code=1)


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
    from mortgage_data_manager.fha.lender_list import LENDER_LIST_STATES, download_lender_list

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
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Rebuild even if the bronze file exists",
    ),
) -> None:
    """Parse raw lender list HTML into a bronze parquet file.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data fha lender-list bronze
      mortgage-data fha lender-list bronze --snapshot-date 20260603 --overwrite
    """
    from mortgage_data_manager.fha.lender_list import build_bronze_lender_list

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
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Re-download and rebuild even if files exist",
    ),
    pause: PauseOption = None,
) -> None:
    """Download today's lender list and build the bronze parquet.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data fha lender-list pipeline
    """
    from mortgage_data_manager.fha.lender_list import (
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
