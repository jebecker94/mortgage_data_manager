"""Standardized logging configuration for mortgage data manager.

This module provides utilities for setting up consistent logging across all
subpackages.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Literal

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def configure_logging(
    level: str = "INFO",
    format_string: str | None = None,
    log_file: Path | None = None,
    include_timestamp: bool = True,
) -> None:
    """Configure logging for mortgage data manager.

    Args:
        level: Logging level ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')
        format_string: Custom format string. If None, uses default format.
        log_file: If provided, also log to this file
        include_timestamp: If True, include timestamp in log messages

    Example:
        >>> from mortgage_data_manager.core.logging import configure_logging
        >>> configure_logging(level="DEBUG")
        >>> logger = get_logger("my_module")
        >>> logger.info("Processing started")
    """
    if format_string is None:
        if include_timestamp:
            format_string = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        else:
            format_string = "%(name)s - %(levelname)s - %(message)s"

    # Configure root logger. basicConfig only acts on the first call, so set the level
    # explicitly: a CLI callback's INFO default must not pin a command's --quiet/--verbose.
    logging.basicConfig(
        level=getattr(logging, level),
        format=format_string,
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger().setLevel(getattr(logging, level))

    # If log file specified, add file handler
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(getattr(logging, level))
        file_handler.setFormatter(logging.Formatter(format_string, datefmt="%Y-%m-%d %H:%M:%S"))

        logging.getLogger().addHandler(file_handler)


def get_logger(name: str, level: LogLevel | None = None) -> logging.Logger:
    """Get logger for a module.

    Args:
        name: Logger name (typically __name__ of the module)
        level: If provided, set this logger's level (overrides root level)

    Returns:
        Configured logger instance

    Example:
        >>> logger = get_logger(__name__)
        >>> logger.info("Starting download process")
        >>> logger.warning("Missing some data for year 2020")
    """
    logger = logging.getLogger(name)

    if level:
        logger.setLevel(getattr(logging, level))

    return logger


def log_execution_time[**P, T](func: Callable[P, T]) -> Callable[P, T]:
    """Decorator to log function execution time.

    Args:
        func: The function to wrap.

    Returns:
        The wrapped function, which logs its elapsed time on return.

    Example:
        >>> @log_execution_time
        ... def slow_operation():
        ...     import time
        ...     time.sleep(2)
        ...     return "done"
        ...
        >>> result = slow_operation()
        >>> # Logs: "slow_operation completed in 2.00 seconds"
    """
    import functools
    import time

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        logger = get_logger(func.__module__)
        start_time = time.time()

        result = func(*args, **kwargs)

        execution_time = time.time() - start_time
        logger.info("%s completed in %.2f seconds", func.__name__, execution_time)

        return result

    return wrapper
