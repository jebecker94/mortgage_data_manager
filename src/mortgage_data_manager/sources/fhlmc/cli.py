"""FHLMC CLI - Main entry point."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

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
    print_summary,
)
from mortgage_data_manager.core.cli.validation import version_callback_factory
from mortgage_data_manager.core.logging import configure_logging
from mortgage_data_manager.sources.fhlmc.config import (
    CRT_DATASETS,
    MAX_YEAR,
    MIN_YEAR,
    VALID_DATASETS,
    FHLMCConfig,
    validate_crt_datasets,
    validate_datasets,
    validate_years,
)
from mortgage_data_manager.sources.fhlmc.pipeline import (
    run_bronze,
    run_crt,
    run_pipeline,
    run_silver,
)

# Create main app
app = typer.Typer(
    name="fhlmc",
    help="FHLMC Data Manager - Download and process Freddie Mac mortgage data",
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=False,
)


_version_callback = version_callback_factory("FHLMC Data Manager", __version__)


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
    """[bold cyan]FHLMC Data Manager[/bold cyan] - Process Freddie Mac mortgage data.

    [yellow]Quick Start:[/yellow]

      # Inspect available schemas
      $ mortgage-data fhlmc schemas list

      # Load data to bronze layer
      $ mortgage-data fhlmc bronze -t origination --min-year 2023 --max-year 2024

      # Run complete pipeline
      $ mortgage-data fhlmc pipeline --min-year 2023

    [yellow]Available Commands:[/yellow]

      [cyan]download[/cyan]   - Download raw data files
      [cyan]schemas[/cyan]    - Inspect and validate data schemas
      [cyan]bronze[/cyan]     - Load data to bronze layer (raw -> Parquet)
      [cyan]silver[/cyan]     - Build cleaned, partitioned silver layer
      [cyan]pipeline[/cyan]   - Run multi-step workflows (end-to-end automation)

    Use [bold]fhlmc COMMAND --help[/bold] for more information on a command.
    """
    configure_logging(level="INFO")


# ============================================================================
# Download subapp
# ============================================================================


@app.command()
def download(
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """Instructions for downloading Freddie Mac Single-Family Loan-Level Dataset.

    [yellow]Manual Download Required[/yellow]

    Freddie Mac data requires free registration on Clarity Data Intelligence.
    Automated download is not supported due to Terms of Service restrictions.

    [bold cyan]Steps to Download:[/bold cyan]

    1. Visit the Freddie Mac data portal:
       https://www.freddiemac.com/research/datasets/sf-loanlevel-dataset

    2. Click to access Clarity Data Intelligence:
       https://capitalmarkets.freddiemac.com/clarity

    3. Register for a free account

    4. Navigate to SFLLD Data Download page

    5. Download files to: data/fhlmc/raw/

    [bold cyan]After Download:[/bold cyan]

    Run the import command to process files:
      $ mortgage-data fhlmc bronze

    [bold cyan]Documentation:[/bold cyan]

    See docs/manual_downloads.md for detailed instructions.
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    print_section_header("Freddie Mac Data Download Instructions")

    console.print()
    console.print("[yellow bold]Manual Download Required[/yellow bold]")
    console.print()
    console.print(
        "Freddie Mac Single-Family Loan-Level Dataset requires free registration "
        "on the Clarity Data Intelligence platform. Automated download is not "
        "supported due to Terms of Service restrictions."
    )
    console.print()

    console.print("[cyan bold]How to Download:[/cyan bold]")
    console.print()
    console.print("1. [bold]Visit the data portal:[/bold]")
    console.print(
        "   [link=https://www.freddiemac.com/research/datasets/sf-loanlevel-dataset]"
        "https://www.freddiemac.com/research/datasets/sf-loanlevel-dataset[/link]"
    )
    console.print()
    console.print("2. [bold]Register for Clarity Data Intelligence[/bold] (free account)")
    console.print(
        "   [link=https://capitalmarkets.freddiemac.com/clarity]"
        "https://capitalmarkets.freddiemac.com/clarity[/link]"
    )
    console.print()
    console.print("3. [bold]Navigate to SFLLD Data Download[/bold] and choose:")
    console.print("   - Full Dataset: All historical data")
    console.print("   - Standard Dataset by Year: Annual files")
    console.print("   - Non-Standard Dataset: ARMs, balloons, etc.")
    console.print("   - Sample Files: For testing")
    console.print()
    console.print("4. [bold]Place files in:[/bold] data/fhlmc/raw/")
    console.print()

    console.print("[cyan bold]Dataset Information:[/cyan bold]")
    console.print()
    console.print("  - Coverage: ~54.8 million mortgages (1999-2024)")
    console.print("  - Datasets: Standard, Non-Standard, RPL Mapping")
    console.print("  - Updates: Quarterly")
    console.print("  - Cost: Free for non-commercial/research use")
    console.print()

    console.print("[cyan bold]After Download:[/cyan bold]")
    console.print()
    console.print("  Process raw files to bronze layer:")
    console.print("  [green]$ mortgage-data fhlmc bronze[/green]")
    console.print()

    console.print("[cyan bold]Support:[/cyan bold]")
    console.print()
    console.print("  Technical issues: SF_LLD_Technology_Support@freddiemac.com")
    console.print()

    console.print("[dim]See docs/manual_downloads.md for complete instructions.[/dim]")


# ============================================================================
# Schemas subapp
# ============================================================================

schemas_app = typer.Typer(
    name="schemas",
    help="Inspect and validate FHLMC data schemas",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(schemas_app, name="schemas")


@schemas_app.command("list")
def list_schemas(
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """List all available schema types.

    Shows the datasets available in the layout CSV
    and the number of columns in each schema.

    [yellow]Example:[/yellow]

      $ mortgage-data fhlmc schemas list
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    from mortgage_data_manager.sources.fhlmc.schemas import crt_schemas, load_all_schemas

    print_section_header("Available FHLMC Schemas")

    try:
        schemas = load_all_schemas(FHLMCConfig.FHLMC_SCHEMA_FILE) | crt_schemas()
    except Exception as e:
        console.print(f"[red]Error loading schemas:[/red] {e}")
        raise typer.Exit(code=1)

    console.print(f"\n[cyan]Schema file:[/cyan] {FHLMCConfig.FHLMC_SCHEMA_FILE}")
    console.print(f"[cyan]Datasets found:[/cyan] {len(schemas)}\n")

    for dataset, schema in schemas.items():
        n_cols = len(schema["column_names"])
        n_dates = len(schema.get("date_columns", []))
        console.print(f"  [green]{dataset}[/green]: {n_cols} columns ({n_dates} date columns)")

    console.print("\n[dim]Use 'fhlmc schemas show <type>' for details[/dim]")


@schemas_app.command("show")
def show_schema(
    dataset: Annotated[
        str,
        typer.Argument(
            help="Dataset to show (origination, performance, reperforming, "
            "crt_lld, crt_agg_zipcode, crt_pre_harp)"
        ),
    ],
    columns: Annotated[int, typer.Option("--columns", "-n", help="Number of columns to show")] = 10,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Show schema details for a specific dataset.

    Displays column names, types, and date format information
    for the specified dataset.

    [yellow]Examples:[/yellow]

      $ mortgage-data fhlmc schemas show origination
      $ mortgage-data fhlmc schemas show performance -n 20
      $ mortgage-data fhlmc schemas show crt_lld -n 93

    [yellow]Valid datasets:[/yellow] origination, performance, reperforming,
    crt_lld, crt_agg_zipcode, crt_pre_harp
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    from mortgage_data_manager.sources.fhlmc.schemas import crt_schemas, load_all_schemas

    print_section_header(f"Schema: {dataset}")

    try:
        schemas = load_all_schemas(FHLMCConfig.FHLMC_SCHEMA_FILE) | crt_schemas()
    except Exception as e:
        console.print(f"[red]Error loading schemas:[/red] {e}")
        raise typer.Exit(code=1)

    schema = schemas.get(dataset)

    if not schema:
        console.print(f"[red]Schema not found for:[/red] {dataset}")
        console.print(f"[yellow]Available types:[/yellow] {', '.join(schemas.keys())}")
        raise typer.Exit(code=1)

    # Summary
    total_cols = len(schema["column_names"])
    n_dates = len(schema.get("date_columns", []))
    console.print(f"\n[cyan]Total columns:[/cyan] {total_cols}")
    console.print(f"[cyan]Date columns:[/cyan] {n_dates}\n")

    # Create table
    table = Table(title=f"{dataset} columns (first {min(columns, total_cols)})")
    table.add_column("#", style="dim", width=4)
    table.add_column("Column Name", style="cyan")
    table.add_column("Type", style="green")
    table.add_column("Date Format", style="yellow")

    column_types = schema.get("column_types", {})
    date_formats = schema.get("date_formats", {})

    for i, col_name in enumerate(schema["column_names"][:columns]):
        col_type = column_types.get(col_name, "Utf8")
        date_fmt = date_formats.get(col_name, "")
        table.add_row(str(i + 1), col_name, str(col_type), date_fmt)

    console.print(table)

    if total_cols > columns:
        console.print(
            f"\n[dim]Showing {columns} of {total_cols} columns. Use -n to show more.[/dim]"
        )


@schemas_app.command("dates")
def show_date_columns(
    dataset: Annotated[str, typer.Argument(help="Dataset to show date columns for")],
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Show all date columns and their formats.

    Lists all columns that are parsed as dates, along with
    their expected format strings.

    [yellow]Example:[/yellow]

      $ mortgage-data fhlmc schemas dates origination
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    from mortgage_data_manager.sources.fhlmc.schemas import crt_schemas, load_all_schemas

    print_section_header(f"Date Columns: {dataset}")

    try:
        schemas = load_all_schemas(FHLMCConfig.FHLMC_SCHEMA_FILE) | crt_schemas()
    except Exception as e:
        console.print(f"[red]Error loading schemas:[/red] {e}")
        raise typer.Exit(code=1)

    schema = schemas.get(dataset)

    if not schema:
        console.print(f"[red]Schema not found for:[/red] {dataset}")
        raise typer.Exit(code=1)

    date_columns = schema.get("date_columns", [])
    date_formats = schema.get("date_formats", {})

    if not date_columns:
        console.print("\n[yellow]No date columns found in this schema.[/yellow]")
        return

    table = Table(title=f"Date columns in {dataset}")
    table.add_column("#", style="dim", width=4)
    table.add_column("Column Name", style="cyan")
    table.add_column("Format", style="green")

    for i, col_name in enumerate(date_columns, 1):
        fmt = date_formats.get(col_name, "unknown")
        table.add_row(str(i), col_name, fmt)

    console.print(table)


# ============================================================================
# Bronze
# ============================================================================


@app.command()
def bronze(
    datasets: Annotated[
        list[str] | None,
        typer.Option(
            "--datasets",
            "-t",
            help=(
                f"Datasets to load: {', '.join(VALID_DATASETS)}, crt "
                "(repeatable; default the non-CRT datasets)"
            ),
        ),
    ] = None,
    min_year: Annotated[int, typer.Option("--min-year", help="First vintage year")] = MIN_YEAR,
    max_year: Annotated[int, typer.Option("--max-year", help="Last vintage year")] = MAX_YEAR,
    crt_parts: Annotated[
        list[str] | None,
        typer.Option(
            "--crt-parts",
            help=f"CRT disclosure parts when -t crt: {', '.join(CRT_DATASETS)} (default all)",
        ),
    ] = None,
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """Load raw FHLMC zips to bronze parquet.

    Datasets (default [cyan]origination[/cyan], [cyan]performance[/cyan],
    [cyan]reperforming[/cyan]):
      - the three standard SF Loan-Level Dataset families, filtered by vintage
        year via [cyan]--min-year[/cyan]/[cyan]--max-year[/cyan]
      - [cyan]crt[/cyan]: the Reference Pool (CRT) disclosure bundle. Its own
        parts axis is [cyan]--crt-parts[/cyan]; the year filter does not apply.

    [yellow]Examples:[/yellow]

      $ mortgage-data fhlmc bronze
      $ mortgage-data fhlmc bronze -t origination --min-year 2020 --max-year 2024
      $ mortgage-data fhlmc bronze -t crt --crt-parts lld --overwrite
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    selected = datasets or list(VALID_DATASETS)
    standard = [d for d in selected if d != "crt"]
    if standard:
        validate_datasets(standard)
    if crt_parts:
        validate_crt_datasets(crt_parts)

    if min_year > max_year:
        print_error(f"--min-year ({min_year}) must be <= --max-year ({max_year})")
        raise typer.Exit(code=1)
    year_list = list(range(min_year, max_year + 1))
    validate_years(year_list)

    print_section_header("FHLMC Bronze Import")
    console.print(f"[cyan]Datasets:[/cyan] {', '.join(selected)}")

    loaded = failed = 0
    if standard:
        console.print(f"[cyan]Years:[/cyan] {min_year}-{max_year}\n")
        results = run_bronze(datasets=standard, years=year_list, overwrite=overwrite)
        loaded += results.get("loaded", 0)
        failed += results.get("failed", 0)
    if "crt" in selected:
        from mortgage_data_manager.sources.fhlmc.import_bronze import import_crt_data

        crt_counts = import_crt_data(overwrite=overwrite, datasets=crt_parts or CRT_DATASETS)
        loaded += sum(crt_counts.values()) if isinstance(crt_counts, dict) else 0

    print_summary(
        "Bronze Import Complete",
        {"Loaded": loaded, "Failed": failed},
        style="green" if failed == 0 else "yellow",
    )
    if failed > 0:
        raise typer.Exit(code=1)


# ============================================================================
# Silver
# ============================================================================


@app.command()
def silver(
    datasets: Annotated[
        list[str] | None,
        typer.Option(
            "--datasets",
            "-t",
            help="Datasets to build: origination, performance, crt (repeatable; default all)",
        ),
    ] = None,
    min_year: Annotated[
        int | None, typer.Option("--min-year", help="First origination vintage (origination only)")
    ] = None,
    max_year: Annotated[
        int | None, typer.Option("--max-year", help="Last origination vintage (origination only)")
    ] = None,
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """Build the FHLMC silver layer from bronze parquet.

    Datasets (default all):
      - [cyan]origination[/cyan]: one row per Loan Sequence Number, hive-partitioned
        by vintage_year. Accepts [cyan]--min-year[/cyan]/[cyan]--max-year[/cyan].
      - [cyan]performance[/cyan]: the loan x month panel, one parquet per quarter
      - [cyan]crt[/cyan]: the at-issuance CRT reference-pool disclosure

    [cyan]reperforming[/cyan] has no silver builder and is ignored if passed.

    [yellow]Examples:[/yellow]

      $ mortgage-data fhlmc silver
      $ mortgage-data fhlmc silver -t origination --min-year 2020
      $ mortgage-data fhlmc silver -t crt --overwrite
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    if (min_year is None) != (max_year is None):
        print_error("--min-year and --max-year must be given together")
        raise typer.Exit(code=1)
    if min_year is not None and max_year is not None and min_year > max_year:
        print_error(f"--min-year ({min_year}) must be <= --max-year ({max_year})")
        raise typer.Exit(code=1)
    years = (
        list(range(min_year, max_year + 1))
        if min_year is not None and max_year is not None
        else None
    )

    print_section_header("FHLMC Silver Build")
    try:
        results = run_silver(datasets, years=years, overwrite=overwrite)
    except ValueError as e:
        print_error(str(e))
        raise typer.Exit(code=1)

    total = 0
    for dataset, counts in results.items():
        written = counts.get("processed", counts.get("written", 0))
        total += written
        console.print(f"  [cyan]{dataset}:[/cyan] {counts}")
    print_summary("Silver Build Complete", {"Datasets": len(results)}, style="green")


# ============================================================================
# Pipeline
# ============================================================================


@app.command()
def pipeline(
    datasets: Annotated[
        list[str] | None,
        typer.Option(
            "--datasets",
            "-t",
            help=f"Datasets to process: {', '.join(VALID_DATASETS)}, crt (default non-CRT)",
        ),
    ] = None,
    min_year: Annotated[int, typer.Option("--min-year", help="First vintage year")] = MIN_YEAR,
    max_year: Annotated[int, typer.Option("--max-year", help="Last vintage year")] = MAX_YEAR,
    crt_parts: Annotated[
        list[str] | None,
        typer.Option("--crt-parts", help=f"CRT parts when -t crt: {', '.join(CRT_DATASETS)}"),
    ] = None,
    skip_bronze: Annotated[
        bool, typer.Option("--skip-bronze", help="Skip bronze (use existing bronze parquet)")
    ] = False,
    skip_silver: Annotated[bool, typer.Option("--skip-silver", help="Skip silver")] = False,
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """Run the complete FHLMC pipeline (bronze -> silver).

    [yellow]Examples:[/yellow]

      $ mortgage-data fhlmc pipeline
      $ mortgage-data fhlmc pipeline --min-year 2019 --max-year 2025
      $ mortgage-data fhlmc pipeline --skip-silver        # bronze only
      $ mortgage-data fhlmc pipeline -t crt               # the CRT bundle
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    print_section_header("Complete FHLMC Pipeline")

    selected = datasets or list(VALID_DATASETS)
    standard = [d for d in selected if d != "crt"]
    if standard:
        validate_datasets(standard)
    if crt_parts:
        validate_crt_datasets(crt_parts)
    if min_year > max_year:
        print_error(f"--min-year ({min_year}) must be <= --max-year ({max_year})")
        raise typer.Exit(code=1)
    year_list = list(range(min_year, max_year + 1))
    validate_years(year_list)

    console.print(f"[cyan]Datasets:[/cyan] {', '.join(selected)}")
    console.print(f"[cyan]Years:[/cyan] {min_year}-{max_year}")
    console.print(f"[cyan]Overwrite:[/cyan] {overwrite}\n")

    if "crt" in selected:
        results = run_crt(overwrite=overwrite, datasets=crt_parts)
        print_summary(
            "CRT Pipeline Complete",
            {"Bronze": results.get("bronze", {}), "Silver": results.get("silver", {})},
            style="green",
        )
        if not standard:
            return

    results = run_pipeline(
        datasets=standard,
        years=year_list,
        overwrite=overwrite,
        skip_bronze=skip_bronze,
        skip_silver=skip_silver,
    )
    bronze_results = results.get("bronze", {})
    print_summary(
        "Pipeline Complete",
        {
            "Bronze Loaded": bronze_results.get("loaded", 0),
            "Bronze Failed": bronze_results.get("failed", 0),
            "Silver Datasets": len(results.get("silver", {})),
        },
        style="green" if bronze_results.get("failed", 0) == 0 else "yellow",
    )
    if bronze_results.get("failed", 0) > 0:
        raise typer.Exit(code=1)


# ============================================================================
# Top-level commands
# ============================================================================


@app.command()
def info(
    bronze_files: Annotated[
        bool,
        typer.Option("--bronze", help="Also list the parquet files present in each bronze dataset"),
    ] = False,
) -> None:
    """Display FHLMC configuration information.

    Shows the current configuration paths and settings for FHLMC data. Pass
    [cyan]--bronze[/cyan] to also list the parquet files present in each bronze
    dataset directory.

    Examples:
      mortgage-data fhlmc info
      mortgage-data fhlmc info --bronze
    """
    print_info_table(
        "FHLMC Configuration",
        {
            "Data Directory": FHLMCConfig.FHLMC_DATA_DIR,
            "Raw Directory": FHLMCConfig.FHLMC_RAW_DIR,
            "Bronze Directory": FHLMCConfig.FHLMC_BRONZE_DIR,
            "Silver Directory": FHLMCConfig.FHLMC_SILVER_DIR,
            "Gold Directory": FHLMCConfig.FHLMC_GOLD_DIR,
            "Schemas Directory": FHLMCConfig.FHLMC_SCHEMAS_DIR,
        },
    )

    # Show available commands
    console.print("\n[bold cyan]Available Commands:[/bold cyan]")
    console.print("  download    - Download raw data files")
    console.print("  schemas     - Inspect and validate data schemas")
    console.print("  bronze      - Load data to bronze layer")
    console.print("  silver      - Build silver layer from bronze")
    console.print("  pipeline    - Run bronze + silver in one step")
    console.print("  info        - Show this configuration information")

    if not bronze_files:
        return

    print_section_header("Bronze Layer Status")
    bronze_root = FHLMCConfig.FHLMC_BRONZE_DIR
    console.print(f"[cyan]Bronze directory:[/cyan] {bronze_root}\n")
    if not bronze_root.exists():
        console.print("[yellow]Bronze directory does not exist yet.[/yellow]")
        return

    for dataset in VALID_DATASETS:
        dataset_dir = bronze_root / dataset
        parquet_files = sorted(dataset_dir.glob("*.parquet")) if dataset_dir.exists() else []
        if not parquet_files:
            console.print(f"[yellow]{dataset}: no parquet files[/yellow]")
            continue
        table = Table(title=f"{dataset} files")
        table.add_column("#", style="dim", width=4)
        table.add_column("File", style="cyan")
        table.add_column("Size", style="green", justify="right")
        for i, file in enumerate(parquet_files, 1):
            size = file.stat().st_size
            if size > 1024 * 1024:
                size_str = f"{size / (1024 * 1024):.1f} MB"
            elif size > 1024:
                size_str = f"{size / 1024:.1f} KB"
            else:
                size_str = f"{size} B"
            table.add_row(str(i), file.name, size_str)
        console.print(table)


def cli_main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    cli_main()
