# Configuration

Every setting has a default, so most installs need at most one line of configuration.
Settings are read with `python-decouple`, which checks in this order:

1. **Environment variables** set in the shell
2. **A `.env` file** in the project root
3. **The default** built into the code

Values are read once, when a config module is imported. Setting an environment variable
after `mortgage_data_manager.*.config` has been imported has no effect; change `.env` or
the environment before the process starts.

## Environment variables

### Core

These set the base directories that every subpackage's paths are derived from.

| Variable | Description | Default |
|----------|-------------|---------|
| `MORTGAGE_DATA_PROJECT_DIR` | Project root directory | Auto-detected |
| `MORTGAGE_DATA_DIR` | Root data directory | `{PROJECT_DIR}/data` |
| `MORTGAGE_DATA_CROSSWALK_DIR` | Final crosswalks, one folder per workflow | `{PROJECT_DIR}/crosswalk` |
| `MORTGAGE_DATA_REFERENCE_DIR` | Downloaded reference files (Census, lender file, macro series) | `{MORTGAGE_DATA_DIR}/reference` |
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
| `FRED_API_KEY` | Optional: FRED series in `core.reference.macro` use the `fredapi` client when it is set, and FRED's public CSV endpoint when it is not |
| `MACRO_USER_AGENT` | Optional: the User-Agent `core.reference.macro` sends to BLS, FHFA and FRED |

### Per subpackage

Each subpackage has its own directory under `{MORTGAGE_DATA_DIR}`, set by a
`{SOURCE}_DATA_DIR` key: `HMDA_DATA_DIR`, `FHA_DATA_DIR`, `GNMA_DATA_DIR`,
`FHFA_DATA_DIR`, `FNMA_DATA_DIR`, `FHLMC_DATA_DIR`, `UMBS_DATA_DIR`, `FHLB_DATA_DIR`,
`HUD_DATA_DIR`, `USPS_CROSSWALK_DATA_DIR`, `COMBINED_DATA_DIR` and `ANALYTICS_DATA_DIR`.
There is no shared `MBS_DATA_DIR`; each agency is separate.

Each layer can also be moved on its own with `{SOURCE}_RAW_DIR`, `{SOURCE}_BRONZE_DIR`
or `{SOURCE}_SILVER_DIR`, plus `{SOURCE}_GOLD_DIR` for the sources that have a gold
layer (FHFA, FHLB, FHLMC, FNMA, GNMA and UMBS). Each source's `info` command prints the
paths it resolved.

These keys move a source for its own commands only. Several matching workflows find
their HMDA, GNMA and UMBS inputs under `{MORTGAGE_DATA_DIR}/{source}/` directly, so
to keep one source on another drive, symlink its directory into the data root instead
(see [Common setups](#common-setups)).

### Matching

| Variable | Description | Default |
|----------|-------------|---------|
| `MATCHING_DATA_DIR` | Intermediate matching data | `{MORTGAGE_DATA_DIR}/matching` |
| `MATCHING_CACHE_DIR` | Matching cache | `{MATCHING_DATA_DIR}/cache` |

Final crosswalks are written outside the data root, under
`{MORTGAGE_DATA_CROSSWALK_DIR}/{workflow}/`.

## The `.env` file

Put a `.env` file in the project root and set only what you want to change:

```bash
# .env
MORTGAGE_DATA_DIR=/data/mortgage_data
```

The file is found by searching upward from the installed package, so it works for a
source checkout or editable install. A regular (non-editable) install has no project
root; set the variables in the environment instead.

## Directory layout

The `data/` directory is not part of the repository. Directories are created when a CLI
command first writes to them, or up front with a config's `ensure_directories()`:

```python
from mortgage_data_manager.sources.hmda.config import HMDAConfig

HMDAConfig.ensure_directories()
```

With the defaults, the tree looks like this:

```
{MORTGAGE_DATA_DIR}/
├── hmda/
│   ├── raw/
│   ├── bronze/
│   └── silver/
├── fha/
├── gnma/
├── fhfa/
├── fnma/
├── fhlmc/
├── umbs/
├── usps_crosswalk/     # raw + bronze only; this source has no silver
├── hud/
├── fhlb/
├── combined/           # cross-source master datasets
├── analytics/
├── reference/          # downloaded reference files
├── download_ledger/
└── matching/
    ├── {workflow}/     # intermediates for one workflow
    └── cache/
```

## Reading paths in code

Use the config classes directly; they are never instantiated.

```python
from mortgage_data_manager.sources.hmda.config import HMDAConfig
from mortgage_data_manager.matching.config import MatchingConfig

HMDAConfig.HMDA_SILVER_DIR                           # {MORTGAGE_DATA_DIR}/hmda/silver
MatchingConfig.get_matching_type_dir("hmda_fha")     # {MATCHING_DATA_DIR}/hmda_fha
MatchingConfig.get_crosswalk_type_dir("hmda_fha")    # {MORTGAGE_DATA_CROSSWALK_DIR}/hmda_fha
```

Every subpackage follows the same naming: `FHAConfig.FHA_RAW_DIR`,
`GNMAConfig.GNMA_SILVER_DIR`, `FHLBConfig.FHLB_DATA_DIR`, and so on.

## Common setups

**One data root.** Set `MORTGAGE_DATA_DIR` and let every subpackage take a
subdirectory of it. This is the normal case.

```bash
# .env
MORTGAGE_DATA_DIR=/data/mortgage_data
```

**External volume by symlink.** `ln -s /Volumes/ExternalSSD/mortgage_data data`
works without any configuration. If the drive is not mounted, reads raise
`FileNotFoundError`.

**Split across drives.** Keep one data root and symlink the sources that live elsewhere
into it, such as a fast SSD for what you query constantly and bulk storage for the
archives:

```bash
ln -s /hdd/fhlmc /data/mortgage_data/fhlmc
```

## Troubleshooting

**Environment variables are not being read.** `.env` must be in the project root
(the directory `MORTGAGE_DATA_PROJECT_DIR` resolves to), and `KEY=value` takes no
spaces around the `=`. `mortgage-data info` prints the project and data directories
that resolved, and `mortgage-data <source> info` prints one source's paths.

**`FileNotFoundError` on a path that should exist.** Check the configured value
before assuming the data is missing:

```python
from mortgage_data_manager.sources.hmda.config import HMDAConfig

print(HMDAConfig.HMDA_DATA_DIR, HMDAConfig.HMDA_DATA_DIR.exists())
```

If the drive holding the data root is not mounted, every path under it fails this
way.

**Running out of space.** The raw layer is the largest, and the easiest to replace
because it can be downloaded again. `du -sh {MORTGAGE_DATA_DIR}/*` shows where the space went.
