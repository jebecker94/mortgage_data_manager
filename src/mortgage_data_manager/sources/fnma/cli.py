"""FNMA CLI - Main entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import typer

from mortgage_data_manager import __version__
from mortgage_data_manager.core.cli.download_options import (
    OverwriteOption,
    QuietOption,
    VerboseOption,
)
from mortgage_data_manager.core.cli.formatting import (
    console,
    print_info_table,
    print_section_header,
)
from mortgage_data_manager.core.cli.validation import version_callback_factory
from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.sources.fnma.config import (
    BRONZE_DATASETS,
    DEFAULT_BRONZE_DATASETS,
    SILVER_DATASETS,
    FNMAConfig,
)
from mortgage_data_manager.sources.fnma.pipeline import run_bronze, run_pipeline, run_silver

app = typer.Typer(
    name="fnma",
    help="FNMA Data Manager - Download and process FNMA data",
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=False,
)

logger = get_logger(__name__)


_version_callback = version_callback_factory("FNMA Data Manager", __version__)


def _log_level(verbose: bool, quiet: bool) -> Literal["DEBUG", "INFO", "WARNING"]:
    return "DEBUG" if verbose else ("WARNING" if quiet else "INFO")


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
    """[bold cyan]FNMA Data Manager[/bold cyan] - Process FNMA mortgage data.

    [yellow]Commands:[/yellow]

      [cyan]download[/cyan]   - Download data files from FNMA (manual instructions)
      [cyan]bronze[/cyan]     - Import raw zip files to bronze parquet
      [cyan]silver[/cyan]     - Extract issuances and terminations from bronze
      [cyan]pipeline[/cyan]   - Run bronze + silver in one step
      [cyan]info[/cyan]       - Show configuration information

    Use [bold]fnma COMMAND --help[/bold] for more information on a command.
    """
    configure_logging(level="INFO")


@app.command()
def bronze(
    datasets: Annotated[
        list[str] | None,
        typer.Option(
            "--datasets",
            "-t",
            help=(
                f"Datasets to import: {', '.join(BRONZE_DATASETS)} "
                f"(repeatable; default {', '.join(DEFAULT_BRONZE_DATASETS)})"
            ),
        ),
    ] = None,
    overwrite: OverwriteOption = False,
    min_year: Annotated[
        int | None,
        typer.Option("--min-year", "-m", help="Lowest year to import (performance_all only)."),
    ] = None,
    max_year: Annotated[
        int | None,
        typer.Option("--max-year", "-M", help="Highest year to import (performance_all only)."),
    ] = None,
    quarters: Annotated[
        list[str] | None,
        typer.Option(
            "--quarters",
            help=(
                "Explicit quarter stems, e.g. 2005Q3 (repeatable; performance_all only). "
                "Overrides --min-year/--max-year."
            ),
        ),
    ] = None,
    tmp_dir: Annotated[
        Path | None,
        typer.Option(
            "--tmp-dir",
            help=(
                "Extraction scratch space (performance_all, exclusions). Defaults to the "
                "data volume; the system temp dir cannot hold the back-history "
                "members (2003Q3 is 40.5 GB)."
            ),
        ),
    ] = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """Import raw FNMA loan-performance zip files to bronze parquet.

    Datasets (default: sf, exclusions, harp, multifamily):
      - [cyan]sf[/cyan]: per-quarter SF zips (YYYYQ[1-4]*.zip) in the raw
        directory -> one parquet per quarter in the bronze root. Bulk archives
        are not read here; use performance_all for the Performance_All archive.
      - [cyan]exclusions[/cyan]: Exclusions.zip -> {YYYYQn}EXCL.parquet beside
        the standard quarters: the loans Fannie leaves out of the standard
        dataset (ARM, Alt-A, government-insured, bulk acquisitions),
        2000Q1-2020Q3. Every quarter; skipped only when the zip is absent.
      - [cyan]performance_all[/cyan]: Performance_All.zip -> one parquet per
        quarter, the only source of the pre-2016 history (2000Q1 forward).
        OPT-IN: not in the default set, since it restates every quarter.
        Scope it with --min-year/--max-year or --quarters; existing quarters
        are skipped unless --overwrite.
      - [cyan]harp[/cyan]: HARP_Files.zip -> bronze/harp/loan_performance.parquet
        (the whole HARP population in one un-partitioned panel, same 113-field
        layout as the quarterly files) + loan_mapping.parquet (the 1:1 pre-HARP
        loan bridge). Skipped when the zip is absent.
      - [cyan]multifamily[/cyan]: Multifamily.zip / Multifamily_DSCR.zip ->
        release-stamped parquet under bronze/multifamily/ (skipped silently
        when the zips are absent).

    Examples:
      mortgage-data fnma bronze
      mortgage-data fnma bronze -t harp
      mortgage-data fnma bronze -t multifamily --overwrite
      mortgage-data fnma bronze -t performance_all -m 2000 -M 2015
      mortgage-data fnma bronze -t performance_all --quarters 2005Q3
    """
    configure_logging(level=_log_level(verbose, quiet))
    try:
        counts = run_bronze(
            datasets,
            overwrite=overwrite,
            min_year=min_year,
            max_year=max_year,
            quarters=quarters or None,
            tmp_dir=tmp_dir,
        )
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    console.print(
        f"\n[bold green]Done.[/bold green] "
        f"Processed {counts['processed']}, "
        f"successful {counts['successful']}, "
        f"failed {counts['failed']}."
    )
    if counts["failed"] > 0:
        raise typer.Exit(code=1)


@app.command()
def silver(
    datasets: Annotated[
        list[str] | None,
        typer.Option(
            "--datasets",
            "-t",
            help=(
                f"Silver datasets to build: {', '.join(SILVER_DATASETS)} (repeatable; default all)"
            ),
        ),
    ] = None,
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """Build the FNMA silver layer from bronze loan-performance parquet.

    Builds four datasets (default all):
      - [cyan]issuances[/cyan]: first observation per loan (one row per loan)
      - [cyan]performance[/cyan]: the full monthly panel (loan x month) with the
        ``MMYYYY`` date columns parsed to dates — the GSE SF-credit input to the
        combined [cyan]loan_performance[/cyan] master
      - [cyan]harp[/cyan]: the HARP performance/issuances/terminations tables plus
        the pre-HARP loan bridge, in silver/harp/. Schema-identical to the
        quarterly silver, but a DISJOINT loan population — union it in explicitly
        if a consumer wants HARP refis included
      - [cyan]multifamily[/cyan]: the MF performance/acquisitions/terminations/
        current_dscr datasets from the latest bronze/multifamily/ release
        (skipped with a warning when no MF bronze exists)

    Already-built outputs are skipped unless [cyan]--overwrite[/cyan]. A new
    multifamily release restates the full history, so rebuild it with
    [cyan]-t multifamily --overwrite[/cyan].

    Examples:
      mortgage-data fnma silver
      mortgage-data fnma silver -t performance
      mortgage-data fnma silver -t multifamily --overwrite
    """
    configure_logging(level=_log_level(verbose, quiet))
    try:
        counts = run_silver(datasets, overwrite=overwrite)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    console.print(
        f"\n[bold green]Silver build complete.[/bold green] "
        f"Built {counts['built']} dataset(s), {counts['processed']} file(s) written."
    )


@app.command()
def pipeline(
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """Run bronze + silver for FNMA loan-performance data in one step.

    Download is manual (see ``fnma download``); pipeline assumes raw files are
    already in place. Runs every bronze dataset, then every silver dataset —
    the same sets ``fnma bronze`` / ``fnma silver`` build by default. Silver
    still runs even if bronze reports failures, since previously-built bronze
    parquet may still cover the requested quarters.

    Examples:
      mortgage-data fnma pipeline
      mortgage-data fnma pipeline --overwrite
    """
    configure_logging(level=_log_level(verbose, quiet))
    try:
        results = run_pipeline(overwrite=overwrite)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[bold red]Pipeline failed:[/bold red] {e}")
        raise typer.Exit(code=1)

    bronze_counts, silver_counts = results["bronze"], results["silver"]
    console.print(
        f"Bronze done — processed {bronze_counts['processed']}, "
        f"successful {bronze_counts['successful']}, failed {bronze_counts['failed']}."
    )
    console.print(
        f"Silver done — built {silver_counts['built']} dataset(s), "
        f"{silver_counts['processed']} file(s) written."
    )
    console.print("\n[bold green]Pipeline complete.[/bold green]")


@app.command()
def info() -> None:
    """Display FNMA configuration information.

    Shows the current configuration paths and settings for FNMA data.

    Examples:
      mortgage-data fnma info
      fnma info
    """
    print_info_table(
        "FNMA Configuration",
        {
            "Data Directory": FNMAConfig.FNMA_DATA_DIR,
            "Raw Directory": FNMAConfig.FNMA_RAW_DIR,
            "Bronze Directory": FNMAConfig.FNMA_BRONZE_DIR,
            "Silver Directory": FNMAConfig.FNMA_SILVER_DIR,
            "Gold Directory": FNMAConfig.FNMA_GOLD_DIR,
        },
    )

    console.print("\n[bold cyan]Available Commands:[/bold cyan]")
    console.print("  download    - Download data files from FNMA (manual)")
    console.print("  bronze      - Import raw zip files to bronze parquet")
    console.print("  silver      - Extract issuances and terminations to silver")
    console.print("  pipeline    - Run bronze + silver in one step")
    console.print("  info        - Show this configuration information")


@app.command()
def download() -> None:
    """Instructions for downloading Fannie Mae Single-Family Loan Performance Data.

    [yellow]Manual Download Required[/yellow]

    Fannie Mae data requires free registration on the Data Dynamics platform.
    Automated download is not supported due to Terms of Service restrictions.

    [bold cyan]Steps to Download:[/bold cyan]

    1. Visit the Fannie Mae data portal:
       https://capitalmarkets.fanniemae.com/credit-risk-transfer/single-family-credit-risk-transfer/fannie-mae-single-family-loan-performance-data

    2. Click "Access the Data" to reach Data Dynamics

    3. Register for a free account (email + password)

    4. Accept Terms and Conditions

    5. Download files to: data/fnma/raw/

    [bold cyan]After Download:[/bold cyan]

    Run the import command to process files:
      $ mortgage-data fnma bronze

    [bold cyan]Documentation:[/bold cyan]

    See docs/manual_downloads.md for detailed instructions.
    """
    print_section_header("Fannie Mae Data Download Instructions")

    console.print()
    console.print("[yellow bold]Manual Download Required[/yellow bold]")
    console.print()
    console.print(
        "Fannie Mae Single-Family Loan Performance Data requires free registration "
        "on the Data Dynamics platform. Automated download is not supported due to "
        "Terms of Service restrictions."
    )
    console.print()

    console.print("[cyan bold]How to Download:[/cyan bold]")
    console.print()
    console.print("1. [bold]Visit the data portal:[/bold]")
    console.print(
        "   [link=https://capitalmarkets.fanniemae.com/credit-risk-transfer/single-family-credit-risk-transfer/fannie-mae-single-family-loan-performance-data]"
        "https://capitalmarkets.fanniemae.com/.../fannie-mae-single-family-loan-performance-data[/link]"
    )
    console.print()
    console.print("2. [bold]Register for Data Dynamics[/bold] (free account)")
    console.print("   - Click 'Access the Data'")
    console.print("   - Create account with email and password")
    console.print("   - Accept Terms and Conditions")
    console.print()
    console.print("3. [bold]Download the files:[/bold]")
    console.print("   - Full Dataset: Use 'Entire Single Family Dataset' for bulk download")
    console.print("   - By Quarter: Download individual quarterly files")
    console.print()
    console.print("4. [bold]Place files in:[/bold] data/fnma/raw/")
    console.print()

    console.print("[cyan bold]Dataset Information:[/cyan bold]")
    console.print()
    console.print("  - Coverage: 30-year and shorter fixed-rate single-family mortgages")
    console.print("  - Fields: 110 data fields (2024 format)")
    console.print("  - Updates: Quarterly (after the 20th following quarter-end)")
    console.print("  - Cost: Free for research/academic use")
    console.print()

    console.print("[cyan bold]Multifamily Loan Performance Data:[/cyan bold]")
    console.print()
    console.print("  Same manual-download model, from the Multifamily CRT page:")
    console.print(
        "  https://capitalmarkets.fanniemae.com/credit-risk-transfer/"
        "multifamily-credit-risk-transfer/multifamily-loan-performance-data"
    )
    console.print(
        "  Place Multifamily.zip and Multifamily_DSCR.zip in data/fnma/raw/ "
        "(each quarterly release restates the full history), then:"
    )
    console.print("  [green]$ mortgage-data fnma bronze -t multifamily[/green]")
    console.print("  [green]$ mortgage-data fnma silver -t multifamily --overwrite[/green]")
    console.print()

    console.print("[cyan bold]After Download:[/cyan bold]")
    console.print()
    console.print("  Process raw files to bronze layer:")
    console.print("  [green]$ mortgage-data fnma bronze[/green]")
    console.print()

    console.print("[dim]See docs/manual_downloads.md for complete instructions.[/dim]")


def cli_main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    cli_main()
