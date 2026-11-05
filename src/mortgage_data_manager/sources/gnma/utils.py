#!/usr/bin/env python3
"""Shared utilities and type definitions for GNMA data processing.

Shared utility functions, result classes, and exceptions used across
the GNMA data processing pipeline.

Classes:
    - ConversionResult: Results from data format conversion operations
    - ProcessingResult: Results from data processing operations
    - DateFormatError: Exception for date parsing errors
    - DownloadError: Exception for download errors

Functions:
    - create_default_configs: Create default configuration objects
    - Date manipulation utilities
    - Path and file utilities

"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import cast

import typer
from decouple import config
from rich.console import Console

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.logging import get_logger

from .config import DownloadConfig, GNMAConfig, ProcessorConfig

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


# ==========================================
# Result Classes
# ==========================================


class ConversionResult:
    """Counts and per-file outcomes of a raw-to-bronze conversion run.

    ``total`` is the number of raw files found; each ends up in exactly one of
    ``successful``, ``failed`` (with an entry in ``errors``) or ``skipped``.
    """

    def __init__(self) -> None:
        self.successful = 0
        self.failed = 0
        self.skipped = 0
        self.total = 0
        self.errors: list[dict] = []
        self.processed_files: list[str] = []
        self.skipped_files: list[str] = []

    @property
    def total_attempted(self) -> int:
        """Files converted or failed (skipped files were not attempted)."""
        return cast(int, self.successful + self.failed)

    def add_success(self, file_path: str):
        """Record a file converted to bronze.

        Args:
            file_path: The raw file converted.
        """
        self.successful += 1
        self.processed_files.append(file_path)

    def add_failure(self, file_path: str, error: str):
        """Record a file that failed to convert, with its error and a timestamp.

        Args:
            file_path: The raw file that failed.
            error: What went wrong.
        """
        self.failed += 1
        self.errors.append(
            {"file": file_path, "error": error, "timestamp": datetime.datetime.now().isoformat()}
        )

    def add_skip(self, file_path: str | Path, reason: str = "File already exists"):
        """Record a file skipped without converting.

        Args:
            file_path: The raw file skipped.
            reason: Why it was skipped (not stored; kept for callers' readability).
        """
        self.skipped += 1
        self.skipped_files.append(str(file_path))


class ProcessingResult:
    """Counts and per-file outcomes of a bronze-to-silver run.

    One outcome is recorded per (bronze file, record type) attempted, so the counts can
    exceed ``total_files``; ``records_by_type`` sums the rows written per record type.
    """

    def __init__(self) -> None:
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
        """Outcomes written or failed (skipped ones were not attempted)."""
        return cast(int, self.successful + self.failed)

    def add_success(
        self, file_path: str | Path, output_path: str | Path, record_count: int, record_type: str
    ):
        """Record a silver file written and the rows it holds.

        Args:
            file_path: The bronze file read.
            output_path: The silver file written.
            record_count: Rows written.
            record_type: The record type the rows belong to.
        """
        self.successful += 1
        self.total_records_processed += record_count
        self.processed_files.append(str(file_path))
        self.output_files.append(str(output_path))

        if record_type in self.records_by_type:
            self.records_by_type[record_type] += record_count
        else:
            self.records_by_type[record_type] = record_count

    def add_failure(self, file_path: str, error: str):
        """Record a bronze file (and record type) that failed, with its error and a timestamp.

        Args:
            file_path: The bronze file that failed.
            error: What went wrong, prefixed with the record type where known.
        """
        self.failed += 1
        self.errors.append(
            {"file": file_path, "error": error, "timestamp": datetime.datetime.now().isoformat()}
        )

    def add_skip(self, file_path: str | Path, reason: str = "File already exists"):
        """Record an outcome skipped without writing.

        Args:
            file_path: The bronze file skipped.
            reason: Why it was skipped (not stored; kept for callers' readability).
        """
        self.skipped += 1
        self.skipped_files.append(str(file_path))


# ==========================================
# Configuration Utilities
# ==========================================


def create_default_configs(
    email_value: str,
    answer_value: str,
    user_agent: str,
    data_dir: str | Path | None = None,
    schemas_dir: str | Path | None = None,
) -> tuple[DownloadConfig, ProcessorConfig]:
    """Create default configurations for the download + processing pipeline.

    Args:
        email_value: Email for GNMA authentication
        answer_value: Answer to the profile's secret question
        user_agent: User agent string
        data_dir: GNMA data root holding ``raw/``, ``bronze/`` and ``silver/`` (defaults
            to GNMAConfig.GNMA_DATA_DIR)
        schemas_dir: Root for the layout-PDF download (defaults to
            GNMAConfig.GNMA_SCHEMAS_DIR)

    Returns:
        Tuple of (DownloadConfig, ProcessorConfig)
    """
    # Use GNMAConfig defaults if not specified
    data_root = Path(data_dir) if data_dir is not None else GNMAConfig.GNMA_DATA_DIR
    schemas_root = Path(schemas_dir) if schemas_dir is not None else GNMAConfig.GNMA_SCHEMAS_DIR

    download_config = DownloadConfig(
        email_value=email_value,
        answer_value=answer_value,
        user_agent=user_agent,
        data_download_folder=data_root / "raw",
        schema_download_folder=schemas_root,
    )

    processor_config = ProcessorConfig(
        raw_folder=data_root / "raw",
        bronze_folder=data_root / "bronze",
        silver_folder=data_root / "silver",
    )

    return download_config, processor_config


def create_configs_from_env() -> tuple[DownloadConfig, ProcessorConfig]:
    """Create configurations using environment variables.

    Requires these environment variables (set in .env or the environment):
    - GNMA_EMAIL: Ginnie Mae download-profile email
    - GNMA_ANSWER: Answer to the profile's secret question

    The User-Agent comes from MortgageDataConfig.USER_AGENT
    (override via MORTGAGE_DATA_USER_AGENT).

    Returns:
        Tuple of configured instances
    """
    gnma_email = config("GNMA_EMAIL", default=None)
    gnma_answer = config("GNMA_ANSWER", default=None)

    missing_vars = [
        name
        for name, value in [("GNMA_EMAIL", gnma_email), ("GNMA_ANSWER", gnma_answer)]
        if not value
    ]
    if missing_vars:
        raise ValueError(f"Missing required environment variables: {missing_vars}")

    return create_default_configs(
        email_value=gnma_email,
        answer_value=gnma_answer,
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
        r"^\d{4}-\d{2}-\d{2}$": "%Y-%m-%d",
        r"^\d{4}/\d{2}/\d{2}$": "%Y/%m/%d",
        r"^\d{2}/\d{2}/\d{4}$": "%m/%d/%Y",
        r"^\d{2}-\d{2}-\d{4}$": "%m-%d-%Y",
        r"^\d{8}$": "%Y%m%d",
        r"^\d{6}$": "%Y%m",
        r"^\d{14}$": "%Y%m%d%H%M%S",
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
    firstlast: str = "last",
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
    period_map = {"monthly": "M", "quarterly": "Q", "yearly": "Y"}

    if frequency not in period_map:
        raise ValueError(f"Unsupported frequency: {frequency}")

    date_period = pd.Series(current_date).dt.to_period(period_map[frequency])

    # Get appropriate date
    if firstlast == "first":
        key_date = date_period.dt.start_time[0]
    else:
        key_date = date_period.dt.end_time[0]

    return cast(str, key_date.strftime(date_format))


# ==========================================
# Data Loading Utilities
# ==========================================


def load_prefix_dictionary(dictionary_file: str | Path) -> dict:
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
        >>> from mortgage_data_manager.sources.gnma.utils import load_prefix_dictionary
        >>> from mortgage_data_manager.sources.gnma.config import GNMAConfig
        >>>
        >>> prefix_dict = load_prefix_dictionary(
        ...     GNMAConfig.PROJECT_DIR / "prefix_dictionary.yaml"
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
        prefix_dict = {k: v for k, v in prefix_dict.items() if not str(k).startswith("#")}

        logger.info("Loaded %s prefixes from %s", len(prefix_dict), dictionary_path.name)

        return prefix_dict

    except yaml.YAMLError as e:
        raise ValueError(f"Error parsing YAML file: {e}")


def default_prefixes(prefix_dict: dict) -> list[str]:
    """Return the default set: the prefixes downloaded and processed when none are named.

    Excludes any entry whose YAML config sets ``default_download: false``. The
    default set is the prefixes some reader uses (the loan-level portfolio and new
    issuance, the pool files behind the combined MBS tables, the issuer files); the
    rest stay buildable when named but are not refreshed by a bare run.

    Args:
        prefix_dict: Loaded prefix dictionary (from load_prefix_dictionary).

    Returns:
        List of prefix names with default_download != False.
    """
    return [
        k
        for k, v in prefix_dict.items()
        if not str(k).startswith("#")
        and not (isinstance(v, dict) and v.get("default_download") is False)
    ]


# ==========================================
# Path Utilities
# ==========================================


# ==========================================
# CLI Validators
# ==========================================

# Common GNMA prefixes
COMMON_PREFIXES = [
    "monthly",
    "llmon1",
    "llmon2",
    "hllmon1",
    "hllmon2",
    "hmbs1",
    "hmbs2",
    "platmon1",
    "platmon2",
    "platissmon",
]


# Record types of the legacy fixed-width loan-level files; the full set for a prefix is
# the record_types of its canonical schema.
VALID_RECORD_TYPES = ["H", "L", "P", "I", "T"]


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
        valid_prefixes = [k for k in prefix_dict.keys() if not str(k).startswith("#")]

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


def validate_record_types(record_types: list[str], prefixes: list[str] | None = None) -> None:
    """Validate record type codes against the prefixes' canonical schemas.

    A code is valid when at least one of ``prefixes`` declares it (``LL`` for ``llmon``,
    ``PS`` for ``nimonSFPS``, ...). Without prefixes, or when none has a canonical schema,
    the legacy loan-level set `VALID_RECORD_TYPES` applies.

    Args:
        record_types: List of record types to validate.
        prefixes: Prefixes whose canonical schemas define the valid codes.

    Raises:
        typer.Exit: If validation fails.
    """
    from .canonical import CanonicalSchema

    valid: set[str] = set()
    for prefix in prefixes or []:
        if CanonicalSchema.exists(prefix, GNMAConfig.GNMA_CANONICAL_DIR):
            schema = CanonicalSchema.load(prefix, GNMAConfig.GNMA_CANONICAL_DIR)
            valid.update(schema.record_type_codes())
    if not valid:
        valid = set(VALID_RECORD_TYPES)
    for rt in record_types:
        if rt not in valid:
            _validator_console.print(f"[red]✗[/red] Invalid record type: {rt}")
            _validator_console.print(
                f"[yellow]Valid record types:[/yellow] {', '.join(sorted(valid))}"
            )
            raise typer.Exit(code=1)
