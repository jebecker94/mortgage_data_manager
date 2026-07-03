"""FNMA CLI - Main entry point."""

from __future__ import annotations

from typing import Annotated

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
from mortgage_data_manager.fnma.config import FNMAConfig

app = typer.Typer(
    name="fnma",
    help="FNMA Data Manager - Download and process FNMA data",
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=False,
)

download_app = typer.Typer(
    name="download",
    help="Download FNMA data files (requires manual registration)",
    no_args_is_help=True,
)
app.add_typer(download_app, name="download")

logger = get_logger(__name__)


_version_callback = version_callback_factory("FNMA Data Manager", __version__)


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
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """Import raw FNMA loan-performance zip files to bronze parquet.

    Processes per-quarter zip files (YYYYQ[1-4]*.zip) in the raw directory and
    writes one parquet per quarter to the bronze directory. Bulk archives like
    Performance_All.zip and Exclusions.zip are handled by dedicated functions
    in fnma.import_bronze and are skipped here.

    Examples:
      mortgage-data fnma bronze
      mortgage-data fnma bronze --overwrite
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    from mortgage_data_manager.fnma.import_bronze import run_bronze_import

    try:
        result = run_bronze_import(overwrite=overwrite)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    console.print(
        f"\n[bold green]Done.[/bold green] "
        f"Processed {result['processed']}, "
        f"successful {result['successful']}, "
        f"failed {result['failed']}."
    )
    if result["failed"] > 0:
        raise typer.Exit(code=1)


@app.command()
def silver(
    datasets: Annotated[
        list[str] | None,
        typer.Option(
            "--datasets",
            "-t",
            help="Silver datasets to build: issuances, performance (repeatable; default both)",
        ),
    ] = None,
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """Build the FNMA silver layer from bronze loan-performance parquet.

    Builds two datasets (default both):
      - [cyan]issuances[/cyan]: first observation per loan (one row per loan)
      - [cyan]performance[/cyan]: the full monthly panel (loan x month) with the
        ``MMYYYY`` date columns parsed to dates — the GSE SF-credit input to the
        combined [cyan]loan_performance[/cyan] master

    Already-built quarters are skipped unless [cyan]--overwrite[/cyan].

    Examples:
      mortgage-data fnma silver
      mortgage-data fnma silver -t performance
      mortgage-data fnma silver -t issuances --overwrite
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    valid = {"issuances", "performance"}
    selected = datasets or ["issuances", "performance"]
    unknown = [d for d in selected if d not in valid]
    if unknown:
        console.print(
            f"[bold red]Error:[/bold red] unknown dataset(s) {unknown}; choose from {sorted(valid)}"
        )
        raise typer.Exit(code=1)

    from mortgage_data_manager.fnma.import_silver import build_silver, build_silver_performance

    try:
        if "issuances" in selected:
            build_silver(overwrite=overwrite)
        if "performance" in selected:
            build_silver_performance(overwrite=overwrite)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    console.print("\n[bold green]Silver build complete.[/bold green]")


@app.command()
def pipeline(
    overwrite: OverwriteOption = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """Run bronze + silver for FNMA loan-performance data in one step.

    Download is manual (see ``fnma download``); pipeline assumes raw files are
    already in place. Bronze runs first, then silver. Silver still runs even
    if bronze reports failures, since previously-built bronze parquet may
    still cover the requested quarters.

    Examples:
      mortgage-data fnma pipeline
      mortgage-data fnma pipeline --overwrite
    """
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    from mortgage_data_manager.fnma.import_bronze import run_bronze_import
    from mortgage_data_manager.fnma.import_silver import build_silver

    console.print("[cyan]Step 1: Bronze[/cyan]")
    try:
        result = run_bronze_import(overwrite=overwrite)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[bold red]Bronze failed:[/bold red] {e}")
        raise typer.Exit(code=1)
    console.print(
        f"Bronze done — processed {result['processed']}, "
        f"successful {result['successful']}, "
        f"failed {result['failed']}."
    )

    console.print("\n[cyan]Step 2: Silver[/cyan]")
    try:
        build_silver(overwrite=overwrite)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[bold red]Silver failed:[/bold red] {e}")
        raise typer.Exit(code=1)

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


@download_app.command("data")
def download_data() -> None:
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
