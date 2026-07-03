"""CLI for FHFA-HMDA matching workflow.

This module provides command-line access to the FHFA-HMDA matching pipeline
for linking HMDA loan applications to FHFA GSE (Fannie Mae/Freddie Mac) data.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from mortgage_data_manager.core.logging import configure_logging
from mortgage_data_manager.matching.match_fhfa_hmda.config import FHFAHMDAMatchingConfig
from mortgage_data_manager.matching.match_fhfa_hmda.round_config import MAX_YEAR, MIN_YEAR

app = typer.Typer(
    name="fhfa-hmda",
    help="FHFA-HMDA matching workflow (FHFA ↔ HMDA)",
    no_args_is_help=True,
)

console = Console()


@app.callback()
def callback():
    """[bold cyan]FHFA-HMDA Matching Workflow[/bold cyan].

    Tools for matching HMDA loan applications with FHFA GSE loan data from Fannie Mae
    and Freddie Mac.
    """
    configure_logging(level="INFO")


@app.command()
def info() -> None:
    """Show workflow status and configuration."""
    console.print("\n[bold cyan]FHFA-HMDA Matching Workflow[/bold cyan]\n")

    console.print(
        "This workflow matches HMDA (Home Mortgage Disclosure Act) loan applications\n"
        "with FHFA (Federal Housing Finance Agency) GSE loan data from Fannie Mae\n"
        "and Freddie Mac.\n"
    )

    # Configuration table
    config_table = Table(title="Configuration", show_header=True, header_style="bold magenta")
    config_table.add_column("Setting", style="cyan", width=25)
    config_table.add_column("Value", style="white")

    config_table.add_row("Matching Dir", str(FHFAHMDAMatchingConfig.FHFA_HMDA_MATCHING_DIR))
    config_table.add_row("Output Dir", str(FHFAHMDAMatchingConfig.FHFA_HMDA_OUTPUT_DIR))
    config_table.add_row("Crosswalk Dir", str(FHFAHMDAMatchingConfig.FHFA_HMDA_CROSSWALK_DIR))

    console.print(config_table)
    console.print()

    # Workflow table
    workflow_table = Table(
        title="Available Workflows", show_header=True, header_style="bold magenta"
    )
    workflow_table.add_column("Workflow", style="cyan", width=25)
    workflow_table.add_column("Description", style="white")
    workflow_table.add_column("Status", style="white", width=15)

    workflow_table.add_row(
        "FHFAPost2018Workflow", "Post-2018 matching (14 rounds)", "[green]Available[/green]"
    )
    workflow_table.add_row(
        "FHFAPre2018Workflow",
        "Pre-2018 matching (2 rounds: same-year R1 + 1-yr-lag R2)",
        "[yellow]Python API only[/yellow]",
    )

    console.print(workflow_table)
    console.print()

    # Usage example
    console.print("[bold]Python API Usage:[/bold]")
    console.print("  from mortgage_data_manager.matching.match_fhfa_hmda import (")
    console.print("      FHFAPost2018Workflow,")
    console.print("      FHFAPre2018Workflow,")
    console.print("  )")
    console.print()
    console.print("  # Post-2018 workflow")
    console.print("  workflow = FHFAPost2018Workflow(data_folder, match_folder)")
    console.print("  workflow.run_all_rounds()")
    console.print()

    console.print("[bold]Legacy Reference:[/bold]")
    console.print("  See the legacy/ folder for original Pandas implementations.")
    console.print()


@app.command()
def status() -> None:
    """Check data availability and pipeline status."""
    console.print("\n[bold cyan]FHFA-HMDA Pipeline Status[/bold cyan]\n")

    # Check directories
    dir_table = Table(title="Directory Status", show_header=True, header_style="bold magenta")
    dir_table.add_column("Directory", style="cyan", width=20)
    dir_table.add_column("Path", style="white", width=50)
    dir_table.add_column("Status", style="white", width=10)

    matching_dir = FHFAHMDAMatchingConfig.FHFA_HMDA_MATCHING_DIR
    matching_status = (
        "[green]Exists[/green]" if matching_dir.exists() else "[yellow]Not created[/yellow]"
    )
    dir_table.add_row("Matching", str(matching_dir), matching_status)

    output_dir = FHFAHMDAMatchingConfig.FHFA_HMDA_OUTPUT_DIR
    output_status = (
        "[green]Exists[/green]" if output_dir.exists() else "[yellow]Not created[/yellow]"
    )
    dir_table.add_row("Output", str(output_dir), output_status)

    crosswalk_dir = FHFAHMDAMatchingConfig.FHFA_HMDA_CROSSWALK_DIR
    crosswalk_status = (
        "[green]Exists[/green]" if crosswalk_dir.exists() else "[yellow]Not created[/yellow]"
    )
    dir_table.add_row("Crosswalk", str(crosswalk_dir), crosswalk_status)

    console.print(dir_table)
    console.print()

    console.print("[yellow]Note: Pre-2018 workflow available via Python API.[/yellow]")
    console.print("Use 'mortgage-data match fhfa-hmda run' for post-2018 matching.\n")


@app.command()
def run(
    min_year: int = typer.Option(
        MIN_YEAR,
        "--min-year",
        "-m",
        help="Start year (inclusive)",
    ),
    max_year: int = typer.Option(
        MAX_YEAR,
        "--max-year",
        "-M",
        help="End year (inclusive)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed progress output",
    ),
    save: bool = typer.Option(
        True,
        "--save/--no-save",
        help="Save crosswalk outputs to disk",
    ),
) -> None:
    """Run FHFA-HMDA post-2018 matching workflow.

    Matches FHFA GSE loan-level data to HMDA records using a
    multi-round matching strategy (14 rounds).

    Examples:
      mortgage-data match fhfa-hmda run
      mortgage-data match fhfa-hmda run --min-year 2020 --max-year 2023
      mortgage-data match fhfa-hmda run --verbose
      mortgage-data match fhfa-hmda run --no-save
    """
    from mortgage_data_manager.matching.match_fhfa_hmda import run_matching

    configure_logging(level="DEBUG" if verbose else "INFO")

    years = list(range(min_year, max_year + 1))

    try:
        console.print(f"[cyan]Running FHFA-HMDA matching for {min_year}-{max_year}...[/cyan]")
        stats = run_matching(years=years, save=save)

        console.print("\n[green]FHFA-HMDA matching complete![/green]")
        if isinstance(stats, dict):
            for key, value in stats.items():
                console.print(f"  {key}: {value}")

    except Exception as e:
        console.print(f"[red]FHFA-HMDA matching failed: {e}[/red]")
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
        help="Save plots to docs/matching/figures/fhfa_hmda/",
    ),
) -> None:
    """Run validation analysis on FHFA-HMDA matches.

    Generates diagnostic figures analyzing match quality:
    - Temporal match rates
    - Enterprise breakdown (FNMA vs FHLMC)
    - State-level match rate map
    - State size correlation
    - Match rates by loan amount and interest rate

    Examples:
      mortgage-data match fhfa-hmda validate
      mortgage-data match fhfa-hmda validate --show-plots
      mortgage-data match fhfa-hmda validate --no-save
    """
    from mortgage_data_manager.matching.match_fhfa_hmda.validation import run_validation

    try:
        console.print("[cyan]Running FHFA-HMDA validation analysis...[/cyan]")

        results = run_validation(save_plots=save_plots, show_plots=show_plots)

        if "error" in results:
            console.print(f"[red]Validation error: {results['error']}[/red]")
            raise typer.Exit(code=1)

        console.print("[green]Validation complete![/green]")

    except Exception as e:
        console.print(f"[red]Validation failed: {e}[/red]")
        raise typer.Exit(code=1)


def cli_main() -> None:
    """Entry point for FHFA-HMDA matching CLI."""
    app()


if __name__ == "__main__":
    cli_main()
