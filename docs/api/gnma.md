# GNMA API Reference

## Module Structure

```
mortgage_data_manager.sources.gnma
├── config          # GNMAConfig, ProcessorConfig, DownloadConfig
├── download        # Data and schema-PDF downloads
├── import_bronze   # Raw → Bronze (ZIP/TXT to parquet)
├── import_silver   # Bronze → Silver, via the canonical schemas
├── canonical       # Canonical schema model, decode and validators
├── load            # Loan-level loaders across the 2026 feed switch
├── performance     # Loan-level performance analysis
├── pipeline        # Orchestrated pipelines
└── cli             # Command-line interface
```

Silver is built **only** from `schemas/gnma/canonical/{prefix}.json`.

## Configuration

::: mortgage_data_manager.sources.gnma.config

## Download

::: mortgage_data_manager.sources.gnma.download

## Bronze layer

::: mortgage_data_manager.sources.gnma.import_bronze

## Silver layer

::: mortgage_data_manager.sources.gnma.import_silver

## Canonical schemas

::: mortgage_data_manager.sources.gnma.canonical

## Loan-level loaders

Ginnie moved single-family loan-level disclosure to pipe-delimited files (layout v2.0):
`llmon` replaces `llmon1` + `llmon2` from the 2026-07 portfolio, and `dailyllmnipd`
replaces `dailyllmni` from the 2026-08 new issuance. The old files run through 2026-08.
In both overlap months the new files hold exactly the old loans: `llmon` 2026-07 has the
same 12,423,780 (pool, sequence number) keys as `llmon1` ∪ `llmon2`, with issue type `X`
reproducing `llmon1` and `C`/`M` reproducing `llmon2`.

`scan_new_issuance` and `scan_portfolio` read each month from one feed and return both
eras under the same column ids and dtypes, so callers do not branch on the date. Values
are loaded as published. On the overlap loans every shared field agrees except:

| Field | Difference |
|-------|------------|
| `loan_age_in_months` | v2.0 portfolio is one month higher on every loan (layout change from the 2026-07 portfolio) |
| `remaining_loan_term_in_months` | v2.0 portfolio is one month lower on every loan (now computed from scheduled UPB) |
| original term, age, remaining term | blank in v2.0 only for 475 near-paid-off loans (2026-07) |
| `annual_mip` | v2.0 rounds to 2 decimals (`0.008` becomes `0.01`) |
| `loan_gross_margin` | kept as published: `2000` in v1.8, `2.0` in v2.0 |
| `prospective_interest_rate` | populated from the 2026-07 portfolio only |
| decimal fields | v1.8 values are decoded from implied decimals and can differ from the v2.0 value in the last binary digit (5.9990000000000006 against 5.999) |

`arm_adjustment_effective_date`, `scheduled_unpaid_principal_balance` and
`re_performing_loan_indicator` are v2.0 fields and null before the cutover.

::: mortgage_data_manager.sources.gnma.load

## Performance

::: mortgage_data_manager.sources.gnma.performance

## Pipeline

::: mortgage_data_manager.sources.gnma.pipeline

## Prefixes

Every GNMA command selects files by **prefix**. Two prefix facts the CLI reference
does not carry:

### Common Prefixes

| Prefix | Description |
|--------|-------------|
| `llmon1` | MBS single-family portfolio, loan level, Ginnie I (2013-10 → 2026-08) |
| `llmon2` | MBS single-family portfolio, loan level, Ginnie II (2013-10 → 2026-08); union it with `llmon1` for the whole portfolio |
| `llmon` | MBS single-family portfolio, loan level, Ginnie I + II in one pipe-delimited file (layout v2.0, 2026-07 on) |
| `dailyllmni` | MBS single-family monthly new issues, loan level (2013-09 → 2026-08) |
| `dailyllmnipd` | MBS single-family monthly new issues, loan level, pipe-delimited (layout v2.0, 2026-08 on) |
| `hllmon1` / `hllmon2` | HMBS portfolio, loan level: monthly-adjustable / fixed and annual-adjustable (2013-07 on) |
| `monthly` | Legacy MBS portfolio, pool level, 2012-02 → 2020-10 |
| `nissues` | Legacy pool-at-issuance header (record `D`), 2012-02 → 2021-04 — superseded by `nimonSFPS` PS from 2020-01, and the only source of Ginnie pool-at-issuance data before it |

Coverage dates and descriptions for every prefix are in `schemas/gnma/prefix_dictionary.yaml`.

### Default Set

`download`, `bronze`, `silver` and `pipeline` run with no prefixes named build the
default set: the prefixes the loaders and combined tables read, and the issuer files.
Every other prefix keeps
its canonical schema and builds the same way when named
(`mortgage-data gnma silver hllmon1`). A prefix leaves the default set with
`default_download: false` in `schemas/gnma/prefix_dictionary.yaml`; `gnma info`
prints the set.

| Prefixes | Read by |
|--------|--------|
| `llmon1`, `llmon2`, `llmon` | Loan-level portfolio (`load.scan_portfolio`) |
| `dailyllmni`, `dailyllmnipd` | Loan-level new issuance (`load.scan_new_issuance`) |
| `issrinfo`, `issrcutoff` | Issuer names (`load.scan_latest_issuer_names`) and cutoff dates |
| `nissues`, `nimonSFPS` | Combined `mbs_issuance` |
| `monthlySFPS`, `ptermmon`, `factorA1`, `factorA2`, `factorAplat`, `platmonPPS` | Combined `mbs_performance` |

HMBS, multifamily, REMIC, the forbearance, liquidation and payment-history files,
and the pool supplementals (`monthlySFS`, `nimonSFS`, deterministic stratifications
of the loan-level files) are outside it.

Silver holds one file per prefix, record type and month, and only where the month's
file has rows of that record type. A pool-statistics file such as `nimonSFPS` holds
only its `PS` records, though the layout it shares with its supplemental lists 34.

## Data Structure

```
{GNMA_DATA_DIR}/
├── raw/{prefix}/            # Downloaded ZIP/TXT files, {prefix}_{YYYYMM}.zip
├── bronze/{prefix}/         # Staged parquet, one text_content column per line
└── silver/{prefix}/{record_type}/{prefix}_{YYYYMM}_{record_type}.parquet

{GNMA_SCHEMAS_DIR}/          # schemas/gnma/ in the package
├── prefix_dictionary.yaml
├── canonical/               # Canonical per-prefix JSON (the only silver path)
└── raw/{prefix}/            # Layout PDFs fetched by `mortgage-data gnma download`
```

## See Also

- [Core API](core.md) - Shared utilities and medallion architecture
- [Configuration Guide](../configuration.md) - Environment variables
