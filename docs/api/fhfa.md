# FHFA API Reference

API reference for the FHFA (Federal Housing Finance Agency) subpackage.

## Table of Contents

- [Configuration](#configuration)
- [Download](#download)
- [Import (Bronze Layer)](#import-bronze-layer)
- [Import (Silver Layer)](#import-silver-layer)
- [Schemas](#schemas)
- [CLI](#cli)

---

## Configuration

### `FHFAConfig`

Configuration class for FHFA data management.

**Module:** `mortgage_data_manager.fhfa.config`

**Inherits from:** `MortgageDataConfig`

#### Class Attributes

| Attribute | Type | Description | Environment Variable |
|-----------|------|-------------|---------------------|
| `FHFA_DATA_DIR` | `Path` | Root directory for FHFA data | `FHFA_DATA_DIR` |
| `FHFA_RAW_DIR` | `Path` | Raw data directory | `FHFA_RAW_DIR` |
| `FHFA_BRONZE_DIR` | `Path` | Bronze layer directory | `FHFA_BRONZE_DIR` |
| `FHFA_SILVER_DIR` | `Path` | Silver layer directory | `FHFA_SILVER_DIR` |
| `FHFA_GOLD_DIR` | `Path` | Gold layer directory | `FHFA_GOLD_DIR` |
| `FHFA_CLEAN_DIR` | `Path` | Clean data directory (legacy) | `FHFA_CLEAN_DIR` |
| `FHFA_DICTIONARY_DIR` | `Path` | Dictionary files directory | `FHFA_DICTIONARY_DIR` |

#### Methods

##### `get_prefix_dir(stage: Literal["raw", "bronze", "silver", "gold"], prefix: str) -> Path`

Get the prefix sub-directory under an FHFA medallion stage.

**Parameters:**
- `stage`: Medallion stage ('raw', 'bronze', 'silver', or 'gold')
- `prefix`: Data prefix naming the sub-directory (e.g., 'sf_c', 'mf')

**Returns:**
- `Path`: Path to the prefix sub-directory under the stage

**Example:**
```python
from mortgage_data_manager.fhfa.config import FHFAConfig

silver_dir = FHFAConfig.get_prefix_dir('silver', 'sf_c')
print(silver_dir)  # /path/to/data/fhfa/silver/sf_c
```

For the bare stage directory (no prefix), use the per-dir attributes
(`FHFA_SILVER_DIR`, etc.) or the inherited base API
`get_medallion_dir('fhfa', 'silver')`.

##### `ensure_directories() -> None`

Create necessary directories for FHFA data processing.

**Example:**
```python
from mortgage_data_manager.fhfa.config import FHFAConfig

FHFAConfig.ensure_directories()
# Creates: data/fhfa/raw, data/fhfa/bronze, data/fhfa/silver, etc.
```

### `ImportOptions`

Dataclass for common operational options.

**Module:** `mortgage_data_manager.fhfa.config`

**Attributes:**
- `overwrite` (bool): Overwrite existing files (default: False)
- `overwrite_raw_dicts` (bool): Overwrite raw dictionaries (default: False)
- `overwrite_clean_dicts` (bool): Overwrite clean dictionaries (default: False)
- `excel_engine` (str | None): Excel engine for reading (default: 'openpyxl')

---

## Download

### Download Functions

**Module:** `mortgage_data_manager.fhfa.core.download`

Download raw data files and dictionaries from FHFA.

---

## Import (Bronze Layer)

### FHFA Data Import

**Module:** `mortgage_data_manager.fhfa.core.import_data.fhfa`

Import FHFA fixed-width data files to bronze layer (Parquet format).

The bronze layer:
- Converts fixed-width files to Parquet
- Applies column names from data dictionaries
- Preserves raw data with minimal transformation

---

## Import (Silver Layer)

### Silver Layer Transformation

Transform bronze data to cleaned, standardized silver layer data.

Silver layer features:
- Data type standardization
- Missing value handling
- Derived fields
- Hive partitioning by year

---

## Schemas

### Schema Processing Pipeline

**Module:** `mortgage_data_manager.fhfa.schemas`

The schema module provides tools for:
1. Extracting field definitions from PDF dictionaries
2. Parsing Excel data dictionaries
3. Building master schema files

### Key Functions

All schema utilities live in the single `mortgage_data_manager.fhfa.schemas` module:

- `extract_enterprise_pudb_pdfs` — extract PDF dictionaries from enterprise PUDB zip files
- `extract_dictionary_tables_for_year` — parse PDF dictionaries (years < 2024) into CSV
- `convert_excel_dictionary` — parse the 2024+ Excel dictionary into CSV
- `build_master_dictionary_for_type` / `build_all_master_dictionaries` — concatenate per-year dictionaries into master schema files
- `load_master_dictionary` / `get_available_years` — load and query master dictionaries
- `resolve_dictionary_for_data_file` / `infer_year_from_name` — map raw data files to their matching dictionaries

---

## CLI

### Commands

**Module:** `mortgage_data_manager.fhfa.cli`

#### `mortgage-data fhfa info`

Display FHFA configuration information.

```bash
mortgage-data fhfa info
```

#### `mortgage-data fhfa download`

Download FHFA data files and dictionaries.

```bash
# Download all data and dictionaries
mortgage-data fhfa download all

# Download data files only
mortgage-data fhfa download data

# Download dictionaries only
mortgage-data fhfa download dicts
```

#### `mortgage-data fhfa schemas`

Process data dictionaries (PDFs, Excel, master building).

```bash
# Run full schema pipeline
mortgage-data fhfa schemas pipeline

# Parse Excel dictionaries
mortgage-data fhfa schemas excel

# Build master schema
mortgage-data fhfa schemas master
```

#### `mortgage-data fhfa bronze`

Load data to bronze layer (fixed-width to Parquet).

```bash
# Load specific dataset and years
mortgage-data fhfa bronze load -t sf_c --min-year 2023 --max-year 2024

# Load all datasets
mortgage-data fhfa bronze load --all

# List available datasets
mortgage-data fhfa bronze list
```

**Options:**
- `-t, --datasets`: Datasets to load (e.g., sf_c, mf)
- `--min-year`: First year to process
- `--max-year`: Last year to process, inclusive
- `--all`: Process all available datasets
- `--overwrite`: Overwrite existing files

#### `mortgage-data fhfa silver`

Transform data to silver layer (standardization).

```bash
# Transform specific dataset and years
mortgage-data fhfa silver transform -t sf_c --min-year 2023 --max-year 2024

# Transform all datasets
mortgage-data fhfa silver transform --all
```

**Options:**
- `-t, --datasets`: Datasets to transform
- `--min-year`: First year to process
- `--max-year`: Last year to process, inclusive
- `--all`: Process all available datasets
- `--overwrite`: Overwrite existing files

#### `mortgage-data fhfa pipeline`

Run multi-step workflows (end-to-end automation).

```bash
# Run full pipeline
mortgage-data fhfa pipeline full --min-year 2020 --max-year 2024

# Run bronze + silver only (no schemas)
mortgage-data fhfa pipeline data-only -t sf_c --min-year 2020 --max-year 2024
```

**Options:**
- `--min-year`: First year to process
- `--max-year`: Last year to process, inclusive
- `--overwrite`: Overwrite existing files

---

## Complete Example

Full pipeline from download to analysis:

```python
import polars as pl
from mortgage_data_manager.fhfa.config import FHFAConfig, ImportOptions
from mortgage_data_manager.core.logging import configure_logging, get_logger

# Setup logging
configure_logging(level="INFO")
logger = get_logger(__name__)

# Ensure directories exist
FHFAConfig.ensure_directories()

# Configure options
options = ImportOptions(
    overwrite=False,
)

# Process pipeline
logger.info("Processing FHFA data...")

# Step 1: Download data
# (Usually done via CLI)

# Step 2: Load to bronze layer
# import_to_bronze(...)

# Step 3: Transform to silver layer
# transform_to_silver(...)

# Step 4: Read silver data for analysis
silver_path = FHFAConfig.FHFA_SILVER_DIR / "sf_c"
df = pl.scan_parquet(silver_path / "**/*.parquet")

# Example analysis
result = (
    df
    .group_by("year")
    .agg(pl.len().alias("count"))
    .sort("year")
    .collect()
)

print(result)
```

---

## Data Structure

### Directory Layout

```
{FHFA_DATA_DIR}/
├── raw/
│   ├── sf_c/              # Single-family census tract
│   ├── mf/                # Multi-family
│   └── ...
├── bronze/
│   ├── sf_c/              # Parquet files
│   └── ...
├── silver/
│   ├── sf_c/              # Cleaned, standardized
│   │   └── year=YYYY/     # Hive-partitioned
│   └── ...
├── gold/
│   └── ...
└── dictionary_files/
    ├── raw/               # Downloaded PDFs/Excel
    └── clean/             # Parsed schemas
```

### Data Types

The FHFA Enterprise PUDB releases each year of Fannie Mae and Freddie Mac
acquisitions as several companion files. Single-family and multifamily are
published separately, and within each, the same loans are split across two or
three files purely for disclosure-privacy reasons (continuous fields in one
file, bucketed in another, etc.). Record numbers are **per-file row IDs** and
cannot be used to join across files.

#### Single-Family

| File   | FHFA name                             | Years     | Grain        | Typical rows/yr | Use it for |
|--------|---------------------------------------|-----------|--------------|-----------------|------------|
| `sf_a` | Single-Family National File A         | 2008-2024 | Loan         | 1.8M – 4.3M     | Coarse demographic / underserved-area counts when you don't need geography. Rarely the right choice once you have sf_c. |
| `sf_b` | Single-Family National File B         | 2008-2024 | Unit         | 2.0M – 4.8M     | Seller-institution and occupancy analysis for **pre-2018** years (sf_c didn't carry `Type of Seller Institution` until 2018). |
| `sf_c` | Single-Family Census Tract File C     | 2008-2024 | Loan         | 1.8M – 4.5M     | Default. Has tract-level geography and, from 2018+, continuous values for rate, DTI, property value, credit-score model, AUS, etc. |
| `sf_d` | Single-Family National File C         | 2010-2024 | Loan (sample)| 20K – 80K       | Pre-2018 credit-score and product-type breakdowns. **It is a small sample (~2%)**, not the full acquisition — never stack it with sf_c. |

Key gotchas:

- **`sf_a` / `sf_b` are privacy-suppressed bucket files.** LTV, income, race, and
  tract minority share are all small-integer category codes (typically 1-9
  with `9` = missing/exempt), not numbers. Treat them as categorical.
- **`sf_b` is unit-level**, so a duplex is two rows. Aggregate with care when
  combining with the loan-level files.
- **`sf_d` mixes one continuous field with categorical buckets.** Only
  `purchase_price` is a real dollar amount (rounded to the nearest $1,000,
  with `999999999` masked to null on import); `credit_score`, `product_type`,
  `interest_rate_at_origination`, `term_of_mortgage_at_origination`, and
  `amortization_term` are all bucketed codes.
- **`Underserved Areas Indicator`** is present in `sf_a` / `sf_b` only for
  2008-2009 and in 2018+ silver dictionaries; it can be recomputed from
  tract-level income & minority data for missing years.

#### Multifamily

| File             | FHFA name                                      | Grain    | Use it for |
|------------------|------------------------------------------------|----------|------------|
| `mf_property_b`  | Multifamily National File Property-Level B     | Property | National (no-tract) property characteristics with privacy buckets. |
| `mf_unit_b`      | Multifamily National File Unit Class-Level B   | Unit class | Unit-mix breakdowns by affordability/bedroom/tenant-income. Join to `mf_property_b` on `Enterprise Flag` + `Record Number`. |
| `mf_c`           | Multifamily Census Tract File C                | Property | Default for multifamily — has tract geography and continuous fields (rate, UPB, property value, etc.). |

### Medallion Architecture

1. **Raw**: Downloaded fixed-width text files
2. **Bronze**: Converted to Parquet with column names
3. **Silver**: Cleaned, typed, and partitioned by year

---

## Best Practices

1. **Process Schemas First**: Build master schemas before importing data
2. **Use Bronze Layer**: Don't skip the bronze layer
3. **Partition by Year**: Silver layer should be hive-partitioned
4. **Check Disk Space**: FHFA data can be large
5. **Incremental Processing**: Process year by year

## Schema Inventories

### Bronze Layer Schema

This section provides a schema inventory for all FHFA bronze layer datasets.
Each file type shows the column names and data types extracted from the master dictionaries.
The schemas are consistent across years (2008-2024) within each file type.
Metadata columns (year, enterprise, source_file) are added during bronze loading.

#### sf_a

| Column Name | Data Type |
|-------------|----------|
| Enterprise Flag | Int64 |
| Record Number | Int64 |
| Metropolitan Statistical Area (MSA) Code | Int64 |
| 2000 Census Tract - Percent Minority | Int64 |
| Tract Income Ratio | Int64 |
| Borrower Income Ratio | Int64 |
| Loan-to-Value Ratio (LTV) at Origination | Int64 |
| Purpose of Loan | Int64 |
| Federal Guarantee | Int64 |
| Borrower Race or National Origin, and Ethnicity | Int64 |
| Co-Borrower Race or National Origin, and Ethnicity | Int64 |
| Borrower Sex | Int64 |
| Co-Borrower Sex | Int64 |
| Number of Units | Int64 |
| Unit - Affordability Category | Int64 |
| Underserved Areas Indicator | Int64 |
| year | Int32 |
| 2010 Census Tract - Percent Minority | Int64 |
| Loan-to-Value Ratio (LTV) at Origination, or Combined LTV (CLTV) where available | Int64 |
| Loan-to-Value Ratio (LTV) at Origination, or Combined LTV where available | Int64 |
| 2020 Census Tract - Percent Minority | Int64 |

#### sf_b

| Column Name | Data Type |
|-------------|----------|
| Enterprise Flag | Int64 |
| Record Number | Int64 |
| Metropolitan Statistical Area (MSA) Code | Int64 |
| 2020 Census Tract - Percent Minority | Int64 |
| Tract Income Ratio | Int64 |
| Borrower Income Ratio or Rent Affordability Category | Int64 |
| Date of Mortgage Note | Int64 |
| Purpose of Loan | Int64 |
| Federal Guarantee | Int64 |
| Type of Seller Institution | Int64 |
| Borrower Race or National Origin, and Ethnicity | Int64 |
| Co-Borrower Race or National Origin, and Ethnicity | Int64 |
| Borrower Sex | Int64 |
| Co-Borrower Sex | Int64 |
| Occupancy Code | Int64 |
| Number of Units | Int64 |
| Unit - Owner Occupied | Int64 |
| Unit - Affordability Category | Int64 |
| year | Int32 |
| 2010 Census Tract - Percent Minority | Int64 |
| 2000 Census Tract - Percent Minority | Int64 |
| Underserved Areas Indicator | Int64 |

#### sf_c

| Column Name | Data Type |
|-------------|----------|
| Enterprise Flag | Int64 |
| Record Number | Int64 |
| US Postal State Code | Int64 |
| Metropolitan Statistical Area (MSA) Code | Int64 |
| County - 2010 Census | Int64 |
| Census Tract - 2010 Census | Int64 |
| 2010 Census Tract - Percent Minority | Float64 |
| 2010 Census Tract - Median Income | Int64 |
| Local Area Median Income | Int64 |
| Tract Income Ratio | Float64 |
| Borrower's (or Borrowers') Annual Income | Float64 |
| Area Median Family Income (2015) | Int64 |
| Borrower Income Ratio | Float64 |
| Acquisition Unpaid Principal Balance (UPB) | Int64 |
| Purpose of Loan | Int64 |
| Federal Guarantee | Int64 |
| Number of Borrowers | Int64 |
| First-Time Home Buyer | Int64 |
| Borrower Race or National Origin 1 | Int64 |
| Borrower Race or National Origin 2 | Int64 |
| Borrower Race or National Origin 3 | Int64 |
| Borrower Race or National Origin 4 | Int64 |
| Borrower Race or National Origin 5 | Int64 |
| Borrower Ethnicity | Int64 |
| Co-Borrower Race or National Origin 1 | Int64 |
| Co-Borrower Race or National Origin 2 | Int64 |
| Co-Borrower Race or National Origin 3 | Int64 |
| Co-Borrower Race or National Origin 4 | Int64 |
| Co-Borrower Race or National Origin 5 | Int64 |
| Co-Borrower Ethnicity | Int64 |
| Borrower Sex | Int64 |
| Co-Borrower Sex | Int64 |
| Age of Borrower | Int64 |
| Age of Co-Borrower | Int64 |
| Occupancy Code | Int64 |
| Rate Spread | Float64 |
| HOEPA Status | Int64 |
| Property Type | Int64 |
| Lien Status | Int64 |
| year | Int32 |
| County - 2020 Census | Int64 |
| Census Tract - 2020 Census | Int64 |
| 2020 Census Tract - Percent Minority | Float64 |
| 2020 Census Tract - Median Income | Int64 |
| Area Median Family Income (2023) | Int64 |
| Borrower Age 62 or older | Int64 |
| Co-Borrower Age 62 or older | Int64 |
| Loan-to-Value Ratio (LTV) at Origination, or Combined LTV (CLTV) where available | Float64 |
| Date of Mortgage Note | Int64 |
| Term of Mortgage at Origination | Int64 |
| Number of Units | Int64 |
| Interest Rate at Origination | Float64 |
| Note Amount | Int64 |
| Preapproval | Int64 |
| Application Channel | Int64 |
| Automated Underwriting System (AUS) Name | Int64 |
| Credit Score Model - Borrower | Int64 |
| Credit Score Model - Co-Borrower | Int64 |
| Debt-to-Income (DTI) Ratio | Int64 |
| Discount Points | Float64 |
| Introductory Rate Period | Int64 |
| Manufactured Home - Land Property Interest | Int64 |
| Property Value | Int64 |
| Rural Census Tract | Int64 |
| Lower Mississippi Delta County | Int64 |
| Middle Appalachia County | Int64 |
| Persistent Poverty County | Int64 |
| Area of Concentrated Poverty | Int64 |
| High Opportunity Area | Int64 |
| Colonias Tract | Int64 |
| Area Median Family Income (2022) | Int64 |
| Area Median Family Income (2014) | Int64 |
| Area Median Family Income (2020) | Int64 |
| Qualified Opportunity Zone (QOZ) Census Tract | Int64 |
| Area Median Family Income (2016) | Int64 |
| Area Median Family Income (2017) | Int64 |
| Area Median Family Income (2021) | Int64 |
| Area Median Family Income (2024) | Int64 |
| Borrower Race 1 | Int64 |
| Borrower Race 2 | Int64 |
| Borrower Race 3 | Int64 |
| Borrower Race 4 | Int64 |
| Borrower Race 5 | Int64 |
| Area Median Family Income (2012) | Int64 |
| Area Median Family Income (2013) | Int64 |
| County - 2000 Census | Int64 |
| Census Tract - 2000 Census | Int64 |
| 2000 Census Tract - Percent Minority | Float64 |
| 2000 Census Tract - Median Income | Int64 |
| 2000 Local Area Median Income | Int64 |
| Area Median Family Income (2011) | Int64 |
| Area Median Family Income (2018) | Int64 |
| Area Median Family Income (2008) | Int64 |
| Underserved Areas Indicator | Int64 |
| Area Median Family Income (2009) | Int64 |
| Area Median Family Income (2019) | Int64 |
| Area Median Family Income (2010) | Int64 |

#### sf_d

| Column Name | Data Type |
|-------------|----------|
| Enterprise Flag | Int64 |
| Record Number | Int64 |
| 2010 Census Tract - Percent Minority | Int64 |
| Tract Income Ratio | Int64 |
| Borrower Income Ratio | Int64 |
| Loan-to-Value Ratio (LTV) at Origination, or Combined LTV where available | Int64 |
| Purpose of Loan | Int64 |
| Federal Guarantee | Int64 |
| Credit Score | Int64 |
| Product Type | Int64 |
| Purchase Price | Int64 |
| Interest Rate at Origination | Int64 |
| Term of Mortgage at Origination | Int64 |
| Amortization Term | Int64 |
| Portfolio Flag | Int64 |
| Percent Repurchased | Float64 |
| year | Int32 |
| 2020 Census Tract - Percent Minority | Int64 |
| Loan-to-Value Ratio (LTV) at Origination, or Combined LTV (CLTV) where available | Int64 |
| 2000 Census Tract - Percent Minority | Int64 |

#### mf_property_b

| Column Name | Data Type |
|-------------|----------|
| Enterprise Flag | Int64 |
| Record Number | Int64 |
| 2020 Census Tract - Percent Minority | Int64 |
| Tract Income Ratio | Int64 |
| Affordability Category | Int64 |
| Date of Mortgage Note | Int64 |
| Purpose of Loan | Int64 |
| Type of Seller Institution | Int64 |
| Federal Guarantee | Int64 |
| Total Number of Units | String |
| year | Int32 |
| 2010 Census Tract - Percent Minority | Int64 |
| 2000 Census Tract - Percent Minority | Int64 |
| Underserved Areas Indicator | Int64 |

#### mf_unit_b

| Column Name | Data Type |
|-------------|----------|
| Enterprise Flag | Int64 |
| Record Number | Int64 |
| Unit Type XX-Number of Bedrooms | Int64 |
| Unit Type XX-Number of Units | String |
| Unit Type XX-Affordability Level | Int64 |
| year | Int32 |
| Unit Type XX-Tenant Income Indicator | Int64 |

#### mf_c

| Column Name | Data Type |
|-------------|----------|
| Enterprise Flag | Int64 |
| Record Number | Int64 |
| US Postal State Code | Int64 |
| Metropolitan Statistical Area (MSA) Code | Int64 |
| County - 2020 Census | Int64 |
| Census Tract - 2020 Census | Int64 |
| 2020 Census Tract - Percent Minority | Float64 |
| 2020 Census Tract - Median Income | Int64 |
| Local Area Median Income | Int64 |
| Tract Income Ratio | Float64 |
| Area Median Family Income (2024) | Int64 |
| Acquisition Unpaid Principal Balance (UPB) | Float64 |
| Purpose of Loan | Int64 |
| Type of Seller Institution | Int64 |
| Federal Guarantee | Int64 |
| Lien Status | String |
| Loan-to-Value Ratio (LTV) at Origination, or Combined LTV (CLTV) where available | Float64 |
| Date of Mortgage Note | Int64 |
| Term of Mortgage at Origination | Int64 |
| Number of Units | Int64 |
| Interest Rate at Origination | Float64 |
| Note Amount | Int64 |
| Property Value | Float64 |
| Prepayment Penalty Term | Int64 |
| Non-fully Amortizing Feature - Balloon | Int64 |
| Non-fully Amortizing Feature - Interest-only | Int64 |
| Non-fully Amortizing Feature - Negative Amortization | Int64 |
| Non-fully Amortizing Feature - Other | Int64 |
| Multifamily Affordable Units - Percent | Int64 |
| Construction Method | Int64 |
| Rural Census Tract | Int64 |
| Lower Mississippi Delta County | Int64 |
| Middle Appalachia County | Int64 |
| Persistent Poverty County | Int64 |
| Area of Concentrated Poverty | Int64 |
| High Opportunity Area | Int64 |
| Colonias Tract | Int64 |
| year | Int32 |
| County - 2010 Census | Int64 |
| Census Tract - 2010 Census | Int64 |
| 2010 Census Tract - Percent Minority | Float64 |
| 2010 Census Tract - Median Income | Int64 |
| Area Median Family Income (2012) | Int64 |
| Area Median Family Income (2013) | Int64 |
| County - 2000 Census | Int64 |
| Census Tract - 2000 Census | Int64 |
| 2000 Census Tract - Percent Minority | Float64 |
| 2000 Census Tract - Median Income | Int64 |
| 2000 Local Area Median Income | Int64 |
| Area Median Family Income (2011) | Int64 |
| Area Median Family Income (2018) | Int64 |
| Qualified Opportunity Zone (QOZ) Census Tract | Int64 |
| Area Median Family Income (2008) | Int64 |
| Underserved Areas Indicator | Int64 |
| Area Median Family Income (2009) | Int64 |
| Area Median Family Income (2019) | Int64 |
| Area Median Family Income (2010) | Int64 |
| Area Median Family Income (2015) | Int64 |
| Area Median Family Income (2023) | Int64 |
| Area Median Family Income (2022) | Int64 |
| Area Median Family Income (2014) | Int64 |
| Area Median Family Income (2020) | Int64 |
| Area Median Family Income (2016) | Int64 |
| Area Median Family Income (2017) | Int64 |
| Area Median Family Income (2021) | Int64 |

### 2024 Standardized Header Names

The 2024 FHFA data dictionaries include standardized header names.
These provide a reference for clean column naming in silver layer transformations.

#### Multifamily_Census_Tract_File_C

| Header Name | Field Name |
|-------------|------------|
| enterprise | Enterprise Flag |
| record_num_mf_ctf | Record Number |
| state_fips | US Postal State Code |
| cbsa_metro_code | Metropolitan Statistical Area (MSA) Code |
| county_fips | County - 2020 Census |
| tract_2020 | Census Tract - 2020 Census |
| tract_minority_pct | 2020 Census Tract - Percent Minority |
| tract_income_med | 2020 Census Tract - Median Income |
| ami_local | Local Area Median Income |
| tract_income_ratio | Tract Income Ratio |
| ami_hud | Area Median Family Income (2024) |
| upb_acq | Acquisition Unpaid Principal Balance (UPB) |
| purpose_ctf | Purpose of Loan |
| seller_type_mf_ctf | Type of Seller Institution |
| fed_guarantee_ctf | Federal Guarantee |
| lien_status | Lien Status |
| ltv | Loan-to-Value Ratio (LTV) at Origination, or Combined LTV (CLTV) where available |
| same_year_acq | Date of Mortgage Note |
| term_orig | Term of Mortgage at Origination |
| units_num_cat | Number of Units |
| rate_orig | Interest Rate at Origination |
| upb_orig | Note Amount |
| property_value | Property Value |
| term_prepay_penalty | Prepayment Penalty Term |
| balloon | Non-fully Amortizing Feature - Balloon |
| io | Non-fully Amortizing Feature - Interest-only |
| neg_am | Non-fully Amortizing Feature - Negative Amortization |
| non_amort_other | Non-fully Amortizing Feature - Other |
| afford_units_pct | Multifamily Affordable Units - Percent |
| construct_method | Construction Method |
| tract_rural | Rural Census Tract |
| county_lower_ ms_delta | Lower Mississippi Delta County |
| county_mid_ appalachia | Middle Appalachia County |
| county_persistent_ poverty | Persistent Poverty County |
| area_concentrated_ poverty | Area of Concentrated Poverty |
| area_high_opp | High Opportunity Area |
| tract_colonias | Colonias Tract |

#### Multifamily_National_File_Property-Level_Data_File_B

| Header Name | Field Name |
|-------------|------------|
| enterprise | Enterprise Flag |
| record_num_mf_nf | Record Number |
| tract_minority_cat | 2020 Census Tract - Percent Minority |
| tract_income_cat | Tract Income Ratio |
| afford_mf | Affordability Category |
| same_year_acq | Date of Mortgage Note |
| purpose_mf_nf | Purpose of Loan |
| seller_type_mf_nf | Type of Seller Institution |
| fed_guarantee_mf_nf | Federal Guarantee |
| units_num_cat | Total Number of Units |

#### Multifamily_National_File_Unit_Class-Level_Data_File_B

| Header Name | Field Name |
|-------------|------------|
| enterprise | Enterprise Flag |
| record_num_mf_nf | Record Number |
| type_bed_num | Unit Type XX-Number of Bedrooms |
| type_units_num | Unit Type XX-Number of Units |
| type_afford_cat | Unit Type XX-Affordability Level |
| type_tenant_inc_basis | Unit Type XX-Tenant Income Indicator |

#### Single_Family_Census_Tract_File_C

| Header Name | Field Name |
|-------------|------------|
| enterprise | Enterprise Flag |
| record_num_sf_ctf | Record Number |
| state_fips | US Postal State Code |
| cbsa_metro_code | Metropolitan Statistical Area (MSA) Code |
| county_fips | County - 2020 Census |
| tract_2020 | Census Tract - 2020 Census |
| tract_minority_pct | 2020 Census Tract - Percent Minority |
| tract_income_med | 2020 Census Tract - Median Income |
| ami_local | Local Area Median Income |
| tract_income_ratio | Tract Income Ratio |
| income_annual | Borrower's (or Borrowers') Annual Income |
| ami_hud | Area Median Family Income (2024) |
| income_ratio | Borrower Income Ratio |
| upb_acq | Acquisition Unpaid Principal Balance (UPB) |
| purpose_ctf | Purpose of Loan |
| fed_guarantee_ctf | Federal Guarantee |
| borr_num | Number of Borrowers |
| fthb | First-Time Home Buyer |
| race1_borr, race2_borr, race3_borr, race4_borr, race5_borr | Borrower Race 1-5 |
| ethnicity_borr | Borrower Ethnicity |
| race1_coborr, race2_coborr, race3_coborr, race4_coborr, race5_coborr | Co-Borrower Race or National Origin 1-5 |
| ethnicity_coborr | Co-Borrower Ethnicity |
| sex_borr | Borrower Sex |
| sex_coborr | Co-Borrower Sex |
| age_borr_cat | Age of Borrower |
| age_coborr_cat | Age of Co-Borrower |
| occupancy_sf_ctf | Occupancy Code |
| rate_spread | Rate Spread |
| hoepa | HOEPA Status |
| property_type | Property Type |
| lien_status | Lien Status |
| age_borr_62_cat | Borrower Age 62 or older |
| age_coborr_62_cat | Co-Borrower Age 62 or older |
| ltv | Loan-to-Value Ratio (LTV) at Origination, or Combined LTV (CLTV) where available |
| same_year_acq | Date of Mortgage Note |
| term_orig | Term of Mortgage at Origination |
| units_num | Number of Units |
| rate_orig | Interest Rate at Origination |
| upb_orig | Note Amount |
| preapproval | Preapproval |
| channel_apply | Application Channel |
| aus | Automated Underwriting System (AUS) Name |
| score_borr_model | Credit Score Model - Borrower |
| score_coborr_model | Credit Score Model - Co-Borrower |
| dti_cat | Debt-to-Income (DTI) Ratio |
| points | Discount Points |
| period_intro_rate | Introductory Rate Period |
| mh_land_interest | Manufactured Home - Land Property Interest |
| property_value | Property Value |
| tract_rural | Rural Census Tract |
| county_lower_ ms_delta | Lower Mississippi Delta County |
| county_mid_ appalachia | Middle Appalachia County |
| county_persistent_ poverty | Persistent Poverty County |
| area_concentrated_ poverty | Area of Concentrated Poverty |
| area_high_opp | High Opportunity Area |
| tract_colonias | Colonias Tract |

#### Single_Family_National_File_A

| Header Name | Field Name |
|-------------|------------|
| enterprise | Enterprise Flag |
| record_num_sf_nfa | Record Number |
| metro | Metropolitan Statistical Area (MSA) Code |
| tract_minority_cat | 2020 Census Tract - Percent Minority |
| tract_income_cat | Tract Income Ratio |
| income_cat | Borrower Income Ratio |
| ltv_cat | Loan-to-Value Ratio (LTV) at Origination, or Combined LTV (CLTV) where available |
| purpose_sf_nfa | Purpose of Loan |
| fed_guarantee_sf_nfa | Federal Guarantee |
| race_ethnicity_borr | Borrower Race or National Origin, and Ethnicity |
| race_ethnicity_coborr | Co-Borrower Race or National Origin, and Ethnicity |
| sex_borr | Borrower Sex |
| sex_coborr | Co-Borrower Sex |
| units_num | Number of Units |
| afford_sf | Unit - Affordability Category |

#### Single_Family_National_File_B

| Header Name | Field Name |
|-------------|------------|
| enterprise | Enterprise Flag |
| record_num_sf_nfb | Record Number |
| metro | Metropolitan Statistical Area (MSA) Code |
| tract_minority_cat | 2020 Census Tract - Percent Minority |
| tract_income_cat | Tract Income Ratio |
| income_cat | Borrower Income Ratio or Rent Affordability Category |
| same_year_acq | Date of Mortgage Note |
| purpose_sf_nfb | Purpose of Loan |
| fed_guarantee_sf_nfb | Federal Guarantee |
| seller_type_sf_nfb | Type of Seller Institution |
| race_ethnicity_borr | Borrower Race or National Origin, and Ethnicity |
| race_ethnicity_coborr | Co-Borrower Race or National Origin, and Ethnicity |
| sex_borr | Borrower Sex |
| sex_coborr | Co-Borrower Sex |
| occupancy_sf_nfb | Occupancy Code |
| units_num | Number of Units |
| unit_own_occ | Unit - Owner Occupied |
| afford_sf | Unit - Affordability Category |

#### Single_Family_National_File_C

| Header Name | Field Name |
|-------------|------------|
| enterprise | Enterprise Flag |
| record_num_sf_nfc | Record Number |
| tract_minority_cat | 2020 Census Tract - Percent Minority |
| tract_income_cat | Tract Income Ratio |
| income_cat | Borrower Income Ratio |
| ltv_cat | Loan-to-Value Ratio (LTV) at Origination, or Combined LTV (CLTV) where available |
| purpose_sf_nfc | Purpose of Loan |
| fed_guarantee_sf_nfc | Federal Guarantee |
| score_cat | Credit Score |
| product | Product Type |
| purchase_price | Purchase Price |
| rate_orig_cat | Interest Rate at Origination |
| term_orig_cat | Term of Mortgage at Origination |
| term_amort_cat | Amortization Term |
| portfolio | Portfolio Flag |
| repurchased_pct | Percent Repurchased |

### Silver Layer Schema

This section provides a schema inventory for FHFA silver layer datasets.
The schemas are consistent across years (2008-2024) within each file type.

#### sf_c (Silver)

| Column Name | Data Type |
|-------------|----------|
| year | Int32 |
| Census Year | Int32 |
| tract_median_income | Int64 |
| tract_percent_minority | Float64 |
| upb_acquisition | Int64 |
| age_borrower | Int64 |
| age_co_borrower | Int64 |
| area_median_family_income | Int64 |
| borrower_ethnicity | Int64 |
| borrower_income_ratio | Float64 |
| borrower_race_1 | Int64 |
| borrower_race_2 | Int64 |
| borrower_race_3 | Int64 |
| borrower_race_4 | Int64 |
| borrower_race_5 | Int64 |
| borrower_sex | Int64 |
| borrower_annual_income | Float64 |
| census_tract | Int64 |
| co_borrower_ethnicity | Int64 |
| co_borrower_race_1 | Int64 |
| co_borrower_race_2 | Int64 |
| co_borrower_race_3 | Int64 |
| co_borrower_race_4 | Int64 |
| co_borrower_race_5 | Int64 |
| co_borrower_sex | Int64 |
| county | Int64 |
| enterprise_flag | Int64 |
| federal_guarantee | Int64 |
| first_time_home_buyer | Int64 |
| hoepa_status | Int64 |
| lien_status | Int64 |
| local_area_median_income | Int64 |
| msa_code | Int64 |
| number_of_borrowers | Int64 |
| occupancy_code | Int64 |
| property_type | Int64 |
| loan_purpose | Int64 |
| rate_spread | Float64 |
| record_number | Int64 |
| tract_income_ratio | Float64 |
| state_code | Int64 |
| application_channel | Int64 |
| area_concentrated_poverty | Int64 |
| aus_name | Int64 |
| borrower_age_62_plus | Int64 |
| co_borrower_age_62_plus | Int64 |
| colonias_tract | Int64 |
| credit_score_model_borrower | Int64 |
| credit_score_model_co_borrower | Int64 |
| date_of_mortgage_note | Int64 |
| dti_ratio | Int64 |
| discount_points | Float64 |
| high_opportunity_area | Int64 |
| interest_rate_at_origination | Float64 |
| introductory_rate_period | Int64 |
| ltv_at_origination | Float64 |
| lower_mississippi_delta_county | Int64 |
| manufactured_home_land_property_interest | Int64 |
| middle_appalachia_county | Int64 |
| note_amount | Int64 |
| number_of_units | Int64 |
| persistent_poverty_county | Int64 |
| preapproval | Int64 |
| property_value | Int64 |
| rural_census_tract | Int64 |
| loan_term | Int64 |
| qoz_census_tract | Int64 |
| underserved_areas_indicator | Int64 |

---

## See Also

- [Core API](core.md) - Shared utilities and medallion architecture
- [Configuration Guide](../configuration.md) - Environment variables and paths
