"""CLI for matching workflows.

This module provides command-line access to matching workflows for linking
records across different mortgage datasets.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from mortgage_data_manager import __version__
from mortgage_data_manager.core.cli.formatting import console
from mortgage_data_manager.core.cli.validation import version_callback_factory
from mortgage_data_manager.matching.master.cli import app as master_app
from mortgage_data_manager.matching.match_crt_mbs.cli import app as crt_mbs_app
from mortgage_data_manager.matching.match_fhfa_mbs.cli import app as fhfa_mbs_app
from mortgage_data_manager.matching.match_fhlb_mbs.cli import app as fhlb_mbs_app
from mortgage_data_manager.matching.match_hmda_fha.cli import app as hmda_fha_app
from mortgage_data_manager.matching.match_hmda_fhfa.cli import app as hmda_fhfa_app
from mortgage_data_manager.matching.match_hmda_fhlb.cli import app as hmda_fhlb_app
from mortgage_data_manager.matching.match_hmda_gnma.cli import app as hmda_gnma_app
from mortgage_data_manager.matching.match_hmda_gse.cli import app as hmda_gse_app
from mortgage_data_manager.matching.match_hmda_sellers_purchasers.cli import (
    app as hmda_sellers_purchasers_app,
)
from mortgage_data_manager.matching.match_mbs_umbs.cli import app as mbs_umbs_app

# Version callback
_version_callback = version_callback_factory("Matching Workflows", __version__)

app = typer.Typer(
    name="match",
    help="Matching workflows for linking records across mortgage datasets",
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=False,
)


@app.callback()
def callback(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit",
        ),
    ] = False,
) -> None:
    """[bold cyan]Matching Workflows[/bold cyan].

    Tools for linking records across different mortgage datasets.
    """
    pass


# ============================================================================
# Register Matching Workflow CLIs
# ============================================================================

app.add_typer(hmda_fha_app, name="hmda-fha")
app.add_typer(hmda_fhfa_app, name="hmda-fhfa")
app.add_typer(hmda_fhlb_app, name="hmda-fhlb")
app.add_typer(hmda_gse_app, name="hmda-gse")
app.add_typer(hmda_gnma_app, name="hmda-gnma")
app.add_typer(hmda_sellers_purchasers_app, name="hmda-sellers-purchasers")
app.add_typer(mbs_umbs_app, name="mbs-umbs")
app.add_typer(crt_mbs_app, name="crt-mbs")
app.add_typer(fhlb_mbs_app, name="fhlb-mbs")
app.add_typer(fhfa_mbs_app, name="fhfa-mbs")
# Assembler, not a pairwise matcher: consumes every crosswalk above.
app.add_typer(master_app, name="master")


#: Every pairwise matching workflow: (``match`` subcommand, module, what it links). Workflow
#: names put HMDA first and the MBS/UMBS side last; ``master`` assembles them all.
WORKFLOWS: tuple[tuple[str, str, str], ...] = (
    ("hmda-fha", "match_hmda_fha", "HMDA originations ↔ FHA endorsements"),
    ("hmda-fhfa", "match_hmda_fhfa", "HMDA originations ↔ FHFA GSE acquisitions"),
    ("hmda-fhlb", "match_hmda_fhlb", "HMDA originations ↔ FHLB acquired member assets"),
    ("hmda-gse", "match_hmda_gse", "HMDA conventional ↔ FNMA/FHLMC loan level (chain + direct)"),
    ("hmda-gnma", "match_hmda_gnma", "HMDA FHA and VA/USDA ↔ GNMA loan level"),
    ("hmda-sellers-purchasers", "match_hmda_sellers_purchasers", "HMDA originations ↔ purchases"),
    ("fhfa-mbs", "match_fhfa_mbs", "FHFA acquisitions ↔ GSE single-family loan level"),
    ("fhlb-mbs", "match_fhlb_mbs", "FHLB acquired member assets ↔ GNMA loan level"),
    ("crt-mbs", "match_crt_mbs", "GSE CRT reference pools ↔ single-family loan level"),
    ("mbs-umbs", "match_mbs_umbs", "GSE single-family loan level ↔ UMBS disclosures"),
)


@app.command()
def info() -> None:
    """Show the matching workflows and where their outputs live."""
    console.print("\n[bold cyan]Matching Workflows[/bold cyan]\n")

    table = Table(title="Matching workflows", show_header=True, header_style="bold magenta")
    table.add_column("Command", style="cyan")
    table.add_column("Links", style="white")
    for command, _module, links in WORKFLOWS:
        table.add_row(f"match {command}", links)
    table.add_row("match master", "Assembles every edge into the HMDA spine")
    console.print(table)

    console.print(
        "\nEvery workflow has [cyan]info[/cyan], [cyan]run[/cyan] and (most) [cyan]validate[/cyan]."
    )
    console.print("\n[bold]Data configuration:[/bold]")
    console.print("  from mortgage_data_manager.matching.config import MatchingConfig")
    console.print("  matching_dir = MatchingConfig.MATCHING_DATA_DIR")
    console.print('  crosswalk_dir = MatchingConfig.get_crosswalk_type_dir("hmda_fha")\n')


@app.command(name="list-workflows")
def list_workflows() -> None:
    """List all available matching workflow modules."""
    console.print("\n[bold cyan]Available Matching Workflow Modules:[/bold cyan]\n")
    for _command, module, _links in WORKFLOWS:
        console.print(f"  • [cyan]mortgage_data_manager.matching.{module}[/cyan]")
    # The assembler over all of the above, not a pairwise matcher.
    console.print("  • [cyan]mortgage_data_manager.matching.master[/cyan]")
    console.print("\n[yellow]Run one with `mortgage-data match <workflow> run`.[/yellow]\n")


def cli_main() -> None:
    """Entry point for matching CLI."""
    app()


if __name__ == "__main__":
    cli_main()
