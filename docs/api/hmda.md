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
- A build skips the release partitions already on disk. `--overwrite` rebuilds each
  requested year in a temp tree and swaps it in only when every partition is written and
  its row count verified, so a failed rebuild leaves the previous year in place.

### Derived columns and shared readers

Silver derives a few columns once, so every consumer reads the same definition:

| Column | Where | Definition |
|--------|-------|------------|
| `number_of_borrowers` | loans, 2007-2017 and post-2018 | 1 when `co_applicant_sex` is 5 (no co-applicant), 2 when it reports a co-applicant (1, 2, 3, and 6 from 2018), null for 4 (not applicable) |
| `purchaser_type_label` | loans, 2007-2017 and post-2018 | The CFPB label for `purchaser_type`, per era (`schemas/hmda/purchaser_type_labels.yaml`) |
| `respondent_key` | loans and panel, 2007-2017 | `respondent_id` without punctuation or leading zeros, so the `a` and `d` spellings of one id agree; unique within `agency_code` |

The latest panel `respondent_name` per LEI is a small table beside the dataset folders
(`{HMDA_SILVER_DIR}/lender_names/lei_latest_name.parquet`), rebuilt with the post-2018
panel and read with `lei_latest_names`. An LEI that filed two names in its latest year has
no row.

Silver keeps the HMDA codes for absent values live: -99999 (`Exempt` in a numeric field)
and 1111 (`Exempt` in a coded field) from 2018, and age 8888 (not applicable) and 9999 (no
co-applicant). `null_sentinel_codes` turns them into nulls; codes that are real answers,
such as `purchaser_type` 0 or `co_applicant_sex` 5, stay. `scan_originations` reads
originated loans (`action_taken` 1), optionally by loan type.

Pre-2018 `income` is null where the filer reported 9999: Applicant Income (000s) is a
4-character field, so 9999 is its ceiling rather than an amount (for example 422 records in
2012). For 2017 the CFPB's nationwide file publishes `edit_status`, `sequence_number` and
`application_date_indicator` empty on every row, and the three-year file has no such
columns, so `HMDAIndex` is the record identifier for that year.

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

`loan_amount` is a $10,000-bin midpoint, so it always ends in 5000. Two 2020 applications
report about $500 billion and $200 billion in the `a` and `b` releases, together about 9% of
that year's dollars. The `c` snapshot holds them as 1,784,395,000 and -1,863,205,000, which is
where those amounts land after a 32-bit overflow, so use a median or trim the tail for dollar
totals.

For complete field reference, see the [FFIEC HMDA documentation](https://ffiec.cfpb.gov/documentation/).

## See Also
- [Core API](core.md) - Shared utilities and medallion architecture
- [Configuration Guide](../configuration.md) - Environment variables and paths
