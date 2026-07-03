# FHLMC API Reference

API reference for the FHLMC (Freddie Mac - Federal Home Loan Mortgage Corporation) subpackage.

## Table of Contents

- [Configuration](#configuration)
- [Schema](#schema)
- [Import (Bronze Layer)](#import-bronze-layer)
- [CLI](#cli)

---

## Configuration

### `FHLMCConfig`

Configuration class for FHLMC data management.

**Module:** `mortgage_data_manager.fhlmc.config`

**Inherits from:** `MortgageDataConfig`

#### Class Attributes

| Attribute | Type | Description | Environment Variable |
|-----------|------|-------------|---------------------|
| `FHLMC_DATA_DIR` | `Path` | Root directory for FHLMC data | `FHLMC_DATA_DIR` |
| `FHLMC_RAW_DIR` | `Path` | Raw data directory | `FHLMC_RAW_DIR` |
| `FHLMC_BRONZE_DIR` | `Path` | Bronze layer directory | `FHLMC_BRONZE_DIR` |
| `FHLMC_SILVER_DIR` | `Path` | Silver layer directory | `FHLMC_SILVER_DIR` |
| `FHLMC_GOLD_DIR` | `Path` | Gold layer directory | `FHLMC_GOLD_DIR` |
| `FHLMC_BRONZE_ORIGINATION` | `Path` | Bronze origination subdirectory | - |
| `FHLMC_BRONZE_PERFORMANCE` | `Path` | Bronze performance subdirectory | - |
| `FHLMC_BRONZE_REPERFORMING` | `Path` | Bronze RPL subdirectory | - |
| `FHLMC_SCHEMA_FILE` | `Path` | Schema Excel file in references | - |

#### Data Type Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `VALID_DATA_TYPES` | `['origination', 'performance', 'reperforming']` | Valid data types |
| `MIN_YEAR` | `2019` | Minimum supported year |
| `MAX_YEAR` | `2025` | Maximum supported year |

#### Parquet Settings

| Setting | Value | Description |
|---------|-------|-------------|
| `PARQUET_COMPRESSION` | `snappy` | Compression algorithm |
| `PARQUET_ROW_GROUP_SIZE` | `100000` | Rows per row group |
| `PARQUET_STATISTICS` | `True` | Include statistics |

#### Methods

##### `get_prefix_dir(stage: Literal["raw", "bronze", "silver", "gold"], prefix: str) -> Path`

Get the prefix sub-directory under an FHLMC medallion stage.

**Parameters:**
- `stage`: Medallion stage ('raw', 'bronze', 'silver', or 'gold')
- `prefix`: Data prefix naming the sub-directory (e.g., 'origination')

**Returns:**
- `Path`: Path to the prefix sub-directory under the stage

**Example:**
```python
from mortgage_data_manager.fhlmc.config import FHLMCConfig

bronze_orig = FHLMCConfig.get_prefix_dir('bronze', 'origination')
print(bronze_orig)  # /path/to/data/fhlmc/bronze/origination
```

For the bare stage directory (no prefix), use the per-dir attributes
(`FHLMC_BRONZE_DIR`, etc.) or the inherited base API
`get_medallion_dir('fhlmc', 'bronze')`.

##### `ensure_directories() -> None`

Create necessary directories for FHLMC data processing.

**Example:**
```python
from mortgage_data_manager.fhlmc.config import FHLMCConfig

FHLMCConfig.ensure_directories()
# Creates: data/fhlmc/raw, data/fhlmc/bronze/origination, etc.
```

---

## Schema

### Schema Module

**Module:** `mortgage_data_manager.fhlmc.schema`

Parse and manage FHLMC data schemas from the Excel file layout.

The schema module provides:
- Column name definitions
- Data type mappings
- Date format specifications
- Column type conversions

---

## Import (Bronze Layer)

### Bronze Import Functions

**Module:** `mortgage_data_manager.fhlmc.import_bronze`

Import FHLMC historical loan data to bronze layer (Parquet format).

#### `import_all_historical_data(schemas: dict[str, dict], raw_dir: Path | None = None, years: Iterable[int] | None = None, overwrite: bool = False) -> None`

Recursively import every pipe-delimited `.txt` file found in raw zip archives.

Walks each `*.zip` in `raw_dir` (excluding `rpl_historical_data.zip`, which has
its own importer + schema) and descends into any nested zips, regardless of
layering. At the leaves, every `.txt` is imported — origination vs performance
is detected from the `_time_` substring in the filename. This handles standalone
yearly zips, the bundled `full_set_standard_historical_data.zip`, or any other
layered `.zip` of the same shape with no source-specific code.

**Parameters:**
- `schemas`: Dict of schemas for origination and performance data
- `raw_dir`: Directory to scan (default: `FHLMCConfig.FHLMC_RAW_DIR`)
- `years`: If provided, filter to `.txt` files whose names contain one of these years (useful for selectively pulling vintages out of the full-set zip)
- `overwrite`: If False, skip `.txt` files whose bronze parquet already exists; if True, force a rebuild

**Example:**
```python
from mortgage_data_manager.fhlmc.import_bronze import import_all_historical_data

# Load schemas
schemas = {
    'origination': origination_schema,
    'performance': performance_schema,
}

# Import only 2020-2024 from whatever zips are in raw/
import_all_historical_data(schemas, years=range(2020, 2025))
```

#### `import_rpl_data(schemas: dict[str, dict]) -> None`

Import RPL (reperforming) loan matching data.

**Parameters:**
- `schemas`: Dict of schemas including 'reperforming' schema

**Example:**
```python
from mortgage_data_manager.fhlmc.import_bronze import import_rpl_data

schemas = {'reperforming': rpl_schema}
import_rpl_data(schemas)
```

#### `apply_type_conversions(lf: pl.LazyFrame, schema: dict) -> pl.LazyFrame`

Apply type conversions to LazyFrame based on schema.

**Parameters:**
- `lf`: LazyFrame with string columns
- `schema`: Schema dict with type information

**Returns:**
- `pl.LazyFrame`: LazyFrame with proper data types

### File Structure

FHLMC historical data ships in layered zip archives. The importer descends
recursively, so any of these shapes are handled identically:

```
full_set_standard_historical_data.zip      # optional outer wrapper
└── historical_data_YYYY.zip                # yearly bundle
    └── historical_data_YYYYQN.zip          # quarterly bundle
        ├── historical_data_YYYYQN.txt      # origination (pipe-delimited)
        └── historical_data_time_YYYYQN.txt # performance ("_time_" marker)
```

RPL data is organized as:
```
rpl_historical_data.zip
├── nested_archive.zip
│   ├── data_file.csv             # Comma-delimited with headers
│   └── ...
└── ...
```

---

## CLI

### Commands

**Module:** `mortgage_data_manager.fhlmc.cli`

#### `mortgage-data fhlmc info`

Display FHLMC configuration information.

```bash
mortgage-data fhlmc info
```

#### `mortgage-data fhlmc download`

Download FHLMC data files.

```bash
# Show download instructions
mortgage-data fhlmc download

# Note: FHLMC requires manual registration
```

**Note:** FHLMC data requires manual registration at freddiemac.com. The CLI provides guidance on the download process.

#### `mortgage-data fhlmc schemas`

Inspect and validate FHLMC data schemas.

```bash
# Show schema information
mortgage-data fhlmc schemas info

# Validate schema file
mortgage-data fhlmc schemas validate
```

#### `mortgage-data fhlmc bronze`

Load data to bronze layer (raw to Parquet).

```bash
# Load historical data
mortgage-data fhlmc bronze load -t origination --min-year 2020 --max-year 2024
```

**Options:**
- `-t, --datasets`: Datasets to load (origination, performance, reperforming)
- `--min-year`: First year to process
- `--max-year`: Last year to process, inclusive
- `--overwrite`: Overwrite existing files

#### `mortgage-data fhlmc pipeline`

Run multi-step workflows (end-to-end automation).

```bash
# Run full pipeline
mortgage-data fhlmc pipeline full --min-year 2020 --max-year 2025

# Run bronze only
mortgage-data fhlmc pipeline bronze-only --min-year 2020 --max-year 2025
```

---

## Complete Example

Full pipeline from raw to bronze:

```python
import polars as pl
from mortgage_data_manager.fhlmc.config import FHLMCConfig
from mortgage_data_manager.fhlmc.import_bronze import (
    import_all_historical_data,
    import_rpl_data,
)
from mortgage_data_manager.fhlmc.schema import load_schemas
from mortgage_data_manager.core.logging import configure_logging, get_logger

# Setup logging
configure_logging(level="INFO")
logger = get_logger(__name__)

# Ensure directories exist
FHLMCConfig.ensure_directories()

# Load schemas from Excel file
logger.info("Loading schemas...")
schemas = load_schemas(FHLMCConfig.FHLMC_SCHEMA_FILE)

# Import historical data (origination + performance)
logger.info("Importing historical data...")
import_all_historical_data(
    schemas=schemas,
    years=range(2020, 2025),
)

# Import RPL data
logger.info("Importing RPL data...")
import_rpl_data(schemas=schemas)

logger.info("Import complete!")

# Verify data
origination_files = list(FHLMCConfig.FHLMC_BRONZE_ORIGINATION.glob("*.parquet"))
performance_files = list(FHLMCConfig.FHLMC_BRONZE_PERFORMANCE.glob("*.parquet"))
rpl_files = list(FHLMCConfig.FHLMC_BRONZE_REPERFORMING.glob("*.parquet"))

logger.info(f"Created {len(origination_files)} origination files")
logger.info(f"Created {len(performance_files)} performance files")
logger.info(f"Created {len(rpl_files)} RPL files")

# Read and analyze
df = pl.scan_parquet(FHLMCConfig.FHLMC_BRONZE_ORIGINATION / "*.parquet")
row_count = df.select(pl.len()).collect().item()
logger.info(f"Total origination records: {row_count:,}")
```

---

## Data Structure

### Directory Layout

```
{FHLMC_DATA_DIR}/
├── raw/
│   ├── historical_data_2020.zip
│   ├── historical_data_2021.zip
│   ├── ...
│   └── rpl_historical_data.zip
├── bronze/
│   ├── origination/           # Loan origination data
│   │   ├── origination_2020Q1.parquet
│   │   └── ...
│   ├── performance/           # Loan performance data
│   │   ├── time_2020Q1.parquet
│   │   └── ...
│   └── reperforming/          # RPL matching data
│       └── ...
├── silver/
│   └── ...
└── references/
    └── fhlmc/
        └── file_layout.xlsx   # Schema file
```

### Data Types

| Type | Description | Format |
|------|-------------|--------|
| `origination` | Loan origination data | Pipe-delimited, no header |
| `performance` | Monthly performance data | Pipe-delimited, no header |
| `reperforming` | RPL matching data | CSV with headers |

### Medallion Architecture

1. **Raw**: Downloaded ZIP files from Freddie Mac
2. **Bronze**: Converted to Parquet with proper schema
3. **Silver**: Cleaned, typed, and standardized (future)

---

## Best Practices

1. **Manual Download Required**: Register at freddiemac.com for data access
2. **Schema File Required**: Ensure `file_layout.xlsx` is in `references/fhlmc/`
3. **Process by Year**: Import one year at a time to manage memory
4. **Check Nested ZIPs**: FHLMC uses nested ZIP archives
5. **Verify Column Counts**: Schema should match data columns

## Data Format

### File Inventory

The `data/raw` directory contains yearly zip files totaling approximately 7.3 GB. These files contain historical mortgage loan data, organized by year and quarter.

| File Name | Size | Quarters Included | Structure |
|-----------|------|-------------------|-----------|
| historical_data_2019.zip | 950 MB | Q1-Q4 | Nested quarterly zips |
| historical_data_2020.zip | 2.6 GB | Q1-Q4 | Nested quarterly zips |
| historical_data_2021.zip | 2.6 GB | Q1-Q4 | Nested quarterly zips |
| historical_data_2022.zip | 773 MB | Q1-Q4 | Nested quarterly zips |
| historical_data_2023.zip | 282 MB | Q1-Q4 | Nested quarterly zips |
| historical_data_2024.zip | 153 MB | Q1-Q4 | Nested quarterly zips |
| historical_data_2025.zip | 19 MB | Q1-Q2 only | Nested quarterly zips |

### RPL (Reference Pool Loan) Data

| File Name | Size | Contents |
|-----------|------|----------|
| rpl_historical_data.zip | 2.4 MB | Contains 2 nested zips with loan ID matching data |

### Historical Data Quarterly Files

Each yearly zip contains nested quarterly zip files. Each quarterly zip contains **two text files**:

1. **`historical_data_YYYYQN.txt`** - Loan origination/static data
2. **`historical_data_time_YYYYQN.txt`** - Loan performance time-series data

**Format Characteristics:**
- Format: Pipe-delimited text files (`|` delimiter)
- Headers: **NO HEADERS** - Files contain data only
- Columns: Both file types contain **32 columns** each
- Column count is **consistent across all years** (2019-2024)

**Sample Data - historical_data_YYYYQN.txt:**
```
758|201905|N|203904|43580|000|1|P|55|14|248000|55|4.375|R|N|FRM|IA|SF|51100|F19Q10000001|N|240|02|Other sellers|Other servicers|||9||2|N|7
```

**Sample Data - historical_data_time_YYYYQN.txt:**
```
F19Q10000001|201904|248000.00|0|000|240|||||4.375|0.00||||||||||||||56||||||248000.00
F19Q10000001|201905|247000.00|0|001|239|||||4.375|0.00||||||||||||||54||||||247000.00
```

**Data Volume (Example from 2019Q1):**
- Loan origination file: 279,861 rows
- Time-series file: 9,302,063 rows (monthly observations per loan)

**Key Identifiers:**
- Loan identifiers follow pattern: `FYYQNNNNNNNN` (e.g., F19Q10000001, F24Q30000003)
- First character: F or A
- Next 2 digits: Year (19, 21, 24, etc.)
- Q followed by quarter number
- Followed by sequential ID

### RPL Historical Data Files

The `rpl_historical_data.zip` contains two nested zip files:

**Format:** Comma-delimited (CSV) with headers
**Columns:** 2 columns (`id_loan`, `loan_identifier`)

**Sample Data:**
```csv
id_loan,loan_identifier
F99Q10000325,2003SCRT00173
F99Q10001172,1902SCRT00010
F99Q10002380,2101SCRT00052
```

### File Type Summary

| File Type | Extension | Delimiter | Headers | Columns | Notes |
|-----------|-----------|-----------|---------|---------|-------|
| Historical Data | .txt | Pipe (`\|`) | No | 32 | Loan origination data |
| Historical Time Series | .txt | Pipe (`\|`) | No | 32 | Monthly performance data |
| RPL Match Files | .csv | Comma (`,`) | Yes | 2 | Loan ID cross-reference |
| Documentation | .pdf | N/A | N/A | N/A | FAQs and guides |

### Archive Structure

```
data/raw/
├── historical_data_YYYY.zip
│   └── historical_data_YYYYQN.zip (Q1, Q2, Q3, Q4)
│       ├── historical_data_YYYYQN.txt (32 cols, pipe-delimited, no headers)
│       └── historical_data_time_YYYYQN.txt (32 cols, pipe-delimited, no headers)
│
└── rpl_historical_data.zip
    ├── rpl_historical_data.zip
    │   ├── rpl_historical_data/SFLLD_RPL_ID_Match.csv (2 cols, comma-delimited, headers)
    │   └── rpl_historical_data/rpl_loan_id_match_faq.pdf
    └── rpl_historical_data_excl.zip
        ├── SFLLD_RPL_ID_Match_excl.csv (2 cols, comma-delimited, headers)
        └── rpl_loan_id_match_faq.pdf
```

### Processing Recommendations

1. **Data Loading**: Will need column name mapping for pipe-delimited files (no headers)
2. **Encoding**: Verify character encoding (likely UTF-8 or ASCII)
3. **Memory Management**: Large files (especially time-series) may require chunked processing
4. **Data Validation**: Verify 32-column structure is maintained during extraction
5. **RPL Integration**: RPL files use different format; will need separate parsing logic
6. **Year 2025**: Partial year data - handle appropriately in analysis

---

## See Also

- [Core API](core.md) - Shared utilities and medallion architecture
- [Configuration Guide](../configuration.md) - Environment variables and paths
- [FNMA API](fnma.md) - Similar GSE data processing
