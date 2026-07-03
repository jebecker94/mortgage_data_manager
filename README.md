# Mortgage Data Manager

A unified data management system for mortgage market data, combining HMDA, FHA/HUD, MBS agencies, and matching workflows into a single, cohesive package.

## Overview

The Mortgage Data Manager provides a unified interface for downloading, processing, and analyzing mortgage-related data from multiple sources:

- **HMDA** (Home Mortgage Disclosure Act) - Loan-level mortgage data
- **FHA / HUD** - FHA-insured mortgage snapshots and HUD single-family/multifamily program data
- **MBS Agencies** - Mortgage-backed securities data from GNMA, FHFA, FNMA, FHLMC, UMBS
- **FHLB** (Federal Home Loan Bank) - Acquired Member Assets (AMA) data
- **Matching** - Workflows for linking records across datasets

**Note on Downloads:** Some data sources (FNMA, FHLMC, UMBS) require manual download due to Terms of Service restrictions. Download functionality can break without warning as agencies change their websites.

## Matching Workflows

A major contribution of this repository is a suite of **matching workflows** that link loan-level records across different mortgage datasets. These crosswalks enable research that connects origination data (HMDA) to securitization outcomes (MBS pools), government insurance records (FHA), and regulatory filings.

### Available Crosswalks

The following crosswalk files are tracked in this repository via Git LFS (~547 MB total):

| Crosswalk | Description | Coverage |
|-----------|-------------|----------|
| `data/matching/fhfa_hmda/output/fhfa_hmda_crosswalk_2018_2024.parquet` | Links FHFA loan-level data to HMDA records | 2018-2024 |
| `data/matching/mbs_umbs/output/fnma_crosswalk.parquet` | Links Fannie Mae MBS to UMBS pools | All years |
| `data/matching/mbs_umbs/output/fhlmc_crosswalk.parquet` | Links Freddie Mac MBS to UMBS pools | All years |
| `data/matching/mbs_fhfa/output/fnma_fhfa_crosswalk_*.parquet` | Links Fannie Mae MBS to FHFA loan-level data | 2019-2024 |
| `data/matching/mbs_fhfa/output/fhlmc_fhfa_crosswalk_*.parquet` | Links Freddie Mac MBS to FHFA loan-level data | 2019-2024 |

### Matching Documentation

Each workflow includes detailed methodology documentation with validation exercises examining match rates across time, geography, and loan characteristics:

| Workflow | Documentation |
|----------|---------------|
| FHA-HMDA | [docs/matching/fha_hmda_matching.md](docs/matching/fha_hmda_matching.md) |
| FHA-GNMA | [docs/matching/fha_gnma_matching.md](docs/matching/fha_gnma_matching.md) |
| FHFA-HMDA | [docs/matching/fhfa_hmda_matching.md](docs/matching/fhfa_hmda_matching.md) |
| MBS-FHFA | [docs/matching/mbs_fhfa_matching.md](docs/matching/mbs_fhfa_matching.md) |
| MBS-UMBS | [docs/matching/mbs_umbs_matching.md](docs/matching/mbs_umbs_matching.md) |
| HMDA-FHLB | [docs/matching/hmda_fhlb_matching.md](docs/matching/hmda_fhlb_matching.md) |
| FNMA-HMDA-FHLB (retired) | [docs/matching/fnma_hmda_fhlb_matching.md](docs/matching/fnma_hmda_fhlb_matching.md) |
| MBS-FHLB | [docs/matching/mbs_fhlb_matching.md](docs/matching/mbs_fhlb_matching.md) |
| HMDA Sellers & Purchasers | [docs/matching/hmda_sellers_purchasers_matching.md](docs/matching/hmda_sellers_purchasers_matching.md) |

## Installation

### Requirements

- Python 3.12 or higher
- uv (recommended) or pip

### Install for Development

```bash
# Clone the repository
cd mortgage_data_manager

# Install with all dependencies for development
uv pip install -e ".[all,dev]"

# Or with pip
pip install -e ".[all,dev]"
```

### Install Specific Subpackages

Once fully implemented, you'll be able to install only what you need:

```bash
# Install only HMDA and FHA support
pip install mortgage-data-manager[hmda,fha]

# Install everything
pip install mortgage-data-manager[all]
```

## Quick Start

### CLI Usage

```bash
# Show version and status
mortgage-data version

# Show project information
mortgage-data info

# Get help
mortgage-data --help

# HMDA Commands
# Download HMDA data
mortgage-data hmda download --min-year 2020 --max-year 2024

# Import HMDA data (bronze + silver layers)
mortgage-data hmda import post2018 --min-year 2020 --max-year 2024

# Import with options
mortgage-data hmda import 2007-2017 --min-year 2007 --max-year 2017 --drop-tract-vars

# FHA Commands
# Download FHA data
mortgage-data fha download single-family
mortgage-data fha download hecm --pause-length 10
mortgage-data fha download both

# Import FHA data (bronze + silver layers)
mortgage-data fha import single-family --min-year 2015 --max-year 2024
mortgage-data fha import hecm --min-year 2015 --max-year 2024

# Run complete pipeline (download + import)
mortgage-data fha pipeline both --min-year 2020

# MBS Agency Commands
# Each MBS agency has its own top-level command

# GNMA (Ginnie Mae) commands
mortgage-data gnma download all monthly
mortgage-data gnma schemas pipeline monthly
mortgage-data gnma process all monthly
mortgage-data gnma pipeline full monthly

# FHFA commands
mortgage-data fhfa download
mortgage-data fhfa process

# FNMA (Fannie Mae) commands
mortgage-data fnma download
mortgage-data fnma process

# FHLMC (Freddie Mac) commands
mortgage-data fhlmc download
mortgage-data fhlmc process
mortgage-data fhlmc pipeline

# UMBS commands
mortgage-data umbs download
mortgage-data umbs process

# FHLB (Federal Home Loan Bank)
# FHLB is available as a Python module (no CLI commands)
# Import and use directly in your code:
from mortgage_data_manager.fhlb import import_members

# Matching Commands
# Show available matching workflows
mortgage-data match info

# List all matching workflow modules
mortgage-data match list-workflows
```

### Python API Usage

#### Core Utilities

```python
from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.core.logging import configure_logging, get_logger

# Configure logging
configure_logging(level="INFO")
logger = get_logger(__name__)

# Access configuration
print(f"Data directory: {MortgageDataConfig.DATA_DIR}")
print(f"HMDA silver data: {MortgageDataConfig.get_medallion_dir('hmda', 'silver')}")

# Check if file needs processing
output_path = config.DATA_DIR / "output.parquet"
if should_process_file(output_path, overwrite=False):
    logger.info("Processing file...")
    # Your processing code here
```

#### HMDA Data

```python
from mortgage_data_manager.hmda import (
    download_hmda_files,
    build_bronze_post2018,
    build_silver_post2018,
)
from mortgage_data_manager.hmda.config import HMDAConfig

# Download HMDA files for specific years
download_hmda_files(
    years=range(2020, 2025),
    include_mlar=True,
)

# Build bronze layer (raw ZIP → parquet)
build_bronze_post2018(
    dataset="loans",
    min_year=2020,
    max_year=2024,
)

# Build silver layer (parquet → analysis-ready parquet)
build_silver_post2018(
    dataset="loans",
    min_year=2020,
    max_year=2024,
)

# Read processed data
import polars as pl
silver_dir = HMDAConfig.HMDA_SILVER_DIR / "loans" / "post2018"
df = pl.scan_parquet(silver_dir / "activity_year=2020/**/*.parquet")
print(f"Loaded {df.select(pl.count()).collect().item():,} records")
```

#### FHA Data

```python
from mortgage_data_manager.fha import (
    download_single_family_snapshots,
    download_hecm_snapshots,
    import_single_family_snapshots,
    import_hecm_snapshots,
    run_pipeline,
)
from mortgage_data_manager.fha.config import FHAConfig

# Download FHA snapshot files
download_single_family_snapshots(
    pause_length=5,
    include_zip=True,
)
download_hecm_snapshots(
    pause_length=5,
    include_zip=True,
)

# Import single-family data (bronze + silver layers)
import_single_family_snapshots(
    overwrite=False,
    min_year=2015,
    max_year=2024,
    add_fips=True,
    add_date=True,
)

# Import HECM data
import_hecm_snapshots(
    overwrite=False,
    min_year=2015,
    max_year=2024,
    add_fips=True,
    add_date=True,
)

# Or run complete pipeline (download + import)
run_pipeline(
    dataset="both",  # "single-family", "hecm", or "both"
    skip_download=False,
    skip_import=False,
    overwrite=False,
    min_year=2015,
    max_year=2024,
)

# Read processed data
import polars as pl
sf_dir = FHAConfig.FHA_SILVER_DIR / "single_family"
df = pl.scan_parquet(sf_dir / "Year=2024/**/*.parquet")
print(f"Loaded {df.select(pl.count()).collect().item():,} records")
```

#### FHLB Data

```python
# FHLB - Federal Home Loan Bank (Acquired Member Assets)
from mortgage_data_manager.fhlb.import_members import import_members
import_members()
```

#### Matching Workflows

```python
from mortgage_data_manager.matching.config import MatchingConfig

# FHA-HMDA matching
from mortgage_data_manager.matching.match_fha_hmda.match_fha_hmda import run_matching
run_matching(start_year=2020, end_year=2024)

# HMDA sellers and purchasers matching
from mortgage_data_manager.matching.match_hmda_sellers_and_purchasers.match import run_matching
run_matching(year=2020)

# Access matching configuration
matching_dir = MatchingConfig.MATCHING_DATA_DIR
output_dir = MatchingConfig.MATCHING_OUTPUT_DIR
cache_dir = MatchingConfig.MATCHING_CACHE_DIR

# Get matching-type-specific directory
fha_hmda_dir = MatchingConfig.get_matching_type_dir("fha_hmda")
```

## Project Structure

```
mortgage_data_manager/
├── src/mortgage_data_manager/     # Source code
│   ├── core/                       # Shared utilities
│   │   ├── config.py              # Base configuration
│   │   ├── medallion.py           # Medallion architecture utilities
│   │   ├── io.py                  # Download and I/O operations
│   │   └── logging.py             # Standardized logging
│   ├── cli/                        # Top-level CLI
│   ├── hmda/                       # HMDA subpackage
│   ├── fha/                        # FHA subpackage
│   ├── gnma/                       # GNMA (Ginnie Mae)
│   ├── fhfa/                       # FHFA
│   ├── fnma/                       # FNMA (Fannie Mae)
│   ├── fhlmc/                      # FHLMC (Freddie Mac)
│   ├── umbs/                       # UMBS
│   ├── hud/                        # HUD single-family programs
│   ├── hud_mf/                     # HUD multifamily programs
│   ├── fhlb/                       # FHLB (Acquired Member Assets)
│   ├── analytics/                  # Derived analytics
│   ├── combined/                   # Cross-source master datasets
│   └── matching/                   # Matching workflows
├── data/                           # Data directory (gitignored)
├── tests/                          # Test suite
└── docs/                           # Documentation (api/, design/, matching/, notes/)
```

## Core Utilities

### Configuration (`core/config.py`)

The `MortgageDataConfig` class provides centralized configuration management:

```python
from mortgage_data_manager.core.config import MortgageDataConfig

# Get subpackage data directory
hmda_data_dir = MortgageDataConfig.get_subpackage_data_dir('hmda')

# Get medallion stage directory
silver_dir = MortgageDataConfig.get_medallion_dir('hmda', 'silver')

# Ensure directories exist
MortgageDataConfig.ensure_directories('hmda')
```

### Medallion Architecture (`core/medallion.py`)

Utilities for working with the medallion architecture (raw → bronze → silver → gold):

```python
from mortgage_data_manager.core.medallion import (
    should_process_file,
    write_hive_partitioned,
    read_medallion_layer,
    validate_medallion_layer,
)

# Check if file should be processed
if should_process_file(output_path, overwrite=False):
    # Process data...

# Write hive-partitioned parquet
write_hive_partitioned(
    lazy_frame,
    output_dir=silver_dir,
    partition_cols=["year", "month"]
)

# Read from medallion layer
df = read_medallion_layer(silver_dir / "year=2020")
```

### I/O Operations (`core/io.py`)

Common file operations:

```python
from mortgage_data_manager.core.io import (
    download_file,
    extract_zip,
    detect_delimiter,
)

# Download file with progress bar
download_file("https://example.com/data.zip", Path("data/raw/data.zip"))

# Extract zip archive
extracted_files = extract_zip(
    Path("data/raw/data.zip"),
    Path("data/raw/extracted")
)

# Auto-detect CSV delimiter
delimiter = detect_delimiter(Path("data/raw/file.txt"))
```

### Logging (`core/logging.py`)

Standardized logging setup:

```python
from mortgage_data_manager.core.logging import (
    configure_logging,
    get_logger,
    log_execution_time,
)

# Configure logging
configure_logging(level="INFO", log_file=Path("logs/app.log"))

# Get logger
logger = get_logger(__name__)
logger.info("Processing started")

# Decorator for timing
@log_execution_time
def slow_operation():
    # Your code here
    pass
```

## Environment Variables

Configure the project using environment variables:

```bash
# Global configuration
export MORTGAGE_DATA_PROJECT_DIR=/path/to/project
export MORTGAGE_DATA_DIR=/path/to/data
export MORTGAGE_OUTPUT_DIR=/path/to/output

# Subpackage overrides (future phases)
export HMDA_DATA_DIR=/path/to/hmda/data
export FHA_RAW_DIR=/path/to/fha/raw
```

Create a `.env` file in the project root:

```bash
MORTGAGE_DATA_DIR=/Volumes/BigDrive/mortgage_data
```

## Development

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src/mortgage_data_manager --cov-report=html

# Run specific test module
pytest tests/test_core/test_config.py -v
```

### Code Quality

```bash
# Format code with ruff
ruff format .

# Lint code
ruff check .

# Type checking with mypy
mypy src/
```

## Documentation

Comprehensive documentation is available in the `docs/` directory:

### Guides

- **[Getting Started](docs/getting_started.md)** - Installation, quick start, and common workflows
- **[CLI Reference](docs/cli_reference.md)** - Complete command-line interface documentation
- **[Configuration Guide](docs/configuration.md)** - Environment variables, configuration options, and best practices
- **[Architecture Documentation](docs/architecture.md)** - System design, patterns, and design decisions

### API Reference

Detailed API documentation for each component:

- **[Core API](docs/api/core.md)** - Configuration, medallion architecture, logging, and I/O utilities
- **[HMDA API](docs/api/hmda.md)** - HMDA data download, import, and processing
- **[FHA API](docs/api/fha.md)** - FHA data download, import, and analysis tools
- **[GNMA API](docs/api/gnma.md)** - Ginnie Mae MBS data
- **[FHFA API](docs/api/fhfa.md)** - FHFA loan-level data
- **[FNMA API](docs/api/fnma.md)** - Fannie Mae loan-level data
- **[FHLMC API](docs/api/fhlmc.md)** - Freddie Mac loan-level data
- **[UMBS API](docs/api/umbs.md)** - Uniform MBS data
- **[HUD API](docs/api/hud.md)** - HUD single-family program data
- **[HUD MF API](docs/api/hud_mf.md)** - HUD multifamily program data
- **[FHLB API](docs/api/fhlb.md)** - Federal Home Loan Bank data
- **[Matching API](docs/api/matching.md)** - Workflows for linking records across datasets

### Quick Links

| Task | Documentation |
|------|---------------|
| Getting started | [Getting Started Guide](docs/getting_started.md) |
| CLI commands reference | [CLI Reference](docs/cli_reference.md) |
| Setting up configuration | [Configuration Guide](docs/configuration.md) |
| Understanding the architecture | [Architecture Documentation](docs/architecture.md) |
| Using core utilities | [Core API Reference](docs/api/core.md) |
| Working with HMDA data | [HMDA API Reference](docs/api/hmda.md) |
| Working with FHA data | [FHA API Reference](docs/api/fha.md) |
| Working with GNMA data | [GNMA API Reference](docs/api/gnma.md) |
| Working with FHFA data | [FHFA API Reference](docs/api/fhfa.md) |
| Working with FNMA data | [FNMA API Reference](docs/api/fnma.md) |
| Working with FHLMC data | [FHLMC API Reference](docs/api/fhlmc.md) |
| Working with UMBS data | [UMBS API Reference](docs/api/umbs.md) |
| Working with HUD data | [HUD API Reference](docs/api/hud.md) |
| Working with FHLB data | [FHLB API Reference](docs/api/fhlb.md) |
| Matching across datasets | [Matching API Reference](docs/api/matching.md) |

## Architecture

The project uses a **medallion architecture** for data processing:

- **Raw**: Original downloaded files (Excel, CSV, ZIP)
- **Bronze**: Minimal processing, parquet format with basic types
- **Silver**: Cleaned, standardized, analysis-ready data
- **Gold**: Aggregated or feature-engineered datasets (optional)

All subpackages follow consistent patterns:

- **Configuration**: Inherits from `MortgageDataConfig`
- **CLI**: Typer-based commands integrated into unified CLI
- **Data Processing**: Polars-first for performance
- **Storage**: Hive-partitioned parquet for efficient querying

## Forthcoming

This project is under active development. Here's what's on the roadmap:

### Near-term

- **Additional validation exercises** for matching workflows, including geographic and temporal analyses
- **Downloader examples** demonstrating how to use each data source's download utilities
- **Data query examples** showing common patterns for loading and filtering processed data
- **Visualization examples** highlighting the value of the cleaned, standardized data
- **MBS manual download guide** with detailed documentation on manual steps required for complete MBS data coverage
- **Data directory skeleton** to help users understand the expected folder structure

### Longer-term

- **Complete HMDA-to-MBS workflow** providing an end-to-end pipeline from raw HMDA data through MBS pool matching
- **Unified CLI patterns** with more standardized commands across all subpackages
- **Consolidated matching interface** with a common API for all matching workflows

---

## A Note on Development

This project is also an experiment in AI-assisted software development. The codebase was developed almost entirely through collaboration with [Claude](https://claude.ai), Anthropic's AI assistant, using the [Claude Code](https://claude.ai/code) CLI tool. Specifically, the vast majority of the code was written by **Claude Opus 4.5** with human guidance, review, and direction.

As a result of this development approach—and the inherent limitations of context windows in large language models—you may notice some inconsistencies in style, naming conventions, or patterns across different parts of the codebase. These are being actively addressed, and non-breaking improvements should be expected in the coming weeks and months.

If you're interested in AI-assisted development or curious about what's possible with modern LLM tooling, feel free to explore the commit history for a sense of how this collaboration unfolded.

*Built with [Claude Code](https://claude.ai/code) by [Anthropic](https://anthropic.com).*

---

## Contributing

Contributions are welcome! Please open an issue or pull request on GitHub.

## License

MIT License

## Contact

For questions or issues, please open an issue on GitHub.

