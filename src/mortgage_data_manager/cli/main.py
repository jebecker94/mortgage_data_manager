"""Top-level unified CLI for mortgage data manager.

This is the main entry point for the 'mortgage-data' command. It orchestrates
all subpackage CLIs into a single cohesive interface.
"""

from __future__ import annotations

import typer
from rich.panel import Panel
from rich.text import Text

from mortgage_data_manager.core.cli.formatting import console

app = typer.Typer(
    name="mortgage-data",
    help="Unified mortgage data management CLI",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.callback()
def main():
    """[bold cyan]Mortgage Data Manager[/bold cyan].

    Unified CLI for managing mortgage market data across:

    • HMDA (Home Mortgage Disclosure Act)
    • FHA (Federal Housing Administration)
    • MBS Agencies: GNMA, FHFA, FNMA, FHLMC, UMBS
    • FHLB (Federal Home Loan Bank)
    • HUD multifamily and the HUD USPS ZIP crosswalk
    • Matching workflows and combined cross-source datasets
    """
    pass


@app.command()
def version():
    """Show version information."""
    from mortgage_data_manager import __version__

    version_text = Text()
    version_text.append("Mortgage Data Manager\n", style="bold cyan")
    version_text.append(f"Version: {__version__}", style="white")

    console.print(Panel(version_text, title="Version Info", border_style="cyan"))


@app.command()
def info():
    """Show project information and status."""
    from mortgage_data_manager.core.config import MortgageDataConfig

    info_text = Text()
    info_text.append("Project Information\n\n", style="bold cyan")

    # Project paths
    info_text.append("Paths:\n", style="bold white")
    info_text.append(f"  Project Dir:  {MortgageDataConfig.PROJECT_DIR}\n", style="white")
    info_text.append(f"  Data Dir:     {MortgageDataConfig.DATA_DIR}\n", style="white")
    info_text.append(f"  Output Dir:   {MortgageDataConfig.OUTPUT_DIR}\n\n", style="white")

    rows = [
        (
            "Data sources",
            [
                ("hmda", "Home Mortgage Disclosure Act loan-level data"),
                ("fha", "FHA single-family snapshots"),
                ("fhfa", "FHFA Public Use Databases"),
                ("fhlb", "Federal Home Loan Bank AMA data"),
                ("gnma", "Ginnie Mae disclosure data"),
                ("fnma", "Fannie Mae loan performance data"),
                ("fhlmc", "Freddie Mac loan-level data"),
                ("umbs", "Fannie Mae / Freddie Mac UMBS disclosures"),
                ("hud", "HUD multifamily housing (FHA-insured + Section 8)"),
                ("usps-crosswalk", "HUD USPS ZIP crosswalks"),
            ],
        ),
        (
            "Cross-source",
            [
                ("match", "Matching workflows that link records across sources"),
                ("combined", "Combined cross-source master datasets"),
            ],
        ),
        (
            "Utilities",
            [
                ("raw", "Raw download ledger"),
                ("geocode", "Geocoder cache"),
            ],
        ),
    ]
    for heading, entries in rows:
        info_text.append(f"{heading}:\n", style="bold white")
        for name, description in entries:
            info_text.append(f"  • {name:<15}- {description}\n", style="green")
        info_text.append("\n")

    info_text.rstrip()
    console.print(Panel(info_text, title="Project Info", border_style="cyan"))


def _register_subcommands() -> None:
    """Attach every public subpackage CLI to ``app``.

    Runs at import so the fully assembled app is visible to tooling that inspects
    the module (``typer mortgage_data_manager.cli.main utils docs`` generates
    ``docs/cli_reference.md`` from it). Plugins are attached separately in
    :func:`cli_main` so the public reference never lists private commands.
    """
    # Register HMDA subcommands
    from mortgage_data_manager.sources.hmda.cli import app as hmda_app

    app.add_typer(hmda_app, name="hmda", help="HMDA data operations")

    # Register FHA subcommands
    from mortgage_data_manager.sources.fha.cli import app as fha_app

    app.add_typer(fha_app, name="fha", help="FHA data operations")

    # Register MBS agencies individually
    from mortgage_data_manager.sources.fhfa.cli import app as fhfa_app
    from mortgage_data_manager.sources.fhlmc.cli import app as fhlmc_app
    from mortgage_data_manager.sources.fnma.cli import app as fnma_app
    from mortgage_data_manager.sources.gnma.cli import app as gnma_app
    from mortgage_data_manager.sources.umbs.cli import app as umbs_app

    app.add_typer(gnma_app, name="gnma", help="GNMA (Ginnie Mae) data operations")
    app.add_typer(fhfa_app, name="fhfa", help="FHFA data operations")
    app.add_typer(fnma_app, name="fnma", help="Fannie Mae data operations")
    app.add_typer(fhlmc_app, name="fhlmc", help="Freddie Mac data operations")
    app.add_typer(umbs_app, name="umbs", help="UMBS data operations")

    # Register Matching subcommands
    from mortgage_data_manager.matching.cli import app as matching_app

    app.add_typer(matching_app, name="match", help="Matching workflows for linking records")

    # Register USPS ZIP crosswalk data
    from mortgage_data_manager.sources.usps_crosswalk.cli import app as usps_crosswalk_app

    app.add_typer(
        usps_crosswalk_app, name="usps-crosswalk", help="USPS ZIP crosswalk data operations"
    )

    # Register HUD multifamily housing data
    from mortgage_data_manager.sources.hud.cli import app as hud_app

    app.add_typer(hud_app, name="hud", help="HUD multifamily housing data operations")

    # Register combined cross-source master datasets
    from mortgage_data_manager.combined.cli import app as combined_app

    app.add_typer(combined_app, name="combined", help="Combined cross-source master datasets")

    # Register FHLB
    from mortgage_data_manager.sources.fhlb.cli import app as fhlb_app

    app.add_typer(fhlb_app, name="fhlb", help="FHLB data operations")

    # Register top-level geocode CLI (cache management)
    from mortgage_data_manager.cli.geocode import app as geocode_app

    app.add_typer(geocode_app, name="geocode", help="Inspect/manage the global geocoder cache")

    # Raw download ledger (register / audit / changes / info)
    from mortgage_data_manager.cli.raw import app as raw_app

    app.add_typer(raw_app, name="raw", help="Raw download ledger: register, audit, changes, info")


def cli_main() -> None:
    """Entry point for the unified CLI (the ``mortgage-data`` console script).

    Attaches optional private/proprietary plugins, then runs the app. Installed
    companion packages declare a ``mortgage_data_manager.plugins`` entry point so
    their sub-commands attach here. Kept deliberately generic: the public repo
    never names proprietary sources, and a plugin is only present if separately
    installed against an external (non-public) data root.
    """
    _register_plugins()
    app()


def _register_plugins() -> None:
    """Discover and attach optional plugin CLIs via entry points.

    Any installed package declaring an entry point in the
    ``mortgage_data_manager.plugins`` group is given the chance to attach its own
    sub-commands to the unified CLI. Each entry point must resolve to a callable
    that accepts the parent Typer ``app``. A broken or missing plugin is reported
    but never aborts the CLI.
    """
    from importlib.metadata import entry_points

    for ep in entry_points(group="mortgage_data_manager.plugins"):
        try:
            register = ep.load()
            register(app)
        except Exception as exc:  # noqa: BLE001 - a bad plugin must not kill the CLI
            console.print(f"[yellow]Warning:[/] could not load plugin '{ep.name}': {exc}")


_register_subcommands()


if __name__ == "__main__":
    cli_main()
