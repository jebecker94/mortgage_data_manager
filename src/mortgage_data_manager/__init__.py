"""Mortgage Data Manager - unified mortgage market data management.

The single source of the package version is the installed distribution
metadata (set in pyproject.toml). Subpackages must not declare their own
``__version__``; import it from here instead.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mortgage-data-manager")
except PackageNotFoundError:  # Running from a source tree without an install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
