"""HMDA-FHLB Matching CLI.

Typer-based command-line interface for HMDA-FHLB matching workflow.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.matching.match_hmda_fhlb.config import (
    DATA_DIR,
    MATCH_SETTINGS,
    MatchHMDAFHLBConfig,
)

# Create Typer app
app = typer.Typer(
    name="hmda-fhlb",
    help="HMDA-FHLB matching workflow commands",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()
logger = get_logger(__name__)


@app.callback()
def callback():
    """[bold cyan]HMDA-FHLB Matching Workflow[/bold cyan].

    Tools for matching HMDA loan records to FHLB acquired member asset data.
    """
    configure_logging(level="INFO")


@app.command()
def run(
    max_year: int = typer.Option(
        2024,
        "--max-year",
        "-M",
        help="Maximum year to process",
    ),
    pre2018: bool = typer.Option(
        True,
        "--pre2018/--no-pre2018",
        help="Run pre-2018 matching",
    ),
    post2018: bool = typer.Option(
        True,
        "--post2018/--no-post2018",
        help="Run post-2018 matching",
    ),
    skip_cleaning: bool = typer.Option(
        False,
        "--skip-cleaning",
        help="Skip data cleaning steps (use existing cleaned data)",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Reprocess and overwrite existing cleaned files",
    ),
    match_dir: Path | None = typer.Option(
        None,
        "--match-dir",
        "-d",
        help=f"Output directory for match results (default: {DATA_DIR})",
    ),
):
    """Run the full HMDA-FHLB matching pipeline.

    This command orchestrates the complete matching process:
    1. Extract FHLB member crosswalk from Avery lender panel files
    2. Clean FHLB acquisition data
    3. Clean HMDA loan data for FHLB members
    4. Validate data
    5. Execute matching algorithm
    6. Calculate match statistics

    Examples:
      mortgage-data match hmda-fhlb run
      mortgage-data match hmda-fhlb run --max-year 2023 --no-pre2018
      mortgage-data match hmda-fhlb run --overwrite
    """
    from mortgage_data_manager.matching.match_hmda_fhlb import run_pipeline

    try:
        console.print(f"[cyan]Running HMDA-FHLB matching pipeline (max year: {max_year})...[/cyan]")

        if not pre2018 and not post2018:
            console.print("[red]Error: Must run at least one of pre2018 or post2018[/red]")
            raise typer.Exit(code=1)

        run_pipeline(
            match_folder=match_dir,
            max_year=max_year,
            run_pre2018=pre2018,
            run_post2018=post2018,
            skip_cleaning=skip_cleaning,
            overwrite=overwrite,
        )

        console.print("[green]Pipeline complete![/green]")

    except Exception as e:
        console.print(f"[red]Pipeline failed: {e}[/red]")
        logger.exception("Pipeline error")
        raise typer.Exit(code=1)


@app.command()
def clean(
    max_year: int = typer.Option(
        2024,
        "--max-year",
        "-M",
        help="Maximum year to process",
    ),
    pre2018: bool = typer.Option(
        True,
        "--pre2018/--no-pre2018",
        help="Clean pre-2018 data",
    ),
    post2018: bool = typer.Option(
        True,
        "--post2018/--no-post2018",
        help="Clean post-2018 data",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Reprocess and overwrite existing cleaned files",
    ),
    match_dir: Path | None = typer.Option(
        None,
        "--match-dir",
        "-d",
        help=f"Output directory for cleaned data (default: {DATA_DIR})",
    ),
):
    """Run only the data cleaning steps without matching.

    This command prepares the data by:
    1. Extracting FHLB member crosswalk
    2. Cleaning FHLB acquisition data
    3. Cleaning HMDA loan data

    Examples:
      mortgage-data match hmda-fhlb clean
      mortgage-data match hmda-fhlb clean --max-year 2023 --overwrite
    """
    from mortgage_data_manager.matching.match_hmda_fhlb import run_cleaning_only

    try:
        console.print(f"[cyan]Cleaning data (max year: {max_year})...[/cyan]")

        run_cleaning_only(
            match_folder=match_dir,
            max_year=max_year,
            run_pre2018=pre2018,
            run_post2018=post2018,
            overwrite=overwrite,
        )

        console.print("[green]Cleaning complete![/green]")

    except Exception as e:
        console.print(f"[red]Cleaning failed: {e}[/red]")
        logger.exception("Cleaning error")
        raise typer.Exit(code=1)


@app.command()
def match(
    pre2018: bool = typer.Option(
        True,
        "--pre2018/--no-pre2018",
        help="Run pre-2018 matching",
    ),
    post2018: bool = typer.Option(
        True,
        "--post2018/--no-post2018",
        help="Run post-2018 matching",
    ),
    match_dir: Path | None = typer.Option(
        None,
        "--match-dir",
        "-d",
        help=f"Directory containing cleaned data (default: {DATA_DIR})",
    ),
):
    """Run only the matching steps (assumes cleaned data exists).

    This command executes the matching algorithm on pre-cleaned data
    and calculates match statistics.

    Examples:
      mortgage-data match hmda-fhlb match
      mortgage-data match hmda-fhlb match --no-pre2018
    """
    from mortgage_data_manager.matching.match_hmda_fhlb import run_matching_only

    try:
        console.print("[cyan]Running matching on cleaned data...[/cyan]")

        run_matching_only(
            match_folder=match_dir,
            run_pre2018=pre2018,
            run_post2018=post2018,
        )

        console.print("[green]Matching complete![/green]")

    except Exception as e:
        console.print(f"[red]Matching failed: {e}[/red]")
        logger.exception("Matching error")
        raise typer.Exit(code=1)


@app.command()
def validate(
    show: bool = typer.Option(
        False,
        "--show",
        "-s",
        help="Show plots interactively",
    ),
    save: bool = typer.Option(
        True,
        "--save/--no-save",
        help="Save plots to docs/matching/figures/hmda_fhlb/",
    ),
):
    """Run validation analysis on HMDA-FHLB matches.

    Generates diagnostic figures analyzing match quality:
    - Match counts by year (post-2018 and pre-2018)
    - Purchaser type distribution
    - Match rates by loan amount

    Examples:
      mortgage-data match hmda-fhlb validate
      mortgage-data match hmda-fhlb validate --show
      mortgage-data match hmda-fhlb validate --no-save
    """
    from mortgage_data_manager.matching.match_hmda_fhlb.validation import run_validation

    try:
        console.print("[cyan]Running HMDA-FHLB validation analysis...[/cyan]")

        results = run_validation(save_plots=save, show_plots=show)

        if "error" in results:
            console.print(f"[red]Validation error: {results['error']}[/red]")
            raise typer.Exit(code=1)

        console.print("[green]Validation complete![/green]")

    except Exception as e:
        console.print(f"[red]Validation failed: {e}[/red]")
        logger.exception("Validation error")
        raise typer.Exit(code=1)


@app.command()
def info():
    """Display HMDA-FHLB matching configuration and settings.

    Shows the current configuration paths and matching thresholds.

    Examples:
      mortgage-data match hmda-fhlb info
    """
    table = Table(
        title="HMDA-FHLB Matching Configuration", show_header=True, header_style="bold cyan"
    )
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("[bold]Paths[/bold]", "")
    table.add_row("  Data Directory", str(MatchHMDAFHLBConfig.DATA_DIR))
    table.add_row("  Avery File (Post-2018)", str(MatchHMDAFHLBConfig.AVERY_FILE_PATH))
    table.add_row("  Avery File (Pre-2018)", str(MatchHMDAFHLBConfig.AVERY_PRE2018_FILE_PATH))

    table.add_row("", "")
    table.add_row("[bold]Matching Thresholds[/bold]", "")

    settings = MATCH_SETTINGS
    table.add_row("  Income Tolerance (Round 1)", f"${settings['income_tol']['round1']:,}")
    table.add_row("  Income Tolerance (Round 2)", f"${settings['income_tol']['round2']:,}")
    table.add_row("  Income Tolerance (Pre-2018)", f"${settings['income_tol']['pre2018']:,}")
    table.add_row("  LTV Tolerance (Round 1)", f"{settings['ltv_tol']['round1']:.2f}%")
    table.add_row("  LTV Tolerance (Round 2)", f"{settings['ltv_tol']['round2']:.2f}%")
    table.add_row("  DTI Tolerance (Round 1)", f"{settings['dti_tol']['round1']}%")
    table.add_row("  DTI Tolerance (Round 2)", f"{settings['dti_tol']['round2']}%")
    table.add_row("  Rate Tolerance", f"{settings['rate_tol']:.4f}%")
    table.add_row("  Term Tolerance", f"{settings['term_tol']} months")
    table.add_row("  Amount Tolerance (Round 2)", f"${settings['amount_tol']:,}")

    console.print(table)

    # Check for input files
    console.print("\n[bold]Input File Status:[/bold]")
    if MatchHMDAFHLBConfig.AVERY_FILE_PATH.exists():
        console.print("  [green]✓[/green] Post-2018 Avery file exists")
    else:
        console.print("  [red]✗[/red] Post-2018 Avery file not found")

    if MatchHMDAFHLBConfig.AVERY_PRE2018_FILE_PATH.exists():
        console.print("  [green]✓[/green] Pre-2018 Avery file exists")
    else:
        console.print("  [red]✗[/red] Pre-2018 Avery file not found")


def cli_main():
    """Entry point for standalone HMDA-FHLB matching CLI."""
    app()


if __name__ == "__main__":
    cli_main()
