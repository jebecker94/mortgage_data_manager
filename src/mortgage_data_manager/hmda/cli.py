"""HMDA CLI - Main entry point.

Typer-based command-line interface for HMDA data management.
"""

from __future__ import annotations

from typing import Annotated

import typer

from mortgage_data_manager import __version__
from mortgage_data_manager.core.cli.download_options import PauseOption
from mortgage_data_manager.core.cli.formatting import console, print_info_table
from mortgage_data_manager.core.cli.validation import version_callback_factory
from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.hmda.config import HMDAConfig
from mortgage_data_manager.hmda.pipeline import (
    bronze_2007_2017,
    bronze_post2018,
    bronze_pre2007,
    import_2007_2017,
    import_post2018,
    import_pre2007,
    run_download,
    silver_2007_2017,
    silver_post2018,
    silver_pre2007,
)

_VALID_PERIODS = ("post2018", "2007-2017", "pre2007")
_PERIOD_DEFAULTS: dict[str, tuple[int, int]] = {
    "post2018": (2018, 2025),
    "2007-2017": (2007, 2017),
    "pre2007": (1990, 2006),
}

# Create Typer app
app = typer.Typer(
    name="hmda",
    help="HMDA data management commands",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

logger = get_logger(__name__)

# Version callback
_version_callback = version_callback_factory("HMDA Data Manager", __version__)


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
    """[bold cyan]HMDA Data Manager[/bold cyan].

    Tools for downloading and processing CFPB's Home Mortgage Disclosure Act (HMDA) data.
    """
    configure_logging(level="INFO")


# ============================================================================
# Download Command
# ============================================================================

@app.command()
def download(
    min_year: int = typer.Option(
        ...,
        "--min-year",
        help="First year to download (e.g., 2018)",
    ),
    max_year: int = typer.Option(
        ...,
        "--max-year",
        help="Last year to download, inclusive (e.g., 2024)",
    ),
    destination: str | None = typer.Option(
        None,
        "--destination",
        help="Destination folder for downloads (default: data/raw)",
    ),
    include_mlar: bool = typer.Option(
        False,
        "--include-mlar",
        help="Include Modified LAR (MLAR) files",
    ),
    include_historical: bool = typer.Option(
        False,
        "--include-historical",
        help="Include historical 2007-2017 files",
    ),
    pause: PauseOption = None,
    wait: int = typer.Option(
        10,
        "--wait",
        help="Seconds to wait for JavaScript to load",
    ),
    overwrite: str = typer.Option(
        "skip",
        "--overwrite",
        help="Overwrite behavior: skip, always, if_newer, if_size_diff",
    ),
) -> None:
    """Download HMDA data files from the CFPB website.

    Files are automatically organized into subdirectories:
    - loans/              (LAR files)
    - panel/              (Panel files)
    - transmittal_series/ (TS files)
    - msamd/              (Geography files)
    - misc/               (Other files)

    Examples:
      mortgage-data hmda download --min-year 2018 --max-year 2024
      mortgage-data hmda download --min-year 2020 --max-year 2024 --include-mlar
    """
    if min_year > max_year:
        console.print(
            f"[red]Error: --min-year ({min_year}) must be <= --max-year ({max_year})[/red]"
        )
        raise typer.Exit(code=1)

    try:
        years_range = range(min_year, max_year + 1)

        console.print(
            f"[cyan]Downloading HMDA data for years: {min_year}-{max_year}[/cyan]"
        )

        run_download(
            years=years_range,
            destination_folder=destination,
            include_mlar=include_mlar,
            include_historical=include_historical,
            pause=pause,
            wait_time=wait,
            overwrite_mode=overwrite,
        )

        console.print("[green]✓ Download complete![/green]")

    except Exception as e:
        console.print(f"[red]Download failed: {e}[/red]")
        raise typer.Exit(code=1)


# ============================================================================
# Import Commands
# ============================================================================

def _resolve_years(period: str, min_year: int | None, max_year: int | None) -> tuple[int, int]:
    """Apply per-period min/max-year defaults."""
    if period not in _PERIOD_DEFAULTS:
        console.print(
            f"[red]Invalid period: {period}. Must be {', '.join(_VALID_PERIODS)}[/red]"
        )
        raise typer.Exit(code=1)
    default_min, default_max = _PERIOD_DEFAULTS[period]
    return (min_year or default_min, max_year or default_max)


def _normalize_results(results: object, dataset_hint: str = "loans") -> dict[str, bool]:
    """Coerce pipeline returns (bool or dict) to a dataset -> success dict."""
    if isinstance(results, dict):
        return results
    return {dataset_hint: bool(results)}


def _report_results(results: dict[str, bool], action: str) -> None:
    """Print a results table and exit non-zero if anything failed."""
    console.print(f"\n[bold]{action} Results:[/bold]")
    for dataset, success in results.items():
        status = "[green]✓[/green]" if success else "[red]✗[/red]"
        console.print(f"  {status} {dataset}")
    if not all(results.values()):
        console.print(f"\n[yellow]⚠ Some {action.lower()} steps failed[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"\n[green]✓ {action} completed successfully![/green]")


def _dispatch_step(
    *,
    step: str,
    period: str,
    min_year: int,
    max_year: int,
    datasets: list[str] | None,
    overwrite: bool,
) -> dict[str, bool]:
    """Dispatch a single bronze or silver step to the right pipeline function."""
    if step == "bronze":
        if period == "post2018":
            return bronze_post2018(
                min_year=min_year, max_year=max_year, datasets=datasets, overwrite=overwrite
            )
        if period == "2007-2017":
            return _normalize_results(
                bronze_2007_2017(min_year=min_year, max_year=max_year, overwrite=overwrite)
            )
        if period == "pre2007":
            return bronze_pre2007(
                min_year=min_year, max_year=max_year, datasets=datasets, overwrite=overwrite
            )

    if step == "silver":
        if period == "post2018":
            return silver_post2018(
                min_year=min_year, max_year=max_year, datasets=datasets, overwrite=overwrite
            )
        if period == "2007-2017":
            return _normalize_results(
                silver_2007_2017(min_year=min_year, max_year=max_year, overwrite=overwrite)
            )
        if period == "pre2007":
            return silver_pre2007(
                min_year=min_year, max_year=max_year, datasets=datasets, overwrite=overwrite
            )

    raise ValueError(f"Unknown step/period combination: {step}/{period}")


@app.command()
def bronze(
    period: str = typer.Argument(..., help=f"Time period: {', '.join(_VALID_PERIODS)}"),
    min_year: int | None = typer.Option(None, "--min-year", help="Minimum year to process"),
    max_year: int | None = typer.Option(None, "--max-year", help="Maximum year to process"),
    datasets: list[str] | None = typer.Option(
        None,
        "--datasets",
        "-t",
        help="Datasets: loans, panel, transmittal_series (post2018/pre2007 only; can repeat)",
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing files"),
) -> None:
    """Build the bronze (raw → parquet) layer for an HMDA period.

    Examples:
      mortgage-data hmda bronze post2018 --min-year 2018 --max-year 2025
      mortgage-data hmda bronze 2007-2017 --overwrite
      mortgage-data hmda bronze pre2007 --datasets loans
    """
    try:
        min_year, max_year = _resolve_years(period, min_year, max_year)
        console.print(f"[cyan]Building bronze for {period} ({min_year}-{max_year})[/cyan]")
        results = _dispatch_step(
            step="bronze",
            period=period,
            min_year=min_year,
            max_year=max_year,
            datasets=datasets,
            overwrite=overwrite,
        )
        _report_results(results, action="Bronze")
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Bronze failed: {e}[/red]")
        logger.exception("Bronze error")
        raise typer.Exit(code=1)


@app.command()
def silver(
    period: str = typer.Argument(..., help=f"Time period: {', '.join(_VALID_PERIODS)}"),
    min_year: int | None = typer.Option(None, "--min-year", help="Minimum year to process"),
    max_year: int | None = typer.Option(None, "--max-year", help="Maximum year to process"),
    datasets: list[str] | None = typer.Option(
        None,
        "--datasets",
        "-t",
        help="Datasets: loans, panel, transmittal_series (post2018/pre2007 only; can repeat)",
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing files"),
) -> None:
    """Build the silver (Hive-partitioned) layer for an HMDA period.

    Examples:
      mortgage-data hmda silver post2018 --min-year 2018 --max-year 2025
      mortgage-data hmda silver 2007-2017 --overwrite
      mortgage-data hmda silver pre2007 --datasets loans
    """
    try:
        min_year, max_year = _resolve_years(period, min_year, max_year)
        console.print(f"[cyan]Building silver for {period} ({min_year}-{max_year})[/cyan]")
        results = _dispatch_step(
            step="silver",
            period=period,
            min_year=min_year,
            max_year=max_year,
            datasets=datasets,
            overwrite=overwrite,
        )
        _report_results(results, action="Silver")
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Silver failed: {e}[/red]")
        logger.exception("Silver error")
        raise typer.Exit(code=1)


@app.command()
def pipeline(
    period: str = typer.Argument(..., help=f"Time period: {', '.join(_VALID_PERIODS)}"),
    min_year: int | None = typer.Option(None, "--min-year", help="Minimum year to process"),
    max_year: int | None = typer.Option(None, "--max-year", help="Maximum year to process"),
    datasets: list[str] | None = typer.Option(
        None,
        "--datasets",
        "-t",
        help="Datasets: loans, panel, transmittal_series (post2018/pre2007 only; can repeat)",
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite existing files"),
) -> None:
    """Build bronze and silver in one step (the former ``hmda import``).

    Each period's bronze step runs first; silver runs only for datasets
    whose bronze build succeeded.

    Examples:
      mortgage-data hmda pipeline post2018 --min-year 2018 --max-year 2025
      mortgage-data hmda pipeline 2007-2017 --min-year 2007 --max-year 2017
      mortgage-data hmda pipeline pre2007 --min-year 1990 --max-year 2006
    """
    try:
        min_year, max_year = _resolve_years(period, min_year, max_year)
        console.print(f"[cyan]Running pipeline for {period} ({min_year}-{max_year})[/cyan]")

        if period == "post2018":
            results = import_post2018(
                datasets=datasets, min_year=min_year, max_year=max_year, overwrite=overwrite
            )
        elif period == "2007-2017":
            results = _normalize_results(
                import_2007_2017(min_year=min_year, max_year=max_year, overwrite=overwrite)
            )
        else:  # pre2007 (already validated)
            results = import_pre2007(
                datasets=datasets, min_year=min_year, max_year=max_year, overwrite=overwrite
            )

        _report_results(results, action="Pipeline")
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Pipeline failed: {e}[/red]")
        logger.exception("Pipeline error")
        raise typer.Exit(code=1)


# ============================================================================
# Info Command
# ============================================================================


@app.command()
def info() -> None:
    """Display HMDA configuration information.

    Shows the current configuration paths and settings for HMDA data.

    Examples:
      mortgage-data hmda info
    """
    print_info_table(
        "HMDA Configuration",
        {
            "Data Directory": HMDAConfig.HMDA_DATA_DIR,
            "Raw Directory": HMDAConfig.HMDA_RAW_DIR,
            "Bronze Directory": HMDAConfig.HMDA_BRONZE_DIR,
            "Silver Directory": HMDAConfig.HMDA_SILVER_DIR,
        },
    )

    # Show available commands
    console.print("\n[bold cyan]Available Commands:[/bold cyan]")
    console.print("  download    - Download HMDA data files from CFPB")
    console.print("  bronze      - Build the bronze (raw → parquet) layer")
    console.print("  silver      - Build the silver (Hive-partitioned) layer")
    console.print("  pipeline    - Bronze + silver in one step")
    console.print("  info        - Show this configuration information")


def cli_main() -> None:
    """Entry point for standalone HMDA CLI."""
    app()


if __name__ == "__main__":
    cli_main()
