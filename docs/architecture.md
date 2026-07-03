# Architecture Documentation

This document describes the architectural patterns, design decisions, and principles used in the Mortgage Data Manager.

## Table of Contents

- [Overview](#overview)
- [Architectural Principles](#architectural-principles)
- [Project Structure](#project-structure)
- [Medallion Architecture](#medallion-architecture)
- [Configuration Management](#configuration-management)
- [CLI Design](#cli-design)
- [Data Processing Patterns](#data-processing-patterns)
- [Dependency Management](#dependency-management)
- [Design Decisions](#design-decisions)

---

## Overview

The Mortgage Data Manager is a unified data management system that consolidates six previously standalone packages into a single, cohesive framework. The architecture prioritizes:

1. **Modularity**: Subpackages are independent but share common utilities
2. **Consistency**: Standardized patterns across all data sources
3. **Scalability**: Efficient processing of large datasets
4. **Maintainability**: Clear separation of concerns and DRY principles
5. **Extensibility**: Easy to add new data sources

---

## Architectural Principles

### 1. Separation of Concerns

Each subpackage is responsible for a single data source:

```
mortgage_data_manager/
├── core/           # Shared utilities (configuration, I/O, logging)
├── hmda/           # HMDA-specific code
├── fha/            # FHA-specific code
├── gnma/           # GNMA (Ginnie Mae)
├── fhfa/           # FHFA
├── fnma/           # FNMA (Fannie Mae)
├── fhlmc/          # FHLMC (Freddie Mac)
├── umbs/           # UMBS
├── hud/            # HUD single-family programs
├── hud_mf/         # HUD multifamily programs
├── fhlb/           # FHLB (Acquired Member Assets)
├── analytics/      # Derived analytics
├── combined/       # Cross-source master datasets
└── matching/       # Matching workflows
```

### 2. Configuration Inheritance

All subpackage configurations inherit from `MortgageDataConfig`:

```python
# Base configuration
class MortgageDataConfig:
    PROJECT_DIR: Path
    DATA_DIR: Path
    OUTPUT_DIR: Path

# Subpackage configuration
class HMDAConfig(MortgageDataConfig):
    HMDA_DATA_DIR: Path
    HMDA_RAW_DIR: Path
    HMDA_BRONZE_DIR: Path
    HMDA_SILVER_DIR: Path
```

**Benefits:**
- Shared functionality (path resolution, directory creation)
- Consistent interface across subpackages
- Easy to add new subpackages

### 3. Medallion Architecture

All data follows the medallion pattern (raw → bronze → silver → gold):

```
data/
└── hmda/
    ├── raw/      # Downloaded data in original format
    ├── bronze/   # Converted to parquet, minimal processing
    ├── silver/   # Cleaned, standardized, partitioned
    └── gold/     # Aggregated, business-ready (future)
```

**Benefits:**
- Clear data lineage
- Incremental processing
- Easy to rebuild downstream layers
- Separation of concerns (acquisition vs. transformation)

### 4. Polars-First Approach

All data processing uses Polars LazyFrames:

```python
# Lazy evaluation for efficiency
df = pl.scan_parquet("data/raw/*.parquet")
df = df.filter(pl.col("year") >= 2020)
df = df.with_columns([pl.col("amount") * 1000])
df.sink_parquet("data/bronze/output.parquet")  # Execute
```

**Benefits:**
- Query optimization
- Memory efficiency
- Fast execution
- Consistent API across subpackages

### 5. CLI Hierarchy

Unified CLI with subcommand structure:

```bash
mortgage-data                    # Root command
├── info                        # Project information
├── version                     # Version info
├── hmda                        # HMDA subcommand
│   ├── download
│   ├── import
│   └── info
├── fha                         # FHA subcommand
│   ├── download
│   ├── import
│   └── pipeline
└── ...
```

**Benefits:**
- Discoverable command structure
- Consistent interface

---

## Project Structure

### Directory Layout

```
mortgage_data_manager/
├── src/mortgage_data_manager/     # Source code
│   ├── core/                       # Shared utilities
│   │   ├── config.py              # Base configuration
│   │   ├── medallion.py           # Medallion utilities
│   │   ├── io.py                  # I/O operations
│   │   └── logging.py             # Logging setup
│   ├── cli/                        # Top-level CLI
│   │   └── main.py                # Unified CLI entry point
│   ├── hmda/                       # HMDA subpackage
│   │   ├── config.py
│   │   ├── download.py
│   │   ├── import_bronze.py
│   │   ├── import_silver.py
│   │   └── cli/
│   │       └── main.py
│   ├── fha/                        # FHA subpackage
│   ├── gnma/                       # GNMA agency
│   ├── fhfa/                       # FHFA agency
│   ├── fnma/                       # FNMA agency
│   ├── fhlmc/                      # FHLMC agency
│   ├── umbs/                       # UMBS agency
│   ├── hud/                        # HUD single-family programs
│   ├── hud_mf/                     # HUD multifamily programs
│   ├── fhlb/                       # FHLB (Acquired Member Assets)
│   ├── analytics/                  # Derived analytics
│   ├── combined/                   # Cross-source master datasets
│   └── matching/                   # Matching workflows
│       ├── match_fha_hmda/
│       ├── match_fhfa_hmda/
│       └── match_hmda_sellers_and_purchasers/
├── data/                           # Data directory (not in repo; created at runtime or via symlink)
├── crosswalk/                      # Final crosswalk outputs from matching workflows
├── tests/                          # Test suite
├── docs/                           # Documentation
│   ├── api/                        # API reference
│   ├── notes/                      # Research & data-quality notes
│   ├── architecture.md
│   └── configuration.md
├── pyproject.toml                  # Project configuration
└── README.md                       # Main documentation
```

### Package Organization

Each subpackage follows a consistent structure:

```
subpackage/
├── __init__.py         # Package exports
├── config.py           # Configuration class
├── download.py         # Download functions
├── import_bronze.py    # Bronze layer import
├── import_silver.py    # Silver layer import
└── cli/                # CLI commands
    ├── __init__.py
    └── main.py
```

---

## Medallion Architecture

### Layer Definitions

#### Raw Layer

**Purpose**: Store data exactly as downloaded from source

**Characteristics:**
- Original file formats (CSV, Excel, PDF, etc.)
- No transformations
- Complete historical record

**Location:** `{SUBPACKAGE_DATA_DIR}/raw/`

**Example:**
```
data/hmda/raw/
├── 2020_hmda_lar.csv
├── 2021_hmda_lar.csv
└── 2022_hmda_lar.csv
```

#### Bronze Layer

**Purpose**: Convert to parquet for efficient storage and access

**Characteristics:**
- Parquet format
- Minimal transformations (type conversions)
- Schema standardization
- Data quality checks

**Location:** `{SUBPACKAGE_DATA_DIR}/bronze/`

**Example:**
```
data/hmda/bronze/
├── 2020.parquet
├── 2021.parquet
└── 2022.parquet
```

#### Silver Layer

**Purpose**: Cleaned, standardized, business-ready data

**Characteristics:**
- Type conversions applied
- Missing values handled
- Derived fields added
- Hive partitioning for efficient queries
- Compressed parquet

**Location:** `{SUBPACKAGE_DATA_DIR}/silver/`

**Example:**
```
data/hmda/silver/
├── activity_year=2020/
│   └── part-0.parquet
├── activity_year=2021/
│   └── part-0.parquet
└── activity_year=2022/
    └── part-0.parquet
```

#### Gold Layer (Future)

**Purpose**: Aggregated, business-specific datasets

**Characteristics:**
- Aggregations for specific use cases
- Optimized for reporting
- May combine multiple sources

**Location:** `{SUBPACKAGE_DATA_DIR}/gold/`

### Processing Pipeline

```
┌──────────┐
│   Raw    │  Downloaded data
│  Layer   │  Original formats
└─────┬────┘
      │
      │ download.py
      ▼
┌──────────┐
│  Bronze  │  Parquet conversion
│  Layer   │  Type standardization
└─────┬────┘
      │
      │ import_bronze.py
      ▼
┌──────────┐
│  Silver  │  Cleaning & enrichment
│  Layer   │  Partitioning
└─────┬────┘
      │
      │ import_silver.py
      ▼
┌──────────┐
│   Gold   │  Aggregations
│  Layer   │  Business datasets
└──────────┘
```

---

## Configuration Management

### Environment Variables

Configuration uses environment variables for flexibility:

```bash
# .env file
MORTGAGE_DATA_DIR=/data/mortgage_data
HMDA_DATA_DIR=/data/hmda
FHA_DATA_DIR=/data/fha
```

### Configuration Hierarchy

```python
# 1. Base configuration (core/config.py)
class MortgageDataConfig:
    PROJECT_DIR: Path
    DATA_DIR: Path
    OUTPUT_DIR: Path

# 2. Subpackage configuration (hmda/config.py)
class HMDAConfig(MortgageDataConfig):
    HMDA_DATA_DIR: Path = Path(config("HMDA_DATA_DIR", default=...))

    @classmethod
    def ensure_directories(cls):
        # Create HMDA-specific directories
        pass

# 3. Usage
config = HMDAConfig()
silver_dir = config.get_medallion_dir('hmda', 'silver')
```

### Design Decisions

1. **Class-level attributes**: Configuration as class attributes for easy access
2. **Inheritance**: Subpackages extend base config, adding specific attributes
3. **Environment variables**: Override defaults with environment variables
4. **Path resolution**: Automatic path resolution from project root
5. **Directory creation**: Automatic directory creation on first use

---

## CLI Design

### Typer Framework

All CLIs use Typer for consistent, modern command-line interfaces:

```python
import typer
from rich.console import Console

app = typer.Typer(
    name="hmda",
    help="HMDA data operations",
    no_args_is_help=True,
)

console = Console()

@app.command()
def download(years: list[int] = typer.Option(..., help="Years to download")):
    """Download HMDA data for specified years."""
    console.print(f"[cyan]Downloading data for {len(years)} years...[/cyan]")
    # Download logic
```

### Unified CLI Structure

```python
# src/mortgage_data_manager/cli/main.py
from mortgage_data_manager.hmda.cli.main import app as hmda_app
from mortgage_data_manager.fha.cli.main import app as fha_app

app = typer.Typer(name="mortgage-data")
app.add_typer(hmda_app, name="hmda")
app.add_typer(fha_app, name="fha")
```

---

## Data Processing Patterns

### Lazy Evaluation

All processing uses lazy evaluation for efficiency:

```python
# Lazy: builds query plan
df = pl.scan_parquet("data/raw/*.parquet")
df = df.filter(pl.col("year") >= 2020)
df = df.select(["id", "amount", "year"])

# Execute: optimized query
df.sink_parquet("data/output.parquet")
```

### Partitioning Strategy

Hive-style partitioning for efficient querying:

```python
# Write with partitioning
write_hive_partitioned(
    lf=df,
    output_dir=Path("data/silver"),
    partition_cols=["activity_year", "state"]
)

# Query specific partition
df = pl.scan_parquet("data/silver/activity_year=2020/state=CA/**/*.parquet")
```

### Incremental Processing

Process data incrementally to handle large datasets:

```python
def import_data(min_year: int, max_year: int):
    for year in range(min_year, max_year + 1):
        logger.info(f"Processing {year}...")

        # Check if already processed
        output = silver_dir / f"year={year}"
        if should_process_file(output, overwrite=False):
            process_year(year)
```

---

## Dependency Management

### Core Dependencies

Shared across all subpackages:

```toml
dependencies = [
    "polars>=1.26.0",      # Data processing
    "pandas>=2.2.0",       # Compatibility
    "pyarrow>=19.0.0",     # Parquet I/O
    "python-decouple>=3.8", # Configuration
    "typer>=0.9.0",        # CLI framework
    "rich>=13.0.0",        # Terminal formatting
]
```

### Optional Dependencies

Subpackage-specific dependencies:

```toml
[project.optional-dependencies]
hmda = ["selenium>=4.32.0", "scipy>=1.15.0"]
fha = ["openpyxl>=3.1.5", "networkx>=3.3", "matplotlib>=3.10.0"]
mbs = ["pymupdf>=1.23.0", "python-dateutil>=2.8.0"]
matching = ["addfips>=0.4.0", "numpy>=1.26.0"]
```

### Installation Options

```bash
# Install only HMDA support
pip install mortgage-data-manager[hmda]

# Install everything
pip install mortgage-data-manager[all]

# Development installation
pip install mortgage-data-manager[all,dev]
```

---

## Design Decisions

### 1. Why Polars over Pandas?

**Rationale:**
- **Performance**: 5-10x faster for large datasets
- **Memory efficiency**: Lazy evaluation reduces memory footprint
- **Modern API**: More intuitive query syntax
- **Arrow backend**: Efficient columnar format

**Trade-offs:**
- Learning curve for pandas users
- Smaller ecosystem than pandas
- Some libraries expect pandas DataFrames

**Solution:**
- Easy conversion: `df.to_pandas()` when needed
- Most mortgage data operations don't require pandas-specific features

### 2. Why Parquet over CSV?

**Rationale:**
- **Compression**: 80-90% smaller file sizes
- **Speed**: 10-100x faster reads
- **Schema**: Built-in type information
- **Partitioning**: Efficient subset queries

**Trade-offs:**
- Not human-readable
- Requires parquet-compatible tools

**Solution:**
- Keep raw layer in original format
- Convert to parquet in bronze layer

### 3. Why Medallion Architecture?

**Rationale:**
- **Data lineage**: Clear transformation steps
- **Reproducibility**: Easy to rebuild downstream layers
- **Flexibility**: Can process data at different levels
- **Debugging**: Easy to inspect intermediate stages

**Trade-offs:**
- More disk space (multiple copies)
- More processing steps

**Solution:**
- Disk is cheap, time is expensive
- Compression mitigates storage costs

### 4. Why Typer over Argparse?

**Rationale:**
- **Type hints**: Leverages Python type system
- **Auto-documentation**: Generates help from docstrings
- **Validation**: Automatic input validation
- **Rich integration**: Beautiful terminal output

**Trade-offs:**
- Additional dependency
- Different from stdlib argparse

**Solution:**
- Better DX (developer experience) worth the dependency
- Typer is stable and well-maintained

### 5. Why Unified Package vs. Separate Packages?

**Rationale:**
- **Shared utilities**: Eliminate code duplication
- **Consistent patterns**: Same architecture across all data sources
- **Easier maintenance**: Single codebase to maintain
- **Simpler dependencies**: One installation for all packages

**Trade-offs:**
- Larger package size
- All dependencies installed together

**Solution:**
- Optional dependencies for subpackages
- Users can install only what they need

---

## Future Architecture Considerations

### Planned Enhancements

1. **Gold Layer**: Add aggregation layer for business-specific datasets
2. **Data Catalog**: Metadata about available datasets
3. **Workflow Engine**: DAG-based processing pipelines
4. **API Layer**: REST API for data access
5. **Caching**: Intelligent caching for repeated queries

### Extensibility

To add a new data source:

1. Create subpackage directory
2. Implement configuration class (inherit from MortgageDataConfig)
3. Add download, import_bronze, import_silver modules
4. Create CLI with Typer
5. Add to unified CLI
6. Update documentation

---

## Best Practices

1. **Follow DRY**: Use shared utilities from `core/`
2. **Use type hints**: Enable better IDE support and validation
3. **Log appropriately**: Use structured logging for debugging
4. **Test thoroughly**: Write tests for new functionality
5. **Document changes**: Update docs when changing architecture

## See Also

- [Configuration Guide](configuration.md) - Environment variables and settings
- [Core API](api/core.md) - Shared utilities API
