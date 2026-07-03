# Core Utilities API Reference

This document provides detailed API reference for the core utilities shared across all subpackages.

## Table of Contents

- [Configuration (`core.config`)](#configuration-coreconfig)
- [Medallion Architecture (`core.medallion`)](#medallion-architecture-coremedallion)
- [Logging (`core.logging`)](#logging-corelogging)
- [I/O Operations (`core.io`)](#io-operations-coreio)

---

## Configuration (`core.config`)

The configuration module provides base configuration management for all subpackages.

### `MortgageDataConfig`

Base configuration class that all subpackage configurations inherit from.

**Module:** `mortgage_data_manager.core.config`

#### Class Attributes

| Attribute | Type | Description | Default |
|-----------|------|-------------|---------|
| `PROJECT_DIR` | `Path` | Root project directory | Auto-detected from package location |
| `DATA_DIR` | `Path` | Root data directory | `{PROJECT_DIR}/data` |
| `OUTPUT_DIR` | `Path` | Root output directory | `{PROJECT_DIR}/output` |

#### Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `MORTGAGE_DATA_PROJECT_DIR` | Override project root | Package location |
| `MORTGAGE_DATA_DIR` | Override data directory | `{PROJECT_DIR}/data` |
| `MORTGAGE_OUTPUT_DIR` | Override output directory | `{PROJECT_DIR}/output` |

#### Methods

##### `get_subpackage_data_dir(subpackage: str) -> Path`

Get data directory for a specific subpackage.

**Parameters:**
- `subpackage` (str): Name of the subpackage (e.g., 'hmda', 'fha', 'mbs')

**Returns:**
- `Path`: Path to the subpackage's data directory

**Example:**
```python
from mortgage_data_manager.core.config import MortgageDataConfig

hmda_dir = MortgageDataConfig.get_subpackage_data_dir('hmda')
print(hmda_dir)  # /path/to/data/hmda
```

##### `get_medallion_dir(subpackage: str, stage: Literal["raw", "bronze", "silver", "gold"]) -> Path`

Get medallion stage directory for a subpackage.

**Parameters:**
- `subpackage` (str): Name of the subpackage
- `stage` (Literal): Medallion stage ('raw', 'bronze', 'silver', or 'gold')

**Returns:**
- `Path`: Path to the medallion stage directory

**Example:**
```python
silver_dir = MortgageDataConfig.get_medallion_dir('hmda', 'silver')
print(silver_dir)  # /path/to/data/hmda/silver
```

##### `ensure_directories(subpackage: str | None = None) -> None`

Create necessary directories if they don't exist.

**Parameters:**
- `subpackage` (str | None): If provided, create medallion directories for this subpackage. If None, only create root directories.

**Example:**
```python
# Create HMDA medallion directories
MortgageDataConfig.ensure_directories('hmda')
# Creates: data/hmda/{raw,bronze,silver}

# Create only root directories
MortgageDataConfig.ensure_directories()
# Creates: data/, output/
```

##### `get_project_root() -> Path`

Get the project root directory.

**Returns:**
- `Path`: Path to the project root

##### `validate_paths() -> bool`

Validate that all configured paths exist or can be created.

**Returns:**
- `bool`: True if all paths are valid

**Raises:**
- `PermissionError`: If unable to create directories due to permissions

---

## Medallion Architecture (`core.medallion`)

Utilities for working with the medallion architecture (raw → bronze → silver → gold).

**Module:** `mortgage_data_manager.core.medallion`

### Functions

#### `should_process_file(output_path: Path, overwrite: bool = False) -> bool`

Check if a file should be processed based on existence and replace flag.

**Parameters:**
- `output_path` (Path): Path to the output file
- `overwrite` (bool): If True, always process. If False, skip existing files.

**Returns:**
- `bool`: True if the file should be processed

**Example:**
```python
from mortgage_data_manager.core.medallion import should_process_file
from pathlib import Path

output = Path("data/silver/file.parquet")
if should_process_file(output, overwrite=False):
    # Process data
    print("Processing...")
else:
    print("File exists, skipping...")
```

#### `write_hive_partitioned(lf: pl.LazyFrame, output_dir: Path, partition_cols: list[str], compression: str = "snappy", **kwargs) -> None`

Write LazyFrame as hive-partitioned parquet.

**Parameters:**
- `lf` (pl.LazyFrame): Polars LazyFrame to write
- `output_dir` (Path): Output directory for partitioned parquet files
- `partition_cols` (list[str]): List of column names to partition by
- `compression` (str): Compression algorithm ('snappy', 'gzip', 'zstd', etc.)
- `**kwargs`: Additional arguments passed to `sink_parquet`

**Example:**
```python
import polars as pl
from mortgage_data_manager.core.medallion import write_hive_partitioned
from pathlib import Path

df = pl.LazyFrame({
    "year": [2020, 2020, 2021],
    "month": [1, 2, 1],
    "value": [100, 200, 300]
})

write_hive_partitioned(
    df,
    Path("data/silver"),
    partition_cols=["year", "month"]
)
# Creates: data/silver/year=2020/month=1/*.parquet
#          data/silver/year=2020/month=2/*.parquet
#          data/silver/year=2021/month=1/*.parquet
```

#### `read_medallion_layer(layer_dir: Path, glob_pattern: str = "**/*.parquet", **kwargs) -> pl.LazyFrame`

Read all parquet files from a medallion layer.

**Parameters:**
- `layer_dir` (Path): Path to the medallion layer directory
- `glob_pattern` (str): Glob pattern for matching files
- `**kwargs`: Additional arguments passed to `scan_parquet`

**Returns:**
- `pl.LazyFrame`: LazyFrame with data from all matching files

**Example:**
```python
from mortgage_data_manager.core.medallion import read_medallion_layer
from pathlib import Path

# Read all data from silver layer
df = read_medallion_layer(Path("data/hmda/silver"))

# Read specific partition
df = read_medallion_layer(
    Path("data/hmda/silver"),
    glob_pattern="activity_year=2020/**/*.parquet"
)

# Collect and display
print(df.collect())
```

#### `get_partitioned_path(base_dir: Path, partitions: dict[str, str | int]) -> Path`

Get path with hive-style partitioning.

**Parameters:**
- `base_dir` (Path): Base directory
- `partitions` (dict): Dictionary of partition column names to values

**Returns:**
- `Path`: Path with hive-style partitioning

**Example:**
```python
from mortgage_data_manager.core.medallion import get_partitioned_path
from pathlib import Path

path = get_partitioned_path(
    Path("data/silver"),
    {"year": 2020, "month": 6}
)
print(path)  # data/silver/year=2020/month=6
```

#### `validate_medallion_layer(layer_dir: Path, expected_columns: list[str] | None = None, check_empty: bool = True) -> tuple[bool, str]`

Validate a medallion layer directory.

**Parameters:**
- `layer_dir` (Path): Path to the medallion layer directory
- `expected_columns` (list[str] | None): If provided, check that all files have these columns
- `check_empty` (bool): If True, validate that layer is not empty

**Returns:**
- `tuple[bool, str]`: (is_valid, message)

**Example:**
```python
from mortgage_data_manager.core.medallion import validate_medallion_layer
from pathlib import Path

is_valid, msg = validate_medallion_layer(
    Path("data/hmda/silver"),
    expected_columns=["lei", "loan_amount"],
    check_empty=True
)

if not is_valid:
    print(f"Validation failed: {msg}")
else:
    print("Layer is valid")
```

#### `count_records_in_layer(layer_dir: Path) -> int`

Count total records in a medallion layer.

**Parameters:**
- `layer_dir` (Path): Path to the medallion layer directory

**Returns:**
- `int`: Total number of records across all parquet files

**Example:**
```python
from mortgage_data_manager.core.medallion import count_records_in_layer
from pathlib import Path

count = count_records_in_layer(Path("data/hmda/silver"))
print(f"Total HMDA records: {count:,}")
```

#### `get_layer_file_stats(layer_dir: Path) -> dict[str, Any]`

Get statistics about files in a medallion layer.

**Parameters:**
- `layer_dir` (Path): Path to the medallion layer directory

**Returns:**
- `dict`: Dictionary with file statistics

**Return Dictionary Keys:**
- `num_files` (int): Number of parquet files
- `total_size_bytes` (int): Total size in bytes
- `total_size_mb` (float): Total size in megabytes
- `total_size_gb` (float): Total size in gigabytes

**Example:**
```python
from mortgage_data_manager.core.medallion import get_layer_file_stats
from pathlib import Path

stats = get_layer_file_stats(Path("data/hmda/silver"))
print(f"Files: {stats['num_files']}")
print(f"Size: {stats['total_size_gb']:.2f} GB")
```

---

## Logging (`core.logging`)

Standardized logging configuration for all subpackages.

**Module:** `mortgage_data_manager.core.logging`

### Types

#### `LogLevel`

Type alias for logging levels: `Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]`

### Functions

#### `configure_logging(level: LogLevel = "INFO", format_string: str | None = None, log_file: Path | None = None, include_timestamp: bool = True) -> None`

Configure logging for mortgage data manager.

**Parameters:**
- `level` (LogLevel): Logging level
- `format_string` (str | None): Custom format string. If None, uses default.
- `log_file` (Path | None): If provided, also log to this file
- `include_timestamp` (bool): If True, include timestamp in log messages

**Example:**
```python
from mortgage_data_manager.core.logging import configure_logging

# Basic configuration
configure_logging(level="INFO")

# Debug mode with file logging
from pathlib import Path
configure_logging(
    level="DEBUG",
    log_file=Path("logs/app.log")
)
```

#### `get_logger(name: str, level: LogLevel | None = None) -> logging.Logger`

Get logger for a module.

**Parameters:**
- `name` (str): Logger name (typically `__name__` of the module)
- `level` (LogLevel | None): If provided, set this logger's level

**Returns:**
- `logging.Logger`: Configured logger instance

**Example:**
```python
from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)
logger.info("Starting download process")
logger.warning("Missing some data for year 2020")
logger.error("Failed to process file", exc_info=True)
```

#### `@log_function_call`

Decorator to log function calls with arguments.

**Example:**
```python
from mortgage_data_manager.core.logging import log_function_call

@log_function_call
def process_data(year: int, month: int):
    return f"Processing {year}-{month}"

result = process_data(2020, 6)
# Logs: "Calling process_data with args=(2020, 6), kwargs={}"
# Logs: "process_data completed successfully"
```

#### `@log_execution_time`

Decorator to log function execution time.

**Example:**
```python
from mortgage_data_manager.core.logging import log_execution_time

@log_execution_time
def slow_operation():
    import time
    time.sleep(2)
    return "done"

result = slow_operation()
# Logs: "slow_operation completed in 2.00 seconds"
```

#### `setup_file_logging(logger: logging.Logger, log_file: Path, level: LogLevel = "DEBUG") -> logging.Handler`

Add file handler to an existing logger.

**Parameters:**
- `logger` (logging.Logger): Logger to add file handler to
- `log_file` (Path): Path to log file
- `level` (LogLevel): Logging level for file handler

**Returns:**
- `logging.Handler`: The created file handler

**Example:**
```python
from mortgage_data_manager.core.logging import get_logger, setup_file_logging
from pathlib import Path

logger = get_logger(__name__)
handler = setup_file_logging(
    logger,
    Path("logs/download.log"),
    level="DEBUG"
)
```

### Classes

#### `LogContext`

Context manager for temporary logging level changes.

**Constructor:**
```python
LogContext(logger: logging.Logger, level: LogLevel)
```

**Example:**
```python
from mortgage_data_manager.core.logging import get_logger, LogContext
import logging

logger = get_logger(__name__)
logger.setLevel(logging.INFO)

with LogContext(logger, "DEBUG"):
    logger.debug("This will be logged")
    # Temporary DEBUG level within context

logger.debug("This will NOT be logged (back to INFO)")
```

---

## I/O Operations (`core.io`)

File download and I/O operations shared across subpackages.

**Module:** `mortgage_data_manager.core.io`

### Functions

#### `download_file(url: str, output_path: Path, chunk_size: int = 8192, verify_ssl: bool = True) -> None`

Download a file from URL to local path with progress indication.

**Parameters:**
- `url` (str): URL to download from
- `output_path` (Path): Local path to save file
- `chunk_size` (int): Download chunk size in bytes
- `verify_ssl` (bool): Whether to verify SSL certificates

**Example:**
```python
from mortgage_data_manager.core.io import download_file
from pathlib import Path

download_file(
    "https://example.com/data.zip",
    Path("data/raw/data.zip")
)
```

#### `extract_zip(zip_path: Path, extract_dir: Path, remove_zip: bool = False) -> list[Path]`

Extract a zip file to directory.

**Parameters:**
- `zip_path` (Path): Path to zip file
- `extract_dir` (Path): Directory to extract to
- `remove_zip` (bool): If True, delete zip file after extraction

**Returns:**
- `list[Path]`: List of extracted file paths

**Example:**
```python
from mortgage_data_manager.core.io import extract_zip
from pathlib import Path

files = extract_zip(
    Path("data/raw/archive.zip"),
    Path("data/raw/extracted"),
    remove_zip=True
)
print(f"Extracted {len(files)} files")
```

#### `safe_read_csv(file_path: Path, encoding: str = "utf-8", errors: str = "replace") -> pl.LazyFrame`

Safely read CSV file with encoding error handling.

**Parameters:**
- `file_path` (Path): Path to CSV file
- `encoding` (str): File encoding
- `errors` (str): Error handling strategy ('replace', 'ignore', 'strict')

**Returns:**
- `pl.LazyFrame`: Polars LazyFrame

**Example:**
```python
from mortgage_data_manager.core.io import safe_read_csv
from pathlib import Path

df = safe_read_csv(
    Path("data/raw/file.csv"),
    encoding="utf-8",
    errors="replace"
)
```

---

## Complete Example

Here's a complete example showing how to use the core utilities together:

```python
from pathlib import Path
import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.core.medallion import (
    should_process_file,
    write_hive_partitioned,
    read_medallion_layer,
    validate_medallion_layer,
    count_records_in_layer,
)

# Configure logging
configure_logging(level="INFO")
logger = get_logger(__name__)

# Setup configuration
config = MortgageDataConfig()
config.ensure_directories('hmda')

# Define paths
raw_dir = config.get_medallion_dir('hmda', 'raw')
bronze_dir = config.get_medallion_dir('hmda', 'bronze')
silver_dir = config.get_medallion_dir('hmda', 'silver')

# Example: Process raw data to bronze
output_file = bronze_dir / "2020.parquet"
if should_process_file(output_file, overwrite=False):
    logger.info("Processing 2020 data...")

    # Read raw data
    df = pl.scan_csv(raw_dir / "2020.csv")

    # Write to bronze
    df.sink_parquet(output_file)
    logger.info(f"Wrote bronze data to {output_file}")

# Example: Process bronze to silver with partitioning
logger.info("Creating silver layer...")
df = pl.scan_parquet(bronze_dir / "*.parquet")

# Add processing logic here
df = df.with_columns([
    pl.col("date").str.strptime(pl.Date, "%Y-%m-%d"),
])

# Write with hive partitioning
write_hive_partitioned(
    df,
    silver_dir,
    partition_cols=["year", "month"],
    compression="snappy"
)

# Validate silver layer
is_valid, msg = validate_medallion_layer(
    silver_dir,
    expected_columns=["lei", "loan_amount", "year", "month"],
    check_empty=True
)

if is_valid:
    count = count_records_in_layer(silver_dir)
    logger.info(f"Silver layer validated: {count:,} records")
else:
    logger.error(f"Validation failed: {msg}")

# Read silver data for analysis
df = read_medallion_layer(
    silver_dir,
    glob_pattern="year=2020/**/*.parquet"
)

print(df.collect())
```

---

## Best Practices

1. **Configuration**: Always inherit from `MortgageDataConfig` for subpackage configs
2. **Logging**: Use `get_logger(__name__)` for module-level logging
3. **Medallion**: Use consistent layer structure (raw → bronze → silver → gold)
4. **Partitioning**: Partition by time dimensions for efficient querying
5. **Validation**: Validate medallion layers after processing
6. **Error Handling**: Use try-except blocks with proper logging

## See Also

- [Configuration Guide](../configuration.md) - Detailed configuration options
- [Architecture Documentation](../architecture.md) - Design patterns and decisions
