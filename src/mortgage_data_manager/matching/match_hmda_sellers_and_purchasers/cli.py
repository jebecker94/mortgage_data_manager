"""CLI for HMDA Sellers/Purchasers matching workflow.

This module provides command-line access to the HMDA Sellers/Purchasers matching
pipeline for linking HMDA loan originations with their subsequent purchases.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from mortgage_data_manager.core.logging import configure_logging
from mortgage_data_manager.matching.match_hmda_sellers_and_purchasers.config.settings import (
    CROSSWALK_OUTPUT_DIR,
    HMDA_SILVER_DIR_POST2018,
    HMDA_SILVER_DIR_PRE2018,
    INTERMEDIATE_DIR,
    MAX_YEAR_POST2018,
    MAX_YEAR_PRE2018,
    MIN_YEAR_POST2018,
    MIN_YEAR_PRE2018,
    OUTPUT_DIR,
    SellersAndPurchasersMatchingConfig,
)

app = typer.Typer(
    name="sellers-purchasers",
    help="HMDA Sellers/Purchasers matching workflow for linking loan originations with purchases",
    no_args_is_help=True,
)

console = Console()


@app.callback()
def callback():
    """[bold cyan]HMDA Sellers/Purchasers Matching Workflow[/bold cyan].

    Tools for matching HMDA loan originations with their subsequent purchases to create
    seller-purchaser crosswalks.
    """
    configure_logging(level="INFO")


@app.command()
def info() -> None:
    """Show information about HMDA Sellers/Purchasers matching configuration and data availability."""
    console.print("\n[bold cyan]HMDA Sellers/Purchasers Matching Workflow[/bold cyan]\n")

    console.print(
        "This workflow matches HMDA (Home Mortgage Disclosure Act) loan originations "
        "with their subsequent purchases to create seller-purchaser crosswalks.\n"
    )

    console.print("[bold]Matching Strategy:[/bold]")
    console.print("  Post-2018: 8 rounds of progressively relaxed matching")
    console.print("  Pre-2018:  2 rounds for legacy HMDA format\n")

    # Configuration table
    config_table = Table(
        title="Configuration", show_header=True, header_style="bold magenta"
    )
    config_table.add_column("Setting", style="cyan", width=30)
    config_table.add_column("Value", style="white")

    config_table.add_row("Post-2018 Year Range", f"{MIN_YEAR_POST2018} - {MAX_YEAR_POST2018}")
    config_table.add_row("Pre-2018 Year Range", f"{MIN_YEAR_PRE2018} - {MAX_YEAR_PRE2018}")
    config_table.add_row("HMDA Silver Dir (Post-2018)", str(HMDA_SILVER_DIR_POST2018))
    config_table.add_row("HMDA Silver Dir (Pre-2018)", str(HMDA_SILVER_DIR_PRE2018))
    config_table.add_row("Crosswalk Output Dir", str(CROSSWALK_OUTPUT_DIR))
    config_table.add_row("Output Dir", str(OUTPUT_DIR))
    config_table.add_row("Intermediate Dir", str(INTERMEDIATE_DIR))

    console.print(config_table)
    console.print()


@app.command()
def status() -> None:
    """Show data availability and matching status."""
    console.print("\n[bold cyan]HMDA Sellers/Purchasers Matching Status[/bold cyan]\n")

    # Data availability table
    data_table = Table(
        title="Data Availability", show_header=True, header_style="bold magenta"
    )
    data_table.add_column("Source", style="cyan", width=20)
    data_table.add_column("Years Available", style="white", width=35)
    data_table.add_column("Status", style="white", width=10)

    # Check Post-2018 HMDA data
    post2018_years = []
    if HMDA_SILVER_DIR_POST2018.exists():
        for year_dir in sorted(HMDA_SILVER_DIR_POST2018.glob("activity_year=*")):
            year = year_dir.name.split("=")[1]
            post2018_years.append(year)
    post2018_status = "[green]OK[/green]" if post2018_years else "[red]Missing[/red]"
    data_table.add_row("HMDA Post-2018", ", ".join(post2018_years) if post2018_years else "None", post2018_status)

    # Check Pre-2018 HMDA data
    pre2018_years = []
    if HMDA_SILVER_DIR_PRE2018.exists():
        for year_dir in sorted(HMDA_SILVER_DIR_PRE2018.glob("activity_year=*")):
            year = year_dir.name.split("=")[1]
            pre2018_years.append(year)
    pre2018_status = "[green]OK[/green]" if pre2018_years else "[yellow]Not found[/yellow]"
    data_table.add_row("HMDA Pre-2018", ", ".join(pre2018_years) if pre2018_years else "None", pre2018_status)

    console.print(data_table)
    console.print()

    # Match output status table
    output_table = Table(
        title="Match Outputs (Post-2018)", show_header=True, header_style="bold magenta"
    )
    output_table.add_column("Round", style="cyan", width=15)
    output_table.add_column("Output Directory", style="white", width=50)
    output_table.add_column("Status", style="white", width=10)

    post2018_output_dir = CROSSWALK_OUTPUT_DIR / "post2018"
    for round_num in range(1, 9):
        round_dir = post2018_output_dir / f"MatchRound={round_num}"
        if round_dir.exists() and any(round_dir.glob("*.parquet")):
            status = "[green]Exists[/green]"
        else:
            status = "[yellow]Not created[/yellow]"
        output_table.add_row(f"Round {round_num}", str(round_dir), status)

    console.print(output_table)
    console.print()


@app.command()
def run(
    round_num: Annotated[
        int | None,
        typer.Option("--round", "-r", help="Specific round to run (1-8 for post2018, 1-2 for pre2018)"),
    ] = None,
    min_year: Annotated[
        int | None,
        typer.Option("--min-year", "-m", help="Minimum year to match"),
    ] = None,
    max_year: Annotated[
        int | None,
        typer.Option("--max-year", "-M", help="Maximum year to match"),
    ] = None,
    pre2018: Annotated[
        bool,
        typer.Option("--pre2018", help="Run pre-2018 matching instead of post-2018"),
    ] = False,
    data_dir: Annotated[
        Path | None,
        typer.Option("--data-dir", help="Override HMDA silver data directory"),
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", "-o", help="Override output directory"),
    ] = None,
) -> None:
    """Run the HMDA Sellers/Purchasers matching pipeline.

    This command runs the matching workflow which links HMDA loan originations
    with their subsequent purchases using a multi-round matching strategy.

    Post-2018 rounds:
      1. Same-year exact matches with strict tolerances
      2. Cross-year matches on remaining unmatched records
      3. Relaxed uniqueness (allow one-to-many for secondary sales)
      4. Match without loan purpose, using purchase indicator instead
      5. Allow small loan amount mismatches
      6. Affiliate-only matches using known relationships
      7. Purchaser-type-constrained matches
      8. Include originations with non-zero purchaser_type, looser tolerances
    """
    from mortgage_data_manager.matching.match_hmda_sellers_and_purchasers import (
        run_round,
    )

    # Set defaults based on period
    if pre2018:
        min_year = min_year or MIN_YEAR_PRE2018
        max_year = max_year or MAX_YEAR_PRE2018
        data_dir = data_dir or HMDA_SILVER_DIR_PRE2018
        console.print("\n[bold cyan]Running Pre-2018 HMDA Sellers/Purchasers Matching[/bold cyan]")
        console.print("[yellow]Note: Pre-2018 matching is not yet fully implemented in CLI.[/yellow]\n")
        raise typer.Exit(code=1)
    else:
        min_year = min_year or MIN_YEAR_POST2018
        max_year = max_year or MAX_YEAR_POST2018
        data_dir = data_dir or HMDA_SILVER_DIR_POST2018

    output_dir = output_dir or CROSSWALK_OUTPUT_DIR / "post2018"
    SellersAndPurchasersMatchingConfig.ensure_directories()

    console.print("\n[bold cyan]Running HMDA Sellers/Purchasers Matching Pipeline[/bold cyan]")
    console.print(f"  Years: {min_year} - {max_year}")
    console.print(f"  Data dir: {data_dir}")
    console.print(f"  Output dir: {output_dir}")
    if round_num:
        console.print(f"  Running only round: {round_num}")
    console.print()

    rounds_to_run = [round_num] if round_num else list(range(1, 9))

    try:
        for r in rounds_to_run:
            if r < 1 or r > 8:
                console.print(f"[red]Error: Invalid round {r}. Must be 1-8.[/red]")
                raise typer.Exit(code=1)

            console.print(f"[cyan]Running Round {r}...[/cyan]")
            run_round(
                r,
                str(data_dir),
                str(output_dir),
                min_year=min_year,
                max_year=max_year,
            )
            console.print(f"[green]Round {r} complete.[/green]\n")

        console.print("\n[bold green]Matching complete![/bold green]")
        console.print(f"Output directory: {output_dir}\n")

    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1)


@app.command()
def validate(
    show_plots: bool = typer.Option(
        False,
        "--show-plots",
        "-s",
        help="Show plots interactively",
    ),
    save_plots: bool = typer.Option(
        True,
        "--save/--no-save",
        help="Save plots to docs/matching/figures/hmda_sellers_purchasers/",
    ),
) -> None:
    """Run validation analysis on HMDA Sellers/Purchasers matches.

    Generates diagnostic figures analyzing match quality:
    - Match statistics by round
    - Matches by year
    - Cross-year patterns heatmap
    - Match rates by loan amount, purpose, type, and purchaser type

    Examples:
      mortgage-data match sellers-purchasers validate
      mortgage-data match sellers-purchasers validate --show-plots
      mortgage-data match sellers-purchasers validate --no-save
    """
    from mortgage_data_manager.matching.match_hmda_sellers_and_purchasers.validation import (
        run_validation,
    )

    console.print("[cyan]Running HMDA Sellers/Purchasers validation analysis...[/cyan]")

    try:
        results = run_validation(save_plots=save_plots, show_plots=show_plots)

        if "error" in results:
            console.print(f"[red]Validation error: {results['error']}[/red]")
            raise typer.Exit(code=1)

        console.print("[green]Validation complete![/green]")

    except Exception as e:
        console.print(f"[red]Validation failed: {e}[/red]")
        raise typer.Exit(code=1)


def cli_main() -> None:
    """Entry point for HMDA Sellers/Purchasers matching CLI."""
    app()


if __name__ == "__main__":
    cli_main()
