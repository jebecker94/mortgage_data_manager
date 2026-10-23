"""HMDA-FHLB Matching CLI.

Typer-based command-line interface for HMDA-FHLB matching workflow.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import typer
from rich.console import Console
from rich.table import Table

from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.matching.match_hmda_fhlb.config import HMDAFHLBMatchingConfig

if TYPE_CHECKING:
    from mortgage_data_manager.matching.match_hmda_fhlb.pipeline import Step

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
        2025,
        "--max-year",
        "-M",
        help="Maximum year to process",
    ),
    pre2018: bool = typer.Option(
        True,
        "--pre2018/--no-pre2018",
        help="Run the pre-2018 era",
    ),
    post2018: bool = typer.Option(
        True,
        "--post2018/--no-post2018",
        help="Run the post-2018 era",
    ),
    step: str = typer.Option(
        "all",
        "--step",
        help="all, or only prepare the inputs, or only match the prepared inputs",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Rebuild prepared inputs that already exist",
    ),
    match_dir: Path | None = typer.Option(
        None,
        "--match-dir",
        "-d",
        help="Directory for prepared inputs and match results (default: the hmda_fhlb "
        "matching dir from config)",
    ),
):
    """Run the HMDA-FHLB matcher.

    Per era: 1. extract the FHLB member list from the Avery lender panels, 2. clean the
    FHLB acquisitions, 3. clean the members' HMDA loans (steps 1-3 are --step prepare),
    then 4. validate the inputs (post-2018), 5. match, 6. report match statistics
    (steps 4-6 are --step match).

    Examples:
      mortgage-data match hmda-fhlb run
      mortgage-data match hmda-fhlb run --max-year 2023 --no-pre2018
      mortgage-data match hmda-fhlb run --step prepare --overwrite
      mortgage-data match hmda-fhlb run --step match --no-pre2018
    """
    from mortgage_data_manager.matching.match_hmda_fhlb import run_matching

    if not pre2018 and not post2018:
        console.print("[red]Error: Must run at least one of pre2018 or post2018[/red]")
        raise typer.Exit(code=1)
    if step not in ("all", "prepare", "match"):
        console.print(f"[red]Invalid step '{step}'. Use all, prepare, or match.[/red]")
        raise typer.Exit(code=1)

    try:
        console.print(f"[cyan]Running HMDA-FHLB matching ({step}, max year {max_year})...[/cyan]")
        run_matching(
            max_year,
            pre2018=pre2018,
            post2018=post2018,
            step=cast("Step", step),
            overwrite=overwrite,
            match_folder=match_dir,
        )
        console.print("[green]HMDA-FHLB matching complete![/green]")
    except Exception as e:
        console.print(f"[red]HMDA-FHLB matching failed: {e}[/red]")
        logger.exception("HMDA-FHLB matching error")
        raise typer.Exit(code=1)


@app.command()
def validate(
    show: bool = typer.Option(
        False,
        "--show",
        "-s",
        help="Show the figure interactively",
    ),
    save: bool = typer.Option(
        True,
        "--save/--no-save",
        help="Save the figure to docs/matching/figures/",
    ),
):
    """Run validation analysis on HMDA-FHLB matches.

    Writes one combined figure, hmda_fhlb_validation.png, covering match counts by
    year, match rates by loan amount (both sides), purchaser type distribution, and
    lender concentration -- with the pre-2018 and post-2018 eras drawn together.

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
    table.add_row("  Data Directory", str(HMDAFHLBMatchingConfig.DATA_DIR))
    table.add_row("  Avery File (Post-2018)", str(HMDAFHLBMatchingConfig.AVERY_FILE_PATH))
    table.add_row("  Avery File (Pre-2018)", str(HMDAFHLBMatchingConfig.AVERY_PRE2018_FILE_PATH))

    table.add_row("", "")
    table.add_row("[bold]Matching Rounds[/bold] (rounds.py; tolerances)", "")

    from mortgage_data_manager.matching.match_hmda_fhlb.matching import LTV_FEE_TOLERANCE
    from mortgage_data_manager.matching.match_hmda_fhlb.rounds import (
        ROUNDS_POST2018,
        ROUNDS_PRE2018,
    )

    labelled = [(f"Post-2018 round {i}", r) for i, r in enumerate(ROUNDS_POST2018, 1)]
    labelled += [(f"Pre-2018 round {i}", r) for i, r in enumerate(ROUNDS_PRE2018, 1)]
    for label, round_ in labelled:
        t = round_.tolerances
        parts = [f"income ±${t.income:,.0f}"]
        parts += [f"LTV ±{t.ltv} pts"] if t.ltv is not None else []
        parts += [f"DTI ±{t.dti} pts"] if t.dti is not None else []
        parts += [f"rate ±{t.rate} pp"] if t.rate is not None else []
        parts += [f"term ±{t.term:.0f} mo"] if t.term is not None else []
        parts += [f"amount ±${t.amount:,.0f}"] if t.amount is not None else []
        table.add_row(f"  {label}: {round_.description}", ", ".join(parts))
    table.add_row("  Fee-adjusted LTV (government loans)", f"±{LTV_FEE_TOLERANCE} pts")

    console.print(table)

    # Check for input files
    console.print("\n[bold]Input File Status:[/bold]")
    if HMDAFHLBMatchingConfig.AVERY_FILE_PATH.exists():
        console.print("  [green]✓[/green] Post-2018 Avery file exists")
    else:
        console.print("  [red]✗[/red] Post-2018 Avery file not found")

    if HMDAFHLBMatchingConfig.AVERY_PRE2018_FILE_PATH.exists():
        console.print("  [green]✓[/green] Pre-2018 Avery file exists")
    else:
        console.print("  [red]✗[/red] Pre-2018 Avery file not found")


def cli_main():
    """Entry point for standalone HMDA-FHLB matching CLI."""
    app()


if __name__ == "__main__":
    cli_main()
