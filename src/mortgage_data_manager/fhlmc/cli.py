"""FHLMC CLI - Main entry point."""

from __future__ import annotations

from pathlib import Path
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
from mortgage_data_manager.fhlmc.config import (
    VALID_DATASETS,
    FHLMCConfig,
    validate_datasets,
    validate_years,
)
from mortgage_data_manager.fhlmc.import_silver import (
    import_origination_to_silver,
    import_performance_to_silver,
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
      $ mortgage-data fhlmc bronze load -t origination -y 2023 -y 2024

      # Run complete pipeline
      $ mortgage-data fhlmc pipeline full --min-year 2023

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

download_app = typer.Typer(
    name="download",
    help="Download FHLMC data files (requires manual registration)",
    no_args_is_help=True,
)
app.add_typer(download_app, name="download")


@download_app.command("data")
def download_data(
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
      $ mortgage-data fhlmc bronze load

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
    console.print("  [green]$ mortgage-data fhlmc bronze load[/green]")
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

    Shows the datasets available in the Excel schema dictionary
    and the number of columns in each schema.

    [yellow]Example:[/yellow]

      $ mortgage-data fhlmc schemas list
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    from mortgage_data_manager.fhlmc.schemas import load_all_schemas

    print_section_header("Available FHLMC Schemas")

    try:
        schemas = load_all_schemas(FHLMCConfig.FHLMC_SCHEMA_FILE)
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
        str, typer.Argument(help="Dataset to show (origination, performance, reperforming)")
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
      $ mortgage-data fhlmc schemas show reperforming --columns 50

    [yellow]Valid datasets:[/yellow] origination, performance, reperforming
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    from mortgage_data_manager.fhlmc.schemas import load_all_schemas

    validate_datasets([dataset])

    print_section_header(f"Schema: {dataset}")

    try:
        schemas = load_all_schemas(FHLMCConfig.FHLMC_SCHEMA_FILE)
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

    from mortgage_data_manager.fhlmc.schemas import load_all_schemas

    validate_datasets([dataset])

    print_section_header(f"Date Columns: {dataset}")

    try:
        schemas = load_all_schemas(FHLMCConfig.FHLMC_SCHEMA_FILE)
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
# Bronze subapp
# ============================================================================

bronze_app = typer.Typer(
    name="bronze",
    help="Load data to bronze layer (raw -> Parquet)",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(bronze_app, name="bronze")


@bronze_app.command("load")
def load_bronze(
    datasets: Annotated[
        list[str],
        typer.Option(
            "--datasets", "-t", help="Datasets to load (origination, performance, reperforming)"
        ),
    ],
    min_year: Annotated[int, typer.Option("--min-year", help="First year to load (e.g., 2023)")],
    max_year: Annotated[
        int, typer.Option("--max-year", help="Last year to load, inclusive (e.g., 2024)")
    ],
    raw_dir: Annotated[Path | None, typer.Option("--raw-dir", help="Raw data directory")] = None,
    output_dir: Annotated[
        Path | None, typer.Option("--output-dir", "-o", help="Bronze output directory")
    ] = None,
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Load raw data files to bronze layer.

    Reads raw data from zip files, parses using Excel schema,
    and saves as Parquet files in the bronze layer.

    [bold]Data Structure:[/bold]

      - [cyan]origination[/cyan]: Loan origination data (pipe-delimited, no headers)
      - [cyan]performance[/cyan]: Monthly performance data (pipe-delimited, no headers)
      - [cyan]reperforming[/cyan]: RPL matching data (CSV with headers)

    [yellow]Examples:[/yellow]

      # Load origination data for 2023-2024
      $ mortgage-data fhlmc bronze load -t origination --min-year 2023 --max-year 2024

      # Load all datasets for a single year
      $ mortgage-data fhlmc bronze load -t origination -t performance -t reperforming --min-year 2024 --max-year 2024

      # Load with custom directories
      $ mortgage-data fhlmc bronze load -t origination --min-year 2024 --max-year 2024 --raw-dir /data/raw --output-dir /data/bronze

    [yellow]Valid datasets:[/yellow] origination, performance, reperforming
    [yellow]Valid years:[/yellow] 2019-2025
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    from mortgage_data_manager.fhlmc.pipeline import run_bronze

    print_section_header("Load Data to Bronze Layer")

    if min_year > max_year:
        print_error(f"--min-year ({min_year}) must be <= --max-year ({max_year})")
        raise typer.Exit(code=1)

    years = list(range(min_year, max_year + 1))

    # Validate inputs
    validate_datasets(datasets)
    validate_years(years)

    # Resolve paths
    raw = raw_dir.resolve() if raw_dir else FHLMCConfig.FHLMC_RAW_DIR
    output = output_dir.resolve() if output_dir else FHLMCConfig.FHLMC_BRONZE_DIR
    output.mkdir(parents=True, exist_ok=True)

    console.print(f"[cyan]Datasets:[/cyan] {', '.join(datasets)}")
    console.print(f"[cyan]Years:[/cyan] {min_year}-{max_year}")
    console.print(f"[cyan]Raw directory:[/cyan] {raw}")
    console.print(f"[cyan]Output directory:[/cyan] {output}\n")

    results = run_bronze(
        datasets=datasets, years=years, overwrite=overwrite, raw_dir=raw, bronze_dir=output
    )

    # Summary
    print_summary(
        "Bronze Loading Complete",
        {
            "Loaded": results["loaded"],
            "Failed": results["failed"],
            "Datasets": ", ".join(results["datasets_processed"]) or "none",
        },
        style="green" if results["failed"] == 0 else "yellow",
    )

    if results["failed"] > 0:
        raise typer.Exit(code=1)


@bronze_app.command("status")
def bronze_status(
    output_dir: Annotated[
        Path | None, typer.Option("--output-dir", "-o", help="Bronze directory to check")
    ] = None,
):
    """Show status of bronze layer files.

    Lists existing parquet files in each dataset subdirectory.

    [yellow]Example:[/yellow]

      $ mortgage-data fhlmc bronze status
    """
    print_section_header("Bronze Layer Status")

    output = output_dir.resolve() if output_dir else FHLMCConfig.FHLMC_BRONZE_DIR
    console.print(f"[cyan]Bronze directory:[/cyan] {output}\n")

    if not output.exists():
        console.print("[yellow]Bronze directory does not exist yet.[/yellow]")
        return

    for dataset in VALID_DATASETS:
        type_dir = output / dataset
        if type_dir.exists():
            parquet_files = sorted(type_dir.glob("*.parquet"))
            if parquet_files:
                table = Table(title=f"{dataset} files")
                table.add_column("#", style="dim", width=4)
                table.add_column("File", style="cyan")
                table.add_column("Size", style="green", justify="right")
                for i, file in enumerate(parquet_files, 1):
                    if file.exists():
                        size = file.stat().st_size
                        if size > 1024 * 1024:
                            size_str = f"{size / (1024 * 1024):.1f} MB"
                        elif size > 1024:
                            size_str = f"{size / 1024:.1f} KB"
                        else:
                            size_str = f"{size} B"
                    else:
                        size_str = "-"
                    table.add_row(str(i), file.name, size_str)
                console.print(table)
            else:
                console.print(f"[dim]{dataset}: no files[/dim]")
        else:
            console.print(f"[dim]{dataset}: directory not created[/dim]")
        console.print()


# ============================================================================
# Silver subapp
# ============================================================================

silver_app = typer.Typer(
    name="silver",
    help="Build silver layer (cleaned, partitioned, analysis-ready)",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(silver_app, name="silver")


@silver_app.command("origination")
def build_origination(
    min_year: Annotated[
        int | None,
        typer.Option("--min-year", help="First vintage year to build (default: all available)"),
    ] = None,
    max_year: Annotated[
        int | None,
        typer.Option("--max-year", help="Last vintage year to build, inclusive"),
    ] = None,
    bronze_dir: Annotated[
        Path | None,
        typer.Option("--bronze-dir", help="Bronze origination directory"),
    ] = None,
    silver_dir: Annotated[
        Path | None,
        typer.Option("--silver-dir", "-o", help="Silver origination output directory"),
    ] = None,
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Build the silver origination layer from bronze.

    Applies sentinel-to-null cleanup, adds categorical label columns, and
    derives vintage_year / vintage_quarter from the loan sequence number.
    Writes one parquet per vintage year, hive-partitioned as
    [cyan]silver/origination/vintage_year=YYYY/data.parquet[/cyan].

    [yellow]Examples:[/yellow]

      # Build all vintages present in bronze
      $ mortgage-data fhlmc silver origination

      # Build a year range
      $ mortgage-data fhlmc silver origination --min-year 2018 --max-year 2024

      # Force a rebuild
      $ mortgage-data fhlmc silver origination --min-year 2024 --max-year 2024 --overwrite
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    print_section_header("Build FHLMC Silver Origination")

    if min_year is not None and max_year is not None:
        if min_year > max_year:
            console.print(
                f"[red]Error:[/red] --min-year ({min_year}) must be <= --max-year ({max_year})"
            )
            raise typer.Exit(code=1)
        years: list[int] | None = list(range(min_year, max_year + 1))
        validate_years(years)
    elif min_year is not None or max_year is not None:
        console.print(
            "[red]Error:[/red] --min-year and --max-year must be provided together "
            "(or both omitted to build all available years)"
        )
        raise typer.Exit(code=1)
    else:
        years = None  # discover from bronze

    bronze = bronze_dir.resolve() if bronze_dir else FHLMCConfig.FHLMC_BRONZE_ORIGINATION
    silver = silver_dir.resolve() if silver_dir else (FHLMCConfig.FHLMC_SILVER_DIR / "origination")

    console.print(f"[cyan]Bronze directory:[/cyan] {bronze}")
    console.print(f"[cyan]Silver directory:[/cyan] {silver}")
    if years is not None:
        console.print(f"[cyan]Years:[/cyan] {years[0]}-{years[-1]}")
    else:
        console.print("[cyan]Years:[/cyan] all available in bronze")
    console.print()

    counts = import_origination_to_silver(
        years=years,
        bronze_dir=bronze,
        silver_dir=silver,
        overwrite=overwrite,
    )

    print_summary(
        "Silver Origination Complete",
        {
            "Processed": counts["processed"],
            "Skipped (already exists)": counts["skipped"],
            "Failed": counts["failed"],
        },
        style="green" if counts["failed"] == 0 else "yellow",
    )

    if counts["failed"] > 0:
        raise typer.Exit(code=1)


@silver_app.command("performance")
def build_performance(
    bronze_dir: Annotated[
        Path | None,
        typer.Option("--bronze-dir", help="Bronze performance directory"),
    ] = None,
    silver_dir: Annotated[
        Path | None,
        typer.Option("--silver-dir", "-o", help="Silver performance output directory"),
    ] = None,
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Build the silver loan-performance panel (loan x month) from bronze.

    Passes each [cyan]historical_data_time_YYYYQn[/cyan] quarterly file through to
    silver (the bronze importer already types the date columns), one parquet per
    origination quarter. This is the GSE SF-credit input to the combined
    [cyan]loan_performance[/cyan] master.

    [yellow]Examples:[/yellow]

      # Build all quarters present in bronze
      $ mortgage-data fhlmc silver performance

      # Force a rebuild
      $ mortgage-data fhlmc silver performance --overwrite
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    print_section_header("Build FHLMC Silver Performance")

    bronze = bronze_dir.resolve() if bronze_dir else FHLMCConfig.FHLMC_BRONZE_PERFORMANCE
    silver = silver_dir.resolve() if silver_dir else (FHLMCConfig.FHLMC_SILVER_DIR / "performance")

    console.print(f"[cyan]Bronze directory:[/cyan] {bronze}")
    console.print(f"[cyan]Silver directory:[/cyan] {silver}")
    console.print()

    counts = import_performance_to_silver(
        bronze_dir=bronze,
        silver_dir=silver,
        overwrite=overwrite,
    )

    print_summary(
        "Silver Performance Complete",
        {
            "Processed": counts["processed"],
            "Skipped (already exists)": counts["skipped"],
            "Total rows": f"{counts['total_rows']:,}",
        },
        style="green",
    )


# ============================================================================
# Pipeline subapp
# ============================================================================

pipeline_app = typer.Typer(
    name="pipeline",
    help="Run multi-step workflows (end-to-end automation)",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
app.add_typer(pipeline_app, name="pipeline")


@pipeline_app.command("full")
def full_pipeline(
    datasets: Annotated[
        list[str] | None,
        typer.Option("--datasets", "-t", help="Datasets to process (default: all)"),
    ] = None,
    min_year: Annotated[
        int, typer.Option("--min-year", help="First year to process (e.g., 2019)")
    ] = 2019,
    max_year: Annotated[
        int, typer.Option("--max-year", help="Last year to process, inclusive")
    ] = 2025,
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Run complete end-to-end pipeline.

    Executes all steps in sequence:

      1. Load schemas from Excel dictionary
      2. Load historical data to bronze layer
      3. Load RPL data to bronze layer (if requested)
      4. (Future) Transform to silver layer

    [yellow]Examples:[/yellow]

      # Process all datasets for 2024
      $ mortgage-data fhlmc pipeline full --min-year 2024 --max-year 2024

      # Process specific datasets and years
      $ mortgage-data fhlmc pipeline full -t origination -t performance --min-year 2023 --max-year 2024

      # Process full historical range
      $ mortgage-data fhlmc pipeline full --min-year 2019 --max-year 2025

    [yellow]Valid datasets:[/yellow] origination, performance, reperforming
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    from mortgage_data_manager.fhlmc.pipeline import run_pipeline

    print_section_header("Complete FHLMC Pipeline")

    if min_year > max_year:
        print_error(f"--min-year ({min_year}) must be <= --max-year ({max_year})")
        raise typer.Exit(code=1)

    year_list = list(range(min_year, max_year + 1))

    # Determine datasets
    if datasets:
        validate_datasets(datasets)
        dt_list = datasets
    else:
        dt_list = VALID_DATASETS.copy()

    validate_years(year_list)

    console.print(f"[cyan]Datasets:[/cyan] {', '.join(dt_list)}")
    console.print(f"[cyan]Years:[/cyan] {min_year}-{max_year}")
    console.print(f"[cyan]Overwrite:[/cyan] {overwrite}\n")

    results = run_pipeline(datasets=dt_list, years=year_list, overwrite=overwrite)

    bronze_results = results.get("bronze", {})

    print_summary(
        "Pipeline Complete",
        {
            "Bronze Loaded": bronze_results.get("loaded", 0),
            "Bronze Failed": bronze_results.get("failed", 0),
            "Datasets Processed": ", ".join(bronze_results.get("datasets_processed", [])) or "none",
        },
        style="green" if bronze_results.get("failed", 0) == 0 else "yellow",
    )

    if bronze_results.get("failed", 0) > 0:
        raise typer.Exit(code=1)


@pipeline_app.command("bronze-only")
def bronze_only_pipeline(
    datasets: Annotated[
        list[str] | None,
        typer.Option("--datasets", "-t", help="Datasets to process (default: all)"),
    ] = None,
    min_year: Annotated[
        int, typer.Option("--min-year", help="First year to process (e.g., 2019)")
    ] = 2019,
    max_year: Annotated[
        int, typer.Option("--max-year", help="Last year to process, inclusive")
    ] = 2025,
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Run bronze layer pipeline only.

    Loads raw data to bronze layer without silver transformation.

    [yellow]Example:[/yellow]

      $ mortgage-data fhlmc pipeline bronze-only --min-year 2023 --max-year 2025
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    from mortgage_data_manager.fhlmc.pipeline import run_bronze

    print_section_header("Bronze Layer Pipeline")

    if min_year > max_year:
        print_error(f"--min-year ({min_year}) must be <= --max-year ({max_year})")
        raise typer.Exit(code=1)

    year_list = list(range(min_year, max_year + 1))

    if datasets:
        validate_datasets(datasets)
        dt_list = datasets
    else:
        dt_list = VALID_DATASETS.copy()

    validate_years(year_list)

    console.print(f"[cyan]Datasets:[/cyan] {', '.join(dt_list)}")
    console.print(f"[cyan]Years:[/cyan] {min_year}-{max_year}\n")

    results = run_bronze(datasets=dt_list, years=year_list, overwrite=overwrite)

    print_summary(
        "Bronze Pipeline Complete",
        {
            "Loaded": results["loaded"],
            "Failed": results["failed"],
            "Datasets": ", ".join(results["datasets_processed"]) or "none",
        },
        style="green" if results["failed"] == 0 else "yellow",
    )


# ============================================================================
# Top-level commands
# ============================================================================


@app.command()
def info() -> None:
    """Display FHLMC configuration information.

    Shows the current configuration paths and settings for FHLMC data.

    Examples:
      mortgage-data fhlmc info
      fhlmc info
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
    console.print("  pipeline    - Run multi-step workflows")
    console.print("  info        - Show this configuration information")


def cli_main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    cli_main()
