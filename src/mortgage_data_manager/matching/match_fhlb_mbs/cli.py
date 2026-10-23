"""FHLB-MBS Matching CLI.

Typer-based command-line interface for FHLB-MBS matching workflows.
Matches MBS loan-level data (GNMA) to FHLB AMA acquisition data.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.matching.match_fhlb_mbs.config import FHLBMBSMatchingConfig as Config

# Create Typer app
app = typer.Typer(
    name="fhlb-mbs",
    help="FHLB-MBS matching workflow commands (FHLB AMA ↔ GNMA loan level)",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()
logger = get_logger(__name__)


@app.callback()
def callback():
    """[bold cyan]FHLB-MBS Matching Workflow[/bold cyan].

    Tools for matching MBS loan-level disclosure data to FHLB AMA acquisition data.
    Matches GNMA (Ginnie Mae) loan-level data to FHLB AMA acquisitions.
    """


@app.command()
def run(
    min_year: int = typer.Option(
        Config.GNMA_START_YEAR,
        "--min-year",
        "-m",
        help="Minimum year for data (based on origination date)",
    ),
    max_year: int = typer.Option(
        Config.GNMA_END_YEAR,
        "--max-year",
        "-M",
        help="Maximum year for data",
    ),
    issuer_id: int = typer.Option(
        3937,
        "--issuer-id",
        "-i",
        help="GNMA issuer ID to filter (use 0 for all issuers)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed progress output",
    ),
):
    """Match FHLB AMA acquisitions to GNMA loan-level disclosures (dailyllmni silver).

    GNMA is the only MBS side: AMA acquisitions do not appear in FNMA's loan-level
    disclosures.

    Output: crosswalk/fhlb_mbs/fhlb_mbs_crosswalk_gnma.parquet.

    Examples:
      mortgage-data match fhlb-mbs run
      mortgage-data match fhlb-mbs run --min-year 2015 --max-year 2020
      mortgage-data match fhlb-mbs run --issuer-id 0  # all issuers
    """
    from mortgage_data_manager.matching.match_fhlb_mbs.matching import run_matching

    configure_logging(level="DEBUG" if verbose else "INFO")

    try:
        console.print("[cyan]Running FHLB-GNMA matching...[/cyan]")
        console.print(f"  Year range: {min_year}-{max_year}")
        console.print(f"  Issuer ID: {issuer_id if issuer_id != 0 else 'all'}")

        # Convert issuer_id 0 to None for "all issuers"
        issuer = issuer_id if issuer_id != 0 else None

        df = run_matching(
            min_year=min_year,
            max_year=max_year,
            issuer_id=issuer,
        )

        console.print("\n[green]FHLB-GNMA matching complete![/green]")
        console.print(f"  Total matches: {len(df):,}")
        console.print(
            f"  Crosswalk saved to: {Config.FHLB_MBS_CROSSWALK_DIR / 'fhlb_mbs_crosswalk_gnma.parquet'}"
        )

    except Exception as e:
        console.print(f"[red]FHLB-GNMA matching failed: {e}[/red]")
        logger.exception("FHLB-GNMA matching error")
        raise typer.Exit(code=1)


@app.command()
def info():
    """Display FHLB-MBS matching configuration and data availability.

    Shows configured paths and checks for available data files.

    Examples:
      mortgage-data match fhlb-mbs info
    """
    table = Table(
        title="FHLB-MBS Matching Configuration", show_header=True, header_style="bold cyan"
    )
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("[bold]Data Directory[/bold]", str(Config.FHLB_MBS_MATCHING_DIR))
    table.add_row("[bold]Intermediate Directory[/bold]", str(Config.FHLB_MBS_INTERMEDIATE_DIR))
    table.add_row("[bold]Output Directory[/bold]", str(Config.FHLB_MBS_CROSSWALK_DIR))

    table.add_row("", "")
    table.add_row("[bold]GNMA Paths[/bold]", "")
    table.add_row("  Silver dailyllmni/L", str(Config.get_gnma_silver_dir()))

    table.add_row("", "")
    table.add_row("[bold]FHLB Paths[/bold]", "")
    table.add_row("  Silver AMA", str(Config.get_fhlb_ama_silver_dir()))

    from mortgage_data_manager.matching.match_fhlb_mbs.rounds import ROUNDS, Tolerances

    def _tolerances(t: Tolerances) -> str:
        term = f", term {t.term}mo" if t.term is not None else ""
        return f"rate {t.rate}, balance ${t.balance:,.0f}, LTV {t.ltv}, DTI {t.dti}{term}"

    table.add_row("", "")
    table.add_row("[bold]Matching Rounds[/bold] (rounds.py)", "")
    for number, rnd in enumerate(ROUNDS, start=1):
        table.add_row(f"  Round {number}", rnd.description)
        table.add_row("    keys", ", ".join(f"{g}={f}" for g, f in rnd.keys))
        table.add_row("    candidate", _tolerances(rnd.candidate))
        table.add_row("    post-match", _tolerances(rnd.post) if rnd.post else "none")

    console.print(table)

    # Check data availability
    console.print("\n[bold]Data Availability:[/bold]")

    def check_parquet_files(path: Path, label: str):
        if path.exists():
            files = list(path.glob("*.parquet"))
            if files:
                console.print(f"  [green]✓[/green] {label}: {len(files)} files")
            else:
                console.print(
                    f"  [yellow]![/yellow] {label}: directory exists but no parquet files"
                )
        else:
            console.print(f"  [red]✗[/red] {label}: not found")

    check_parquet_files(Config.get_gnma_silver_dir(), "GNMA Silver L")
    check_parquet_files(Config.get_fhlb_ama_silver_dir(), "FHLB AMA Silver")

    # Check output files
    console.print("\n[bold]Output Files:[/bold]")

    # FHLB-GNMA outputs
    gnma_crosswalk = Config.FHLB_MBS_CROSSWALK_DIR / "fhlb_mbs_crosswalk_gnma.parquet"
    gnma_details = Config.FHLB_MBS_INTERMEDIATE_DIR / "fhlb_mbs_match_details_gnma.parquet"

    if gnma_crosswalk.exists():
        import polars as pl

        df = pl.read_parquet(gnma_crosswalk)
        console.print(f"  [green]✓[/green] FHLB-GNMA crosswalk: {len(df):,} matches")
    else:
        console.print("  [yellow]![/yellow] FHLB-GNMA crosswalk: not yet generated")

    if gnma_details.exists():
        console.print("  [green]✓[/green] FHLB-GNMA match details: exists")
    else:
        console.print("  [yellow]![/yellow] FHLB-GNMA match details: not yet generated")


@app.command()
def validate(
    min_year: int = typer.Option(2018, "--min-year", help="First year to include"),
    max_year: int = typer.Option(2025, "--max-year", help="Last year to include"),
    issuer_id: int = typer.Option(
        3937, "--issuer-id", "-i", help="GNMA issuer ID filter (use 0 for all)"
    ),
    show_plots: bool = typer.Option(False, "--show-plots", "-s", help="Show plots interactively"),
    save_plots: bool = typer.Option(
        True, "--save/--no-save", help="Save plots to docs/matching/figures/"
    ),
    output_dir: Path | None = typer.Option(
        None, "--output-dir", "-o", help="Output directory for figures"
    ),
):
    """Run validation analysis on FHLB-GNMA matches.

    Writes one combined figure, fhlb_mbs_validation.png, covering match rates by
    origination year, loan amount, interest rate, LTV and mortgage type, plus the
    four match-quality difference distributions. The state match-rate map stays a
    separate file (plotly, so it cannot share the grid).

    Examples:
      mortgage-data match fhlb-mbs validate
      mortgage-data match fhlb-mbs validate --show-plots
      mortgage-data match fhlb-mbs validate --min-year 2020 --max-year 2023
    """
    from mortgage_data_manager.matching.match_fhlb_mbs.validation import run_validation

    configure_logging(level="INFO")

    issuer = issuer_id if issuer_id else None

    try:
        console.print("[cyan]Running FHLB-GNMA validation...[/cyan]")
        results = run_validation(
            min_year=min_year,
            max_year=max_year,
            issuer_id=issuer,
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
        logger.exception("Validation error")
        raise typer.Exit(code=1)


def cli_main():
    """Entry point for standalone FHLB-MBS matching CLI."""
    app()


if __name__ == "__main__":
    cli_main()
