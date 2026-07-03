"""UMBS CLI - Main entry point."""

from __future__ import annotations

from typing import Annotated

import typer

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
from mortgage_data_manager.umbs.config import UMBSConfig

app = typer.Typer(
    name="umbs",
    help="UMBS Data Manager - Download and process UMBS data",
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=False,
)

download_app = typer.Typer(
    name="download",
    help="Download UMBS data files (requires manual registration)",
    no_args_is_help=True,
)
app.add_typer(download_app, name="download")

logger = get_logger(__name__)


_version_callback = version_callback_factory("UMBS Data Manager", __version__)


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
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    from mortgage_data_manager.umbs.import_bronze import (
        run_bronze_import,
        run_multi_rt_bronze_import,
    )

    try:
        # Pipe-delimited-with-headers folders (ILLD, IS, MF, GN_MEGA, ...).
        headered = run_bronze_import(
            raw_dir=UMBSConfig.UMBS_RAW_DIR,
            bronze_dir=UMBSConfig.UMBS_BRONZE_DIR,
            overwrite=overwrite,
            gse=gse,
            folders=folder,
        )
        # Headerless multi-record-type folders (MS, ISS, RISS, ESF_MSS, ...);
        # complementary folder sets, so the same filters route correctly.
        multi_rt = run_multi_rt_bronze_import(
            raw_dir=UMBSConfig.UMBS_RAW_DIR,
            bronze_dir=UMBSConfig.UMBS_BRONZE_DIR,
            overwrite=overwrite,
            gse=gse,
            folders=folder,
        )
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    processed = headered["processed"] + multi_rt["processed"]
    successful = headered["successful"] + multi_rt["successful"]
    failed = headered["failed"] + multi_rt["failed"]
    console.print(
        f"\n[bold green]Done.[/bold green] "
        f"Processed {processed}, "
        f"successful {successful}, "
        f"failed {failed}."
    )
    if failed > 0:
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
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    from mortgage_data_manager.umbs.import_silver import run_silver_import

    try:
        results = run_silver_import(
            bronze_dir=UMBSConfig.UMBS_BRONZE_DIR,
            silver_dir=UMBSConfig.UMBS_SILVER_DIR,
            overwrite=overwrite,
            gse=gse,
            kinds=kind,
        )
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    for pair, counts in results.items():
        # Correction-pair results carry 'missing_pair'; perf-kind results
        # (MLLD/FU/MF/GN_MEGA) do not — default to 0 for those.
        console.print(
            f"  {pair}: wrote {counts['wrote']}, "
            f"skipped {counts['skipped_existing']} existing, "
            f"{counts.get('missing_pair', 0)} missing pair(s)"
        )
    wrote = sum(c["wrote"] for c in results.values())
    skipped = sum(c["skipped_existing"] for c in results.values())
    console.print(f"\n[bold green]Done.[/bold green] Wrote {wrote}, skipped {skipped} existing.")


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
    log_level = "DEBUG" if verbose else ("WARNING" if quiet else "INFO")
    configure_logging(level=log_level)

    from mortgage_data_manager.umbs.import_bronze import (
        run_bronze_import,
        run_multi_rt_bronze_import,
    )
    from mortgage_data_manager.umbs.import_silver import run_silver_import

    console.print("[cyan]Step 1: Importing raw zip files to bronze[/cyan]")
    try:
        headered = run_bronze_import(
            raw_dir=UMBSConfig.UMBS_RAW_DIR,
            bronze_dir=UMBSConfig.UMBS_BRONZE_DIR,
            overwrite=overwrite,
            gse=gse,
        )
        multi_rt = run_multi_rt_bronze_import(
            raw_dir=UMBSConfig.UMBS_RAW_DIR,
            bronze_dir=UMBSConfig.UMBS_BRONZE_DIR,
            overwrite=overwrite,
            gse=gse,
        )
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    console.print(
        f"Bronze: processed {headered['processed'] + multi_rt['processed']}, "
        f"successful {headered['successful'] + multi_rt['successful']}, "
        f"failed {headered['failed'] + multi_rt['failed']}."
    )

    console.print("\n[cyan]Step 2: Building silver layer[/cyan]")
    try:
        silver_results = run_silver_import(
            bronze_dir=UMBSConfig.UMBS_BRONZE_DIR,
            silver_dir=UMBSConfig.UMBS_SILVER_DIR,
            overwrite=overwrite,
            gse=gse,
        )
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    wrote = sum(c["wrote"] for c in silver_results.values())
    skipped = sum(c["skipped_existing"] for c in silver_results.values())
    console.print(f"Silver: wrote {wrote}, skipped {skipped} existing.")

    console.print("\n[bold green]Pipeline complete.[/bold green]")
    if headered["failed"] + multi_rt["failed"] > 0:
        raise typer.Exit(code=1)


@app.command()
def info() -> None:
    """Display UMBS configuration information.

    Shows the current configuration paths and settings for UMBS data.

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

    console.print("\n[bold cyan]Available Commands:[/bold cyan]")
    console.print("  download    - Download data files for UMBS")
    console.print("  bronze      - Import raw zip files to bronze parquet")
    console.print("  silver      - Build silver layer (merge original/correction pairs)")
    console.print("  pipeline    - Bronze + silver in one step")
    console.print("  info        - Show this configuration information")


@download_app.command("data")
def download_data() -> None:
    """Instructions for downloading UMBS (Uniform Mortgage-Backed Securities) data.

    [yellow]Manual Download Required[/yellow]

    UMBS data is available through both Fannie Mae and Freddie Mac platforms.
    Registration is required on one or both platforms.

    [bold cyan]Access Options:[/bold cyan]

    Option 1: Fannie Mae Capital Markets
       https://capitalmarkets.fanniemae.com/mortgage-backed-securities/single-family-mbs

    Option 2: Freddie Mac Capital Markets
       https://capitalmarkets.freddiemac.com/mbs/products/umbs

    [bold cyan]After Download:[/bold cyan]

    Run the import command to process files:
      $ mortgage-data umbs bronze

    [bold cyan]Documentation:[/bold cyan]

    See docs/manual_downloads.md for detailed instructions.
    """
    print_section_header("UMBS Data Download Instructions")

    console.print()
    console.print("[yellow bold]Manual Download Required[/yellow bold]")
    console.print()
    console.print(
        "UMBS (Uniform Mortgage-Backed Securities) data is available through both "
        "Fannie Mae and Freddie Mac platforms. These are passthrough securities "
        "issued by both enterprises since June 2019 as part of the Single Security Initiative."
    )
    console.print()

    console.print("[cyan bold]Access Option 1: Fannie Mae[/cyan bold]")
    console.print()
    console.print("  [bold]Portal:[/bold]")
    console.print(
        "  [link=https://capitalmarkets.fanniemae.com/mortgage-backed-securities/single-family-mbs]"
        "https://capitalmarkets.fanniemae.com/.../single-family-mbs[/link]"
    )
    console.print()
    console.print("  [bold]Registration:[/bold] Data Dynamics account (free)")
    console.print(
        "  [bold]Data Available:[/bold] MBS issuance, monthly disclosures, supplemental info"
    )
    console.print()

    console.print("[cyan bold]Access Option 2: Freddie Mac[/cyan bold]")
    console.print()
    console.print("  [bold]Portal:[/bold]")
    console.print(
        "  [link=https://capitalmarkets.freddiemac.com/mbs/products/umbs]"
        "https://capitalmarkets.freddiemac.com/mbs/products/umbs[/link]"
    )
    console.print()
    console.print("  [bold]Registration:[/bold] Clarity Data Intelligence account (free)")
    console.print("  [bold]Data Available:[/bold] CRT issuance, monthly disclosures, MBS info")
    console.print()

    console.print("[cyan bold]File Placement:[/cyan bold]")
    console.print()
    console.print("  Download files to: [bold]data/umbs/raw/[/bold]")
    console.print()

    console.print("[cyan bold]About UMBS:[/cyan bold]")
    console.print()
    console.print("  - Issued since June 2019 (Single Security Initiative)")
    console.print("  - Backed by 30-, 20-, 15-, and 10-year fixed-rate mortgages")
    console.print("  - Same characteristics regardless of issuer (Fannie or Freddie)")
    console.print("  - TBA-eligible securities")
    console.print()

    console.print("[cyan bold]After Download:[/cyan bold]")
    console.print()
    console.print("  Process raw files to bronze layer:")
    console.print("  [green]$ mortgage-data umbs bronze[/green]")
    console.print()

    console.print("[dim]See docs/manual_downloads.md for complete instructions.[/dim]")


def cli_main():
    """Entry point for CLI."""
    app()


if __name__ == "__main__":
    cli_main()
