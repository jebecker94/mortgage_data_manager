"""Master crosswalk CLI.

Typer-based interface for assembling the HMDA spine from every other matching
workflow's crosswalk, QC'ing the build, and promoting it to
``crosswalk/master/``.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from pathlib import Path

from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.matching.master.config import MasterCrosswalkConfig as Config

app = typer.Typer(
    name="master",
    help="Master crosswalk: the HMDA spine assembled from every matching workflow",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()
logger = get_logger(__name__)


@app.callback()
def callback() -> None:
    """[bold cyan]Master Crosswalk[/bold cyan].

    One row per post-2018 HMDA loan appearing in at least one matching edge,
    with nullable downstream ids, ``in_*`` coverage flags and a ``channel``.
    The conventional edge is the GSE chain UNION the direct HMDA-issuance
    matcher; ``mbs_source`` records which path supplied each row.
    """
    configure_logging(level="INFO")


def _coverage_table(qc: dict, title: str) -> Table:
    """Render the coverage + provenance block of a QC result."""
    table = Table(title=title)
    table.add_column("Metric", style="cyan")
    table.add_column("Loans", style="green", justify="right")
    table.add_column("Share of spine", style="magenta", justify="right")
    total = qc["total_rows"]
    table.add_row("spine (unique HMDA loans)", f"{total:,}", "100.0%")
    for flag, n in qc["coverage"].items():
        table.add_row(flag, f"{n:,}", f"{n / total * 100:.1f}%" if total else "-")
    for source, n in qc["mbs_source"].items():
        table.add_row(
            f"  mbs_source={source}", f"{n:,}", f"{n / total * 100:.1f}%" if total else "-"
        )
    return table


@app.command()
def build(
    min_year: int = typer.Option(
        None, "--min-year", help=f"First activity year (default {Config.MIN_YEAR})"
    ),
    max_year: int = typer.Option(
        None, "--max-year", help=f"Last activity year (default {Config.MAX_YEAR})"
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Rebuild years already staged"),
    promote: bool = typer.Option(
        True, "--promote/--no-promote", help="Promote to crosswalk/master/ when QC passes"
    ),
    verify: bool = typer.Option(
        True,
        "--verify/--no-verify",
        help="Re-run QC over the whole promoted artifact (catches pooled violations)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed progress"),
) -> None:
    """Assemble the spine, QC it, and promote it to [cyan]crosswalk/master/[/cyan].

    The build lands in a private staging tree first so a QC failure can never
    replace a good artifact; promotion is partition-scoped, so a single-year
    rebuild only ever touches that year.

    Staging QC sees only the years just built, so the POOLED invariants (target
    exclusivity across every year) are re-checked against the whole artifact
    after promotion. That second pass is what would catch a partial rebuild
    leaving the artifact internally inconsistent.

    Examples:
      mortgage-data match master build
      mortgage-data match master build --min-year 2024 --overwrite
      mortgage-data match master build --no-promote      # stage + QC only
    """
    from mortgage_data_manager.matching.master.build import build_master_crosswalk
    from mortgage_data_manager.matching.master.build import promote as promote_partitions
    from mortgage_data_manager.matching.master.qc import run_qc

    configure_logging(level="DEBUG" if verbose else "INFO")

    written = build_master_crosswalk(min_year=min_year, max_year=max_year, overwrite=overwrite)
    if not written:
        console.print(
            "[yellow]Nothing built — every requested year was already staged. "
            "Pass --overwrite to rebuild.[/yellow]"
        )
        raise typer.Exit(code=1)

    stamp = dt.date.today().isoformat()
    qc = run_qc(staging=True, stamp=stamp)
    console.print(_coverage_table(qc, "Master crosswalk (staged)"))

    if not qc["passed"]:
        console.print(f"[red]QC FAILED: {', '.join(qc['failures'])} — refusing to promote.[/red]")
        console.print(f"Details: {Config.get_qc_path(stamp)}")
        raise typer.Exit(code=1)
    console.print("[green]QC PASS[/green]")

    if not promote:
        console.print(f"[cyan]Staged at {Config.MASTER_STAGING_DIR} (not promoted).[/cyan]")
        return
    promoted = promote_partitions(years=sorted(written))
    console.print(
        f"[green]Promoted {len(promoted)} partition(s) -> {Config.MASTER_CROSSWALK_DIR}[/green]"
    )

    if not verify:
        return
    # distinct stamp so the pooled result does not overwrite the staging one
    final = run_qc(staging=False, stamp=f"{stamp}-promoted")
    if not final["passed"]:
        console.print(
            f"[red]Promoted artifact FAILS pooled QC: {', '.join(final['failures'])}.[/red]\n"
            "A partial rebuild can leave cross-year invariants violated — rebuild every year "
            "(`match master build --overwrite`) and re-check."
        )
        raise typer.Exit(code=1)
    console.print(
        f"[green]Pooled QC PASS over {final['total_rows']:,} loans "
        f"({len(final['per_year'])} partitions)[/green]"
    )


@app.command()
def qc(
    crosswalk: bool = typer.Option(
        False, "--crosswalk", help="Audit the promoted crosswalk instead of staging"
    ),
) -> None:
    """Run the invariant suite and print the result.

    Checks grain (one row per HMDA loan), pooled target exclusivity, the
    agency/enterprise cross-matcher agreement, and the reconciliation of
    ``mbs_without_fhfa`` against the ``direct_no_link`` count.

    Examples:
      mortgage-data match master qc --crosswalk
    """
    from mortgage_data_manager.matching.master.qc import run_qc

    result = run_qc(staging=not crosswalk)
    console.print(_coverage_table(result, f"Master crosswalk QC ({result['source']})"))

    table = Table(title="Invariants")
    table.add_column("Check", style="cyan")
    table.add_column("Value", style="green", justify="right")
    table.add_row("grain (rows == unique HMDAIndex)", "OK" if result["grain_ok"] else "FAIL")
    table.add_row(
        "mbs_without_fhfa == direct_no_link",
        "reconciles" if result["mbs_without_fhfa_reconciles"] else "MISMATCH",
    )
    for key, value in result["invariants"].items():
        table.add_row(key, f"{value:,}")
    console.print(table)

    wh = Table(title="Links withheld by the strict conflict policy")
    wh.add_column("Reason", style="cyan")
    wh.add_column("Loans", style="yellow", justify="right")
    wh.add_column("Withholds", style="white")
    detail = {
        "mbs_contested": "conventional link (chain vs direct name different targets)",
        "gse_gnma": "GSE links AND the GNMA link (one guarantor per loan)",
        "gnma_disagree": "GNMA link (FHLB bridge vs master disagree)",
        "fha_cross_era": "FHA link (the pre-2018 crosswalk also claims the endorsement)",
    }
    for reason, n in result["conflicts_withheld"].items():
        wh.add_row(reason, f"{n:,}", detail[reason])
    console.print(wh)
    console.print(
        f"[dim]{result['invariants']['all_links_withheld']:,} loans have every link withheld — "
        "kept as a record (all in_* false, conflict_flags set), never silently dropped.[/dim]"
    )

    if not result["passed"]:
        console.print(f"[red]FAIL: {', '.join(result['failures'])}[/red]")
        raise typer.Exit(code=1)
    console.print("[green]QC PASS[/green]")


@app.command()
def info() -> None:
    """Report what is on disk: inputs, partitions, coverage, last QC.

    Examples:
      mortgage-data match master info
    """
    from mortgage_data_manager.matching.master.build import load_master_crosswalk
    from mortgage_data_manager.matching.master.qc import latest_qc

    inputs = Table(title="Input crosswalks")
    inputs.add_column("Edge", style="cyan")
    inputs.add_column("Path", style="white", overflow="fold")
    inputs.add_column("Present", justify="right")
    entries: list[tuple[str, Path]] = [
        ("hmda_fha", Config.HMDA_FHA_CROSSWALK),
        ("hmda_fhfa", Config.HMDA_FHFA_CROSSWALK),
        *[(f"chain {g}", Config.get_chain_crosswalk_path(g)) for g in Config.GSES],  # type: ignore[arg-type]
        ("hmda_gse_direct", Config.get_direct_crosswalk_path(Config.CONVENTIONAL_MAX_YEAR)),
        ("hmda_gnma_fha", Config.HMDA_GNMA_FHA_CROSSWALK),
        ("hmda_gnma_va_usda", Config.HMDA_GNMA_VA_USDA_CROSSWALK),
        ("hmda_fhlb", Config.HMDA_FHLB_CROSSWALK),
        ("gnma_fhlb", Config.FHLB_MBS_CROSSWALK),
        ("hmda_sellers_purchasers", Config.HMDA_SELLERS_PURCHASERS_CROSSWALK),
    ]
    for name, path in entries:
        ok = path.exists()
        inputs.add_row(name, str(path), "[green]yes[/green]" if ok else "[red]NO[/red]")
    console.print(inputs)

    try:
        lf = load_master_crosswalk()
    except FileNotFoundError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(code=1) from exc

    import polars as pl

    per_year = (
        lf.group_by(Config.PARTITION_COL)
        .agg(pl.len().alias("spine"), *[pl.col(c).sum() for c in Config.flag_columns()])
        .sort(Config.PARTITION_COL)
        .collect()
    )
    table = Table(title=f"crosswalk/master — {int(per_year['spine'].sum()):,} loans")
    table.add_column("year", style="cyan", justify="right")
    table.add_column("spine", justify="right")
    for flag in Config.flag_columns():
        table.add_column(flag.removeprefix("in_"), justify="right")
    for row in per_year.iter_rows(named=True):
        table.add_row(
            str(row[Config.PARTITION_COL]),
            f"{row['spine']:,}",
            *[f"{row[f]:,}" for f in Config.flag_columns()],
        )
    console.print(table)
    console.print(
        f"[dim]Conventional edges stop at {Config.CONVENTIONAL_MAX_YEAR}; later years are "
        "right-censored (a conventional loan appears only if seller/purchaser matched, "
        "and then as channel='other').[/dim]"
    )

    last = latest_qc(staging=False) or latest_qc(staging=True)
    if last:
        verdict = "[green]PASS[/green]" if last["passed"] else f"[red]FAIL {last['failures']}[/red]"
        console.print(f"Last QC ({last['stamp']}, {last['source']}): {verdict}")
    else:
        console.print("[yellow]No QC result recorded — run `match master qc`.[/yellow]")


def cli_main() -> None:
    """Entry point for the master crosswalk CLI."""
    app()


if __name__ == "__main__":
    cli_main()
