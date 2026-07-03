"""HUD CLI - Main entry point.

Typer-based command-line interface for HUD crosswalk data management.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from mortgage_data_manager import __version__
from mortgage_data_manager.core.cli.download_options import (
    OverwriteOption,
    VerboseOption,
)
from mortgage_data_manager.core.cli.formatting import console
from mortgage_data_manager.core.cli.validation import version_callback_factory
from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.hud.config import VALID_CROSSWALK_NAMES, HUDConfig

# Create Typer app
app = typer.Typer(
    name="hud",
    help="HUD USPS crosswalk data management commands",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

logger = get_logger(__name__)

_version_callback = version_callback_factory("HUD Data Manager", __version__)


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
    """[bold cyan]HUD Data Manager[/bold cyan].

    Tools for downloading and processing HUD USPS ZIP Code Crosswalk data.
    Supports 12 crosswalk types mapping between ZIP codes and Census geographies.
    """
    pass


# ============================================================================
# Download Command
# ============================================================================


@app.command()
def download(
    overwrite: OverwriteOption = False,
    min_year: Annotated[
        int,
        typer.Option("--min-year", help="First year to download"),
    ] = 2010,
    verbose: VerboseOption = False,
):
    """Download HUD crosswalk data from the HUD API.

    Downloads all 12 crosswalk types from the HUD USPS API. Requires
    HUD_API_KEY to be set in your .env file or environment.

    [bold yellow]Note:[/bold yellow] The API 'query=All' parameter only works
    reliably for 2021+ data. Pre-2021 data may return errors.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data hud download
      mortgage-data hud download --overwrite
      mortgage-data hud download --min-year 2021 --verbose
    """
    log_level = "DEBUG" if verbose else "INFO"
    configure_logging(level=log_level)

    try:
        from mortgage_data_manager.hud.download import download_all

        console.print(f"[cyan]Downloading HUD crosswalk data to {HUDConfig.HUD_RAW_DIR}...[/cyan]")
        download_all(overwrite=overwrite, min_year=min_year)
        console.print("[green]Download complete![/green]")

    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]Download failed: {e}[/red]")
        logger.exception("Download error")
        raise typer.Exit(code=1)


# ============================================================================
# Bronze Command
# ============================================================================


def _validate_datasets(datasets: list[str] | None) -> None:
    """Exit with an error if any requested crosswalk type is invalid."""
    for t in datasets or []:
        if t not in VALID_CROSSWALK_NAMES:
            console.print(
                f"[red]Invalid crosswalk type: {t}. Valid datasets: {VALID_CROSSWALK_NAMES}[/red]"
            )
            raise typer.Exit(code=1)


@app.command()
def bronze(
    datasets: Annotated[
        list[str] | None,
        typer.Option(
            "--datasets",
            "-t",
            help="Datasets (crosswalk types) to process (default: all available)",
        ),
    ] = None,
    min_year: Annotated[
        int,
        typer.Option("--min-year", help="First year to include"),
    ] = 2010,
    max_year: Annotated[
        int,
        typer.Option("--max-year", help="Last year to include"),
    ] = 2025,
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
):
    """Build per-quarter bronze parquet from raw JSON.

    Reads each quarter's untouched raw JSON, enforces the schema (zero-padded
    geographic codes, numeric ratios), and writes a per-quarter parquet. Run
    ``hud silver`` afterwards to combine quarters into longitudinal datasets.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data hud bronze
      mortgage-data hud bronze --datasets ZIP_TRACT --datasets TRACT_ZIP
      mortgage-data hud bronze --min-year 2015 --max-year 2024
    """
    log_level = "DEBUG" if verbose else "INFO"
    configure_logging(level=log_level)

    if min_year > max_year:
        console.print(
            f"[red]Error: --min-year ({min_year}) must be <= --max-year ({max_year})[/red]"
        )
        raise typer.Exit(code=1)
    _validate_datasets(datasets)

    try:
        from mortgage_data_manager.hud.import_bronze import build_bronze

        HUDConfig.ensure_directories()
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


@app.command()
def silver(
    datasets: Annotated[
        list[str] | None,
        typer.Option(
            "--datasets",
            "-t",
            help="Datasets (crosswalk types) to combine (default: all available)",
        ),
    ] = None,
    min_year: Annotated[
        int,
        typer.Option("--min-year", help="First year to include"),
    ] = 2010,
    max_year: Annotated[
        int,
        typer.Option("--max-year", help="Last year to include"),
    ] = 2025,
    save_csv: Annotated[
        bool,
        typer.Option("--csv", help="Also save as pipe-delimited CSV"),
    ] = False,
    save_dta: Annotated[
        bool,
        typer.Option("--dta", help="Also save as Stata DTA"),
    ] = False,
    verbose: VerboseOption = False,
):
    """Combine per-quarter bronze parquet into longitudinal silver datasets.

    Concatenates each crosswalk type's quarters (deduplicated, sorted) into a
    unified parquet, with optional CSV/Stata output and a rounded ZIP_TRACT
    variant.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data hud silver
      mortgage-data hud silver --datasets ZIP_TRACT --datasets TRACT_ZIP
      mortgage-data hud silver --min-year 2015 --max-year 2024 --csv --dta
    """
    log_level = "DEBUG" if verbose else "INFO"
    configure_logging(level=log_level)

    if min_year > max_year:
        console.print(
            f"[red]Error: --min-year ({min_year}) must be <= --max-year ({max_year})[/red]"
        )
        raise typer.Exit(code=1)
    _validate_datasets(datasets)

    try:
        from mortgage_data_manager.hud.import_silver import process_all, process_crosswalk_type

        HUDConfig.ensure_directories()
        if datasets:
            for t in datasets:
                console.print(f"[cyan]Combining {t}...[/cyan]")
                process_crosswalk_type(
                    t, min_year=min_year, max_year=max_year, save_csv=save_csv, save_dta=save_dta
                )
        else:
            console.print("[cyan]Combining all available crosswalk types...[/cyan]")
            process_all(min_year=min_year, max_year=max_year, save_csv=save_csv, save_dta=save_dta)

        console.print("[green]Silver complete![/green]")

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Silver build failed: {e}[/red]")
        logger.exception("Silver error")
        raise typer.Exit(code=1)


# ============================================================================
# Validate Command
# ============================================================================


@app.command()
def validate(
    crosswalk_type: Annotated[
        str,
        typer.Argument(help="Crosswalk type to validate, or 'all'"),
    ] = "all",
    skip_geo: Annotated[
        bool,
        typer.Option("--skip-geo", help="Skip geographic code validation"),
    ] = False,
    skip_ratios: Annotated[
        bool,
        typer.Option("--skip-ratios", help="Skip ratio sum validation"),
    ] = False,
    sample_quarters: Annotated[
        int,
        typer.Option("--sample-quarters", help="Number of quarters to sample"),
    ] = 4,
):
    """Validate HUD crosswalk data files.

    Checks file coverage, geographic code formats, and ratio consistency.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data hud validate
      mortgage-data hud validate ZIP_TRACT
      mortgage-data hud validate all --skip-ratios
    """
    configure_logging(level="INFO")

    from mortgage_data_manager.hud.validate import validate_crosswalk

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

    all_valid = True
    for ct in types_to_check:
        result = validate_crosswalk(
            ct,
            check_geo_codes=not skip_geo,
            check_ratios=not skip_ratios,
            sample_quarters=sample_quarters,
        )
        console.print(str(result))
        console.print()
        if not result.is_valid:
            all_valid = False

    if all_valid:
        console.print("[green]All validations passed![/green]")
    else:
        console.print("[yellow]Some validations failed.[/yellow]")
        raise typer.Exit(code=1)


# ============================================================================
# Migrate Command
# ============================================================================


migrate_app = typer.Typer(
    help="Migrate bronze crosswalk parquet between flat and Hive partitioning",
    no_args_is_help=True,
)
app.add_typer(migrate_app, name="migrate")

# migrate operates on the per-quarter bronze parquet (raw is JSON since T3.5).
_CrosswalkArg = Annotated[str, typer.Argument(help="Crosswalk type or 'all'")]
_DryRunOpt = Annotated[bool, typer.Option("--dry-run", help="Show what would be done")]


@migrate_app.command("status")
def migrate_status_cmd():
    """Show each crosswalk type's bronze storage format (flat vs hive).

    [bold cyan]Examples:[/bold cyan]

      mortgage-data hud migrate status
    """
    configure_logging(level="INFO")
    from mortgage_data_manager.hud.migrate import detect_storage_format

    input_dir = HUDConfig.HUD_BRONZE_DIR
    console.print(f"[cyan]Storage format status (in {input_dir}):[/cyan]")
    for ct in VALID_CROSSWALK_NAMES:
        crosswalk_dir = input_dir / ct
        if crosswalk_dir.exists():
            console.print(f"  {ct}: {detect_storage_format(crosswalk_dir)}")


@migrate_app.command("run")
def migrate_run_cmd(
    crosswalk_type: _CrosswalkArg = "all",
    dry_run: _DryRunOpt = False,
    delete_original: Annotated[
        bool,
        typer.Option("--delete-original", help="Delete original files after migration"),
    ] = False,
    output_dir: Annotated[
        str | None,
        typer.Option("--output-dir", help="Destination directory (default: in place)"),
    ] = None,
):
    """Migrate bronze crosswalk parquet to Hive partitioning.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data hud migrate run all --dry-run
      mortgage-data hud migrate run ZIP_TRACT
    """
    configure_logging(level="INFO")
    from mortgage_data_manager.hud.migrate import migrate_all
    from mortgage_data_manager.hud.migrate import (
        migrate_crosswalk_type as do_migrate,
    )

    input_dir = HUDConfig.HUD_BRONZE_DIR
    resolved_output = input_dir if output_dir is None else Path(output_dir)

    if input_dir != resolved_output:
        console.print(f"[cyan]Migrating from {input_dir} to {resolved_output}[/cyan]")
    if crosswalk_type == "all":
        results = migrate_all(input_dir, resolved_output, dry_run, not delete_original)
        for ct, result in results.items():
            console.print(f"  {ct}: {result}")
    else:
        result = do_migrate(
            crosswalk_type, input_dir, resolved_output, dry_run, not delete_original
        )
        console.print(str(result))


@migrate_app.command("rollback")
def migrate_rollback_cmd(
    crosswalk_type: _CrosswalkArg = "all",
    dry_run: _DryRunOpt = False,
):
    """Roll back a Hive migration back to flat bronze parquet.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data hud migrate rollback ZIP_TRACT
    """
    configure_logging(level="INFO")
    from mortgage_data_manager.hud.migrate import rollback_crosswalk_type

    input_dir = HUDConfig.HUD_BRONZE_DIR
    if crosswalk_type == "all":
        for ct in VALID_CROSSWALK_NAMES:
            console.print(f"[cyan]Rolling back {ct}...[/cyan]")
            console.print(f"  {rollback_crosswalk_type(ct, input_dir, dry_run)}")
    else:
        console.print(str(rollback_crosswalk_type(crosswalk_type, input_dir, dry_run)))


# ============================================================================
# Info Command
# ============================================================================


@app.command()
def info():
    """Display HUD configuration and crosswalk type information.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data hud info
    """
    # Configuration table
    config_table = Table(title="HUD Configuration", show_header=True, header_style="bold cyan")
    config_table.add_column("Setting", style="cyan")
    config_table.add_column("Value", style="white")

    config_table.add_row("Data Directory", str(HUDConfig.HUD_DATA_DIR))
    config_table.add_row("Raw Directory", str(HUDConfig.HUD_RAW_DIR))
    config_table.add_row("Bronze Directory", str(HUDConfig.HUD_BRONZE_DIR))
    config_table.add_row("API Base URL", HUDConfig.HUD_API_BASE_URL)
    config_table.add_row("API Key", "***" if HUDConfig.HUD_API_KEY else "[red]NOT SET[/red]")
    config_table.add_row("Request Delay", f"{HUDConfig.HUD_REQUEST_DELAY}s")

    console.print(config_table)
    console.print()

    # Crosswalk types table
    from mortgage_data_manager.hud.config import CROSSWALK_TYPES

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
    """Entry point for standalone HUD CLI."""
    app()


if __name__ == "__main__":
    cli_main()
