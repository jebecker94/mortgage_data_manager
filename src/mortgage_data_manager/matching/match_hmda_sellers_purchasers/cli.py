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

from mortgage_data_manager.core.cli import OverwriteOption
from mortgage_data_manager.core.logging import configure_logging
from mortgage_data_manager.matching.match_hmda_sellers_purchasers.config import (
    HMDASellersPurchasersMatchingConfig as Config,
)

app = typer.Typer(
    name="hmda-sellers-purchasers",
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

    from mortgage_data_manager.matching.match_hmda_sellers_purchasers.rounds import (
        ROUNDS_POST2018,
        ROUNDS_PRE2018,
    )

    console.print("[bold]Matching rounds[/bold] (rounds.py):")
    for number, round_ in enumerate(ROUNDS_POST2018, start=1):
        console.print(f"  Post-2018 round {number}: {round_.description}")
    for number, spec in enumerate(ROUNDS_PRE2018, start=1):
        keys = ", ".join(spec.match_columns)
        console.print(
            f"  Pre-2018 round {number}: {'pooled' if spec.pooled else 'by year'} on {keys}"
        )
    console.print()

    # Configuration table
    config_table = Table(title="Configuration", show_header=True, header_style="bold magenta")
    config_table.add_column("Setting", style="cyan", width=30)
    config_table.add_column("Value", style="white")

    config_table.add_row(
        "Post-2018 Year Range", f"{Config.MIN_YEAR_POST2018} - {Config.MAX_YEAR_POST2018}"
    )
    config_table.add_row(
        "Pre-2018 Year Range", f"{Config.MIN_YEAR_PRE2018} - {Config.MAX_YEAR_PRE2018}"
    )
    config_table.add_row("HMDA Silver Dir (Post-2018)", str(Config.HMDA_SILVER_DIR_POST2018))
    config_table.add_row("HMDA Silver Dir (Pre-2018)", str(Config.HMDA_SILVER_DIR_PRE2018))
    config_table.add_row("Crosswalk Output Dir", str(Config.HMDA_SELLERS_PURCHASERS_CROSSWALK_DIR))
    config_table.add_row("Output Dir", str(Config.HMDA_SELLERS_PURCHASERS_OUTPUT_DIR))
    config_table.add_row("Intermediate Dir", str(Config.HMDA_SELLERS_PURCHASERS_INTERMEDIATE_DIR))

    console.print(config_table)
    console.print()
    _print_status()


def _print_status() -> None:
    """Data availability and per-round crosswalk status, for ``info``."""
    # Data availability table
    data_table = Table(title="Data Availability", show_header=True, header_style="bold magenta")
    data_table.add_column("Source", style="cyan", width=20)
    data_table.add_column("Years Available", style="white", width=35)
    data_table.add_column("Status", style="white", width=10)

    # Check Post-2018 HMDA data
    post2018_years = []
    if Config.HMDA_SILVER_DIR_POST2018.exists():
        for year_dir in sorted(Config.HMDA_SILVER_DIR_POST2018.glob("activity_year=*")):
            year = year_dir.name.split("=")[1]
            post2018_years.append(year)
    post2018_status = "[green]OK[/green]" if post2018_years else "[red]Missing[/red]"
    data_table.add_row(
        "HMDA Post-2018", ", ".join(post2018_years) if post2018_years else "None", post2018_status
    )

    # Check Pre-2018 HMDA data
    pre2018_years = []
    if Config.HMDA_SILVER_DIR_PRE2018.exists():
        for year_dir in sorted(Config.HMDA_SILVER_DIR_PRE2018.glob("activity_year=*")):
            year = year_dir.name.split("=")[1]
            pre2018_years.append(year)
    pre2018_status = "[green]OK[/green]" if pre2018_years else "[yellow]Not found[/yellow]"
    data_table.add_row(
        "HMDA Pre-2018", ", ".join(pre2018_years) if pre2018_years else "None", pre2018_status
    )

    console.print(data_table)
    console.print()

    # Match output status table
    output_table = Table(
        title="Match Outputs (Post-2018)", show_header=True, header_style="bold magenta"
    )
    output_table.add_column("Round", style="cyan", width=15)
    output_table.add_column("Output Directory", style="white", width=50)
    output_table.add_column("Status", style="white", width=10)

    from mortgage_data_manager.matching.match_hmda_sellers_purchasers.rounds import (
        ROUNDS_POST2018,
        ROUNDS_PRE2018,
    )

    post2018_output_dir = Config.get_crosswalk_dir("post2018")
    for round_num in range(1, len(ROUNDS_POST2018) + 1):
        round_dir = post2018_output_dir / f"match_round={round_num}"
        if round_dir.exists() and any(round_dir.glob("**/*.parquet")):
            status = "[green]Exists[/green]"
        else:
            status = "[yellow]Not created[/yellow]"
        output_table.add_row(f"Round {round_num}", str(round_dir), status)

    console.print(output_table)
    console.print()

    pre2018_table = Table(
        title="Match Outputs (Pre-2018)", show_header=True, header_style="bold magenta"
    )
    pre2018_table.add_column("Round", style="cyan", width=15)
    pre2018_table.add_column("Output Directory", style="white", width=50)
    pre2018_table.add_column("Status", style="white", width=10)

    pre2018_output_dir = Config.get_crosswalk_dir("pre2018")
    for round_num in range(1, len(ROUNDS_PRE2018) + 1):
        round_dir = pre2018_output_dir / f"match_round={round_num}"
        if round_dir.exists() and any(round_dir.glob("**/*.parquet")):
            status = "[green]Exists[/green]"
        else:
            status = "[yellow]Not created[/yellow]"
        pre2018_table.add_row(f"Round {round_num}", str(round_dir), status)

    console.print(pre2018_table)
    console.print()


@app.command()
def run(
    round_num: Annotated[
        int | None,
        typer.Option(
            "--round", "-r", help="Run only this round (its position in rounds.py; see info)"
        ),
    ] = None,
    min_year: Annotated[
        int | None,
        typer.Option("--min-year", "-m", help="Minimum year to match"),
    ] = None,
    max_year: Annotated[
        int | None,
        typer.Option("--max-year", "-M", help="Maximum year to match"),
    ] = None,
    era: Annotated[
        str,
        typer.Option("--era", help="Matching era: 'post2018' (default) or 'pre2018' (2007-2017)"),
    ] = "post2018",
    data_dir: Annotated[
        Path | None,
        typer.Option("--data-dir", help="Override HMDA silver data directory"),
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", "-o", help="Override output directory"),
    ] = None,
    overwrite: OverwriteOption = False,
) -> None:
    """Run the HMDA Sellers/Purchasers matching pipeline.

    This command runs the matching workflow which links HMDA loan originations
    with their subsequent purchases using a multi-round matching strategy.

    The rounds for each era are listed in rounds.py and printed by ``info``; each runs
    on the records earlier rounds left unmatched.

    Examples:
      mortgage-data match hmda-sellers-purchasers run
      mortgage-data match hmda-sellers-purchasers run --era pre2018
      mortgage-data match hmda-sellers-purchasers run --era pre2018 -r 1 --overwrite
    """
    from mortgage_data_manager.matching.match_hmda_sellers_purchasers import (
        ROUNDS_POST2018,
        ROUNDS_PRE2018,
        run_round,
        run_round_pre2018,
    )

    if era not in ("post2018", "pre2018"):
        console.print(f"[red]Error: --era must be post2018 or pre2018, not {era!r}[/red]")
        raise typer.Exit(code=1)
    pre2018 = era == "pre2018"

    # Set defaults based on era
    if pre2018:
        min_year = min_year or Config.MIN_YEAR_PRE2018
        max_year = max_year or Config.MAX_YEAR_PRE2018
        data_dir = data_dir or Config.HMDA_SILVER_DIR_PRE2018
        output_dir = output_dir or Config.get_crosswalk_dir("pre2018")
        valid_rounds = list(range(1, len(ROUNDS_PRE2018) + 1))
    else:
        min_year = min_year or Config.MIN_YEAR_POST2018
        max_year = max_year or Config.MAX_YEAR_POST2018
        data_dir = data_dir or Config.HMDA_SILVER_DIR_POST2018
        output_dir = output_dir or Config.get_crosswalk_dir("post2018")
        valid_rounds = list(range(1, len(ROUNDS_POST2018) + 1))

    Config.ensure_directories()

    label = "Pre-2018" if pre2018 else "Post-2018"
    console.print(f"\n[bold cyan]Running {label} HMDA Sellers/Purchasers Matching[/bold cyan]")
    console.print(f"  Years: {min_year} - {max_year}")
    console.print(f"  Data dir: {data_dir}")
    console.print(f"  Output dir: {output_dir}")
    if round_num:
        console.print(f"  Running only round: {round_num}")
    console.print()

    rounds_to_run = [round_num] if round_num else valid_rounds
    pairs_total = 0

    try:
        for r in rounds_to_run:
            if r not in valid_rounds:
                console.print(f"[red]Error: Invalid round {r}. Valid rounds: {valid_rounds}[/red]")
                raise typer.Exit(code=1)

            console.print(f"[cyan]Running Round {r}...[/cyan]")
            if pre2018:
                pairs = run_round_pre2018(
                    r,
                    data_folder=data_dir,
                    save_folder=output_dir,
                    min_year=min_year,
                    max_year=max_year,
                    overwrite=overwrite,
                )
                pairs_total += pairs
                console.print(f"[green]Round {r} complete: {pairs:,} pairs.[/green]\n")
            else:
                run_round(
                    r,
                    str(data_dir),
                    str(output_dir),
                    min_year=min_year,
                    max_year=max_year,
                )
                console.print(f"[green]Round {r} complete.[/green]\n")

        if pre2018 and pairs_total == 0:
            console.print("[red]No pairs matched.[/red]")
            raise typer.Exit(code=1)

        console.print("\n[bold green]Matching complete![/bold green]")
        console.print(f"Output directory: {output_dir}\n")

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1)


@app.command()
def validate(
    show_plots: bool = typer.Option(
        False,
        "--show-plots",
        "-s",
        help="Show the figure interactively",
    ),
    save_plots: bool = typer.Option(
        True,
        "--save/--no-save",
        help="Save the figure to docs/matching/figures/",
    ),
) -> None:
    """Run validation analysis on HMDA Sellers/Purchasers matches.

    Writes one combined figure, hmda_sellers_purchasers_validation.png, covering
    match counts by round and cumulative coverage, matches by origination year,
    same-year vs cross-year patterns, purchaser-type volume and match rates, and
    match rates by loan amount, loan type and loan purpose.

    Examples:
      mortgage-data match hmda-sellers-purchasers validate
      mortgage-data match hmda-sellers-purchasers validate --show-plots
      mortgage-data match hmda-sellers-purchasers validate --no-save
    """
    from mortgage_data_manager.matching.match_hmda_sellers_purchasers.validation import (
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
