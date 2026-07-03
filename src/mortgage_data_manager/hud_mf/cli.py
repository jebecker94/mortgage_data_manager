"""Typer CLI for the HUD multifamily subpackage.

Surfaced as ``mortgage-data hud-mf <command>`` after registration in
``cli/main.py``.
"""

from __future__ import annotations

from typing import Annotated, Literal

import typer
from rich.table import Table

from mortgage_data_manager import __version__
from mortgage_data_manager.core.cli.download_options import (
    PauseOption,
    QuietOption,
    TimeoutOption,
    VerboseOption,
)
from mortgage_data_manager.core.cli.formatting import console
from mortgage_data_manager.core.cli.validation import version_callback_factory
from mortgage_data_manager.core.logging import configure_logging
from mortgage_data_manager.hud_mf.config import (
    DATASET_MAP,
    VALID_DATASETS,
    HudMfConfig,
    validate_datasets,
)
from mortgage_data_manager.hud_mf.download import load_manifest
from mortgage_data_manager.hud_mf.pipeline import (
    run_bronze,
    run_download,
    run_pipeline,
    run_silver,
)

app = typer.Typer(
    name="hud-mf",
    help="HUD multifamily housing data operations",
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=False,
)

_version_callback = version_callback_factory("HUD Multifamily Data Manager", __version__)


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
) -> None:
    """HUD multifamily housing data operations."""
    configure_logging(level="INFO")


DatasetsOption = Annotated[
    list[str] | None,
    typer.Option(
        "--datasets",
        "-t",
        help=f"Tables to process (repeatable). Default: all ({len(VALID_DATASETS)} tables).",
    ),
]
OverwriteOpt = Annotated[
    bool,
    typer.Option("--overwrite", help="Overwrite existing files."),
]


def _log_level(verbose: bool, quiet: bool) -> Literal["DEBUG", "INFO", "WARNING"]:
    return "DEBUG" if verbose else ("WARNING" if quiet else "INFO")


@app.command("info")
def info_command() -> None:
    """Show HUD-MF configuration: paths and the logical-table registry."""
    paths = Table(title="HUD-MF paths", show_lines=False)
    paths.add_column("Key", style="cyan")
    paths.add_column("Path", style="green")
    paths.add_row("HUD_MF_DATA_DIR", str(HudMfConfig.HUD_MF_DATA_DIR))
    paths.add_row("HUD_MF_RAW_DIR", str(HudMfConfig.HUD_MF_RAW_DIR))
    paths.add_row("HUD_MF_BRONZE_DIR", str(HudMfConfig.HUD_MF_BRONZE_DIR))
    paths.add_row("HUD_MF_SILVER_DIR", str(HudMfConfig.HUD_MF_SILVER_DIR))
    console.print(paths)

    manifest = load_manifest()
    registry = Table(title="Logical tables", show_lines=False)
    registry.add_column("Table", style="cyan")
    registry.add_column("Family", style="green")
    registry.add_column("Source file", style="green")
    registry.add_column("Snapshot", style="green")
    registry.add_column("What it is", style="white")
    for dataset, meta in DATASET_MAP.items():
        snapshot = str(manifest.get(meta["source"], {}).get("last_modified", "—"))
        registry.add_row(
            dataset,
            meta["family"],
            meta["source"],
            snapshot,
            meta["description"],
        )
    console.print(registry)


@app.command("download")
def download_command(
    datasets: DatasetsOption = None,
    overwrite: OverwriteOpt = False,
    skip_dictionaries: Annotated[
        bool,
        typer.Option("--skip-dictionaries", help="Skip the Data Element Dictionary PDFs."),
    ] = False,
    pause: PauseOption = None,
    timeout: TimeoutOption = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """Download HUD multifamily workbooks + Data Element Dictionaries."""
    configure_logging(level=_log_level(verbose, quiet))
    if datasets is not None:
        validate_datasets(datasets)
    counts = run_download(
        datasets,
        overwrite=overwrite,
        pause=pause,
        timeout=timeout,
        skip_dictionaries=skip_dictionaries,
    )
    console.print(
        f"[bold]Download:[/bold] {counts['downloaded']} fetched, "
        f"{counts['skipped']} skipped, {counts['dictionaries']} dictionaries, "
        f"{counts['failed']} failed"
    )
    if counts["failed"]:
        raise typer.Exit(code=1)


@app.command("bronze")
def bronze_command(
    datasets: DatasetsOption = None,
    overwrite: OverwriteOpt = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """Convert raw workbooks → typed bronze parquet (one per table)."""
    configure_logging(level=_log_level(verbose, quiet))
    if datasets is not None:
        validate_datasets(datasets)
    counts = run_bronze(datasets, overwrite=overwrite)
    console.print(f"[bold]Bronze:[/bold] wrote {counts['built']} table parquets")
    if counts["built"] == 0:
        raise typer.Exit(code=1)


@app.command("silver")
def silver_command(
    datasets: DatasetsOption = None,
    overwrite: OverwriteOpt = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """Build cleaned, key-normalized silver parquet from bronze."""
    configure_logging(level=_log_level(verbose, quiet))
    if datasets is not None:
        validate_datasets(datasets)
    counts = run_silver(datasets, overwrite=overwrite)
    console.print(f"[bold]Silver:[/bold] built {counts['built']} table parquets")
    if counts["built"] == 0:
        raise typer.Exit(code=1)


@app.command("pipeline")
def pipeline_command(
    datasets: DatasetsOption = None,
    overwrite: OverwriteOpt = False,
    skip_download: Annotated[
        bool,
        typer.Option("--skip-download", help="Skip download step (use existing raw files)."),
    ] = False,
    skip_dictionaries: Annotated[
        bool,
        typer.Option("--skip-dictionaries", help="Skip the Data Element Dictionary PDFs."),
    ] = False,
    pause: PauseOption = None,
    timeout: TimeoutOption = None,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """End-to-end: download → bronze → silver."""
    configure_logging(level=_log_level(verbose, quiet))
    if datasets is not None:
        validate_datasets(datasets)
    results = run_pipeline(
        datasets,
        overwrite=overwrite,
        skip_download=skip_download,
        skip_dictionaries=skip_dictionaries,
        pause=pause,
        timeout=timeout,
    )
    console.print(f"[green]HUD-MF pipeline complete:[/green] {results}")


def cli_main() -> None:
    """Entry point for standalone HUD-MF CLI."""
    app()


if __name__ == "__main__":
    cli_main()
