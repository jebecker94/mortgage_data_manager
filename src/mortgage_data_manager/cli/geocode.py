"""Top-level CLI for inspecting / managing the global geocoder cache.

Subpackage callers (``mortgage-data ncua geocode-branches``, etc.) own the
actual geocode workflow. This module just exposes cache operations.
"""

from __future__ import annotations

from datetime import date

import typer
from rich.table import Table

from mortgage_data_manager.core.cli.formatting import console
from mortgage_data_manager.core.geocoding import cache as cachemod

app = typer.Typer(
    name="geocode",
    help="Inspect and manage the global geocoder cache",
    no_args_is_help=True,
)

cache_app = typer.Typer(name="cache", help="Cache operations", no_args_is_help=True)
app.add_typer(cache_app, name="cache")


@cache_app.command("stats")
def cache_stats():
    """Show row counts and provider/match-reason breakdowns for the cache."""
    cache = cachemod.load_cache()
    table = Table(title="Geocoder cache", show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Path", str(cachemod.cache_path()))
    table.add_row("Rows", f"{len(cache):,}")
    if cache.is_empty():
        console.print(table)
        return
    n_match = int(cache.filter(cache["geocode_matched"]).height)
    table.add_row("Matched", f"{n_match:,} ({n_match / len(cache):.1%})")
    table.add_row("Earliest cached_at", str(cache["geocode_cached_at"].min()))
    table.add_row("Latest cached_at", str(cache["geocode_cached_at"].max()))
    console.print(table)

    import polars as pl

    prov = (
        cache.group_by("geocode_provider")
        .agg(pl.len().alias("n"), pl.col("geocode_matched").mean().alias("match_rate"))
        .sort("n", descending=True)
    )
    console.print("\n[bold]By provider:[/bold]")
    console.print(prov.head(10))


@cache_app.command("purge")
def cache_purge(
    provider: list[str] | None = typer.Option(None, "--provider", help="Provider(s) to purge"),
    older_than: str | None = typer.Option(None, "--older-than", help="ISO date — purge rows cached before this date"),
    match_reason: list[str] | None = typer.Option(
        None,
        "--match-reason",
        help="Purge rows with the given match_reason(s) — e.g., no_match, http_429",
    ),
):
    """Purge cache rows matching the given filters."""
    cutoff = date.fromisoformat(older_than) if older_than else None
    removed = cachemod.purge_cache(
        older_than=cutoff,
        providers=provider,
        match_reason=match_reason,
    )
    console.print(f"[green]Removed {removed:,} rows from {cachemod.cache_path()}[/green]")
