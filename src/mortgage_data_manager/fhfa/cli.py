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
from mortgage_data_manager.core.download import summarize_results
from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.fhfa.config import (
    VALID_DATASETS,
    FHFAConfig,
    validate_datasets,
    validate_years,
)
from mortgage_data_manager.fhfa.download import (
    download_fhfa_data,
    download_fhfa_dictionaries,
    download_hud_gse_archive,
)
from mortgage_data_manager.fhfa.import_bronze import (
    HUD_ERA_DATASETS,
    HUD_ERA_MAX_YEAR,
    HUD_ERA_MIN_YEAR,
    save_to_bronze,
)
from mortgage_data_manager.fhfa.import_silver import save_to_silver
from mortgage_data_manager.fhfa.pipeline import (
    run_data_pipeline,
    run_hud_era_backfill,
    run_pipeline,
    run_schemas,
)
from mortgage_data_manager.fhfa.schemas import (
    build_all_master_dictionaries,
    convert_excel_dictionary,
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
            help="Show version and exit"
        )
    ] = False,
):
    """[bold cyan]FHFA Data Manager[/bold cyan] - Process enterprise PUDB data.

    [yellow]Quick Start:[/yellow]

      # Download all data and dictionaries
      $ fhfa download all

      # Process data dictionaries
      $ fhfa schemas pipeline

      # Load data to bronze layer
      $ fhfa bronze load -t sf_c -y 2023 2024

      # Transform to silver layer
      $ fhfa silver transform -t sf_c -y 2023 2024

      # Run complete pipeline
      $ mortgage-data fhfa pipeline full --min-year 2020

    [yellow]Available Commands:[/yellow]

      [cyan]download[/cyan]   - Download raw data files and dictionaries
      [cyan]schemas[/cyan]    - Process data dictionaries (PDFs, Excel, master building)
      [cyan]bronze[/cyan]     - Load data to bronze layer (fixed-width → Parquet)
      [cyan]silver[/cyan]     - Transform data to silver layer (standardization)
      [cyan]pipeline[/cyan]   - Run multi-step workflows (end-to-end automation)

    Use [bold]fhfa COMMAND --help[/bold] for more information on a command.
    """
    pass


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
# Download subapp
# ============================================================================

download_app = typer.Typer(
    name="download",
    help="Download FHFA data files and dictionaries",
    no_args_is_help=True,
)
app.add_typer(download_app, name="download")


@download_app.command("data")
def download_data(
    output_dir: Path | None = typer.Option(
        None, "--output", "-o", help="Output directory for downloaded files"
    ),
    overwrite: OverwriteOption = False,
    pause: PauseOption = None,
    timeout: TimeoutOption = None,
    retries: RetriesOption = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Download FHFA enterprise data files.

    Downloads enterprise PUDB zip files from the FHFA website.
    Files are saved with names like: 2024_enterprise_pudb.zip

    Note: FHLB AMA data has been moved to the fhlb subpackage.
    Use 'mortgage-data fhlb download data' for FHLB data.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data fhfa download data
      mortgage-data fhfa download data -o /custom/path
      mortgage-data fhfa download data --overwrite --verbose
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    raw_dir = output_dir or FHFAConfig.FHFA_RAW_DIR

    console.print("[cyan]Downloading FHFA enterprise data files...[/cyan]")
    console.print(f"Output: {raw_dir}\n")

    try:
        results = download_fhfa_data(
            output_dir=raw_dir,
            overwrite=overwrite,
            pause=pause,
            timeout=timeout,
            retries=retries,
            show_progress=not quiet,
        )
        _report_results(results, quiet)
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error downloading FHFA data: {e}[/red]")
        logger.exception("Download error")
        raise typer.Exit(code=1)


@download_app.command("dictionaries")
def download_dictionaries(
    output_dir: Path | None = typer.Option(
        None, "--output", "-o", help="Output directory"
    ),
    overwrite: OverwriteOption = False,
    pause: PauseOption = None,
    timeout: TimeoutOption = None,
    retries: RetriesOption = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Download FHFA data dictionary files.

    Downloads Excel dictionary files from FHFA website.

    Note: FHLB dictionaries have been moved to the fhlb subpackage.
    Use 'mortgage-data fhlb download dictionaries' for FHLB dictionaries.

    [bold cyan]Examples:[/bold cyan]

      mortgage-data fhfa download dictionaries
      mortgage-data fhfa download dictionaries -o /custom/path
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    dict_dir = output_dir or (FHFAConfig.FHFA_SCHEMAS_DIR / "fhfa")

    console.print("[cyan]Downloading FHFA dictionaries...[/cyan]")
    console.print(f"Output: {dict_dir}\n")

    try:
        results = download_fhfa_dictionaries(
            output_dir=dict_dir,
            overwrite=overwrite,
            pause=pause,
            timeout=timeout,
            retries=retries,
            show_progress=not quiet,
        )
        _report_results(results, quiet)
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error downloading FHFA dictionaries: {e}[/red]")
        logger.exception("Download error")
        raise typer.Exit(code=1)


@download_app.command("all")
def download_all(
    data_dir: Path | None = typer.Option(
        None, "--data-dir", help="Directory for data files"
    ),
    dict_dir: Path | None = typer.Option(
        None, "--dict-dir", help="Directory for dictionary files"
    ),
    overwrite: OverwriteOption = False,
    pause: PauseOption = None,
    timeout: TimeoutOption = None,
    retries: RetriesOption = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
):
    """Download both FHFA data and dictionaries.

    Convenience command to download everything in one step.

    Note: FHLB data has been moved to the fhlb subpackage.
    Use 'mortgage-data fhlb download all' for FHLB files.

    [bold cyan]Example:[/bold cyan]

      mortgage-data fhfa download all
      mortgage-data fhfa download all --overwrite --verbose
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    console.print("[bold cyan]Download All FHFA Files[/bold cyan]")
    console.print("[dim]Downloading FHFA data files and dictionaries...[/dim]\n")

    total_success = 0
    total_skipped = 0
    total_failed = 0

    try:
        # Download data
        console.print("[cyan]1. Downloading FHFA data...[/cyan]")
        data_results = download_fhfa_data(
            output_dir=data_dir,
            overwrite=overwrite,
            pause=pause,
            timeout=timeout,
            retries=retries,
            show_progress=not quiet,
        )
        data_summary = summarize_results(data_results)
        total_success += data_summary.get("success", 0)
        total_skipped += data_summary.get("skipped_exists", 0)
        total_failed += data_summary.get("failed", 0)

        # Download dictionaries
        console.print("\n[cyan]2. Downloading dictionaries...[/cyan]")
        dict_results = download_fhfa_dictionaries(
            output_dir=dict_dir,
            overwrite=overwrite,
            pause=pause,
            timeout=timeout,
            retries=retries,
            show_progress=not quiet,
        )
        dict_summary = summarize_results(dict_results)
        total_success += dict_summary.get("success", 0)
        total_skipped += dict_summary.get("skipped_exists", 0)
        total_failed += dict_summary.get("failed", 0)

        # Final summary
        console.print("\n[bold]Summary:[/bold]")
        console.print(f"  Data files: {data_summary.get('success', 0)} downloaded")
        console.print(f"  Dictionary files: {dict_summary.get('success', 0)} downloaded")

        if total_failed > 0:
            console.print(
                f"\n[yellow]Download completed with errors: "
                f"{total_success} downloaded, {total_skipped} skipped, {total_failed} failed[/yellow]"
            )
            raise typer.Exit(code=1)
        else:
            console.print(
                f"\n[bold green]All downloads complete: "
                f"{total_success} downloaded, {total_skipped} skipped[/bold green]"
            )

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        logger.exception("Download error")
        raise typer.Exit(code=1)


@download_app.command("hud-era")
def download_hud_era(
    min_year: Annotated[
        int,
        typer.Option("--min-year", help="First HUD-era year to fetch")
    ] = HUD_ERA_MIN_YEAR,
    max_year: Annotated[
        int,
        typer.Option("--max-year", help="Last HUD-era year to fetch, inclusive")
    ] = HUD_ERA_MAX_YEAR,
    overwrite: OverwriteOption = False,
    pause: PauseOption = None,
    timeout: TimeoutOption = None,
    retries: RetriesOption = None,
):
    """Download the HUD-era GSE PUDB multifamily archive zips (1993-2007).

    Fetches the pre-FHFA GSE Public Use Database archive from HUD USER into
    [cyan]raw/hud_era[/cyan]. Archive zips spanning several years are fetched
    whole when any covered year is in range.

    [yellow]Example:[/yellow]

      $ fhfa download hud-era
      $ fhfa download hud-era --min-year 1996 --max-year 1998
    """
    print_section_header("Download HUD-era GSE Archive (1993-2007)")

    if min_year > max_year:
        print_error(f"--min-year ({min_year}) must be <= --max-year ({max_year})")
        raise typer.Exit(code=1)

    years = list(range(min_year, max_year + 1))
    results = download_hud_gse_archive(
        years=years, overwrite=overwrite, pause=pause,
        timeout=timeout, retries=retries,
    )
    summary = summarize_results(results)
    print_summary("HUD-era Download Complete", {
        "Downloaded": summary.get("success", 0),
        "Skipped": summary.get("skipped_exists", 0),
        "[red]Failed[/red]": summary.get("failed", 0),
    }, style="green")


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
        Path | None,
        typer.Option("--raw-dir", help="Raw data directory (default: data/fhfa/raw)")
    ] = None,
    dict_dir: Annotated[
        Path | None,
        typer.Option("--dict-dir", help="Dictionary output directory")
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
        pdfs = extract_enterprise_pudb_pdfs(
            raw_dir=raw,
            dictionary_root=dict_root
        )

    print_success(f"Extracted {len(pdfs)} PDF files")
    for pdf in pdfs[:5]:  # Show first 5
        console.print(f"  • {pdf.name}")
    if len(pdfs) > 5:
        console.print(f"  [dim]... and {len(pdfs) - 5} more[/dim]")


@schemas_app.command("convert")
def convert_pdfs(
    min_year: Annotated[
        int | None,
        typer.Option("--min-year", help="First year to convert (default: all available)")
    ] = None,
    max_year: Annotated[
        int | None,
        typer.Option("--max-year", help="Last year to convert, inclusive (default: all available)")
    ] = None,
    dict_dir: Annotated[
        Path | None,
        typer.Option("--dict-dir", help="Dictionary directory")
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Overwrite existing CSV files")
    ] = False,
):
    """Convert PDFs/Excel to CSV/Parquet format.

    Converts dictionary PDFs (years < 2024) and the 2024 Excel file
    to CSV format for easier processing.

    [yellow]Examples:[/yellow]

      # Convert all years
      $ fhfa schemas convert

      # Convert specific years
      $ fhfa schemas convert --min-year 2023 --max-year 2024
    """
    from mortgage_data_manager.fhfa.config import PROJECT_DIR

    print_section_header("Convert Dictionaries to CSV")

    if min_year is not None and max_year is not None and min_year > max_year:
        console.print(
            f"[red]Error:[/red] --min-year ({min_year}) must be <= --max-year ({max_year})"
        )
        raise typer.Exit(code=1)

    dict_root = (dict_dir or FHFAConfig.FHFA_SCHEMAS_DIR).resolve()
    fhfa_dict_root = dict_root / 'fhfa'

    console.print(f"[cyan]Dictionary directory:[/cyan] {dict_root}\n")

    # Determine years to convert
    if min_year is not None or max_year is not None:
        lo = min_year if min_year is not None else 2008
        hi = max_year if max_year is not None else 2024
        years_to_convert = [y for y in range(lo, hi + 1) if y < 2024]
        convert_2024_excel = lo <= 2024 <= hi
    else:
        # Find all available years
        if fhfa_dict_root.exists():
            year_dirs = [d for d in fhfa_dict_root.iterdir() if d.is_dir() and d.name.isdigit()]
            years_to_convert = sorted([int(d.name) for d in year_dirs if int(d.name) < 2024])
        else:
            years_to_convert = []
        convert_2024_excel = True

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
                    output_formats=('csv',)
                )
                success_count += 1
                console.print(f"  [green]✓[/green] {year}")
            except Exception as e:
                fail_count += 1
                console.print(f"  [red]✗[/red] {year}: {str(e)[:50]}")

    # Convert 2024 Excel dictionary
    if convert_2024_excel:
        console.print("\n[cyan]Converting 2024 Excel dictionary...[/cyan]")
        excel_path = dict_root / 'fhfa' / '2024-enterprise-pudb-data-dictionary.xlsx'
        if excel_path.exists():
            try:
                results = convert_excel_dictionary(
                    excel_path,
                    year=2024,
                    output_dir=dict_root / 'fhfa' / '2024',
                    overwrite=overwrite
                )
                success_count += 1
                print_success(f"Converted {len(results)} sheets from 2024 Excel file")
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
    min_year: Annotated[
        int,
        typer.Option("--min-year", help="Minimum year to include")
    ] = 2008,
    max_year: Annotated[
        int,
        typer.Option("--max-year", help="Maximum year to include")
    ] = 2024,
    dict_dir: Annotated[
        Path | None,
        typer.Option("--dict-dir", help="Dictionary directory")
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Overwrite existing master files")
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
            min_year=min_year,
            max_year=max_year,
            dictionary_root=dict_root,
            overwrite=overwrite
        )

    print_success(f"Built {len(masters)} master dictionaries")

    # Show details for each master
    console.print("\n[bold]Master Dictionaries:[/bold]")
    for dataset, path in sorted(masters.items()):
        try:
            years = get_available_years(dataset, dictionary_root=dict_root)
            console.print(f"  [cyan]{dataset}:[/cyan] {len(years)} years ({min(years)}-{max(years)})")
        except Exception:
            console.print(f"  [cyan]{dataset}:[/cyan] (built)")

    console.print(f"\n[dim]Location: {dict_root / 'fhfa' / 'masters'}[/dim]")


@schemas_app.command("pipeline")
def schemas_pipeline(
    min_year: Annotated[
        int,
        typer.Option("--min-year", help="Minimum year for masters")
    ] = 2008,
    max_year: Annotated[
        int,
        typer.Option("--max-year", help="Maximum year for masters")
    ] = 2024,
    skip_extract: Annotated[
        bool,
        typer.Option("--skip-extract", help="Skip PDF extraction")
    ] = False,
    skip_convert: Annotated[
        bool,
        typer.Option("--skip-convert", help="Skip PDF/Excel conversion")
    ] = False,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Overwrite existing files")
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
        overwrite=overwrite
    )

    # Show summary
    print_summary("Pipeline Complete", {
        "PDFs Extracted": results['pdfs_extracted'] if not skip_extract else "skipped",
        "Years Converted": results['years_converted'] if not skip_convert else "skipped",
        "Master Dictionaries": results['masters_built'],
        "Datasets": ', '.join(results['datasets']) if results['datasets'] else "none",
    })


@schemas_app.command("list-years")
def list_years(
    dataset: Annotated[
        str,
        typer.Argument(help="Dataset (e.g., sf_c, mf_property_b)")
    ],
    dict_dir: Annotated[
        Path | None,
        typer.Option("--dict-dir", help="Dictionary directory")
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
# Bronze subapp
# ============================================================================

bronze_app = typer.Typer(
    name="bronze",
    help="Load data to bronze layer (fixed-width → Parquet)",
    no_args_is_help=True,
)
app.add_typer(bronze_app, name="bronze")


@bronze_app.command("load")
def load_bronze(
    datasets: Annotated[
        list[str],
        typer.Option("--datasets", "-t", help="Datasets to load (e.g., sf_c sf_a)")
    ],
    min_year: Annotated[
        int,
        typer.Option("--min-year", help="First year to load (e.g., 2023)")
    ],
    max_year: Annotated[
        int,
        typer.Option("--max-year", help="Last year to load, inclusive (e.g., 2024)")
    ],
    raw_dir: Annotated[
        Path | None,
        typer.Option("--raw-dir", help="Raw data directory (default: data/fhfa/raw)")
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", "-o", help="Bronze output directory (default: data/fhfa/bronze)")
    ] = None,
    overwrite: OverwriteOption = False,
):
    """Load fixed-width data files to bronze layer.

    Reads raw data from zip files, parses using master dictionaries,
    and saves as Parquet files in the bronze layer.

    [yellow]Examples:[/yellow]

      # Load SF Census Tract for 2023-2024
      $ fhfa bronze load -t sf_c --min-year 2023 --max-year 2024

      # Load multiple datasets
      $ fhfa bronze load -t sf_c sf_d mf_c --min-year 2023 --max-year 2023

      # Custom directories
      $ fhfa bronze load -t sf_c --min-year 2023 --max-year 2023 --raw-dir /data/raw -o /data/bronze

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
                    overwrite=overwrite
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
# Silver subapp
# ============================================================================

silver_app = typer.Typer(
    name="silver",
    help="Transform data to silver layer (standardization)",
    no_args_is_help=True,
)
app.add_typer(silver_app, name="silver")


@silver_app.command("transform")
def transform_silver(
    datasets: Annotated[
        list[str],
        typer.Option("--datasets", "-t", help="Datasets to transform (e.g., sf_c sf_a)")
    ],
    min_year: Annotated[
        int,
        typer.Option("--min-year", help="First year to transform (e.g., 2023)")
    ],
    max_year: Annotated[
        int,
        typer.Option("--max-year", help="Last year to transform, inclusive (e.g., 2024)")
    ],
    bronze_dir: Annotated[
        Path | None,
        typer.Option("--bronze-dir", help="Bronze input directory (default: data/fhfa/bronze)")
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", "-o", help="Silver output directory (default: data/fhfa/silver)")
    ] = None,
    drop_patterns: Annotated[
        list[str] | None,
        typer.Option("--drop-pattern", help="Column patterns to drop (can specify multiple)")
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Overwrite existing silver files")
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
      $ fhfa silver transform -t sf_c --min-year 2023 --max-year 2024

      # Transform multiple datasets
      $ fhfa silver transform -t sf_c sf_d --min-year 2023 --max-year 2023

      # Custom drop patterns
      $ fhfa silver transform -t sf_c --min-year 2024 --max-year 2024 --drop-pattern "Rural" --drop-pattern "Poverty"

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
                    overwrite=overwrite
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
# Pipeline subapp
# ============================================================================

pipeline_app = typer.Typer(
    name="pipeline",
    help="Run multi-step workflows (end-to-end automation)",
    no_args_is_help=True,
)
app.add_typer(pipeline_app, name="pipeline")


@pipeline_app.command("full")
def full_pipeline(
    datasets: Annotated[
        list[str] | None,
        typer.Option("--datasets", "-t", help="Datasets to process (default: all)")
    ] = None,
    min_year: Annotated[
        int,
        typer.Option("--min-year", help="First year to process (e.g., 2024)")
    ] = 2024,
    max_year: Annotated[
        int,
        typer.Option("--max-year", help="Last year to process, inclusive")
    ] = 2024,
    skip_schemas: Annotated[
        bool,
        typer.Option("--skip-schemas", help="Skip schema processing")
    ] = False,
    overwrite: OverwriteOption = False,
):
    """Run complete end-to-end pipeline.

    Executes all steps:
      1. Process schemas (extract PDFs, convert, build masters)
      2. Load data to bronze layer
      3. Transform data to silver layer

    [yellow]Examples:[/yellow]

      # Process all datasets for 2024
      $ fhfa pipeline full --min-year 2024 --max-year 2024

      # Process specific datasets and years
      $ fhfa pipeline full -t sf_c sf_a --min-year 2023 --max-year 2024

      # Skip schema processing (use existing masters)
      $ fhfa pipeline full --min-year 2023 --max-year 2024 --skip-schemas

    [yellow]Valid datasets:[/yellow] sf_a, sf_b, sf_c, sf_d, mf_property_b, mf_unit_b, mf_c
    """
    print_section_header("Complete FHFA Pipeline")

    if min_year > max_year:
        print_error(f"--min-year ({min_year}) must be <= --max-year ({max_year})")
        raise typer.Exit(code=1)

    year_list = list(range(min_year, max_year + 1))

    # Determine datasets
    if datasets:
        validate_datasets(datasets)
        ds_list = datasets
    else:
        ds_list = VALID_DATASETS

    # Validate years
    validate_years(year_list)

    console.print(f"[cyan]Datasets:[/cyan] {', '.join(ds_list)}")
    console.print(f"[cyan]Years:[/cyan] {min_year}-{max_year}")
    console.print(f"[cyan]Process schemas:[/cyan] {not skip_schemas}\n")

    results = run_pipeline(
        datasets=ds_list,
        years=year_list,
        process_schemas=not skip_schemas,
        schema_min_year=min(year_list),
        schema_max_year=max(year_list),
        overwrite=overwrite
    )

    # Show comprehensive summary
    summary = {}

    if not skip_schemas:
        summary["Schemas - PDFs Extracted"] = results['schemas'].get('pdfs_extracted', 0)
        summary["Schemas - Years Converted"] = results['schemas'].get('years_converted', 0)
        summary["Schemas - Masters Built"] = results['schemas'].get('masters_built', 0)

    summary["Data - Bronze Loaded"] = results['data'].get('bronze_loaded', 0)
    summary["Data - Silver Transformed"] = results['data'].get('silver_transformed', 0)

    if results['data'].get('failed_bronze', 0) > 0:
        summary["[red]Data - Failed Bronze[/red]"] = results['data']['failed_bronze']
    if results['data'].get('failed_silver', 0) > 0:
        summary["[red]Data - Failed Silver[/red]"] = results['data']['failed_silver']

    print_summary("Pipeline Complete", summary, style="green")


@pipeline_app.command("data-only")
def data_only_pipeline(
    datasets: Annotated[
        list[str],
        typer.Option("--datasets", "-t", help="Datasets to process")
    ],
    min_year: Annotated[
        int,
        typer.Option("--min-year", help="First year to process (e.g., 2023)")
    ],
    max_year: Annotated[
        int,
        typer.Option("--max-year", help="Last year to process, inclusive (e.g., 2024)")
    ],
    skip_bronze: Annotated[
        bool,
        typer.Option("--skip-bronze", help="Skip bronze loading")
    ] = False,
    skip_silver: Annotated[
        bool,
        typer.Option("--skip-silver", help="Skip silver transformation")
    ] = False,
    overwrite: OverwriteOption = False,
):
    """Run data pipeline only (skip schema processing).

    Executes:
      1. Load data to bronze layer (optional)
      2. Transform data to silver layer (optional)

    Note: FHLB AMA data processing has been moved to the fhlb subpackage.
    Use 'mortgage-data fhlb pipeline ama' for FHLB data.

    [yellow]Examples:[/yellow]

      # Load to bronze and transform to silver
      $ fhfa pipeline data-only -t sf_c --min-year 2023 --max-year 2024

      # Only transform to silver (bronze already exists)
      $ fhfa pipeline data-only -t sf_c --min-year 2024 --max-year 2024 --skip-bronze

    [yellow]Valid datasets:[/yellow] sf_a, sf_b, sf_c, sf_d, mf_property_b, mf_unit_b, mf_c
    """
    print_section_header("Data Pipeline (Bronze + Silver)")

    if min_year > max_year:
        print_error(f"--min-year ({min_year}) must be <= --max-year ({max_year})")
        raise typer.Exit(code=1)

    years = list(range(min_year, max_year + 1))

    # Validate inputs
    validate_datasets(datasets)
    validate_years(years)

    console.print(f"[cyan]Datasets:[/cyan] {', '.join(datasets)}")
    console.print(f"[cyan]Years:[/cyan] {min_year}-{max_year}")
    console.print(f"[cyan]Skip bronze:[/cyan] {skip_bronze}")
    console.print(f"[cyan]Skip silver:[/cyan] {skip_silver}\n")

    results = run_data_pipeline(
        datasets=datasets,
        years=years,
        skip_bronze=skip_bronze,
        skip_silver=skip_silver,
        overwrite=overwrite,
    )

    # Show summary
    summary = {}
    if not skip_bronze:
        summary["Bronze Loaded"] = results['bronze_loaded']
        if results.get('failed_bronze', 0) > 0:
            summary["[red]Bronze Failed[/red]"] = results['failed_bronze']

    if not skip_silver:
        summary["Silver Transformed"] = results['silver_transformed']
        if results.get('failed_silver', 0) > 0:
            summary["[red]Silver Failed[/red]"] = results['failed_silver']

    print_summary("Data Pipeline Complete", summary, style="green")


@pipeline_app.command("schemas-only")
def schemas_only_pipeline(
    min_year: Annotated[
        int,
        typer.Option("--min-year", help="Minimum year for masters")
    ] = 2008,
    max_year: Annotated[
        int,
        typer.Option("--max-year", help="Maximum year for masters")
    ] = 2024,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Overwrite existing files")
    ] = False,
):
    """Run schema processing only.

    Executes:
      1. Extract PDFs from zips
      2. Convert PDFs/Excel to CSV
      3. Build master dictionaries

    This is equivalent to: fhfa schemas pipeline

    [yellow]Example:[/yellow]

      $ fhfa pipeline schemas-only --min-year 2020
    """
    print_section_header("Schema Processing Pipeline")

    console.print(f"[cyan]Year range:[/cyan] {min_year}-{max_year}\n")

    results = run_schemas(
        min_year=min_year,
        max_year=max_year,
        overwrite=overwrite
    )

    print_summary("Schema Pipeline Complete", {
        "PDFs Extracted": results['pdfs_extracted'],
        "Years Converted": results['years_converted'],
        "Master Dictionaries": results['masters_built'],
        "Datasets": ', '.join(results['datasets']) if results['datasets'] else "none",
    }, style="green")


@pipeline_app.command("hud-era")
def hud_era_pipeline(
    datasets: Annotated[
        list[str] | None,
        typer.Option("--datasets", "-t",
                     help="Multifamily datasets (default: all three)")
    ] = None,
    min_year: Annotated[
        int,
        typer.Option("--min-year", help="First year to process")
    ] = HUD_ERA_MIN_YEAR,
    max_year: Annotated[
        int,
        typer.Option("--max-year", help="Last year to process, inclusive")
    ] = HUD_ERA_MAX_YEAR,
    skip_download: Annotated[
        bool,
        typer.Option("--skip-download", help="Use already-staged raw zips")
    ] = False,
    skip_bronze: Annotated[
        bool,
        typer.Option("--skip-bronze", help="Skip bronze building")
    ] = False,
    skip_silver: Annotated[
        bool,
        typer.Option("--skip-silver", help="Skip silver harmonization")
    ] = False,
    overwrite: OverwriteOption = False,
):
    """Run the HUD-era (1993-2007) multifamily backfill: download -> bronze -> silver.

    Extends the existing [cyan]mf_c[/cyan]/[cyan]mf_property_b[/cyan]/
    [cyan]mf_unit_b[/cyan] series back to 1993 using the pre-FHFA GSE Public Use
    Database. Bronze and silver land in the same namespaces as the modern data
    and harmonize to the same schema (the bucketed UPB is kept as
    [cyan]upb_acquisition_range[/cyan]; FHFA-era-only fields are absent).

    [yellow]Examples:[/yellow]

      # Full backfill (download + build everything)
      $ fhfa pipeline hud-era

      # Rebuild silver only from staged raw, single era
      $ fhfa pipeline hud-era --skip-download --skip-bronze --min-year 1996 --max-year 1998

    [yellow]Valid datasets:[/yellow] mf_c, mf_property_b, mf_unit_b
    """
    print_section_header("HUD-era Multifamily Backfill (1993-2007)")

    if min_year > max_year:
        print_error(f"--min-year ({min_year}) must be <= --max-year ({max_year})")
        raise typer.Exit(code=1)

    if datasets:
        invalid = [d for d in datasets if d not in HUD_ERA_DATASETS]
        if invalid:
            print_error(f"Invalid HUD-era datasets: {', '.join(invalid)}")
            console.print(f"[yellow]Valid:[/yellow] {', '.join(HUD_ERA_DATASETS)}")
            raise typer.Exit(code=1)

    years = list(range(min_year, max_year + 1))
    console.print(f"[cyan]Datasets:[/cyan] {', '.join(datasets or HUD_ERA_DATASETS)}")
    console.print(f"[cyan]Years:[/cyan] {min_year}-{max_year}\n")

    results = run_hud_era_backfill(
        datasets=datasets,
        years=years,
        skip_download=skip_download,
        skip_bronze=skip_bronze,
        skip_silver=skip_silver,
        overwrite=overwrite,
    )

    print_summary("HUD-era Backfill Complete", {
        "Archives Downloaded": results['downloaded'],
        "Bronze Files Written": results['bronze_written'],
        "Silver Datasets": results['silver_transformed'],
    }, style="green")


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
