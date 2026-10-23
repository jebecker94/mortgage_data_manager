"""Top-level CLI for the raw download ledger.

``mortgage-data raw`` inspects and maintains the per-source acquisition ledger
kept by ``core.download_ledger``: register hand-placed files, audit raw dirs
against the ledger, list upstream revisions, and summarize the last run per
source.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal

import polars as pl
import typer
from rich.table import Table

from mortgage_data_manager.core.cli.formatting import console
from mortgage_data_manager.core.download_ledger import (
    changes,
    latest,
    ledger_dir,
    read_ledger,
    register_files,
    unrecorded_files,
)

app = typer.Typer(
    name="raw",
    help="Raw download ledger: register, audit, changes, info",
    no_args_is_help=True,
)


def _raw_dir_registry() -> dict[str, Callable[[], Path]]:
    """Source name → raw directory resolver, imported lazily to keep startup fast.

    Only true raw layers are listed. ``core.reference`` downloads are ledgered
    under ``source="reference"`` but its cache directory also holds derived
    parquet, so it is not audited as a raw dir.

    Returns:
        Raw-directory resolvers keyed by source name.
    """

    def fha() -> Path:
        from mortgage_data_manager.sources.fha.config import FHAConfig

        return FHAConfig.FHA_RAW_DIR

    def fhfa() -> Path:
        from mortgage_data_manager.sources.fhfa.config import FHFAConfig

        return FHFAConfig.FHFA_RAW_DIR

    def fhlb() -> Path:
        from mortgage_data_manager.sources.fhlb.config import FHLBConfig

        return FHLBConfig.FHLB_RAW_DIR

    def fhlmc() -> Path:
        from mortgage_data_manager.sources.fhlmc.config import FHLMCConfig

        return FHLMCConfig.FHLMC_RAW_DIR

    def fnma() -> Path:
        from mortgage_data_manager.sources.fnma.config import FNMAConfig

        return FNMAConfig.FNMA_RAW_DIR

    def gnma() -> Path:
        from mortgage_data_manager.sources.gnma.config import GNMAConfig

        return GNMAConfig.GNMA_RAW_DIR

    def hmda() -> Path:
        from mortgage_data_manager.sources.hmda.config import HMDAConfig

        return HMDAConfig.HMDA_RAW_DIR

    def hud() -> Path:
        from mortgage_data_manager.sources.hud.config import HUDConfig

        return HUDConfig.HUD_RAW_DIR

    def umbs() -> Path:
        from mortgage_data_manager.sources.umbs.config import UMBSConfig

        return UMBSConfig.UMBS_RAW_DIR

    def usps_crosswalk() -> Path:
        from mortgage_data_manager.sources.usps_crosswalk.config import USPSCrosswalkConfig

        return USPSCrosswalkConfig.USPS_CROSSWALK_RAW_DIR

    return {
        "fha": fha,
        "fhfa": fhfa,
        "fhlb": fhlb,
        "fhlmc": fhlmc,
        "fnma": fnma,
        "gnma": gnma,
        "hmda": hmda,
        "hud": hud,
        "umbs": umbs,
        "usps_crosswalk": usps_crosswalk,
    }


def raw_dirs(sources: list[str] | None = None) -> dict[str, Path]:
    """Resolve raw directories for ``sources`` (``None`` = every known source).

    Args:
        sources: Source names to resolve, or None for every known source.

    Returns:
        Raw directories keyed by source name.

    Raises:
        typer.BadParameter: If a source name is unknown.
    """
    registry = _raw_dir_registry()
    names = sources or list(registry)
    unknown = [s for s in names if s not in registry]
    if unknown:
        raise typer.BadParameter(f"Unknown source(s) {unknown}; known: {sorted(registry)}")
    return {name: registry[name]() for name in names}


SourceOption = Annotated[
    list[str] | None,
    typer.Option("--source", "-s", help="Source(s) to act on. Default: all known sources."),
]


@app.command("register")
def register_command(
    source: SourceOption = None,
    method: Annotated[
        str,
        typer.Option(
            "--method",
            help="'manual' for hand-placed files; 'backfill' for the one-time baseline of a raw "
            "layer that predates the ledger.",
        ),
    ] = "manual",
    note: Annotated[
        str | None, typer.Option("--note", help="Provenance note stored on every record.")
    ] = None,
) -> None:
    """Record every raw file that has no ledger record yet (hashes each file).

    Resumable: files whose latest record already matches their size are skipped.
    """
    if method not in ("manual", "backfill"):
        raise typer.BadParameter("--method must be 'manual' or 'backfill'")
    method_lit: Literal["manual", "backfill"] = "manual" if method == "manual" else "backfill"
    total = 0
    for name, raw_dir in raw_dirs(source).items():
        if not raw_dir.exists():
            console.print(f"[yellow]{name}: raw dir missing ({raw_dir}); skipped[/yellow]")
            continue
        n = register_files(name, raw_dir, method=method_lit, note=note)
        console.print(f"[green]{name}: registered {n} file(s)[/green]")
        total += n
    if total == 0:
        console.print("[yellow]Nothing to register — every raw file already has a record.[/yellow]")
        raise typer.Exit(code=1)


@app.command("audit")
def audit_command(source: SourceOption = None) -> None:
    """List raw files with no successful/registered ledger record (or a size mismatch)."""
    table = Table(title="Raw files without a ledger record", show_lines=False)
    table.add_column("Source", style="cyan")
    table.add_column("Unrecorded", justify="right", style="white")
    table.add_column("Examples", style="white")
    total = 0
    for name, raw_dir in raw_dirs(source).items():
        if not raw_dir.exists():
            continue
        missing = unrecorded_files(name, raw_dir)
        total += len(missing)
        table.add_row(
            name,
            f"{len(missing):,}",
            ", ".join(missing[:3]) + (" …" if len(missing) > 3 else ""),
        )
    console.print(table)
    if total:
        console.print(f"[red]{total:,} unrecorded file(s). Run `mortgage-data raw register`.[/red]")
        raise typer.Exit(code=1)
    console.print("[green]Every raw file has a ledger record.[/green]")


@app.command("changes")
def changes_command(source: SourceOption = None) -> None:
    """Destinations acquired more than once with different bytes (upstream revisions)."""
    frames = [changes(name) for name in (source or [None])]  # type: ignore[list-item]
    df = pl.concat(frames, how="diagonal_relaxed") if len(frames) > 1 else frames[0]
    if df.is_empty():
        console.print("[green]No destination has changed bytes between acquisitions.[/green]")
        return
    table = Table(title="Upstream revisions", show_lines=False)
    for col in ("source", "destination", "recorded_at", "sha256", "bytes", "method"):
        table.add_column(col, style="white")
    for row in df.iter_rows(named=True):
        table.add_row(
            row["source"],
            row["destination"],
            row["recorded_at"][:19],
            row["sha256"][:12],
            f"{row['bytes']:,}" if row["bytes"] is not None else "—",
            row["method"],
        )
    console.print(table)


@app.command("info")
def info_command(source: SourceOption = None) -> None:
    """Ledger location and, per source, files on record and the last run."""
    console.print(f"Ledger dir: [cyan]{ledger_dir()}[/cyan]")
    names = source or sorted(_raw_dir_registry())
    lf = read_ledger(None)
    df = lf.filter(pl.col("source").is_in(names)).collect() if names else lf.collect()
    table = Table(title="Raw download ledger", show_lines=False)
    for col in ("Source", "Records", "Files on record", "GB on record", "Last run", "Last argv"):
        table.add_column(col, style="white")
    for name in names:
        sub = df.filter(pl.col("source") == name)
        if sub.is_empty():
            table.add_row(name, "0", "—", "—", "—", "—")
            continue
        present = latest(name)
        last = sub.sort("recorded_at").row(-1, named=True)
        table.add_row(
            name,
            f"{sub.height:,}",
            f"{present.height:,}",
            f"{(present['bytes'].fill_null(0).sum()) / 2**30:,.1f}",
            last["recorded_at"][:19],
            last["argv"][:60],
        )
    console.print(table)


__all__ = ["app", "raw_dirs"]
