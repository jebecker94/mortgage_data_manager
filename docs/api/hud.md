# HUD Crosswalk API Reference

API reference for the HUD (Department of Housing and Urban Development) USPS ZIP Code Crosswalk subpackage.

## Table of Contents

- [Overview](#overview)
- [Crosswalk Types](#crosswalk-types)
- [Census Vintage Boundaries](#census-vintage-boundaries)
- [Configuration](#configuration)
- [Download](#download)
- [Loading Data](#loading-data)
- [Allocation & Aggregation](#allocation--aggregation)
- [Processing (Raw to Bronze)](#processing-raw-to-bronze)
- [Validation](#validation)
- [Migration](#migration)
- [CLI](#cli)
- [Data Structure](#data-structure)
- [Common Pitfalls](#common-pitfalls)
- [Best Practices](#best-practices)

---

## Overview

The HUD subpackage provides tools for downloading, processing, and using HUD USPS ZIP Code Crosswalk files. These crosswalks map between ZIP codes and Census geographies (tracts, counties, CBSAs, congressional districts, and county subdivisions) using address-count ratios from the USPS.

The crosswalks are published quarterly by HUD and are essential for:

- Converting between ZIP-level and tract-level data (e.g., IRS income data to ACS-compatible tracts)
- Bridging datasets that use different geographic identifiers (ZIP vs census tract)
- Geographic allocation of aggregate statistics
- Supporting matching workflows (e.g., FHA-HMDA matching uses ZIP-tract crosswalks to bridge FHA ZIP codes with HMDA census tracts)

### Data Pipeline

The HUD pipeline uses a simplified medallion architecture:

1. **Raw**: Individual quarterly Parquet files downloaded from the HUD API (one file per type/year/quarter)
2. **Bronze**: Combined longitudinal files (optional, for batch analysis)

There is no silver or gold layer — the crosswalks are reference data, not transactional data, so the raw layer is the primary source of truth.

---

## Crosswalk Types

HUD publishes 12 crosswalk types organized into 6 bidirectional pairs:

| ID | Name | From | To | Available From |
|----|------|------|----|----------------|
| 1 | `ZIP_TRACT` | ZIP | Census Tract | 2010 Q1 |
| 2 | `ZIP_COUNTY` | ZIP | County | 2010 Q1 |
| 3 | `ZIP_CBSA` | ZIP | Core-Based Statistical Area | 2010 Q1 |
| 4 | `ZIP_CBSA_DIV` | ZIP | CBSA Division | 2017 Q4 |
| 5 | `ZIP_CD` | ZIP | Congressional District | 2010 Q1 |
| 6 | `TRACT_ZIP` | Census Tract | ZIP | 2010 Q1 |
| 7 | `COUNTY_ZIP` | County | ZIP | 2010 Q1 |
| 8 | `CBSA_ZIP` | CBSA | ZIP | 2010 Q1 |
| 9 | `CBSA_DIV_ZIP` | CBSA Division | ZIP | 2017 Q4 |
| 10 | `CD_ZIP` | Congressional District | ZIP | 2010 Q1 |
| 11 | `ZIP_COUNTY_SUB` | ZIP | County Subdivision | 2018 Q2 |
| 12 | `COUNTY_SUB_ZIP` | County Subdivision | ZIP | 2018 Q2 |

### Why Both Directions?

Both directions contain the same geographic pairs, but the **ratio denominators differ**:

- **`ZIP_TRACT`**: Ratios represent the share of each ZIP's addresses in each tract. For a given ZIP, ratios sum to 1.0.
- **`TRACT_ZIP`**: Ratios represent the share of each tract's addresses in each ZIP. For a given tract, ratios sum to 1.0.

Use `ZIP_TRACT` when disaggregating ZIP-level data to tracts; use `TRACT_ZIP` when disaggregating tract-level data to ZIPs.

### Ratio Columns

Each crosswalk row includes four allocation ratios:

| Column | Description | Use For |
|--------|-------------|---------|
| `res_ratio` | Residential address share | Population, housing, demographic data |
| `bus_ratio` | Business address share | Employment, commercial, economic data |
| `oth_ratio` | Other address share | PO boxes, group quarters |
| `tot_ratio` | Total address share | General purpose |

For a given source geography, the ratios within each column sum to approximately 1.0.

---

## Census Vintage Boundaries

HUD crosswalks use different Census geography definitions depending on the time period. Tract IDs are **not** comparable across vintages — the same numeric ID may refer to different geographic boundaries.

| Vintage | Period | Notes |
|---------|--------|-------|
| 2000 Census | 2010 Q1 – 2011 Q4 | Original tract definitions |
| 2010 Census | 2012 Q1 – 2022 Q4 | Tracts redrawn after 2010 Census |
| 2020 Census | 2023 Q1 onwards | Tracts redrawn after 2020 Census |

**Critical**: Never mix data from different census vintages in the same analysis without an explicit tract-to-tract vintage crosswalk.

```python
from mortgage_data_manager.hud import get_vintage_for_crosswalk

get_vintage_for_crosswalk(2015, 1)  # 2010
get_vintage_for_crosswalk(2023, 3)  # 2020
```

---

## Configuration

### `HUDConfig`

Configuration class for HUD crosswalk data management.

**Module:** `mortgage_data_manager.hud.config`

**Inherits from:** `MortgageDataConfig`

#### Class Attributes

| Attribute | Type | Description | Environment Variable |
|-----------|------|-------------|---------------------|
| `HUD_DATA_DIR` | `Path` | Root directory for HUD data | `HUD_DATA_DIR` |
| `HUD_RAW_DIR` | `Path` | Raw data directory | `HUD_RAW_DIR` |
| `HUD_BRONZE_DIR` | `Path` | Bronze layer directory | `HUD_BRONZE_DIR` |
| `HUD_API_BASE_URL` | `str` | HUD API endpoint URL | — |
| `HUD_API_KEY` | `str` | API bearer token | `HUD_API_KEY` |
| `HUD_REQUEST_DELAY` | `float` | Seconds between API requests (1.0) | — |

#### Example

```python
from mortgage_data_manager.hud.config import HUDConfig

print(HUDConfig.HUD_RAW_DIR)     # data/hud/raw
print(HUDConfig.HUD_BRONZE_DIR)  # data/hud/bronze

HUDConfig.ensure_directories()
```

### API Key Setup

Register for a free API key at [HUD USER](https://www.huduser.gov/hudapi/public/register?comingfrom=1) and set it in your `.env` file:

```bash
HUD_API_KEY=your_token_here
```

---

## Download

### `download_all()`

Downloads all 12 crosswalk types from the HUD API, from 2010 to the current quarter.

**Module:** `mortgage_data_manager.hud.download`

```python
from mortgage_data_manager.hud.download import download_all

download_all()                        # Download everything (skips existing)
download_all(overwrite=True)          # Re-download all files
download_all(min_year=2021)         # Only download 2021+
```

**Note:** The API `query=All` parameter only works reliably for 2021+ data. Pre-2021 requests may return 400 errors. If you need pre-2021 data, you may need to download it state-by-state or use the HUD USER web interface.

### `download_crosswalk()`

Downloads a single crosswalk file for a specific type/year/quarter.

```python
from mortgage_data_manager.hud.download import download_crosswalk

data = download_crosswalk(type_id=1, year=2023, quarter=1, token="your_key")
```

---

## Loading Data

All loading functions return Polars `LazyFrame` objects for memory-efficient query building.

**Module:** `mortgage_data_manager.hud.load`

### `load_crosswalk()`

Load crosswalk data with optional year/quarter range filtering.

```python
from mortgage_data_manager.hud import load_crosswalk

# Load a year range
lf = load_crosswalk("ZIP_TRACT", min_year=2015, max_year=2020)

# Load with quarter precision
lf = load_crosswalk("ZIP_TRACT", min_year=2018, min_quarter=2, max_year=2019, max_quarter=3)

# Collect to materialize
df = lf.collect()
```

### `get_crosswalk_for_date()`

Load crosswalk data for exactly one quarter.

```python
from mortgage_data_manager.hud import get_crosswalk_for_date

lf = get_crosswalk_for_date("ZIP_TRACT", year=2020, quarter=1)
df = lf.collect()  # 172,121 rows
```

### `load_crosswalk_by_vintage()`

Load all data for a specific census geography vintage. Prevents accidentally mixing census geographies.

```python
from mortgage_data_manager.hud import load_crosswalk_by_vintage

# All ZIP_TRACT data using 2010 census tracts (2012 Q1 – 2022 Q4)
lf = load_crosswalk_by_vintage("ZIP_TRACT", vintage=2010)
df = lf.collect()  # ~7.4M rows
```

### `get_vintage_for_crosswalk()`

Returns the census vintage for a given year/quarter.

```python
from mortgage_data_manager.hud import get_vintage_for_crosswalk

get_vintage_for_crosswalk(2019, 1)  # 2010
get_vintage_for_crosswalk(2023, 1)  # 2020
```

---

## Allocation & Aggregation

Functions for redistributing data between geographies using crosswalk ratios.

**Module:** `mortgage_data_manager.hud.allocate`

### Disaggregation

Splits source geography values across target geographies using ratios.

```python
from mortgage_data_manager.hud import allocate_to_tracts, allocate_to_zips
import polars as pl

# ZIP → Tract: distribute ZIP populations to tracts
zip_data = pl.DataFrame({"zip": ["10001", "10002"], "population": [20000, 30000]})
tract_data = allocate_to_tracts(zip_data, "population", year=2020, quarter=1)

# Tract → ZIP: distribute tract demographics to ZIPs
tract_data = pl.DataFrame({"tract": ["36061000100"], "median_income": [75000]})
zip_data = allocate_to_zips(tract_data, "median_income", year=2020, quarter=1)
```

### Aggregation

Allocates then groups by target geography to produce one row per target.

```python
from mortgage_data_manager.hud import aggregate_to_tracts, aggregate_to_zips

# Sum ZIP populations by tract
tract_pop = aggregate_to_tracts(zip_data, "population", 2020, 1, agg_method="sum")

# Weighted mean of tract incomes by ZIP
zip_income = aggregate_to_zips(tract_data, "median_income", 2020, 1, agg_method="weighted_mean")
```

### Choosing the Right Ratio

| Data Type | Ratio Column | Examples |
|-----------|-------------|----------|
| Residential/demographic | `res_ratio` | Population, housing units, ACS variables |
| Business/economic | `bus_ratio` | Employment counts, commercial permits |
| General purpose | `tot_ratio` | When data type is mixed or unclear |

### Extensive vs Intensive Variables

| Variable Type | Aggregation Method | Examples |
|---------------|-------------------|----------|
| Extensive (counts, totals) | `agg_method="sum"` | Population, housing units, jobs |
| Intensive (rates, averages) | `agg_method="weighted_mean"` | Median income, vacancy rate, price index |

---

## Processing (Raw to Bronze)

Combines quarterly raw files into unified longitudinal datasets.

**Module:** `mortgage_data_manager.hud.import_bronze`

```python
from mortgage_data_manager.hud.import_bronze import process_all, process_crosswalk_type

# Process all available types
process_all(min_year=2010, max_year=2025)

# Process a single type with optional CSV/Stata output
process_crosswalk_type("ZIP_TRACT", min_year=2015, max_year=2024, save_csv=True)
```

The ZIP_TRACT type also produces a "rounded" variant that truncates ZIP codes to 3-digit prefixes for broader geographic matching.

---

## Validation

Checks data integrity: file coverage, geographic code formats, and ratio consistency.

**Module:** `mortgage_data_manager.hud.validate`

```python
from mortgage_data_manager.hud.validate import validate_crosswalk

result = validate_crosswalk("ZIP_TRACT")
print(result)  # Shows missing files, geo code issues, ratio issues
print(result.is_valid)  # True/False
```

### Validation Checks

| Check | What It Verifies |
|-------|-----------------|
| File coverage | All expected quarterly files exist |
| Geographic codes | String types, correct lengths, numeric-only characters |
| Ratio sums | Ratios sum to ~1.0 per source geography |

---

## Migration

Converts between flat and Hive-partitioned storage formats.

**Module:** `mortgage_data_manager.hud.migrate`

```python
from mortgage_data_manager.hud.migrate import migrate_all, detect_storage_format

# Check current format
fmt = detect_storage_format(HUDConfig.HUD_RAW_DIR / "ZIP_TRACT")  # "flat", "hive", "mixed", "empty"

# Migrate raw to bronze with Hive partitioning
migrate_all(
    input_dir=HUDConfig.HUD_RAW_DIR,
    output_dir=HUDConfig.HUD_BRONZE_DIR,
    dry_run=True,  # Preview first
)
```

---

## CLI

**Module:** `mortgage_data_manager.hud.cli.main`

### `mortgage-data hud download`

Download crosswalk data from the HUD API.

```bash
mortgage-data hud download
mortgage-data hud download --overwrite
mortgage-data hud download --min-year 2021 --verbose
```

### `mortgage-data hud bronze`

Process raw files into combined bronze datasets.

```bash
mortgage-data hud bronze
mortgage-data hud bronze --datasets ZIP_TRACT --datasets TRACT_ZIP
mortgage-data hud bronze --min-year 2015 --max-year 2024 --csv
```

### `mortgage-data hud validate`

Validate data integrity.

```bash
mortgage-data hud validate
mortgage-data hud validate ZIP_TRACT
mortgage-data hud validate all --skip-ratios
```

### `mortgage-data hud migrate`

Migrate between storage formats.

```bash
mortgage-data hud migrate status
mortgage-data hud migrate migrate all --dry-run
mortgage-data hud migrate migrate ZIP_TRACT --output-dir bronze
mortgage-data hud migrate rollback ZIP_TRACT
```

### `mortgage-data hud info`

Display configuration and crosswalk type information.

```bash
mortgage-data hud info
```

---

## Data Structure

### Raw Layer

- **Format**: Flat Parquet files, one per type/year/quarter
- **Naming**: `{TYPE}_{YEAR}_Q{QUARTER}.parquet` (e.g., `ZIP_TRACT_2020_Q1.parquet`)
- **Location**: `{HUD_RAW_DIR}/{TYPE}/`
- **Schema**: Geographic codes as zero-padded strings, ratios as floats, plus `year`, `quarter`, `type_id`, `type_name` metadata columns

### Bronze Layer

- **Format**: Hive-partitioned Parquet or combined longitudinal files
- **Structure**: `{TYPE}/year={YEAR}/quarter={QUARTER}/data.parquet`
- **Location**: `{HUD_BRONZE_DIR}/{TYPE}/`

### Schema

| Column | Type | Description |
|--------|------|-------------|
| `zip` | `str` | 5-digit ZIP code (zero-padded) |
| `geoid` | `str` | Target geography ID (length varies by type) |
| `res_ratio` | `f64` | Residential address ratio |
| `bus_ratio` | `f64` | Business address ratio |
| `oth_ratio` | `f64` | Other address ratio |
| `tot_ratio` | `f64` | Total address ratio |
| `year` | `i64` | Data release year |
| `quarter` | `i64` | Data release quarter (1–4) |
| `type_id` | `i64` | Crosswalk type ID (1–12) |
| `type_name` | `str` | Crosswalk type name |

### Geographic Code Lengths

| Geography | Column | Length | Format |
|-----------|--------|--------|--------|
| ZIP | `zip` | 5 | ZZZZZ |
| Census Tract | `tract`/`geoid` | 11 | SS-CCC-TTTTTT (state-county-tract) |
| County | `county`/`geoid` | 5 | SS-CCC (state-county) |
| CBSA | `cbsa`/`geoid` | 5 | NNNNN |
| Congressional District | `cd`/`geoid` | 4 | SS-DD (state-district) |
| County Subdivision | `countysub`/`geoid` | 10 | SS-CCC-SSSSS |

---

## Common Pitfalls

### Mixing Census Vintages

Tract `36061000100` in 2015 (2010 Census) may have entirely different boundaries than `36061000100` in 2023 (2020 Census). Always use `load_crosswalk_by_vintage()` or check vintages with `get_vintage_for_crosswalk()` when working with tracts across time.

### Pre-2021 API Limitations

The HUD API's `query=All` parameter only works reliably for 2021+ data. Downloads for earlier years may fail with 400 errors. Check file coverage with `mortgage-data hud validate` after downloading.

### Allocating Intensive Variables with Sum

Using `agg_method="sum"` on rates or averages (e.g., median income) produces meaningless results. Use `"weighted_mean"` instead.

### ZIP Codes Are Not Geographies

ZIP codes are mail delivery routes, not areas. A ZIP can span multiple counties and states. The crosswalk ratios handle this, but be aware that ZIP-level analysis has inherent geographic imprecision.

---

## Best Practices

1. **Stay within vintage**: Don't mix 2010 and 2020 census tracts in the same analysis
2. **Match your data**: Use crosswalks from the same period as your source data
3. **Choose the right ratio**: `res_ratio` for demographic data, `bus_ratio` for economic data
4. **Use lazy evaluation**: Build queries with `load_crosswalk()` and filter before calling `.collect()`
5. **Validate after downloading**: Run `mortgage-data hud validate` to catch gaps
6. **Use counties for long time series**: County boundaries are more stable than tracts across census vintages

---

## See Also

- [Core API](core.md) — Shared utilities and medallion architecture
- [Configuration Guide](../configuration.md) — Environment variables and paths
- [FHA-HMDA Matching](../matching/fha_hmda_matching.md) — Uses HUD ZIP-tract crosswalks to bridge FHA and HMDA geographies
- [HUD USPS Crosswalk Files](https://www.huduser.gov/portal/datasets/usps_crosswalk.html) — Official data documentation
- [HUD API Registration](https://www.huduser.gov/hudapi/public/register?comingfrom=1) — Get an API key
