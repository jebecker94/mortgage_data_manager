# FHLMC API Reference

FHLMC data cannot be fetched programmatically: register on Freddie Mac's Clarity Data
Intelligence portal and download the archives by hand (`mortgage-data fhlmc download` prints
the steps). Bronze parsing reads Freddie's file layouts, transcribed from the file-layout
workbooks on the page below into `schemas/fhlmc/sf_file_layout_july_2026.csv` (the Release 47
layout) and `schemas/fhlmc/sf_file_layout.csv` (the earlier one, which still describes the
non-standard dataset).

## Source documentation
Freddie Mac publishes the Single-Family Loan-Level Dataset documentation on its
[Single-Family Loan-Level Dataset page](https://www.freddiemac.com/research/datasets/sf-loanlevel-dataset):
the General User Guide (one for the July 2026 layout and one for the earlier layout), the
file layouts, the release notes, the Disclosure Changes Summary for the July 2026 relayout,
the RPL loan-ID match FAQ and the dataset licensing agreement. The data itself is downloaded from
[Clarity Data Intelligence](https://capitalmarkets.freddiemac.com/clarity).

## Configuration

::: mortgage_data_manager.sources.fhlmc.config

## Schemas

::: mortgage_data_manager.sources.fhlmc.schemas

## Download

::: mortgage_data_manager.sources.fhlmc.download

## Bronze layer

::: mortgage_data_manager.sources.fhlmc.import_bronze

## Silver layer

::: mortgage_data_manager.sources.fhlmc.import_silver

## Pipeline

::: mortgage_data_manager.sources.fhlmc.pipeline

## Complete Example
```python
from mortgage_data_manager.sources.fhlmc.pipeline import run_pipeline

counts = run_pipeline(datasets=["origination"], years=list(range(2019, 2026)))
```

```bash
mortgage-data fhlmc pipeline --min-year 2019 --max-year 2025
```

Datasets: `origination`, `performance` and `reperforming`.

## Data Structure
### Raw files
Place the Clarity downloads in `{FHLMC_RAW_DIR}/`. The bronze importer walks every zip there,
descending into nested zips:

| Download | Contents |
|----------|----------|
| `full_set_standard_historical_data.zip` (or per-year `historical_data_YYYY.zip`) | Standard dataset, 1999Q1-2026Q1: `historical_data_YYYY.zip` → `historical_data_YYYYQn.zip` → `orig_YYYYQn.txt` + `perf_YYYYQn.txt` |
| `non_std_historical_data.zip` | Non-standard dataset (ARMs, high-LTV and other loans outside the standard set), 1999-2021Q2: `historical_data_excl_YYYYQn.txt` + `historical_data_excl_time_YYYYQn.txt` |
| `rpl_historical_data.zip` | Reperforming-loan ID match: nested zip(s) holding `SFLLD_RPL_ID_Match*.csv` and `rpl_loan_id_match_faq.pdf` |

```
{FHLMC_RAW_DIR}/
├── full_set_standard_historical_data.zip
│   └── historical_data_YYYY.zip
│       └── historical_data_YYYYQn.zip
│           ├── orig_YYYYQn.txt        (31 fields, pipe-delimited, no header)
│           └── perf_YYYYQn.txt        (35 fields, pipe-delimited, no header)
├── non_std_historical_data.zip
│   └── historical_data_excl_YYYY.zip
│       └── historical_data_excl_YYYYQn.zip
│           ├── historical_data_excl_YYYYQn.txt
│           └── historical_data_excl_time_YYYYQn.txt
└── rpl_historical_data.zip
    └── rpl_historical_data.zip
        ├── SFLLD_RPL_ID_Match.csv     (2 columns, comma-delimited, header)
        └── rpl_loan_id_match_faq.pdf
```

**Two layout eras.** Freddie reissued the whole standard dataset at Release 47 (July 2026)
under new file names and layouts: `orig_YYYYQn.txt` has 31 fields and `perf_YYYYQn.txt` 35
(previously `historical_data_YYYYQn.txt` and `historical_data_time_YYYYQn.txt`, 32 each).
Servicer Name and the Mortgage Insurance Cancellation Indicator moved from the origination
file to the monthly file, VantageScore 4.0 and Bankruptcy Cramdown Costs are new, and about
ten fields were renamed. The non-standard bundle was not reissued and keeps the earlier
layout, so both eras load side by side.

Loan identifiers have the form `PYYQnXXXXXXX` (e.g. `F19Q10000001`): a prefix letter (`F` or
`A`), the two-digit origination year, the quarter and a sequence number. The RPL match file
maps `id_loan` (the loan-level dataset identifier) to `loan_identifier` (the reperforming
security's loan identifier).

### Directory Layout

```
{FHLMC_DATA_DIR}/
├── raw/
├── bronze/
│   ├── origination/     historical_data_YYYYQn.parquet, historical_data_excl_YYYYQn.parquet
│   ├── performance/     historical_data_time_YYYYQn.parquet, historical_data_excl_time_YYYYQn.parquet
│   └── reperforming/    SFLLD_RPL_ID_Match.parquet, SFLLD_RPL_ID_Match_excl.parquet
└── silver/
    ├── origination/     vintage_year=YYYY/data.parquet
    └── performance/     historical_data_time_YYYYQn.parquet
```

Bronze file names keep the pre-Release-47 convention for both eras; the era is carried by the
column names. Bronze stores each file's own column names, so read it through
`scan_origination_bronze` / `scan_performance_bronze`, which normalize Release 47 files to the
established names.

### Medallion Architecture

1. **Raw**: ZIP archives downloaded by hand from Clarity
2. **Bronze**: one parquet per quarterly text file, typed from the layout workbooks
3. **Silver**: two datasets. Origination is one row per `Loan Sequence Number`,
   hive-partitioned by `vintage_year`, combining the standard and non-standard releases (a
   loan in both keeps one row; a field the two copies fill differently is nulled). Performance
   is the monthly panel, one parquet per standard-dataset origination quarter. Each
   null-maps the user-guide sentinels (Credit Score 9999, CLTV/DTI/LTV 999) and applies its
   `SilverSpec` dtypes. `reperforming` has no silver builder.

## See Also
- [Core API](core.md) - Shared utilities and medallion architecture
- [Configuration Guide](../configuration.md) - Environment variables and paths
- [FNMA API](fnma.md) - Similar GSE data processing
