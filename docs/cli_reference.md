# CLI Reference

Complete command-line interface reference for Mortgage Data Manager.

## Table of Contents

- [Main Command](#main-command)
- [Global Options](#global-options)
- [HMDA Commands](#hmda-commands)
- [FHA Commands](#fha-commands)
- [GNMA Commands](#gnma-commands)
- [FHFA Commands](#fhfa-commands)
- [FNMA Commands](#fnma-commands)
- [FHLMC Commands](#fhlmc-commands)
- [UMBS Commands](#umbs-commands)
- [FHLB Commands](#fhlb-commands)
- [Matching Commands](#matching-commands)

---

## Main Command

### `mortgage-data`

Unified entry point for all mortgage data operations.

```bash
mortgage-data [OPTIONS] COMMAND [ARGS]...
```

**Available Commands:**
- `version` - Show version information
- `info` - Show project information and status
- `hmda` - HMDA data operations
- `fha` - FHA data operations
- `gnma` - GNMA (Ginnie Mae) data operations
- `fhfa` - FHFA data operations
- `fnma` - Fannie Mae data operations
- `fhlmc` - Freddie Mac data operations
- `umbs` - UMBS data operations
- `fhlb` - FHLB (Federal Home Loan Banks) data operations
- `match` - Matching workflows for linking records

**Global Options:**
- `--install-completion` - Install shell completion
- `--show-completion` - Show completion script
- `--help` - Show help message

### `mortgage-data version`

Display version information.

```bash
mortgage-data version
```

**Output:**
```
Mortgage Data Manager
Version: 0.1.0
```

### `mortgage-data info`

Show project information, paths, and available data sources.

```bash
mortgage-data info
```

**Output includes:**
- Project and data directory paths
- Implementation status (phases)
- Available core modules
- Available data sources
- Financial institutions (Python API only)

---

## Global Options

These options work with most commands:

### Common Flags

- `--help, -h` - Show help for the command
- `--version, -V` - Show version (where applicable)
- `--verbose` - Increase output verbosity (some commands)
- `--quiet` - Suppress non-error output (some commands)

---

## HMDA Commands

### `mortgage-data hmda`

Home Mortgage Disclosure Act data operations.

```bash
mortgage-data hmda [OPTIONS] COMMAND [ARGS]...
```

**Available Commands:**
- `download` - Download HMDA data files
- `bronze` - Build the bronze (raw → parquet) layer
- `silver` - Build the silver (Hive-partitioned) layer
- `pipeline` - Bronze + silver in one step
- `info` - Show HMDA-specific information

### `mortgage-data hmda download`

Download HMDA data for specified years.

```bash
mortgage-data hmda download --min-year YEAR --max-year YEAR
```

**Options:**
- `--min-year INTEGER` - First year to download (required)
- `--max-year INTEGER` - Last year to download, inclusive (required)
- `--destination TEXT` - Destination folder for downloads (default: data/raw)
- `--include-mlar` - Include Modified LAR (MLAR) files
- `--include-historical` - Include historical 2007-2017 files
- `--pause SECONDS` - Seconds to pause between downloads (default: 5.0)
- `--wait INTEGER` - Seconds to wait for JavaScript to load (default: 10)
- `--overwrite TEXT` - Overwrite behavior: skip, always, if_newer, if_size_diff (default: skip)

**Examples:**
```bash
# Download data for 2020-2024
mortgage-data hmda download --min-year 2020 --max-year 2024

# Single year
mortgage-data hmda download --min-year 2023 --max-year 2023

# Overwrite existing files
mortgage-data hmda download --min-year 2023 --max-year 2023 --overwrite always
```

### `mortgage-data hmda bronze`

Build the bronze (raw → parquet) layer for an HMDA period.

```bash
mortgage-data hmda bronze PERIOD [OPTIONS]
```

**Arguments:**
- `PERIOD` - Time period: `post2018`, `2007-2017`, or `pre2007`

**Options:**
- `--min-year INTEGER` - Minimum year (defaults: 2018 / 2007 / 1990)
- `--max-year INTEGER` - Maximum year (defaults: 2025 / 2017 / 2006)
- `-t, --datasets TEXT` - Datasets to process: `loans`, `panel`, `transmittal_series` (post2018 and pre2007 only; can repeat)
- `--overwrite` - Overwrite existing files

**Examples:**
```bash
mortgage-data hmda bronze post2018 --min-year 2018 --max-year 2025
mortgage-data hmda bronze 2007-2017 --overwrite
mortgage-data hmda bronze pre2007 --datasets loans
```

### `mortgage-data hmda silver`

Build the silver (Hive-partitioned) layer for an HMDA period. Arguments and options match `bronze`.

```bash
mortgage-data hmda silver PERIOD [OPTIONS]
```

**Examples:**
```bash
mortgage-data hmda silver post2018 --min-year 2020 --max-year 2024
mortgage-data hmda silver 2007-2017 --overwrite
```

### `mortgage-data hmda pipeline`

Run bronze + silver in one step (formerly `hmda import`). Same arguments as `bronze`/`silver`.

```bash
mortgage-data hmda pipeline PERIOD [OPTIONS]
```

**Examples:**
```bash
mortgage-data hmda pipeline post2018 --min-year 2018 --max-year 2025
mortgage-data hmda pipeline 2007-2017 --min-year 2007 --max-year 2017
mortgage-data hmda pipeline pre2007 --min-year 1990 --max-year 2006
```

---

## FHA Commands

### `mortgage-data fha`

Federal Housing Administration data operations.

```bash
mortgage-data fha [OPTIONS] COMMAND [ARGS]...
```

**Available Commands:**
- `download` - Download FHA snapshot data files from HUD.gov
- `bronze` - Convert FHA raw snapshots to the bronze layer (Parquet)
- `silver` - Clean FHA bronze snapshots into the hive-partitioned silver layer
- `pipeline` - Run complete pipeline (download + import)
- `info` - Show FHA-specific information
- `lender-list` - HUD FHA-approved lender list (roster of mortgagee IDs)

### `mortgage-data fha download`

Download FHA snapshot data files from HUD.gov.

```bash
mortgage-data fha download DATASET [OPTIONS]
```

**Arguments:**
- `DATASET` - Dataset to download: `single-family`, `hecm`, or `both`

**Options:**
- `-o, --destination PATH` - Destination folder for downloads (default: `data/raw/{dataset}`)
- `--no-zip` - Skip downloading .zip archives
- `--overwrite` - Overwrite existing files instead of skipping them
- `--pause SECONDS` - Seconds to pause between downloads (default: 5.0)
- `--timeout SECONDS` - Request timeout in seconds (default: 30)
- `--retries COUNT` - Number of retry attempts for failed downloads (default: 3)
- `-v, --verbose` - Enable verbose (DEBUG level) logging output
- `-q, --quiet` - Suppress progress bars and reduce output

**Examples:**
```bash
# Download single-family data
mortgage-data fha download single-family

# Download HECM data with a longer pause
mortgage-data fha download hecm --pause 10

# Download both datasets, overwriting existing files
mortgage-data fha download both --overwrite --verbose
```

### `mortgage-data fha bronze`

Convert FHA raw snapshots to the bronze layer (Parquet).

```bash
mortgage-data fha bronze DATASET [OPTIONS]
```

**Arguments:**
- `DATASET` - Dataset: "single-family", "hecm", or "both"

**Options:**
- `--overwrite` - Overwrite existing bronze files

**Examples:**
```bash
mortgage-data fha bronze single-family
mortgage-data fha bronze hecm --overwrite
```

### `mortgage-data fha silver`

Clean FHA bronze snapshots into the hive-partitioned silver layer.

```bash
mortgage-data fha silver DATASET [OPTIONS]
```

**Arguments:**
- `DATASET` - Dataset: "single-family", "hecm", or "both"

**Options:**
- `--min-year INTEGER` - Minimum year to process (default: 2010)
- `--max-year INTEGER` - Maximum year to process (default: 2025)
- `--overwrite` - Overwrite existing silver files
- `--no-fips` - Skip FIPS code enrichment
- `--no-date` - Skip date column generation

**Examples:**
```bash
mortgage-data fha silver single-family --min-year 2020
mortgage-data fha silver hecm --min-year 2015 --max-year 2023
mortgage-data fha silver both --overwrite
```

### `mortgage-data fha pipeline`

Run complete download + import pipeline for FHA data.

```bash
mortgage-data fha pipeline DATASET [OPTIONS]
```

**Arguments:**
- `DATASET` - Dataset to process: `single-family`, `hecm`, or `both`

**Options:**
- `--skip-download` - Skip download step (use existing files)
- `--skip-import` - Skip import step (download only)
- `--no-zip` - Skip downloading .zip archives
- `--overwrite` - Overwrite existing files instead of skipping them
- `--pause SECONDS` - Seconds to pause between downloads (default: 5.0)
- `--min-year INTEGER` - Minimum year to process (default: 2010)
- `--max-year INTEGER` - Maximum year to process (default: 2025)
- `--no-fips` - Skip FIPS code enrichment
- `--no-date` - Skip date column generation

**Examples:**
```bash
# Run complete pipeline for both datasets
mortgage-data fha pipeline both --overwrite --min-year 2020

# Single-family only
mortgage-data fha pipeline single-family

# HECM, skipping the download step
mortgage-data fha pipeline hecm --skip-download
```

---

## GNMA Commands

### `mortgage-data gnma`

GNMA (Ginnie Mae) data operations.

```bash
mortgage-data gnma [OPTIONS] COMMAND [ARGS]...
```

**Available Commands:**
- `download` - Download GNMA data files and schemas
- `schemas` - Process GNMA schema files (PDFs, cleaning, standardization)
- `bronze` - Stage GNMA raw data files to bronze parquet
- `silver` - Transform GNMA bronze parquet to silver using schemas
- `pipeline` - Run multi-step workflows (end-to-end automation)
- `info` - Display GNMA configuration information

### `mortgage-data gnma download`

Download GNMA data files and schemas. This is a command group; data is selected by prefix, not by frequency.

```bash
mortgage-data gnma download COMMAND [PREFIXES]... [OPTIONS]
```

**Subcommands:**
- `data` - Download GNMA data files for specified prefixes
- `schemas` - Download GNMA schema files (PDFs) for specified prefixes
- `all` - Download both data and schema files
- `bulk` - Download files from the GNMA bulk download page

**Common prefixes:** `monthly`, `llmon1`, `llmon2`, `hllmon1`, `hllmon2`

**Options (on `data`/`schemas`/`all`):**
- `--min-date TEXT` - Start date in YYYYMM format (e.g. `202501`)
- `-c, --config TEXT` - Path to config file with credentials

**Examples:**
```bash
# Download data for the monthly prefix
mortgage-data gnma download data monthly

# Download data for multiple prefixes from 2025 onward
mortgage-data gnma download data llmon1 llmon2 --min-date 202501

# Download all prefixes (leave prefixes empty)
mortgage-data gnma download data

# Download both data and schemas
mortgage-data gnma download all monthly
```

### `mortgage-data gnma schemas`

Process GNMA schema files (PDFs, cleaning, standardization).

```bash
mortgage-data gnma schemas COMMAND [OPTIONS]
```

**Subcommands:**
- `extract` - Extract schema tables from PDF files
- `combine` - Combine cleaned schema files by prefix
- `standardize` - Count and standardize field names across all schemas
- `analyze` - Analyze temporal coverage and formats of schemas
- `pipeline` - Run complete schema processing pipeline

**Examples:**
```bash
# Run complete schema pipeline
mortgage-data gnma schemas pipeline

# Just extract from PDFs
mortgage-data gnma schemas extract
```

### `mortgage-data gnma bronze`

Stage GNMA raw data files to bronze parquet.

```bash
mortgage-data gnma bronze PREFIXES...
```

**Arguments:**
- `PREFIXES` - One or more prefixes to stage (e.g., `monthly`, `llmon1`, `llmon2`)

**Examples:**
```bash
mortgage-data gnma bronze monthly
mortgage-data gnma bronze llmon1 llmon2
```

### `mortgage-data gnma silver`

Transform GNMA bronze parquet to silver using schemas.

```bash
mortgage-data gnma silver PREFIXES... [OPTIONS]
```

**Arguments:**
- `PREFIXES` - One or more prefixes to transform

**Options:**
- `-r, --record-types TEXT` - Record types to process (H, L, P, I, T)
- `-v, --verbose` - Enable verbose (DEBUG level) logging output
- `-q, --quiet` - Suppress progress bars and reduce output

**Examples:**
```bash
mortgage-data gnma silver monthly
mortgage-data gnma silver llmon1 -r L P
mortgage-data gnma silver llmon1 llmon2
```

### `mortgage-data gnma pipeline`

Run multi-step GNMA workflows. This is a command group; the pipeline operates on one or more prefixes.

```bash
mortgage-data gnma pipeline COMMAND PREFIXES... [OPTIONS]
```

**Subcommands:**
- `full` - Run complete end-to-end pipeline (download → process → analyze)
- `data-only` - Run data pipeline only (skip analysis)
- `schemas-only` - Run schema processing only

**Options (on `full`):**
- `-s, --start-date TEXT` - Start date for analysis (YYYYMM)
- `-e, --end-date TEXT` - End date for analysis (YYYYMM)
- `--skip-download` - Skip download step
- `--skip-processing` - Skip processing step
- `--skip-analysis` - Skip analysis step
- `-v, --verbose` - Enable verbose (DEBUG level) logging output
- `-q, --quiet` - Suppress progress bars and reduce output

**Examples:**
```bash
# Run complete pipeline for the monthly prefix
mortgage-data gnma pipeline full monthly

# Pipeline with a date range for analysis
mortgage-data gnma pipeline full llmon1 llmon2 --start-date 202301 --end-date 202312

# Data pipeline only (skip analysis)
mortgage-data gnma pipeline data-only monthly
```

---

## FHFA Commands

### `mortgage-data fhfa`

FHFA (Federal Housing Finance Agency) data operations.

```bash
mortgage-data fhfa [OPTIONS] COMMAND [ARGS]...
```

**Available Commands:**
- `download` - Download FHFA data files and dictionaries
- `schemas` - Process data dictionaries (PDFs, Excel, master building)
- `bronze` - Load data to bronze layer (fixed-width → Parquet)
- `silver` - Transform data to silver layer (standardization)
- `pipeline` - Run multi-step workflows (end-to-end automation)
- `info` - Display FHFA configuration information

> **Note:** FHLB AMA data has moved to the `fhlb` subpackage. Use `mortgage-data fhlb download data` for FHLB data.

### `mortgage-data fhfa download`

Download FHFA data files and dictionaries. This is a command group.

```bash
mortgage-data fhfa download COMMAND [OPTIONS]
```

**Subcommands:**
- `data` - Download FHFA enterprise data files (PUDB zips)
- `dictionaries` - Download FHFA data dictionary files
- `all` - Download both FHFA data and dictionaries

**Options (on `data`):**
- `-o, --output PATH` - Output directory for downloaded files
- `--overwrite` - Overwrite existing files instead of skipping them
- `--pause SECONDS` - Seconds to pause between downloads (default: 5.0)
- `--timeout SECONDS` - Request timeout in seconds (default: 30)
- `--retries COUNT` - Number of retry attempts for failed downloads (default: 3)
- `-v, --verbose` - Enable verbose (DEBUG level) logging output
- `-q, --quiet` - Suppress progress bars and reduce output

**Examples:**
```bash
# Download FHFA enterprise data
mortgage-data fhfa download data

# Download to a custom directory, overwriting
mortgage-data fhfa download data -o /custom/path --overwrite

# Download both data and dictionaries
mortgage-data fhfa download all
```

### `mortgage-data fhfa pipeline`

Run multi-step FHFA workflows. This is a command group.

```bash
mortgage-data fhfa pipeline COMMAND [OPTIONS]
```

**Subcommands:**
- `full` - Run complete end-to-end pipeline (schemas → bronze → silver)
- `data-only` - Run data pipeline only (skip schema processing)
- `schemas-only` - Run schema processing only

**Options (on `full`):**
- `-t, --datasets TEXT` - Datasets to process (default: all). Valid: `sf_a`, `sf_b`, `sf_c`, `sf_d`, `mf_property_b`, `mf_unit_b`, `mf_c`
- `--min-year INTEGER` - First year to process (default: 2024)
- `--max-year INTEGER` - Last year to process, inclusive (default: 2024)
- `--skip-schemas` - Skip schema processing
- `--overwrite` - Overwrite existing files instead of skipping them

**Examples:**
```bash
# Process all datasets for 2024
mortgage-data fhfa pipeline full --min-year 2024 --max-year 2024

# Process specific datasets and years
mortgage-data fhfa pipeline full -t sf_c sf_a --min-year 2023 --max-year 2024

# Skip schema processing (use existing masters)
mortgage-data fhfa pipeline full --min-year 2023 --max-year 2024 --skip-schemas
```

---

## FNMA Commands

### `mortgage-data fnma`

Fannie Mae data operations.

```bash
mortgage-data fnma [OPTIONS] COMMAND [ARGS]...
```

**Available Commands:**
- `bronze` - Import raw FNMA loan-performance zip files to bronze parquet
- `silver` - Extract issuances and terminations from bronze loan-performance parquet
- `pipeline` - Run bronze + silver for FNMA loan-performance data in one step
- `download` - Download instructions (requires manual registration)
- `info` - Display FNMA configuration information

### `mortgage-data fnma download`

Fannie Mae Single-Family Loan Performance Data requires manual registration; this command prints download instructions rather than fetching files.

```bash
mortgage-data fnma download data
```

**Subcommands:**
- `data` - Instructions for downloading Fannie Mae Single-Family Loan Performance Data

### `mortgage-data fnma bronze`

Import raw FNMA loan-performance zip files to bronze parquet (one parquet per quarter).

```bash
mortgage-data fnma bronze [OPTIONS]
```

**Options:**
- `--overwrite` - Overwrite existing files instead of skipping them
- `-v, --verbose` - Enable verbose (DEBUG level) logging output
- `-q, --quiet` - Suppress progress bars and reduce output

### `mortgage-data fnma silver`

Extract issuances and terminations from bronze loan-performance parquet.

```bash
mortgage-data fnma silver [OPTIONS]
```

**Options:**
- `-v, --verbose` - Enable verbose (DEBUG level) logging output
- `-q, --quiet` - Suppress progress bars and reduce output

### `mortgage-data fnma pipeline`

Run bronze + silver in one step. Download is manual (see `fnma download`); the pipeline assumes raw files are already in place.

```bash
mortgage-data fnma pipeline [OPTIONS]
```

**Options:**
- `--overwrite` - Overwrite existing files instead of skipping them
- `-v, --verbose` - Enable verbose (DEBUG level) logging output
- `-q, --quiet` - Suppress progress bars and reduce output

**Examples:**
```bash
mortgage-data fnma pipeline
mortgage-data fnma pipeline --overwrite
```

---

## FHLMC Commands

### `mortgage-data fhlmc`

Freddie Mac data operations.

```bash
mortgage-data fhlmc [OPTIONS] COMMAND [ARGS]...
```

**Available Commands:**
- `download` - Download FHLMC data files (requires manual registration)
- `schemas` - Inspect and validate FHLMC data schemas
- `bronze` - Load data to bronze layer (raw → Parquet)
- `silver` - Build silver layer (cleaned, partitioned, analysis-ready)
- `pipeline` - Run multi-step workflows (end-to-end automation)
- `info` - Display FHLMC configuration information

### `mortgage-data fhlmc download`

Freddie Mac Single-Family Loan-Level Dataset requires manual registration; this command prints download instructions rather than fetching files.

```bash
mortgage-data fhlmc download data
```

**Subcommands:**
- `data` - Instructions for downloading Freddie Mac Single-Family Loan-Level Dataset

### `mortgage-data fhlmc schemas`

Inspect and validate FHLMC data schemas.

```bash
mortgage-data fhlmc schemas COMMAND [OPTIONS]
```

**Subcommands:**
- `list` - List all available schema types
- `show` - Show schema details for a specific dataset
- `dates` - Show all date columns and their formats

**Examples:**
```bash
# List all schemas
mortgage-data fhlmc schemas list

# Show origination schema
mortgage-data fhlmc schemas show origination
```

### `mortgage-data fhlmc bronze`

Load data to bronze layer. This is a command group.

```bash
mortgage-data fhlmc bronze COMMAND [OPTIONS]
```

**Subcommands:**
- `load` - Load raw data files to bronze layer
- `status` - Show status of bronze layer files

**Options (on `load`):**
- `-t, --datasets TEXT` (required) - Datasets to load: `origination`, `performance`, `reperforming` (repeatable)
- `--min-year INTEGER` (required) - First year to load (e.g., 2023)
- `--max-year INTEGER` (required) - Last year to load, inclusive (e.g., 2024)
- `--raw-dir PATH` - Raw data directory
- `-o, --output-dir PATH` - Bronze output directory
- `--overwrite` - Overwrite existing files instead of skipping them
- `-v, --verbose` - Enable verbose (DEBUG level) logging output
- `-q, --quiet` - Suppress progress bars and reduce output

**Examples:**
```bash
# Load origination data for 2023-2024
mortgage-data fhlmc bronze load -t origination --min-year 2023 --max-year 2024

# Load all datasets for a single year
mortgage-data fhlmc bronze load -t origination -t performance -t reperforming --min-year 2024 --max-year 2024
```

### `mortgage-data fhlmc silver`

Build silver layer (cleaned, partitioned, analysis-ready). This is a command group.

```bash
mortgage-data fhlmc silver COMMAND [OPTIONS]
```

**Subcommands:**
- `origination` - Build the silver origination layer from bronze

### `mortgage-data fhlmc pipeline`

Run multi-step FHLMC workflows. This is a command group.

```bash
mortgage-data fhlmc pipeline COMMAND [OPTIONS]
```

**Subcommands:**
- `full` - Run complete end-to-end pipeline (schemas → bronze)
- `bronze-only` - Run bronze layer pipeline only

**Options (on `full`):**
- `-t, --datasets TEXT` - Datasets to process (default: all): `origination`, `performance`, `reperforming`
- `--min-year INTEGER` - First year to process (default: 2019)
- `--max-year INTEGER` - Last year to process, inclusive (default: 2025)
- `--overwrite` - Overwrite existing files instead of skipping them
- `-v, --verbose` - Enable verbose (DEBUG level) logging output
- `-q, --quiet` - Suppress progress bars and reduce output

**Examples:**
```bash
# Run complete pipeline
mortgage-data fhlmc pipeline full --min-year 2020 --max-year 2025

# Specific datasets
mortgage-data fhlmc pipeline full -t origination -t performance --min-year 2023 --max-year 2024
```

---

## UMBS Commands

### `mortgage-data umbs`

UMBS (Uniform Mortgage-Backed Securities) data operations.

```bash
mortgage-data umbs [OPTIONS] COMMAND [ARGS]...
```

**Available Commands:**
- `download` - Download instructions (manual registration required)
- `bronze` - Import raw zip files to bronze parquet
- `silver` - Build silver layer (merge original/correction pairs)
- `pipeline` - Bronze + silver in one step
- `info` - Display UMBS configuration information

**Options:**
- `--version, -V` - Show version and exit
- `--help` - Show help message

### `mortgage-data umbs bronze`

Import raw zip files to bronze parquet.

```bash
mortgage-data umbs bronze [OPTIONS]
```

**Options:**
- `--overwrite` - Overwrite existing files instead of skipping them
- `--gse TEXT` - Filter to a single GSE: FNMA or FHLMC
- `--folder TEXT` - Filter to specific folder name(s) (repeatable)
- `-v, --verbose` - Enable verbose (DEBUG level) logging output
- `-q, --quiet` - Suppress progress bars and reduce output

### `mortgage-data umbs silver`

Build the silver layer by merging each (original, correction) bronze pair.

```bash
mortgage-data umbs silver [OPTIONS]
```

**Options:**
- `--overwrite` - Overwrite existing files instead of skipping them
- `--gse TEXT` - Filter to a single GSE: FNMA or FHLMC
- `--kind TEXT` - Filter to canonical kind name(s), e.g. ILLD, IS (repeatable)
- `-v, --verbose` - Enable verbose (DEBUG level) logging output
- `-q, --quiet` - Suppress progress bars and reduce output

### `mortgage-data umbs pipeline`

Run bronze + silver in one step.

```bash
mortgage-data umbs pipeline [OPTIONS]
```

**Options:**
- `--overwrite` - Overwrite existing files instead of skipping them
- `--gse TEXT` - Filter to a single GSE: FNMA or FHLMC
- `-v, --verbose` - Enable verbose (DEBUG level) logging output
- `-q, --quiet` - Suppress progress bars and reduce output

**Examples:**
```bash
# Full pipeline for all GSEs
mortgage-data umbs pipeline

# Rebuild Freddie silver only
mortgage-data umbs silver --gse FHLMC --overwrite
```

---

## FHLB Commands

### `mortgage-data fhlb`

Federal Home Loan Bank (FHLB) data operations.

```bash
mortgage-data fhlb [OPTIONS] COMMAND [ARGS]...
```

**Available Commands:**
- `download` - Download FHLB data files (AMA, members, dictionaries)
- `bronze` - Import FHLB data to bronze layer
- `silver` - Transform FHLB data to silver layer
- `pipeline` - Run complete FHLB data pipelines
- `info` - Display FHLB configuration information

**Options:**
- `--version, -V` - Show version and exit
- `--help` - Show help message

### `mortgage-data fhlb download`

Download FHLB data files.

```bash
mortgage-data fhlb download COMMAND [OPTIONS]
```

**Subcommands:**
- `data` - Download AMA (Acquired Member Assets) CSV files
- `dictionaries` - Download AMA schema dictionary files (PDF, Excel)
- `members` - Download member institution data (Excel files)
- `all` - Download all data types

### `mortgage-data fhlb download data`

Download FHLB AMA data files.

```bash
mortgage-data fhlb download data [OPTIONS]
```

**Options:**
- `-o, --output PATH` - Output directory for downloaded files
- `--overwrite` - Overwrite existing files instead of skipping them
- `--pause SECONDS` - Seconds to pause between downloads (default: 5.0)
- `--timeout SECONDS` - Request timeout in seconds (default: 30)
- `--retries COUNT` - Number of retry attempts for failed downloads (default: 3)
- `-v, --verbose` - Enable verbose (DEBUG level) logging output
- `-q, --quiet` - Suppress progress bars and reduce output

**Examples:**
```bash
# Download to default directory
mortgage-data fhlb download data

# Custom output directory
mortgage-data fhlb download data -o /custom/path

# Force overwrite existing files
mortgage-data fhlb download data --overwrite --verbose
```

### `mortgage-data fhlb download dictionaries`

Download FHLB AMA data dictionary files.

```bash
mortgage-data fhlb download dictionaries [OPTIONS]
```

**Options:**
- `-o, --output PATH` - Output directory
- `--overwrite` - Overwrite existing files instead of skipping them
- `--pause SECONDS` - Seconds to pause between downloads (default: 5.0)
- `--timeout SECONDS` - Request timeout in seconds (default: 30)
- `--retries COUNT` - Number of retry attempts for failed downloads (default: 3)
- `-v, --verbose` - Enable verbose (DEBUG level) logging output
- `-q, --quiet` - Suppress progress bars and reduce output

**Examples:**
```bash
mortgage-data fhlb download dictionaries
mortgage-data fhlb download dictionaries -o /custom/path
```

### `mortgage-data fhlb download members`

Download FHLB membership data files.

```bash
mortgage-data fhlb download members [OPTIONS]
```

**Options:**
- `-o, --output PATH` - Output directory for downloaded files
- `--overwrite` - Overwrite existing files instead of skipping them
- `--pause SECONDS` - Seconds to pause between downloads (default: 5.0)
- `--timeout SECONDS` - Request timeout in seconds (default: 30)
- `--retries COUNT` - Number of retry attempts for failed downloads (default: 3)
- `-v, --verbose` - Enable verbose (DEBUG level) logging output
- `-q, --quiet` - Suppress progress bars and reduce output

**Examples:**
```bash
# Download to default directory
mortgage-data fhlb download members

# Custom output directory
mortgage-data fhlb download members -o /custom/path
```

### `mortgage-data fhlb download all`

Download all FHLB data files (AMA data, membership data, and dictionaries).

```bash
mortgage-data fhlb download all [OPTIONS]
```

**Options:**
- `--ama-dir PATH` - Directory for AMA data files
- `--members-dir PATH` - Directory for members data files
- `--dict-dir PATH` - Directory for dictionary files
- `--overwrite` - Overwrite existing files instead of skipping them
- `--pause SECONDS` - Seconds to pause between downloads (default: 5.0)
- `--timeout SECONDS` - Request timeout in seconds (default: 30)
- `--retries COUNT` - Number of retry attempts for failed downloads (default: 3)
- `-v, --verbose` - Enable verbose (DEBUG level) logging output
- `-q, --quiet` - Suppress progress bars and reduce output

**Examples:**
```bash
mortgage-data fhlb download all
mortgage-data fhlb download all --overwrite --verbose
```

### `mortgage-data fhlb bronze`

Import FHLB data to bronze layer.

```bash
mortgage-data fhlb bronze COMMAND [OPTIONS]
```

**Subcommands:**
- `ama` - Import AMA data to bronze (CSV → Parquet)
- `members` - Import member data to bronze (Excel → Parquet)

### `mortgage-data fhlb bronze ama`

Import FHLB AMA data to bronze layer.

```bash
mortgage-data fhlb bronze ama [OPTIONS]
```

**Options:**
- `--min-year INTEGER` - Minimum year (default: 2009)
- `--max-year INTEGER` - Maximum year (default: 2024)
- `--raw-dir PATH` - Raw data directory
- `-o, --output PATH` - Output directory for bronze parquet files
- `--overwrite` - Overwrite existing files instead of skipping them

**Examples:**
```bash
# Import all available years
mortgage-data fhlb bronze ama

# Import specific year range
mortgage-data fhlb bronze ama --min-year 2020 --max-year 2024

# Force overwrite existing files
mortgage-data fhlb bronze ama --overwrite
```

### `mortgage-data fhlb bronze members`

Import FHLB member data to bronze layer.

```bash
mortgage-data fhlb bronze members [OPTIONS]
```

**Options:**
- `--min-year INTEGER` - Minimum year (default: 2009)
- `--max-year INTEGER` - Maximum year (default: 2024)
- `--raw-dir PATH` - Raw data directory with Excel files
- `-o, --output PATH` - Output directory for bronze parquet files
- `--overwrite` - Overwrite existing files instead of skipping them

**Examples:**
```bash
# Import all available years
mortgage-data fhlb bronze members

# Import specific year range
mortgage-data fhlb bronze members --min-year 2015 --max-year 2023
```

### `mortgage-data fhlb silver`

Transform FHLB data to silver layer.

```bash
mortgage-data fhlb silver COMMAND [OPTIONS]
```

**Subcommands:**
- `ama` - Transform AMA data to silver

### `mortgage-data fhlb silver ama`

Transform FHLB AMA data to silver layer.

```bash
mortgage-data fhlb silver ama [OPTIONS]
```

**Options:**
- `--min-year INTEGER` - Minimum year (default: 2009)
- `--max-year INTEGER` - Maximum year (default: 2024)
- `--bronze-dir PATH` - Bronze data directory
- `-o, --output PATH` - Output directory for silver parquet files
- `--overwrite` - Overwrite existing files instead of skipping them

**Examples:**
```bash
# Transform all available years
mortgage-data fhlb silver ama

# Transform specific year range
mortgage-data fhlb silver ama --min-year 2020 --max-year 2024

# Force overwrite existing files
mortgage-data fhlb silver ama --overwrite
```

### `mortgage-data fhlb pipeline`

Run complete FHLB data pipelines.

```bash
mortgage-data fhlb pipeline COMMAND [OPTIONS]
```

**Subcommands:**
- `ama` - Full AMA pipeline (download → bronze → silver)

### `mortgage-data fhlb pipeline ama`

Run complete FHLB AMA data pipeline.

```bash
mortgage-data fhlb pipeline ama [OPTIONS]
```

**Options:**
- `--min-year INTEGER` - Minimum year (default: 2009)
- `--max-year INTEGER` - Maximum year (default: 2024)
- `--skip-download` - Skip download step (use existing raw files)
- `--skip-bronze` - Skip bronze loading step
- `--skip-silver` - Skip silver transformation step
- `--overwrite` - Overwrite existing files instead of skipping them
- `--pause SECONDS` - Seconds to pause between downloads (default: 5.0)

**Examples:**
```bash
# Run complete pipeline for all years
mortgage-data fhlb pipeline ama

# Run for specific year range
mortgage-data fhlb pipeline ama --min-year 2020 --max-year 2024

# Skip download (use existing raw files)
mortgage-data fhlb pipeline ama --skip-download

# Only run bronze step
mortgage-data fhlb pipeline ama --skip-download --skip-silver

# Force overwrite existing files
mortgage-data fhlb pipeline ama --overwrite
```

### `mortgage-data fhlb info`

Display FHLB configuration information.

```bash
mortgage-data fhlb info
```

**Output includes:**
- Data directory paths (raw, bronze, silver, gold)
- Default year ranges for member and AMA data

---

## Matching Commands

### `mortgage-data match`

Matching workflows for linking records across datasets.

```bash
mortgage-data match [OPTIONS] COMMAND [ARGS]...
```

**Available Commands:**
- `info` - Show information about available matching workflows
- `list-workflows` - List all available matching workflow modules
- `fha-gnma` - Match FHA loans to GNMA disclosure data
- `fha-hmda` - Match FHA loans to HMDA applications
- `fhfa-hmda` - Match FHFA to HMDA (FHFA ↔ HMDA)
- `hmda-fhlb` - HMDA-FHLB matching workflow commands
- `hmda-mbs` - Build master crosswalks (HMDA → FHFA → MBS → UMBS)
- `sellers-purchasers` - Match HMDA loan originations to subsequent purchases
- `mbs-umbs` - MBS-UMBS matching workflow (MBS ↔ UMBS)
- `mbs-fhlb` - MBS-FHLB matching workflow (MBS ↔ FHLB AMA)
- `mbs-fhfa` - MBS-FHFA matching workflow (MBS ↔ FHFA)

Most workflows are themselves command groups (e.g. `info`, `status`, `run`, `validate`). Use `--help` on a workflow to see its subcommands, e.g. `mortgage-data match sellers-purchasers --help`.

### `mortgage-data match info`

Display information about matching workflows.

```bash
mortgage-data match info
```

### `mortgage-data match list-workflows`

List all available matching workflow modules.

```bash
mortgage-data match list-workflows
```

### `mortgage-data match sellers-purchasers`

HMDA Sellers/Purchasers matching workflow for linking loan originations with their subsequent purchases. This is a command group.

```bash
mortgage-data match sellers-purchasers COMMAND [OPTIONS]
```

**Subcommands:**
- `info` - Show configuration and data availability
- `status` - Show data availability and matching status
- `run` - Run the matching pipeline
- `validate` - Run validation analysis on matches

**Options (on `run`):**
- `-r, --round INTEGER` - Specific round to run (1-8 for post2018, 1-2 for pre2018)
- `-m, --min-year INTEGER` - Minimum year to match
- `-M, --max-year INTEGER` - Maximum year to match
- `--pre2018` - Run pre-2018 matching instead of post-2018
- `--data-dir PATH` - Override HMDA silver data directory
- `-o, --output-dir PATH` - Override output directory

**Examples:**
```bash
# Match originations to purchases for 2020-2024
mortgage-data match sellers-purchasers run --min-year 2020 --max-year 2024

# Run a specific round only
mortgage-data match sellers-purchasers run --min-year 2020 --round 3

# Run the pre-2018 matcher
mortgage-data match sellers-purchasers run --pre2018
```

---

## Common Patterns

### Download and Process Workflow

Most data sources follow this pattern:

```bash
# 1. Download raw data
mortgage-data {source} download [OPTIONS]

# 2. Process to bronze (Parquet conversion)
mortgage-data {source} bronze [OPTIONS]

# 3. Process to silver (analysis-ready)
mortgage-data {source} silver [OPTIONS]

# Or run everything at once:
mortgage-data {source} pipeline [OPTIONS]
```

### Check Available Commands

```bash
# See all commands
mortgage-data --help

# See subcommand help
mortgage-data hmda --help
mortgage-data gnma download --help
mortgage-data fhlmc schemas --help
```

### Version Information

```bash
# Package version
mortgage-data version

# Subpackage version (where applicable)
mortgage-data gnma --version
mortgage-data fhlmc --version
```

---

## Shell Completion

### Install Completion

Install shell completion for faster command entry:

```bash
# Bash
mortgage-data --install-completion bash

# Zsh
mortgage-data --install-completion zsh

# Fish
mortgage-data --install-completion fish
```

### Show Completion Script

View the completion script:

```bash
mortgage-data --show-completion
```

---

## Exit Codes

All commands follow standard exit code conventions:

- `0` - Success
- `1` - General error
- `2` - Command line usage error

---

## Environment Variables

Commands respect these environment variables:

- `MORTGAGE_DATA_DIR` - Override default data directory
- `MORTGAGE_OUTPUT_DIR` - Override default output directory
- `{SOURCE}_DATA_DIR` - Override specific data source directory (e.g., `HMDA_DATA_DIR`)

Example:

```bash
# Use custom data directory
export MORTGAGE_DATA_DIR=/mnt/data/mortgage
mortgage-data hmda download --min-year 2024 --max-year 2024
```

---

## Getting Help

### Command-Specific Help

Every command and subcommand supports `--help`:

```bash
mortgage-data --help
mortgage-data hmda --help
mortgage-data gnma download --help
mortgage-data fhlmc schemas list --help
```

### Project Information

```bash
# Show project paths and status
mortgage-data info
```

### Documentation

- **Getting Started**: [getting_started.md](getting_started.md)
- **API Reference**: [api/](api/)
- **Configuration**: [configuration.md](configuration.md)

---

## Examples

### Complete HMDA Workflow

```bash
# 1. Download data
mortgage-data hmda download --min-year 2020 --max-year 2024

# 2. Process to bronze + silver
mortgage-data hmda pipeline post2018 --min-year 2020 --max-year 2024

# 3. Verify
ls data/hmda/silver/loans/post2018/
```

### Complete FHA Pipeline

```bash
# Run everything in one command
mortgage-data fha pipeline both --min-year 2020
```

### GNMA End-to-End

```bash
# Download, process schemas, and transform data
mortgage-data gnma pipeline full monthly
```

### Freddie Mac with Schema Inspection

```bash
# 1. List available schemas
mortgage-data fhlmc schemas list

# 2. Inspect a schema's date columns
mortgage-data fhlmc schemas dates

# 3. Download and process
mortgage-data fhlmc pipeline full --min-year 2020 --max-year 2025
```

### Multi-Source Analysis Setup

```bash
# Download all major sources
mortgage-data hmda download --min-year 2020 --max-year 2024
mortgage-data fha pipeline both --min-year 2020
mortgage-data gnma pipeline data-only monthly
mortgage-data fhlmc pipeline full --min-year 2020 --max-year 2025

# Run matching workflows
mortgage-data match sellers-purchasers run --min-year 2020 --max-year 2024
```

---

## Troubleshooting

### Command Not Found

If `mortgage-data` command is not found:

```bash
# Ensure package is installed
uv pip install -e .

# Or use the module directly
python -m mortgage_data_manager.cli.main --help
```

### Missing Dependencies

If you get import errors:

```bash
# Install dependencies for specific data source
uv pip install -e ".[hmda]"
uv pip install -e ".[fha]"
uv pip install -e ".[mbs]"

# Or install everything
uv pip install -e ".[all]"
```

### Permission Errors

If you get permission errors:

```bash
# Check data directory permissions
ls -la data/

# Create directory if needed
mkdir -p data/
chmod 755 data/
```

---

For more detailed information, see the [Getting Started Guide](getting_started.md) and [API Documentation](api/).
