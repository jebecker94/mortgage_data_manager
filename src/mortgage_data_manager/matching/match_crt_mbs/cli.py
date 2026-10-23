"""CRT-MBS Matching CLI.

Typer-based command-line interface for linking GSE credit-risk-transfer
reference pools to the SF loan-level datasets (Freddie STACR/ACIS by
characteristics match, Fannie CAS/CIRT by direct extraction).
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.matching.match_crt_mbs.config import CRTMBSMatchingConfig
from mortgage_data_manager.matching.match_crt_mbs.rounds import ROUNDS_FHLMC

app = typer.Typer(
    name="crt-mbs",
    help="CRT-MBS matching workflow commands (CRT reference pools ↔ SF loan-level)",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()
logger = get_logger(__name__)


@app.callback()
def callback():
    """[bold cyan]CRT-MBS Matching Workflow[/bold cyan].

    Links credit-risk-transfer reference-pool disclosures to the SF loan-level
    datasets: Freddie (STACR/ACIS) via rounds of characteristics matching with
    UPB-at-issuance confirmation; Fannie (CAS/CIRT) via direct extraction of
    the Reference Pool ID / Deal Name fields.
    """
    configure_logging(level="INFO")


@app.command()
def info() -> None:
    """Show the CRT-MBS paths and the FHLMC matching rounds.

    Examples:
      mortgage-data match crt-mbs info
    """
    table = Table(
        title="CRT-MBS Matching Configuration", show_header=True, header_style="bold cyan"
    )
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("FHLMC crosswalk", str(CRTMBSMatchingConfig.FHLMC_CROSSWALK_FILE))
    table.add_row("FNMA crosswalk", str(CRTMBSMatchingConfig.FNMA_CROSSWALK_FILE))
    table.add_row("FHLMC deal summary", str(CRTMBSMatchingConfig.FHLMC_DEAL_SUMMARY_FILE))
    table.add_row(
        "UPB confirmation",
        f"±{CRTMBSMatchingConfig.CONFIRM_MONTH_TOLERANCE} month, "
        f"±${CRTMBSMatchingConfig.CONFIRM_UPB_TOLERANCE}",
    )
    table.add_row("", "")
    table.add_row("[bold]FHLMC rounds[/bold] (rounds.py)", "")
    for number, rnd in enumerate(ROUNDS_FHLMC, start=1):
        table.add_row(f"  Round {number}", f"{rnd.description}: {', '.join(rnd.keys)}")
    table.add_row("FNMA", "deal membership read from the CAS/CIRT files; no rounds")
    console.print(table)


@app.command()
def run(
    agency: str = typer.Option("all", "--agency", "-a", help="GSE to link: fhlmc, fnma, or all"),
    deals: list[str] = typer.Option(
        None,
        "--deals",
        "-d",
        help="FHLMC only: match just these Reference Pool Numbers (default: every deal in "
        "silver/crt)",
    ),
    confirm: bool = typer.Option(
        True,
        "--confirm/--no-confirm",
        help="FHLMC only: confirm pins against the SF performance panel (UPB at issuance, "
        "±1 month)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed progress"),
):
    """Link GSE CRT reference-pool loans to the SF loan-level datasets.

    FHLMC: per deal, the rounds in rounds.py (exact joins, 1:1 both directions) against the SF
    origination silver, then UPB-at-issuance confirmation against the performance panel
    (cent-exact within ±1 month of the deal Period). Output:
    [cyan]crosswalk/crt_mbs/crt_mbs_crosswalk_fhlmc.parquet[/cyan] plus a per-deal summary.

    FNMA: extracts CAS/CIRT deal membership, streaming the [cyan]CAS_*.zip[/cyan] /
    [cyan]CIRT_*.zip[/cyan] historical downloads in fnma raw when present (one ~350 GB
    decompressing pass, ~30 min), else scanning the SF performance bronze. The extracted
    ids are the CRT-specific [cyan]CRT Loan Identifier[/cyan] and do NOT join to the LPPUB
    Loan Identifier. Output: [cyan]crosswalk/crt_mbs/crt_mbs_crosswalk_fnma.parquet[/cyan].

    Examples:
      mortgage-data match crt-mbs run --agency fhlmc
      mortgage-data match crt-mbs run --agency fhlmc -d 19DNA1 -d 24DNA3 --no-confirm
      mortgage-data match crt-mbs run --agency fnma
    """
    configure_logging(level="DEBUG" if verbose else "INFO")
    token = agency.lower()
    if token not in ("fhlmc", "fnma", "all"):
        console.print(f"[red]Invalid agency '{agency}'. Use fhlmc, fnma, or all.[/red]")
        raise typer.Exit(code=1)
    if token in ("fhlmc", "all"):
        _run_fhlmc(deals, confirm)
    if token in ("fnma", "all"):
        _run_fnma()


def _run_fhlmc(deals: list[str] | None, confirm: bool) -> None:
    """The FHLMC leg of ``run``: pin, confirm, print the per-deal summary."""
    from mortgage_data_manager.matching.match_crt_mbs.matching_fhlmc import run_matching_fhlmc

    summary = run_matching_fhlmc(deals=deals or None, confirm=confirm)
    if summary.is_empty():
        console.print("[red]No deals matched — is silver/crt built?[/red]")
        raise typer.Exit(code=1)

    total = int(summary["n_loans"].sum())
    pinned = int(summary["n_pinned"].sum())
    table = Table(title="FHLMC CRT matching summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green", justify="right")
    table.add_row("Deals", f"{summary.height:,}")
    table.add_row("CRT loans", f"{total:,}")
    table.add_row("Pinned 1:1", f"{pinned:,} ({pinned / total * 100:.2f}%)")
    if "n_confirmed" in summary.columns:
        confirmed = summary["n_confirmed"].sum()
        unverifiable = summary["n_unverifiable"].sum()
        table.add_row("UPB-confirmed", f"{confirmed:,}")
        table.add_row("Unverifiable (no perf quarter)", f"{unverifiable:,}")
    console.print(table)

    if pinned == 0:
        raise typer.Exit(code=1)


def _run_fnma() -> None:
    """The FNMA leg of ``run``: extract CAS/CIRT deal membership."""
    from mortgage_data_manager.matching.match_crt_mbs.matching_fnma import run_matching_fnma

    crosswalk = run_matching_fnma()
    if crosswalk.is_empty():
        console.print(
            "[yellow]No populated Reference Pool ID / Deal Name fields found. "
            "Refresh Performance_All.zip (or add CAS/CIRT loan-level files) and re-run.[/yellow]"
        )
        raise typer.Exit(code=1)
    console.print(f"[green]Extracted {crosswalk.height:,} loan-deal rows[/green]")


@app.command()
def check():
    """Report crosswalk health (pin rates, confirmation rates) from disk.

    Reads the written crosswalks and prints summary statistics — a cheap
    regression guard after re-runs.

    Examples:
      mortgage-data match crt-mbs check
    """
    import polars as pl

    any_found = False
    fhlmc_file = CRTMBSMatchingConfig.FHLMC_CROSSWALK_FILE
    if fhlmc_file.exists():
        any_found = True
        xw = pl.read_parquet(fhlmc_file)
        confirmed = xw.filter(pl.col("upb_confirmed") == True).height  # noqa: E712
        checked = xw.filter(pl.col("upb_confirmed").is_not_null()).height
        table = Table(title="FHLMC CRT crosswalk")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green", justify="right")
        table.add_row("Pairs", f"{xw.height:,}")
        table.add_row("Deals", f"{xw['Reference Pool Number'].n_unique():,}")
        for match_round, rnd in enumerate(ROUNDS_FHLMC, start=1):
            n = xw.filter(pl.col("match_round") == match_round).height
            table.add_row(
                f"Round {match_round} ({len(rnd.keys)} keys)", f"{n:,} ({n / xw.height * 100:.1f}%)"
            )
        if checked:
            table.add_row("UPB-confirmed (of checkable)", f"{confirmed / checked * 100:.2f}%")
        table.add_row("Unverifiable (no perf quarter)", f"{xw.height - checked:,}")
        console.print(table)
    else:
        console.print(f"[yellow]No FHLMC crosswalk at {fhlmc_file}[/yellow]")

    fnma_file = CRTMBSMatchingConfig.FNMA_CROSSWALK_FILE
    if fnma_file.exists():
        any_found = True
        xw = pl.read_parquet(fnma_file)
        console.print(
            f"[green]FNMA crosswalk: {xw.height:,} loan-deal rows, "
            f"{xw['Deal Name'].n_unique():,} deals[/green]"
        )
    else:
        console.print(
            f"[yellow]No FNMA crosswalk at {fnma_file} (see `run --agency fnma`)[/yellow]"
        )

    if not any_found:
        raise typer.Exit(code=1)


def cli_main() -> None:
    """Entry point for the CRT-MBS matching CLI."""
    app()


if __name__ == "__main__":
    cli_main()
