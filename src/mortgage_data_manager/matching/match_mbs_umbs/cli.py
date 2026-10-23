"""MBS Matching CLI.

Typer-based command-line interface for MBS matching workflows.
Matches MBS loan-level data to UMBS issuance data for FNMA and FHLMC.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

import typer
from rich.console import Console
from rich.table import Table

from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.matching.match_mbs_umbs.config import MBSUMBSMatchingConfig as Config

# Create Typer app
app = typer.Typer(
    name="mbs-umbs",
    help="MBS-UMBS matching workflow commands (MBS ↔ UMBS)",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()
logger = get_logger(__name__)

EXACT_ROUND_HELP = (
    "Run round 4, which resolves leftover candidate groups on exactness over "
    "LTV/CLTV/DTI/Term and drops every tie. --no-exact-attribute-round reverts to the "
    "three-round matcher."
)


def _staleness_lines(gse: str) -> list[str]:
    """Staleness warnings for one GSE's crosswalk, or a note if the inputs are unreachable."""
    from mortgage_data_manager.matching.match_mbs_umbs.metrics import check_input_staleness

    try:
        return check_input_staleness(
            gse,
            Config.MBS_UMBS_CROSSWALK_DIR,
            Config.get_mbs_dir(gse),
            Config.get_umbs_illd_dir(gse),
            Config.get_umbs_snapshot_file(gse),
            Config.get_mbs_harp_file(gse),
        )
    except Exception as e:  # unreadable inputs must not take the command down
        logger.warning("%s: input-size check skipped (%s)", gse, e)
        return [f"{gse}: input-size check skipped ({e})"]


def _print_staleness(gses: list[str]) -> None:
    """Print each GSE's input-size warnings (never fatal — a stale crosswalk still loads)."""
    warned = False
    for g in gses:
        for line in _staleness_lines(g):
            warned = True
            console.print(f"  [yellow]![/yellow] {line}")
    if not warned:
        console.print(
            f"  [green]✓[/green] Input row counts match the crosswalk sidecars ({', '.join(gses)})"
        )


@app.callback()
def callback():
    """[bold cyan]MBS-UMBS Matching Workflow[/bold cyan].

    Tools for matching MBS loan-level disclosure data to UMBS issuance data.
    Supports both Fannie Mae (FNMA) and Freddie Mac (FHLMC) datasets.
    """
    configure_logging(level="INFO")


Agency = Literal["fnma", "fhlmc"]
AGENCIES: tuple[Agency, ...] = ("fnma", "fhlmc")


def _agencies(agency: str) -> list[Agency]:
    """The GSEs one ``--agency`` value selects; exits on an unknown value."""
    key = agency.lower()
    if key == "all":
        return list(AGENCIES)
    if key not in AGENCIES:
        console.print(f"[red]Invalid agency '{agency}'. Use fnma, fhlmc, or all.[/red]")
        raise typer.Exit(code=1)
    return [cast(Agency, key)]


@app.command()
def run(
    agency: str = typer.Option("all", "--agency", "-a", help="GSE to match: fnma, fhlmc, or all"),
    snapshot: bool = typer.Option(
        True,
        "--snapshot/--no-snapshot",
        help="Recover pre-2019 loans from the first monthly snapshot (MLLD/FU). "
        "--no-snapshot reverts to the post-2019 ILLD-only matcher.",
    ),
    harp: bool = typer.Option(
        True,
        "--harp/--no-harp",
        help="Include Fannie's separately-released HARP/Refi Plus population in the MBS "
        "universe (FNMA only; Freddie has no equivalent release).",
    ),
    null_tolerant: bool = typer.Option(
        True,
        "--null-tolerant/--no-null-tolerant",
        help="Let a tolerance check pass when either side's value is missing, so one "
        "absent attribute cannot veto an otherwise-agreeing candidate. "
        "--no-null-tolerant is the null-vetoing baseline.",
    ),
    exact_attribute_round: bool = typer.Option(
        True,
        "--exact-attribute-round/--no-exact-attribute-round",
        help=EXACT_ROUND_HELP,
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed progress output",
    ),
):
    """Match GSE single-family loans (the MBS side) to UMBS disclosures.

    Both sides are two releases by default. The MBS side adds Fannie's separately
    published HARP/Refi Plus population (silver/harp) to the quarterly files; the UMBS side
    adds the first monthly snapshot (MLLD/FU) to post-2019 ILLD, recovering loans
    originated before ILLD coverage began (2019-06).

    Output: crosswalk/mbs_umbs/mbs_umbs_crosswalk_{agency}.parquet, with a metrics sidecar.

    Examples:
      mortgage-data match mbs-umbs run
      mortgage-data match mbs-umbs run --agency fnma
      mortgage-data match mbs-umbs run --agency fnma --no-harp
      mortgage-data match mbs-umbs run --no-snapshot --no-exact-attribute-round
    """
    from mortgage_data_manager.matching.match_mbs_umbs.matching import run_matching

    configure_logging(level="DEBUG" if verbose else "INFO")
    for gse in _agencies(agency):
        crosswalk_file = Config.get_crosswalk_path(gse)
        console.print(f"[cyan]Running {gse.upper()} MBS-UMBS matching...[/cyan]")
        console.print(f"  MBS data: {Config.get_mbs_dir(gse)}")
        harp_file = Config.get_mbs_harp_file(gse) if harp else None
        console.print(f"  MBS HARP: {harp_file if harp_file else '(none)'}")
        console.print(f"  UMBS data: {Config.get_umbs_illd_dir(gse)}")
        snapshot_file = Config.get_umbs_snapshot_file(gse) if snapshot else None
        console.print(f"  UMBS snapshot: {snapshot_file if snapshot_file else '(disabled)'}")
        console.print(f"  Output: {crosswalk_file}")
        try:
            df = run_matching(
                gse,
                snapshot=snapshot,
                harp=harp,
                null_tolerant=null_tolerant,
                exact_attribute_round=exact_attribute_round,
            )
        except Exception as e:
            console.print(f"[red]{gse.upper()} matching failed: {e}[/red]")
            logger.exception("%s MBS-UMBS matching error", gse.upper())
            raise typer.Exit(code=1)
        console.print(f"[green]{gse.upper()} matching complete:[/green] {len(df):,} pairs\n")


@app.command()
def info():
    """Display MBS matching configuration and data availability.

    Shows configured paths and checks for available data files.

    Examples:
      mortgage-data match mbs-umbs info
    """
    from mortgage_data_manager.core.config import MortgageDataConfig

    table = Table(
        title="MBS-UMBS Matching Configuration", show_header=True, header_style="bold cyan"
    )
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("[bold]Crosswalk Output Dir[/bold]", str(Config.MBS_UMBS_CROSSWALK_DIR))

    table.add_row("", "")
    table.add_row("[bold]FNMA Paths[/bold]", "")
    fnma_mbs = MortgageDataConfig.get_subpackage_data_dir("fnma") / "silver" / "issuances"
    fnma_umbs = Config.get_umbs_illd_dir("fnma")
    table.add_row("  MBS Data (silver/issuances)", str(fnma_mbs))
    table.add_row("  UMBS Data (FNM_ILLD)", str(fnma_umbs))

    table.add_row("", "")
    table.add_row("[bold]FHLMC Paths[/bold]", "")
    fhlmc_mbs = Config.get_fhlmc_config().FHLMC_SILVER_DIR / "origination"
    fhlmc_umbs = Config.get_umbs_illd_dir("fhlmc")
    table.add_row("  MBS Data (silver/origination)", str(fhlmc_mbs))
    table.add_row("  UMBS Data (FRE_ILLD)", str(fhlmc_umbs))

    from mortgage_data_manager.matching.match_mbs_umbs.rounds import ROUNDS

    table.add_row("", "")
    table.add_row("[bold]Matching Rounds[/bold] (rounds.py)", "")
    for number, rnd in enumerate(ROUNDS, start=1):
        rules = ["MIP exact" if rnd.mip else "no MIP"]
        rules.append("credit score" if rnd.credit_score else "no credit score")
        if rnd.exact_attribute:
            rules.append("contested groups resolved on exact LTV/CLTV/DTI/term")
        table.add_row(f"  Round {number}: {rnd.label}", ", ".join(rules))

    console.print(table)

    # Check data availability
    console.print("\n[bold]Data Availability:[/bold]")

    def check_parquet_files(path: Path, label: str):
        if path.exists():
            files = list(path.rglob("*.parquet"))  # silver may be hive-partitioned
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
    fnma_output = Config.get_crosswalk_path("fnma")
    fhlmc_output = Config.get_crosswalk_path("fhlmc")

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

    # Structural staleness: mtimes cannot detect an input that grew without a newer file.
    console.print("\n[bold]Crosswalk freshness (input row counts vs the sidecars):[/bold]")
    _print_staleness(["fnma", "fhlmc"])


@app.command()
def check(
    agency: str = typer.Option("all", "--agency", "-a", help="GSE to check: fnma, fhlmc, or all"),
):
    """Check the latest matcher run against pinned match-rate floors (drift guard).

    Every matcher run writes a metrics sidecar (mbs_umbs_crosswalk_{agency}_metrics.json)
    next to the crosswalk. This command compares it against the floors pinned in
    expected_match_rates.json and exits non-zero on any breach — run it after regenerating
    a crosswalk, especially following upstream import-layer rebuilds (fnma/fhlmc/umbs).

    It also re-counts the matcher's input globs and reports any crosswalk whose inputs have
    changed size since it was built. That is a warning, not a breach: the crosswalk is not
    wrong, it is behind. Mtimes do not catch it — an input directory can grow while every
    file in it stays older than the crosswalk.

    Examples:
      mortgage-data match mbs-umbs check
      mortgage-data match mbs-umbs check --agency fnma
    """
    from mortgage_data_manager.matching.match_mbs_umbs.metrics import check_against_baseline

    gses: list[str] = list(_agencies(agency))

    console.print("[bold]Crosswalk freshness (input row counts vs the sidecars):[/bold]")
    _print_staleness(gses)
    console.print()

    breaches: list[str] = []
    for g in gses:
        breaches.extend(check_against_baseline(g, Config.MBS_UMBS_CROSSWALK_DIR))

    if breaches:
        console.print("[red bold]Match-rate drift guard FAILED:[/red bold]")
        for b in breaches:
            console.print(f"  [red]✗[/red] {b}")
        console.print(
            "\nIf an upstream import layer changed, diagnose before accepting the new "
            "crosswalk; if the new rates are correct, update expected_match_rates.json."
        )
        raise typer.Exit(code=1)
    console.print(f"[green]✓ Match metrics within pinned floors ({', '.join(gses)})[/green]")


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
):
    """Run validation analysis on MBS-UMBS matches (both GSEs).

    Prints the match-statistics sections for FNMA and FHLMC and renders the single
    combined diagnostic figure (mbs_umbs_match_rates_panel.png): match rates by month,
    interest rate, FICO, LTV, and DTI, with loan distributions and underwriting cut
    points on each characteristic panel.

    Examples:
      mortgage-data match mbs-umbs validate
      mortgage-data match mbs-umbs validate --show-plots
      mortgage-data match mbs-umbs validate --no-save
    """
    from mortgage_data_manager.matching.match_mbs_umbs.validation import (
        run_validation as _run_validation,
    )

    try:
        console.print("[cyan]Running MBS-UMBS validation (fnma + fhlmc)...[/cyan]")
        _run_validation(
            save_plots=save_plots,
            show_plots=show_plots,
        )
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
