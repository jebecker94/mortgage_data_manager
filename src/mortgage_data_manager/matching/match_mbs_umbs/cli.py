"""MBS Matching CLI.

Typer-based command-line interface for MBS matching workflows.
Matches MBS loan-level data to UMBS issuance data for FNMA and FHLMC.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.matching.match_mbs_umbs.config import (
    CROSSWALK_OUTPUT_DIR,
    MBSUMBSConfig,
    get_umbs_bronze_dir,
)

# Create Typer app
app = typer.Typer(
    name="mbs-umbs",
    help="MBS-UMBS matching workflow commands (MBS ↔ UMBS)",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()
logger = get_logger(__name__)


@app.callback()
def callback():
    """[bold cyan]MBS-UMBS Matching Workflow[/bold cyan].

    Tools for matching MBS loan-level disclosure data to UMBS issuance data.
    Supports both Fannie Mae (FNMA) and Freddie Mac (FHLMC) datasets.
    """
    configure_logging(level="INFO")


@app.command()
def fnma(
    snapshot: bool = typer.Option(
        True,
        "--snapshot/--no-snapshot",
        help="Recover pre-2019 loans from the first monthly snapshot (FNM_MLLD). "
        "--no-snapshot reverts to the post-2019 ILLD-only matcher.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed progress output",
    ),
):
    """Run FNMA (Fannie Mae) MBS to UMBS matching.

    Matches FNMA single-family loan-level data (silver/issuances) to UMBS data. By default the
    UMBS side combines post-2019 ILLD (FNM_ILLD) with the first monthly snapshot (FNM_MLLD),
    recovering loans originated before ILLD coverage began (2019-06).

    Output: fnma_crosswalk.parquet with matched loan IDs.

    Examples:
      mortgage-data match mbs-umbs fnma
      mortgage-data match mbs-umbs fnma --no-snapshot
    """
    from mortgage_data_manager.core.config import MortgageDataConfig
    from mortgage_data_manager.matching.match_mbs_umbs.fnma_match import match_fnma_mbs_umbs

    configure_logging(level="DEBUG" if verbose else "INFO")

    try:
        # Get paths
        mbs_dir = MortgageDataConfig.get_subpackage_data_dir("fnma") / "silver" / "issuances"
        umbs_dir = get_umbs_bronze_dir() / "FNMA" / "FNM_ILLD"
        snapshot_file = MBSUMBSConfig.get_umbs_snapshot_file("fnma") if snapshot else None
        crosswalk_file = CROSSWALK_OUTPUT_DIR / "fnma_crosswalk.parquet"
        variable_file = Path(__file__).parent / "fnma_column_mapping.csv"

        console.print("[cyan]Running FNMA MBS matching...[/cyan]")
        console.print(f"  MBS data: {mbs_dir}")
        console.print(f"  UMBS data: {umbs_dir}")
        console.print(f"  UMBS snapshot: {snapshot_file if snapshot_file else '(disabled)'}")
        console.print(f"  Output: {crosswalk_file}")

        # Ensure output directory exists
        CROSSWALK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # Run matching
        df = match_fnma_mbs_umbs(
            mbs_dir=mbs_dir,
            umbs_dir=umbs_dir,
            crosswalk_file=crosswalk_file,
            variable_file=variable_file,
            snapshot_file=snapshot_file,
        )

        console.print("\n[green]FNMA matching complete![/green]")
        console.print(f"  Total matches: {len(df):,}")
        console.print(f"  Output saved to: {crosswalk_file}")

    except Exception as e:
        console.print(f"[red]FNMA matching failed: {e}[/red]")
        logger.exception("FNMA matching error")
        raise typer.Exit(code=1)


@app.command()
def fhlmc(
    snapshot: bool = typer.Option(
        True,
        "--snapshot/--no-snapshot",
        help="Recover pre-2019 loans from the first monthly snapshot (FU). "
        "--no-snapshot reverts to the post-2019 FRE_ILLD-only matcher.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed progress output",
    ),
):
    """Run FHLMC (Freddie Mac) MBS to UMBS matching.

    Matches FHLMC single-family loan-level data (bronze/origination) to UMBS data. By default
    the UMBS side combines post-2019 ILLD (FRE_ILLD) with the first monthly snapshot (FU),
    recovering loans originated before ILLD coverage began (2019-06).

    Output: fhlmc_crosswalk.parquet with matched loan IDs.

    Examples:
      mortgage-data match mbs-umbs fhlmc
      mortgage-data match mbs-umbs fhlmc --no-snapshot
    """
    from mortgage_data_manager.matching.match_mbs_umbs.config import get_fhlmc_config
    from mortgage_data_manager.matching.match_mbs_umbs.fhlmc_match import match_fhlmc_mbs_umbs

    configure_logging(level="DEBUG" if verbose else "INFO")

    try:
        # Get paths
        fhlmc_config = get_fhlmc_config()
        mbs_dir = fhlmc_config.FHLMC_BRONZE_ORIGINATION
        umbs_dir = get_umbs_bronze_dir() / "FHLMC" / "FRE_ILLD"
        snapshot_file = MBSUMBSConfig.get_umbs_snapshot_file("fhlmc") if snapshot else None
        crosswalk_file = CROSSWALK_OUTPUT_DIR / "fhlmc_crosswalk.parquet"
        variable_file = Path(__file__).parent / "fhlmc_column_mapping.csv"

        console.print("[cyan]Running FHLMC MBS matching...[/cyan]")
        console.print(f"  MBS data: {mbs_dir}")
        console.print(f"  UMBS data: {umbs_dir}")
        console.print(f"  UMBS snapshot: {snapshot_file if snapshot_file else '(disabled)'}")
        console.print(f"  Output: {crosswalk_file}")

        # Ensure output directory exists
        CROSSWALK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # Run matching
        df = match_fhlmc_mbs_umbs(
            mbs_dir=mbs_dir,
            umbs_dir=umbs_dir,
            crosswalk_file=crosswalk_file,
            variable_file=variable_file,
            snapshot_file=snapshot_file,
        )

        console.print("\n[green]FHLMC matching complete![/green]")
        console.print(f"  Total matches: {len(df):,}")
        console.print(f"  Output saved to: {crosswalk_file}")

    except Exception as e:
        console.print(f"[red]FHLMC matching failed: {e}[/red]")
        logger.exception("FHLMC matching error")
        raise typer.Exit(code=1)


@app.command()
def all(
    snapshot: bool = typer.Option(
        True,
        "--snapshot/--no-snapshot",
        help="Recover pre-2019 loans from the first monthly snapshot (MLLD/FU) for both GSEs.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed progress output",
    ),
):
    """Run both FNMA and FHLMC MBS matching.

    Executes matching for both GSEs sequentially.

    Examples:
      mortgage-data match mbs-umbs all
      mortgage-data match mbs-umbs all --no-snapshot
    """
    console.print("[bold cyan]Running MBS matching for all GSEs...[/bold cyan]\n")

    # Run FNMA
    console.print("[bold]1. FNMA Matching[/bold]")
    fnma(snapshot=snapshot, verbose=verbose)

    console.print()

    # Run FHLMC
    console.print("[bold]2. FHLMC Matching[/bold]")
    fhlmc(snapshot=snapshot, verbose=verbose)

    console.print("\n[bold green]All MBS matching complete![/bold green]")


@app.command()
def info():
    """Display MBS matching configuration and data availability.

    Shows configured paths and checks for available data files.

    Examples:
      mortgage-data match mbs-umbs info
    """
    from mortgage_data_manager.core.config import MortgageDataConfig
    from mortgage_data_manager.matching.match_mbs_umbs.config import get_fhlmc_config

    table = Table(
        title="MBS-UMBS Matching Configuration", show_header=True, header_style="bold cyan"
    )
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("[bold]Crosswalk Output Dir[/bold]", str(CROSSWALK_OUTPUT_DIR))

    table.add_row("", "")
    table.add_row("[bold]FNMA Paths[/bold]", "")
    fnma_mbs = MortgageDataConfig.get_subpackage_data_dir("fnma") / "silver" / "issuances"
    fnma_umbs = get_umbs_bronze_dir() / "FNMA" / "FNM_ILLD"
    table.add_row("  MBS Data (silver/issuances)", str(fnma_mbs))
    table.add_row("  UMBS Data (FNM_ILLD)", str(fnma_umbs))

    table.add_row("", "")
    table.add_row("[bold]FHLMC Paths[/bold]", "")
    try:
        fhlmc_config = get_fhlmc_config()
        fhlmc_mbs = fhlmc_config.BRONZE_ORIGINATION
    except ImportError:
        fhlmc_mbs = MortgageDataConfig.get_subpackage_data_dir("fhlmc") / "bronze" / "origination"
    fhlmc_umbs = get_umbs_bronze_dir() / "FHLMC" / "FRE_ILLD"
    table.add_row("  MBS Data (bronze/origination)", str(fhlmc_mbs))
    table.add_row("  UMBS Data (FRE_ILLD)", str(fhlmc_umbs))

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

    check_parquet_files(fnma_mbs, "FNMA MBS")
    check_parquet_files(fnma_umbs, "FNMA UMBS")
    check_parquet_files(fhlmc_mbs, "FHLMC MBS")
    check_parquet_files(fhlmc_umbs, "FHLMC UMBS")

    # Check output files
    console.print("\n[bold]Output Files:[/bold]")
    fnma_output = CROSSWALK_OUTPUT_DIR / "fnma_crosswalk.parquet"
    fhlmc_output = CROSSWALK_OUTPUT_DIR / "fhlmc_crosswalk.parquet"

    if fnma_output.exists():
        import polars as pl

        df = pl.read_parquet(fnma_output)
        console.print(f"  [green]✓[/green] FNMA crosswalk: {len(df):,} matches")
    else:
        console.print("  [yellow]![/yellow] FNMA crosswalk: not yet generated")

    if fhlmc_output.exists():
        import polars as pl

        df = pl.read_parquet(fhlmc_output)
        console.print(f"  [green]✓[/green] FHLMC crosswalk: {len(df):,} matches")
    else:
        console.print("  [yellow]![/yellow] FHLMC crosswalk: not yet generated")


@app.command()
def validate(
    gse: str = typer.Option(
        "fnma",
        "--gse",
        "-g",
        help="GSE to validate: fnma or fhlmc",
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
        help="Save plots to docs/matching/figures/mbs_umbs/",
    ),
):
    """Run validation analysis on MBS-UMBS matches.

    Generates diagnostic figures analyzing match quality for
    FNMA or FHLMC crosswalks.

    Examples:
      mortgage-data match mbs-umbs validate
      mortgage-data match mbs-umbs validate --gse fhlmc
      mortgage-data match mbs-umbs validate --show-plots
      mortgage-data match mbs-umbs validate --no-save
    """
    from mortgage_data_manager.matching.match_mbs_umbs.validation import (
        run_validation as _run_validation,
    )

    gse_lower = gse.lower()
    if gse_lower not in ("fnma", "fhlmc"):
        console.print(f"[red]Invalid GSE '{gse}'. Use fnma or fhlmc.[/red]")
        raise typer.Exit(code=1)

    try:
        console.print(f"[cyan]Running MBS-UMBS validation ({gse_lower})...[/cyan]")
        results = _run_validation(
            gse=gse_lower,
            save_plots=save_plots,
            show_plots=show_plots,
        )

        if "error" in results:
            console.print(f"[red]Validation error: {results['error']}[/red]")
            raise typer.Exit(code=1)

        console.print("[green]Validation complete![/green]")

    except Exception as e:
        console.print(f"[red]Validation failed: {e}[/red]")
        logger.exception("MBS-UMBS validation error")
        raise typer.Exit(code=1)


def cli_main():
    """Entry point for standalone MBS-UMBS matching CLI."""
    app()


if __name__ == "__main__":
    cli_main()
