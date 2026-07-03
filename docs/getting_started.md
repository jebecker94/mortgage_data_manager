# Getting Started with Mortgage Data Manager

This guide will help you get up and running with the Mortgage Data Manager package quickly.

## Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Understanding the Medallion Architecture](#understanding-the-medallion-architecture)
- [Configuration](#configuration)
- [Common Workflows](#common-workflows)
- [Next Steps](#next-steps)

---

## Installation

### Basic Installation

Install the core package using `uv` (recommended) or `pip`:

```bash
# Using uv (recommended)
uv pip install -e .

# Using pip
pip install -e .
```

This installs the core utilities and configuration management, but not the data-source-specific dependencies.

### Install with Specific Data Sources

Install only the data sources you need:

```bash
# HMDA data only
uv pip install -e ".[hmda]"

# FHA data only
uv pip install -e ".[fha]"

# MBS agencies (GNMA, FHFA, FNMA, FHLMC, UMBS)
uv pip install -e ".[mbs]"

# Matching workflows
uv pip install -e ".[matching]"

# Multiple sources
uv pip install -e ".[hmda,fha,mbs]"
```

### Install Everything (Development)

For development or to use all features:

```bash
# All data sources + development tools
uv pip install -e ".[all,dev]"
```

**Optional Dependency Groups:**
- `[hmda]` - HMDA-specific dependencies (selenium, webdriver-manager, scipy)
- `[fha]` - FHA-specific dependencies (fastexcel, openpyxl, addfips, networkx, matplotlib, plotly)
- `[mbs]` - MBS-specific dependencies (pymupdf, openpyxl, python-dateutil)
- `[matching]` - Matching workflow dependencies (addfips, numpy)
- `[hud_mf]` - HUD multifamily dependencies (fastexcel)
- `[analytics]` - Analytics dependencies (numpy, scikit-learn, statsmodels, xgboost, fredapi, seaborn, matplotlib)
- `[all]` - All of the above
- `[dev]` - Development tools (ruff, pytest, pytest-cov, ipython, jupyter, mypy)

---

## Data Directory Setup

The `data/` directory is **not included** in the repository — it's too large to track in git. Directories are created automatically when you run CLI commands or call `ensure_directories()` in Python.

### Default Setup

By default, data is stored in `data/` within the project root. No configuration needed — just start downloading:

```bash
mortgage-data hmda download --min-year 2024 --max-year 2024
# data/hmda/raw/ is created automatically
```

### Custom Data Location

To store data elsewhere (e.g., external drive, network share), set `MORTGAGE_DATA_DIR` in a `.env` file in the project root:

```bash
# .env
MORTGAGE_DATA_DIR=/Volumes/ExternalDrive/mortgage_data
```

Or use a symlink:

```bash
ln -s /Volumes/ExternalDrive/mortgage_data data
```

### Create Directories Manually

You can also pre-create the directory structure:

```python
from mortgage_data_manager.core.config import MortgageDataConfig

# Create all subpackage directories
MortgageDataConfig.ensure_directories()
```

See the [Configuration Guide](configuration.md) for more options.

---

## Quick Start

### HMDA: Download and Process Mortgage Disclosure Data

Download HMDA data for specific years:

```bash
# Download HMDA data for 2020-2024
mortgage-data hmda download --min-year 2020 --max-year 2024

# Check what was downloaded
ls data/hmda/raw/
```

Import to silver (analysis-ready) format:

```bash
# Process to bronze + silver (silver is partitioned by year and state)
mortgage-data hmda pipeline post2018 --min-year 2020 --max-year 2024
```

Use in Python:

```python
import polars as pl
from mortgage_data_manager.hmda.config import HMDAConfig

# Load silver data (lazy loading for efficiency)
silver_dir = HMDAConfig.HMDA_SILVER_DIR / "loans" / "post2018"
df = pl.scan_parquet(f"{silver_dir}/**/*.parquet")

# Filter for California mortgages in 2024
ca_2024 = df.filter(
    (pl.col("activity_year") == 2024) &
    (pl.col("state") == "CA")
).collect()

print(f"Found {len(ca_2024):,} mortgages in CA for 2024")
```

### FHA: Federal Housing Administration Data

Download and process FHA single-family data:

```bash
# Run complete pipeline (download + import)
mortgage-data fha pipeline single-family --min-year 2020

# Check the data
ls data/fha/silver/single_family/
```

Use in Python:

```python
import polars as pl
from mortgage_data_manager.fha.config import FHAConfig

# Load FHA silver data
silver_dir = FHAConfig.FHA_SILVER_DIR / "single_family"
df = pl.scan_parquet(f"{silver_dir}/**/*.parquet")

# Analyze lender activity
lender_stats = df.group_by("lender_id").agg([
    pl.count("case_number").alias("loan_count"),
    pl.sum("upfront_mip_amount").alias("total_mip")
]).collect()

print(lender_stats.head(10))
```

### GNMA: Ginnie Mae Mortgage-Backed Securities

Download and process GNMA monthly data:

```bash
# Download all monthly data files
mortgage-data gnma download all monthly

# Process schemas from PDFs
mortgage-data gnma schemas pipeline monthly

# Stage raw data to bronze, then transform to silver
mortgage-data gnma bronze monthly
mortgage-data gnma silver monthly

# Or run everything at once
mortgage-data gnma pipeline full monthly
```

Use in Python:

```python
from mortgage_data_manager.gnma.config import GNMAConfig
import polars as pl

# Load processed GNMA data
silver_dir = GNMAConfig.GNMA_SILVER_DIR / "monthly"
df = pl.scan_parquet(f"{silver_dir}/**/*.parquet")

# Analyze by loan type
print(df.group_by("loan_type").agg(
    pl.count().alias("pool_count")
).collect())
```

### FHLMC: Freddie Mac Data

Download and process Freddie Mac loan performance data:

```bash
# Download historical data
mortgage-data fhlmc download

# Load to bronze (Parquet conversion)
mortgage-data fhlmc bronze load -t origination --min-year 2020 --max-year 2024

# Or run complete pipeline
mortgage-data fhlmc pipeline full --min-year 2020 --max-year 2024
```

Use in Python:

```python
from mortgage_data_manager.fhlmc.config import FHLMCConfig
import polars as pl

# Load bronze data
bronze_dir = FHLMCConfig.FHLMC_BRONZE_DIR / "historical" / "origination"
df = pl.scan_parquet(f"{bronze_dir}/**/*.parquet")

print(df.head())
```

### Matching Workflows: Link Records Across Datasets

Match HMDA originations with their subsequent sales:

```bash
# Show available workflows
mortgage-data match info

# Run HMDA seller-purchaser matching
mortgage-data match hmda-sellers-purchasers --min-year 2020 --max-year 2024
```

Use in Python:

```python
from mortgage_data_manager.matching.config import MatchingConfig
import polars as pl

# Load matching results
match_dir = MatchingConfig.MATCHING_DATA_DIR / "hmda_sellers_purchasers"
matches = pl.scan_parquet(f"{match_dir}/**/*.parquet")

# Analyze GSE purchase patterns
gse_purchases = matches.filter(
    pl.col("purchaser_type").is_in([1, 2, 3, 4])  # Fannie, Freddie, Ginnie, Farmer Mac
).collect()

print(f"Found {len(gse_purchases):,} GSE purchases")
```

---

## Understanding the Medallion Architecture

All data sources follow a consistent 4-layer architecture:

```
data/{subpackage}/
├── raw/       # Original downloaded files (CSV, Excel, ZIP)
├── bronze/    # Parquet conversion with minimal processing
├── silver/    # Cleaned, partitioned, analysis-ready
└── gold/      # Aggregations (future phase)
```

### Data Flow

1. **Raw Layer**: Original files exactly as downloaded
   - CSVs, Excel files, ZIPs
   - Untouched source data
   - Located in `data/{subpackage}/raw/`

2. **Bronze Layer**: Parquet conversion
   - Minimal processing (type conversions)
   - One-to-one with raw files
   - Fast to query, smaller size
   - Located in `data/{subpackage}/bronze/`

3. **Silver Layer**: Analysis-ready
   - Cleaned and standardized
   - Hive-partitioned for efficient querying
   - Validated data types
   - Located in `data/{subpackage}/silver/`

4. **Gold Layer**: Aggregations (planned)
   - Pre-computed metrics
   - Dashboards and reports
   - Located in `data/{subpackage}/gold/`

### Why This Architecture?

- **Reproducibility**: Raw files preserved, transformations documented
- **Efficiency**: Query only what you need with partitioning
- **Flexibility**: Different processing stages for different needs
- **Performance**: Polars LazyFrames enable query optimization

---

## Configuration

### Environment Variables

Configure paths via `.env` file or environment variables:

```bash
# Global configuration
MORTGAGE_DATA_PROJECT_DIR=/path/to/project
MORTGAGE_DATA_DIR=/path/to/data
MORTGAGE_OUTPUT_DIR=/path/to/output

# Subpackage overrides (optional)
HMDA_DATA_DIR=/custom/hmda/path
FHA_RAW_DIR=/custom/fha/raw
GNMA_BRONZE_DIR=/custom/gnma/bronze
```

### Python Configuration

Access configuration in code:

```python
from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.hmda.config import HMDAConfig

# Base paths
print(f"Project: {MortgageDataConfig.PROJECT_DIR}")
print(f"Data: {MortgageDataConfig.DATA_DIR}")

# HMDA-specific paths
print(f"HMDA Raw: {HMDAConfig.HMDA_RAW_DIR}")
print(f"HMDA Silver: {HMDAConfig.HMDA_SILVER_DIR}")

# Get medallion directories programmatically
# Base layout (subpackage, stage):
bronze_dir = MortgageDataConfig.get_medallion_dir("hmda", "bronze")
# HMDA dataset/era layout (stage, dataset, period):
bronze_loans_dir = HMDAConfig.get_dataset_dir("bronze", "loans", "post2018")
```

### Directory Structure

Default directory structure (created automatically):

```
mortgage_data_manager/
├── data/
│   ├── hmda/
│   │   ├── raw/
│   │   ├── bronze/
│   │   └── silver/
│   ├── fha/
│   │   ├── raw/
│   │   ├── bronze/
│   │   └── silver/
│   ├── gnma/
│   └── matching/
└── output/
```

---

## Common Workflows

### Workflow 1: Analyze HMDA Lending Patterns

```python
import polars as pl
from mortgage_data_manager.hmda.config import HMDAConfig

# Load HMDA silver data
silver_dir = HMDAConfig.HMDA_SILVER_DIR / "loans" / "post2018"
df = pl.scan_parquet(f"{silver_dir}/**/*.parquet")

# Analyze denial rates by race/ethnicity
denial_analysis = df.filter(
    pl.col("action_taken").is_in([1, 3])  # Originated or denied
).group_by(["activity_year", "derived_race"]).agg([
    pl.count().alias("total_apps"),
    (pl.col("action_taken") == 3).sum().alias("denials"),
    ((pl.col("action_taken") == 3).sum() / pl.count() * 100).alias("denial_rate")
]).collect()

print(denial_analysis)
```

### Workflow 2: Track FHA Lender Market Share

```python
import polars as pl
from mortgage_data_manager.fha.config import FHAConfig

# Load FHA silver data
silver_dir = FHAConfig.FHA_SILVER_DIR / "single_family"
df = pl.scan_parquet(f"{silver_dir}/**/*.parquet")

# Calculate lender market share
market_share = df.group_by("lender_name").agg([
    pl.count("case_number").alias("endorsement_count"),
    pl.sum("original_principal_balance").alias("total_volume")
]).with_columns([
    (pl.col("total_volume") / pl.col("total_volume").sum() * 100).alias("market_share_pct")
]).sort("market_share_pct", descending=True).collect()

print(market_share.head(20))
```

### Workflow 3: Match HMDA Loans to Secondary Market Sales

```bash
# Run matching workflow
mortgage-data match hmda-sellers-purchasers --min-year 2020 --max-year 2024
```

```python
import polars as pl
from mortgage_data_manager.matching.config import MatchingConfig

# Load matches
match_dir = MatchingConfig.MATCHING_DATA_DIR / "hmda_sellers_purchasers"
matches = pl.scan_parquet(f"{match_dir}/MatchRound=1/**/*.parquet")

# Analyze time to sale
matches_with_sale_time = matches.with_columns([
    (pl.col("action_taken_date_p") - pl.col("action_taken_date_s")).alias("days_to_sale")
]).collect()

print(matches_with_sale_time.select(["days_to_sale"]).describe())
```

### Workflow 4: Combine Multiple Data Sources

```python
import polars as pl
from mortgage_data_manager.hmda.config import HMDAConfig
from mortgage_data_manager.fha.config import FHAConfig

# Load HMDA data
hmda = pl.scan_parquet(f"{HMDAConfig.HMDA_SILVER_DIR}/loans/post2018/**/*.parquet")

# Load FHA data
fha = pl.scan_parquet(f"{FHAConfig.FHA_SILVER_DIR}/single_family/**/*.parquet")

# Compare FHA-insured vs conventional loan characteristics
fha_hmda = hmda.filter(
    pl.col("loan_type") == 2  # FHA-insured
).select(["activity_year", "loan_amount", "interest_rate"]).collect()

print(f"FHA loans in HMDA: {len(fha_hmda):,}")
print(f"FHA endorsements: {fha.select(pl.count()).collect()[0,0]:,}")
```

---

## Next Steps

### Learn More

- **[CLI Reference](cli_reference.md)** - Complete command reference
- **[API Documentation](api/)** - Python API for each subpackage
- **[Architecture Guide](architecture.md)** - Detailed design decisions
- **[Configuration Guide](configuration.md)** - Advanced configuration options

### Get Help

```bash
# General help
mortgage-data --help

# Subcommand help
mortgage-data hmda --help
mortgage-data fha pipeline --help
mortgage-data gnma download --help

# Show project info
mortgage-data info
```

### Join the Community

- Report issues: [GitHub Issues](https://github.com/your-org/mortgage-data-manager/issues)
- Contribute: See [CONTRIBUTING.md](../CONTRIBUTING.md)
- Documentation: [docs/](.)

---

## Troubleshooting

### Common Issues

**Issue**: `ModuleNotFoundError: No module named 'pymupdf'`

**Solution**: Install the MBS dependencies:
```bash
uv pip install -e ".[mbs]"
```

**Issue**: `PermissionError` when downloading files

**Solution**: Check that you have write permissions to the data directory:
```bash
ls -la data/
# If needed, create the directory
mkdir -p data/
```

**Issue**: Out of memory when processing large datasets

**Solution**: Use Polars LazyFrames and process incrementally:
```python
# Don't collect() the entire dataset
df = pl.scan_parquet("data/**/*.parquet")

# Filter first, then collect
result = df.filter(pl.col("year") == 2024).collect()
```

---

Happy analyzing!
