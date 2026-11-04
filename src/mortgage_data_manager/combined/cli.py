"""Typer CLI for the combined master datasets.

Surfaced as ``mortgage-data combined <command>``. Builds the small curated set
of cross-source master datasets (loan/MBS issuance + performance).
"""

from __future__ import annotations

from typing import Annotated, Literal

import typer
from rich.table import Table

from mortgage_data_manager import __version__
from mortgage_data_manager.combined.builders import build_datasets
from mortgage_data_manager.combined.config import (
    DATASET_MAP,
    VALID_DATASETS,
    CombinedConfig,
)
from mortgage_data_manager.core.cli.download_options import QuietOption, VerboseOption
from mortgage_data_manager.core.cli.formatting import console
from mortgage_data_manager.core.cli.validation import validate_datasets, version_callback_factory
from mortgage_data_manager.core.logging import configure_logging

app = typer.Typer(
    name="combined",
    help="Combined cross-source master datasets",
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=False,
)

_version_callback = version_callback_factory("Combined Datasets", __version__)


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
    """Combined cross-source master datasets."""
    configure_logging(level="INFO")


DatasetsOption = Annotated[
    list[str] | None,
    typer.Option(
        "--datasets",
        "-t",
        help=f"Masters to build (repeatable), run in registry order. Default: all ({', '.join(VALID_DATASETS)}).",
    ),
]


def _log_level(verbose: bool, quiet: bool) -> Literal["DEBUG", "INFO", "WARNING"]:
    return "DEBUG" if verbose else ("WARNING" if quiet else "INFO")


@app.command("info")
def info_command() -> None:
    """Show the combined-dataset registry and output path."""
    console.print(f"[cyan]COMBINED_DATA_DIR:[/cyan] {CombinedConfig.COMBINED_DATA_DIR}")
    registry = Table(title="Combined master datasets", show_lines=False)
    registry.add_column("Dataset", style="cyan")
    registry.add_column("Description", style="green")
    registry.add_column("Grain", style="green")
    registry.add_column("Sources", style="green")
    for dataset, meta in DATASET_MAP.items():
        registry.add_row(
            dataset,
            str(meta["description"]),
            str(meta["grain"]),
            ", ".join(meta["sources"]) or "—",  # type: ignore[arg-type]
        )
    console.print(registry)


@app.command("build")
def build_command(
    datasets: DatasetsOption = None,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Rebuild existing outputs.")
    ] = False,
    verbose: VerboseOption = False,
    quiet: QuietOption = False,
) -> None:
    """Build the requested combined master datasets."""
    configure_logging(level=_log_level(verbose, quiet))
    if datasets:
        validate_datasets(datasets, VALID_DATASETS)
    built = build_datasets(datasets, overwrite=overwrite)
    if not built:
        console.print(
            "[yellow]Nothing built: every requested master exists (use --overwrite).[/yellow]"
        )
        raise typer.Exit(code=1)
    console.print(f"[green]Built:[/green] {', '.join(built)}")


def cli_main() -> None:
    """Entry point for standalone combined CLI."""
    app()


if __name__ == "__main__":
    cli_main()
