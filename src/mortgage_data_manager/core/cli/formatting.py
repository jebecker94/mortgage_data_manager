"""Rich output formatting utilities for CLI commands.

This module provides consistent output formatting across all subpackage CLIs,
using Rich for terminal styling and tables.

Example usage:
    from mortgage_data_manager.core.cli.formatting import (
        console, print_success, print_error, print_info_table
    )

    print_success("Download complete!")
    print_error("File not found")
    print_info_table("HMDA Configuration", {
        "Data Directory": "/path/to/data",
        "Raw Directory": "/path/to/raw",
    })
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Shared console instance for all CLI output
console = Console()


def print_success(message: str) -> None:
    """Print a success message with green checkmark.

    Args:
        message: Success message to display
    """
    console.print(f"[green]\u2713[/green] {message}")


def print_warning(message: str) -> None:
    """Print a warning message with yellow indicator.

    Args:
        message: Warning message to display
    """
    console.print(f"[yellow]\u26a0[/yellow] {message}")


def print_error(message: str) -> None:
    """Print an error message with red indicator.

    Args:
        message: Error message to display
    """
    console.print(f"[red]\u2717[/red] {message}")


def print_summary(title: str, data: dict[str, Any], style: str = "green") -> None:
    """Print a formatted summary panel.

    Args:
        title: Panel title
        data: Dictionary of key-value pairs to display
        style: Border style (default: "green")
    """
    lines = [f"[cyan]{k}:[/cyan] {v}" for k, v in data.items()]
    console.print(
        Panel(
            "\n".join(lines),
            title=title,
            border_style=style
        )
    )


def print_section_header(text: str) -> None:
    """Print a formatted section header.

    Args:
        text: Header text
    """
    console.print(f"\n[bold cyan]{text}[/bold cyan]")
    console.print("=" * len(text))


def print_path_info(label: str, path: Path) -> None:
    """Print formatted path information.

    Args:
        label: Label for the path
        path: Path to display
    """
    console.print(f"[cyan]{label}:[/cyan] {path}")


def print_info_table(
    title: str,
    data: dict[str, str | Path],
    header_style: str = "bold cyan",
) -> None:
    """Print a configuration info table.

    This is the standard format for `info` commands across all subpackages.

    Args:
        title: Table title (e.g., "HMDA Configuration")
        data: Dictionary of setting names to values
        header_style: Style for table headers

    Example:
        print_info_table("HMDA Configuration", {
            "Data Directory": "/path/to/data",
            "Raw Directory": "/path/to/raw",
            "Bronze Directory": "/path/to/bronze",
        })
    """
    table = Table(title=title, show_header=True, header_style=header_style)
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="white")

    for key, value in data.items():
        table.add_row(key, str(value))

    console.print(table)


def print_file_table(files: list[dict[str, Any]], title: str = "Files") -> None:
    """Print a formatted table of files.

    Args:
        files: List of dictionaries with file information
        title: Table title

    Example:
        files = [
            {'prefix': 'monthly', 'date': '202401', 'files': 5, 'status': 'success'},
            {'prefix': 'llmon1', 'date': '202401', 'files': 3, 'status': 'success'},
        ]
        print_file_table(files)
    """
    table = Table(title=title)
    table.add_column("Prefix", style="cyan")

    # Dynamically add columns based on first file entry
    if files:
        for key in files[0].keys():
            if key != "prefix":
                table.add_column(key.replace("_", " ").title(), style="magenta")

    for file_info in files:
        row = [str(v) for v in file_info.values()]
        table.add_row(*row)

    console.print(table)
