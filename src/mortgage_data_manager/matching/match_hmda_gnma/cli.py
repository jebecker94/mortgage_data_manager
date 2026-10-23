"""CLI for the HMDA-GNMA matching workflow (FHA and VA/USDA loans)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, cast

import typer
from rich.console import Console
from rich.table import Table

from mortgage_data_manager.core.logging import configure_logging
from mortgage_data_manager.matching.match_hmda_gnma.config import HMDAGNMAMatchingConfig as Config
from mortgage_data_manager.matching.match_hmda_gnma.config import LoanType

app = typer.Typer(
    name="hmda-gnma",
    help="HMDA-GNMA matching workflow (HMDA FHA and VA/USDA loans ↔ GNMA loan-level)",
    no_args_is_help=True,
)

console = Console()

LOAN_TYPES = ("fha", "va-usda")


@app.callback()
def callback():
    """[bold cyan]HMDA-GNMA Matching Workflow[/bold cyan].

    Links HMDA government originations to GNMA loan-level disclosures, one crosswalk for
    FHA loans and one for VA and USDA loans.
    """
    configure_logging(level="INFO")


@app.command()
def info() -> None:
    """Show the HMDA-GNMA configuration and which crosswalks exist."""
    table = Table(title="HMDA-GNMA Matching", show_header=True, header_style="bold magenta")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Years", f"{Config.MIN_YEAR}-{Config.MAX_YEAR}")
    table.add_row("FHA score floor", str(Config.FHA_MIN_SCORE))
    table.add_row(
        "VA/USDA score floors",
        f"{Config.VA_USDA_MIN_SCORE} (rounds 1-2), {Config.VA_USDA_CAPPED_MIN_SCORE} (round 3)",
    )
    table.add_row("HMDA-FHA crosswalk (input)", str(Config.HMDA_FHA_CROSSWALK_PATH))
    from mortgage_data_manager.matching.match_hmda_gnma.rounds import ROUNDS_FHA, ROUNDS_VA_USDA

    for number, fha in enumerate(ROUNDS_FHA, start=1):
        lo, hi = fha.month_window
        rule = [f"keys {', '.join(fha.keys)}", f"score >= {fha.min_accept}"]
        rule += [f"month diff {lo:+d} to {hi:+d}"]
        table.add_row(f"FHA round {number} (rounds.py)", "; ".join(rule))
    for number, rnd in enumerate(ROUNDS_VA_USDA, start=1):
        rule = [f"keys {', '.join(rnd.hmda_keys)}"]
        rule += [f"+ {', '.join(rnd.sub_keys)}"] if rnd.sub_keys else []
        rule += [f"score >= {rnd.min_accept if rnd.min_accept is not None else 'capped floor'}"]
        rule += [f"reopens round {rnd.reopens}'s capped cells"] if rnd.reopens else []
        table.add_row(f"VA/USDA round {number} (rounds.py)", "; ".join(rule))
    for loan_type in ("fha", "va_usda"):
        path = Config.get_crosswalk_path(loan_type)
        mark = "[green]✓[/green]" if path.exists() else "[yellow]missing[/yellow]"
        table.add_row(f"Crosswalk ({loan_type})", f"{path}  {mark}")
    from mortgage_data_manager.matching.match_hmda_gnma.triangulation import sp_lei_pairs_path

    pairs = sp_lei_pairs_path()
    mark = "[green]✓[/green]" if pairs.exists() else "[yellow]built by validate[/yellow]"
    table.add_row("S/P → LEI pairs (validate)", f"{pairs}  {mark}")
    console.print(table)


@app.command()
def run(
    loan_type: Annotated[
        str,
        typer.Option("--loan-type", "-t", help="Population to match: fha, va-usda, or all"),
    ] = "all",
    min_year: Annotated[
        int,
        typer.Option("--min-year", "-m", help="Minimum HMDA activity year"),
    ] = Config.MIN_YEAR,
    max_year: Annotated[
        int,
        typer.Option("--max-year", "-M", help="Maximum HMDA activity year"),
    ] = Config.MAX_YEAR,
    min_score: Annotated[
        int | None,
        typer.Option(
            "--min-score",
            help="total_score floor (default: FHA 11, >=14 strict; VA/USDA 9 for rounds 1-2, "
            "where score=12 is a medium-confidence pocket)",
        ),
    ] = None,
    capped_min_score: Annotated[
        int | None,
        typer.Option(
            "--capped-min-score",
            help="VA/USDA only: total_score floor for round 3, the capped-cell round "
            "(5 of it is its own key)",
        ),
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir", "-o", help="FHA only: write the crosswalk here instead of crosswalk/"
        ),
    ] = None,
) -> None:
    """Match HMDA government originations to GNMA loan-level disclosures.

    FHA: an FHA-enriched direct match. Each FHA-matched HMDA loan carries its FHA record's
    exact amount and endorsement month (through the hmda_fha crosswalk) plus HMDA's exact
    rate and term, and is scored on units, borrowers, MIP-aware CLTV, month, LEI-issuer
    dyads and DTI. Pairs enter on the score floor, or on absent CLTV/DTI evidence when
    everything present agrees.

    VA/USDA (HMDA loan_type 3/4 -> GNMA Agency V/R): a pure scored direct match with no
    administrative bridge -- exact-rate/term/year blocking in $10k amount bins, fee-aware
    CLTV, one-sided DTI, purpose evidence, LEI-issuer dyads, and purchaser-type-aware
    retail identity alignment, then a third round over the cells the cell cap dropped,
    sub-blocked on purpose x units x borrowers.

    Output: crosswalk/hmda_gnma/hmda_gnma_crosswalk_{fha,va_usda}.parquet.

    Examples:
      mortgage-data match hmda-gnma run
      mortgage-data match hmda-gnma run --loan-type fha --min-score 14
      mortgage-data match hmda-gnma run --loan-type va-usda --capped-min-score 11
    """
    token = loan_type.lower()
    if token not in (*LOAN_TYPES, "all"):
        console.print(f"[red]Invalid loan type '{loan_type}'. Use fha, va-usda, or all.[/red]")
        raise typer.Exit(code=1)
    if token in ("fha", "all"):
        _run_fha(min_year, max_year, min_score, output_dir)
    if token in ("va-usda", "all"):
        _run_va_usda(min_year, max_year, min_score, capped_min_score)


def _run_fha(min_year: int, max_year: int, min_score: int | None, output_dir: Path | None) -> None:
    """The FHA leg of ``run``."""
    from mortgage_data_manager.matching.match_hmda_gnma.matching_fha import run_matching_fha

    console.print("\n[bold cyan]HMDA-GNMA crosswalk (FHA loans)[/bold cyan]")
    console.print(f"  Years: {min_year}-{max_year}")
    console.print(f"  Score floor: {Config.FHA_MIN_SCORE if min_score is None else min_score}\n")
    try:
        counts = run_matching_fha(
            min_year=min_year,
            max_year=max_year,
            min_score=min_score,
            output_path=output_dir / "hmda_gnma_crosswalk_fha.parquet" if output_dir else None,
        )
    except FileNotFoundError as e:
        console.print(f"\n[bold red]Error:[/bold red] {e}")
        console.print(
            "[yellow]Hint:[/yellow] Ensure the hmda_fha crosswalk exists "
            "('mortgage-data match hmda-fha run')."
        )
        raise typer.Exit(code=1) from e

    if counts["total"] == 0:
        console.print("\n[bold red]No FHA matches produced.[/bold red]\n")
        raise typer.Exit(code=1)
    console.print(
        f"\n[bold green]FHA done:[/bold green] {counts['total']:,} matched loans "
        f"({counts['by_score']:,} on score + {counts['by_absence']:,} on absent evidence) "
        f"of {counts['hmda_fha_loans']:,} HMDA FHA originations "
        f"({counts['total'] / counts['hmda_fha_loans']:.1%}).\n"
    )


def _run_va_usda(
    min_year: int, max_year: int, min_score: int | None, capped_min_score: int | None
) -> None:
    """The VA/USDA leg of ``run``."""
    from mortgage_data_manager.matching.match_hmda_gnma.matching_va_usda import (
        run_matching_va_usda,
    )

    floor = Config.VA_USDA_MIN_SCORE if min_score is None else min_score
    capped = Config.VA_USDA_CAPPED_MIN_SCORE if capped_min_score is None else capped_min_score
    console.print("\n[bold cyan]HMDA-GNMA crosswalk (VA/USDA loans)[/bold cyan]")
    console.print(
        f"  Years: {min_year}-{max_year};  score floor: {floor} (capped-cell round: {capped})\n"
    )
    counts = run_matching_va_usda(
        min_year=min_year,
        max_year=max_year,
        min_score=min_score,
        capped_min_score=capped_min_score,
    )
    if counts["total"] == 0:
        console.print("[bold red]No VA/USDA matches produced.[/bold red]")
        raise typer.Exit(code=1)
    console.print(
        f"\n[bold green]VA/USDA done:[/bold green] {counts['total']:,} matches — "
        f"VA {counts['matched_va']:,}/{counts['hmda_va']:,} "
        f"({counts['matched_va'] / counts['hmda_va']:.1%}), "
        f"USDA {counts['matched_usda']:,}/{counts['hmda_usda']:,} "
        f"({counts['matched_usda'] / counts['hmda_usda']:.1%}).\n"
    )


@app.command()
def validate(
    loan_type: Annotated[
        str,
        typer.Option("--loan-type", "-t", help="Crosswalk to score: fha, va-usda, or all"),
    ] = "all",
    crosswalk: Annotated[
        Path | None,
        typer.Option(
            "--crosswalk",
            help="Score this parquet instead (needs HMDAIndex and issuer_id or gnma_loan_id)",
        ),
    ] = None,
    by: Annotated[
        str | None, typer.Option("--by", help="Also score per value of this column")
    ] = None,
    min_year: Annotated[int, typer.Option("--min-year", "-m", help="First HMDA year")] = 2018,
    max_year: Annotated[
        int | None, typer.Option("--max-year", "-M", help="Last HMDA year (default: all)")
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite",
            help="Rebuild the sellers/purchasers -> LEI pair table (after an s/p or HMDA rebuild)",
        ),
    ] = False,
) -> None:
    """Score HMDA-GNMA crosswalks by aggregator ↔ issuer name triangulation.

    `aggregator` is the share of testable pairs whose purchaser (aggregator) panel
    name matches the pool issuer name; `originator_ctrl` is the same test against
    the originator and should stay low. The gauge uses no field the matchers key on.
    The sellers/purchasers -> LEI pair table it scores against is built on first use.

    Examples:
      mortgage-data match hmda-gnma validate
      mortgage-data match hmda-gnma validate --loan-type fha --by source
      mortgage-data match hmda-gnma validate --crosswalk staged.parquet --by match_round
    """
    from mortgage_data_manager.matching.match_hmda_gnma.triangulation import (
        run_build,
        triangulate_crosswalk,
    )

    token = loan_type.lower()
    if token not in (*LOAN_TYPES, "all"):
        console.print(f"[red]Invalid loan type '{loan_type}'. Use fha, va-usda, or all.[/red]")
        raise typer.Exit(code=1)
    if crosswalk is not None:
        targets = [(crosswalk.name, crosswalk)]
    else:
        names = ("fha", "va_usda") if token == "all" else (token.replace("-", "_"),)
        targets = [(name, Config.get_crosswalk_path(cast("LoanType", name))) for name in names]
    missing = [path for _, path in targets if not path.exists()]
    if missing:
        console.print(f"[red]Crosswalk not found: {', '.join(map(str, missing))}[/red]")
        raise typer.Exit(code=1)

    for name, n in run_build(min_year=min_year, max_year=max_year, overwrite=overwrite).items():
        if n:
            console.print(f"[green]Built {name}:[/green] {n:,} rows")

    for label, path in targets:
        res = triangulate_crosswalk(path, by=by, min_year=min_year, max_year=max_year)
        table = Table(
            title=f"Triangulation: {label}", show_header=True, header_style="bold magenta"
        )
        for col in res.columns:
            table.add_column(col, justify="right" if col != "set" else "left")
        for row in res.iter_rows():
            table.add_row(
                *["" if v is None else (f"{v:,}" if isinstance(v, int) else str(v)) for v in row]
            )
        console.print(table)


def cli_main() -> None:
    """Entry point for the HMDA-GNMA matching CLI."""
    app()


if __name__ == "__main__":
    cli_main()
