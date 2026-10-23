"""FHFA CLI - Main entry point.

Typer-based command-line interface for FHFA data management.
Surfaces download, schemas, bronze, silver, and pipeline subcommand groups.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

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
from mortgage_data_manager.core.cli.formatting import (
    console,
    print_error,
    print_info_table,
    print_section_header,
    print_success,
    print_summary,
)
from mortgage_data_manager.core.cli.progress import create_progress, create_spinner
from mortgage_data_manager.core.cli.validation import version_callback_factory
from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.sources.fhfa.config import (
    FIRST_EXCEL_DICTIONARY_YEAR,
    LAST_FIXED_WIDTH_YEAR,
    LATEST_DATA_YEAR,
    VALID_DATASETS,
    FHFAConfig,
    validate_datasets,
    validate_years,
)
from mortgage_data_manager.sources.fhfa.download import (
    download_fhfa_data,
    download_fhfa_dictionaries,
    download_hud_gse_archive,
)
from mortgage_data_manager.sources.fhfa.import_bronze import (
    HUD_ERA_MAX_YEAR,
    HUD_ERA_MIN_YEAR,
    save_to_bronze,
)
from mortgage_data_manager.sources.fhfa.import_silver import save_to_silver
from mortgage_data_manager.sources.fhfa.pipeline import (
    run_data_pipeline,
    run_hud_era_backfill,
    run_pipeline,
    run_schemas,
)
from mortgage_data_manager.sources.fhfa.schemas import (
    build_all_master_dictionaries,
    convert_excel_dictionary,
    excel_dictionary_path,
    extract_dictionary_tables_for_year,
    extract_enterprise_pudb_pdfs,
    get_available_years,
)

logger = get_logger(__name__)


# ============================================================================
# Root app
# ============================================================================

app = typer.Typer(
    name="fhfa",
    help="FHFA Data Manager - Download and process FHFA enterprise data",
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=False,
)


#: Year defaults, split so the schema masters and a data build can differ. The
#: masters describe the fixed-width layouts only, so they stop at the last
#: fixed-width year; CSV-era years read their per-year dictionary.
DEFAULT_DATA_YEAR = LATEST_DATA_YEAR
SCHEMA_MIN_YEAR = 2008
SCHEMA_MAX_YEAR = LAST_FIXED_WIDTH_YEAR

_version_callback = version_callback_factory("FHFA Data Manager", __version__)


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
    """[bold cyan]FHFA Data Manager[/bold cyan] - Process enterprise PUDB data.

    [yellow]Quick Start:[/yellow]

      # Download all data and dictionaries
      $ fhfa download

      # Process data dictionaries
      $ fhfa schemas pipeline

      # Load data to bronze layer
      $ fhfa bronze -t sf_c --min-year 2023 --max-year 2024

      # Transform to silver layer
      $ fhfa silver -t sf_c --min-year 2023 --max-year 2024

      # Run complete pipeline
      $ mortgage-data fhfa pipeline --min-year 2020 --max-year 2024

    [yellow]Available Commands:[/yellow]

      [cyan]download[/cyan]   - Download raw data files and dictionaries
      [cyan]schemas[/cyan]    - Process data dictionaries (PDFs, Excel, master building)
      [cyan]bronze[/cyan]     - Load data to bronze layer (fixed-width → Parquet)
      [cyan]silver[/cyan]     - Transform data to silver layer (standardization)
      [cyan]pipeline[/cyan]   - Run multi-step workflows (end-to-end automation)

    Use [bold]fhfa COMMAND --help[/bold] for more information on a command.
    """
    pass


# ============================================================================
# Download
# ============================================================================


@app.command()
def download(
    datasets: Annotated[
        list[str] | None,
        typer.Option(
            "--datasets",
            "-t",
            help="What to fetch: data, dictionaries (repeatable; default both). "
            "Ignored when --era hud.",
        ),
    ] = None,
    era: Annotated[
        str,
        typer.Option(
            "--era",
            help="Source era: 'current' (FHFA PUDB) or 'hud' (pre-FHFA GSE PUDB archive, 1993-2007)",
        ),
    ] = "current",
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", "-o", help="Output directory (single dataset only)"),
    ] = None,
    min_year: Annotated[
        int, typer.Option("--min-year", help="First HUD-era year to fetch (--era hud only)")
    ] = HUD_ERA_MIN_YEAR,
    max_year: Annotated[
        int, typer.Option("--max-year", help="Last HUD-era year to fetch (--era hud only)")
    ] = HUD_ERA_MAX_YEAR,
    overwrite: OverwriteOption = False,
    pause: PauseOption = None,
    timeout: TimeoutOption = None,
    retries: RetriesOption = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Download FHFA data files and dictionaries.

    Datasets (default both, [cyan]--era current[/cyan]):
      - [cyan]data[/cyan]: the FHFA Public Use Database files
      - [cyan]dictionaries[/cyan]: the accompanying data dictionaries

    [cyan]--era hud[/cyan] instead fetches the pre-FHFA GSE Public Use Database
    multifamily archive (1993-2007) from HUD USER into [cyan]raw/hud_era[/cyan].
    Archive zips spanning several years are fetched whole when any covered year
    is in range, so [cyan]--min-year[/cyan]/[cyan]--max-year[/cyan] select archives,
    not rows.

    [yellow]Examples:[/yellow]

      $ mortgage-data fhfa download
      $ mortgage-data fhfa download -t data -o /custom/path
      $ mortgage-data fhfa download --era hud
      $ mortgage-data fhfa download --era hud --min-year 1996 --max-year 1998
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    if era not in ("current", "hud"):
        print_error("--era must be one of: current, hud")
        raise typer.Exit(code=1)

    if era == "hud":
        print_section_header("Download HUD-era GSE Archive (1993-2007)")
        if min_year > max_year:
            print_error(f"--min-year ({min_year}) must be <= --max-year ({max_year})")
            raise typer.Exit(code=1)
        results = download_hud_gse_archive(
            years=list(range(min_year, max_year + 1)),
            output_dir=output_dir,
            overwrite=overwrite,
            pause=pause,
            timeout=timeout,
            retries=retries,
        )
        report_download_results(results)
        return

    valid = ["data", "dictionaries"]
    selected = datasets or valid
    unknown = [d for d in selected if d not in valid]
    if unknown:
        print_error(f"Unknown FHFA download datasets: {unknown}; valid: {valid}")
        raise typer.Exit(code=1)
    if output_dir is not None and len(selected) > 1:
        print_error(
            f"--output-dir applies to a single dataset, but {len(selected)} were selected "
            f"({', '.join(selected)}); pass --datasets with exactly one."
        )
        raise typer.Exit(code=1)

    print_section_header("Download FHFA Files")
    console.print(f"[cyan]Datasets:[/cyan] {', '.join(selected)}\n")

    downloaders = {"data": download_fhfa_data, "dictionaries": download_fhfa_dictionaries}
    all_results = []
    for dataset in selected:
        all_results += downloaders[dataset](
            output_dir=output_dir,
            overwrite=overwrite,
            pause=pause,
            timeout=timeout,
            retries=retries,
            show_progress=not quiet,
        )
    report_download_results(all_results)


# ============================================================================
# Schemas subapp
# ============================================================================

schemas_app = typer.Typer(
    name="schemas",
    help="Process data dictionaries (PDFs, Excel, master building)",
    no_args_is_help=True,
)
app.add_typer(schemas_app, name="schemas")


@schemas_app.command("extract")
def extract_pdfs(
    raw_dir: Annotated[
        Path | None, typer.Option("--raw-dir", help="Raw data directory (default: data/fhfa/raw)")
    ] = None,
    dict_dir: Annotated[
        Path | None, typer.Option("--dict-dir", help="Dictionary output directory")
    ] = None,
):
    """Extract PDFs from enterprise PUDB zip files.

    Searches for *_enterprise_pudb.zip files in the raw directory
    and extracts data dictionary PDFs.

    [yellow]Example:[/yellow]

      $ fhfa schemas extract
    """
    print_section_header("Extract Dictionary PDFs")

    raw = (raw_dir or FHFAConfig.FHFA_RAW_DIR).resolve()
    dict_root = (dict_dir or FHFAConfig.FHFA_SCHEMAS_DIR).resolve()

    console.print(f"[cyan]Raw directory:[/cyan] {raw}")
    console.print(f"[cyan]Dictionary directory:[/cyan] {dict_root}\n")

    with create_spinner("Extracting PDFs from zip files..."):
        pdfs = extract_enterprise_pudb_pdfs(raw_dir=raw, dictionary_root=dict_root)

    print_success(f"Extracted {len(pdfs)} PDF files")
    for pdf in pdfs[:5]:  # Show first 5
        console.print(f"  • {pdf.name}")
    if len(pdfs) > 5:
        console.print(f"  [dim]... and {len(pdfs) - 5} more[/dim]")


@schemas_app.command("convert")
def convert_pdfs(
    min_year: Annotated[
        int | None,
        typer.Option("--min-year", help="First year to convert (default: all available)"),
    ] = None,
    max_year: Annotated[
        int | None,
        typer.Option("--max-year", help="Last year to convert, inclusive (default: all available)"),
    ] = None,
    dict_dir: Annotated[
        Path | None, typer.Option("--dict-dir", help="Dictionary directory")
    ] = None,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Overwrite existing CSV files")
    ] = False,
):
    """Convert PDFs/Excel to CSV/Parquet format.

    Converts dictionary PDFs (years before 2024) and the Excel
    dictionaries (2024 on) to CSV format for easier processing.

    [yellow]Examples:[/yellow]

      # Convert all years
      $ fhfa schemas convert

      # Convert specific years
      $ fhfa schemas convert --min-year 2024 --max-year 2025
    """
    from mortgage_data_manager.sources.fhfa.config import PROJECT_DIR

    print_section_header("Convert Dictionaries to CSV")

    if min_year is not None and max_year is not None and min_year > max_year:
        console.print(
            f"[red]Error:[/red] --min-year ({min_year}) must be <= --max-year ({max_year})"
        )
        raise typer.Exit(code=1)

    dict_root = (dict_dir or FHFAConfig.FHFA_SCHEMAS_DIR).resolve()
    fhfa_dict_root = dict_root / "fhfa"

    console.print(f"[cyan]Dictionary directory:[/cyan] {dict_root}\n")

    # Determine years to convert
    if min_year is not None or max_year is not None:
        lo = min_year if min_year is not None else 2008
        hi = max_year if max_year is not None else LATEST_DATA_YEAR
        years_to_convert = [y for y in range(lo, hi + 1) if y < FIRST_EXCEL_DICTIONARY_YEAR]
        excel_years = [y for y in range(lo, hi + 1) if y >= FIRST_EXCEL_DICTIONARY_YEAR]
    else:
        # Find all available years
        if fhfa_dict_root.exists():
            year_dirs = [d for d in fhfa_dict_root.iterdir() if d.is_dir() and d.name.isdigit()]
            years_to_convert = sorted(
                int(d.name) for d in year_dirs if int(d.name) < FIRST_EXCEL_DICTIONARY_YEAR
            )
        else:
            years_to_convert = []
        excel_years = list(range(FIRST_EXCEL_DICTIONARY_YEAR, LATEST_DATA_YEAR + 1))

    success_count = 0
    fail_count = 0

    # Convert PDF dictionaries
    if years_to_convert:
        console.print(f"[cyan]Converting {len(years_to_convert)} PDF dictionaries...[/cyan]")
        for year in years_to_convert:
            try:
                extract_dictionary_tables_for_year(
                    year=year,
                    paths_project_dir=PROJECT_DIR,
                    overwrite=overwrite,
                    dictionary_root=dict_root,
                    output_formats=("csv",),
                )
                success_count += 1
                console.print(f"  [green]✓[/green] {year}")
            except Exception as e:
                fail_count += 1
                console.print(f"  [red]✗[/red] {year}: {str(e)[:50]}")

    # Convert Excel dictionaries
    for year in excel_years:
        console.print(f"\n[cyan]Converting {year} Excel dictionary...[/cyan]")
        excel_path = excel_dictionary_path(year, dict_root)
        if excel_path.exists():
            try:
                results = convert_excel_dictionary(
                    excel_path,
                    year=year,
                    output_dir=dict_root / "fhfa" / str(year),
                    overwrite=overwrite,
                )
                success_count += 1
                print_success(f"Converted {len(results)} sheets from {year} Excel file")
            except Exception as e:
                fail_count += 1
                console.print(f"[red]Failed: {str(e)}[/red]")
        else:
            console.print(f"[yellow]Excel file not found: {excel_path.name}[/yellow]")

    # Summary
    console.print("\n[bold]Summary:[/bold]")
    console.print(f"  [green]✓ Converted:[/green] {success_count} year(s)")
    if fail_count > 0:
        console.print(f"  [red]✗ Failed:[/red] {fail_count} year(s)")


@schemas_app.command("build")
def build_masters(
    min_year: Annotated[int, typer.Option("--min-year", help="Minimum year to include")] = 2008,
    max_year: Annotated[
        int, typer.Option("--max-year", help="Maximum year to include")
    ] = SCHEMA_MAX_YEAR,
    dict_dir: Annotated[
        Path | None, typer.Option("--dict-dir", help="Dictionary directory")
    ] = None,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Overwrite existing master files")
    ] = False,
):
    """Build master dictionaries combining all years.

    Combines year-specific dictionaries into master files that span
    multiple years. Master dictionaries are saved as:
    dictionary_files/fhfa/masters/master_{dataset}.csv

    [yellow]Examples:[/yellow]

      # Build masters for all years (2008-2024)
      $ fhfa schemas build

      # Build masters for recent years only
      $ fhfa schemas build --min-year 2020 --max-year 2024

      # Rebuild existing masters
      $ fhfa schemas build --overwrite
    """
    print_section_header("Build Master Dictionaries")

    dict_root = (dict_dir or FHFAConfig.FHFA_SCHEMAS_DIR).resolve()

    console.print(f"[cyan]Year range:[/cyan] {min_year}-{max_year}")
    console.print(f"[cyan]Dictionary directory:[/cyan] {dict_root}\n")

    with create_spinner("Building master dictionaries..."):
        masters = build_all_master_dictionaries(
            min_year=min_year, max_year=max_year, dictionary_root=dict_root, overwrite=overwrite
        )

    print_success(f"Built {len(masters)} master dictionaries")

    # Show details for each master
    console.print("\n[bold]Master Dictionaries:[/bold]")
    for dataset, path in sorted(masters.items()):
        try:
            years = get_available_years(dataset, dictionary_root=dict_root)
            console.print(
                f"  [cyan]{dataset}:[/cyan] {len(years)} years ({min(years)}-{max(years)})"
            )
        except Exception:
            console.print(f"  [cyan]{dataset}:[/cyan] (built)")

    console.print(f"\n[dim]Location: {dict_root / 'fhfa' / 'masters'}[/dim]")


@schemas_app.command("pipeline")
def schemas_pipeline(
    min_year: Annotated[int, typer.Option("--min-year", help="Minimum year for masters")] = 2008,
    max_year: Annotated[
        int, typer.Option("--max-year", help="Maximum year for masters")
    ] = SCHEMA_MAX_YEAR,
    skip_extract: Annotated[
        bool, typer.Option("--skip-extract", help="Skip PDF extraction")
    ] = False,
    skip_convert: Annotated[
        bool, typer.Option("--skip-convert", help="Skip PDF/Excel conversion")
    ] = False,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Overwrite existing files")
    ] = False,
):
    """Run complete schema processing pipeline.

    This is equivalent to running:
      1. fhfa schemas extract
      2. fhfa schemas convert
      3. fhfa schemas build

    [yellow]Examples:[/yellow]

      # Process all years
      $ fhfa schemas pipeline

      # Process recent years only
      $ fhfa schemas pipeline --min-year 2020

      # Use existing PDFs, just rebuild masters
      $ fhfa schemas pipeline --skip-extract --skip-convert
    """
    print_section_header("Schema Processing Pipeline")

    console.print(f"[cyan]Year range:[/cyan] {min_year}-{max_year}")
    console.print(f"[cyan]Skip extraction:[/cyan] {skip_extract}")
    console.print(f"[cyan]Skip conversion:[/cyan] {skip_convert}\n")

    results = run_schemas(
        min_year=min_year,
        max_year=max_year,
        skip_extract=skip_extract,
        skip_convert=skip_convert,
        overwrite=overwrite,
    )

    # Show summary
    print_summary(
        "Pipeline Complete",
        {
            "PDFs Extracted": results["pdfs_extracted"] if not skip_extract else "skipped",
            "Years Converted": results["years_converted"] if not skip_convert else "skipped",
            "Master Dictionaries": results["masters_built"],
            "Datasets": ", ".join(results["datasets"]) if results["datasets"] else "none",
        },
    )


@schemas_app.command("list-years")
def list_years(
    dataset: Annotated[str, typer.Argument(help="Dataset (e.g., sf_c, mf_property_b)")],
    dict_dir: Annotated[
        Path | None, typer.Option("--dict-dir", help="Dictionary directory")
    ] = None,
):
    """List available years for a dataset.

    Queries the master dictionary to show which years have
    data definitions available.

    [yellow]Example:[/yellow]

      $ fhfa schemas list-years sf_c
    """
    # Validate dataset
    validate_datasets([dataset])

    dict_root = (dict_dir or FHFAConfig.FHFA_SCHEMAS_DIR).resolve()

    try:
        years = get_available_years(dataset, dictionary_root=dict_root)
    except FileNotFoundError:
        console.print(f"[red]Error:[/red] Master dictionary not found for {dataset}")
        console.print("[yellow]Hint:[/yellow] Run 'fhfa schemas build' first")
        raise typer.Exit(code=1)

    console.print(f"\n[bold cyan]{dataset}[/bold cyan] - Available Years:")
    console.print(f"  [green]{len(years)} years:[/green] {min(years)}-{max(years)}")
    console.print(f"\n  {', '.join(map(str, years))}\n")


# ============================================================================
# Bronze
# ============================================================================


@app.command()
def bronze(
    datasets: Annotated[
        list[str], typer.Option("--datasets", "-t", help="Datasets to load (e.g., sf_c sf_a)")
    ],
    min_year: Annotated[int, typer.Option("--min-year", help="First year to load (e.g., 2023)")],
    max_year: Annotated[
        int, typer.Option("--max-year", help="Last year to load, inclusive (e.g., 2024)")
    ],
    raw_dir: Annotated[
        Path | None, typer.Option("--raw-dir", help="Raw data directory (default: data/fhfa/raw)")
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir", "-o", help="Bronze output directory (default: data/fhfa/bronze)"
        ),
    ] = None,
    overwrite: OverwriteOption = False,
):
    """Load FHFA data files (fixed-width through 2024, CSV from 2025) to bronze layer.

    Reads raw data from zip files, parses using the data dictionaries,
    and saves as Parquet files in the bronze layer.

    [yellow]Examples:[/yellow]

      # Load SF Census Tract for 2023-2024
      $ fhfa bronze -t sf_c --min-year 2023 --max-year 2024

      # Load multiple datasets
      $ fhfa bronze -t sf_c sf_d mf_c --min-year 2023 --max-year 2023

      # Custom directories
      $ fhfa bronze -t sf_c --min-year 2023 --max-year 2023 --raw-dir /data/raw -o /data/bronze

    [yellow]Valid datasets:[/yellow] sf_a, sf_b, sf_c, sf_d, mf_property_b, mf_unit_b, mf_c
    """
    print_section_header("Load Data to Bronze Layer")

    if min_year > max_year:
        print_error(f"--min-year ({min_year}) must be <= --max-year ({max_year})")
        raise typer.Exit(code=1)

    years = list(range(min_year, max_year + 1))

    # Validate inputs
    validate_datasets(datasets)
    validate_years(years)

    # Resolve paths
    raw = (raw_dir or FHFAConfig.FHFA_RAW_DIR).resolve()
    output = (output_dir or FHFAConfig.FHFA_BRONZE_DIR).resolve()
    output.mkdir(parents=True, exist_ok=True)

    console.print(f"[cyan]Datasets:[/cyan] {', '.join(datasets)}")
    console.print(f"[cyan]Years:[/cyan] {min_year}-{max_year}")
    console.print(f"[cyan]Raw directory:[/cyan] {raw}")
    console.print(f"[cyan]Output directory:[/cyan] {output}\n")

    # Track results
    total_tasks = len(datasets) * len(years)
    completed = 0
    failed = 0

    with create_progress() as progress:
        task = progress.add_task("Loading to bronze...", total=len(datasets))

        for dataset in datasets:
            progress.update(task, description=f"Loading {dataset}...")

            try:
                save_to_bronze(
                    dataset=dataset,
                    years=years,
                    raw_dir=raw,
                    output_dir=output,
                    overwrite=overwrite,
                )
                completed += len(years)
                print_success(f"Loaded {dataset} for {len(years)} year(s)")
            except Exception as e:
                failed += len(years)
                print_error(f"Failed {dataset}: {str(e)}")

            progress.update(task, advance=1)

    # Summary
    console.print("\n[bold]Summary:[/bold]")
    console.print(f"  [green]✓ Completed:[/green] {completed}/{total_tasks} file-year combinations")
    if failed > 0:
        console.print(f"  [red]✗ Failed:[/red] {failed}/{total_tasks} file-year combinations")

    if failed == total_tasks:
        raise typer.Exit(code=1)


# ============================================================================
# Silver
# ============================================================================


@app.command()
def silver(
    datasets: Annotated[
        list[str], typer.Option("--datasets", "-t", help="Datasets to transform (e.g., sf_c sf_a)")
    ],
    min_year: Annotated[
        int, typer.Option("--min-year", help="First year to transform (e.g., 2023)")
    ],
    max_year: Annotated[
        int, typer.Option("--max-year", help="Last year to transform, inclusive (e.g., 2024)")
    ],
    bronze_dir: Annotated[
        Path | None,
        typer.Option("--bronze-dir", help="Bronze input directory (default: data/fhfa/bronze)"),
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir", "-o", help="Silver output directory (default: data/fhfa/silver)"
        ),
    ] = None,
    drop_patterns: Annotated[
        list[str] | None,
        typer.Option("--drop-pattern", help="Column patterns to drop (can specify multiple)"),
    ] = None,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Overwrite existing silver files")
    ] = False,
):
    """Transform bronze data to silver layer.

    Applies standardization:
    - Renames columns using translation dictionary
    - Drops unwanted columns based on patterns
    - Adds census year metadata
    - Standardizes data types

    [yellow]Examples:[/yellow]

      # Transform SF Census Tract for 2023-2024
      $ fhfa silver -t sf_c --min-year 2023 --max-year 2024

      # Transform multiple datasets
      $ fhfa silver -t sf_c sf_d --min-year 2023 --max-year 2023

      # Custom drop patterns
      $ fhfa silver -t sf_c --min-year 2024 --max-year 2024 --drop-pattern "Rural" --drop-pattern "Poverty"

    [yellow]Valid datasets:[/yellow] sf_a, sf_b, sf_c, sf_d, mf_property_b, mf_unit_b, mf_c
    """
    print_section_header("Transform Data to Silver Layer")

    if min_year > max_year:
        print_error(f"--min-year ({min_year}) must be <= --max-year ({max_year})")
        raise typer.Exit(code=1)

    years = list(range(min_year, max_year + 1))

    # Validate inputs
    validate_datasets(datasets)
    validate_years(years)

    # Resolve paths
    bronze = (bronze_dir or FHFAConfig.FHFA_BRONZE_DIR).resolve()
    output = (output_dir or FHFAConfig.FHFA_SILVER_DIR).resolve()
    output.mkdir(parents=True, exist_ok=True)

    console.print(f"[cyan]Datasets:[/cyan] {', '.join(datasets)}")
    console.print(f"[cyan]Years:[/cyan] {min_year}-{max_year}")
    console.print(f"[cyan]Bronze directory:[/cyan] {bronze}")
    console.print(f"[cyan]Silver directory:[/cyan] {output}")
    if drop_patterns:
        console.print(f"[cyan]Drop patterns:[/cyan] {', '.join(drop_patterns)}\n")
    else:
        console.print()

    # Track results
    total_tasks = len(datasets) * len(years)
    completed = 0
    failed = 0

    with create_progress() as progress:
        task = progress.add_task("Transforming to silver...", total=len(datasets))

        for dataset in datasets:
            progress.update(task, description=f"Transforming {dataset}...")

            try:
                save_to_silver(
                    dataset=dataset,
                    years=years,
                    bronze_dir=bronze / dataset if bronze_dir is None else bronze / dataset,
                    silver_dir=output / dataset if output_dir is None else output / dataset,
                    additional_drop_patterns=drop_patterns if drop_patterns else None,
                    overwrite=overwrite,
                )
                completed += len(years)
                print_success(f"Transformed {dataset} for {len(years)} year(s)")
            except Exception as e:
                failed += len(years)
                print_error(f"Failed {dataset}: {str(e)}")

            progress.update(task, advance=1)

    # Summary
    console.print("\n[bold]Summary:[/bold]")
    console.print(f"  [green]✓ Completed:[/green] {completed}/{total_tasks} file-year combinations")
    if failed > 0:
        console.print(f"  [red]✗ Failed:[/red] {failed}/{total_tasks} file-year combinations")

    if failed == total_tasks:
        raise typer.Exit(code=1)


# ============================================================================
# Pipeline
# ============================================================================


@app.command()
def pipeline(
    datasets: Annotated[
        list[str] | None,
        typer.Option("--datasets", "-t", help="Datasets to process (default all)"),
    ] = None,
    min_year: Annotated[
        int, typer.Option("--min-year", help="First data year to process")
    ] = DEFAULT_DATA_YEAR,
    max_year: Annotated[
        int, typer.Option("--max-year", help="Last data year, inclusive")
    ] = DEFAULT_DATA_YEAR,
    schema_min_year: Annotated[
        int, typer.Option("--schema-min-year", help="First year for the schema masters")
    ] = SCHEMA_MIN_YEAR,
    schema_max_year: Annotated[
        int, typer.Option("--schema-max-year", help="Last year for the schema masters")
    ] = SCHEMA_MAX_YEAR,
    era: Annotated[
        str,
        typer.Option(
            "--era", help="Source era: 'current' (FHFA PUDB) or 'hud' (1993-2007 archive)"
        ),
    ] = "current",
    skip_schemas: Annotated[
        bool, typer.Option("--skip-schemas", help="Skip the schema build step")
    ] = False,
    skip_download: Annotated[
        bool, typer.Option("--skip-download", help="Skip download (--era hud only)")
    ] = False,
    skip_bronze: Annotated[bool, typer.Option("--skip-bronze", help="Skip bronze loading")] = False,
    skip_silver: Annotated[
        bool, typer.Option("--skip-silver", help="Skip silver transformation")
    ] = False,
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Run the FHFA pipeline (schemas -> bronze -> silver).

    Each step can be skipped, which covers the four previous sub-commands:
      - schemas + data (the default)   -> was [cyan]pipeline full[/cyan]
      - [cyan]--skip-schemas[/cyan]                  -> was [cyan]pipeline data-only[/cyan]
      - [cyan]--skip-bronze --skip-silver[/cyan]     -> was [cyan]pipeline schemas-only[/cyan]
      - [cyan]--era hud[/cyan]                       -> was [cyan]pipeline hud-era[/cyan]

    Data years ([cyan]--min-year[/cyan]/[cyan]--max-year[/cyan]) and schema-master
    years ([cyan]--schema-min-year[/cyan]/[cyan]--schema-max-year[/cyan]) are separate
    axes, since the masters span more years than any single data build.

    [cyan]--era hud[/cyan] runs the pre-FHFA GSE PUDB multifamily backfill
    (download -> bronze -> silver over the 1993-2007 archive); it uses the
    HUD-era year bounds and ignores the schema options.

    [yellow]Examples:[/yellow]

      $ mortgage-data fhfa pipeline --min-year 2018 --max-year 2023
      $ mortgage-data fhfa pipeline --skip-schemas -t sf_c
      $ mortgage-data fhfa pipeline --skip-bronze --skip-silver
      $ mortgage-data fhfa pipeline --era hud
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    if era not in ("current", "hud"):
        print_error("--era must be one of: current, hud")
        raise typer.Exit(code=1)

    if era == "hud":
        print_section_header("HUD-era GSE PUDB Backfill (1993-2007)")
        hud_min = min_year if min_year != DEFAULT_DATA_YEAR else HUD_ERA_MIN_YEAR
        hud_max = max_year if max_year != DEFAULT_DATA_YEAR else HUD_ERA_MAX_YEAR
        if hud_min > hud_max:
            print_error(f"--min-year ({hud_min}) must be <= --max-year ({hud_max})")
            raise typer.Exit(code=1)
        console.print(f"[cyan]Years:[/cyan] {hud_min}-{hud_max}\n")
        hud_results = run_hud_era_backfill(
            years=list(range(hud_min, hud_max + 1)),
            skip_download=skip_download,
            skip_bronze=skip_bronze,
            skip_silver=skip_silver,
            overwrite=overwrite,
        )
        print_summary("HUD-era Backfill Complete", dict(hud_results), style="green")
        return

    if min_year > max_year:
        print_error(f"--min-year ({min_year}) must be <= --max-year ({max_year})")
        raise typer.Exit(code=1)
    dt_list = list(datasets) if datasets else list(VALID_DATASETS)
    validate_datasets(dt_list)
    years = list(range(min_year, max_year + 1))
    validate_years(years)

    print_section_header("FHFA Pipeline")
    console.print(f"[cyan]Datasets:[/cyan] {', '.join(dt_list)}")
    console.print(f"[cyan]Years:[/cyan] {min_year}-{max_year}")
    console.print(f"[cyan]Schemas:[/cyan] {not skip_schemas}")
    console.print(f"[cyan]Bronze:[/cyan] {not skip_bronze}")
    console.print(f"[cyan]Silver:[/cyan] {not skip_silver}\n")

    if skip_bronze and skip_silver:
        if skip_schemas:
            print_error("Nothing to do: every step was skipped.")
            raise typer.Exit(code=1)
        schema_results = run_schemas(
            min_year=schema_min_year, max_year=schema_max_year, overwrite=overwrite
        )
        print_summary("Schema Build Complete", dict(schema_results), style="green")
        return

    if skip_schemas:
        results = run_data_pipeline(
            datasets=dt_list,
            years=years,
            skip_bronze=skip_bronze,
            skip_silver=skip_silver,
            overwrite=overwrite,
        )
    else:
        results = run_pipeline(
            datasets=dt_list,
            years=years,
            process_schemas=True,
            schema_min_year=schema_min_year,
            schema_max_year=schema_max_year,
            overwrite=overwrite,
        )
    print_summary("Pipeline Complete", dict(results), style="green")


# ============================================================================
# Top-level commands
# ============================================================================


@app.command()
def info() -> None:
    """Display FHFA configuration information.

    Shows the current configuration paths and settings for FHFA data.

    Examples:
      mortgage-data fhfa info
      fhfa info
    """
    print_info_table(
        "FHFA Configuration",
        {
            "Data Directory": FHFAConfig.FHFA_DATA_DIR,
            "Raw Directory": FHFAConfig.FHFA_RAW_DIR,
            "Bronze Directory": FHFAConfig.FHFA_BRONZE_DIR,
            "Silver Directory": FHFAConfig.FHFA_SILVER_DIR,
            "Gold Directory": FHFAConfig.FHFA_GOLD_DIR,
            "Schemas Directory": FHFAConfig.FHFA_SCHEMAS_DIR,
        },
    )

    # Show available commands
    console.print("\n[bold cyan]Available Commands:[/bold cyan]")
    console.print("  download    - Download raw data files and dictionaries")
    console.print("  schemas     - Process data dictionaries")
    console.print("  bronze      - Load data to bronze layer")
    console.print("  silver      - Transform data to silver layer")
    console.print("  pipeline    - Run multi-step workflows")
    console.print("  info        - Show this configuration information")


def cli_main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    cli_main()
