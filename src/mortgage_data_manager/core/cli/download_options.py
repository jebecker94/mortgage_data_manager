"""Standardized CLI options for download commands.

This module provides reusable Typer option types for consistent download
command interfaces across all subpackages.

Example:
    >>> from mortgage_data_manager.core.cli.download_options import (
    ...     OutputDirOption,
    ...     OverwriteOption,
    ...     PauseOption,
    ... )
    >>>
    >>> @app.command()
    >>> def download(
    ...     output_dir: OutputDirOption = None,
    ...     overwrite: OverwriteOption = False,
    ...     pause: PauseOption = None,
    ... ):
    ...     pass
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer

from mortgage_data_manager.core.cli.formatting import console
from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.download import (
    SKIPPED_STATUSES,
    DownloadResult,
    DownloadStatus,
)

# Output directory option
OutputDirOption = Annotated[
    str | None,
    typer.Option(
        "--output-dir",
        "-o",
        help="Output directory for downloaded files. Defaults to subpackage raw directory.",
        metavar="PATH",
    ),
]

# Overwrite existing files option
OverwriteOption = Annotated[
    bool,
    typer.Option(
        "--overwrite",
        help="Overwrite existing files instead of skipping them.",
    ),
]

# Pause between requests option
PauseOption = Annotated[
    float | None,
    typer.Option(
        "--pause",
        help="Seconds to pause between downloads. Default: the source's configured value.",
        metavar="SECONDS",
    ),
]

# Request timeout option
TimeoutOption = Annotated[
    int | None,
    typer.Option(
        "--timeout",
        help="Request timeout in seconds. Default: the source's configured value.",
        metavar="SECONDS",
    ),
]

# Retry attempts option
RetriesOption = Annotated[
    int | None,
    typer.Option(
        "--retries",
        help=f"Number of retry attempts for failed downloads. Default: {MortgageDataConfig.DOWNLOAD_RETRIES}",
        metavar="COUNT",
    ),
]

# Verbose logging option
VerboseOption = Annotated[
    bool,
    typer.Option(
        "--verbose",
        "-v",
        help="Enable verbose (DEBUG level) logging output.",
    ),
]

# Quiet logging option (suppress progress bars)
QuietOption = Annotated[
    bool,
    typer.Option(
        "--quiet",
        "-q",
        help="Suppress progress bars and reduce output.",
    ),
]


def resolve_output_dir(
    output_dir: str | None,
    default_dir: Path,
) -> Path:
    """Resolve output directory from CLI option or default.

    Args:
        output_dir: User-provided output directory (may be None)
        default_dir: Default directory to use if output_dir is None

    Returns:
        Resolved Path object
    """
    if output_dir is not None:
        return Path(output_dir)
    return default_dir


def report_download_results(results: Sequence[DownloadResult]) -> None:
    """Print the one-line download summary; exit 1 if any file failed or none was attempted.

    Every ``skipped_*`` status counts as skipped. Each failed file is listed with its
    error. An empty ``results`` means discovery found nothing to fetch, which is an
    error for a download command.

    Args:
        results: The DownloadResult of every file the command attempted.

    Raises:
        typer.Exit: With code 1 when any download failed or ``results`` is empty.
    """
    if not results:
        console.print("[red]Nothing to download: no files were found[/red]")
        raise typer.Exit(code=1)
    success = sum(1 for r in results if r.status == DownloadStatus.SUCCESS)
    skipped = sum(1 for r in results if r.status in SKIPPED_STATUSES)
    failed = [r for r in results if r.status == DownloadStatus.FAILED]
    if failed:
        console.print(
            f"[yellow]Download completed with errors: "
            f"{success} downloaded, {skipped} skipped, {len(failed)} failed[/yellow]"
        )
        for result in failed:
            console.print(f"  [red]Failed: {result.path.name} - {result.error_message}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Download complete: {success} downloaded, {skipped} skipped[/green]")
