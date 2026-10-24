# Getting Started

Install the package, build a first dataset, and read it.

## Installation

The base install has the shared utilities and the CLI. Each source's dependencies are an
extra: `hmda`, `fha`, `mbs` (GNMA, FHFA, FNMA, FHLMC and UMBS), `matching`, `hud` and
`analytics`. `all` installs every one, `dev` adds the toolchain and `docs` the
documentation build.

```bash
uv pip install -e ".[hmda]"        # one source
uv pip install -e ".[all,dev]"     # everything, plus the toolchain
```

Data is written to `data/` in the project root unless `MORTGAGE_DATA_DIR` in `.env` points
elsewhere. [Configuration](configuration.md) lists every setting.

## First build

Each source moves through raw, bronze and silver
([Architecture](architecture.md#medallion-layers)), and you read silver:

```bash
mortgage-data hmda download --dry-run                     # list what would be fetched
mortgage-data hmda download --min-year 2020 --max-year 2024
mortgage-data hmda pipeline post2018 --min-year 2020 --max-year 2024
mortgage-data info                                        # resolved paths, what is on disk
```

Fannie Mae, Freddie Mac and UMBS files must be downloaded by hand first, and GNMA needs
credentials in `.env`; see [Manual downloads](manual_downloads.md).

## Reading the data

Most silver is a plain `pl.scan_parquet` over the source's silver directory
(`FHAConfig.FHA_SILVER_DIR`, `FHLMCConfig.FHLMC_SILVER_DIR`, …). The exceptions:

| Data | Read with | Why |
|---|---|---|
| HMDA | `sources.hmda.load.scan_hmda_years("post2018", 2020, 2024)` | Silver keeps every CFPB release of a year side by side; a glob stacks them. |
| GNMA | One `{GNMA_SILVER_DIR}/{prefix}/{record_type}/` at a time | Each record type has its own columns. |
| USPS crosswalk | `sources.usps_crosswalk.load.load_crosswalk()` | There is no silver; bronze is the analysis layer. |
| HMDA rows behind a crosswalk | `sources.hmda.load.scan_hmda_for_index(...)` | It reads exactly the HMDA releases the crosswalk was built from. |

## Matching

```bash
mortgage-data match info                                           # list the workflows
mortgage-data match hmda-sellers-purchasers run --min-year 2020 --max-year 2024
```

Each workflow's method, output path, schema and match rates are in the
[matching docs](matching/README.md).

## Help

Every command takes `--help`, the [CLI reference](cli_reference.md) lists them all, and
the API pages document the Python functions. A `ModuleNotFoundError` for a package such
as `pymupdf` or `fastexcel` means that source's extra isn't installed.
