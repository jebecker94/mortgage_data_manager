"""MBS-FHLB Matching CLI.

Typer-based command-line interface for MBS-FHLB matching workflows.
Matches MBS loan-level data (GNMA, FNMA) to FHLB AMA acquisition data.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.matching.match_mbs_fhlb.config import (
    CROSSWALK_OUTPUT_DIR,
    DATA_DIR,
    HAS_FHLB,
    HAS_GNMA,
    HAS_UMBS,
    INTERMEDIATE_DIR,
    MBSFHLBConfig,
    get_fhlb_ama_silver_dir,
    get_gnma_silver_dir,
    get_umbs_fnma_illd_bronze_dir,
)

# Create Typer app
app = typer.Typer(
    name="mbs-fhlb",
    help="MBS-FHLB matching workflow commands (MBS ↔ FHLB AMA)",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()
logger = get_logger(__name__)


@app.callback()
def callback():
    """[bold cyan]MBS-FHLB Matching Workflow[/bold cyan].

    Tools for matching MBS loan-level disclosure data to FHLB AMA acquisition data.
    Supports GNMA (Ginnie Mae) and FNMA (Fannie Mae) datasets.
    """


@app.command()
def gnma(
    min_year: int = typer.Option(
        MBSFHLBConfig.GNMA_START_YEAR,
        "--min-year",
        "-m",
        help="Minimum year for data (based on origination date)",
    ),
    max_year: int = typer.Option(
        MBSFHLBConfig.GNMA_END_YEAR,
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
    """Run GNMA-FHLB matching.

    Matches GNMA loan-level disclosure data (dailyllmni silver) to
    FHLB AMA (Acquired Member Assets) data using loan characteristics.

    Output: gnma_fhlb_crosswalk.parquet with matched loan IDs.

    Examples:
      mortgage-data match mbs-fhlb gnma
      mortgage-data match mbs-fhlb gnma --min-year 2015 --max-year 2020
      mortgage-data match mbs-fhlb gnma --issuer-id 0  # all issuers
    """
    from mortgage_data_manager.matching.match_mbs_fhlb.gnma_match import run_gnma_fhlb_matching

    configure_logging(level="DEBUG" if verbose else "INFO")

    try:
        console.print("[cyan]Running GNMA-FHLB matching...[/cyan]")
        console.print(f"  Year range: {min_year}-{max_year}")
        console.print(f"  Issuer ID: {issuer_id if issuer_id != 0 else 'all'}")

        # Convert issuer_id 0 to None for "all issuers"
        issuer = issuer_id if issuer_id != 0 else None

        df = run_gnma_fhlb_matching(
            min_year=min_year,
            max_year=max_year,
            issuer_id=issuer,
        )

        console.print("\n[green]GNMA-FHLB matching complete![/green]")
        console.print(f"  Total matches: {len(df):,}")
        console.print(
            f"  Crosswalk saved to: {CROSSWALK_OUTPUT_DIR / 'gnma_fhlb_crosswalk.parquet'}"
        )

    except Exception as e:
        console.print(f"[red]GNMA-FHLB matching failed: {e}[/red]")
        logger.exception("GNMA-FHLB matching error")
        raise typer.Exit(code=1)


@app.command()
def fnma(
    min_year: int = typer.Option(
        MBSFHLBConfig.FNMA_START_YEAR,
        "--min-year",
        "-m",
        help="Minimum year for data (default: 2019, when UMBS ILLD starts)",
    ),
    max_year: int = typer.Option(
        MBSFHLBConfig.FNMA_END_YEAR,
        "--max-year",
        "-M",
        help="Maximum year for data",
    ),
    seller_filter: str = typer.Option(
        "FEDERAL HOME LOAN BANK",
        "--seller-filter",
        "-s",
        help="Filter FNMA data to sellers containing this string",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed progress output",
    ),
):
    """Run FNMA-FHLB matching (mutual exclusivity test).

    Tests whether loans sold to FNMA via UMBS (with FHLB as seller) appear
    in FHLB AMA direct acquisitions. Expected result: near-zero matches,
    confirming these are mutually exclusive populations.

    Data sources:
      - FNMA: UMBS ILLD bronze data (filtered by seller name)
      - FHLB: AMA silver data (conventional loans only)

    Output: fnma_fhlb_crosswalk.parquet with matched loan IDs (expect empty).

    Examples:
      mortgage-data match mbs-fhlb fnma
      mortgage-data match mbs-fhlb fnma --min-year 2020 --max-year 2023
      mortgage-data match mbs-fhlb fnma --seller-filter "CHICAGO"
    """
    from mortgage_data_manager.matching.match_mbs_fhlb.fnma_match import run_fnma_fhlb_matching

    configure_logging(level="DEBUG" if verbose else "INFO")

    try:
        console.print("[cyan]Running FNMA-FHLB matching (mutual exclusivity test)...[/cyan]")
        console.print(f"  Year range: {min_year}-{max_year}")
        console.print(f"  Seller filter: '{seller_filter}'")

        df = run_fnma_fhlb_matching(
            min_year=min_year,
            max_year=max_year,
            seller_filter=seller_filter,
        )

        console.print("\n[green]FNMA-FHLB matching complete![/green]")
        console.print(f"  Total matches: {len(df):,}")
        console.print(
            f"  Crosswalk saved to: {CROSSWALK_OUTPUT_DIR / 'fnma_fhlb_crosswalk.parquet'}"
        )

        if len(df) == 0:
            console.print("[green]✓ Mutual exclusivity confirmed: zero matches found[/green]")
        elif len(df) < 100:
            console.print(
                f"[yellow]! Low match count ({len(df)}) - likely noise or edge cases[/yellow]"
            )
        else:
            console.print("[red]! Unexpected overlap detected - investigate matches[/red]")

    except Exception as e:
        console.print(f"[red]FNMA-FHLB matching failed: {e}[/red]")
        logger.exception("FNMA-FHLB matching error")
        raise typer.Exit(code=1)


@app.command()
def info():
    """Display MBS-FHLB matching configuration and data availability.

    Shows configured paths and checks for available data files.

    Examples:
      mortgage-data match mbs-fhlb info
    """
    table = Table(
        title="MBS-FHLB Matching Configuration", show_header=True, header_style="bold cyan"
    )
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("[bold]Data Directory[/bold]", str(DATA_DIR))
    table.add_row("[bold]Intermediate Directory[/bold]", str(INTERMEDIATE_DIR))
    table.add_row("[bold]Output Directory[/bold]", str(CROSSWALK_OUTPUT_DIR))

    table.add_row("", "")
    table.add_row("[bold]GNMA Paths[/bold]", "")
    if HAS_GNMA:
        gnma_dir = get_gnma_silver_dir()
        table.add_row("  Silver dailyllmni/L", str(gnma_dir))
    else:
        table.add_row("  Status", "[red]GNMA module not available[/red]")

    table.add_row("", "")
    table.add_row("[bold]FHLB Paths[/bold]", "")
    if HAS_FHLB:
        fhlb_dir = get_fhlb_ama_silver_dir()
        table.add_row("  Silver AMA", str(fhlb_dir))
    else:
        table.add_row("  Status", "[red]FHLB module not available[/red]")

    table.add_row("", "")
    table.add_row("[bold]FNMA/UMBS Paths[/bold]", "")
    if HAS_UMBS:
        umbs_dir = get_umbs_fnma_illd_bronze_dir()
        table.add_row("  UMBS FNMA ILLD Bronze", str(umbs_dir))
    else:
        table.add_row("  UMBS Status", "[red]UMBS module not available[/red]")

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

    if HAS_GNMA:
        check_parquet_files(get_gnma_silver_dir(), "GNMA Silver L")
    if HAS_FHLB:
        check_parquet_files(get_fhlb_ama_silver_dir(), "FHLB AMA Silver")
    if HAS_UMBS:
        check_parquet_files(get_umbs_fnma_illd_bronze_dir(), "UMBS FNMA ILLD Bronze")

    # Check output files
    console.print("\n[bold]Output Files:[/bold]")

    # GNMA-FHLB outputs
    gnma_crosswalk = CROSSWALK_OUTPUT_DIR / "gnma_fhlb_crosswalk.parquet"
    gnma_details = INTERMEDIATE_DIR / "gnma_fhlb_match_details.parquet"

    if gnma_crosswalk.exists():
        import polars as pl

        df = pl.read_parquet(gnma_crosswalk)
        console.print(f"  [green]✓[/green] GNMA-FHLB crosswalk: {len(df):,} matches")
    else:
        console.print("  [yellow]![/yellow] GNMA-FHLB crosswalk: not yet generated")

    if gnma_details.exists():
        console.print("  [green]✓[/green] GNMA-FHLB match details: exists")
    else:
        console.print("  [yellow]![/yellow] GNMA-FHLB match details: not yet generated")

    # FNMA-FHLB outputs
    fnma_crosswalk = CROSSWALK_OUTPUT_DIR / "fnma_fhlb_crosswalk.parquet"

    if fnma_crosswalk.exists():
        import polars as pl

        df = pl.read_parquet(fnma_crosswalk)
        console.print(f"  [green]✓[/green] FNMA-FHLB crosswalk: {len(df):,} matches")
    else:
        console.print("  [yellow]![/yellow] FNMA-FHLB crosswalk: not yet generated")


@app.command()
def validate(
    workflow: str = typer.Argument(
        None,
        help="Which validation to run: 'gnma', 'fnma', or omit for both",
    ),
    min_year: int = typer.Option(
        2018,
        "--min-year",
        help="First year to include",
    ),
    max_year: int = typer.Option(
        2024,
        "--max-year",
        help="Last year to include",
    ),
    gnma_issuer_id: int = typer.Option(
        3937,
        "--gnma-issuer-id",
        help="GNMA issuer ID filter (use 0 for all)",
    ),
    fnma_exclude_banks: list[str] | None = typer.Option(
        None,
        "--fnma-exclude-banks",
        help="FHLB bank names to exclude from FNMA analysis (repeatable)",
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
        help="Save plots to docs/matching/figures/mbs_fhlb_{gnma,fnma}/",
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        "-o",
        help="Base output directory for figures (subdirs created per workflow)",
    ),
):
    """Run validation analysis on MBS-FHLB matches.

    Validates either GNMA-FHLB matching, FNMA-FHLB mutual exclusivity test,
    or both if no workflow specified.

    GNMA validation generates:
    - Temporal match rates
    - State match rate map
    - Match quality distributions
    - Match rates by mortgage type, loan amount, interest rate, LTV

    FNMA validation generates:
    - Summary panel (mutual exclusivity confirmation)
    - Distribution comparisons (year, state, loan amount, rate, LTV, DTI)

    Examples:
      mortgage-data match mbs-fhlb validate           # Run both
      mortgage-data match mbs-fhlb validate gnma      # GNMA only
      mortgage-data match mbs-fhlb validate fnma      # FNMA only
      mortgage-data match mbs-fhlb validate --show-plots
      mortgage-data match mbs-fhlb validate --min-year 2020 --max-year 2023
    """
    from mortgage_data_manager.matching.match_mbs_fhlb.validation import (
        run_fnma_validation,
        run_gnma_validation,
    )
    from mortgage_data_manager.matching.match_mbs_fhlb.validation import (
        run_validation as run_all_validation,
    )

    configure_logging(level="INFO")

    issuer_id = gnma_issuer_id if gnma_issuer_id else None

    try:
        if workflow is None:
            console.print("[cyan]Running both GNMA and FNMA validation...[/cyan]")
            results = run_all_validation(
                min_year=min_year,
                max_year=max_year,
                gnma_issuer_id=issuer_id,
                output_dir=output_dir,
                save_plots=save_plots,
                show_plots=show_plots,
                fnma_exclude_banks=fnma_exclude_banks,
            )
        elif workflow.lower() == "gnma":
            console.print("[cyan]Running GNMA-FHLB validation...[/cyan]")
            results = run_gnma_validation(
                min_year=min_year,
                max_year=max_year,
                issuer_id=issuer_id,
                output_dir=output_dir,
                save_plots=save_plots,
                show_plots=show_plots,
            )
        elif workflow.lower() == "fnma":
            console.print("[cyan]Running FNMA-FHLB validation...[/cyan]")
            results = run_fnma_validation(
                min_year=min_year,
                max_year=max_year,
                output_dir=output_dir,
                save_plots=save_plots,
                show_plots=show_plots,
                exclude_banks=fnma_exclude_banks,
            )
        else:
            console.print(f"[red]Unknown workflow: {workflow}. Use 'gnma', 'fnma', or omit.[/red]")
            raise typer.Exit(code=1)

        if "error" in results:
            console.print(f"[red]Validation error: {results['error']}[/red]")
            raise typer.Exit(code=1)

        console.print("[green]Validation complete![/green]")

    except Exception as e:
        console.print(f"[red]Validation failed: {e}[/red]")
        logger.exception("Validation error")
        raise typer.Exit(code=1)


def cli_main():
    """Entry point for standalone MBS-FHLB matching CLI."""
    app()


if __name__ == "__main__":
    cli_main()
