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

from pathlib import Path
from typing import Annotated

import typer

from mortgage_data_manager.core.config import MortgageDataConfig

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
        help=f"Seconds to pause between downloads. Default: {MortgageDataConfig.DOWNLOAD_PAUSE}",
        metavar="SECONDS",
    ),
]

# Request timeout option
TimeoutOption = Annotated[
    int | None,
    typer.Option(
        "--timeout",
        help=f"Request timeout in seconds. Default: {MortgageDataConfig.DOWNLOAD_TIMEOUT}",
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


def get_download_mode(overwrite: bool) -> str:
    """Get download mode string from overwrite flag.

    Args:
        overwrite: If True, return "overwrite" mode

    Returns:
        "overwrite" if overwrite is True, "skip" otherwise
    """
    return "overwrite" if overwrite else "skip"
