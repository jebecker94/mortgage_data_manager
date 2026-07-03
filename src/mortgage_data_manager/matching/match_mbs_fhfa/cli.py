"""MBS-FHFA Matching CLI.

Typer-based command-line interface for MBS-FHFA matching workflows.
Matches MBS loan-level data (FHLMC, FNMA) to FHFA acquisition data.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.matching.match_mbs_fhfa.config import (
    CROSSWALK_OUTPUT_DIR,
    DATA_DIR,
    HAS_FHFA,
    HAS_FHLMC,
    HAS_FNMA,
    MBSFHFAConfig,
)

# Create Typer app
app = typer.Typer(
    name="mbs-fhfa",
    help="MBS-FHFA matching workflow commands (MBS ↔ FHFA)",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()
logger = get_logger(__name__)


@app.callback()
def callback():
    """[bold cyan]MBS-FHFA Matching Workflow[/bold cyan].

    Tools for matching MBS loan-level data (FHLMC, FNMA) to FHFA acquisition data.
    Supports FHLMC (Freddie Mac) and FNMA (Fannie Mae) datasets.
    """
    configure_logging(level="INFO")


@app.command()
def fnma(
    year: int = typer.Option(
        2023,
        "--year",
        "-y",
        help="Year to match",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed progress output",
    ),
):
    """Run FNMA-FHFA matching for a single year.

    Matches FNMA (Fannie Mae) loan-level data to FHFA acquisition data.

    Examples:
      mortgage-data match mbs-fhfa fnma
      mortgage-data match mbs-fhfa fnma --year 2022
      mortgage-data match mbs-fhfa fnma --quiet
    """
    from mortgage_data_manager.matching.match_mbs_fhfa.matching_fnma import (
        run_fnma_fhfa_matching,
    )

    configure_logging(level="DEBUG" if verbose else "INFO")

    try:
        console.print(f"[cyan]Running FNMA-FHFA matching for {year}...[/cyan]")
        df = run_fnma_fhfa_matching(year=year)
        console.print("\n[green]FNMA-FHFA matching complete![/green]")
        console.print(f"  Total matches: {len(df):,}")
    except Exception as e:
        console.print(f"[red]FNMA-FHFA matching failed: {e}[/red]")
        logger.exception("FNMA-FHFA matching error")
        raise typer.Exit(code=1)


@app.command()
def fhlmc(
    year: int = typer.Option(
        2023,
        "--year",
        "-y",
        help="Year to match",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed progress output",
    ),
):
    """Run FHLMC-FHFA matching for a single year.

    Matches FHLMC (Freddie Mac) loan-level data to FHFA acquisition data.

    Examples:
      mortgage-data match mbs-fhfa fhlmc
      mortgage-data match mbs-fhfa fhlmc --year 2022
      mortgage-data match mbs-fhfa fhlmc --quiet
    """
    from mortgage_data_manager.matching.match_mbs_fhfa.matching_fhlmc import (
        run_fhlmc_fhfa_matching,
    )

    configure_logging(level="DEBUG" if verbose else "INFO")

    try:
        console.print(f"[cyan]Running FHLMC-FHFA matching for {year}...[/cyan]")
        df = run_fhlmc_fhfa_matching(year=year)
        console.print("\n[green]FHLMC-FHFA matching complete![/green]")
        console.print(f"  Total matches: {len(df):,}")
    except Exception as e:
        console.print(f"[red]FHLMC-FHFA matching failed: {e}[/red]")
        logger.exception("FHLMC-FHFA matching error")
        raise typer.Exit(code=1)


@app.command(name="all")
def all_agencies(
    min_year: int = typer.Option(
        MBSFHFAConfig.FNMA_START_YEAR,
        "--min-year",
        "-m",
        help="Start year (inclusive)",
    ),
    max_year: int = typer.Option(
        MBSFHFAConfig.FNMA_END_YEAR,
        "--max-year",
        "-M",
        help="End year (inclusive)",
    ),
    agency: str = typer.Option(
        "both",
        "--agency",
        "-a",
        help="Agency to match: fnma, fhlmc, or both",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed progress output",
    ),
):
    """Run MBS-FHFA matching for multiple years.

    Runs matching across a year range for FNMA, FHLMC, or both.
    FHLMC uses the multi-year function; FNMA runs year-by-year.

    Examples:
      mortgage-data match mbs-fhfa all
      mortgage-data match mbs-fhfa all --min-year 2020 --max-year 2023
      mortgage-data match mbs-fhfa all --agency fnma
      mortgage-data match mbs-fhfa all --agency fhlmc --quiet
    """
    from mortgage_data_manager.matching.match_mbs_fhfa.matching_fhlmc import (
        run_fhlmc_fhfa_matching_multi_year,
    )
    from mortgage_data_manager.matching.match_mbs_fhfa.matching_fnma import (
        run_fnma_fhfa_matching,
    )

    configure_logging(level="DEBUG" if verbose else "INFO")

    agency_lower = agency.lower()
    if agency_lower not in ("fnma", "fhlmc", "both"):
        console.print(f"[red]Invalid agency '{agency}'. Use fnma, fhlmc, or both.[/red]")
        raise typer.Exit(code=1)

    try:
        console.print(
            f"[bold cyan]Running MBS-FHFA matching ({agency_lower}) "
            f"for {min_year}-{max_year}...[/bold cyan]\n"
        )

        if agency_lower in ("fhlmc", "both"):
            console.print("[bold]FHLMC matching[/bold]")
            run_fhlmc_fhfa_matching_multi_year(min_year=min_year, max_year=max_year)
            console.print("[green]FHLMC matching complete.[/green]\n")

        if agency_lower in ("fnma", "both"):
            console.print("[bold]FNMA matching[/bold]")
            for year in range(min_year, max_year + 1):
                console.print(f"  [cyan]Year {year}...[/cyan]")
                run_fnma_fhfa_matching(year=year)
            console.print("[green]FNMA matching complete.[/green]\n")

        console.print("[bold green]All MBS-FHFA matching complete![/bold green]")

    except Exception as e:
        console.print(f"[red]MBS-FHFA matching failed: {e}[/red]")
        logger.exception("MBS-FHFA matching error")
        raise typer.Exit(code=1)


@app.command()
def validate(
    enterprise: str = typer.Option(
        "both",
        "--enterprise",
        "-e",
        help="Enterprise to validate: fnma, fhlmc, or both",
    ),
    fnma_min_year: int = typer.Option(
        2019,
        "--fnma-min-year",
        help="First year for FNMA validation",
    ),
    fnma_max_year: int = typer.Option(
        2024,
        "--fnma-max-year",
        help="Last year for FNMA validation",
    ),
    fhlmc_min_year: int = typer.Option(
        2019,
        "--fhlmc-min-year",
        help="First year for FHLMC validation",
    ),
    fhlmc_max_year: int = typer.Option(
        2024,
        "--fhlmc-max-year",
        help="Last year for FHLMC validation",
    ),
    show_plots: bool = typer.Option(
        False,
        "--show-plots",
        "-s",
        help="Show plots interactively",
    ),
    save_plots: bool = typer.Option(
        True,
        "--save/--no-save",
        help="Save plots to docs/matching/figures/mbs_fhfa/",
    ),
):
    """Run validation analysis on MBS-FHFA matches.

    Generates diagnostic figures analyzing match quality across
    FNMA and/or FHLMC datasets.

    Examples:
      mortgage-data match mbs-fhfa validate
      mortgage-data match mbs-fhfa validate --enterprise fnma
      mortgage-data match mbs-fhfa validate --show-plots
      mortgage-data match mbs-fhfa validate --no-save
      mortgage-data match mbs-fhfa validate --fnma-min-year 2020
    """
    from mortgage_data_manager.matching.match_mbs_fhfa.validation import (
        run_validation as _run_validation,
    )

    enterprise_lower = enterprise.lower()
    if enterprise_lower not in ("fnma", "fhlmc", "both"):
        console.print(f"[red]Invalid enterprise '{enterprise}'. Use fnma, fhlmc, or both.[/red]")
        raise typer.Exit(code=1)

    try:
        console.print(f"[cyan]Running MBS-FHFA validation ({enterprise_lower})...[/cyan]")
        results = _run_validation(
            enterprise=enterprise_lower,
            save_plots=save_plots,
            show_plots=show_plots,
            fnma_min_year=fnma_min_year,
            fnma_max_year=fnma_max_year,
            fhlmc_min_year=fhlmc_min_year,
            fhlmc_max_year=fhlmc_max_year,
        )

        if "error" in results:
            console.print(f"[red]Validation error: {results['error']}[/red]")
            raise typer.Exit(code=1)

        console.print("[green]Validation complete![/green]")

    except Exception as e:
        console.print(f"[red]Validation failed: {e}[/red]")
        logger.exception("MBS-FHFA validation error")
        raise typer.Exit(code=1)


@app.command()
def info():
    """Display MBS-FHFA matching configuration and data availability.

    Shows configured paths and checks for available data sources.

    Examples:
      mortgage-data match mbs-fhfa info
    """
    table = Table(
        title="MBS-FHFA Matching Configuration", show_header=True, header_style="bold cyan"
    )
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("[bold]Data Directory[/bold]", str(DATA_DIR))
    table.add_row("[bold]Crosswalk Output Dir[/bold]", str(CROSSWALK_OUTPUT_DIR))

    table.add_row("", "")
    table.add_row("[bold]FHLMC Availability[/bold]", "")
    if HAS_FHLMC:
        table.add_row("  Status", "[green]FHLMC module available[/green]")
    else:
        table.add_row("  Status", "[red]FHLMC module not available[/red]")

    table.add_row("", "")
    table.add_row("[bold]FNMA Availability[/bold]", "")
    if HAS_FNMA:
        table.add_row("  Status", "[green]FNMA module available[/green]")
    else:
        table.add_row("  Status", "[red]FNMA module not available[/red]")

    table.add_row("", "")
    table.add_row("[bold]FHFA Availability[/bold]", "")
    if HAS_FHFA:
        table.add_row("  Status", "[green]FHFA module available[/green]")
    else:
        table.add_row("  Status", "[red]FHFA module not available[/red]")

    console.print(table)


def cli_main():
    """Entry point for standalone MBS-FHFA matching CLI."""
    app()


if __name__ == "__main__":
    cli_main()
