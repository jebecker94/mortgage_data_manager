"""CLI for HMDA-MBS matching workflow.

This module provides command-line access to the HMDA-MBS matching pipeline
for building master crosswalks linking HMDA → FHFA → MBS → UMBS.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import typer
from rich.console import Console
from rich.table import Table

from mortgage_data_manager.core.logging import configure_logging
from mortgage_data_manager.matching.match_hmda_mbs.config import HMDAMBSMatchingConfig as Config

app = typer.Typer(
    name="hmda-mbs",
    help="HMDA-MBS matching workflow for building master crosswalks (HMDA → FHFA → MBS → UMBS)",
    no_args_is_help=True,
)

console = Console()


@app.callback()
def callback():
    """[bold cyan]HMDA-MBS Matching Workflow[/bold cyan].

    Tools for building master crosswalks linking HMDA loans through FHFA, MBS, and UMBS data
    for FNMA and FHLMC agencies.
    """
    configure_logging(level="INFO")


@app.command()
def info() -> None:
    """Show information about HMDA-MBS matching configuration and data availability."""
    console.print("\n[bold cyan]HMDA-MBS Matching Workflow[/bold cyan]\n")

    console.print(
        "This workflow builds master crosswalks linking HMDA loans through FHFA, "
        "MBS, and UMBS data for FNMA and FHLMC agencies.\n"
    )

    # Configuration table
    config_table = Table(title="Configuration", show_header=True, header_style="bold magenta")
    config_table.add_column("Setting", style="cyan", width=25)
    config_table.add_column("Value", style="white")

    config_table.add_row("Min Year", str(Config.MIN_YEAR))
    config_table.add_row("Max Year", str(Config.MAX_YEAR))
    config_table.add_row("Crosswalk Output Dir", str(Config.HMDA_MBS_CROSSWALK_OUTPUT_DIR))
    config_table.add_row("Output Dir", str(Config.HMDA_MBS_OUTPUT_DIR))
    config_table.add_row("FHFA-HMDA Crosswalk", str(Config.FHFA_HMDA_CROSSWALK))
    config_table.add_row("MBS-FHFA Output Dir", str(Config.MBS_FHFA_OUTPUT_DIR))
    config_table.add_row("MBS-UMBS Output Dir", str(Config.MBS_UMBS_OUTPUT_DIR))

    console.print(config_table)
    console.print()

    # Input data availability table
    data_table = Table(
        title="Input Data Availability", show_header=True, header_style="bold magenta"
    )
    data_table.add_column("Source", style="cyan", width=30)
    data_table.add_column("Status", style="white", width=15)

    # Check FHFA-HMDA crosswalk
    fhfa_hmda_status = (
        "[green]OK[/green]" if Config.FHFA_HMDA_CROSSWALK.exists() else "[red]Missing[/red]"
    )
    data_table.add_row("FHFA-HMDA Crosswalk", fhfa_hmda_status)

    # Check MBS-FHFA crosswalks for each agency and year
    for agency in ["fnma", "fhlmc"]:
        years_available = []
        for year in range(Config.MIN_YEAR, Config.MAX_YEAR + 1):
            path = Config.get_mbs_fhfa_path(agency, year)  # type: ignore[arg-type]
            if path.exists():
                years_available.append(str(year))

        if years_available:
            status = f"[green]{', '.join(years_available)}[/green]"
        else:
            status = "[red]Missing[/red]"
        data_table.add_row(f"MBS-FHFA ({agency.upper()})", status)

    # Check MBS-UMBS crosswalks
    for agency in ["fnma", "fhlmc"]:
        path = Config.get_mbs_umbs_path(agency)  # type: ignore[arg-type]
        status = "[green]OK[/green]" if path.exists() else "[red]Missing[/red]"
        data_table.add_row(f"MBS-UMBS ({agency.upper()})", status)

    console.print(data_table)
    console.print()

    # Output files table
    output_table = Table(title="Output Files", show_header=True, header_style="bold magenta")
    output_table.add_column("File", style="cyan", width=45)
    output_table.add_column("Status", style="white", width=15)

    for agency in ["fnma", "fhlmc"]:
        # Base crosswalk
        base_path = Config.get_output_path(agency, enriched=False)  # type: ignore[arg-type]
        base_status = (
            "[green]Exists[/green]" if base_path.exists() else "[yellow]Not created[/yellow]"
        )
        output_table.add_row(base_path.name, base_status)

        # Enriched crosswalk
        enriched_path = Config.get_output_path(agency, enriched=True)  # type: ignore[arg-type]
        enriched_status = (
            "[green]Exists[/green]" if enriched_path.exists() else "[yellow]Not created[/yellow]"
        )
        output_table.add_row(enriched_path.name, enriched_status)

    console.print(output_table)
    console.print()


@app.command()
def run(
    agency: Annotated[
        str,
        typer.Option(
            "--agency",
            "-a",
            help="Which agency to process (fnma, fhlmc, or both)",
        ),
    ] = "both",
    min_year: Annotated[
        int,
        typer.Option("--min-year", "-m", help="Minimum year to process"),
    ] = Config.MIN_YEAR,
    max_year: Annotated[
        int,
        typer.Option("--max-year", "-M", help="Maximum year to process"),
    ] = Config.MAX_YEAR,
    enrich: Annotated[
        bool,
        typer.Option("--enrich", "-e", help="Create enriched version with HMDA/UMBS data"),
    ] = False,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", "-o", help="Override output directory"),
    ] = None,
) -> None:
    """Run the HMDA-MBS master crosswalk pipeline.

    This command builds master crosswalks that link HMDA loans through
    the FHFA, MBS, and UMBS data for the specified agency (or both).

    The pipeline:
    1. Loads FHFA-HMDA crosswalks for the specified years
    2. Joins with MBS-FHFA crosswalks (extended matching)
    3. Joins with MBS-UMBS crosswalks
    4. Optionally enriches with HMDA loan data and UMBS seller info
    """
    from mortgage_data_manager.matching.match_hmda_mbs.build_master_crosswalk import (
        build_master_crosswalk,
        enrich_crosswalk,
    )

    # Validate agency
    if agency not in ["fnma", "fhlmc", "both"]:
        console.print(
            f"[bold red]Error:[/bold red] Invalid agency '{agency}'. Must be fnma, fhlmc, or both."
        )
        raise typer.Exit(code=1)

    agencies: list[Literal["fnma", "fhlmc"]] = (
        ["fnma", "fhlmc"] if agency == "both" else [agency]  # type: ignore[list-item]
    )

    # Determine output directory
    actual_output_dir = output_dir if output_dir else Config.HMDA_MBS_OUTPUT_DIR

    console.print("\n[bold cyan]HMDA-MBS Master Crosswalk Builder[/bold cyan]")
    console.print(f"  Agencies: {', '.join(a.upper() for a in agencies)}")
    console.print(f"  Years: {min_year}-{max_year}")
    console.print(f"  Enrich: {enrich}")
    console.print(f"  Output: {actual_output_dir}\n")

    # Ensure output directory exists
    Config.ensure_directories()
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    try:
        for ag in agencies:
            # Build crosswalk
            df = build_master_crosswalk(ag, min_year=min_year, max_year=max_year)

            # Determine output path
            if output_dir:
                base_path = output_dir / f"master_crosswalk_{ag}.parquet"
            else:
                base_path = Config.get_output_path(ag, enriched=False)

            # Save base crosswalk
            df.write_parquet(base_path)
            console.print(f"\n[green]Saved:[/green] {base_path}")

            # Enrich if requested
            if enrich:
                enriched_df = enrich_crosswalk(ag, df)
                if output_dir:
                    enriched_path = output_dir / f"master_crosswalk_{ag}_enriched.parquet"
                else:
                    enriched_path = Config.get_output_path(ag, enriched=True)
                enriched_df.write_parquet(enriched_path)
                console.print(f"[green]Saved enriched:[/green] {enriched_path}")

        console.print("\n[bold green]Pipeline complete![/bold green]\n")

    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1)


@app.command()
def fha(
    min_year: Annotated[
        int,
        typer.Option("--min-year", "-m", help="Minimum year to process"),
    ] = 2018,
    max_year: Annotated[
        int,
        typer.Option("--max-year", "-M", help="Maximum year to process"),
    ] = 2024,
    include_direct: Annotated[
        bool,
        typer.Option("--direct/--no-direct", "-d", help="Include direct HMDA-GNMA matching"),
    ] = True,
    exact_year: Annotated[
        bool,
        typer.Option(
            "--exact-year/--year-tolerance", help="Require exact year match for direct matching"
        ),
    ] = True,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", "-o", help="Override output directory"),
    ] = None,
) -> None:
    """Run HMDA-GNMA matching for FHA loans.

    This command builds crosswalks linking HMDA FHA loans to GNMA securitization
    using two methods:

    1. Two-crosswalk: HMDA → FHA-HMDA crosswalk → FHA → FHA-GNMA crosswalk → GNMA
       - Uses existing crosswalks from match_fha_hmda and match_fha_gnma
       - Achieves ~34.5% coverage

    2. Direct matching (optional): HMDA → GNMA via probabilistic record linkage
       - Uses LEI→Issuer mapping from validated two-crosswalk matches
       - Adds ~16% additional coverage

    Combined coverage: ~50.7% of HMDA FHA loans linked to GNMA.
    """
    from mortgage_data_manager.matching.match_hmda_mbs.fha_matching import (
        run_fha_matching_pipeline,
    )

    actual_output_dir = output_dir if output_dir else Config.HMDA_MBS_OUTPUT_DIR

    console.print("\n[bold cyan]HMDA-GNMA FHA Matching Pipeline[/bold cyan]")
    console.print(f"  Years: {min_year}-{max_year}")
    console.print(f"  Include direct matching: {include_direct}")
    if include_direct:
        console.print(f"  Year matching: {'exact' if exact_year else '±1 tolerance'}")
    console.print(f"  Output: {actual_output_dir}\n")

    # Ensure output directory exists
    Config.ensure_directories()
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    try:
        two_crosswalk, direct_matches = run_fha_matching_pipeline(
            min_year=min_year,
            max_year=max_year,
            include_direct=include_direct,
            exact_year=exact_year,
            output_dir=actual_output_dir,
        )

        console.print("\n[bold green]FHA matching pipeline complete![/bold green]\n")

    except FileNotFoundError as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}")
        console.print(
            "[yellow]Hint:[/yellow] Ensure FHA-HMDA and FHA-GNMA crosswalks exist. "
            "Run 'mortgage-data match fha-hmda run' and 'mortgage-data match fha-gnma run' first."
        )
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1)


@app.command()
def lender_crosswalk(
    min_loans: Annotated[
        int,
        typer.Option("--min-loans", "-l", help="Minimum number of matched loans to include a pair"),
    ] = 100,
    channel: Annotated[
        str,
        typer.Option(
            "--channel", "-c", help="Channel to filter on (R=Retail, C=Correspondent, B=Broker)"
        ),
    ] = "R",
    report: Annotated[
        bool,
        typer.Option("--report", "-r", help="Generate markdown documentation report"),
    ] = False,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", "-o", help="Override output directory"),
    ] = None,
) -> None:
    """Build lender/seller crosswalk for name mismatches.

    Identifies HMDA lenders and UMBS sellers that frequently match but have
    different names. These represent mergers, acquisitions, rebrands, affiliates,
    and DBAs worth documenting.

    The analysis focuses on Retail channel by default, where the originating
    lender is expected to be the same as the seller to the GSEs.
    """
    from mortgage_data_manager.matching.match_hmda_mbs.lender_seller_crosswalk import (
        build_lender_seller_crosswalk,
        generate_markdown_report,
    )

    # Determine output path
    if output_dir:
        output_path = output_dir / "lender_seller_crosswalk.parquet"
    else:
        output_path = Config.LENDER_SELLER_CROSSWALK_PATH

    # Ensure directories exist
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Build crosswalk
        crosswalk = build_lender_seller_crosswalk(min_loans=min_loans, channel=channel)

        # Save
        crosswalk.write_parquet(output_path)
        console.print(f"\n[green]Saved:[/green] {output_path}")

        # Generate report if requested
        if report:
            generate_markdown_report(crosswalk)

        console.print("\n[bold green]Lender crosswalk complete![/bold green]\n")

    except FileNotFoundError as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}")
        console.print(
            "[yellow]Hint:[/yellow] Run 'mortgage-data match hmda-mbs run --enrich' first."
        )
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1)


@app.command()
def direct(
    agency: Annotated[
        str,
        typer.Option(
            "--agency",
            "-a",
            help="Which agency to process (fnma, fhlmc, or both)",
        ),
    ] = "both",
    min_year: Annotated[
        int,
        typer.Option("--min-year", "-m", help="Minimum year to process"),
    ] = 2020,
    max_year: Annotated[
        int,
        typer.Option("--max-year", "-M", help="Maximum year to process"),
    ] = 2024,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", "-o", help="Override output directory"),
    ] = None,
) -> None:
    r"""Run direct HMDA-UMBS matching for unmatched GSE-sold loans.

    Matches HMDA loans with purchaser_type=1,3 (GSE sales) that are NOT
    in the chain crosswalk (HMDA->FHFA->MBS->UMBS).

    Uses three-phase approach:

    \b
    1. All channels with lender-seller constraint
    2. Correspondent loans without constraint
    3. Correspondent chains using purchaser LEI from seller/purchaser crosswalk
    """
    from mortgage_data_manager.matching.match_hmda_mbs.direct_match import run_direct_matching

    # Validate agency
    if agency not in ["fnma", "fhlmc", "both"]:
        console.print(
            f"[bold red]Error:[/bold red] Invalid agency '{agency}'. Must be fnma, fhlmc, or both."
        )
        raise typer.Exit(code=1)

    agencies: list[Literal["fnma", "fhlmc"]] = (
        ["fnma", "fhlmc"] if agency == "both" else [agency]  # type: ignore[list-item]
    )

    # Determine output directory
    actual_output_dir = output_dir if output_dir else Config.DIRECT_MATCH_OUTPUT_DIR

    console.print("\n[bold cyan]Direct HMDA-UMBS Matching[/bold cyan]")
    console.print(f"  Agencies: {', '.join(a.upper() for a in agencies)}")
    console.print(f"  Years: {min_year}-{max_year}")
    console.print(f"  Output: {actual_output_dir}\n")

    # Ensure output directory exists
    Config.ensure_directories()
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    try:
        for ag in agencies:
            console.print(f"\n[bold]Processing {ag.upper()}[/bold]")

            # Run direct matching
            matches = run_direct_matching(ag, min_year=min_year, max_year=max_year)

            # Determine output path
            if output_dir:
                output_path = output_dir / f"direct_match_{ag}.parquet"
            else:
                output_path = Config.get_direct_match_output_path(ag)

            # Save results
            matches.write_parquet(output_path)
            console.print(f"\n[green]Saved:[/green] {output_path}")
            console.print(f"  Total matches: {len(matches):,}")

        console.print("\n[bold green]Direct matching complete![/bold green]\n")

    except FileNotFoundError as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}")
        console.print(
            "[yellow]Hint:[/yellow] Ensure master crosswalks and lender_seller_good_matches.parquet exist. "
            "Run 'mortgage-data match hmda-mbs run' and 'mortgage-data match hmda-mbs lender-crosswalk' first."
        )
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1)


@app.command()
def validate(
    min_year: int = typer.Option(
        2018,
        "--min-year",
        help="Minimum activity year",
    ),
    max_year: int = typer.Option(
        2024,
        "--max-year",
        help="Maximum activity year",
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
        help="Save plots to docs/matching/figures/hmda_mbs/",
    ),
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", "-o", help="Override output directory for figures"),
    ] = None,
) -> None:
    """Run validation analysis on HMDA-MBS matches.

    Generates HMDA and UMBS match-rate diagnostics across the combined
    crosswalks (chain + direct match):
    - HMDA match rates by year
    - UMBS match rates by month

    Examples:
      mortgage-data match hmda-mbs validate
      mortgage-data match hmda-mbs validate --show-plots
      mortgage-data match hmda-mbs validate --min-year 2020 --max-year 2023
    """
    from mortgage_data_manager.matching.match_hmda_mbs.validation import run_validation

    console.print("[cyan]Running HMDA-MBS validation analysis...[/cyan]")

    try:
        run_validation(
            min_year=min_year,
            max_year=max_year,
            output_dir=output_dir,
            save_plots=save_plots,
            show_plots=show_plots,
        )
        console.print("[green]Validation complete![/green]")

    except Exception as e:
        console.print(f"[red]Validation failed: {e}[/red]")
        raise typer.Exit(code=1)


def cli_main() -> None:
    """Entry point for HMDA-MBS matching CLI."""
    app()


if __name__ == "__main__":
    cli_main()
