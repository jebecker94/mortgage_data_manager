"""CLI for FHA-HMDA matching workflow.

This module provides command-line access to the FHA-HMDA matching pipeline
for linking FHA mortgage records to HMDA loan applications.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from mortgage_data_manager.core.logging import configure_logging
from mortgage_data_manager.matching.match_fha_hmda.config import (
    CROSSWALK_OUTPUT_DIR,
    FHA_SILVER_DIR,
    HMDA_SILVER_DIR,
    HUD_CROSSWALK_DIR,
    INTERMEDIATE_DIR,
    MAX_YEAR,
    MIN_YEAR,
    OUTPUT_DIR,
)

app = typer.Typer(
    name="fha-hmda",
    help="FHA-HMDA matching workflow for linking FHA loans to HMDA applications",
    no_args_is_help=True,
)

console = Console()


@app.callback()
def callback():
    """[bold cyan]FHA-HMDA Matching Workflow[/bold cyan].

    Tools for matching FHA mortgage data with HMDA loan applications to create
    loan-level crosswalks.
    """
    configure_logging(level="INFO")


@app.command()
def info() -> None:
    """Show information about FHA-HMDA matching configuration and data availability."""
    console.print("\n[bold cyan]FHA-HMDA Matching Workflow[/bold cyan]\n")

    console.print(
        "This workflow matches FHA (Federal Housing Administration) mortgage data "
        "with HMDA (Home Mortgage Disclosure Act) data to create loan-level crosswalks.\n"
    )

    # Configuration table
    config_table = Table(
        title="Configuration", show_header=True, header_style="bold magenta"
    )
    config_table.add_column("Setting", style="cyan", width=25)
    config_table.add_column("Value", style="white")

    config_table.add_row("Min Year", str(MIN_YEAR))
    config_table.add_row("Max Year", str(MAX_YEAR))
    config_table.add_row("FHA Silver Dir", str(FHA_SILVER_DIR))
    config_table.add_row("HMDA Silver Dir", str(HMDA_SILVER_DIR))
    config_table.add_row("Crosswalk Output Dir", str(CROSSWALK_OUTPUT_DIR))
    config_table.add_row("Output Dir", str(OUTPUT_DIR))
    config_table.add_row("Intermediate Dir", str(INTERMEDIATE_DIR))
    config_table.add_row("HUD Crosswalk Dir", str(HUD_CROSSWALK_DIR))

    console.print(config_table)
    console.print()

    # Data availability table
    data_table = Table(
        title="Data Availability", show_header=True, header_style="bold magenta"
    )
    data_table.add_column("Source", style="cyan", width=15)
    data_table.add_column("Years Available", style="white", width=30)
    data_table.add_column("Status", style="white", width=10)

    # Check FHA data
    fha_years = []
    if FHA_SILVER_DIR.exists():
        for year_dir in sorted(FHA_SILVER_DIR.glob("Year=*")):
            year = year_dir.name.split("=")[1]
            fha_years.append(year)
    fha_status = "[green]OK[/green]" if fha_years else "[red]Missing[/red]"
    data_table.add_row("FHA Silver", ", ".join(fha_years) if fha_years else "None", fha_status)

    # Check HMDA data
    hmda_years = []
    if HMDA_SILVER_DIR.exists():
        for year_dir in sorted(HMDA_SILVER_DIR.glob("activity_year=*")):
            year = year_dir.name.split("=")[1]
            hmda_years.append(year)
    hmda_status = "[green]OK[/green]" if hmda_years else "[red]Missing[/red]"
    data_table.add_row("HMDA Silver", ", ".join(hmda_years) if hmda_years else "None", hmda_status)

    # Check HUD crosswalk data
    hud_available = (HUD_CROSSWALK_DIR / "ZIP_TRACT").exists()
    hud_status = "[green]OK[/green]" if hud_available else "[red]Missing[/red]"
    data_table.add_row("HUD Crosswalk", "ZIP_TRACT" if hud_available else "None", hud_status)

    console.print(data_table)
    console.print()

    # Output files
    output_table = Table(
        title="Output Files", show_header=True, header_style="bold magenta"
    )
    output_table.add_column("File", style="cyan", width=50)
    output_table.add_column("Status", style="white", width=10)

    output_file = CROSSWALK_OUTPUT_DIR / f"fha_hmda_crosswalk_{MIN_YEAR}_{MAX_YEAR}.parquet"
    output_status = "[green]Exists[/green]" if output_file.exists() else "[yellow]Not created[/yellow]"
    output_table.add_row(str(output_file.name), output_status)

    intermediate_fha = INTERMEDIATE_DIR / "fha_match_data_post2018.parquet"
    intermediate_hmda = INTERMEDIATE_DIR / "hmda_match_data_post2018.parquet"
    fha_intermediate_status = "[green]Exists[/green]" if intermediate_fha.exists() else "[yellow]Not created[/yellow]"
    hmda_intermediate_status = "[green]Exists[/green]" if intermediate_hmda.exists() else "[yellow]Not created[/yellow]"
    output_table.add_row("fha_match_data_post2018.parquet", fha_intermediate_status)
    output_table.add_row("hmda_match_data_post2018.parquet", hmda_intermediate_status)

    console.print(output_table)
    console.print()


@app.command()
def run(
    min_year: Annotated[
        int,
        typer.Option("--min-year", "-m", help="Minimum year to match"),
    ] = MIN_YEAR,
    max_year: Annotated[
        int,
        typer.Option("--max-year", "-M", help="Maximum year to match"),
    ] = MAX_YEAR,
    skip_data_prep: Annotated[
        bool,
        typer.Option("--skip-prep", help="Skip data preparation if intermediate files exist"),
    ] = False,
    fha_dir: Annotated[
        Path | None,
        typer.Option("--fha-dir", help="Override FHA silver data directory"),
    ] = None,
    hmda_dir: Annotated[
        Path | None,
        typer.Option("--hmda-dir", help="Override HMDA silver data directory"),
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", "-o", help="Override output directory"),
    ] = None,
) -> None:
    """Run the FHA-HMDA matching pipeline.

    This command runs the full matching pipeline which:
    1. Prepares FHA data for matching
    2. Prepares HMDA data for matching (with ZIP-tract crosswalk)
    3. Runs Round 1 matching (strict criteria)
    4. Runs Round 2 matching (looser criteria for remaining loans)
    5. Combines crosswalks into final output
    """
    from mortgage_data_manager.matching.match_fha_hmda.match_hmda_fha_post2018 import (
        run_fha_hmda_matching,
    )

    console.print("\n[bold cyan]Running FHA-HMDA Matching Pipeline[/bold cyan]")
    console.print(f"  Years: {min_year} - {max_year}")
    console.print(f"  Skip data prep: {skip_data_prep}\n")

    try:
        output_file = run_fha_hmda_matching(
            min_year=min_year,
            max_year=max_year,
            fha_folder=fha_dir,
            hmda_folder=hmda_dir,
            output_folder=output_dir,
            skip_data_prep=skip_data_prep,
        )
        console.print("\n[bold green]Matching complete![/bold green]")
        console.print(f"Output file: {output_file}\n")
    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1)


@app.command()
def build_crosswalk(
    census_vintage: Annotated[
        int,
        typer.Argument(help="Census vintage (2010 or 2020)"),
    ],
    output_path: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output path for crosswalk file"),
    ] = None,
) -> None:
    """Build consolidated ZIP-tract crosswalk from HUD data.

    The HUD crosswalk uses different census tract vintages:
    - 2010: Census tracts used for years 2010-2019
    - 2020: Census tracts used for years 2020+
    """
    from mortgage_data_manager.matching.match_fha_hmda.config import CROSSWALK_DIR
    from mortgage_data_manager.matching.match_fha_hmda.match_hmda_fha_post2018 import (
        build_zip_tract_crosswalk,
    )

    if census_vintage not in (2010, 2020):
        console.print("[red]Error: Census vintage must be 2010 or 2020[/red]")
        raise typer.Exit(code=1)

    if output_path is None:
        output_path = CROSSWALK_DIR / f"zip_tract_{census_vintage}_census.parquet"

    console.print(f"\n[bold cyan]Building {census_vintage} Census Crosswalk[/bold cyan]")
    console.print(f"  HUD source: {HUD_CROSSWALK_DIR}")
    console.print(f"  Output: {output_path}\n")

    try:
        build_zip_tract_crosswalk(HUD_CROSSWALK_DIR, census_vintage, output_path)  # type: ignore
        console.print("\n[bold green]Crosswalk built successfully![/bold green]\n")
    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1)


@app.command()
def validate(
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", "-o", help="Directory to save validation figures"),
    ] = None,
    save_plots: Annotated[
        bool,
        typer.Option("--save-plots/--no-save-plots", help="Save plots to disk"),
    ] = True,
    show_plots: Annotated[
        bool,
        typer.Option("--show-plots", help="Display plots interactively"),
    ] = False,
    min_loans: Annotated[
        int,
        typer.Option("--min-loans", help="Minimum loans for county filtering"),
    ] = 50,
) -> None:
    """Run validation analysis and generate diagnostic figures.

    This command runs comprehensive validation analyses on the FHA-HMDA
    matching results to assess match quality across time, geography,
    and loan characteristics.

    Output includes:
    - Match rates over time (yearly and monthly)
    - Geographic analysis (state and county maps)
    - Size-correlation analysis (state/county volume vs match rate)
    - Loan characteristic analysis (amount, rate, purpose, ARM)
    """
    from mortgage_data_manager.matching.match_fha_hmda.validation import (
        VALIDATION_OUTPUT_DIR,
        run_validation,
    )

    if output_dir is None:
        output_dir = VALIDATION_OUTPUT_DIR

    console.print("\n[bold cyan]Running FHA-HMDA Match Validation[/bold cyan]")
    console.print(f"  Output directory: {output_dir}")
    console.print(f"  Save plots: {save_plots}")
    console.print(f"  Show plots: {show_plots}")
    console.print(f"  Min loans (county filter): {min_loans}\n")

    try:
        results = run_validation(
            output_dir=output_dir,
            save_plots=save_plots,
            show_plots=show_plots,
            min_loans=min_loans,
        )
        console.print("\n[bold green]Validation complete![/bold green]")

        # Summary table
        summary_table = Table(
            title="Validation Summary", show_header=True, header_style="bold magenta"
        )
        summary_table.add_column("Metric", style="cyan", width=35)
        summary_table.add_column("Value", style="white", width=20)

        summary_table.add_row(
            "Overall FHA Match Rate", f"{results['overall_fha_rate']:.1%}"
        )
        summary_table.add_row(
            "Overall HMDA Match Rate", f"{results['overall_hmda_rate']:.1%}"
        )
        summary_table.add_row(
            "State Size Correlation (FHA)",
            f"r = {results['fha_corr']['spearman_r']:.3f}",
        )
        summary_table.add_row(
            "State Size Correlation (HMDA)",
            f"r = {results['hmda_corr']['spearman_r']:.3f}",
        )
        if results.get("county_corr"):
            summary_table.add_row(
                "County Size Correlation (FHA)",
                f"r = {results['county_corr']['fha_spearman_r']:.3f}",
            )
            summary_table.add_row(
                "County Size Correlation (HMDA)",
                f"r = {results['county_corr']['hmda_spearman_r']:.3f}",
            )

        console.print()
        console.print(summary_table)

        if save_plots:
            console.print(f"\nFigures saved to: {output_dir}\n")

    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1)


def cli_main() -> None:
    """Entry point for FHA-HMDA matching CLI."""
    app()


if __name__ == "__main__":
    cli_main()
