"""USPS crosswalk CLI - Main entry point.

Typer-based command-line interface for USPS crosswalk data management.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from mortgage_data_manager import __version__
from mortgage_data_manager.core.cli.download_options import (
    OverwriteOption,
    PauseOption,
    VerboseOption,
    report_download_results,
)
from mortgage_data_manager.core.cli.formatting import console
from mortgage_data_manager.core.cli.validation import version_callback_factory
from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.sources.usps_crosswalk.config import (
    VALID_CROSSWALK_NAMES,
    USPSCrosswalkConfig,
)

# Create Typer app
app = typer.Typer(
    name="usps-crosswalk",
    help="USPS crosswalk data management commands",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

logger = get_logger(__name__)

DatasetsOption = Annotated[
    list[str] | None,
    typer.Option("--datasets", "-t", help="Crosswalk types to process (default: all 12)"),
]
MinYearOption = Annotated[
    int | None,
    typer.Option("--min-year", help="First year (default: each type's own start)"),
]
MaxYearOption = Annotated[
    int | None,
    typer.Option("--max-year", help="Last year (default: through the current quarter)"),
]

_version_callback = version_callback_factory("USPS Crosswalk Data Manager", __version__)


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
    """[bold cyan]USPS Crosswalk Data Manager[/bold cyan].

    Tools for downloading and processing HUD USPS ZIP Code Crosswalk data.
    Supports 12 crosswalk types mapping between ZIP codes and Census geographies.
    """
    pass


# ============================================================================
# Download Command
# ============================================================================


@app.command()
def download(
    datasets: DatasetsOption = None,
    min_year: MinYearOption = None,
    max_year: MaxYearOption = None,
    overwrite: OverwriteOption = False,
    pause: PauseOption = None,
    verbose: VerboseOption = False,
):
    """Download USPS crosswalk data from the HUD API.

    Downloads all 12 crosswalk types from the HUD USPS API. Requires
    USPS_CROSSWALK_API_KEY to be set in your .env file or environment.

    [bold yellow]Note:[/bold yellow] The API 'query=All' parameter only works
    reliably for 2021+ data. Pre-2021 data may return errors.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data usps-crosswalk download
      mortgage-data usps-crosswalk download -t ZIP_TRACT -t TRACT_ZIP
      mortgage-data usps-crosswalk download --min-year 2021 --pause 2 --verbose
    """
    log_level = "DEBUG" if verbose else "INFO"
    configure_logging(level=log_level)

    try:
        from mortgage_data_manager.sources.usps_crosswalk.download import download_all

        console.print(
            f"[cyan]Downloading USPS crosswalk data to {USPSCrosswalkConfig.USPS_CROSSWALK_RAW_DIR}...[/cyan]"
        )
        results = download_all(
            datasets,
            min_year=min_year,
            max_year=max_year,
            overwrite=overwrite,
            pause=pause,
        )
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]Download failed: {e}[/red]")
        logger.exception("Download error")
        raise typer.Exit(code=1)
    report_download_results(results)


# ============================================================================
# Bronze Command
# ============================================================================


@app.command()
def bronze(
    datasets: DatasetsOption = None,
    min_year: MinYearOption = None,
    max_year: MaxYearOption = None,
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
):
    """Build per-quarter bronze parquet from raw JSON.

    Reads each quarter's untouched raw JSON, enforces the schema (zero-padded
    geographic codes, numeric ratios), and writes a per-quarter parquet. Bronze
    is the analysis layer for this source — read across quarters with
    ``load_crosswalk()`` / ``load_crosswalk_by_vintage()`` rather than building
    a combined file.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data usps-crosswalk bronze
      mortgage-data usps-crosswalk bronze --datasets ZIP_TRACT --datasets TRACT_ZIP
      mortgage-data usps-crosswalk bronze --min-year 2015 --max-year 2024
    """
    log_level = "DEBUG" if verbose else "INFO"
    configure_logging(level=log_level)

    try:
        from mortgage_data_manager.sources.usps_crosswalk.import_bronze import build_bronze

        USPSCrosswalkConfig.ensure_directories()
        written = build_bronze(
            datasets=datasets, min_year=min_year, max_year=max_year, overwrite=overwrite
        )
        if not written:
            console.print("[yellow]No bronze files written (nothing new to build).[/yellow]")
            raise typer.Exit(code=1)
        for type_name, n in written.items():
            console.print(f"  {type_name}: {n:,} quarterly file(s)")
        console.print(
            f"\n[green]Bronze complete: {sum(written.values()):,} file(s) "
            f"across {len(written)} type(s)[/green]"
        )

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Bronze build failed: {e}[/red]")
        logger.exception("Bronze error")
        raise typer.Exit(code=1)


# ============================================================================
# Pipeline Command
# ============================================================================


@app.command()
def pipeline(
    datasets: DatasetsOption = None,
    min_year: MinYearOption = None,
    max_year: MaxYearOption = None,
    overwrite: OverwriteOption = False,
    pause: PauseOption = None,
    skip_download: Annotated[
        bool, typer.Option("--skip-download", help="Build bronze from the raw already present")
    ] = False,
    skip_bronze: Annotated[bool, typer.Option("--skip-bronze", help="Fetch raw only")] = False,
    verbose: VerboseOption = False,
):
    """Bring the crosswalks up to date: download → bronze.

    The normal way to run this source. Both steps skip what is already on disk,
    so a bare [bold]pipeline[/bold] asks HUD for anything newer than what you
    have, fetches it, and builds the parquet. Safe to re-run any time.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data usps-crosswalk pipeline
      mortgage-data usps-crosswalk pipeline -t ZIP_TRACT -t TRACT_ZIP
      mortgage-data usps-crosswalk pipeline --skip-download
    """
    configure_logging(level="DEBUG" if verbose else "INFO")

    try:
        from mortgage_data_manager.sources.usps_crosswalk.pipeline import run_pipeline

        USPSCrosswalkConfig.ensure_directories()
        results = run_pipeline(
            datasets,
            min_year=min_year,
            max_year=max_year,
            overwrite=overwrite,
            pause=pause,
            skip_download=skip_download,
            skip_bronze=skip_bronze,
        )
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]Pipeline failed: {e}[/red]")
        logger.exception("Pipeline error")
        raise typer.Exit(code=1)

    saved = results.get("download", {}).get("saved", 0)
    built = sum(results.get("bronze", {}).values())
    if not saved and not built:
        console.print("[green]Already up to date — nothing new to fetch or build.[/green]")
        return
    if "download" in results:
        console.print(f"  raw:    {saved:,} file(s) downloaded")
    if "bronze" in results:
        console.print(f"  bronze: {built:,} file(s) built across {len(results['bronze'])} type(s)")
    console.print("\n[green]Pipeline complete.[/green]")


# ============================================================================
# Validate Command
# ============================================================================


@app.command()
def validate(
    crosswalk_type: Annotated[
        str,
        typer.Argument(help="Crosswalk type to validate, or 'all'"),
    ] = "all",
    skip_ratios: Annotated[
        bool,
        typer.Option("--skip-ratios", help="Skip the per-quarter ratio-sum check"),
    ] = False,
    sample_quarters: Annotated[
        int,
        typer.Option("--sample-quarters", help="Quarters to ratio-check per type"),
    ] = 4,
):
    """Check bronze coverage and ratio sums.

    Reports quarters HUD should have published that are missing from bronze,
    and source geographies whose allocation ratios don't sum to ~1.0.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data usps-crosswalk validate
      mortgage-data usps-crosswalk validate ZIP_TRACT
      mortgage-data usps-crosswalk validate all --skip-ratios
    """
    configure_logging(level="INFO")

    from mortgage_data_manager.sources.usps_crosswalk.validate import validate_crosswalk

    if crosswalk_type == "all":
        types_to_check = VALID_CROSSWALK_NAMES
    else:
        if crosswalk_type not in VALID_CROSSWALK_NAMES:
            console.print(
                f"[red]Invalid crosswalk type: {crosswalk_type}. "
                f"Valid types: {VALID_CROSSWALK_NAMES}[/red]"
            )
            raise typer.Exit(code=1)
        types_to_check = [crosswalk_type]

    table = Table(title="USPS crosswalk validation", show_lines=False)
    table.add_column("Type", style="cyan")
    table.add_column("Missing quarters", justify="right")
    table.add_column("Detail")

    failed = []
    for ct in types_to_check:
        result = validate_crosswalk(
            ct, check_ratios=not skip_ratios, sample_quarters=sample_quarters
        )
        if not result.is_valid:
            failed.append(result)
        missing = result.missing_quarters
        detail = "; ".join(result.ratio_issues) if result.ratio_issues else ""
        if missing:
            shown = ", ".join(f"{y} Q{q}" for y, q in missing[:4])
            if len(missing) > 4:
                shown += f", +{len(missing) - 4} more"
            detail = f"{shown}{' | ' + detail if detail else ''}"
        table.add_row(
            ct,
            f"[red]{len(missing)}[/red]" if missing else "[green]0[/green]",
            detail or "[green]ok[/green]",
        )

    console.print(table)

    if not failed:
        console.print("[green]All validations passed.[/green]")
        return
    console.print(f"[yellow]{len(failed)} of {len(types_to_check)} type(s) flagged.[/yellow]")
    raise typer.Exit(code=1)


# ============================================================================
# Info Command
# ============================================================================


@app.command()
def info():
    """Display USPS crosswalk configuration and crosswalk type information.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data usps-crosswalk info
    """
    # Configuration table
    config_table = Table(
        title="USPS Crosswalk Configuration", show_header=True, header_style="bold cyan"
    )
    config_table.add_column("Setting", style="cyan")
    config_table.add_column("Value", style="white")

    config_table.add_row("Data Directory", str(USPSCrosswalkConfig.USPS_CROSSWALK_DATA_DIR))
    config_table.add_row("Raw Directory", str(USPSCrosswalkConfig.USPS_CROSSWALK_RAW_DIR))
    config_table.add_row("Bronze Directory", str(USPSCrosswalkConfig.USPS_CROSSWALK_BRONZE_DIR))
    config_table.add_row("API Base URL", USPSCrosswalkConfig.USPS_CROSSWALK_API_BASE_URL)
    config_table.add_row(
        "API Key", "***" if USPSCrosswalkConfig.USPS_CROSSWALK_API_KEY else "[red]NOT SET[/red]"
    )
    config_table.add_row("Request Delay", f"{USPSCrosswalkConfig.USPS_CROSSWALK_REQUEST_DELAY}s")

    console.print(config_table)
    console.print()

    # Crosswalk types table
    from mortgage_data_manager.sources.usps_crosswalk.config import CROSSWALK_TYPES

    types_table = Table(title="Crosswalk Types", show_header=True, header_style="bold cyan")
    types_table.add_column("ID", style="cyan", justify="right")
    types_table.add_column("Name", style="white")
    types_table.add_column("From", style="green")
    types_table.add_column("To", style="green")
    types_table.add_column("Available From", style="yellow")

    for type_id, ct in CROSSWALK_TYPES.items():
        types_table.add_row(
            str(type_id),
            ct.name,
            ct.source_geo,
            ct.target_geo,
            f"{ct.start_year} Q{ct.start_quarter}",
        )

    console.print(types_table)


def cli_main():
    """Entry point for standalone USPS crosswalk CLI."""
    app()


if __name__ == "__main__":
    cli_main()
