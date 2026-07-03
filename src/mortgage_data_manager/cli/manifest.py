"""Manifest CLI commands.

Provides commands for generating and viewing the data manifest catalog.
"""

from __future__ import annotations

import typer
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from mortgage_data_manager.core.cli.formatting import console
from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.core.manifest import (
    MEDALLION_LAYERS,
    STANDARD_SUBPACKAGES,
    ManifestConfig,
    build_manifest,
    format_record_count,
    generate_summary,
    get_layer_status_indicator,
    human_readable_size,
    load_manifest,
    load_manifest_metadata,
    save_manifest,
    update_manifest_incremental,
)

app = typer.Typer(
    name="manifest",
    help="Data manifest catalog operations",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

logger = get_logger(__name__)


@app.callback()
def callback():
    """[bold cyan]Data Manifest Catalog[/bold cyan].

    Manage and view the centralized data manifest that catalogs all files
    across subpackages and medallion layers.
    """
    configure_logging(level="INFO")


@app.command()
def generate(
    full: bool = typer.Option(
        False,
        "--full",
        "-f",
        help="Force full regeneration instead of incremental update",
    ),
    subpackages: str | None = typer.Option(
        None,
        "--subpackages",
        "-s",
        help="Comma-separated list of subpackages to scan (default: all)",
    ),
):
    """Generate or update the data manifest.

    By default, performs an incremental update that only rescans files
    modified since the last scan. Use --full to force a complete regeneration.

    Examples:
        mortgage-data manifest generate
        mortgage-data manifest generate --full
        mortgage-data manifest generate --subpackages hmda,fha,gnma
    """
    subpackage_list = None
    if subpackages:
        subpackage_list = [s.strip() for s in subpackages.split(",")]
        invalid = [s for s in subpackage_list if s not in STANDARD_SUBPACKAGES]
        if invalid:
            console.print(f"[red]Invalid subpackages: {', '.join(invalid)}[/red]")
            console.print(f"Valid subpackages: {', '.join(STANDARD_SUBPACKAGES)}")
            raise typer.Exit(code=1)

    console.print("[cyan]Generating data manifest...[/cyan]")

    if full or subpackage_list:
        # Full scan
        console.print("  Mode: Full scan")
        if subpackage_list:
            console.print(f"  Subpackages: {', '.join(subpackage_list)}")
        df = build_manifest(subpackages=subpackage_list)
    else:
        # Incremental update
        console.print("  Mode: Incremental update")
        df = update_manifest_incremental()

    # Save manifest
    manifest_path = save_manifest(df)

    # Show summary
    summary = generate_summary(df)
    console.print("\n[green]Manifest generated successfully![/green]")
    console.print(f"  Total files: {summary['total_files']:,}")
    console.print(f"  Total size: {human_readable_size(summary['total_size_bytes'])}")
    if summary['total_records']:
        console.print(f"  Total records: {summary['total_records']:,}")
    console.print(f"  Saved to: {manifest_path}")


@app.command()
def summary():
    """Display a summary table of the data manifest.

    Shows file counts and sizes per subpackage and medallion layer.

    Examples:
        mortgage-data manifest summary
    """
    df = load_manifest()
    metadata = load_manifest_metadata()

    if df is None:
        console.print("[yellow]No manifest found. Run 'mortgage-data manifest generate' first.[/yellow]")
        raise typer.Exit(code=1)

    stats = generate_summary(df)

    # Header panel
    last_updated = metadata.get("scan_timestamp", "Unknown") if metadata else "Unknown"
    total_records_str = format_record_count(stats['total_records'])

    header_text = Text()
    header_text.append(f"Last Updated: {last_updated}\n", style="white")
    header_text.append(
        f"Total Files: {stats['total_files']:,} | "
        f"Total Size: {human_readable_size(stats['total_size_bytes'])} | "
        f"Total Records: {total_records_str}",
        style="cyan",
    )

    console.print(Panel(header_text, title="Data Manifest Summary", border_style="cyan"))

    # Main summary table
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Subpackage", style="cyan", width=12)
    table.add_column("Raw", justify="center", width=10)
    table.add_column("Bronze", justify="center", width=10)
    table.add_column("Silver", justify="center", width=10)
    table.add_column("Gold", justify="center", width=10)
    table.add_column("Size", justify="right", width=12)
    table.add_column("Records", justify="right", width=18)

    for subpackage in sorted(stats['subpackage_summaries'].keys()):
        sub_stats = stats['subpackage_summaries'][subpackage]
        layers = sub_stats['layers']

        raw_indicator, _ = get_layer_status_indicator(layers['raw']['file_count'])
        bronze_indicator, _ = get_layer_status_indicator(layers['bronze']['file_count'])
        silver_indicator, _ = get_layer_status_indicator(layers['silver']['file_count'])
        gold_indicator, _ = get_layer_status_indicator(layers['gold']['file_count'])

        table.add_row(
            subpackage,
            raw_indicator,
            bronze_indicator,
            silver_indicator,
            gold_indicator,
            human_readable_size(sub_stats['total_size_bytes']),
            format_record_count(sub_stats['total_records']),
        )

    console.print(table)


@app.command()
def status(
    subpackage: str | None = typer.Argument(
        None,
        help="Subpackage to show detailed status for (optional)",
    ),
):
    """Display detailed status for a subpackage or all subpackages.

    Shows per-layer file counts, sizes, and record counts.

    Examples:
        mortgage-data manifest status
        mortgage-data manifest status hmda
    """
    df = load_manifest()

    if df is None:
        console.print("[yellow]No manifest found. Run 'mortgage-data manifest generate' first.[/yellow]")
        raise typer.Exit(code=1)

    if subpackage:
        if subpackage not in STANDARD_SUBPACKAGES:
            console.print(f"[red]Invalid subpackage: {subpackage}[/red]")
            console.print(f"Valid subpackages: {', '.join(STANDARD_SUBPACKAGES)}")
            raise typer.Exit(code=1)

        _show_subpackage_status(df, subpackage)
    else:
        # Show all subpackages
        stats = generate_summary(df)
        for sub in sorted(stats['subpackage_summaries'].keys()):
            _show_subpackage_status(df, sub)
            console.print()


def _show_subpackage_status(df, subpackage: str):
    """Show detailed status for a single subpackage."""
    import polars as pl

    sub_df = df.filter(pl.col("subpackage") == subpackage)

    if len(sub_df) == 0:
        console.print(f"[dim]No data found for {subpackage}[/dim]")
        return

    console.print(f"[bold cyan]{subpackage.upper()}[/bold cyan]")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Layer", style="cyan", width=10)
    table.add_column("Files", justify="right", width=8)
    table.add_column("Size", justify="right", width=12)
    table.add_column("Records", justify="right", width=15)
    table.add_column("Extensions", width=30)

    for layer in MEDALLION_LAYERS:
        layer_df = sub_df.filter(pl.col("medallion_layer") == layer)

        if len(layer_df) == 0:
            table.add_row(
                layer,
                "[dim]0[/dim]",
                "[dim]-[/dim]",
                "[dim]-[/dim]",
                "[dim]-[/dim]",
            )
        else:
            file_count = len(layer_df)
            total_size = layer_df["file_size_bytes"].sum()
            total_records = layer_df["record_count"].sum()

            # Get unique extensions
            extensions = layer_df["file_extension"].unique().to_list()
            ext_str = ", ".join(sorted(set(e for e in extensions if e)))

            table.add_row(
                layer,
                str(file_count),
                human_readable_size(total_size),
                format_record_count(total_records),
                ext_str or "-",
            )

    console.print(table)


@app.command()
def files(
    subpackage: str | None = typer.Option(
        None,
        "--subpackage",
        "-s",
        help="Filter by subpackage",
    ),
    layer: str | None = typer.Option(
        None,
        "--layer",
        "-l",
        help="Filter by medallion layer (raw, bronze, silver, gold)",
    ),
    extension: str | None = typer.Option(
        None,
        "--extension",
        "-e",
        help="Filter by file extension (e.g., .parquet)",
    ),
    limit: int = typer.Option(
        50,
        "--limit",
        "-n",
        help="Maximum number of files to display",
    ),
):
    """List files in the manifest with optional filters.

    Examples:
        mortgage-data manifest files --subpackage hmda --layer silver
        mortgage-data manifest files --extension .parquet --limit 100
    """
    import polars as pl

    df = load_manifest()

    if df is None:
        console.print("[yellow]No manifest found. Run 'mortgage-data manifest generate' first.[/yellow]")
        raise typer.Exit(code=1)

    # Apply filters
    if subpackage:
        df = df.filter(pl.col("subpackage") == subpackage)
    if layer:
        df = df.filter(pl.col("medallion_layer") == layer)
    if extension:
        ext = extension if extension.startswith(".") else f".{extension}"
        df = df.filter(pl.col("file_extension") == ext)

    if len(df) == 0:
        console.print("[yellow]No files match the specified filters.[/yellow]")
        return

    # Display results
    console.print(f"[cyan]Found {len(df):,} files matching filters[/cyan]")

    if len(df) > limit:
        console.print(f"[dim]Showing first {limit} files (use --limit to show more)[/dim]")
        df = df.head(limit)

    table = Table(show_header=True, header_style="bold")
    table.add_column("Subpackage", style="cyan", width=10)
    table.add_column("Layer", width=8)
    table.add_column("File", width=40)
    table.add_column("Size", justify="right", width=10)
    table.add_column("Records", justify="right", width=12)

    for row in df.iter_rows(named=True):
        table.add_row(
            row["subpackage"],
            row["medallion_layer"],
            row["file_name"],
            human_readable_size(row["file_size_bytes"]),
            format_record_count(row["record_count"]),
        )

    console.print(table)


@app.command()
def info():
    """Display manifest configuration and status.

    Examples:
        mortgage-data manifest info
    """
    table = Table(title="Manifest Configuration", show_header=True, header_style="bold cyan")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("[bold]Data Directory[/bold]", str(ManifestConfig.DATA_DIR))
    table.add_row("[bold]Manifest Directory[/bold]", str(ManifestConfig.MANIFEST_DIR))
    table.add_row("[bold]Manifest File[/bold]", str(ManifestConfig.MANIFEST_FILE))

    console.print(table)

    # Check manifest status
    console.print("\n[bold]Manifest Status:[/bold]")

    if ManifestConfig.MANIFEST_FILE.exists():
        df = load_manifest()
        metadata = load_manifest_metadata()

        if df is not None:
            console.print(f"  [green]\u2713[/green] Manifest exists: {len(df):,} files cataloged")
        if metadata:
            console.print(f"  [green]\u2713[/green] Last updated: {metadata.get('scan_timestamp', 'Unknown')}")
    else:
        console.print("  [yellow]![/yellow] No manifest found")
        console.print("      Run 'mortgage-data manifest generate' to create one")

    # List tracked subpackages
    console.print("\n[bold]Tracked Subpackages:[/bold]")
    for subpackage in STANDARD_SUBPACKAGES:
        subpackage_dir = ManifestConfig.DATA_DIR / subpackage
        if subpackage_dir.exists():
            console.print(f"  [green]\u2713[/green] {subpackage}")
        else:
            console.print(f"  [dim]\u25cb[/dim] {subpackage} [dim](no data)[/dim]")


def cli_main():
    """Entry point for standalone manifest CLI."""
    app()


if __name__ == "__main__":
    cli_main()
