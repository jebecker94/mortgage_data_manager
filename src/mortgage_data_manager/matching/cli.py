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

from mortgage_data_manager.matching.match_fha_gnma.cli import app as fha_gnma_app
from mortgage_data_manager.matching.match_fha_hmda.cli import app as fha_hmda_app
from mortgage_data_manager.matching.match_fhfa_hmda.cli import app as fhfa_hmda_app
from mortgage_data_manager.matching.match_hmda_fhlb.cli import app as hmda_fhlb_app
from mortgage_data_manager.matching.match_hmda_mbs.cli import app as hmda_mbs_app
from mortgage_data_manager.matching.match_hmda_sellers_and_purchasers.cli import (
    app as sellers_purchasers_app,
)
from mortgage_data_manager.matching.match_mbs_fhfa.cli import app as mbs_fhfa_app
from mortgage_data_manager.matching.match_mbs_fhlb.cli import app as mbs_fhlb_app
from mortgage_data_manager.matching.match_mbs_umbs.cli import app as mbs_umbs_app

app.add_typer(fha_gnma_app, name="fha-gnma")
app.add_typer(fha_hmda_app, name="fha-hmda")
app.add_typer(fhfa_hmda_app, name="fhfa-hmda")
app.add_typer(hmda_fhlb_app, name="hmda-fhlb")
app.add_typer(hmda_mbs_app, name="hmda-mbs")
app.add_typer(sellers_purchasers_app, name="sellers-purchasers")
app.add_typer(mbs_umbs_app, name="mbs-umbs")
app.add_typer(mbs_fhlb_app, name="mbs-fhlb")
app.add_typer(mbs_fhfa_app, name="mbs-fhfa")


@app.command()
def info() -> None:
    """Show information about available matching workflows."""
    console.print("\n[bold cyan]Matching Workflows[/bold cyan]\n")

    console.print(
        "The matching module provides tools for linking records across different "
        "mortgage data sources.\n"
    )

    # Create table of available workflows
    table = Table(title="Available Matching Workflows", show_header=True, header_style="bold magenta")
    table.add_column("Workflow", style="cyan", width=30)
    table.add_column("Description", style="white")

    workflows = [
        ("FHA ↔ GNMA", "Match FHA endorsements to GNMA loan-level disclosure"),
        ("FHA ↔ HMDA", "Match FHA loans to HMDA loan applications"),
        ("HMDA ↔ MBS", "Match HMDA loans to MBS pool data"),
        ("HMDA ↔ FHLB", "Match HMDA loans to FHLB advance data"),
        ("HMDA ↔ FHFA", "Match HMDA loans to FHFA data"),
        ("MBS ↔ UMBS", "Match MBS loan-level to UMBS issuance data"),
        ("MBS ↔ FHLB", "Match MBS pools to FHLB data"),
        ("MBS ↔ FHFA", "Match MBS pools to FHFA data"),
        ("HMDA Sellers/Purchasers", "Match HMDA loans to seller/purchaser data"),
    ]

    for workflow, description in workflows:
        table.add_row(workflow, description)

    console.print(table)

    console.print("\n[yellow]Matching workflows are available as Python modules.[/yellow]\n")

    console.print("[bold]Example usage:[/bold]")
    console.print("  from mortgage_data_manager.matching.match_fha_hmda import match_fha_hmda")
    console.print("  from mortgage_data_manager.matching.match_hmda_sellers_and_purchasers import run_matching")
    console.print("\n[bold]Data configuration:[/bold]")
    console.print("  from mortgage_data_manager.matching.config import MatchingConfig")
    console.print("  matching_dir = MatchingConfig.MATCHING_DATA_DIR")
    console.print("  output_dir = MatchingConfig.MATCHING_OUTPUT_DIR\n")


@app.command(name="list-workflows")
def list_workflows() -> None:
    """List all available matching workflow modules."""
    console.print("\n[bold cyan]Available Matching Workflow Modules:[/bold cyan]\n")

    workflows = [
        "match_fha_gnma",
        "match_fha_hmda",
        "match_fhfa_hmda",
        "match_hmda_sellers_and_purchasers",
        "match_hmda_mbs",
        "match_hmda_fhlb",
        "match_mbs_umbs",
        "match_mbs_fhlb",
        "match_mbs_fhfa",
    ]

    for workflow in workflows:
        console.print(f"  • [cyan]mortgage_data_manager.matching.{workflow}[/cyan]")

    console.print("\n[yellow]Use Python API to access matching functionality.[/yellow]\n")


def cli_main() -> None:
    """Entry point for matching CLI."""
    app()


if __name__ == "__main__":
    cli_main()
