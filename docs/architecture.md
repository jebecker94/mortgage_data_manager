# Architecture

How the package is organized, how data moves through it, and why it is built this way.

## Package layout

Each data source is its own subpackage under `sources/`. The sources share one kernel,
`core/`, and nothing else; the cross-source layers sit beside them:

```
src/mortgage_data_manager/
├── core/               # Configuration, medallion helpers, downloads, logging,
│                       #   reference tables
├── cli/                # The unified `mortgage-data` entry point
├── sources/            # One subpackage per data source
│   ├── hmda/           # Home Mortgage Disclosure Act
│   ├── fha/            # FHA single-family and HECM endorsements
│   ├── gnma/           # Ginnie Mae disclosures
│   ├── fhfa/           # FHFA Public Use Database
│   ├── fnma/           # Fannie Mae loan performance
│   ├── fhlmc/          # Freddie Mac loan-level data
│   ├── umbs/           # UMBS disclosures
│   ├── usps_crosswalk/ # USPS ZIP crosswalks (published by HUD)
│   ├── hud/            # HUD multifamily programs
│   └── fhlb/           # FHLB Acquired Member Assets
├── matching/           # Record linkage: one `match_{workflow}/` per pair of sources,
│                       #   plus `master/`, which joins them onto one HMDA spine
└── combined/           # Cross-source master datasets
```

A data-source subpackage has the same files every time:

```
subpackage/
├── __init__.py         # Re-exports the config class and main functions
├── config.py           # Configuration class
├── download.py         # Fetch into raw
├── import_bronze.py    # raw → bronze
├── import_silver.py    # bronze → silver (every silver writer lives here)
├── pipeline.py         # download → bronze → silver in one call
└── cli.py              # Typer app: one flat module, never a cli/ package
```

The matching workflows are described in the [matching docs](matching/README.md).

## Medallion layers

Every source moves through the same four layers, each in its own directory under the
source's data root:

```
{SUBPACKAGE_DATA_DIR}/
├── raw/       # Files exactly as published
├── bronze/    # Parquet, typed, minimal processing
├── silver/    # Cleaned, hive-partitioned, analysis-ready
└── gold/      # Derived assets promoted out of research use
```

**Raw** holds the files exactly as the publisher released them (CSV, Excel, ZIP, or for
a JSON API the literal response). Nothing is transformed here, so the raw layer can
always be compared against the source.

**Bronze** converts raw to parquet. This is where column types and schemas are fixed
and JSON is flattened, but the rows are otherwise left alone.

**Silver** is the layer you analyse. Values are cleaned and standardized, and the output
is hive-partitioned (for example `activity_year=2024/file_type=c/` for HMDA,
`Year=2024/Month=6/` for FHA) so a query reads only the partitions it needs.

Each layer is written by one module:

```
 download.py        import_bronze.py        import_silver.py
──────────────► raw ────────────────► bronze ────────────────► silver
```

`usps_crosswalk` stops at bronze. Its bronze files already need no further cleaning,
so a silver layer would only copy them; read across quarters with `load_crosswalk()`.

## Configuration

Every subpackage has a config class that inherits `MortgageDataConfig` and adds its own
directories. The [configuration guide](configuration.md) lists every setting.

## CLI

Every subpackage exposes a Typer app, and `cli/main.py` mounts them all under one
`mortgage-data` command. The same verbs recur across sources: `download`, `bronze`,
`silver`, `pipeline` and `info`, with the same option names wherever they apply
(`--min-year`/`--max-year`, `--overwrite`, `--output-dir`, `--datasets`/`-t`, `--pause`).
The matching workflows under `mortgage-data match` use `info`, `run` and `validate`. The
[CLI reference](cli_reference.md) lists every command.

Another installed package can add its own subcommands through the
`mortgage_data_manager.plugins` entry-point group: each entry point is a callable that
receives the top-level Typer app. A plugin that fails to load prints a warning and
leaves the rest of the CLI working.

## Processing patterns

- **Lazy frames throughout.** Builders `scan_parquet`, transform and `sink_parquet`
  without loading a whole file into memory.
- **Hive-partitioned silver.** `core.medallion.write_hive_partitioned` writes one
  directory per partition value. Rows with a null partition key are dropped first, since
  a null has no directory name.
- **Skip what already exists.** Builders process one year or file at a time and skip
  outputs already on disk (`core.medallion.should_process_file`), so an interrupted build
  resumes where it stopped. `--overwrite` rebuilds, and it only deletes the partitions
  being rebuilt.

## Design decisions

- **Polars, not pandas.** Lazy evaluation and the Arrow backend are what make
  multi-billion-row loan-month panels tractable on one machine. `.to_pandas()`
  is there for the libraries that need it.
- **Parquet from bronze onward.** Typed, compressed, and cheap to subset;
  the raw layer stays in the publisher's original format so nothing is lost.
- **A layer per transformation.** Downstream layers are rebuildable from
  upstream ones, and an intermediate stage is inspectable when a build goes
  wrong. The cost is disk, which is cheaper than a re-download.
- **One package, many extras.** Shared `core` utilities and one configuration
  model beat six near-identical packages; extras keep a narrow install narrow.
