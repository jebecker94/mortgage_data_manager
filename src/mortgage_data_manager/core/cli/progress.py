"""Progress bar utilities for CLI commands.

This module provides consistent progress indicators across all subpackage CLIs,
using Rich for terminal-based progress bars and spinners.

Example usage:
    from mortgage_data_manager.core.cli.progress import create_progress, create_spinner

    # For determinate progress (known number of items)
    with create_progress("Downloading files") as progress:
        task = progress.add_task("Download", total=10)
        for i in range(10):
            download_file(i)
            progress.update(task, advance=1)

    # For indeterminate progress (unknown duration)
    with create_spinner("Processing schemas"):
        process_all_schemas()
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)


@contextmanager
def create_progress(description: str = "Processing...") -> Iterator[Progress]:
    """Create a rich progress bar context.

    Use this for operations where you know the total number of items to process.

    Args:
        description: Default task description

    Yields:
        Progress instance for tracking tasks

    Example:
        with create_progress("Downloading files") as progress:
            task = progress.add_task("Download", total=10)
            for i in range(10):
                download_file(i)
                progress.update(task, advance=1)
    """
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        TimeElapsedColumn(),
    ) as progress:
        yield progress


@contextmanager
def create_spinner(description: str = "Working...") -> Iterator[Progress]:
    """Create a simple spinner without progress bar.

    Use this for operations where duration is unknown (indeterminate progress).

    Args:
        description: Spinner description

    Yields:
        Progress instance for indeterminate tasks

    Example:
        with create_spinner("Processing schemas"):
            process_all_schemas()
    """
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
    ) as progress:
        progress.add_task(description, total=None)
        yield progress
