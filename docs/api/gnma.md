# GNMA API Reference

API reference for the GNMA (Ginnie Mae) subpackage.

## Module Structure

```
mortgage_data_manager.gnma
├── config          # GNMAConfig, ProcessorConfig, DownloadConfig, SchemaReaderConfig
├── download        # Data and schema file downloads
├── import_bronze   # Raw → Bronze (ZIP/TXT to parquet)
├── import_silver   # Bronze → Silver (parquet to structured)
├── schemas/        # PDF extraction, COBOL parsing, schema combination
├── performance     # Loan-level performance analysis
├── pipeline        # Orchestrated pipelines
└── cli             # Command-line interface
```

---

## Configuration

### `GNMAConfig`

**Module:** `mortgage_data_manager.gnma.config`

| Attribute | Type | Description |
|-----------|------|-------------|
| `GNMA_DATA_DIR` | `Path` | Root GNMA data directory |
| `GNMA_RAW_DIR` | `Path` | Raw downloads |
| `GNMA_BRONZE_DIR` | `Path` | Staged parquet files |
| `GNMA_SILVER_DIR` | `Path` | Transformed data |
| `GNMA_SCHEMAS_DIR` | `Path` | Schema files directory |
| `GNMA_PREFIX_DICTIONARY` | `Path` | Prefix dictionary YAML |
| `GNMA_SCHEMA_COMBINED_DIR` | `Path` | Combined schema CSVs |

All paths support environment variable overrides (e.g., `GNMA_DATA_DIR`).

### Config Dataclasses

| Class | Purpose | Key Attributes |
|-------|---------|----------------|
| `DownloadConfig` | Data/schema downloads | `email_value`, `id_value`, `data_download_folder` |
| `ProcessorConfig` | Bronze/silver processing | `raw_folder`, `bronze_folder`, `silver_folder`, `schemas_folder` |
| `SchemaReaderConfig` | PDF schema extraction | `schemas_folder`, `prefix_file` |

---

## Import Functions

### Bronze Layer (`import_bronze`)

**Module:** `mortgage_data_manager.gnma.import_bronze`

| Function | Description |
|----------|-------------|
| `import_zip_to_parquet()` | Convert ZIP to parquet |
| `import_txt_to_parquet()` | Convert TXT to parquet |
| `import_prefix_bronze()` | Import all files for a prefix |
| `import_all_bronze()` | Import all prefixes |

### Silver Layer (`import_silver`)

**Module:** `mortgage_data_manager.gnma.import_silver`

| Function | Description |
|----------|-------------|
| `import_prefix_silver()` | Transform prefix to silver |
| `import_file_silver()` | Transform single file |
| `transform_fixed_width()` | Parse fixed-width format |
| `transform_delimited()` | Parse delimited format |
| `load_schema_by_record_type()` | Load schema grouped by record type |

---

## Schemas

**Module:** `mortgage_data_manager.gnma.schemas`

Schema processing pipeline: PDF extraction → cleaning → combination.

| Submodule | Purpose |
|-----------|---------|
| `pdf_extraction` | Extract tables from PDF schema docs |
| `cleaning` | Clean extracted data, add record types |
| `cobol` | Parse COBOL format codes (e.g., `9(13)v9(2)`) |
| `combination` | Merge schema versions across time |
| `standardization` | Normalize field names |
| `analysis` | Temporal coverage, format detection |

---

## CLI

```bash
# Download
mortgage-data gnma download all monthly
mortgage-data gnma download schemas monthly

# Bronze (stage raw → parquet)
mortgage-data gnma bronze monthly

# Silver (transform with schemas)
mortgage-data gnma silver monthly

# Schemas
mortgage-data gnma schemas extract monthly
mortgage-data gnma schemas pipeline monthly

# Full pipeline (download + bronze + silver)
mortgage-data gnma pipeline full monthly
```

### Common Prefixes

| Prefix | Description |
|--------|-------------|
| `monthly` | Monthly disclosure data |
| `llmon1` | Loan-level performance (MBS) |
| `llmon2` | Loan-level performance (HMBS) |
| `dailyllmni` | Daily loan-level data |

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

See `../notes/gnma.md` and the supporting investigation under
`investigations/reports/` for validation.

---

## Data Structure

```
data/gnma/
├── raw/            # Downloaded ZIP/TXT files
├── bronze/         # Staged parquet (text_content column)
├── silver/         # Structured data by record type
│   └── {prefix}/{record_type}/
└── schemas/
    ├── prefix_dictionary.yaml
    ├── raw/        # Downloaded PDF schemas
    ├── clean/      # Extracted CSVs
    └── combined/   # Merged schemas (used by import_silver)
```

---

## See Also

- [Core API](core.md) - Shared utilities and medallion architecture
- [Configuration Guide](../configuration.md) - Environment variables
