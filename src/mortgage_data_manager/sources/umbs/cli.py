"""UMBS CLI - Main entry point."""

from __future__ import annotations

from typing import Annotated, Literal

import typer
from rich.table import Table

from mortgage_data_manager import __version__
from mortgage_data_manager.core.cli.download_options import (
    OverwriteOption,
    QuietOption,
    VerboseOption,
)
from mortgage_data_manager.core.cli.formatting import (
    console,
    print_info_table,
    print_section_header,
)
from mortgage_data_manager.core.cli.validation import version_callback_factory
from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.sources.umbs.config import UMBSConfig
from mortgage_data_manager.sources.umbs.download import (
    get_manual_download_instructions,
    raw_coverage,
)
from mortgage_data_manager.sources.umbs.pipeline import run_bronze, run_pipeline, run_silver

app = typer.Typer(
    name="umbs",
    help="UMBS Data Manager - Download and process UMBS data",
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=False,
)

logger = get_logger(__name__)


_version_callback = version_callback_factory("UMBS Data Manager", __version__)


def _log_level(verbose: bool, quiet: bool) -> Literal["DEBUG", "INFO", "WARNING"]:
    return "DEBUG" if verbose else ("WARNING" if quiet else "INFO")


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit",
        ),
    ] = False,
):
    """[bold cyan]UMBS Data Manager[/bold cyan] - Process UMBS mortgage data.

    [yellow]Commands:[/yellow]

      [cyan]download[/cyan]   - Download data files for UMBS
      [cyan]bronze[/cyan]     - Import raw zip files to bronze parquet
      [cyan]silver[/cyan]     - Build silver layer (merge original/correction pairs)
      [cyan]pipeline[/cyan]   - Bronze + silver in one step
      [cyan]info[/cyan]       - Show configuration information

    Use [bold]umbs COMMAND --help[/bold] for more information on a command.
    """
    configure_logging(level="INFO")


@app.command()
def bronze(
    overwrite: OverwriteOption = False,
    gse: Annotated[
        str | None,
        typer.Option("--gse", help="Filter to a single GSE: FNMA or FHLMC"),
    ] = None,
    folder: Annotated[
        list[str] | None,
        typer.Option("--folder", help="Filter to specific folder name(s) (repeatable)"),
    ] = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """Import raw zip files to bronze parquet.

    Converts pipe-delimited zip files in the raw directory to parquet
    in the bronze directory, preserving GSE/folder structure.

    Examples:
      mortgage-data umbs bronze
      mortgage-data umbs bronze --overwrite
      mortgage-data umbs bronze --gse FNMA --folder FNM_MLLD
      mortgage-data umbs bronze --gse FHLMC --folder FRE_IS --folder FRE_ILLD
    """
    configure_logging(level=_log_level(verbose, quiet))
    try:
        counts = run_bronze(overwrite=overwrite, gse=gse, folders=folder)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    console.print(
        f"\n[bold green]Done.[/bold green] "
        f"Processed {counts['processed']}, "
        f"successful {counts['successful']}, "
        f"failed {counts['failed']}."
    )
    if counts["failed"] > 0:
        raise typer.Exit(code=1)


@app.command()
def silver(
    overwrite: OverwriteOption = False,
    gse: Annotated[
        str | None,
        typer.Option("--gse", help="Filter to a single GSE: FNMA or FHLMC"),
    ] = None,
    kind: Annotated[
        list[str] | None,
        typer.Option("--kind", help="Filter to canonical kind name(s), e.g. ILLD, IS (repeatable)"),
    ] = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """Build the silver layer for UMBS data.

    Merges each (original, correction) bronze pair into a single
    silver dataset per GSE/kind.

    Examples:
      mortgage-data umbs silver
      mortgage-data umbs silver --overwrite
      mortgage-data umbs silver --gse FNMA --kind ILLD
    """
    configure_logging(level=_log_level(verbose, quiet))
    try:
        results = run_silver(overwrite=overwrite, gse=gse, kinds=kind)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    for pair, counts in results.items():
        # Correction-pair results carry 'missing_pair'; perf-kind results
        # (MLLD/FU/MF/GN_MEGA) do not — default to 0 for those.
        console.print(
            f"  {pair}: wrote {counts['wrote']}, "
            f"skipped {counts['skipped_existing']} existing, "
            f"{counts.get('missing_pair', 0)} missing pair(s), "
            f"failed {counts['failed']}"
        )
    wrote = sum(c["wrote"] for c in results.values())
    skipped = sum(c["skipped_existing"] for c in results.values())
    failed = sum(c["failed"] for c in results.values())
    console.print(
        f"\n[bold green]Done.[/bold green] Wrote {wrote}, skipped {skipped} existing, "
        f"failed {failed}."
    )
    if failed > 0:
        raise typer.Exit(code=1)


@app.command()
def pipeline(
    overwrite: OverwriteOption = False,
    gse: Annotated[
        str | None,
        typer.Option("--gse", help="Filter to a single GSE: FNMA or FHLMC"),
    ] = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """Run bronze + silver for UMBS data in one step.

    Examples:
      mortgage-data umbs pipeline
      mortgage-data umbs pipeline --gse FHLMC --overwrite
    """
    configure_logging(level=_log_level(verbose, quiet))
    try:
        results = run_pipeline(overwrite=overwrite, gse=gse)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    bronze_counts, silver_results = results["bronze"], results["silver"]
    console.print(
        f"Bronze: processed {bronze_counts['processed']}, "
        f"successful {bronze_counts['successful']}, "
        f"failed {bronze_counts['failed']}."
    )
    wrote = sum(c["wrote"] for c in silver_results.values())
    skipped = sum(c["skipped_existing"] for c in silver_results.values())
    silver_failed = sum(c["failed"] for c in silver_results.values())
    console.print(f"Silver: wrote {wrote}, skipped {skipped} existing, failed {silver_failed}.")

    console.print("\n[bold green]Pipeline complete.[/bold green]")
    if bronze_counts["failed"] > 0 or silver_failed > 0:
        raise typer.Exit(code=1)


@app.command()
def info() -> None:
    """Display UMBS configuration information and raw coverage.

    Shows the configuration paths, then one row per raw feed A reads: files on disk,
    first and last month, months missing since the feed's first published month,
    and months holding more than one file (an original plus a republication).

    Examples:
      mortgage-data umbs info
      umbs info
    """
    print_info_table(
        "UMBS Configuration",
        {
            "Data Directory": UMBSConfig.UMBS_DATA_DIR,
            "Raw Directory": UMBSConfig.UMBS_RAW_DIR,
            "Bronze Directory": UMBSConfig.UMBS_BRONZE_DIR,
            "Silver Directory": UMBSConfig.UMBS_SILVER_DIR,
            "Gold Directory": UMBSConfig.UMBS_GOLD_DIR,
        },
    )

    _print_raw_coverage()

    console.print("\n[bold cyan]Available Commands:[/bold cyan]")
    console.print("  download    - Download data files for UMBS")
    console.print("  bronze      - Import raw zip files to bronze parquet")
    console.print("  silver      - Build silver layer (merge original/correction pairs)")
    console.print("  pipeline    - Bronze + silver in one step")
    console.print("  info        - Show this configuration information")


def _print_raw_coverage() -> None:
    """Print the raw coverage table (`raw_coverage`) for the configured raw root."""
    table = Table(title="Raw coverage", title_style="bold cyan")
    for col in ("GSE", "Prefix", "Folder", "Files", "First", "Last", "Missing", "Multi-file"):
        table.add_column(col)
    for row in raw_coverage(UMBSConfig.UMBS_RAW_DIR).iter_rows(named=True):
        missing = row["missing_months"]
        table.add_row(
            row["gse"],
            row["prefix"],
            row["folder"],
            str(row["files"]),
            row["first_month"] or "-",
            row["last_month"] or "-",
            f"[red]{', '.join(missing)}[/red]" if missing else "none",
            ", ".join(row["multi_file_months"]) or "none",
        )
    console.print()
    console.print(table)


@app.command()
def download() -> None:
    """Instructions for downloading UMBS (Uniform Mortgage-Backed Securities) data.

    [yellow]Manual Download Required[/yellow]

    Both GSEs publish the disclosure files behind a registered login or on SFTP.
    Prints the feeds A reads (prefix, raw folder, file name, first month), the
    look-alike files to avoid, and the rule to keep mid-month republications.

    Examples:
      mortgage-data umbs download
    """
    print_section_header("UMBS Data Download Instructions")
    console.print(get_manual_download_instructions(), markup=False, highlight=False)


def cli_main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    cli_main()
