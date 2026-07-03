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
    • Matching workflows

    Regulatory/financial-institution sources (FDIC, NCUA, FFIEC, CRA, FCA, FRB,
    SEC, BPS, NSI, NFIP, SBA) attach via the optional mortgage-data-regulatory plugin.

    [bold]Phase 6 Complete[/bold]
    All data sources and matching workflows are now available.
    Phase 7 (Documentation & Polish) in progress.
    """
    pass


@app.command()
def version():
    """Show version information."""
    from mortgage_data_manager import __version__

    version_text = Text()
    version_text.append("Mortgage Data Manager\n", style="bold cyan")
    version_text.append(f"Version: {__version__}\n", style="white")
    version_text.append("\nPhase 1: Foundation (Core utilities implemented)", style="yellow")

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

    # Implementation status
    info_text.append("Implementation Status:\n", style="bold white")
    info_text.append("  ✓ Phase 1: Foundation (Core utilities)\n", style="green")
    info_text.append("  ✓ Phase 2: HMDA Migration\n", style="green")
    info_text.append("  ✓ Phase 3: FHA Migration\n", style="green")
    info_text.append("  ✓ Phase 4: MBS Migration\n", style="green")
    info_text.append("  ✓ Phase 5: Financial Institutions\n", style="green")
    info_text.append("  ✓ Phase 6: Matching Integration\n", style="green")
    info_text.append("  ○ Phase 7: Documentation & Polish\n\n", style="dim")

    # Available modules
    info_text.append("Available Core Modules:\n", style="bold white")
    info_text.append("  • config     - Base configuration management\n", style="white")
    info_text.append("  • medallion  - Medallion architecture utilities\n", style="white")
    info_text.append("  • io         - File download and I/O operations\n", style="white")
    info_text.append("  • logging    - Standardized logging setup\n\n", style="white")

    info_text.append("Available Data Sources:\n", style="bold white")
    info_text.append("  • hmda       - Home Mortgage Disclosure Act data\n", style="green")
    info_text.append("  • fha        - Federal Housing Administration data\n", style="green")
    info_text.append("  • gnma       - GNMA (Ginnie Mae) data\n", style="green")
    info_text.append("  • fhfa       - FHFA data\n", style="green")
    info_text.append("  • fnma       - Fannie Mae data\n", style="green")
    info_text.append("  • fhlmc      - Freddie Mac data\n", style="green")
    info_text.append("  • umbs       - UMBS data\n", style="green")
    info_text.append("  • match      - Matching workflows for linking records\n", style="green")
    info_text.append("  • hud        - HUD USPS ZIP Code crosswalk data\n", style="green")
    info_text.append("  • hud-mf     - HUD multifamily housing (FHA-insured + Section 8) data\n", style="green")
    info_text.append("\nDerived datasets:\n", style="bold white")
    info_text.append("  • combined   - Cross-source master datasets (scaffold)\n", style="green")
    info_text.append("  • analytics  - Calculated + estimated MBS analytics (library, scaffold)\n", style="green")
    info_text.append("\nFI Agencies:\n", style="bold white")
    info_text.append("  • fhlb       - Federal Home Loan Bank data\n", style="green")
    info_text.append(
        "\n(Regulatory sources via the optional mortgage-data-regulatory plugin.)\n",
        style="dim",
    )

    console.print(Panel(info_text, title="Project Info", border_style="cyan"))


def cli_main():
    """Entry point for the unified CLI.

    This function will be called when users run 'mortgage-data' from the command line.
    """
    # Register HMDA subcommands (Phase 2)
    from mortgage_data_manager.hmda.cli import app as hmda_app
    app.add_typer(hmda_app, name="hmda", help="HMDA data operations")

    # Register FHA subcommands (Phase 3)
    from mortgage_data_manager.fha.cli import app as fha_app
    app.add_typer(fha_app, name="fha", help="FHA data operations")

    # Register MBS agencies individually (Phase 4)
    from mortgage_data_manager.fhfa.cli import app as fhfa_app
    from mortgage_data_manager.fhlmc.cli import app as fhlmc_app
    from mortgage_data_manager.fnma.cli import app as fnma_app
    from mortgage_data_manager.gnma.cli import app as gnma_app
    from mortgage_data_manager.umbs.cli import app as umbs_app

    app.add_typer(gnma_app, name="gnma", help="GNMA (Ginnie Mae) data operations")
    app.add_typer(fhfa_app, name="fhfa", help="FHFA data operations")
    app.add_typer(fnma_app, name="fnma", help="Fannie Mae data operations")
    app.add_typer(fhlmc_app, name="fhlmc", help="Freddie Mac data operations")
    app.add_typer(umbs_app, name="umbs", help="UMBS data operations")

    # Register Matching subcommands (Phase 6)
    from mortgage_data_manager.matching.cli import app as matching_app
    app.add_typer(matching_app, name="match", help="Matching workflows for linking records")

    # Register HUD crosswalk data
    from mortgage_data_manager.hud.cli import app as hud_app
    app.add_typer(hud_app, name="hud", help="HUD USPS crosswalk data operations")

    # Register HUD multifamily housing data
    from mortgage_data_manager.hud_mf.cli import app as hud_mf_app
    app.add_typer(hud_mf_app, name="hud-mf", help="HUD multifamily housing data operations")

    # Register combined cross-source master datasets
    from mortgage_data_manager.combined.cli import app as combined_app
    app.add_typer(combined_app, name="combined", help="Combined cross-source master datasets")

    # Register FHLB (Bucket A mortgage source)
    from mortgage_data_manager.fhlb.cli import app as fhlb_app
    app.add_typer(fhlb_app, name="fhlb", help="FHLB data operations")

    # Register Manifest CLI (data catalog)
    from mortgage_data_manager.cli.manifest import app as manifest_app

    app.add_typer(manifest_app, name="manifest", help="Data manifest catalog operations")

    # Register top-level geocode CLI (cache management)
    from mortgage_data_manager.cli.geocode import app as geocode_app

    app.add_typer(geocode_app, name="geocode", help="Inspect/manage the global geocoder cache")

    # Register optional private/proprietary plugins. Installed companion
    # packages declare a 'mortgage_data_manager.plugins' entry point so their
    # sub-commands attach here. Kept deliberately generic: the public repo never
    # names proprietary sources, and a plugin is only present if separately
    # installed against an external (non-public) data root.
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
            console.print(
                f"[yellow]Warning:[/] could not load plugin '{ep.name}': {exc}"
            )


if __name__ == "__main__":
    cli_main()
