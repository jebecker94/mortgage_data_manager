"""CLI for FHA-GNMA matching workflow.

This module provides command-line access to the FHA-GNMA matching pipeline
for linking FHA endorsement records to GNMA loan-level disclosure data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from mortgage_data_manager.core.cli.download_options import QuietOption
from mortgage_data_manager.core.logging import configure_logging
from mortgage_data_manager.matching.match_fha_gnma.config import (
    CROSSWALK_OUTPUT_DIR,
    FHA_SILVER_DIR,
    GNMA_SILVER_DIR,
    INTERMEDIATE_DIR,
    MAX_YEAR,
    MIN_YEAR,
    OUTPUT_DIR,
    PILOT_STATE,
)

app = typer.Typer(
    name="fha-gnma",
    help="FHA-GNMA matching workflow for linking FHA loans to GNMA disclosure data",
    no_args_is_help=True,
)

console = Console()


@app.callback()
def callback():
    """[bold cyan]FHA-GNMA Matching Workflow[/bold cyan].

    Tools for matching FHA endorsement records with GNMA loan-level disclosure data using
    probabilistic matching on loan characteristics.
    """
    configure_logging(level="INFO")


@app.command()
def info() -> None:
    """Show information about FHA-GNMA matching configuration and data availability."""
    console.print("\n[bold cyan]FHA-GNMA Matching Workflow[/bold cyan]\n")

    console.print(
        "This workflow matches FHA (Federal Housing Administration) endorsement records "
        "with GNMA (Ginnie Mae) loan-level disclosure data using probabilistic matching.\n"
    )

    console.print("[bold]Key characteristics:[/bold]")
    console.print("  - GNMA lacks FHA Case Numbers, so matching uses loan characteristics")
    console.print("  - GNMA truncates loan amounts to thousands (floor, not round)")
    console.print("  - Multi-round matching with strict then relaxed tolerances")
    console.print("  - Match rates vary by state size (r = -0.907 correlation)\n")

    # Configuration table
    config_table = Table(title="Configuration", show_header=True, header_style="bold magenta")
    config_table.add_column("Setting", style="cyan", width=25)
    config_table.add_column("Value", style="white")

    config_table.add_row("Min Year", str(MIN_YEAR))
    config_table.add_row("Max Year", str(MAX_YEAR))
    config_table.add_row("Pilot State", str(PILOT_STATE))
    config_table.add_row("FHA Silver Dir", str(FHA_SILVER_DIR))
    config_table.add_row("GNMA Silver Dir", str(GNMA_SILVER_DIR))
    config_table.add_row("Crosswalk Output Dir", str(CROSSWALK_OUTPUT_DIR))
    config_table.add_row("Output Dir", str(OUTPUT_DIR))
    config_table.add_row("Intermediate Dir", str(INTERMEDIATE_DIR))

    console.print(config_table)
    console.print()

    # Data availability table
    data_table = Table(title="Data Availability", show_header=True, header_style="bold magenta")
    data_table.add_column("Source", style="cyan", width=15)
    data_table.add_column("Years Available", style="white", width=30)
    data_table.add_column("Status", style="white", width=10)

    # Check FHA data
    fha_years = []
    if FHA_SILVER_DIR.exists():
        for year_dir in sorted(FHA_SILVER_DIR.glob("Year=*")):
            year = year_dir.name.split("=")[1]
            fha_years.append(year)
    fha_status = "[green]OK[/green]" if fha_years else "[red]Missing[/red]"
    data_table.add_row("FHA Silver", ", ".join(fha_years) if fha_years else "None", fha_status)

    # Check GNMA data
    gnma_files = list(GNMA_SILVER_DIR.glob("*.parquet")) if GNMA_SILVER_DIR.exists() else []
    gnma_status = "[green]OK[/green]" if gnma_files else "[red]Missing[/red]"
    data_table.add_row(
        "GNMA Silver",
        f"{len(gnma_files)} files" if gnma_files else "None",
        gnma_status,
    )

    console.print(data_table)
    console.print()

    # Output files
    output_table = Table(title="Output Files", show_header=True, header_style="bold magenta")
    output_table.add_column("File", style="cyan", width=50)
    output_table.add_column("Status", style="white", width=10)

    output_file = CROSSWALK_OUTPUT_DIR / f"fha_gnma_crosswalk_{MIN_YEAR}_{MAX_YEAR}.parquet"
    output_status = (
        "[green]Exists[/green]" if output_file.exists() else "[yellow]Not created[/yellow]"
    )
    output_table.add_row(str(output_file.name), output_status)

    intermediate_fha = INTERMEDIATE_DIR / f"fha_prepared_{MIN_YEAR}_{MAX_YEAR}.parquet"
    intermediate_gnma = INTERMEDIATE_DIR / f"gnma_prepared_{MIN_YEAR}_{MAX_YEAR}.parquet"
    fha_intermediate_status = (
        "[green]Exists[/green]" if intermediate_fha.exists() else "[yellow]Not created[/yellow]"
    )
    gnma_intermediate_status = (
        "[green]Exists[/green]" if intermediate_gnma.exists() else "[yellow]Not created[/yellow]"
    )
    output_table.add_row(f"fha_prepared_{MIN_YEAR}_{MAX_YEAR}.parquet", fha_intermediate_status)
    output_table.add_row(f"gnma_prepared_{MIN_YEAR}_{MAX_YEAR}.parquet", gnma_intermediate_status)

    console.print(output_table)
    console.print()

    # Expected results summary
    console.print("[bold]Expected Results (2015-2024, All States):[/bold]")
    console.print("  - Total matches: ~3.9M")
    console.print("  - FHA match rate: ~35.8%")
    console.print("  - GNMA match rate: ~33.7%")
    console.print("  - Round 1: ~89%, Round 2: ~11%\n")


@app.command()
def run(
    min_year: Annotated[
        int,
        typer.Option("--min-year", "-m", help="Minimum year to match"),
    ] = MIN_YEAR,
    max_year: Annotated[
        int,
        typer.Option("--max-year", "-M", help="Maximum year to match"),
    ] = MAX_YEAR,
    state: Annotated[
        str | None,
        typer.Option("--state", "-s", help="State to filter (2-char code, e.g., 'DC', 'CA')"),
    ] = None,
    all_states: Annotated[
        bool,
        typer.Option("--all-states", "-a", help="Run for all states (no state filter)"),
    ] = False,
    skip_data_prep: Annotated[
        bool,
        typer.Option("--skip-prep", help="Skip data preparation if intermediate files exist"),
    ] = False,
    quiet: QuietOption = False,
) -> None:
    """Run the FHA-GNMA matching pipeline.

    This command runs the full matching pipeline which:
    1. Prepares FHA endorsement data for matching
    2. Prepares GNMA dailyllmni data (filtered to FHA loans)
    3. Runs Round 1 matching (strict tolerances)
    4. Runs Round 2 matching (relaxed tolerances for remaining loans)
    5. Creates crosswalk and saves match details
    """
    from mortgage_data_manager.matching.match_fha_gnma.run_pipeline import (
        run_fha_gnma_matching,
    )

    configure_logging(level="WARNING" if quiet else "INFO")

    # Determine state filter
    if all_states:
        state_filter = None
    elif state:
        state_filter = state.upper()
    else:
        state_filter = PILOT_STATE  # Default to DC pilot

    console.print("\n[bold cyan]Running FHA-GNMA Matching Pipeline[/bold cyan]")
    console.print(f"  Years: {min_year} - {max_year}")
    console.print(f"  State filter: {state_filter or 'All states'}")
    console.print(f"  Skip data prep: {skip_data_prep}\n")

    try:
        output_file = run_fha_gnma_matching(
            min_year=min_year,
            max_year=max_year,
            state_filter=state_filter,
            skip_data_prep=skip_data_prep,
        )
        console.print("\n[bold green]Matching complete![/bold green]")
        console.print(f"Output file: {output_file}\n")
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
        help="Save plots to docs/matching/figures/fha_gnma/",
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        "-o",
        help="Override output directory for figures",
    ),
) -> None:
    """Run validation analysis on FHA-GNMA matches.

    Generates diagnostic figures analyzing match quality:
    - Temporal match rates
    - State match rate map (choropleth)
    - State size bias analysis (CRITICAL: r = -0.907)
    - Blocking cell analysis
    - Match rates by loan amount, interest rate, and purpose

    Examples:
      mortgage-data match fha-gnma validate
      mortgage-data match fha-gnma validate --show-plots
      mortgage-data match fha-gnma validate --no-save
      mortgage-data match fha-gnma validate --output-dir ./figures
    """
    from mortgage_data_manager.matching.match_fha_gnma.validation import run_validation

    console.print("[cyan]Running FHA-GNMA validation analysis...[/cyan]")

    try:
        results = run_validation(
            output_dir=output_dir,
            save_plots=save_plots,
            show_plots=show_plots,
        )

        if "error" in results:
            console.print(f"[red]Validation error: {results['error']}[/red]")
            raise typer.Exit(code=1)

        console.print("[green]Validation complete![/green]")

    except Exception as e:
        console.print(f"[red]Validation failed: {e}[/red]")
        raise typer.Exit(code=1)


def cli_main() -> None:
    """Entry point for FHA-GNMA matching CLI."""
    app()


if __name__ == "__main__":
    cli_main()
