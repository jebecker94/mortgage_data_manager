"""The shared ``--version`` callback for subpackage CLIs.

Example usage:
    from mortgage_data_manager.core.cli.validation import version_callback_factory

    version_callback = version_callback_factory("HMDA Data Manager", "0.2.0")
"""

from __future__ import annotations

from collections.abc import Callable

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
