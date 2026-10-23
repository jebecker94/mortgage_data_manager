"""CLI for the HMDA-GSE matching workflow (the chain and the direct match)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, cast

import typer
from rich.console import Console
from rich.table import Table

from mortgage_data_manager.core.logging import configure_logging
from mortgage_data_manager.core.medallion import atomic_output
from mortgage_data_manager.matching.match_hmda_gse.config import HMDAGSEMatchingConfig as Config

app = typer.Typer(
    name="hmda-gse",
    help="HMDA-GSE matching workflow: HMDA conventional loans ↔ GSE loan-level records, by "
    "the chain (HMDA → FHFA → MBS → UMBS) and by a direct match",
    no_args_is_help=True,
)

console = Console()


@app.callback()
def callback():
    """[bold cyan]HMDA-GSE Matching Workflow[/bold cyan].

    Links HMDA conventional originations to the FNMA and FHLMC loan-level records by two
    routes: the chain through the FHFA, MBS and UMBS crosswalks, and a direct match against
    the combined loan-issuance spine.
    """
    configure_logging(level="INFO")


@app.command()
def info() -> None:
    """Show information about HMDA-GSE matching configuration and data availability."""
    console.print("\n[bold cyan]HMDA-GSE Matching Workflow[/bold cyan]\n")

    console.print(
        "This workflow links HMDA conventional loans to FNMA and FHLMC loan-level records, "
        "by the chain (HMDA → FHFA → MBS → UMBS) and by a direct match.\n"
    )

    # Configuration table
    config_table = Table(title="Configuration", show_header=True, header_style="bold magenta")
    config_table.add_column("Setting", style="cyan", width=25)
    config_table.add_column("Value", style="white")

    config_table.add_row("Min Year", str(Config.MIN_YEAR))
    config_table.add_row("Max Year", str(Config.MAX_YEAR))
    config_table.add_row("Crosswalk Output Dir", str(Config.HMDA_GSE_CROSSWALK_DIR))
    config_table.add_row("Output Dir", str(Config.HMDA_GSE_OUTPUT_DIR))
    config_table.add_row("HMDA-FHFA Crosswalk", str(Config.HMDA_FHFA_CROSSWALK))
    config_table.add_row("FHFA-MBS Output Dir", str(Config.FHFA_MBS_OUTPUT_DIR))
    config_table.add_row("MBS-UMBS Output Dir", str(Config.MBS_UMBS_OUTPUT_DIR))

    console.print(config_table)
    console.print()

    # Input data availability table
    data_table = Table(
        title="Input Data Availability", show_header=True, header_style="bold magenta"
    )
    data_table.add_column("Source", style="cyan", width=30)
    data_table.add_column("Status", style="white", width=15)

    # Check HMDA-FHFA crosswalk
    hmda_fhfa_status = (
        "[green]OK[/green]" if Config.HMDA_FHFA_CROSSWALK.exists() else "[red]Missing[/red]"
    )
    data_table.add_row("HMDA-FHFA Crosswalk", hmda_fhfa_status)

    # Check FHFA-MBS crosswalks for each agency and year
    for agency in ["fnma", "fhlmc"]:
        years_available = []
        for year in range(Config.MIN_YEAR, Config.MAX_YEAR + 1):
            path = Config.get_fhfa_mbs_path(agency, year)  # type: ignore[arg-type]
            if path.exists():
                years_available.append(str(year))

        if years_available:
            status = f"[green]{', '.join(years_available)}[/green]"
        else:
            status = "[red]Missing[/red]"
        data_table.add_row(f"FHFA-MBS ({agency.upper()})", status)

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
        base_path = Config.get_chain_crosswalk_path(agency, enriched=False)  # type: ignore[arg-type]
        base_status = (
            "[green]Exists[/green]" if base_path.exists() else "[yellow]Not created[/yellow]"
        )
        output_table.add_row(base_path.name, base_status)

        # Enriched crosswalk
        enriched_path = Config.get_chain_crosswalk_path(agency, enriched=True)  # type: ignore[arg-type]
        enriched_status = (
            "[green]Exists[/green]" if enriched_path.exists() else "[yellow]Not created[/yellow]"
        )
        output_table.add_row(enriched_path.name, enriched_status)

    console.print(output_table)
    console.print()
    _print_rounds()


def _print_rounds() -> None:
    """Print the direct matcher's rounds (``rounds.py``) in run order, after its bridge pass."""
    from mortgage_data_manager.matching.match_hmda_gse.rounds import BRIDGE_PASS, ROUNDS

    table = Table(title="Direct rounds (rounds.py)", show_header=True, header_style="bold magenta")
    for column in ("Round", "Description", "Seller name", "Score floor", "Cells"):
        table.add_column(column, style="cyan" if column == "Round" else "white")
    for label, rnd in [("bridge", BRIDGE_PASS), *((str(n), r) for n, r in enumerate(ROUNDS, 1))]:
        table.add_row(
            label,
            rnd.description,
            "via bridge" if rnd.name_pinned else "-",
            f"{rnd.score_floor} of 7",
            "all" if rnd.cell_cap is None else f"<= {rnd.cell_cap} pairs (full pool)",
        )
    console.print(table)
    bridge = Config.get_bridge_path()
    status = "[green]saved[/green]" if bridge.exists() else "[yellow]not built[/yellow]"
    console.print(f"Lender bridge: {bridge.name} ({status})\n")


ROUTES = ("chain", "direct")


@app.command()
def run(
    route: Annotated[
        str,
        typer.Option(
            "--route",
            "-r",
            help="chain (HMDA → FHFA → MBS → UMBS), direct (HMDA ↔ combined loan "
            "issuance), or all (chain first: the direct match reads it)",
        ),
    ] = "all",
    agency: Annotated[
        str,
        typer.Option("--agency", "-a", help="Chain only: fnma, fhlmc, or all"),
    ] = "all",
    min_year: Annotated[
        int,
        typer.Option("--min-year", "-m", help="Minimum HMDA activity year"),
    ] = Config.MIN_YEAR,
    max_year: Annotated[
        int,
        typer.Option("--max-year", "-M", help="Maximum HMDA activity year"),
    ] = Config.MAX_YEAR,
    enrich: Annotated[
        bool,
        typer.Option("--enrich", "-e", help="Chain only: also write the enriched crosswalk"),
    ] = False,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", "-o", help="Chain only: write the crosswalks here"),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite",
            help="Direct only: rebuild years that already exist and rerun the bridge pass",
        ),
    ] = False,
    keep_double_claims: Annotated[
        bool,
        typer.Option(
            "--keep-double-claims",
            help="Direct only: admit increment rows whose target the chain already claims "
            "(not recommended)",
        ),
    ] = False,
    saved_bridge: Annotated[
        bool,
        typer.Option(
            "--saved-bridge",
            help="Direct only: block on the saved lender bridge instead of rebuilding it",
        ),
    ] = False,
) -> None:
    """Build the HMDA-GSE crosswalks.

    The chain composes the upstream crosswalks per enterprise: HMDA-FHFA, then FHFA-MBS
    (extended matching), then MBS-UMBS; --enrich adds HMDA loan fields and UMBS seller
    info. Output: crosswalk/hmda_gse/hmda_gse_crosswalk_chain_{agency}.parquet.

    The direct match links HMDA straight to the combined loan-issuance spine (no FHFA
    leg). It first builds the lender bridge (lender names from HMDA's own filings, plus
    names learned from a characteristics-only pass) and saves it, or with --saved-bridge
    reads the saved one. Then the rounds in rounds.py run in order, each on the loans
    earlier rounds left: a name-pinned round and a nameless one, blocking on ZIP3, $10k
    amount bin, 3-decimal rate, term, coarse purpose and occupancy, with a quarter-aware
    origination window. The rounds always run over the whole year window; the year
    options choose which files are written. Every row carries its ``disposition``
    against the chain and an ``fhfa_verdict``. Output:
    crosswalk/hmda_gse/hmda_gse_crosswalk_direct_{year}.parquet and
    hmda_gse_lender_bridge.parquet.

    Examples:
      mortgage-data match hmda-gse run
      mortgage-data match hmda-gse run --route chain --agency fnma --enrich
      mortgage-data match hmda-gse run --route direct --min-year 2024 --overwrite
      mortgage-data match hmda-gse run --route direct --saved-bridge --overwrite
    """
    token = route.lower()
    if token not in (*ROUTES, "all"):
        console.print(
            f"[bold red]Error:[/bold red] Invalid route '{route}'. Use chain, direct, or all."
        )
        raise typer.Exit(code=1)
    if token in ("chain", "all"):
        _run_chain(agency, min_year, max_year, enrich, output_dir)
    if token in ("direct", "all"):
        _run_direct(
            min_year,
            max_year,
            overwrite,
            keep_double_claims,
            saved_bridge,
            required=token == "direct",
        )


def _run_chain(
    agency: str, min_year: int, max_year: int, enrich: bool, output_dir: Path | None
) -> None:
    """The chain leg of ``run``."""
    from mortgage_data_manager.matching.match_hmda_gse.matching_chain import (
        enrich_crosswalk,
        run_matching_chain,
    )

    if agency not in ("fnma", "fhlmc", "all"):
        console.print(
            f"[bold red]Error:[/bold red] Invalid agency '{agency}'. Must be fnma, fhlmc, or all."
        )
        raise typer.Exit(code=1)

    agencies: list[Literal["fnma", "fhlmc"]] = (
        ["fnma", "fhlmc"] if agency == "all" else [cast(Literal["fnma", "fhlmc"], agency)]
    )

    # Determine output directory
    actual_output_dir = output_dir if output_dir else Config.HMDA_GSE_OUTPUT_DIR

    console.print("\n[bold cyan]HMDA-GSE chain crosswalks[/bold cyan]")
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
            df = run_matching_chain(ag, min_year=min_year, max_year=max_year)

            # Determine output path
            if output_dir:
                base_path = output_dir / f"hmda_gse_crosswalk_chain_{ag}.parquet"
            else:
                base_path = Config.get_chain_crosswalk_path(ag, enriched=False)

            # Save base crosswalk
            with atomic_output(base_path) as tmp:
                df.write_parquet(tmp)
            console.print(f"\n[green]Saved:[/green] {base_path}")

            # Enrich if requested
            if enrich:
                enriched_df = enrich_crosswalk(ag, df)
                if output_dir:
                    enriched_path = output_dir / f"hmda_gse_crosswalk_chain_{ag}_enriched.parquet"
                else:
                    enriched_path = Config.get_chain_crosswalk_path(ag, enriched=True)
                enriched_df.write_parquet(enriched_path)
                console.print(f"[green]Saved enriched:[/green] {enriched_path}")

        console.print("\n[bold green]Chain complete![/bold green]\n")

    except Exception as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1)


def _run_direct(
    min_year: int,
    max_year: int,
    overwrite: bool,
    keep_double_claims: bool,
    saved_bridge: bool,
    *,
    required: bool,
) -> None:
    """The direct leg of ``run``; with ``required``, building nothing is an error."""
    from mortgage_data_manager.matching.match_hmda_gse.matching_direct import run_matching_direct

    console.print("\n[bold cyan]HMDA-GSE direct crosswalks[/bold cyan]")
    console.print(f"  Years: {min_year}-{max_year}\n")
    try:
        written = run_matching_direct(
            min_year=min_year,
            max_year=max_year,
            overwrite=overwrite,
            record_exclusive=not keep_double_claims,
            saved_bridge=saved_bridge,
        )
    except FileNotFoundError as e:
        console.print(f"[bold red]Error:[/bold red] {e}\n")
        raise typer.Exit(code=1)
    if not written:
        console.print(
            "[yellow]Nothing built[/yellow] (all years present; use --overwrite to rebuild).\n"
        )
        if required:
            raise typer.Exit(code=1)
        return
    for year, n in sorted(written.items()):
        console.print(f"  {year}: {n:,} rows")
    console.print(f"\n[bold green]Done:[/bold green] {sum(written.values()):,} rows.\n")


def _validate_direct(min_year: int, max_year: int) -> None:
    """``validate --direct``: score the direct crosswalk on held-out fields, by cell.

    Reports held-out borrower agreement (against a shuffled floor), destination agreement
    from purchaser_type, and chain concordance, split by disposition and FHFA verdict.
    None of the three is a matcher input.
    """
    import polars as pl

    from mortgage_data_manager.matching.match_hmda_gse.matching_direct import run_validation_direct

    frames = []
    for year in range(min_year, max_year + 1):
        if not Config.get_direct_crosswalk_path(year).exists():
            console.print(f"[yellow]{year}: not built; skipping[/yellow]")
            continue
        frames.append(run_validation_direct(year))
    if not frames:
        console.print("[bold red]No built years to validate.[/bold red]")
        raise typer.Exit(code=1)

    result = pl.concat(frames, how="diagonal_relaxed")
    table = Table(show_header=True, header_style="bold magenta")
    for col, just in [
        ("year", "right"),
        ("disposition", "left"),
        ("fhfa_verdict", "left"),
        ("n", "right"),
        ("borrowers", "right"),
        ("destination", "right"),
        ("concordance", "right"),
    ]:
        table.add_column(col, justify=just)  # type: ignore[arg-type]
    fmt = lambda v: "-" if v is None else f"{v:.4f}"  # noqa: E731
    for row in result.sort("activity_year", "n", descending=[False, True]).iter_rows(named=True):
        table.add_row(
            str(row["activity_year"]),
            row["disposition"],
            row["fhfa_verdict"],
            f"{row['n']:,}",
            fmt(row["borrowers"]),
            fmt(row["destination"]),
            fmt(row["concordance"]),
        )
    console.print(table)
    floor = result.select(pl.col("borrowers_shuffled").mean()).item() or 0.0
    console.print(
        f"\n[dim]Shuffled borrower floor ~{floor:.4f}; "
        f"chain-confirmed rows benchmark ~0.998.[/dim]\n"
    )


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
    from mortgage_data_manager.matching.match_hmda_gse.lender_seller_crosswalk import (
        build_lender_seller_crosswalk,
        generate_markdown_report,
    )

    # Determine output path
    if output_dir:
        output_path = output_dir / "hmda_gse_lender_seller_crosswalk.parquet"
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
            "[yellow]Hint:[/yellow] Run 'mortgage-data match hmda-gse run --route chain --enrich' first."
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
        2025,
        "--max-year",
        help="Maximum activity year",
    ),
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
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", "-o", help="Override output directory for the figure"),
    ] = None,
    direct: Annotated[
        bool,
        typer.Option(
            "--direct",
            help="Instead score the direct crosswalk on held-out fields (borrowers, "
            "destination, chain concordance), by disposition and FHFA verdict",
        ),
    ] = False,
) -> None:
    """Run validation analysis on HMDA-GSE matches.

    Writes one combined figure, hmda_gse_validation.png, covering both directions
    across the combined crosswalks (chain + direct match): the HMDA-to-UMBS match
    rate by activity year, and the UMBS-to-HMDA match rate by first payment date.

    Examples:
      mortgage-data match hmda-gse validate --show-plots
      mortgage-data match hmda-gse validate --min-year 2020 --max-year 2023
      mortgage-data match hmda-gse validate --direct
    """
    if direct:
        _validate_direct(min_year, max_year)
        return

    from mortgage_data_manager.matching.match_hmda_gse.validation import run_validation

    console.print("[cyan]Running HMDA-GSE validation analysis...[/cyan]")

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
    """Entry point for HMDA-GSE matching CLI."""
    app()


if __name__ == "__main__":
    cli_main()
