# GNMA API Reference

## Module Structure

```
mortgage_data_manager.sources.gnma
├── config          # GNMAConfig, ProcessorConfig, DownloadConfig
├── download        # Data and schema-PDF downloads
├── import_bronze   # Raw → Bronze (ZIP/TXT to parquet)
├── import_silver   # Bronze → Silver, via the canonical schemas
├── canonical       # Canonical schema model, decode and validators
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
| `llmon1` | MBS single-family portfolio, loan level, Ginnie I (2013-10 on) |
| `llmon2` | MBS single-family portfolio, loan level, Ginnie II (2013-10 on); union it with `llmon1` for the whole portfolio |
| `llmon` | Combined Ginnie I + II loan-level portfolio file (layout V2.0, 2026-01 on). No canonical schema yet, so no silver is built for it |
| `dailyllmni` | MBS single-family monthly new issues, loan level (2013-09 on) |
| `hllmon1` / `hllmon2` | HMBS portfolio, loan level: monthly-adjustable / fixed and annual-adjustable (2013-07 on) |
| `monthly` | Legacy MBS portfolio, pool level, 2012-02 → 2020-10 |
| `nissues` | Legacy pool-at-issuance header (record `D`), 2012-02 → 2021-04 — superseded by `nimonSFPS` PS from 2020-01, and the only source of Ginnie pool-at-issuance data before it |

Coverage dates and descriptions for every prefix are in `schemas/gnma/prefix_dictionary.yaml`.

### Default-Download Opt-Out

Some prefixes are deterministic aggregates of other files in the catalog and
are therefore excluded from the default download list. They remain fully
supported and can be downloaded by passing the prefix explicitly. Opt-outs
are flagged with `default_download: false` in
`schemas/gnma/prefix_dictionary.yaml`.

| Prefix | Reason |
|--------|--------|
| `monthlySFS` | Pool supplemental — deterministic stratification of `llmon1`+`llmon2` |
| `nimonSFS` | Pool supplemental — deterministic stratification of `dailyllmni` |
| `hmonthlyS` | Pool supplemental — deterministic stratification of `hllmon1`+`hllmon2` |

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
