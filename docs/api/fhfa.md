# FHFA API Reference

## Configuration

::: mortgage_data_manager.sources.fhfa.config

## Download

::: mortgage_data_manager.sources.fhfa.download

## Bronze layer

::: mortgage_data_manager.sources.fhfa.import_bronze

## Silver layer

::: mortgage_data_manager.sources.fhfa.import_silver

## Schemas

::: mortgage_data_manager.sources.fhfa.schemas

## Pipeline

::: mortgage_data_manager.sources.fhfa.pipeline

## Complete Example
```python
from mortgage_data_manager.sources.fhfa.pipeline import run_pipeline

counts = run_pipeline(datasets=["sf_c"], years=list(range(2018, 2025)))
```

```bash
mortgage-data fhfa pipeline
```

Valid datasets: `sf_a`, `sf_b`, `sf_c`, `sf_d`, `mf_c`, `mf_property_b`, `mf_unit_b`.

## Data Structure
### Directory Layout

```
{FHFA_DATA_DIR}/
├── raw/
│   ├── {year}_enterprise_pudb.zip     # Through 2024: fixed-width files, one zip per year
│   ├── {year}_pudb_sf_ctf.zip, ...    # From 2025: one headed-CSV zip per file
│   └── hud_era/                       # Pre-FHFA multifamily archive, 1993-2007 (--era hud)
├── bronze/
│   └── {dataset}/{dataset}_{year}.parquet
└── silver/
    └── {dataset}/{dataset}_{year}.parquet

{FHFA_SCHEMAS_DIR}/fhfa/               # Data dictionaries (schemas/fhfa/ in the package)
├── {year}/                            # Downloaded and converted per-year dictionaries
└── masters/master_{dataset}.csv       # All years combined (fhfa schemas build)
```

### Data Types

The FHFA Enterprise PUDB releases each year of Fannie Mae and Freddie Mac
acquisitions as several companion files. Single-family and multifamily are
published separately, and within each, the same loans are split across two or
three files purely for disclosure-privacy reasons (continuous fields in one
file, bucketed in another, etc.). Record numbers are **per-file row IDs** and
cannot be used to join across files. FHFA publishes the files and their data dictionaries on
its [Public Use Database page](https://www.fhfa.gov/data/pudb).

#### Single-Family

| File   | FHFA name                             | Years     | Grain        | Typical rows/yr | Use it for |
|--------|---------------------------------------|-----------|--------------|-----------------|------------|
| `sf_a` | Single-Family National File A         | 2008-2024 | Loan         | 1.8M – 8.5M     | Coarse demographic / underserved-area counts when you don't need geography. Rarely the right choice once you have sf_c. |
| `sf_b` | Single-Family National File B         | 2008-2025 | Unit         | 2.0M – 9.2M     | The only single-family file with `Type of Seller Institution` (mortgage company, depository, credit union), every year. sf_c has never carried it, and the two files share no record key. |
| `sf_c` | Single-Family Census Tract File C     | 2008-2025 | Loan         | 1.9M – 9.0M     | Default. Has tract-level geography and, from 2018+, continuous values for rate, DTI, property value, credit-score model, AUS, etc. |
| `sf_d` | Single-Family National File C         | 2010-2025 | Loan (sample)| 20K – 148K      | Pre-2018 credit-score and product-type breakdowns. **It is a small sample (0.4-3.7% of sf_c's rows, by year)**, not the full acquisition — never stack it with sf_c. |

Key gotchas:

- **FHFA discontinued National File A (`sf_a`) with the 2025 release**, and
  `sf_b` lost `Unit - Owner Occupied` the same year (null in 2025 silver).
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
- **`Underserved Areas Indicator`** is populated in `sf_a` / `sf_b` only for
  2008-2009 (null in every later year); it can be recomputed from tract-level
  income & minority data for missing years.

#### Multifamily

All three multifamily files cover 1993-2025: 2008 on from the FHFA PUDB, 1993-2007 from the
pre-FHFA GSE public use database archived by HUD (`mortgage-data fhfa pipeline --era hud`).

| File             | FHFA name                                      | Grain    | Use it for |
|------------------|------------------------------------------------|----------|------------|
| `mf_property_b`  | Multifamily National File Property-Level B     | Property | National (no-tract) property characteristics with privacy buckets. |
| `mf_unit_b`      | Multifamily National File Unit Class-Level B   | Unit class | Unit-mix breakdowns by affordability/bedroom/tenant-income. Join to `mf_property_b` on `Enterprise Flag` + `Record Number`. |
| `mf_c`           | Multifamily Census Tract File C                | Property | Default for multifamily — has tract geography and continuous fields (rate, UPB, property value, etc.). |

### Medallion Architecture

1. **Raw**: Downloaded zips — through 2024 one `{year}_enterprise_pudb.zip` of
   fixed-width text files per Enterprise; from 2025 one headed-CSV zip per file
   (`{year}_pudb_sf_ctf.zip`, …) with both Enterprises stacked
2. **Bronze**: Converted to Parquet under the fixed-width-era field names (the
   CSV headers are mapped through that year's data dictionary), so both eras
   share one bronze schema
3. **Silver**: Cleaned and typed, one parquet per dataset and year

#### The 2025 release

Beyond the format change, the 2025 dictionary redefines several codes. Silver
carries each year's codes as published, so compare across the 2024/2025 boundary
with these in mind:

- **Not-available continuous values are blank, not in-band sentinels.** Through
  2024, `999999`-style codes (masked to null in silver) marked missing values;
  from 2025 the cell is blank, which is also null in silver. Two fields change
  meaning in the process: `rate_spread` was `0` for "not applicable / below the
  reporting threshold" and is now blank (null), and `introductory_rate_period`
  was `999` for "no introductory period" and is now blank (null).
- **Multifamily `affordability_category` recodes `9`.** Through 2024: `8` = not
  available, `9` = not eligible, `0` = missing. From 2025: `7` = not eligible,
  `9` = not available.
- **`lien_status`** "not applicable" `4` becomes "not available" `9`.
- **`type_of_seller_institution` is renumbered** (sf_b, mf_c, mf_property_b).
  From 2025: `1` = mortgage company, `6` = depository insured by the Deposit
  Insurance Fund, `7` = credit union, `9` = not available/other. Through 2024,
  sf_b and mf_c used `2` = SAIF-insured and `3` = BIF-insured (both now `6`),
  `4` = credit union and `5` = other/unknown; mf_property_b used `2` = SAIF or
  BIF, `3` = credit union and `4` = other. The change appears only in the
  dictionary, not in FHFA's release notes.
- **A separate "no co-borrower" code `8`**, previously folded into `9`. It is new
  in sf_c co-borrower age and age-62+ and in sf_b co-borrower race/ethnicity;
  sf_c co-borrower race already used `8` in 2024, and sf_c co-borrower
  ethnicity does not use it. The co-borrower credit-score model keeps `10`.
  Both score-model fields drop `9` (not applicable).

## Schema Inventories
Column names and dtypes are **derived from the data dictionaries, not listed here.**
FHFA revises dictionaries between vintages, and a pasted inventory goes stale silently —
the generating commands below always agree with what is actually on disk.

Build the master dictionaries, then query them:

```bash
mortgage-data fhfa schemas pipeline      # extract -> convert -> build
mortgage-data fhfa schemas list-years    # which years each dataset has a dictionary for
```

```python
from mortgage_data_manager.sources.fhfa.schemas import load_master_dictionary

master = load_master_dictionary("sf_c")   # {FHFA_SCHEMAS_DIR}/fhfa/masters/master_sf_c.csv
print(master.head())
```

For a built layer, read the schema off the parquet itself:

```python
import polars as pl
from mortgage_data_manager.sources.fhfa.config import FHFAConfig

pl.scan_parquet(FHFAConfig.FHFA_SILVER_DIR / "sf_c" / "*.parquet").collect_schema()
```

Two conventions the dictionaries do not state:

- **Bronze adds `year`** to whatever the dictionary defines.
- **Silver column names are snake_case**; bronze keeps the dictionary's display names
  (`Loan-to-Value Ratio (LTV) at Origination`), so the same field is spelled differently in
  the two layers.

## See Also
- [Core API](core.md) - Shared utilities and medallion architecture
- [Configuration Guide](../configuration.md) - Environment variables and paths
