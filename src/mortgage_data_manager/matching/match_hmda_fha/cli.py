"""CLI for HMDA-FHA matching workflow.

This module provides command-line access to the HMDA-FHA matching pipeline
for linking FHA mortgage records to HMDA loan applications.

The workflow spans two eras with different inputs, keys and rebuild cadences.
They are selected with ``--era`` rather than with separate verbs, following the
package idiom (``mortgage-data fhfa download --era hud``); ``post2018`` is the
default, so every existing invocation keeps its behaviour.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, cast

import typer
from rich.console import Console
from rich.table import Table

from mortgage_data_manager.core.logging import configure_logging
from mortgage_data_manager.matching.match_hmda_fha.config import Era
from mortgage_data_manager.matching.match_hmda_fha.config import HMDAFHAMatchingConfig as Config

app = typer.Typer(
    name="hmda-fha",
    help="HMDA-FHA matching workflow for linking FHA loans to HMDA applications",
    no_args_is_help=True,
)

console = Console()


@app.callback()
def callback():
    """[bold cyan]HMDA-FHA Matching Workflow[/bold cyan].

    Tools for matching FHA mortgage data with HMDA loan applications to create
    loan-level crosswalks.
    """
    configure_logging(level="INFO")


ERAS = ("post2018", "pre2018")

_ERA_HELP = "Matching era: 'post2018' (default) or 'pre2018' (2010-2017)"


def _hive_years(directory: Path, key: str) -> list[str]:
    """Partition values present under ``directory`` for a hive key."""
    if not directory.exists():
        return []
    return [d.name.split("=", 1)[1] for d in sorted(directory.glob(f"{key}=*"))]


def _exists(path: Path) -> str:
    return "[green]Exists[/green]" if path.exists() else "[yellow]Not created[/yellow]"


@app.command()
def info() -> None:
    """Show HMDA-FHA matching configuration and data availability, both eras."""
    console.print("\n[bold cyan]HMDA-FHA Matching Workflow[/bold cyan]\n")

    console.print(
        "This workflow matches FHA (Federal Housing Administration) mortgage data "
        "with HMDA (Home Mortgage Disclosure Act) data to create loan-level crosswalks.\n"
        "Two eras: [cyan]post2018[/cyan] (the default; keys on interest rate) and "
        "[cyan]pre2018[/cyan] (2010-2017; no rate exists, so it keys on\n"
        "ZIP + amount@$1k + purpose and disambiguates with the lender bridge).\n"
    )

    # Configuration table
    config_table = Table(title="Configuration", show_header=True, header_style="bold magenta")
    config_table.add_column("Setting", style="cyan", width=25)
    config_table.add_column("post2018", style="white")
    config_table.add_column("pre2018", style="white")

    config_table.add_row(
        "Year range",
        f"{Config.MIN_YEAR}-{Config.MAX_YEAR}",
        f"{Config.PRE2018_MIN_YEAR}-{Config.PRE2018_MAX_YEAR}",
    )
    config_table.add_row("FHA Silver Dir", str(Config.FHA_SILVER_DIR), "(same)")
    config_table.add_row(
        "HMDA Silver Dir", str(Config.HMDA_SILVER_DIR), str(Config.HMDA_PRE2018_SILVER_DIR)
    )
    config_table.add_row("Crosswalk Output Dir", str(Config.HMDA_FHA_CROSSWALK_DIR), "(same)")
    config_table.add_row("Output Dir", str(Config.HMDA_FHA_OUTPUT_DIR), "(same)")
    config_table.add_row("Intermediate Dir", str(Config.HMDA_FHA_INTERMEDIATE_DIR), "(same)")
    config_table.add_row("USPS Crosswalk Dir", str(Config.USPS_CROSSWALK_DIR), "(same)")

    console.print(config_table)
    console.print()

    # Data availability table
    data_table = Table(title="Data Availability", show_header=True, header_style="bold magenta")
    data_table.add_column("Source", style="cyan", width=20)
    data_table.add_column("Years Available", style="white", width=40)
    data_table.add_column("Status", style="white", width=10)

    for label, years in (
        ("FHA Silver", _hive_years(Config.FHA_SILVER_DIR, "Year")),
        ("HMDA Silver (post)", _hive_years(Config.HMDA_SILVER_DIR, "activity_year")),
        ("HMDA Silver (pre)", _hive_years(Config.HMDA_PRE2018_SILVER_DIR, "activity_year")),
    ):
        status = "[green]OK[/green]" if years else "[red]Missing[/red]"
        data_table.add_row(label, ", ".join(years) if years else "None", status)

    usps_available = (Config.USPS_CROSSWALK_DIR / "ZIP_TRACT").exists()
    usps_status = "[green]OK[/green]" if usps_available else "[red]Missing[/red]"
    data_table.add_row("USPS Crosswalk", "ZIP_TRACT" if usps_available else "None", usps_status)

    # The pre-2018 era cannot run without the lender bridge artifact.
    from mortgage_data_manager.matching.match_hmda_fha.lender_bridge import BRIDGE_NAME

    bridge_path = Config.get_bridge_path(BRIDGE_NAME)
    bridge_status = "[green]OK[/green]" if bridge_path.exists() else "[red]Missing[/red]"
    data_table.add_row(
        "Lender bridge",
        BRIDGE_NAME if bridge_path.exists() else "run `run --era pre2018 --step bridge`",
        bridge_status,
    )

    console.print(data_table)
    console.print()

    # Output files, per era
    output_table = Table(title="Output Files", show_header=True, header_style="bold magenta")
    output_table.add_column("Era", style="cyan", width=10)
    output_table.add_column("File", style="cyan", width=45)
    output_table.add_column("Status", style="white", width=14)

    for era, lo, hi in (
        ("post2018", Config.MIN_YEAR, Config.MAX_YEAR),
        ("pre2018", Config.PRE2018_MIN_YEAR, Config.PRE2018_MAX_YEAR),
    ):
        crosswalk = Config.HMDA_FHA_CROSSWALK_DIR / Config.crosswalk_name(cast("Era", era), lo, hi)
        output_table.add_row(era, crosswalk.name, _exists(crosswalk))
        for stem in (f"fha_match_data_{era}.parquet", f"hmda_match_data_{era}.parquet"):
            output_table.add_row("", stem, _exists(Config.HMDA_FHA_INTERMEDIATE_DIR / stem))

    console.print(output_table)
    console.print()

    _print_rounds()

    if bridge_path.exists():
        _print_bridge_composition()


def _print_rounds() -> None:
    """Print both eras' rounds (``rounds.py``), one row per round in run order."""
    from mortgage_data_manager.matching.match_hmda_fha.rounds import (
        ROUNDS_POST2018,
        ROUNDS_PRE2018,
    )

    table = Table(
        title="Post-2018 rounds (rounds.py)", show_header=True, header_style="bold magenta"
    )
    for column in ("Round", "Description", "Geography", "Window", "Rate tol.", "Gates", "Resolve"):
        table.add_column(column, style="cyan" if column == "Round" else "white")
    for number, rnd in enumerate(ROUNDS_POST2018, start=1):
        gates = ["purpose"]
        gates += ["county"] if rnd.county_check else []
        gates += ["sponsor"] if rnd.sponsor_filter else []
        gates += ["lender"] if rnd.lender_filter else []
        resolve = "1:1 or drop" if rnd.min_score is None else f"scored, floor {rnd.min_score}"
        if rnd.learns_roster:
            resolve += "; learns roster"
        table.add_row(
            str(number),
            rnd.description,
            ", ".join(rnd.geo),
            f"{rnd.window[0]:+d}..{rnd.window[1]:+d} mo",
            f"{rnd.rate_tolerance}",
            ", ".join(gates),
            resolve,
        )
    console.print(table)
    console.print()

    table = Table(
        title="Pre-2018 rounds (rounds.py)", show_header=True, header_style="bold magenta"
    )
    for column in ("Round", "Description", "Window", "Lender bridge", "Resolve"):
        table.add_column(column, style="cyan" if column == "Round" else "white")
    for number, pre in enumerate(ROUNDS_PRE2018, start=1):
        resolve = "1:1 or drop" if not pre.tiers else f"margin tiers: {', '.join(pre.tiers)}"
        if pre.learns_rate_offset:
            resolve += "; learns rate offset"
        table.add_row(
            str(number),
            pre.description,
            f"{pre.window[0]:+d}..{pre.window[1]:+d} mo",
            "filter" if pre.bridge_filter else "-",
            resolve,
        )
    console.print(table)
    console.print()


def _print_bridge_composition() -> None:
    """Print the built pre-2018 lender bridge's composition by tier and provenance."""
    import polars as pl

    from mortgage_data_manager.matching.match_hmda_fha.lender_bridge import load_bridge

    built = load_bridge()
    lenders = built.select("fha_id_type", "fha_id").unique().height
    console.print(
        f"[bold]Lender bridge (pre-2018):[/bold] {len(built):,} entries over {lenders:,} FHA "
        f"lenders, {built.select('resp_key', 'agency_code').unique().height:,} HMDA respondents "
        f"({int(built['era_tagged'].sum()):,} era-tagged)"
    )
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Tier", style="cyan", justify="right")
    table.add_column("Rule", style="cyan")
    table.add_column("Entries", justify="right")
    table.add_column("FHA lenders", justify="right")
    table.add_column("FHA loans", justify="right")
    composition = (
        built.unique(subset=["name_tier", "name_rule", "fha_id_type", "fha_id"])
        .group_by("name_tier", "name_rule")
        .agg(lenders=pl.len(), loans=pl.col("fha_loans").sum())
        .join(
            built.group_by("name_tier", "name_rule").agg(entries=pl.len()),
            on=["name_tier", "name_rule"],
        )
        .sort(["name_tier", "loans"], descending=[False, True])
    )
    for row in composition.iter_rows(named=True):
        table.add_row(
            str(row["name_tier"]),
            row["name_rule"],
            f"{row['entries']:,}",
            f"{row['lenders']:,}",
            f"{row['loans']:,}",
        )
    console.print(table)

    provenance = Table(show_header=True, header_style="bold magenta")
    provenance.add_column("Provenance", style="cyan")
    provenance.add_column("Entries", justify="right")
    for row in built["provenance"].value_counts().sort("provenance").iter_rows(named=True):
        provenance.add_row(row["provenance"], f"{row['count']:,}")
    console.print(provenance)
    console.print()


@app.command()
def run(
    era: Annotated[
        str,
        typer.Option("--era", help=_ERA_HELP),
    ] = "post2018",
    step: Annotated[
        str,
        typer.Option(
            "--step",
            help="match (default), or bridge: pre2018 only, build just the lender bridge "
            "the pre-2018 match reads",
        ),
    ] = "match",
    min_year: Annotated[
        int | None,
        typer.Option("--min-year", "-m", help="Minimum year to match (default: the era's)"),
    ] = None,
    max_year: Annotated[
        int | None,
        typer.Option("--max-year", "-M", help="Maximum year to match (default: the era's)"),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Rebuild the data-prep intermediates even if they exist"),
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
    """Run the HMDA-FHA matching pipeline for one era.

    [cyan]--era post2018[/cyan] (default):
    1. Prepare FHA data
    2. Prepare HMDA data (with ZIP-tract crosswalk)
    3. Run the rounds in rounds.py (`info` prints them), each on the earlier rounds'
       leftovers, and write one crosswalk tagged with match_round

    [cyan]--era pre2018[/cyan] (2010-2017):
    1. Prepare the FHA endorsement pool
    2. Prepare the ZIP-fanned HMDA origination pool
    3. Run the rounds in rounds.py (`info` prints them), each on the earlier rounds'
       leftovers: base key (ZIP + amount@$1k + purpose) inside the round's endorsement
       window, the optional lender-bridge filter, then 1:1 or margin tiers; write one
       crosswalk tagged with match_round

    [cyan]--era pre2018 --step bridge[/cyan] builds only the FHA-mortgagee ↔
    HMDA-respondent bridge that step 3 reads. It reads FHA silver, the HMDA panel and
    the HMDA loan register -- never a crosswalk -- so the yield it unlocks is not
    circular. Both sources are closed, so it rebuilds on a method change or an
    upstream silver rebuild (with --overwrite), not on the annual HMDA cycle.

    Examples:
      mortgage-data match hmda-fha run
      mortgage-data match hmda-fha run --era pre2018 --step bridge
      mortgage-data match hmda-fha run --era pre2018
    """
    if era not in ERAS:
        console.print(f"[bold red]Error:[/bold red] --era must be one of: {', '.join(ERAS)}\n")
        raise typer.Exit(code=1)
    if step not in ("match", "bridge"):
        console.print("[bold red]Error:[/bold red] --step must be one of: match, bridge\n")
        raise typer.Exit(code=1)

    if step == "bridge":
        if era != "pre2018":
            console.print("[bold red]Error:[/bold red] --step bridge is pre2018 only\n")
            raise typer.Exit(code=1)
        _run_bridge(min_year, max_year, overwrite)
        return

    era_min, era_max = Config.year_range_for_era(cast("Era", era))
    min_year = era_min if min_year is None else min_year
    max_year = era_max if max_year is None else max_year

    console.print("\n[bold cyan]Running HMDA-FHA Matching Pipeline[/bold cyan]")
    console.print(f"  Era:   {era}")
    console.print(f"  Years: {min_year} - {max_year}")

    try:
        if era == "pre2018":
            from mortgage_data_manager.matching.match_hmda_fha.matching_pre2018 import (
                run_matching_pre2018,
            )

            console.print(f"  Overwrite data prep: {overwrite}\n")
            output_file = run_matching_pre2018(
                min_year=min_year,
                max_year=max_year,
                fha_folder=fha_dir,
                hmda_folder=hmda_dir,
                output_folder=output_dir,
                overwrite=overwrite,
            )
        else:
            from mortgage_data_manager.matching.match_hmda_fha.matching_post2018 import (
                run_matching,
            )

            console.print(f"  Overwrite data prep: {overwrite}\n")
            output_file = run_matching(
                min_year=min_year,
                max_year=max_year,
                fha_folder=fha_dir,
                hmda_folder=hmda_dir,
                output_folder=output_dir,
                overwrite=overwrite,
            )
        console.print("\n[bold green]Matching complete![/bold green]")
        console.print(f"Output file: {output_file}\n")
    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1)


def _run_bridge(min_year: int | None, max_year: int | None, overwrite: bool) -> None:
    """``run --era pre2018 --step bridge``: build the pre-2018 lender bridge."""
    from mortgage_data_manager.matching.match_hmda_fha.lender_bridge import run_build

    era_min, era_max = Config.year_range_for_era("pre2018")
    counts = run_build(
        min_year=era_min if min_year is None else min_year,
        max_year=era_max if max_year is None else max_year,
        overwrite=overwrite,
    )
    if not any(counts.values()):
        console.print("[yellow]Nothing built (artifacts exist; use --overwrite).[/yellow]")
        raise typer.Exit(code=1)
    for name, n in counts.items():
        console.print(f"[green]{name}:[/green] {n:,} rows")


@app.command()
def build_zip_tract(
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
    from mortgage_data_manager.matching.match_hmda_fha.config import HMDAFHAMatchingConfig as Config
    from mortgage_data_manager.matching.match_hmda_fha.matching_post2018 import (
        build_zip_tract_crosswalk,
    )

    if census_vintage not in (2010, 2020):
        console.print("[red]Error: Census vintage must be 2010 or 2020[/red]")
        raise typer.Exit(code=1)

    if output_path is None:
        output_path = Config.HMDA_FHA_ZIP_TRACT_DIR / f"zip_tract_{census_vintage}_census.parquet"

    console.print(f"\n[bold cyan]Building {census_vintage} Census Crosswalk[/bold cyan]")
    console.print(f"  USPS crosswalk source: {Config.USPS_CROSSWALK_DIR}")
    console.print(f"  Output: {output_path}\n")

    try:
        build_zip_tract_crosswalk(Config.USPS_CROSSWALK_DIR, census_vintage, output_path)  # type: ignore
        console.print("\n[bold green]Crosswalk built successfully![/bold green]\n")
    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1)


@app.command()
def validate(
    era: Annotated[
        str,
        typer.Option("--era", help="Matching era: 'post2018' (default) or 'pre2018' (2010-2017)"),
    ] = "post2018",
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
    skip_placebo: Annotated[
        bool,
        typer.Option("--skip-placebo", help="pre2018 only: skip the bridge placebo controls"),
    ] = False,
) -> None:
    """Run validation analysis and generate diagnostic figures.

    Checks the HMDA-FHA crosswalk's match quality by year, geography and
    loan characteristics.

    Each era writes one combined figure plus the state and county choropleths
    (plotly, so they cannot share the grid) into its own figure directory.

    --era post2018 (default) panels: match rates over time, by loan amount, by
    interest rate, by loan purpose, by rate type, by HMDA submission channel,
    and the state and county size-vs-match-rate scatters.

    --era pre2018 panels: match rates by year, round contribution, the
    lender-bridge placebo controls, loan amount, loan purpose, and the same
    size-vs-match-rate scatters. It also prints the era's own evidence -
    agreement with the frozen 2010-2017 reference artifact, the held-out
    round-1 lender-agreement test, and the selection-bias tables. There is no
    interest-rate panel; pre-2018 HMDA carries no interest rate.

    Era-invalid flags are rejected rather than ignored.
    """
    from mortgage_data_manager.matching.match_hmda_fha.validation import (
        VALIDATION_OUTPUT_DIR,
        run_validation,
        run_validation_pre2018,
    )

    if era not in ("post2018", "pre2018"):
        console.print(f"\n[bold red]Error:[/bold red] unknown era {era!r}\n")
        raise typer.Exit(code=1)
    if skip_placebo and era != "pre2018":
        console.print("\n[bold red]Error:[/bold red] --skip-placebo is pre2018 only\n")
        raise typer.Exit(code=1)

    if output_dir is None:
        output_dir = VALIDATION_OUTPUT_DIR

    console.print("\n[bold cyan]Running HMDA-FHA Match Validation[/bold cyan]")
    console.print(f"  Era: {era}")
    console.print(f"  Output directory: {output_dir}")
    console.print(f"  Save plots: {save_plots}")
    console.print(f"  Show plots: {show_plots}")
    console.print(f"  Min loans (county filter): {min_loans}\n")

    try:
        if era == "pre2018":
            results = run_validation_pre2018(
                output_dir=output_dir,
                save_plots=save_plots,
                show_plots=show_plots,
                min_loans=min_loans,
                skip_placebo=skip_placebo,
            )
        else:
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

        summary_table.add_row("Overall FHA Match Rate", f"{results['overall_fha_rate']:.1%}")
        summary_table.add_row("Overall HMDA Match Rate", f"{results['overall_hmda_rate']:.1%}")
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
    """Entry point for HMDA-FHA matching CLI."""
    app()


if __name__ == "__main__":
    cli_main()
