"""Input validation utilities for CLI commands.

This module provides consistent input validation across all subpackage CLIs,
including year range validation and version callback factory.

Example usage:
    from mortgage_data_manager.core.cli.validation import (
        validate_year_range,
        version_callback_factory,
    )

    # Validate year range
    years = validate_year_range(min_year=2020, max_year=2024)  # Returns range(2020, 2025)

    # Create version callback for CLI
    version_callback = version_callback_factory("HMDA Data Manager", "0.2.0")
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import typer

from mortgage_data_manager.core.cli.formatting import console


def validate_year_range(
    min_year: int | None = None,
    max_year: int | None = None,
    default_min: int = 2007,
    default_max: int | None = None,
    min_allowed: int = 1990,
    max_allowed: int | None = None,
) -> range:
    """Validate and return a year range.

    Args:
        min_year: Minimum year (uses default_min if None)
        max_year: Maximum year (uses default_max if None)
        default_min: Default minimum year if min_year is None
        default_max: Default maximum year if max_year is None (current year if None)
        min_allowed: Earliest allowed year
        max_allowed: Latest allowed year (current year if None)

    Returns:
        range: Range object from min_year to max_year (inclusive)

    Raises:
        typer.BadParameter: If years are invalid or out of range

    Example:
        # Basic usage with defaults
        years = validate_year_range(min_year=2020, max_year=2024)
        # Returns: range(2020, 2025)

        # With custom defaults
        years = validate_year_range(
            min_year=None,  # Uses default_min
            max_year=None,  # Uses current year
            default_min=2018,
        )
    """
    current_year = datetime.now().year

    # Apply defaults
    if default_max is None:
        default_max = current_year
    if max_allowed is None:
        max_allowed = current_year

    actual_min = min_year if min_year is not None else default_min
    actual_max = max_year if max_year is not None else default_max

    # Validate bounds
    if actual_min < min_allowed:
        raise typer.BadParameter(
            f"min_year ({actual_min}) cannot be earlier than {min_allowed}"
        )
    if actual_max > max_allowed:
        raise typer.BadParameter(
            f"max_year ({actual_max}) cannot be later than {max_allowed}"
        )
    if actual_min > actual_max:
        raise typer.BadParameter(
            f"min_year ({actual_min}) cannot be greater than max_year ({actual_max})"
        )

    return range(actual_min, actual_max + 1)


def parse_year_string(year_str: str) -> range:
    """Parse a year string into a range.

    Supports formats:
    - "2020" -> range(2020, 2021)
    - "2020-2024" -> range(2020, 2025)

    Args:
        year_str: Year string (e.g., "2020" or "2020-2024")

    Returns:
        range: Range object representing the years

    Raises:
        ValueError: If the year string format is invalid
    """
    if "-" in year_str:
        parts = year_str.split("-")
        if len(parts) != 2:
            raise ValueError(
                f"Invalid year range: {year_str}. "
                "Expected format: 'START-END' (e.g., '2018-2024')"
            )
        try:
            min_year = int(parts[0])
            max_year = int(parts[1])
        except ValueError:
            raise ValueError(
                f"Invalid year range: {year_str}. Years must be integers."
            )

        if min_year > max_year:
            raise ValueError(
                f"Invalid year range: {year_str}. Start year must be <= end year."
            )

        return range(min_year, max_year + 1)
    else:
        try:
            year = int(year_str)
        except ValueError:
            raise ValueError(f"Invalid year: {year_str}. Expected integer or range.")

        return range(year, year + 1)


def version_callback_factory(
    package_name: str,
    version: str,
) -> Callable[[bool], None]:
    """Create a version callback function for a CLI.

    This factory creates a consistent version callback for use with
    typer.Option's callback parameter.

    Args:
        package_name: Display name of the package (e.g., "HMDA Data Manager")
        version: Version string (e.g., "0.2.0")

    Returns:
        Callable: Version callback function for Typer

    Example:
        from mortgage_data_manager import __version__

        version_callback = version_callback_factory("My Data Manager", __version__)

        @app.callback()
        def main(
            version: Annotated[
                bool,
                typer.Option(
                    "--version", "-V",
                    callback=version_callback,
                    is_eager=True,
                    help="Show version and exit"
                )
            ] = False,
        ):
            pass
    """
    def _version_callback(value: bool) -> None:
        if value:
            console.print(
                f"[bold cyan]{package_name}[/bold cyan] version [green]{version}[/green]"
            )
            raise typer.Exit()

    return _version_callback
