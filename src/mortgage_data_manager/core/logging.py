"""Standardized logging configuration for mortgage data manager.

This module provides utilities for setting up consistent logging across all
subpackages.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def configure_logging(
    level: LogLevel = "INFO",
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
            format_string = (
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
        else:
            format_string = "%(name)s - %(levelname)s - %(message)s"

    # Configure root logger
    logging.basicConfig(
        level=getattr(logging, level),
        format=format_string,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # If log file specified, add file handler
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(getattr(logging, level))
        file_handler.setFormatter(
            logging.Formatter(format_string, datefmt="%Y-%m-%d %H:%M:%S")
        )

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


def log_function_call[**P, T](func: Callable[P, T]) -> Callable[P, T]:
    """Decorator to log function calls with arguments.

    Example:
        >>> @log_function_call
        ... def process_data(year: int, month: int):
        ...     return f"Processing {year}-{month}"
        ...
        >>> result = process_data(2020, 6)
        >>> # Logs: "Calling process_data with args=(2020, 6), kwargs={}"
    """
    import functools

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        logger = get_logger(func.__module__)
        logger.debug(
            f"Calling {func.__name__} with args={args}, kwargs={kwargs}"
        )
        try:
            result = func(*args, **kwargs)
            logger.debug(f"{func.__name__} completed successfully")
            return result
        except Exception as e:
            logger.error(
                f"{func.__name__} raised {type(e).__name__}: {e}",
                exc_info=True
            )
            raise

    return wrapper


def log_execution_time[**P, T](func: Callable[P, T]) -> Callable[P, T]:
    """Decorator to log function execution time.

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
        logger.info(
            f"{func.__name__} completed in {execution_time:.2f} seconds"
        )

        return result

    return wrapper


class LogContext:
    """Context manager for temporary logging level changes.

    Example:
        >>> logger = get_logger(__name__)
        >>> logger.setLevel(logging.INFO)
        >>>
        >>> with LogContext(logger, "DEBUG"):
        ...     logger.debug("This will be logged")
        ...
        >>> logger.debug("This will NOT be logged (back to INFO)")
    """

    def __init__(self, logger: logging.Logger, level: LogLevel):
        """Initialize log context.

        Args:
            logger: Logger to modify
            level: Temporary logging level
        """
        self.logger = logger
        self.new_level = getattr(logging, level)
        self.old_level = logger.level

    def __enter__(self) -> logging.Logger:
        """Enter context - set new logging level."""
        self.logger.setLevel(self.new_level)
        return self.logger

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit context - restore original logging level."""
        self.logger.setLevel(self.old_level)


def setup_file_logging(
    logger: logging.Logger,
    log_file: Path,
    level: LogLevel = "DEBUG",
) -> logging.Handler:
    """Add file handler to an existing logger.

    Args:
        logger: Logger to add file handler to
        log_file: Path to log file
        level: Logging level for file handler

    Returns:
        The created file handler

    Example:
        >>> logger = get_logger(__name__)
        >>> handler = setup_file_logging(
        ...     logger,
        ...     Path("logs/download.log"),
        ...     level="DEBUG"
        ... )
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(getattr(logging, level))
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
    )

    logger.addHandler(file_handler)

    return file_handler
