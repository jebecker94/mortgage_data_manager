"""HMDA CLI - Main entry point.

Typer-based command-line interface for HMDA data management.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, cast

import typer

from mortgage_data_manager import __version__
from mortgage_data_manager.core.cli.download_options import (
    OutputDirOption,
    OverwriteOption,
    PauseOption,
)
from mortgage_data_manager.core.cli.formatting import console, print_info_table, print_summary
from mortgage_data_manager.core.cli.validation import version_callback_factory
from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.sources.hmda.config import (
    DEFAULT_PRODUCTS,
    VALID_DATASETS,
    VALID_DOWNLOAD_DATASETS,
    VALID_PRODUCTS,
    HMDAConfig,
)
from mortgage_data_manager.sources.hmda.download import (
    HMDAFileFormat,
    discover_files,
    plan_downloads,
)
from mortgage_data_manager.sources.hmda.pipeline import (
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
    products: Annotated[
        list[str] | None,
        typer.Option(
            "--products",
            "-p",
            help=(
                f"Products to fetch: {', '.join(VALID_PRODUCTS)} "
                f"(repeatable; default {', '.join(DEFAULT_PRODUCTS)})"
            ),
        ),
    ] = None,
    datasets: Annotated[
        list[str] | None,
        typer.Option(
            "--datasets",
            "-t",
            help=(
                f"Record types to fetch: {', '.join(VALID_DOWNLOAD_DATASETS)} "
                "(repeatable; default all)"
            ),
        ),
    ] = None,
    min_year: Annotated[
        int | None,
        typer.Option("--min-year", help="First filing year (default: each series' own start)"),
    ] = None,
    max_year: Annotated[
        int | None,
        typer.Option("--max-year", help="Last filing year, inclusive (default: current year)"),
    ] = None,
    output_dir: OutputDirOption = None,
    file_format: Annotated[
        str,
        typer.Option("--file-format", help="Delimiter variant to fetch: csv or pipe"),
    ] = "csv",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would be fetched without downloading"),
    ] = False,
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            help="Re-download files whose size differs from the server's (catches restatements)",
        ),
    ] = False,
    overwrite: OverwriteOption = False,
    pause: PauseOption = None,
) -> None:
    """Download HMDA data files from the CFPB file servers.

    Files land in subdirectories of the raw layer by record type:
    [cyan]loans/[/cyan], [cyan]panel/[/cyan], [cyan]transmittal_series/[/cyan],
    [cyan]msamd/[/cyan], and [cyan]misc/[/cyan].

    Years CFPB has not published yet are detected and reported, not treated as
    failures — so the leading edge is picked up automatically once released.

    Examples:
      mortgage-data hmda download
      mortgage-data hmda download --dry-run
      mortgage-data hmda download -p snapshot -t loans -t transmittal_series
      mortgage-data hmda download -p historic
      mortgage-data hmda download -p mlar --min-year 2024
      mortgage-data hmda download --refresh
    """
    if min_year is not None and max_year is not None and min_year > max_year:
        console.print(
            f"[red]Error: --min-year ({min_year}) must be <= --max-year ({max_year})[/red]"
        )
        raise typer.Exit(code=1)

    if file_format not in ("csv", "pipe"):
        console.print(
            f"[red]Error: --file-format must be 'csv' or 'pipe', got {file_format!r}[/red]"
        )
        raise typer.Exit(code=1)

    try:
        files = discover_files(
            products,
            datasets,
            min_year=min_year,
            max_year=max_year,
            file_format=cast(HMDAFileFormat, file_format),
        )
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e

    if dry_run:
        root = Path(output_dir) if output_dir is not None else HMDAConfig.HMDA_RAW_DIR
        tasks, unpublished = plan_downloads(files, root)
        pending = [t for t in tasks if not t.destination.exists()]
        print_summary(
            "Download Plan",
            {
                "Candidates": len(files),
                "Already on disk": len(tasks) - len(pending),
                "To download": len(pending),
                "Not published yet": len(unpublished),
            },
            style="green",
        )
        for task in pending:
            console.print(f"  [green]fetch[/green]  {task.destination.name}")
        for file in unpublished:
            console.print(f"  [dim]unpublished  {file.filename}[/dim]")
        return

    try:
        summary = run_download(
            products,
            datasets,
            output_dir=output_dir,
            min_year=min_year,
            max_year=max_year,
            file_format=cast(HMDAFileFormat, file_format),
            overwrite=overwrite,
            refresh=refresh,
            pause=pause,
        )
    except Exception as e:
        console.print(f"[red]Download failed: {e}[/red]")
        raise typer.Exit(code=1) from e

    print_summary(
        "HMDA Download Complete",
        {
            "Downloaded": summary.get("success", 0),
            "Skipped": summary.get("skipped_exists", 0) + summary.get("skipped_size_match", 0),
            "Failed": summary.get("failed", 0),
        },
        style="green" if summary.get("failed", 0) == 0 else "yellow",
    )
    if summary.get("failed", 0) > 0:
        raise typer.Exit(code=1)


# ============================================================================
# Import Commands
# ============================================================================


def _resolve_years(period: str, min_year: int | None, max_year: int | None) -> tuple[int, int]:
    """Apply per-period min/max-year defaults."""
    if period not in _PERIOD_DEFAULTS:
        console.print(f"[red]Invalid period: {period}. Must be {', '.join(_VALID_PERIODS)}[/red]")
        raise typer.Exit(code=1)
    default_min, default_max = _PERIOD_DEFAULTS[period]
    return (min_year or default_min, max_year or default_max)


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
            return bronze_2007_2017(
                min_year=min_year, max_year=max_year, datasets=datasets, overwrite=overwrite
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
            return silver_2007_2017(
                min_year=min_year, max_year=max_year, datasets=datasets, overwrite=overwrite
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
        help=(f"Datasets: {', '.join(VALID_DATASETS)} (post2018/pre2007 only; can repeat)"),
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
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)
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
        help=(f"Datasets: {', '.join(VALID_DATASETS)} (post2018/pre2007 only; can repeat)"),
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
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)
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
        help=(f"Datasets: {', '.join(VALID_DATASETS)} (post2018/pre2007 only; can repeat)"),
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
            results = import_2007_2017(
                min_year=min_year, max_year=max_year, datasets=datasets, overwrite=overwrite
            )
        else:  # pre2007 (already validated)
            results = import_pre2007(
                datasets=datasets, min_year=min_year, max_year=max_year, overwrite=overwrite
            )

        _report_results(results, action="Pipeline")
    except typer.Exit:
        raise
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)
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
