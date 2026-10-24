"""Shared CLI checks: the ``--version`` callback and argument validators.

Example usage:
    from mortgage_data_manager.core.cli.validation import version_callback_factory

    version_callback = version_callback_factory("HMDA Data Manager", "0.2.0")
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable

import typer

from mortgage_data_manager.core.cli.formatting import console


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
            console.print(f"[bold cyan]{package_name}[/bold cyan] version [green]{version}[/green]")
            raise typer.Exit()

    return _version_callback


def validate_datasets(
    datasets: Iterable[str], valid: Collection[str], label: str = "datasets"
) -> None:
    """Exit with an error naming every dataset not in a source's registry.

    Args:
        datasets: Dataset identifiers given on the command line.
        valid: The source's registry (``VALID_DATASETS``), listed in the error.
        label: Plural noun for the message (e.g. ``"CRT datasets"``).

    Raises:
        typer.Exit: If any identifier is not in ``valid``.
    """
    invalid = [d for d in datasets if d not in valid]
    if invalid:
        console.print(f"[red]Error: Invalid {label}:[/red] {', '.join(invalid)}")
        console.print(f"[yellow]Valid {label}:[/yellow] {', '.join(valid)}")
        raise typer.Exit(code=1)


def validate_years(years: Iterable[int], min_year: int, max_year: int) -> None:
    """Exit with an error naming every year outside a source's coverage.

    Args:
        years: Requested years.
        min_year: First year the source covers.
        max_year: Last year the source covers.

    Raises:
        typer.Exit: If any year falls outside ``[min_year, max_year]``.
    """
    invalid = [y for y in years if not min_year <= y <= max_year]
    if invalid:
        console.print(f"[red]Error: Years out of valid range:[/red] {', '.join(map(str, invalid))}")
        console.print(f"[yellow]Valid range:[/yellow] {min_year}-{max_year}")
        raise typer.Exit(code=1)
