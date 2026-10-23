# USPS Crosswalk API Reference

API reference for the USPS ZIP Code Crosswalk subpackage (data published by HUD).

---

## Overview
This subpackage downloads, processes and serves the USPS ZIP Code Crosswalk files published by HUD. These crosswalks map between ZIP codes and Census geographies (tracts, counties, CBSAs, congressional districts, and county subdivisions) using address-count ratios from the USPS.

The crosswalks are published quarterly by HUD and are essential for:

- Converting between ZIP-level and tract-level data (e.g., IRS income data to ACS-compatible tracts)
- Bridging datasets that use different geographic identifiers (ZIP vs census tract)
- Supporting matching workflows (e.g., HMDA-FHA matching uses ZIP-tract crosswalks to bridge FHA ZIP codes with HMDA census tracts)

### Data Pipeline

This source uses **two layers**, not the usual four:

1. **Raw**: each quarter's untouched HUD API response, saved as JSON (one file per type/year/quarter)
2. **Bronze**: per-quarter Parquet — schema enforced (zero-padded geographic codes, numeric ratios) and tagged with `year`/`quarter`/`type_id`/`type_name`

**Bronze is the analysis layer — there is no silver.** The published crosswalks
need no cleaning beyond what bronze already applies. The only schema change in
the 2010–2026 series is `city`/`state` appearing from 2021, which the loaders
absorb via `missing_columns="insert"` / `extra_columns="ignore"`. A silver layer
would only restate bronze, so read across quarters with `load_crosswalk()` or
`load_crosswalk_by_vintage()` instead.

Derived assets are promoted to `core.reference` rather than to a gold layer —
see [`tract_zip3`](core.md), the residential-share-weighted tract→ZIP3 mapping.

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
from mortgage_data_manager.sources.usps_crosswalk import get_census_vintage

get_census_vintage(2015, 1)  # 2010
get_census_vintage(2023, 3)  # 2020
```

---

## Configuration

::: mortgage_data_manager.sources.usps_crosswalk.config

---

## Download

::: mortgage_data_manager.sources.usps_crosswalk.download

---

## Loading data

::: mortgage_data_manager.sources.usps_crosswalk.load

---

## Bronze layer

::: mortgage_data_manager.sources.usps_crosswalk.import_bronze

---

## Pipeline

::: mortgage_data_manager.sources.usps_crosswalk.pipeline

---

## Validation

::: mortgage_data_manager.sources.usps_crosswalk.validate

---

## CLI
**Module:** `mortgage_data_manager.sources.usps_crosswalk.cli`

### `mortgage-data usps-crosswalk pipeline`

**Start here.** Brings the crosswalks up to date — `download → bronze` — skipping
whatever is already built. Safe to re-run any time; a no-op run takes seconds.

```bash
mortgage-data usps-crosswalk pipeline
mortgage-data usps-crosswalk pipeline -t ZIP_TRACT -t TRACT_ZIP
mortgage-data usps-crosswalk pipeline --skip-download        # rebuild from held raw
```

### `mortgage-data usps-crosswalk download`

Raw JSON only. Requires `USPS_CROSSWALK_API_KEY`.

```bash
mortgage-data usps-crosswalk download
mortgage-data usps-crosswalk download -t ZIP_TRACT
mortgage-data usps-crosswalk download --min-year 2021 --pause 2 --verbose
```

### `mortgage-data usps-crosswalk bronze`

Per-quarter parquet from the raw already downloaded.

```bash
mortgage-data usps-crosswalk bronze
mortgage-data usps-crosswalk bronze -t ZIP_TRACT -t TRACT_ZIP
mortgage-data usps-crosswalk bronze --min-year 2015 --max-year 2024 --overwrite
```

### `mortgage-data usps-crosswalk validate`

Report bronze coverage gaps and ratio-sum problems. Exits 1 if anything is flagged.

```bash
mortgage-data usps-crosswalk validate
mortgage-data usps-crosswalk validate ZIP_TRACT
mortgage-data usps-crosswalk validate all --skip-ratios       # coverage only (fast)
mortgage-data usps-crosswalk validate all --sample-quarters 8
```

### `mortgage-data usps-crosswalk info`

Display configuration and crosswalk type information.

```bash
mortgage-data usps-crosswalk info
```

---

## Data Structure
### Raw Layer

- **Format**: JSON — the untouched HUD API response, one file per type/year/quarter
- **Naming**: `{TYPE}_{YEAR}_Q{QUARTER}.json` (e.g., `ZIP_TRACT_2020_Q1.json`)
- **Location**: `{USPS_CROSSWALK_RAW_DIR}/{TYPE}/`
- **Retention**: keep it. The API's `query=All` is only officially supported for 2021+, so the pre-2021 responses may not be re-fetchable.

### Bronze Layer

- **Format**: flat Parquet, one file per type/year/quarter
- **Naming**: `{TYPE}_{YEAR}_Q{QUARTER}.parquet` (e.g., `ZIP_TRACT_2020_Q1.parquet`)
- **Location**: `{USPS_CROSSWALK_BRONZE_DIR}/{TYPE}/`
- **Schema**: geographic codes as zero-padded strings, ratios as floats, plus `year`, `quarter`, `type_id`, `type_name` metadata columns

### Schema

| Column | Type | Description |
|--------|------|-------------|
| `zip`, `tract`, `county`, `cbsa`, `cbsa_div`, `cd` or `county_sub` | `str` | Source geography ID, named for the type's "from" side (`zip` for every `ZIP_*` type) |
| `geoid` | `str` | Target geography ID (length varies by type) |
| `res_ratio` | `f64` | Residential address ratio |
| `bus_ratio` | `f64` | Business address ratio |
| `oth_ratio` | `f64` | Other address ratio |
| `tot_ratio` | `f64` | Total address ratio |
| `city`, `state` | `str` | USPS city and state of the ZIP; present from 2021 only |
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
| CBSA Division | `cbsa_div`/`geoid` | 5 | NNNNN |
| Congressional District | `cd`/`geoid` | 4 | SS-DD (state-district) |
| County Subdivision | `county_sub`/`geoid` | 10 | SS-CCC-SSSSS |

---

## Common Pitfalls
### Mixing Census Vintages

Tract `36061000100` in 2015 (2010 Census) may have entirely different boundaries than `36061000100` in 2023 (2020 Census). Always use `load_crosswalk_by_vintage()` or check vintages with `get_census_vintage()` when working with tracts across time. County boundaries are far more stable than tracts across vintages, so prefer county-level aggregation for long time series.

### Pre-2021 API Limitations

The HUD API's `query=All` parameter only works reliably for 2021+ data. Downloads for earlier years may fail with 400 errors. Check file coverage with `mortgage-data usps-crosswalk validate` after downloading.

### ZIP Codes Are Not Geographies

ZIP codes are mail delivery routes, not areas. A ZIP can span multiple counties and states. The crosswalk ratios handle this, but be aware that ZIP-level analysis has inherent geographic imprecision.

---

## See Also
- [Core API](core.md) — Shared utilities and medallion architecture
- [Configuration Guide](../configuration.md) — Environment variables and paths
- [HMDA-FHA Matching](../matching/hmda_fha_matching.md) — Uses HUD ZIP-tract crosswalks to bridge FHA and HMDA geographies
- [HUD USPS Crosswalk Files](https://www.huduser.gov/portal/datasets/usps_crosswalk.html) — Official data documentation
- [HUD API Registration](https://www.huduser.gov/hudapi/public/register?comingfrom=1) — Get an API key
