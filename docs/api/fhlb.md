# FHLB API Reference

API reference for the FHLB (Federal Home Loan Bank) subpackage.

## Table of Contents

- [Overview](#overview)
- [Configuration](#configuration)
- [Download](#download)
- [Import](#import)
- [CLI](#cli)

---

## Overview

The FHLB subpackage provides tools for downloading and processing Federal Home Loan Bank data, including:

- **Member Institution Data**: Excel files with FHLB member information
- **AMA (Acquired Member Assets)**: Loan-level data from member acquisitions

### Data Types

| Data Type | Description | Format |
|-----------|-------------|--------|
| Members | FHLB member institution data | Excel (XLS/XLSX) |
| AMA | Acquired Member Assets loan data | CSV |

---

## Configuration

### `FHLBConfig`

Configuration class for FHLB data management.

**Module:** `mortgage_data_manager.fhlb.config`

**Inherits from:** `MortgageDataConfig`

#### Class Attributes

| Attribute | Type | Description | Environment Variable |
|-----------|------|-------------|---------------------|
| `FHLB_DATA_DIR` | `Path` | Root directory for FHLB data | `FHLB_DATA_DIR` |
| `FHLB_RAW_DIR` | `Path` | Raw data directory | `FHLB_RAW_DIR` |
| `FHLB_RAW_MEMBERS_DIR` | `Path` | Members raw subdirectory | - |
| `FHLB_RAW_AMA_DIR` | `Path` | AMA raw subdirectory | - |
| `FHLB_BRONZE_DIR` | `Path` | Bronze layer directory | `FHLB_BRONZE_DIR` |
| `FHLB_SILVER_DIR` | `Path` | Silver layer directory | `FHLB_SILVER_DIR` |
| `FHLB_GOLD_DIR` | `Path` | Gold layer directory | `FHLB_GOLD_DIR` |
| `FHLB_DEFAULT_MIN_YEAR` | `int` | Default start year for members | - |
| `FHLB_DEFAULT_MAX_YEAR` | `int` | Default end year for members | - |
| `FHLB_DEFAULT_AMA_MIN_YEAR` | `int` | Default start year for AMA | - |
| `FHLB_DEFAULT_AMA_MAX_YEAR` | `int` | Default end year for AMA | - |

#### Example

```python
from mortgage_data_manager.fhlb.config import FHLBConfig

# Access directories
print(FHLBConfig.FHLB_DATA_DIR)          # /data/fhlb
print(FHLBConfig.FHLB_RAW_MEMBERS_DIR)   # /data/fhlb/raw/members
print(FHLBConfig.FHLB_RAW_AMA_DIR)       # /data/fhlb/raw/ama

# Check year defaults
print(FHLBConfig.FHLB_DEFAULT_MIN_YEAR)  # 2009
print(FHLBConfig.FHLB_DEFAULT_MAX_YEAR)  # 2023

# Ensure directories exist
FHLBConfig.ensure_directories()
```

---

## Download

**Module:** `mortgage_data_manager.fhlb.download`

### Download AMA Data

Download Acquired Member Assets loan-level CSV files from the FHFA public use database.

**Function:** `download_ama_data(output_dir, pause, overwrite)`

**Example:**
```python
from mortgage_data_manager.fhlb.download import download_ama_data
from mortgage_data_manager.fhlb.config import FHLBConfig

# Download AMA data
downloaded = download_ama_data(
    output_dir=FHLBConfig.FHLB_RAW_AMA_DIR,
    pause=5,
    overwrite=False
)
print(f"Downloaded {len(downloaded)} files")
```

### Download Member Data

Download FHLB member institution Excel files from the FHFA membership data page.

**Function:** `download_members_data(output_dir, pause, overwrite)`

**File Pattern:** `FHLB_Members*{year}*.xls*`

**Example:**
```python
from mortgage_data_manager.fhlb.download import download_members_data
from mortgage_data_manager.fhlb.config import FHLBConfig

# Download member data
downloaded = download_members_data(
    output_dir=FHLBConfig.FHLB_RAW_MEMBERS_DIR,
    pause=5,
    overwrite=False
)
print(f"Downloaded {len(downloaded)} files")
```

### Download AMA Dictionaries

Download AMA schema dictionary files (PDF and Excel).

**Function:** `download_ama_dictionaries(output_dir, pause, overwrite)`

**Example:**
```python
from mortgage_data_manager.fhlb.download import download_ama_dictionaries
from mortgage_data_manager.fhlb.config import FHLBConfig

# Download dictionaries
downloaded = download_ama_dictionaries(
    output_dir=FHLBConfig.FHLB_DATA_DIR / "dictionaries",
    pause=5,
    overwrite=False
)
print(f"Downloaded {len(downloaded)} files")
```

---

## Import

### Import AMA to Bronze

Import AMA CSV files to bronze layer (Parquet format).

**Module:** `mortgage_data_manager.fhlb.import_bronze`

**Function:** `import_ama_bronze(years, raw_dir, output_dir, overwrite)`

**Example:**
```python
from mortgage_data_manager.fhlb.import_bronze import import_ama_bronze
from mortgage_data_manager.fhlb.config import FHLBConfig

# Import specific years to bronze
results = import_ama_bronze(
    years=[2020, 2021, 2022, 2023, 2024],
    raw_dir=FHLBConfig.FHLB_RAW_AMA_DIR,
    output_dir=FHLBConfig.FHLB_BRONZE_DIR / "ama",
    overwrite=False
)
print(f"Loaded: {results['loaded']}, Skipped: {results['skipped']}")
```

### Import Members to Bronze

Import FHLB member Excel files to bronze layer (Parquet format).

**Module:** `mortgage_data_manager.fhlb.import_bronze`

**Function:** `import_members_bronze(years, raw_dir, output_dir, overwrite)`

**Example:**
```python
from mortgage_data_manager.fhlb.import_bronze import import_members_bronze
from mortgage_data_manager.fhlb.config import FHLBConfig

# Import member data for specific years
results = import_members_bronze(
    years=list(range(2015, 2024)),
    raw_dir=FHLBConfig.FHLB_RAW_MEMBERS_DIR,
    output_dir=FHLBConfig.FHLB_BRONZE_DIR / "members",
    overwrite=False
)
print(f"Loaded: {results['loaded']}, Skipped: {results['skipped']}")
```

### Import AMA to Silver

Transform AMA bronze data to silver layer with data quality improvements.

**Module:** `mortgage_data_manager.fhlb.import_silver`

**Function:** `import_ama_silver(years, bronze_dir, output_dir, overwrite)`

**Transformations Applied:**
- Normalize percentage values across schema versions
- Fix income amount units
- Standardize indicator variable encoding
- Clean census tract identifiers
- Replace sentinel missing values with nulls

**Example:**
```python
from mortgage_data_manager.fhlb.import_silver import import_ama_silver
from mortgage_data_manager.fhlb.config import FHLBConfig

# Transform bronze to silver
results = import_ama_silver(
    years=[2020, 2021, 2022, 2023, 2024],
    bronze_dir=FHLBConfig.FHLB_BRONZE_DIR / "ama",
    output_dir=FHLBConfig.FHLB_SILVER_DIR / "ama",
    overwrite=False
)
print(f"Transformed: {results['loaded']}, Skipped: {results['skipped']}")
```

---

## CLI

### Commands

**Module:** `mortgage_data_manager.fhlb.cli.main`

#### `mortgage-data fhlb download`

Download FHLB data files. Supports multiple subcommands for different data types.

```bash
# Download AMA data files
mortgage-data fhlb download data

# Download member institution data
mortgage-data fhlb download members

# Download AMA schema dictionaries
mortgage-data fhlb download dictionaries

# Download all data types
mortgage-data fhlb download all
```

**Subcommands:**

| Command | Description | Output Format |
|---------|-------------|---------------|
| `data` | Download AMA loan-level CSV files | CSV |
| `members` | Download member institution Excel files | XLS/XLSX |
| `dictionaries` | Download AMA schema dictionary files | PDF, Excel |
| `all` | Download all data types | Mixed |

**Common Options:**
- `--output, -o PATH`: Output directory for downloaded files
- `--overwrite`: Overwrite existing files
- `--pause SECONDS`: Seconds to pause between downloads (default: `DOWNLOAD_PAUSE`, 5.0)

**Examples:**
```bash
# Download AMA data to custom directory
mortgage-data fhlb download data -o /custom/path

# Download with overwrite
mortgage-data fhlb download data --overwrite

# Download all with custom pause
mortgage-data fhlb download all --pause 10
```

#### `mortgage-data fhlb bronze`

Import data to bronze layer (convert raw files to Parquet).

```bash
# Import AMA data to bronze
mortgage-data fhlb bronze ama

# Import member data to bronze
mortgage-data fhlb bronze members
```

**Subcommands:**

| Command | Description | Input Format | Output Format |
|---------|-------------|--------------|---------------|
| `ama` | Import AMA loan data | CSV | Parquet |
| `members` | Import member institution data | Excel | Parquet |

**Common Options:**
- `--min-year INTEGER`: Minimum year to process
- `--max-year INTEGER`: Maximum year to process
- `--raw-dir PATH`: Input raw data directory
- `--output, -o PATH`: Output directory for bronze files
- `--overwrite`: Overwrite existing files

**Examples:**
```bash
# Import all available AMA years
mortgage-data fhlb bronze ama

# Import specific year range
mortgage-data fhlb bronze ama --min-year 2020 --max-year 2024

# Import members with overwrite
mortgage-data fhlb bronze members --overwrite
```

#### `mortgage-data fhlb silver`

Transform data to silver layer (apply data quality transformations).

```bash
# Transform AMA data to silver
mortgage-data fhlb silver ama
```

**Subcommands:**

| Command | Description |
|---------|-------------|
| `ama` | Transform AMA data with quality fixes |

**Transformations Applied:**
- Normalize percentage values across schema versions
- Fix income amount units
- Standardize indicator variable encoding
- Clean census tract identifiers
- Replace sentinel missing values with nulls

**Options:**
- `--min-year INTEGER`: Minimum year to process
- `--max-year INTEGER`: Maximum year to process
- `--bronze-dir PATH`: Input bronze data directory
- `--output, -o PATH`: Output directory for silver files
- `--overwrite`: Overwrite existing files

**Examples:**
```bash
# Transform all available years
mortgage-data fhlb silver ama

# Transform specific year range
mortgage-data fhlb silver ama --min-year 2020 --max-year 2024
```

#### `mortgage-data fhlb pipeline`

Run complete processing pipelines (download → bronze → silver).

```bash
# Run complete AMA pipeline
mortgage-data fhlb pipeline ama
```

**Subcommands:**

| Command | Description |
|---------|-------------|
| `ama` | Full AMA pipeline (download → bronze → silver) |

**Options:**
- `--min-year INTEGER`: Minimum year to process
- `--max-year INTEGER`: Maximum year to process
- `--skip-download`: Skip download step (use existing raw files)
- `--skip-bronze`: Skip bronze loading step
- `--skip-silver`: Skip silver transformation step
- `--overwrite`: Overwrite existing files
- `--pause SECONDS`: Seconds to pause between downloads (default: `DOWNLOAD_PAUSE`, 5.0)

**Examples:**
```bash
# Run complete pipeline
mortgage-data fhlb pipeline ama

# Run for specific years
mortgage-data fhlb pipeline ama --min-year 2020 --max-year 2024

# Skip download, process existing files
mortgage-data fhlb pipeline ama --skip-download

# Only run bronze step
mortgage-data fhlb pipeline ama --skip-download --skip-silver
```

#### `mortgage-data fhlb info`

Display FHLB configuration information.

```bash
mortgage-data fhlb info
```

**Output includes:**
- Data directories (raw/bronze/silver/gold)
- Raw subdirectories (members/ama)
- Default year ranges for member and AMA data

#### `mortgage-data fhlb --version`

Display version information.

```bash
mortgage-data fhlb --version
```

---

## Data Structure

### Raw Layer

**Members:**
- **Location**: `{FHLB_RAW_MEMBERS_DIR}/`
- **Format**: Excel files (XLS/XLSX)
- **Pattern**: `FHLB_Members*{year}*.xls*`

**AMA:**
- **Location**: `{FHLB_RAW_AMA_DIR}/`
- **Format**: CSV files

### Bronze Layer

**Members:**
- **Format**: Parquet files (one per quarter)
- **Location**: `{FHLB_BRONZE_DIR}/members/`

**AMA:**
- **Format**: Parquet files (one per year)
- **Location**: `{FHLB_BRONZE_DIR}/ama/`

### Silver Layer

**AMA:**
- **Format**: Cleaned and standardized Parquet files (one per year)
- **Location**: `{FHLB_SILVER_DIR}/ama/`
- **Transformations**: Percentage normalization, income unit fixes, indicator standardization, census tract cleaning, null handling

---

## Best Practices

1. **Year Range**: Use appropriate year ranges for your analysis needs
2. **Member Data**: Data available from 2009 onwards
3. **AMA Data**: Loan-level data available from 2009-2024
4. **File Matching**: Member files may have varying naming conventions

---

## See Also

- [Core API](core.md) - Shared utilities and medallion architecture
- [Configuration Guide](../configuration.md) - Environment variables and paths
- [FHLB Data](https://www.fhlbanks.com/) - Federal Home Loan Banks
