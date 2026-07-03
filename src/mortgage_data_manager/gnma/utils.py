#!/usr/bin/env python3
"""GNMA Data Manager - Utilities and Type Definitions.

Shared utility functions, result classes, and exceptions used across
the GNMA data processing pipeline.

Classes:
    - ConversionResult: Results from data format conversion operations
    - ProcessingResult: Results from data processing operations
    - DateFormatError: Exception for date parsing errors
    - DownloadError: Exception for download errors
    - SchemaReaderError: Exception for schema reader errors

Functions:
    - create_default_configs: Create default configuration objects
    - Date manipulation utilities
    - Path and file utilities
    - Logging utilities (deprecated - use core.logging instead)

"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

import typer
from decouple import config
from rich.console import Console

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.logging import get_logger

from .config import DownloadConfig, GNMAConfig, ProcessorConfig, SchemaReaderConfig

_validator_console = Console()

# Module logger using core logging
logger = get_logger(__name__)


# ==========================================
# Exceptions
# ==========================================

class DateFormatError(Exception):
    """Custom exception for date format detection errors."""
    pass


class DownloadError(Exception):
    """Custom exception for download errors."""
    pass


class SchemaReaderError(Exception):
    """Custom exception for schema reader errors."""
    pass


# ==========================================
# Result Classes
# ==========================================

class ConversionResult:
    """Container for conversion operation results."""

    def __init__(self):
        self.successful = 0
        self.failed = 0
        self.skipped = 0
        self.total = 0
        self.errors: list[dict] = []
        self.processed_files: list[str] = []
        self.skipped_files: list[str] = []

    @property
    def total_attempted(self) -> int:
        return self.successful + self.failed

    @property
    def success_rate(self) -> float:
        if self.total_attempted == 0:
            return 0.0
        return (self.successful / self.total_attempted) * 100

    def add_success(self, file_path: str):
        self.successful += 1
        self.processed_files.append(file_path)

    def add_failure(self, file_path: str, error: str):
        self.failed += 1
        self.errors.append({
            'file': file_path,
            'error': error,
            'timestamp': datetime.datetime.now().isoformat()
        })

    def add_skip(self, file_path: str | Path, reason: str = "File already exists"):
        self.skipped += 1
        self.skipped_files.append(str(file_path))


class ProcessingResult:
    """Container for processing operation results."""

    def __init__(self):
        self.successful = 0
        self.failed = 0
        self.skipped = 0
        self.total_files = 0
        self.total_records_processed = 0
        self.records_by_type: dict[str, int] = {}
        self.errors: list[dict] = []
        self.processed_files: list[str] = []
        self.skipped_files: list[str] = []
        self.output_files: list[str] = []

    @property
    def total_attempted(self) -> int:
        return self.successful + self.failed

    @property
    def success_rate(self) -> float:
        if self.total_attempted == 0:
            return 0.0
        return (self.successful / self.total_attempted) * 100

    def add_success(self, file_path: str | Path, output_path: str | Path,
                   record_count: int, record_type: str):
        self.successful += 1
        self.total_records_processed += record_count
        self.processed_files.append(str(file_path))
        self.output_files.append(str(output_path))

        if record_type in self.records_by_type:
            self.records_by_type[record_type] += record_count
        else:
            self.records_by_type[record_type] = record_count

    def add_failure(self, file_path: str, error: str):
        self.failed += 1
        self.errors.append({
            'file': file_path,
            'error': error,
            'timestamp': datetime.datetime.now().isoformat()
        })

    def add_skip(self, file_path: str | Path, reason: str = "File already exists"):
        self.skipped += 1
        self.skipped_files.append(str(file_path))

# ==========================================
# Configuration Utilities
# ==========================================

def create_default_configs(
    email_value: str,
    id_value: str,
    user_agent: str,
    base_data_folder: str | Path | None = None,
    base_schema_folder: str | Path | None = None
) -> tuple[DownloadConfig, ProcessorConfig, SchemaReaderConfig]:
    """Create default configurations for all pipeline components.

    Args:
        email_value: Email for GNMA authentication
        id_value: ID for GNMA authentication
        user_agent: User agent string
        base_data_folder: Base folder for data files (defaults to GNMAConfig.GNMA_DATA_DIR)
        base_schema_folder: Base folder for schema files (defaults to GNMAConfig.GNMA_SCHEMAS_DIR)

    Returns:
        Tuple of (DownloadConfig, ProcessorConfig, SchemaReaderConfig)
    """
    # Use GNMAConfig defaults if not specified
    if base_data_folder is None:
        base_data_folder = GNMAConfig.GNMA_DATA_DIR
    else:
        base_data_folder = Path(base_data_folder)

    if base_schema_folder is None:
        base_schema_folder = GNMAConfig.GNMA_SCHEMAS_DIR
    else:
        base_schema_folder = Path(base_schema_folder)

    download_config = DownloadConfig(
        email_value=email_value,
        id_value=id_value,
        user_agent=user_agent,
        data_download_folder=base_data_folder / "raw",
        schema_download_folder=base_schema_folder
    )

    processor_config = ProcessorConfig(
        raw_folder=base_data_folder / "raw",
        bronze_folder=base_data_folder / "bronze",
        silver_folder=base_data_folder / "silver",
        schema_folder=base_schema_folder / "combined"
    )

    schema_reader_config = SchemaReaderConfig(
        input_folder=base_data_folder / "raw",
        output_folder=base_data_folder / "raw",
        schemas_folder=base_schema_folder
    )

    return download_config, processor_config, schema_reader_config


def create_configs_from_env() -> tuple[DownloadConfig, ProcessorConfig, SchemaReaderConfig]:
    """Create configurations using environment variables.

    Requires these environment variables (set in .env or the environment):
    - GNMA_EMAIL: GNMA eDisclosure email
    - GNMA_ID: GNMA eDisclosure ID

    The User-Agent comes from MortgageDataConfig.USER_AGENT
    (override via MORTGAGE_DATA_USER_AGENT).

    Returns:
        Tuple of configured instances
    """
    gnma_email = config('GNMA_EMAIL', default=None)
    gnma_id = config('GNMA_ID', default=None)

    missing_vars = [
        name for name, value in [('GNMA_EMAIL', gnma_email), ('GNMA_ID', gnma_id)]
        if not value
    ]
    if missing_vars:
        raise ValueError(f"Missing required environment variables: {missing_vars}")

    return create_default_configs(
        email_value=gnma_email,
        id_value=gnma_id,
        user_agent=MortgageDataConfig.USER_AGENT,
    )


# ==========================================
# Date Utilities
# ==========================================

def get_date_format(date_string: str) -> str:
    """Analyze a date string and return the corresponding strptime/strftime format.

    Args:
        date_string: The string containing the date to analyze

    Returns:
        The detected strptime/strftime format string

    Raises:
        DateFormatError: If the format cannot be determined
    """
    if not isinstance(date_string, str):
        raise TypeError("Input must be a string.")

    patterns = {
        r'^\d{4}-\d{2}-\d{2}$': '%Y-%m-%d',
        r'^\d{4}/\d{2}/\d{2}$': '%Y/%m/%d',
        r'^\d{2}/\d{2}/\d{4}$': '%m/%d/%Y',
        r'^\d{2}-\d{2}-\d{4}$': '%m-%d-%Y',
        r'^\d{8}$': '%Y%m%d',
        r'^\d{6}$': '%Y%m',
        r'^\d{14}$': '%Y%m%d%H%M%S',
    }

    for pattern, date_format in patterns.items():
        if re.match(pattern, date_string):
            try:
                datetime.datetime.strptime(date_string, date_format)
                return date_format
            except ValueError:
                continue

    raise DateFormatError(f"Could not determine date format for input: '{date_string}'")


def create_date_suffix(
    current_date: datetime.datetime,
    date_format: str,
    frequency: str,
    firstlast: str = 'last',
) -> str:
    """Create a date suffix based on the current date, format, frequency, and period.

    Args:
        current_date: The reference date
        date_format: The desired output format
        frequency: 'monthly', 'quarterly', or 'yearly'
        firstlast: 'first' or 'last' day of the period

    Returns:
        Formatted date string

    Raises:
        ValueError: If frequency is not supported
    """
    import pandas as pd

    # Convert to pandas period
    period_map = {
        'monthly': 'M',
        'quarterly': 'Q',
        'yearly': 'Y'
    }

    if frequency not in period_map:
        raise ValueError(f"Unsupported frequency: {frequency}")

    date_period = pd.Series(current_date).dt.to_period(period_map[frequency])

    # Get appropriate date
    if firstlast == 'first':
        key_date = date_period.dt.start_time[0]
    else:
        key_date = date_period.dt.end_time[0]

    return key_date.strftime(date_format)


# ==========================================
# Data Loading Utilities
# ==========================================

def load_prefix_dictionary(
    dictionary_file: str | Path
) -> dict:
    """Load the prefix dictionary from YAML file.

    The prefix dictionary contains configuration for each data prefix,
    including date ranges, file extensions, frequencies, etc.

    Args:
        dictionary_file: Path to the YAML dictionary file

    Returns:
        Dictionary mapping prefix names to their configuration

    Raises:
        FileNotFoundError: If dictionary file doesn't exist
        ValueError: If YAML file is invalid

    Example:
        >>> from mortgage_data_manager.gnma.utils import load_prefix_dictionary
        >>> from mortgage_data_manager.gnma.config import PROJECT_ROOT
        >>>
        >>> prefix_dict = load_prefix_dictionary(
        ...     PROJECT_ROOT / "prefix_dictionary.yaml"
        ... )
        >>> print(prefix_dict['monthly']['extension'])
        'zip'
    """
    import yaml

    dictionary_path = Path(dictionary_file)

    if not dictionary_path.exists():
        raise FileNotFoundError(
            f"Dictionary file not found: {dictionary_path}\n"
            f"Expected location: {dictionary_path.absolute()}"
        )

    try:
        with open(dictionary_path) as f:
            prefix_dict = yaml.safe_load(f)

        if not isinstance(prefix_dict, dict):
            raise ValueError("Dictionary file must contain a YAML dictionary")

        # Filter out commented entries (keys starting with '#')
        prefix_dict = {
            k: v for k, v in prefix_dict.items()
            if not str(k).startswith('#')
        }

        logger.info(
            f"Loaded {len(prefix_dict)} prefixes from {dictionary_path.name}"
        )

        return prefix_dict

    except yaml.YAMLError as e:
        raise ValueError(f"Error parsing YAML file: {e}")


def default_prefixes(prefix_dict: dict) -> list[str]:
    """Return the prefixes that should be downloaded/processed by default.

    Excludes any entry whose YAML config sets ``default_download: false``.
    Use this in workflows when the user has not specified an explicit prefix
    list, so that opt-out prefixes (e.g. pool supplementals fully derivable
    from loan-level files) are skipped unless explicitly requested.

    Args:
        prefix_dict: Loaded prefix dictionary (from load_prefix_dictionary).

    Returns:
        List of prefix names with default_download != False.
    """
    return [
        k for k, v in prefix_dict.items()
        if not str(k).startswith('#')
        and not (isinstance(v, dict) and v.get('default_download') is False)
    ]


# ==========================================
# Path Utilities
# ==========================================

def ensure_directory_exists(directory: str | Path) -> Path:
    """Ensure a directory exists, creating it if necessary.

    Args:
        directory: Path to directory

    Returns:
        Path object for the directory
    """
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    return path


# ==========================================
# CLI Validators
# ==========================================

# Common GNMA prefixes
COMMON_PREFIXES = [
    'monthly', 'llmon1', 'llmon2', 'hllmon1', 'hllmon2',
    'hmbs1', 'hmbs2', 'platmon1', 'platmon2', 'platissmon'
]

# Performance prefixes (those that have loan-level data)
PERFORMANCE_PREFIXES = ['llmon1', 'llmon2', 'hllmon1', 'hllmon2']

# Record types
VALID_RECORD_TYPES = ['H', 'L', 'P', 'I', 'T']


def validate_prefixes(prefixes: list[str], allow_all: bool = True) -> None:
    """Validate prefix names against the GNMA prefix dictionary.

    Args:
        prefixes: List of prefixes to validate.
        allow_all: Whether to allow empty list (meaning all prefixes).

    Raises:
        typer.Exit: If validation fails.
    """
    if not prefixes and not allow_all:
        _validator_console.print("[red]✗[/red] At least one prefix must be specified")
        raise typer.Exit(code=1)

    try:
        prefix_dict = load_prefix_dictionary(GNMAConfig.GNMA_PREFIX_DICTIONARY)
        valid_prefixes = [k for k in prefix_dict.keys() if not str(k).startswith('#')]

        for prefix in prefixes:
            if prefix not in valid_prefixes:
                _validator_console.print(f"[red]✗[/red] Invalid prefix: {prefix}")
                _validator_console.print(
                    f"[yellow]Valid prefixes:[/yellow] {', '.join(sorted(valid_prefixes))}"
                )
                raise typer.Exit(code=1)

    except FileNotFoundError:
        for prefix in prefixes:
            if prefix not in COMMON_PREFIXES:
                _validator_console.print(
                    f"[yellow]Warning:[/yellow] Prefix '{prefix}' not in common prefixes list"
                )
                _validator_console.print(
                    f"[dim]Common prefixes: {', '.join(COMMON_PREFIXES)}[/dim]"
                )


def validate_date_format(date: str) -> None:
    """Validate YYYYMM date format.

    Args:
        date: Date string to validate.

    Raises:
        typer.Exit: If validation fails.
    """
    if not date:
        return

    if len(date) != 6 or not date.isdigit():
        _validator_console.print(f"[red]✗[/red] Invalid date format: {date}")
        _validator_console.print("[yellow]Expected format:[/yellow] YYYYMM (e.g., 202401)")
        raise typer.Exit(code=1)

    year = int(date[:4])
    month = int(date[4:])

    if year < 1990 or year > 2100:
        _validator_console.print(f"[red]✗[/red] Invalid year: {year}")
        raise typer.Exit(code=1)

    if month < 1 or month > 12:
        _validator_console.print(f"[red]✗[/red] Invalid month: {month}")
        raise typer.Exit(code=1)


def validate_record_types(record_types: list[str]) -> None:
    """Validate record type codes.

    Args:
        record_types: List of record types to validate.

    Raises:
        typer.Exit: If validation fails.
    """
    for rt in record_types:
        if rt not in VALID_RECORD_TYPES:
            _validator_console.print(f"[red]✗[/red] Invalid record type: {rt}")
            _validator_console.print(
                f"[yellow]Valid record types:[/yellow] {', '.join(VALID_RECORD_TYPES)}"
            )
            raise typer.Exit(code=1)

