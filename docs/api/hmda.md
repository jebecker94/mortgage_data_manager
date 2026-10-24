# HMDA API Reference

## Configuration

::: mortgage_data_manager.sources.hmda.config

## Download

::: mortgage_data_manager.sources.hmda.download

## Bronze layer

::: mortgage_data_manager.sources.hmda.import_bronze

## Silver layer

::: mortgage_data_manager.sources.hmda.import_silver

## Loading silver

::: mortgage_data_manager.sources.hmda.load

## Pipeline

::: mortgage_data_manager.sources.hmda.pipeline

## Utilities

::: mortgage_data_manager.sources.hmda.utils

## Complete Example
```python
from mortgage_data_manager.sources.hmda.pipeline import run_pipeline

results = run_pipeline(eras=["post2018"], download=True)
```

```bash
mortgage-data hmda pipeline post2018 --min-year 2018 --max-year 2025
mortgage-data hmda pipeline 2007-2017 --min-year 2007 --max-year 2017
mortgage-data hmda pipeline pre2007 --min-year 1990 --max-year 2006
```

CLI periods are `post2018`, `2007-2017`, `pre2007`; the Python `eras` argument
spells the middle one `2007_2017`. Datasets are `loans`, `panel`,
`transmittal_series` (selectable with `--datasets` for `post2018` and `pre2007`).

## Data Structure
### Bronze Layer

- One parquet per raw source file, named after it (e.g. `2024_public_lar_one_year.parquet`,
  `loans_2006.parquet`)
- Minimal processing
- Location: `{HMDA_BRONZE_DIR}/{dataset}/`

### Silver Layer

- Cleaned and standardized data, typed by a per-(dataset, period) `SilverSpec`
- One directory per period: `post2018`, `period_2007_2017`, `pre2007`
- Hive-partitioned by `activity_year`, and by `file_type` (the CFPB release: post-2018
  `a` three-year, `b` one-year, `c` snapshot, `e` modified LAR; 2007-2017 `a` and `d`)
  where a year has several releases. The 2007-2017 panel and transmittal sheet and all of
  pre-2007 are partitioned by year alone.
- Location: `{HMDA_SILVER_DIR}/{dataset}/{period}/activity_year=YYYY/[file_type=X/]`
- Read it through `hmda.load` (`scan_hmda`, `scan_hmda_for_index`), which picks exactly one
  release per year; the releases of one year carry different schemas.

### Common Fields (Post-2018)

Key fields in HMDA data:

| Field | Type | Description |
|-------|------|-------------|
| `lei` | str | Legal Entity Identifier |
| `HMDAIndex` | str | Row identifier, prefixed by year and release (`2024b_000000001`) |
| `activity_year` | int | Activity year |
| `loan_type` | int | Type of loan |
| `loan_purpose` | int | Purpose of loan |
| `loan_amount` | int | Loan amount in dollars |
| `interest_rate` | float | Interest rate |
| `occupancy_type` | int | Property occupancy type |
| `action_taken` | int | Action taken on application |
| `state_code` | str | Two-letter state postal code |
| `county_code` | str | Five-digit county FIPS code |
| `census_tract` | str | Eleven-digit census tract |

For complete field reference, see the [FFIEC HMDA documentation](https://ffiec.cfpb.gov/documentation/).

## See Also
- [Core API](core.md) - Shared utilities and medallion architecture
- [Configuration Guide](../configuration.md) - Environment variables and paths
