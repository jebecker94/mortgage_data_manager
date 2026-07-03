# FHA API Reference

API reference for the FHA (Federal Housing Administration) subpackage.

## Table of Contents

- [Configuration](#configuration)
- [Download](#download)
- [Import](#import)
- [Analysis Tools](#analysis-tools)
- [CLI](#cli)

---

## Configuration

### `FHAConfig`

Configuration class for FHA data management.

**Module:** `mortgage_data_manager.fha.config`

**Inherits from:** `MortgageDataConfig`

#### Class Attributes

| Attribute | Type | Description | Environment Variable |
|-----------|------|-------------|---------------------|
| `FHA_DATA_DIR` | `Path` | Root directory for FHA data | `FHA_DATA_DIR` |
| `FHA_RAW_DIR` | `Path` | Raw data directory | - |
| `FHA_BRONZE_DIR` | `Path` | Bronze layer directory | - |
| `FHA_SILVER_DIR` | `Path` | Silver layer directory | - |

#### Example

```python
from mortgage_data_manager.fha.config import FHAConfig

config = FHAConfig()
print(config.FHA_DATA_DIR)  # /data/fha
print(config.FHA_SILVER_DIR)  # /data/fha/silver
```

---

## Download

### `download_single_family_snapshots(destination: Path | str = ..., *, pause: float | None = None, include_zip: bool = True, overwrite: bool = False, ...) -> list[DownloadResult]`

Download FHA Single Family snapshots.

**Module:** `mortgage_data_manager.fha.download`

**Parameters:**
- `destination` (Path | str): Output directory. Defaults to `FHA_RAW_DIR / "single_family"`
- `pause` (float | None): Seconds to pause between downloads. Defaults to `DOWNLOAD_PAUSE`
- `overwrite` (bool): If True, re-download existing files

**Example:**
```python
from mortgage_data_manager.fha.download import download_single_family_snapshots

# Download all single family snapshots
download_single_family_snapshots(pause=10)
```

### `download_hecm_snapshots(destination: Path | str = ..., *, pause: float | None = None, include_zip: bool = True, overwrite: bool = False, ...) -> list[DownloadResult]`

Download FHA HECM (Home Equity Conversion Mortgage / reverse mortgage) snapshots.

**Parameters:**
- `destination` (Path | str): Output directory. Defaults to `FHA_RAW_DIR / "hecm"`
- `pause` (float | None): Seconds to pause between downloads. Defaults to `DOWNLOAD_PAUSE`
- `overwrite` (bool): If True, re-download existing files

**Example:**
```python
from mortgage_data_manager.fha.download import download_hecm_snapshots

# Download all HECM snapshots
download_hecm_snapshots(pause=10)
```

### Lender List

Scrape HUD's roster of FHA-approved lenders (the [Lender List Search](https://www.hud.gov/hud-partners/single-family-lender-list)). Each record carries HUD's 10-digit `mort_id`; its first five digits are the **mortgagee ID** — the same identifier as `Originating Mortgagee Number` and `Sponsor Number` in the FHA snapshot data (which stores them as integers, dropping leading zeros — zero-pad with `.cast(pl.Utf8).str.zfill(5)` before joining).

**Module:** `mortgage_data_manager.fha.lender_list`

> **Note:** The roster is a point-in-time snapshot of *currently approved* lenders. Lenders that lost approval or merged will not appear, so older snapshot vintages cannot be reconstructed from it.

#### `download_lender_list(states=None, snapshot_date=None, *, pause=None, timeout=None, retries=None, overwrite=False) -> list[Path]`

Download raw HTML for each state/territory (54 requests total; every state fits in one page). Files are saved to `raw/lender_list/{YYYYMMDD}/lender_list_{ST}.html`.

#### `build_bronze_lender_list(snapshot_date=None, *, overwrite=False) -> Path`

Parse a raw snapshot into `bronze/lender_list/lender_list_{YYYYMMDD}.parquet`. Validates per-state record counts against each page's own "N lenders match" marker. Key columns: `lender_name`, address fields, `title_type`, `approval_date`, `areas_approved`, `mort_id`, `mortgagee_id` (5-digit, zero-padded), `branch_id`, `hecm`, `originates_203k`, `telephone`, `snapshot_date`. (`email` exists in the page layout but is empty site-wide.)

**Example:**
```python
import polars as pl
from mortgage_data_manager.fha.lender_list import (
    download_lender_list,
    build_bronze_lender_list,
)

download_lender_list(pause=1)
path = build_bronze_lender_list()

# Join FHA snapshot originators to the lender roster
roster = pl.scan_parquet(path)
snapshot = pl.scan_parquet("data/fha/silver/single_family/**/*.parquet")
joined = snapshot.with_columns(
    pl.col("Originating Mortgagee Number").cast(pl.Utf8).str.zfill(5).alias("mortgagee_id")
).join(roster.unique("mortgagee_id"), on="mortgagee_id", how="left")
```

**CLI:**
```bash
mortgage-data fha lender-list download            # all states, dated snapshot
mortgage-data fha lender-list download --states CA,TX
mortgage-data fha lender-list bronze              # parse latest snapshot
mortgage-data fha lender-list pipeline            # download + bronze
```

---

## Import

### `import_single_family(min_year: int, max_year: int, overwrite: bool = False) -> None`

Import Single Family data to bronze and silver layers.

**Module:** `mortgage_data_manager.fha.import_bronze` and `mortgage_data_manager.fha.import_silver`

**Parameters:**
- `min_year` (int): First year to import
- `max_year` (int): Last year to import
- `overwrite` (bool): If True, replace existing files

**Example:**
```python
from mortgage_data_manager.fha.import_bronze import import_single_family as import_sf_bronze
from mortgage_data_manager.fha.import_silver import import_single_family as import_sf_silver

# Import to bronze
import_sf_bronze(min_year=2015, max_year=2024)

# Create silver layer
import_sf_silver(min_year=2015, max_year=2024)
```

### `import_hecm(min_year: int, max_year: int, overwrite: bool = False) -> None`

Import HECM data to bronze and silver layers.

**Example:**
```python
from mortgage_data_manager.fha.import_bronze import import_hecm as import_hecm_bronze
from mortgage_data_manager.fha.import_silver import import_hecm as import_hecm_silver

# Import HECM data
import_hecm_bronze(min_year=2015, max_year=2024)
import_hecm_silver(min_year=2015, max_year=2024)
```

---

## Analysis Tools

### HHI (Herfindahl-Hirschman Index)

Calculate market concentration.

**Module:** `mortgage_data_manager.fha.analysis.hhi`

#### `calculate_hhi(df: pl.DataFrame | pl.LazyFrame, group_by: list[str], amount_col: str = "upb") -> pl.LazyFrame`

Calculate HHI by grouping variables.

**Parameters:**
- `df`: DataFrame with FHA data
- `group_by`: List of columns to group by (e.g., ['year', 'state'])
- `amount_col`: Column name for amounts (default: 'upb')

**Returns:**
- LazyFrame with HHI calculations

**Example:**
```python
import polars as pl
from mortgage_data_manager.fha.analysis.hhi import calculate_hhi
from mortgage_data_manager.fha.config import FHAConfig

config = FHAConfig()
df = pl.scan_parquet(config.FHA_SILVER_DIR / "single_family/**/*.parquet")

# Calculate HHI by year and state
hhi = calculate_hhi(
    df,
    group_by=["year", "state"],
    amount_col="upb"
)

print(hhi.collect())
```

### Network Analysis

Analyze lender networks and relationships.

**Module:** `mortgage_data_manager.fha.analysis.network`

#### `create_network_graph(df: pl.DataFrame, lender_col: str = "lender_id", servicer_col: str = "servicer_id") -> networkx.Graph`

Create network graph of lender-servicer relationships.

**Example:**
```python
import polars as pl
import networkx as nx
from mortgage_data_manager.fha.analysis.network import create_network_graph

df = pl.read_parquet("data/fha/silver/single_family/**/*.parquet")

G = create_network_graph(df)
print(f"Nodes: {G.number_of_nodes()}")
print(f"Edges: {G.number_of_edges()}")

# Find most central lenders
centrality = nx.degree_centrality(G)
top_lenders = sorted(centrality.items(), key=lambda x: x[1], reverse=True)[:10]
```

### Geographic Analysis

Analyze geographic distribution of FHA loans.

**Module:** `mortgage_data_manager.fha.analysis.geographic`

#### `analyze_geographic_distribution(df: pl.DataFrame, group_col: str = "state") -> pl.DataFrame`

Analyze loan distribution by geography.

**Example:**
```python
import polars as pl
from mortgage_data_manager.fha.analysis.geographic import analyze_geographic_distribution

df = pl.read_parquet("data/fha/silver/single_family/**/*.parquet")

# Analyze by state
state_dist = analyze_geographic_distribution(df, group_col="state")
print(state_dist)

# Analyze by county
county_dist = analyze_geographic_distribution(df, group_col="county_code")
print(county_dist)
```

---

## CLI

### Commands

**Module:** `mortgage_data_manager.fha.cli.main`

#### `mortgage-data fha download`

Download FHA snapshots.

```bash
# Download single family data
mortgage-data fha download single-family

# Download HECM data
mortgage-data fha download hecm --pause-length 10

# Download both
mortgage-data fha download both

# Replace existing files
mortgage-data fha download single-family --overwrite
```

**Arguments:**
- `loan_type`: Type to download ('single-family', 'hecm', or 'both')

**Options:**
- `--pause-length INT`: Seconds to pause between downloads (default: 5)
- `--overwrite`: Replace existing files

#### `mortgage-data fha bronze`

Convert FHA raw snapshots to the bronze layer.

```bash
# Bronze for single-family
mortgage-data fha bronze single-family

# Bronze for HECM, replacing existing files
mortgage-data fha bronze hecm --overwrite

# Both datasets
mortgage-data fha bronze both
```

**Arguments:**
- `dataset`: Dataset to convert ('single-family', 'hecm', or 'both')

**Options:**
- `--overwrite`: Replace existing bronze files

#### `mortgage-data fha silver`

Clean FHA bronze snapshots into the hive-partitioned silver layer.

```bash
# Silver for single-family across a year range
mortgage-data fha silver single-family --min-year 2015 --max-year 2024

# Silver for HECM, replacing existing files
mortgage-data fha silver hecm --min-year 2023 --max-year 2024 --overwrite
```

**Arguments:**
- `dataset`: Dataset to clean ('single-family', 'hecm', or 'both')

**Options:**
- `--min-year INT`: First year to process
- `--max-year INT`: Last year to process
- `--overwrite`: Replace existing silver files
- `--no-fips`: Skip FIPS code enrichment
- `--no-date`: Skip date column generation

#### `mortgage-data fha pipeline`

Run complete pipeline (download + import).

```bash
# Run full pipeline for single family
mortgage-data fha pipeline single-family --min-year 2020

# Run for both loan types
mortgage-data fha pipeline both --min-year 2020 --overwrite

# With custom pause length
mortgage-data fha pipeline single-family --min-year 2020 --pause-length 15
```

**Arguments:**
- `loan_type`: Type to process ('single-family', 'hecm', or 'both')

**Options:**
- `--min-year INT`: First year to import (required)
- `--max-year INT`: Last year to import (optional, defaults to current year)
- `--pause-length INT`: Download pause (default: 5)
- `--overwrite`: Overwrite existing files

---

## Complete Example

Full pipeline from download to analysis:

```python
import polars as pl
from mortgage_data_manager.fha.download import download_single_family_snapshots
from mortgage_data_manager.fha.import_bronze import import_single_family as import_sf_bronze
from mortgage_data_manager.fha.import_silver import import_single_family as import_sf_silver
from mortgage_data_manager.fha.analysis.hhi import calculate_hhi
from mortgage_data_manager.fha.config import FHAConfig
from mortgage_data_manager.core.logging import configure_logging, get_logger

# Setup
configure_logging(level="INFO")
logger = get_logger(__name__)
config = FHAConfig()

# Step 1: Download
logger.info("Downloading FHA single family data...")
download_single_family_snapshots(pause=10)

# Step 2: Import to bronze
logger.info("Importing to bronze...")
import_sf_bronze(min_year=2015, max_year=2024)

# Step 3: Create silver layer
logger.info("Creating silver layer...")
import_sf_silver(min_year=2015, max_year=2024)

# Step 4: Analysis
logger.info("Analyzing data...")
df = pl.scan_parquet(config.FHA_SILVER_DIR / "single_family/**/*.parquet")

# Calculate HHI by year and state
hhi = calculate_hhi(df, group_by=["year", "state"])
print("\nMarket Concentration (HHI):")
print(hhi.collect())

# Summary statistics
summary = (
    df
    .group_by("year")
    .agg([
        pl.count().alias("num_loans"),
        pl.col("upb").sum().alias("total_upb"),
        pl.col("upb").mean().alias("avg_upb"),
    ])
    .sort("year")
    .collect()
)
print("\nYearly Summary:")
print(summary)
```

---

## Data Structure

### Loan Types

1. **Single Family**: Traditional forward mortgages insured by FHA
2. **HECM**: Home Equity Conversion Mortgages (reverse mortgages)

### Bronze Layer

- Raw snapshot data in parquet format
- One file per snapshot date
- Location: `{FHA_BRONZE_DIR}/single_family/` or `{FHA_BRONZE_DIR}/hecm/`

### Silver Layer

- Cleaned and standardized data
- Partitioned by year
- Location: `{FHA_SILVER_DIR}/single_family/Year=YYYY/` or `{FHA_SILVER_DIR}/hecm/Year=YYYY/`

### Common Fields

Key fields in FHA Single Family data:

| Field | Type | Description |
|-------|------|-------------|
| `case_number` | str | FHA case number (unique loan ID) |
| `snapshot_date` | date | Date of snapshot |
| `year` | int | Year derived from snapshot date |
| `upb` | float | Unpaid principal balance |
| `loan_status` | str | Current loan status |
| `lender_id` | str | Originating lender ID |
| `servicer_id` | str | Current servicer ID |
| `state` | str | Property state |
| `county_code` | str | County FIPS code |
| `zip_code` | str | Property ZIP code |
| `original_loan_amount` | float | Original loan amount |
| `interest_rate` | float | Current interest rate |

---

## Best Practices

1. **Pause Between Downloads**: Use `pause` to avoid rate limiting
2. **Incremental Updates**: Process recent snapshots rather than reprocessing all data
3. **Memory Management**: Process one year at a time for large datasets
4. **Use Analysis Tools**: Leverage built-in HHI and network analysis functions
5. **Track Snapshots**: Monitor snapshot dates for data updates

## See Also

- [Core API](core.md) - Shared utilities and medallion architecture
- [Configuration Guide](../configuration.md) - Environment variables and paths

---

## Data Pipeline Guide

Complete workflow guide for managing FHA data from download to analysis.

### Pipeline Overview

The FHA Data Manager provides a complete pipeline for:
1. **Downloading** FHA data snapshots
2. **Importing** and cleaning the data
3. **Validating** data quality
4. **Analyzing** the processed data

### Quick Start

```bash
# Install dependencies
uv sync

# 1. Download data
python download_fha_data.py

# 2. Import and clean
python import_fha_data.py

# 3. Validate data quality
python -m fha_data_manager.validation.validators

# 4. Analyze
python -m fha_data_manager.analysis.exploratory
```

### Step-by-Step Guide

#### 1. Download FHA Data

The download script fetches Single Family and HECM snapshots from FHA's website:

```bash
python download_fha_data.py
```

Or use the package API:

```python
from mortgage_data_manager.fha import (
    download_single_family_snapshots,
    download_hecm_snapshots,
)

download_single_family_snapshots()
download_hecm_snapshots()
```

**Output**: Raw files saved to `data/raw/single_family/` and `data/raw/hecm/`

#### 2. Import and Clean Data

The import script processes raw files and saves them to a hive-structured database:

```bash
python import_fha_data.py
```

Or use the package API:

```python
from mortgage_data_manager.fha import (
    import_single_family_snapshots,
    import_hecm_snapshots,
)

import_single_family_snapshots()
import_hecm_snapshots()
```

**What it does**:
- Converts Excel/CSV files to Parquet format
- Standardizes column names and data types
- Adds FHA_Index unique identifier
- Handles data quality issues (e.g., Aug 2014 sponsor name bug)
- Saves to hive-partitioned structure

**Output**: Clean data saved to `data/database/single_family/` and `data/database/hecm/`

#### 3. Validate Data Quality

Run validation checks to ensure data integrity:

```bash
python -m fha_data_manager.validation.validators
```

Or programmatically:

```python
from examples.fha.validation import FHADataValidator

validator = FHADataValidator("data/database/single_family")
validator.load_data()
validator.run_all()
validator.print_summary()
```

**What it checks**:
- Schema compliance
- Data completeness
- ID-name consistency
- Relationship patterns
- Data ranges

#### 4. Analyze Data

##### Exploratory Analysis

Run exploratory analysis with visualizations:

```bash
python -m fha_data_manager.analysis.exploratory
```

Or:

```python
from examples.fha.analysis import (
    load_combined_data,
    analyze_lender_activity,
    analyze_sponsor_activity,
    analyze_loan_characteristics,
)

lf = load_combined_data("data/database/single_family")
lender_stats = analyze_lender_activity(lf)
sponsor_stats = analyze_sponsor_activity(lf)
loan_stats = analyze_loan_characteristics(lf.collect())
```

##### Institution Analysis

Analyze institution identities and mappings:

```bash
python -m fha_data_manager.analysis.institutions
```

Or:

```python
from examples.fha.analysis.institutions import InstitutionAnalyzer

analyzer = InstitutionAnalyzer("data/database/single_family")
analyzer.load_data()
analyzer.generate_full_report(output_dir="output")
```

### Hive-Partitioned Database

The processed data is stored in a hive-partitioned structure for efficient querying:

```
data/database/
├── single_family/
│   ├── Year=2010/
│   │   ├── Month=5/
│   │   │   └── data.parquet
│   │   ├── Month=6/
│   │   │   └── data.parquet
│   │   └── ...
│   ├── Year=2011/
│   └── ...
└── hecm/
    └── (similar structure)
```

### Loading Data

Load all data:
```python
import polars as pl
df = pl.scan_parquet("data/database/single_family")
```

Load specific year/month:
```python
df = pl.scan_parquet("data/database/single_family/Year=2025/Month=6")
```

Filter efficiently:
```python
df = (
    pl.scan_parquet("data/database/single_family")
    .filter(pl.col("Year") >= 2020)
    .filter(pl.col("Property State") == "CA")
    .collect()
)
```

### Pipeline Best Practices

#### 1. Incremental Updates

When new monthly data is released:
```bash
# Download only new snapshots
python download_fha_data.py

# Import only new files
python import_fha_data.py
```

The import script automatically detects and processes only new files.

#### 2. Data Validation

Always validate after importing:
```bash
python -m fha_data_manager.validation.validators --critical-only
```

Review any failures before proceeding with analysis.

#### 3. Track Data Inventory

Log your current data inventory:
```bash
python -m fha_data_manager.utils.inventory
```

This creates `data/data_inventory.csv` with metadata about all files.

#### 4. Memory Management

For large datasets, use lazy evaluation:
```python
import polars as pl

# Don't collect immediately
df = pl.scan_parquet("data/database/single_family")

# Filter before collecting
result = (
    df
    .filter(pl.col("Year") == 2025)
    .group_by("Property State")
    .agg(pl.col("FHA_Index").count())
    .collect()  # Only collect final result
)
```

### Troubleshooting

**Issue**: Download fails
- **Solution**: Check internet connection and FHA website availability

**Issue**: Import fails with encoding errors
- **Solution**: Ensure input files are properly formatted Excel/CSV files

**Issue**: Validation shows high % missing IDs
- **Solution**: This is normal for certain time periods; review historical context

**Issue**: Out of memory during analysis
- **Solution**: Use lazy evaluation with `scan_parquet` instead of `read_parquet`

---

## Data Validation

This section covers the consolidated validation and analysis scripts.

### Validation Scripts Overview

1. **`validations.py`** - Comprehensive data quality validation suite
   - Replaces: `check_common_ids.py`, `check_originator_ids.py`
   - Includes checks from institution mapping analysis

2. **`analyze_institutions.py`** - Institution identity and mapping analysis
   - Replaces: `analyze_institution_mappings.py`, `analyze_name_changes.py`, `analyze_name_oscillations.py`

### Running Validations

```bash
# Run all validation checks
python validations.py

# Run only critical checks (no warnings)
python validations.py --critical-only

# Export results to CSV
python validations.py --export output/validation_results.csv

# Specify data path
python validations.py --data-path data/database/single_family

# Run specific checks
python validations.py --checks check_missing_originator_ids check_fha_index_uniqueness
```

### Running Institution Analysis

```bash
# Run full comprehensive analysis
python analyze_institutions.py

# Specify output directory
python analyze_institutions.py --output-dir analysis_output

# Build crosswalk only (faster)
python analyze_institutions.py --crosswalk-only

# Specify data path
python analyze_institutions.py --data-path data/database/single_family
```

### Using as Python Modules

#### Validation Suite

```python
from validations import FHADataValidator

# Initialize and load data
validator = FHADataValidator("data/database/single_family")
validator.load_data()

# Run all validations
validator.run_all()
validator.print_summary()

# Run specific check
result = validator.check_missing_originator_ids(threshold_pct=3.0)
print(result)
print(result.details)

# Run only critical checks
validator.run_critical()
validator.print_summary()

# Export results
validator.export_results("output/validation_results.csv")
```

#### Institution Analysis

```python
from analyze_institutions import InstitutionAnalyzer

# Initialize and load data
analyzer = InstitutionAnalyzer("data/database/single_family")
analyzer.load_data()

# Build crosswalk
crosswalk = analyzer.build_institution_crosswalk()
print(crosswalk)

# Find mapping errors
errors = analyzer.find_mapping_errors()
print(f"Found {len(errors)} errors")

# Analyze name changes for specific IDs
name_changes = analyzer.analyze_name_changes_over_time(
    notable_ids=[71970, 75159]  # Quicken/Rocket, Freedom
)

# Detect oscillations
oscillations = analyzer.detect_oscillations()

# Generate full report
analyzer.generate_full_report(output_dir="output")
```

### Validation Checks

#### Critical Checks
- **Required Columns Present** - All necessary columns exist
- **FHA_Index Uniqueness** - No duplicate FHA_Index values
- **ID-Name Consistency Within Months** - IDs don't map to multiple names in same month

#### Warning Checks
- **Missing Originator IDs Below Threshold** - Completeness check
- **Missing Originator Names Below Threshold** - Completeness check
- **No Orphaned Sponsors** - Sponsors without originator IDs
- **Non-overlapping ID Spaces** - Originator and sponsor IDs don't overlap
- **Name Stability** - No oscillating name patterns
- **Consistent Originator-ID Mappings** - Originators don't have multiple IDs
- **Reasonable Mortgage Amounts** - No zero/negative or extremely high amounts

#### Informational Checks
- **Sponsor Coverage** - Percentage of loans with sponsors
- **Date Coverage** - Temporal span of the dataset

### Institution Analysis Outputs

When running the full institution analysis, the following files are generated:

1. **`institution_crosswalk.csv`**
   - Complete mapping of institution IDs to names
   - Columns: `institution_number`, `institution_name`, `type`, `first_date`, `last_date`, `num_months`
   - Includes both originators and sponsors

2. **`institution_mapping_errors.csv`**
   - Detected mapping inconsistencies
   - Columns: `institution_number`, `date`, `names`, `issue`
   - Issues include: multiple names in same month, oscillations

3. **`institution_analysis_report.txt`**
   - Comprehensive text report with:
     - ID space overlap analysis
     - Detailed name change timelines
     - Oscillation detection
     - Summary statistics

### Validation Tips

1. **Start with validations** - Run `validations.py` first to catch critical issues
2. **Use `--critical-only`** for quick checks during development
3. **Run institution analysis periodically** - It's more comprehensive and slower
4. **Keep exploratory analysis separate** - `analyze_fha_data.py` is for ad-hoc exploration and visualization
5. **Export validation results** - Use `--export` to track validation metrics over time

---

## Analysis Examples

Guide to performing exploratory data analysis and institutional analysis with FHA Data Manager.

### Analysis Overview

FHA Data Manager provides two main types of analysis:

1. **Exploratory Analysis** - Trends, volumes, and loan characteristics
2. **Institutional Analysis** - Lender/sponsor identities and relationships

### Exploratory Analysis

#### Loading Data

```python
from fha_data_manager.analysis import load_combined_data

lf = load_combined_data("data/database/single_family")
# Collect to a DataFrame only if you need to materialize the entire table
df = lf.collect()
```

#### Lender Activity Analysis

Analyze lender market activity and concentration:

```python
from fha_data_manager.analysis import analyze_lender_activity

lender_stats = analyze_lender_activity(lf)

# Top lenders by volume
print(lender_stats['lender_volume'].head(10))

# Yearly lender trends
print(lender_stats['yearly_lenders'])
```

**Outputs**:
- `lender_volume` - Top 20 lenders by loan count and total volume
- `yearly_lenders` - Active lenders and loan counts by year

#### Sponsor Activity Analysis

Analyze sponsor participation in FHA lending:

```python
from fha_data_manager.analysis import analyze_sponsor_activity

sponsor_stats = analyze_sponsor_activity(lf)

# Top sponsors
print(sponsor_stats['sponsor_volume'].head(10))

# Sponsorship trends
print(sponsor_stats['yearly_sponsors'])
```

#### Loan Characteristics

Analyze loan types, sizes, and distributions:

```python
from fha_data_manager.analysis import analyze_loan_characteristics

loan_stats = analyze_loan_characteristics(df)

# Loan purpose distribution
print(loan_stats['loan_purpose'])

# Down payment sources
print(loan_stats['down_payment'])

# Loan size trends
print(loan_stats['yearly_loan_size'])
```

#### Running Complete Analysis

Run all exploratory analyses with visualizations:

```bash
python -m fha_data_manager.analysis.exploratory
```

**Outputs**:
- Console summary statistics
- `output/active_lenders_trend.png` - Lender count over time
- `output/avg_loan_size_trend.png` - Average loan size over time
- `output/loan_purpose_dist.png` - Loan purpose distribution

### Institutional Analysis

#### Initialize Analyzer

```python
from fha_data_manager.analysis.institutions import InstitutionAnalyzer

analyzer = InstitutionAnalyzer("data/database/single_family")
analyzer.load_data()
```

#### Build Institution Crosswalk

Create a mapping of institution IDs to names over time:

```python
crosswalk = analyzer.build_institution_crosswalk()
print(crosswalk.head(20))
```

**Output columns**:
- `institution_number` - ID number
- `institution_name` - Institution name
- `type` - "Originator" or "Sponsor"
- `first_date` - First appearance
- `last_date` - Last appearance
- `num_months` - Number of months active

#### Find Mapping Errors

Identify potential data quality issues in ID-name mappings:

```python
errors = analyzer.find_mapping_errors()
print(f"Found {len(errors)} mapping errors")
print(errors)
```

**Error types**:
- Multiple names for same ID in one month
- Name oscillations (name changes back and forth)

#### Analyze Name Changes

Track how institution names change over time:

```python
# Analyze specific institutions (e.g., Quicken/Rocket, Freedom)
name_changes = analyzer.analyze_name_changes_over_time(
    notable_ids=[71970, 75159],
    log_file="output/name_changes.txt"
)

# Or analyze all
name_changes = analyzer.analyze_name_changes_over_time()
```

#### Detect Oscillations

Find institutions with inconsistent naming patterns:

```python
oscillations = analyzer.detect_oscillations(
    log_file="output/oscillations.txt"
)

print(f"Found {len(oscillations['originators'])} originator oscillations")
print(f"Found {len(oscillations['sponsors'])} sponsor oscillations")
```

#### Analyze ID Spaces

Check for overlaps between originator and sponsor ID spaces:

```python
id_stats = analyzer.analyze_id_spaces(
    log_file="output/id_spaces.txt"
)

print(f"Unique originator IDs: {id_stats['unique_originator_ids']:,}")
print(f"Unique sponsor IDs: {id_stats['unique_sponsor_ids']:,}")
print(f"Overlapping IDs: {id_stats['overlapping_ids']:,}")
```

#### Generate Comprehensive Report

Run all institutional analyses and generate a complete report:

```python
analyzer.generate_full_report(output_dir="output")
```

Or via command line:

```bash
python -m fha_data_manager.analysis.institutions
```

**Outputs**:
- `output/institution_crosswalk.csv` - Complete ID-name crosswalk
- `output/institution_mapping_errors.csv` - Detected errors
- `output/institution_analysis_report.txt` - Detailed analysis report

### Custom Analysis Examples

#### Example 1: Market Concentration

Calculate market concentration (HHI index):

```python
import polars as pl

df = pl.scan_parquet("data/database/single_family")

# Calculate market shares by year
market_shares = (
    df
    .group_by(["Year", "Originating Mortgagee"])
    .agg(pl.count().alias("loans"))
    .with_columns([
        (pl.col("loans") / pl.col("loans").sum().over("Year")).alias("share")
    ])
    .sort(["Year", "loans"], descending=[False, True])
    .collect()
)

# Calculate HHI by year
hhi = (
    market_shares
    .group_by("Year")
    .agg((pl.col("share") ** 2).sum().alias("HHI"))
)
print(hhi)
```

#### Example 2: Geographic Analysis

Analyze lending patterns by state:

```python
state_stats = (
    df
    .group_by("Property State")
    .agg([
        pl.count().alias("loan_count"),
        pl.col("Mortgage Amount").mean().alias("avg_loan_size"),
        pl.col("Interest Rate").mean().alias("avg_rate"),
        pl.col("Originating Mortgagee").n_unique().alias("unique_lenders"),
    ])
    .sort("loan_count", descending=True)
    .collect()
)
print(state_stats.head(20))
```

#### Example 3: Time Series Analysis

Analyze trends over time:

```python
import matplotlib.pyplot as plt

monthly_stats = (
    df
    .group_by(["Year", "Month"])
    .agg([
        pl.count().alias("loan_count"),
        pl.col("Mortgage Amount").mean().alias("avg_amount"),
        pl.col("Interest Rate").mean().alias("avg_rate"),
    ])
    .sort(["Year", "Month"])
    .collect()
)

# Create time series plot
plt.figure(figsize=(14, 8))

plt.subplot(3, 1, 1)
plt.plot(range(len(monthly_stats)), monthly_stats["loan_count"])
plt.ylabel("Loan Count")
plt.title("FHA Monthly Trends")

plt.subplot(3, 1, 2)
plt.plot(range(len(monthly_stats)), monthly_stats["avg_amount"])
plt.ylabel("Avg Loan Amount ($)")

plt.subplot(3, 1, 3)
plt.plot(range(len(monthly_stats)), monthly_stats["avg_rate"])
plt.ylabel("Avg Interest Rate (%)")
plt.xlabel("Month")

plt.tight_layout()
plt.savefig("output/monthly_trends.png")
```

### Performance Tips

#### 1. Use Lazy Evaluation

```python
# Good - lazy evaluation
df = pl.scan_parquet("data/database/single_family")
result = df.filter(...).group_by(...).collect()

# Avoid - loads everything into memory
df = pl.read_parquet("data/database/single_family")
```

#### 2. Filter Early

```python
# Good - filter before heavy operations
result = (
    df
    .filter(pl.col("Year") >= 2020)
    .filter(pl.col("Property State") == "CA")
    .group_by("Originating Mortgagee")
    .agg(pl.count())
    .collect()
)
```

#### 3. Use Hive Partitioning

```python
# Load specific partition
df = pl.scan_parquet("data/database/single_family/Year=2025/Month=6")
```

#### 4. Sample for Development

```python
# Use a sample for developing queries
sample = pl.read_parquet("data/database/single_family", n_rows=10000)
```

---

## Data Schemas

Complete schema definitions for FHA Single Family and HECM datasets.

### Single Family Schema

#### Required Columns

All Single Family data includes the following columns:

| Column Name | Data Type | Description | Notes |
|------------|-----------|-------------|-------|
| `Property State` | string | Two-letter state code | e.g., "CA", "TX" |
| `Property City` | string | City name | |
| `Property County` | string | County name | |
| `Property Zip` | int32 | ZIP code | 5-digit |
| `Originating Mortgagee` | string | Lender name | |
| `Originating Mortgagee Number` | int32 | Lender ID | May be null (~15% of records) |
| `Sponsor Name` | string | Sponsor name | Present for most loans |
| `Sponsor Number` | int32 | Sponsor ID | |
| `Down Payment Source` | string | Source of down payment | e.g., "Gift", "Savings" |
| `Non Profit Number` | int64 | Non-profit organization ID | Rarely populated |
| `Product Type` | string | FHA product type | e.g., "Standard", "Energy Efficient" |
| `Loan Purpose` | string | Loan purpose | "Purchase" or "Refinance" |
| `Property Type` | string | Property type | e.g., "Single Family", "Condo" |
| `Interest Rate` | float64 | Interest rate | Percentage (e.g., 3.5 = 3.5%) |
| `Mortgage Amount` | int64 | Original loan amount | US Dollars |
| `Year` | int16 | Endorsement year | |
| `Month` | int16 | Endorsement month | 1-12 |
| `FHA_Index` | string | Unique identifier | Format: YYYYMMDD_XXXXXXX |

#### Derived/Added Columns

When data is processed through the pipeline, these columns may be added:

| Column Name | Data Type | Description |
|------------|-----------|-------------|
| `Date` | date | Date constructed from Year/Month | Set to first of month |
| `FIPS` | string | County FIPS code | Added when county can be matched |

### HECM (Reverse Mortgage) Schema

#### Required Columns

| Column Name | Data Type | Description | Notes |
|------------|-----------|-------------|-------|
| `Property State` | string | Two-letter state code | |
| `Property City` | string | City name | |
| `Property County` | string | County name | |
| `Property Zip` | int32 | ZIP code | |
| `Originating Mortgagee` | string | Lender name | |
| `Originating Mortgagee Number` | int32 | Lender ID | |
| `Sponsor Name` | string | Sponsor name | |
| `Sponsor Number` | int32 | Sponsor ID | |
| `Sponsor Originator` | string | Sponsor originator name | |
| `NMLS` | int64 | NMLS number | |
| `Standard/Saver` | string | Product type | "Standard" or "Saver" |
| `Purchase/Refinance` | string | Loan purpose | |
| `Rate Type` | string | Interest rate type | Fixed or Variable |
| `Interest Rate` | float64 | Interest rate | Percentage |
| `Initial Principal Limit` | float64 | Initial principal limit | US Dollars |
| `Maximum Claim Amount` | float64 | Maximum claim amount | US Dollars |
| `Year` | int16 | Endorsement year | |
| `Month` | int16 | Endorsement month | |
| `Current Servicer ID` | int64 | Current servicer identifier | |
| `Previous Servicer ID` | int64 | Previous servicer identifier | |
| `FHA_Index` | string | Unique identifier | Format: YYYYMMDD_XXXXXXX |

### Data Quality Notes

#### Known Issues

##### 1. August 2014 Sponsor Name Bug

**Issue**: In the refinance data tab for August 2014, sponsor names are incorrectly set to the originating mortgagee name, while sponsor numbers remain correct.

**Handling**: The import pipeline automatically sets sponsor names to empty string for this month.

##### 2. Missing Originator IDs

**Issue**: Approximately 15.66% of loans have missing Originating Mortgagee Numbers.

**Pattern**: These loans typically have sponsor information.

**Status**: This appears to be a data reporting pattern rather than an error.

##### 3. Name Variations

**Issue**: Same institution ID may have multiple name spellings due to:
- Typos (e.g., "NORWICH COMMERICAL" vs "NORWICH COMMERCIAL")
- Extra spaces
- Legitimate name changes (e.g., "Quicken Loans" → "Rocket Mortgage")

**Handling**: Use institutional analysis tools to identify and track these variations.

### Schema Validation Rules

The validation suite checks for:

1. **Required Columns Present** - All expected columns exist
2. **FHA_Index Uniqueness** - No duplicate identifiers
3. **ID-Name Consistency** - Same ID doesn't map to multiple names in same month
4. **Data Ranges** - Mortgage amounts and interest rates within reasonable bounds
5. **Completeness** - Missing data below threshold levels

### FHA_Index Construction

The `FHA_Index` is a unique identifier constructed as follows:

```
YYYYMMDD_XXXXXXX
```

Where:
- `YYYYMM` = Year and month (e.g., 202506 for June 2025)
- `DD` = Always "01" (first of month)
- `XXXXXXX` = Row number in original file, zero-padded to 7 digits

Example: `20250601_0001234` = 1,234th row in June 2025 data

**Note**: Row numbers increment sequentially across all sheets in the original file (typically Purchase first, then Refinance).

### Data Types

#### Pandas vs Polars vs PyArrow

The schema is implemented with support for multiple dataframe libraries:

**Pandas/Polars**:
- Strings: `str` or `pl.Utf8`
- Integers: `Int32`, `Int64`, `Int16` (nullable integers)
- Floats: `float64`

**PyArrow**:
- Strings: `pa.string()`
- Integers: `pa.int32()`, `pa.int64()`, `pa.int16()`
- Floats: `pa.float64()`

### Categorical Values

#### Loan Purpose
- "Purchase"
- "Refinance"

#### Property Type
- "Single Family"
- "Condo"
- "Manufactured Home"
- Others (varies by time period)

#### Product Type (Single Family)
- "Standard"
- "Energy Efficient"
- Others (varies)

#### Down Payment Source
- "Gift"
- "Savings"
- "Sale of Property"
- "Cash on Hand"
- Others

#### Standard/Saver (HECM)
- "Standard"
- "Saver"

#### Rate Type (HECM)
- "Fixed"
- "Variable"

### Geographic Data

#### State Codes
Two-letter abbreviations following US Postal Service standards.

#### County Names
- Free text field
- May contain spelling variations
- Can be matched to FIPS codes using `addfips` package
- Some manual corrections applied for common misspellings

#### FIPS Codes
- 5-digit codes (2-digit state + 3-digit county)
- Added during processing when county can be matched
- ~1-2% of records may have missing FIPS due to unmatched counties

### Temporal Coverage

#### Single Family
- **Available**: May 2010 - Present
- **Update Frequency**: Monthly (typically released mid-month)
- **Completeness**: Near-complete coverage since 2010

#### HECM
- **Available**: July 2010 - Present (post-2011 recommended)
- **Update Frequency**: Monthly
- **Note**: Early years (2010-2011) have formatting inconsistencies

### Schema Usage Examples

#### Loading with Schema Validation

```python
from mtgdicts import FHADictionary
import polars as pl

# Get schema
fha_dict = FHADictionary()
schema = fha_dict.single_family.schema

# Load with schema enforcement
df = pl.read_parquet("data.parquet", schema=schema)
```

#### Type Casting

```python
# Cast to expected types
df = df.cast(fha_dict.single_family.data_types)
```

### Data References

- [FHA Single Family Data Portal](https://www.hud.gov/program_offices/housing/rmra/oe/rpts/sfsnap/sfsnap)
- [FHA HECM Data Portal](https://www.hud.gov/program_offices/housing/rmra/oe/rpts/hecm/hecmsfsnap)
