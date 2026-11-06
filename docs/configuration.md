# Configuration

Every setting has a default, so most installs need at most one line of configuration.
Settings are read with `python-decouple`, which checks in this order:

1. **Environment variables** set in the shell
2. **A `.env` file** in the project root
3. **The default** built into the code

Values are read once, when a config module is imported. Setting an environment variable
after `mortgage_data_manager.*.config` has been imported has no effect; change `.env` or
the environment before the process starts.

The `.env` file is found by searching upward from the installed package, so it works for
a source checkout or editable install. A regular (non-editable) install has no project
root; set the variables in the environment instead. Set only what you want to change:

```bash
# .env
MORTGAGE_DATA_DIR=/data/mortgage_data
```

## Environment variables

### Core

These set the base directories that every subpackage's paths are derived from.

| Variable | Description | Default |
|----------|-------------|---------|
| `MORTGAGE_DATA_PROJECT_DIR` | Project root directory | Auto-detected |
| `MORTGAGE_DATA_DIR` | Root data directory | `{PROJECT_DIR}/data` |
| `MORTGAGE_DATA_CROSSWALK_DIR` | Final crosswalks, one folder per workflow | `{PROJECT_DIR}/crosswalk` |
| `MORTGAGE_DATA_REFERENCE_DIR` | Downloaded reference files (Census, lender file) | `{MORTGAGE_DATA_DIR}/reference` |
| `MORTGAGE_DATA_SCHEMAS_DIR` | Hand-maintained tables shipped with the package | `{PROJECT_DIR}/schemas` |
| `MORTGAGE_OUTPUT_DIR` | Root output directory | `{PROJECT_DIR}/output` |
| `DOWNLOAD_LEDGER_DIR` | Raw download ledger (`{source}.jsonl` per source) | `{MORTGAGE_DATA_DIR}/download_ledger` |

### Downloads

| Variable | Description | Default |
|----------|-------------|---------|
| `MORTGAGE_DATA_USER_AGENT` | HTTP User-Agent for downloads | A desktop browser string |
| `DOWNLOAD_RETRIES` | Retry attempts per file | `3` |
| `DOWNLOAD_TIMEOUT` | Request timeout, seconds | `30` |
| `DOWNLOAD_PAUSE` | Pause between files in a batch, seconds (`--pause` overrides it) | `5.0` |
| `DOWNLOAD_BACKOFF_FACTOR` | Exponential backoff multiplier between retries | `2.0` |
| `DOWNLOAD_CHUNK_SIZE` | Streaming chunk size, bytes | `8192` |

### Credentials

Two downloads need credentials, and each fails with a message naming the missing key.

| Variable | Used by |
|----------|---------|
| `GNMA_EMAIL`, `GNMA_ANSWER` | `mortgage-data gnma download` (see [Manual Downloads](manual_downloads.md#ginnie-mae-disclosure-data)) |
| `USPS_CROSSWALK_API_KEY` | `mortgage-data usps-crosswalk download` (a free HUD USPS API token) |

### Per subpackage

Each subpackage has its own directory under `{MORTGAGE_DATA_DIR}`, set by a
`{SOURCE}_DATA_DIR` key: `HMDA_DATA_DIR`, `FHA_DATA_DIR`, `GNMA_DATA_DIR`,
`FHFA_DATA_DIR`, `FNMA_DATA_DIR`, `FHLMC_DATA_DIR`, `UMBS_DATA_DIR`, `FHLB_DATA_DIR`,
`USPS_CROSSWALK_DATA_DIR` and `COMBINED_DATA_DIR`.
There is no shared `MBS_DATA_DIR`; each agency is separate.

Each layer can also be moved on its own with `{SOURCE}_RAW_DIR`, `{SOURCE}_BRONZE_DIR`
or `{SOURCE}_SILVER_DIR`. Each source's `info` command prints the
paths it resolved.

These keys move a source for its own commands only. Several matching workflows find
their HMDA, GNMA and UMBS inputs under `{MORTGAGE_DATA_DIR}/{source}/` directly, so to
keep a source on another drive, symlink its directory into the data root instead
(`ln -s /hdd/fhlmc /data/mortgage_data/fhlmc`). The whole data root can be a symlink
too; if its drive is not mounted, every read under it raises `FileNotFoundError`.

### Matching

| Variable | Description | Default |
|----------|-------------|---------|
| `MATCHING_DATA_DIR` | Intermediate matching data | `{MORTGAGE_DATA_DIR}/matching` |
| `MATCHING_CACHE_DIR` | Matching cache | `{MATCHING_DATA_DIR}/cache` |

Final crosswalks are written outside the data root, under
`{MORTGAGE_DATA_CROSSWALK_DIR}/{workflow}/`.

## Reading paths in code

Use the config class attributes directly, such as `HMDAConfig.HMDA_SILVER_DIR` or
`GNMAConfig.GNMA_RAW_DIR`; the classes are never instantiated. Directories are created
when a command first writes to them.

## Troubleshooting

`mortgage-data info` prints the project and data directories that resolved, and
`mortgage-data <source> info` prints one source's paths. If a setting is ignored, check
that `.env` is in that project directory and that `KEY=value` has no spaces around
the `=`.
