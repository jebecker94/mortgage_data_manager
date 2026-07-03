"""Core CLI utilities for Mortgage Data Manager.

This module provides shared utilities for building consistent CLIs across all subpackages:
- formatting: Rich console output helpers (print_success, print_error, etc.)
- progress: Progress bars and spinners using Rich
- validation: Common input validation (year ranges, file types, etc.)
- download_options: Standardized Typer options for download commands

Example usage:
    from mortgage_data_manager.core.cli import (
        console,
        print_success,
        print_error,
        print_info_table,
        create_progress,
        create_spinner,
        validate_year_range,
        version_callback_factory,
        OutputDirOption,
        OverwriteOption,
    )
"""

from mortgage_data_manager.core.cli.download_options import (
    OutputDirOption,
    OverwriteOption,
    PauseOption,
    QuietOption,
    RetriesOption,
    TimeoutOption,
    VerboseOption,
    get_download_mode,
    resolve_output_dir,
)
from mortgage_data_manager.core.cli.formatting import (
    console,
    print_error,
    print_info_table,
    print_path_info,
    print_section_header,
    print_success,
    print_summary,
    print_warning,
)
from mortgage_data_manager.core.cli.progress import (
    create_progress,
    create_spinner,
)
from mortgage_data_manager.core.cli.validation import (
    validate_year_range,
    version_callback_factory,
)

__all__ = [
    # formatting
    "console",
    "print_success",
    "print_warning",
    "print_error",
    "print_summary",
    "print_section_header",
    "print_path_info",
    "print_info_table",
    # progress
    "create_progress",
    "create_spinner",
    # validation
    "validate_year_range",
    "version_callback_factory",
    # download options
    "OutputDirOption",
    "OverwriteOption",
    "PauseOption",
    "TimeoutOption",
    "RetriesOption",
    "VerboseOption",
    "QuietOption",
    "resolve_output_dir",
    "get_download_mode",
]
