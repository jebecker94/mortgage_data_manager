# HMDA API Reference

API reference for the HMDA (Home Mortgage Disclosure Act) subpackage.

## Table of Contents

- [Configuration](#configuration)
- [Download](#download)
- [Import (Bronze Layer)](#import-bronze-layer)
- [Import (Silver Layer)](#import-silver-layer)
- [CLI](#cli)

---

## Configuration

### `HMDAConfig`

Configuration class for HMDA data management.

**Module:** `mortgage_data_manager.hmda.config`

**Inherits from:** `MortgageDataConfig`

#### Class Attributes

| Attribute | Type | Description | Environment Variable |
|-----------|------|-------------|---------------------|
| `HMDA_DATA_DIR` | `Path` | Root directory for HMDA data | `HMDA_DATA_DIR` |
| `HMDA_RAW_DIR` | `Path` | Raw data directory | - |
| `HMDA_BRONZE_DIR` | `Path` | Bronze layer directory | - |
| `HMDA_SILVER_DIR` | `Path` | Silver layer directory | - |

#### Example

```python
from mortgage_data_manager.hmda.config import HMDAConfig

config = HMDAConfig()
print(config.HMDA_DATA_DIR)  # /data/hmda
print(config.HMDA_SILVER_DIR)  # /data/hmda/silver
```

---

## Download

### `download_hmda(years: list[int], data_type: str = "lar", output_dir: Path | None = None, overwrite: bool = False) -> None`

Download HMDA data for specified years.

**Module:** `mortgage_data_manager.hmda.download`

**Parameters:**
- `years` (list[int]): List of years to download
- `data_type` (str): Type of data ('lar', 'panel', or 'ts')
- `output_dir` (Path | None): Output directory (defaults to HMDA_RAW_DIR)
- `overwrite` (bool): If True, re-download existing files

**Example:**
```python
from mortgage_data_manager.hmda.download import download_hmda

# Download LAR data for 2020-2024
download_hmda(
    years=list(range(2020, 2025)),
    data_type="lar",
    overwrite=False
)
```

### Supported Data Types

- **lar**: Loan/Application Register (main loan-level data)
- **panel**: Institution panel file (lender information)
- **ts**: Transmittal sheet (submission metadata)

---

## Import (Bronze Layer)

### `import_post2018(min_year: int, max_year: int, overwrite: bool = False) -> None`

Import post-2018 HMDA data to bronze layer.

**Module:** `mortgage_data_manager.hmda.import_bronze`

**Parameters:**
- `min_year` (int): First year to import
- `max_year` (int): Last year to import
- `overwrite` (bool): If True, replace existing bronze files

**Example:**
```python
from mortgage_data_manager.hmda.import_bronze import import_post2018

# Import 2020-2024 data to bronze
import_post2018(
    min_year=2020,
    max_year=2024,
    overwrite=False
)
```

### `import_2007_2017(min_year: int, max_year: int, overwrite: bool = False, drop_tract_vars: bool = False) -> None`

Import 2007-2017 HMDA data to bronze layer.

**Parameters:**
- `min_year` (int): First year to import (2007-2017)
- `max_year` (int): Last year to import (2007-2017)
- `overwrite` (bool): If True, replace existing bronze files
- `drop_tract_vars` (bool): If True, drop census tract variables to save space

**Example:**
```python
from mortgage_data_manager.hmda.import_bronze import import_2007_2017

# Import 2007-2017 data to bronze
import_2007_2017(
    min_year=2007,
    max_year=2017,
    drop_tract_vars=True  # Reduce file size
)
```

---

## Import (Silver Layer)

### `import_silver_post2018(min_year: int, max_year: int, overwrite: bool = False) -> None`

Import post-2018 HMDA data to silver layer with cleaning and standardization.

**Module:** `mortgage_data_manager.hmda.import_silver`

**Parameters:**
- `min_year` (int): First year to import
- `max_year` (int): Last year to import
- `overwrite` (bool): If True, replace existing silver files

**Example:**
```python
from mortgage_data_manager.hmda.import_silver import import_silver_post2018

# Create silver layer from bronze
import_silver_post2018(
    min_year=2020,
    max_year=2024,
    overwrite=False
)
```

### Silver Layer Features

The silver layer includes:
- Data type standardization
- Missing value handling
- Derived fields (e.g., DTI calculations)
- Hive partitioning by year
- Parquet compression

---

## CLI

### Commands

**Module:** `mortgage_data_manager.hmda.cli.main`

#### `mortgage-data hmda download`

Download HMDA data.

```bash
# Download LAR data for 2020-2024
mortgage-data hmda download --min-year 2020 --max-year 2024

# Include MLAR files
mortgage-data hmda download --min-year 2020 --max-year 2024 --include-mlar

# Replace existing files
mortgage-data hmda download --min-year 2023 --max-year 2024 --overwrite always
```

**Options:**
- `--min-year`: First year to download (required)
- `--max-year`: Last year to download, inclusive (required)
- `--include-mlar`: Include Modified LAR (MLAR) files
- `--include-historical`: Include historical 2007-2017 files
- `--overwrite`: Overwrite mode (skip, always, if_newer, if_size_diff)

#### `mortgage-data hmda bronze / silver / pipeline`

The former `hmda import` is split into three medallion-aligned commands:

- `bronze` — build only the raw → parquet bronze layer
- `silver` — build only the Hive-partitioned silver layer
- `pipeline` — bronze and silver in one step (same behavior as the old `import`)

All three take the same arguments and options.

```bash
# Bronze + silver in one shot, post-2018
mortgage-data hmda pipeline post2018 --min-year 2020 --max-year 2024

# Bronze only for 2007-2017
mortgage-data hmda bronze 2007-2017 --min-year 2007 --max-year 2017

# Silver only for pre-2007 loans
mortgage-data hmda silver pre2007 --datasets loans

# Replace existing files
mortgage-data hmda pipeline post2018 --min-year 2023 --max-year 2024 --overwrite
```

**Arguments:**
- `period`: Time period — `post2018`, `2007-2017`, or `pre2007`

**Options:**
- `--min-year` / `--max-year`: Year range (per-period defaults if omitted)
- `--datasets`: `loans`, `panel`, `transmittal_series` (post2018 / pre2007 only; can repeat)
- `--overwrite`: Replace existing files

#### `mortgage-data hmda info`

Show HMDA package information and data status.

```bash
mortgage-data hmda info
```

---

## Complete Example

Full pipeline from download to analysis:

```python
import polars as pl
from mortgage_data_manager.hmda.download import download_hmda
from mortgage_data_manager.hmda.import_bronze import import_post2018
from mortgage_data_manager.hmda.import_silver import import_silver_post2018
from mortgage_data_manager.hmda.config import HMDAConfig
from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.core.medallion import read_medallion_layer

# Setup logging
configure_logging(level="INFO")
logger = get_logger(__name__)

# Define years
years = list(range(2020, 2025))  # 2020-2024

# Step 1: Download raw data
logger.info("Downloading HMDA data...")
download_hmda(years=years, data_type="lar")

# Step 2: Import to bronze layer
logger.info("Importing to bronze layer...")
import_post2018(min_year=2020, max_year=2024)

# Step 3: Import to silver layer
logger.info("Creating silver layer...")
import_silver_post2018(min_year=2020, max_year=2024)

# Step 4: Read and analyze silver data
logger.info("Reading silver data...")
config = HMDAConfig()
df = read_medallion_layer(config.HMDA_SILVER_DIR)

# Example analysis: Loan volume by year
result = (
    df
    .group_by("activity_year")
    .agg([
        pl.count().alias("num_loans"),
        pl.col("loan_amount").sum().alias("total_loan_amount"),
        pl.col("loan_amount").mean().alias("avg_loan_amount"),
    ])
    .sort("activity_year")
    .collect()
)

print(result)
```

---

## Data Structure

### Bronze Layer

- Raw data in parquet format
- Minimal processing
- Partitioned by year
- Location: `{HMDA_BRONZE_DIR}/`

### Silver Layer

- Cleaned and standardized data
- Type conversions applied
- Derived fields added
- Hive-partitioned by activity_year
- Location: `{HMDA_SILVER_DIR}/activity_year=YYYY/`

### Common Fields (Post-2018)

Key fields in HMDA data:

| Field | Type | Description |
|-------|------|-------------|
| `lei` | str | Legal Entity Identifier |
| `activity_year` | int | Activity year |
| `loan_type` | int | Type of loan |
| `loan_purpose` | int | Purpose of loan |
| `loan_amount` | float | Loan amount |
| `interest_rate` | float | Interest rate |
| `occupancy_type` | int | Property occupancy type |
| `action_taken` | int | Action taken on application |
| `state_code` | str | State FIPS code |
| `county_code` | str | County FIPS code |
| `census_tract` | str | Census tract |

For complete field reference, see the [FFIEC HMDA documentation](https://ffiec.cfpb.gov/documentation/).

---

## Best Practices

1. **Download First**: Always download data before importing
2. **Check Space**: HMDA data is large; ensure sufficient disk space
3. **Use Partitions**: Query specific years using partition filters
4. **Incremental Processing**: Process one year at a time for large datasets
5. **Validate**: Check record counts after import

## See Also

- [Core API](core.md) - Shared utilities and medallion architecture
- [Configuration Guide](../configuration.md) - Environment variables and paths
