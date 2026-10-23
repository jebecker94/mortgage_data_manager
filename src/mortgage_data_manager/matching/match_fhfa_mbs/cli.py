"""FHFA-MBS Matching CLI.

Typer-based command-line interface for the FHFA-MBS matching workflow: GSE
loan-level data (FNMA, FHLMC) against FHFA acquisition data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

import typer
from rich.console import Console
from rich.table import Table

from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.matching.match_fhfa_mbs.config import FHFAMBSMatchingConfig as Config
from mortgage_data_manager.matching.match_fhfa_mbs.rounds import Leg

# Create Typer app
app = typer.Typer(
    name="fhfa-mbs",
    help="FHFA-MBS matching workflow commands (FHFA ↔ MBS)",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()
logger = get_logger(__name__)


@app.callback()
def callback():
    """[bold cyan]FHFA-MBS Matching Workflow[/bold cyan].

    Tools for matching FHFA acquisition data to GSE loan-level data (FNMA, FHLMC).
    """
    configure_logging(level="INFO")


def _agencies(agency: str) -> list[Literal["fnma", "fhlmc"]]:
    """Resolve the ``--agency`` token, Freddie first when all; exits on an unknown token."""
    token = agency.lower()
    if token not in ("fnma", "fhlmc", "all"):
        console.print(f"[red]Invalid agency '{agency}'. Use fnma, fhlmc, or all.[/red]")
        raise typer.Exit(code=1)
    return ["fhlmc", "fnma"] if token == "all" else [cast(Literal["fnma", "fhlmc"], token)]


@app.command()
def run(
    agency: str = typer.Option(
        "all", "--agency", "-a", help="Enterprise to match: fnma, fhlmc, or all"
    ),
    min_year: int = typer.Option(
        Config.FNMA_START_YEAR, "--min-year", "-m", help="First acquisition year (inclusive)"
    ),
    max_year: int = typer.Option(
        Config.FNMA_END_YEAR, "--max-year", "-M", help="Last acquisition year (inclusive)"
    ),
    year: int | None = typer.Option(
        None,
        "--year",
        "-y",
        help="Match this one acquisition year only. Its crosswalk is written unresolved "
        "across years; omit it for the resolved year range.",
    ),
    arm: bool = typer.Option(
        False,
        "--arm",
        help="Run the FHFA->UMBS ARM pass instead: both enterprises pooled into "
        "fhfa_mbs_crosswalk_arm.parquet, keyed on the UMBS loan id (--agency ignored).",
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        help="Write crosswalks here instead of the production crosswalk directory",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Rebuild the cached Freddie first-reporting-period tables",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed progress output"),
):
    """Match FHFA acquisitions to GSE loan-level data, one crosswalk per enterprise and year.

    The year range is matched year by year, then cross-year claims are resolved (one loan,
    one acquisition year) before anything is written.

    Examples:
      mortgage-data match fhfa-mbs run
      mortgage-data match fhfa-mbs run --agency fnma --min-year 2020 --max-year 2023
      mortgage-data match fhfa-mbs run --agency fhlmc --year 2022
      mortgage-data match fhfa-mbs run --output-dir /tmp/staging
      mortgage-data match fhfa-mbs run --arm
    """
    configure_logging(level="DEBUG" if verbose else "INFO")
    out = output_dir or Config.FHFA_MBS_CROSSWALK_DIR
    if arm:
        _run_arm(min_year, max_year, out)
        return

    from mortgage_data_manager.matching.match_fhfa_mbs.inputs import SPECS
    from mortgage_data_manager.matching.match_fhfa_mbs.matching import run_matching, run_year

    try:
        for gse in _agencies(agency):
            if year is not None:
                console.print(f"[cyan]Running FHFA-{gse.upper()} matching for {year}...[/cyan]")
                df = run_year(year, SPECS[gse], output_dir=out, overwrite=overwrite)
                console.print(f"[green]{gse.upper()}: {len(df):,} matches[/green]")
                continue
            console.print(f"[bold]FHFA-{gse.upper()} matching, {min_year}-{max_year}[/bold]")
            run_matching(
                gse,
                min_year=min_year,
                max_year=max_year,
                output_dir=out,
                overwrite=overwrite,
            )
            console.print(f"[green]{gse.upper()} matching complete.[/green]\n")
    except Exception as e:
        console.print(f"[red]FHFA-MBS matching failed: {e}[/red]")
        logger.exception("FHFA-MBS matching error")
        raise typer.Exit(code=1)


def _run_arm(min_year: int, max_year: int, output_dir: Path) -> None:
    """The ``run --arm`` pass: FHFA ARM acquisitions against the UMBS ARM disclosures."""
    from mortgage_data_manager.matching.match_fhfa_mbs.matching_arm import run_matching_arm

    try:
        crosswalk = run_matching_arm(min_year, max_year, output_dir)
    except Exception as e:
        console.print(f"[red]FHFA-UMBS ARM matching failed: {e}[/red]")
        logger.exception("FHFA-UMBS ARM matching error")
        raise typer.Exit(code=1)
    if crosswalk.is_empty():
        console.print("[red]No FHFA-UMBS ARM pairs.[/red]")
        raise typer.Exit(code=1)
    for enterprise, n in crosswalk.group_by("fhfa_enterprise").len().sort("fhfa_enterprise").rows():
        console.print(f"[green]{'FNMA' if enterprise == 1 else 'FHLMC'}: {n:,} pairs[/green]")


@app.command()
def validate(
    agency: str = typer.Option(
        "all", "--agency", "-a", help="Enterprise to validate: fnma, fhlmc, or all"
    ),
    fnma_min_year: int = typer.Option(
        2019,
        "--fnma-min-year",
        help="First year for FNMA validation",
    ),
    fnma_max_year: int = typer.Option(
        Config.FNMA_END_YEAR,
        "--fnma-max-year",
        help="Last year for FNMA validation",
    ),
    fhlmc_min_year: int = typer.Option(
        2019,
        "--fhlmc-min-year",
        help="First year for FHLMC validation",
    ),
    fhlmc_max_year: int = typer.Option(
        Config.FHLMC_END_YEAR,
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
        help="Save plots to docs/matching/figures/",
    ),
    arm_coverage: bool = typer.Option(
        True,
        "--arm-coverage/--no-arm-coverage",
        help="Also measure how many FHFA ARM records the SF crosswalks reach with and "
        "without the FHFA->UMBS ARM crosswalk, and draw fhfa_mbs_umbs_arm_coverage.png",
    ),
):
    """Run validation analysis on FHFA-MBS matches.

    Writes one combined figure, fhfa_mbs_validation.png, with a column per enterprise
    and a row per dimension: origination period, loan amount, LTV, interest rate, DTI,
    loan purpose, channel, and occupancy. Also writes fhfa_mbs_umbs_arm_coverage.png,
    the share of FHFA ARM records matched by acquisition year, by the SF crosswalk
    alone and with the ARM crosswalk (skip it with --no-arm-coverage).

    Examples:
      mortgage-data match fhfa-mbs validate
      mortgage-data match fhfa-mbs validate --agency fnma
      mortgage-data match fhfa-mbs validate --show-plots
      mortgage-data match fhfa-mbs validate --no-save
      mortgage-data match fhfa-mbs validate --fnma-min-year 2020
      mortgage-data match fhfa-mbs validate --no-arm-coverage
    """
    from mortgage_data_manager.matching.match_fhfa_mbs.validation import (
        run_validation as _run_validation,
    )

    agencies = _agencies(agency)
    enterprise_lower = agencies[0] if len(agencies) == 1 else "both"

    try:
        console.print(f"[cyan]Running FHFA-MBS validation ({enterprise_lower})...[/cyan]")
        results = _run_validation(
            enterprise=cast("Literal['fnma', 'fhlmc', 'both']", enterprise_lower),
            save_plots=save_plots,
            show_plots=show_plots,
            fnma_min_year=fnma_min_year,
            fnma_max_year=fnma_max_year,
            fhlmc_min_year=fhlmc_min_year,
            fhlmc_max_year=fhlmc_max_year,
            arm_coverage=arm_coverage,
        )

        if "error" in results:
            console.print(f"[red]Validation error: {results['error']}[/red]")
            raise typer.Exit(code=1)

        console.print("[green]Validation complete![/green]")

    except Exception as e:
        console.print(f"[red]Validation failed: {e}[/red]")
        logger.exception("FHFA-MBS validation error")
        raise typer.Exit(code=1)


@app.command()
def info():
    """Display FHFA-MBS matching configuration.

    Shows the configured matching and crosswalk directories and each enterprise's rounds.

    Examples:
      mortgage-data match fhfa-mbs info
    """
    table = Table(
        title="FHFA-MBS Matching Configuration", show_header=True, header_style="bold cyan"
    )
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("[bold]Data Directory[/bold]", str(Config.FHFA_MBS_MATCHING_DIR))
    table.add_row("[bold]Crosswalk Output Dir[/bold]", str(Config.FHFA_MBS_CROSSWALK_DIR))

    console.print(table)
    _print_rounds()


def _leg(leg: Leg) -> str:
    """One leg as ``flag N <- Y-a..Y-b``, Y the acquisition year."""
    youngest = "Y" if leg.min_lag == 0 else f"Y-{leg.min_lag}"
    if leg.max_lag is None:
        span = f"<= {youngest}"
    else:
        span = youngest if leg.max_lag == leg.min_lag else f"Y-{leg.max_lag}..{youngest}"
    return f"flag {leg.fhfa_flag} <- {span}"


def _print_rounds() -> None:
    """Print each enterprise's rounds and the ARM rounds (``rounds.py``) in run order."""
    from mortgage_data_manager.matching.match_fhfa_mbs.rounds import (
        ROUNDS_ARM,
        ROUNDS_FHLMC,
        ROUNDS_FNMA,
    )

    for name, rounds in (("FNMA", ROUNDS_FNMA), ("FHLMC", ROUNDS_FHLMC)):
        table = Table(title=f"{name} rounds (rounds.py)", header_style="bold magenta")
        columns = ("Round", "Description", "Legs", "Term", "Passes", "Keys", "Pool", "Gate")
        for column in columns:
            table.add_column(column, style="cyan" if column == "Round" else "white")
        for number, rnd in enumerate(rounds, start=1):
            keys = [] if rnd.msa else ["no MSA"]
            keys += ["primary -> investor"] if rnd.occupancy_recode else []
            gate = ["ZIP3"] if rnd.require_zip3 else []
            gate += [f"tier <= {rnd.max_tier}"] if rnd.max_tier is not None else []
            table.add_row(
                str(number),
                rnd.description,
                "\n".join(_leg(leg) for leg in rnd.legs),
                rnd.term,
                str(rnd.max_passes),
                ", ".join(keys) or "all",
                "filtered" if rnd.pool_filter else "all",
                ", ".join(gate) or "-",
            )
        console.print(table)

    table = Table(title="ARM rounds (rounds.py)", header_style="bold magenta")
    for column in ("Round", "Description", "Keys"):
        table.add_column(column, style="cyan" if column == "Round" else "white")
    for number, arm in enumerate(ROUNDS_ARM, start=1):
        table.add_row(str(number), arm.description, ", ".join(arm.keys))
    console.print(table)


def cli_main():
    """Entry point for standalone FHFA-MBS matching CLI."""
    app()


if __name__ == "__main__":
    cli_main()
