# FNMA API Reference

API reference for the FNMA (Fannie Mae - Federal National Mortgage Association) subpackage.

FNMA data cannot be fetched programmatically: register on Fannie Mae's Data Dynamics portal
and download the files by hand (`mortgage-data fnma download` prints the steps). The package
covers the single-family primary dataset (acquisitions 2000Q1 to the latest release), the
Exclusion dataset (loans left out of the primary dataset, 2000Q1-2020Q3), the HARP dataset and
the multifamily loan performance data. Bronze names and types the headerless single-family
files from `schemas/fnma/sf_loan_layout.csv`, the field positions, names, types and lengths
transcribed from the "Combined Glossary" sheet of Fannie Mae's CRT file layout and glossary
workbook (linked from the page below).

---

## Source documentation
Fannie Mae publishes the dataset documentation on its
[Single-Family Loan Performance Data page](https://capitalmarkets.fanniemae.com/credit-risk-transfer/fannie-mae-single-family-loan-performance-data):
the FAQs, the CRT glossary and file layout, the statistical summaries for the primary and
HARP datasets, and the R import code. The page also links to Data Dynamics, where the data
is downloaded.

---

## Configuration

::: mortgage_data_manager.sources.fnma.config

---

## Download

::: mortgage_data_manager.sources.fnma.download

---

## Bronze layer

::: mortgage_data_manager.sources.fnma.import_bronze

---

## Silver layer

::: mortgage_data_manager.sources.fnma.import_silver

---

## Pipeline

::: mortgage_data_manager.sources.fnma.pipeline

---

## CLI
Commands, options and examples for `mortgage-data fnma` live in the
[CLI Reference](../cli_reference.md#mortgage-data-fnma). `mortgage-data fnma --help` is
authoritative.

---

## Complete Example
```python
from mortgage_data_manager.sources.fnma.pipeline import run_pipeline

counts = run_pipeline(overwrite=False)
```

```bash
mortgage-data fnma pipeline
```

Bronze datasets: `sf`, `exclusions`, `harp`, `multifamily` (the defaults) and the opt-in
`performance_all`, which reads `Performance_All.zip`, the only source of the pre-2016
history. Silver datasets: `issuances` (which also writes `terminations`), `performance`,
`harp` and `multifamily`.

---

## Data Structure
### Directory Layout

```
{FNMA_DATA_DIR}/
├── raw/
│   ├── 2020Q1.zip                    # Per-quarter single-family zips (YYYYQn*.zip)
│   ├── Performance_All.zip           # Full-history archive (opt-in performance_all)
│   ├── Exclusions.zip                # Exclusion dataset, 2000Q1-2020Q3
│   ├── HARP_Files.zip
│   ├── Multifamily.zip
│   └── Multifamily_DSCR.zip
├── bronze/
│   ├── 2020Q1.parquet                # One parquet per quarter
│   ├── 2020Q1EXCL.parquet            # Exclusion quarters sit beside the standard ones
│   ├── harp/                         # loan_performance.parquet, loan_mapping.parquet
│   └── multifamily/                  # Release-stamped: loan_performance_YYYYMM.parquet, current_dscr_YYYYMM.parquet
└── silver/
    ├── issuances/                    # First observation per loan, one parquet per quarter
    ├── terminations/                 # One parquet per quarter
    ├── performance/                  # Loan x month panel, one parquet per quarter
    ├── harp/                         # performance, issuances, terminations, loan_mapping
    └── multifamily/                  # performance, acquisitions, terminations, current_dscr
```

The HARP silver is schema-identical to the quarterly silver but a disjoint loan population;
`loan_mapping` links each HARP loan to the primary-dataset loan it refinanced. Multifamily
silver is built from the newest bronze release, since each release restates the full history.

### File Format

The single-family loan-performance files are pipe-delimited zipped CSVs with no
header row, so column names and dtypes come from the layout workbook rather than
from the file. The multifamily files differ on every one of those points: they
are comma-delimited, they carry a header, and dollar amounts arrive as
`"$1,234.56"` strings with parenthesized negatives.

---

## See Also
- [Core API](core.md) - Shared utilities and medallion architecture
- [Configuration Guide](../configuration.md) - Environment variables and paths
- [FHLMC API](fhlmc.md) - Similar GSE data processing
