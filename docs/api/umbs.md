# UMBS API Reference

API reference for the UMBS (Uniform Mortgage-Backed Securities) subpackage.

## Table of Contents

- [Configuration](#configuration)
- [Import (Bronze Layer)](#import-bronze-layer)
- [CLI](#cli)

---

## Configuration

### `UMBSConfig`

Configuration class for UMBS data management.

**Module:** `mortgage_data_manager.umbs.config`

**Inherits from:** `MortgageDataConfig`

#### Class Attributes

| Attribute | Type | Description | Environment Variable |
|-----------|------|-------------|---------------------|
| `UMBS_DATA_DIR` | `Path` | Root directory for UMBS data | `UMBS_DATA_DIR` |
| `UMBS_RAW_DIR` | `Path` | Raw data directory | `UMBS_RAW_DIR` |
| `UMBS_BRONZE_DIR` | `Path` | Bronze layer directory | `UMBS_BRONZE_DIR` |
| `UMBS_SILVER_DIR` | `Path` | Silver layer directory | `UMBS_SILVER_DIR` |
| `UMBS_GOLD_DIR` | `Path` | Gold layer directory | `UMBS_GOLD_DIR` |

#### Methods

##### `get_prefix_dir(stage: Literal["raw", "bronze", "silver", "gold"], prefix: str) -> Path`

Get the prefix sub-directory under a UMBS medallion stage.

**Parameters:**
- `stage`: Medallion stage ('raw', 'bronze', 'silver', or 'gold')
- `prefix`: Data prefix naming the sub-directory (e.g., 'issuances')

**Returns:**
- `Path`: Path to the prefix sub-directory under the stage

**Example:**
```python
from mortgage_data_manager.umbs.config import UMBSConfig

silver_dir = UMBSConfig.get_prefix_dir('silver', 'issuances')
print(silver_dir)  # /path/to/data/umbs/silver/issuances
```

For the bare stage directory (no prefix), use the per-dir attributes
(`UMBS_BRONZE_DIR`, etc.) or the inherited base API
`get_medallion_dir('umbs', 'bronze')`.

##### `ensure_directories() -> None`

Create necessary directories for UMBS data processing.

**Example:**
```python
from mortgage_data_manager.umbs.config import UMBSConfig

UMBSConfig.ensure_directories()
# Creates: data/umbs/raw, data/umbs/bronze, data/umbs/silver, etc.
```

---

## Import (Bronze Layer)

### Bronze Import Functions

**Module:** `mortgage_data_manager.umbs.import_bronze`

Import GSE (FNMA and FHLMC) raw data files to bronze layer in Parquet format.

This module processes pipe-delimited files with headers from the raw directory and converts them to Parquet format, preserving the GSE and folder structure.

#### Supported Data Folders

**FNMA Folders:**
- `FNM_GN_MEGA`
- `FNM_DPR_FCTR`
- `FNM_ESF_MS`, `FNM_ESF_MSS`
- `FNM_IS`, `FNM_ISS`
- `FNM_RIS`, `FNM_RISS`
- `FNM_REMIC`, `FNM_REMIC_COMPONENT`, `FNM_REMIC_SHORT`
- `FNM_MF`
- `FNM_ILLD`, `FNM_MLLD`

**FHLMC Folders:**
- `FRE_DPR_Fctr`
- `FRE_ILLD`
- `FRE_IS`, `FRE_ISS`
- `FRE_Multilender`
- `FRE_RIS`, `FRE_RISS`
- Two-letter codes: `AU`, `FU`, `AC`, `ML`, `MI`, `AR`, `PF`, `XF`, `FD`

**Excluded (require special handling):**
- FNMA: `FNM_MS`, `SIFMA`, `REMICSUP`, `FNM_DEALERREPORT`, `RC`
- FHLMC: `GE`, `FQ`, `MJ`, `XS` (no headers)

#### `get_zip_files(raw_dir: Path) -> dict[str, dict[str, list[Path]]]`

Get all zip files from the raw data directories.

**Parameters:**
- `raw_dir`: Root raw data directory

**Returns:**
- Nested dictionary: `{GSE: {folder: [zip_files]}}`

**Example:**
```python
from mortgage_data_manager.umbs.import_bronze import get_zip_files
from mortgage_data_manager.umbs.config import UMBSConfig

zip_files = get_zip_files(UMBSConfig.UMBS_RAW_DIR)
# {'FNMA': {'FNM_IS': [Path('...'), ...], ...}, 'FHLMC': {...}}
```

#### `process_zip_to_parquet(zip_path: Path, output_dir: Path, overwrite: bool = False) -> bool`

Extract a zip file, scan with Polars, and sink to Parquet.

**Parameters:**
- `zip_path`: Path to the zip file
- `output_dir`: Directory where parquet file should be saved
- `overwrite`: If False, skip files that already exist

**Returns:**
- `bool`: True if successful, False otherwise

**Example:**
```python
from mortgage_data_manager.umbs.import_bronze import process_zip_to_parquet
from mortgage_data_manager.umbs.config import UMBSConfig

zip_path = UMBSConfig.UMBS_RAW_DIR / "FNMA" / "FNM_IS" / "data.zip"
output_dir = UMBSConfig.UMBS_BRONZE_DIR / "FNMA" / "FNM_IS"

success = process_zip_to_parquet(zip_path, output_dir)
```

#### `main() -> None`

Main execution function that processes all supported zip files.

**Command-line usage:**
```bash
python -m mortgage_data_manager.umbs.import_bronze

# With overwrite flag
python -m mortgage_data_manager.umbs.import_bronze --overwrite
```

---

## CLI

### Commands

**Module:** `mortgage_data_manager.umbs.cli`

#### `mortgage-data umbs info`

Display UMBS configuration information.

```bash
mortgage-data umbs info
```

#### `mortgage-data umbs download`

Download UMBS data files.

```bash
# Show download instructions
mortgage-data umbs download

# Note: UMBS requires manual registration
```

**Note:** UMBS data requires manual registration. The CLI provides guidance on the download process.

---

## Complete Example

Full pipeline from raw to bronze:

```python
import polars as pl
from mortgage_data_manager.umbs.config import UMBSConfig
from mortgage_data_manager.umbs.import_bronze import (
    get_zip_files,
    process_zip_to_parquet,
)
from mortgage_data_manager.core.logging import configure_logging, get_logger

# Setup logging
configure_logging(level="INFO")
logger = get_logger(__name__)

# Ensure directories exist
UMBSConfig.ensure_directories()

# Get all zip files
raw_dir = UMBSConfig.UMBS_RAW_DIR
bronze_dir = UMBSConfig.UMBS_BRONZE_DIR

zip_files = get_zip_files(raw_dir)

# Count total files
total_files = sum(
    len(files)
    for gse_folders in zip_files.values()
    for files in gse_folders.values()
)

logger.info(f"Found {total_files} files to process")

# Process each GSE's folders
successful = 0
failed = 0

for gse, gse_folders in zip_files.items():
    for folder, zip_list in gse_folders.items():
        bronze_subfolder = bronze_dir / gse / folder

        for zip_path in zip_list:
            logger.info(f"Processing {zip_path.name}")

            if process_zip_to_parquet(zip_path, bronze_subfolder):
                successful += 1
            else:
                failed += 1

logger.info(f"Processed: {successful} successful, {failed} failed")

# Verify data
parquet_files = list(bronze_dir.rglob("*.parquet"))
logger.info(f"Created {len(parquet_files)} parquet files")
```

---

## Data Structure

### Directory Layout

```
{UMBS_DATA_DIR}/
├── raw/
│   ├── FNMA/
│   │   ├── FNM_IS/
│   │   │   └── data.zip
│   │   ├── FNM_ISS/
│   │   └── ...
│   └── FHLMC/
│       ├── FRE_IS/
│       ├── AU/
│       └── ...
├── bronze/
│   ├── FNMA/
│   │   ├── FNM_IS/
│   │   │   └── data.parquet
│   │   └── ...
│   └── FHLMC/
│       └── ...
└── silver/
    └── ...
```

### File Formats

| Source Format | Description | Output Format |
|--------------|-------------|---------------|
| ZIP containing TXT | Pipe-delimited with headers | Parquet (zstd) |

### GSE Structure

The UMBS module handles data from both GSEs:
- **FNMA (Fannie Mae)**: Various disclosure and issuance data
- **FHLMC (Freddie Mac)**: Matching disclosure data types

Both are processed together since they share similar formats and are part of the Uniform MBS program.

### Medallion Architecture

1. **Raw**: Downloaded ZIP files organized by GSE and data type
2. **Bronze**: Converted to Parquet preserving folder structure
3. **Silver**: Cleaned and standardized (future)

---

## Best Practices

1. **Preserve Structure**: Keep GSE/folder hierarchy in bronze layer
2. **Skip Existing**: Use `overwrite=False` to avoid reprocessing
3. **Check File Formats**: Some folders require special handling
4. **Monitor Large Files**: Files over 500MB get warnings
5. **Verify Headers**: Ensure files have headers before processing

## Data Formats

### Overview

The raw data directory contains mortgage-backed securities and REMIC data from two GSEs:
- **FNMA (Fannie Mae)**: Various disclosure and issuance data types
- **FHLMC (Freddie Mac)**: Matching disclosure data types

### FNMA File Formats

#### Format Type 1: Pipe-Delimited Text Files (Most Common)

**Files:** FNM_GN_MEGA, FNM_DPR_FCTR, FNM_ESF_MS, FNM_ESF_MSS, FNM_IS, FNM_ISS, FNM_RIS, FNM_REMIC, FNM_REMIC_COMPONENT, FNM_REMIC_SHORT, FNM_MS, FNM_RISS, FNM_MF, FNM_ILLD, FNM_MLLD

**Structure:**
- Delimiter: Pipe character (`|`)
- Headers: First row contains column names
- Encoding: UTF-8
- Line endings: Standard text

**Sample (FNM_GN_MEGA):**
```
Security Identifier|Issue Date|Issuance Investor Security UPB|WA Current Interest Rate|...
458077|08012002|6919901.00|7.500|13|GN|MEGA|31381D2J3|...
```

**Parsing Recommendations:**
```python
import polars as pl

# For files with headers
df = pl.scan_csv('filename.txt', separator='|', encoding='utf8')

# For very large files, use chunking
# or process directly to parquet
df.sink_parquet('output.parquet')
```

#### Format Type 2: Fixed-Width Report Files

**Files:** SIFMA, REMICSUP

**Structure:**
- Fixed-width columns with headers
- Contains formatted report headers and footers
- Multiple header rows with metadata
- Data aligned in columns with spacing

**Parsing Recommendations:**
```python
import pandas as pd

# Skip header/footer rows and use fixed-width parser
colspecs = [(0, 10), (10, 30), (30, 40), ...]  # Define column positions

df = pd.read_fwf('SIFMA_202512.txt',
                  skiprows=6,  # Skip header rows
                  skipfooter=2,  # Skip footer if present
                  colspecs=colspecs)
```

#### Format Type 3: Excel Binary Format

**Files:** FNM_DEALERREPORT

**Structure:**
- Genuine Microsoft Excel file
- Requires `xlrd` engine for .xls format

**Parsing:**
```python
import pandas as pd
df = pd.read_excel('FNM_DEALERREPORT.xls', engine='xlrd')
```

### FHLMC File Formats

#### FRE_ Prefixed Files: Pipe-Delimited with Headers

**Files:** FRE_DPR_Fctr, FRE_ILLD, FRE_IS, FRE_ISS, FRE_Multilender, FRE_RIS, FRE_RISS

**Structure:**
- Delimiter: Pipe character (`|`)
- Headers: First row contains column names
- Encoding: UTF-8
- Similar format to FNMA's FNM_ prefixed files

**Sample (FRE_ILLD):**
```
Loan Identifier|Loan Correction Indicator|Prefix|Security Identifier|CUSIP|...
9850651358|N|CL|QX3416|31425XYN3|160000.00|160000.00|160000.00|FRM|...
```

#### Two-Letter Code Files: Mixed Format

**WITH Headers (9 types):**
- **AU, FU, AC:** Loan-Level Disclosure data
- **ML:** Multifamily Loan-Level data
- **MI, AR, FD, XF:** Security/Pool Issuance data
- **PF:** Pool Factor data

**WITHOUT Headers (4 types):**
- **GE, FQ, MJ, XS:** Quantile/Distribution data (MAX, 75th, MED, 25th, MIN percentiles)

**Sample (GE - No Headers):**
```
1|HA|HA0001|3132NMAA2|MAX|488000.00|5.000|4.750|478|299|173|||63||||816|100||7777
1|HA|HA0001|3132NMAA2|75|358000.00|4.125|3.875|437|245|170|||43||||703|90||7777
```

### FHLMC File Format Matrix

| Code | Has Headers | Data Type | Description |
|------|-------------|-----------|-------------|
| FRE_IS | Yes | Issuance | Security issuance data |
| FRE_ISS | Yes | Issuance | Individual security data |
| FRE_ILLD | Yes | Loan-Level | Loan-level disclosure |
| FRE_RIS | Yes | REMIC Issuance | REMIC issuance data |
| FRE_RISS | Yes | REMIC Issuance Supp | REMIC supplement |
| FRE_Multilender | Yes | Multifamily | Multifamily data |
| FRE_DPR_Fctr | Yes | Factor | Factor data |
| AU | Yes | Loan-Level | Loan-level disclosure (Alt format) |
| FU | Yes | Loan-Level | Loan-level disclosure |
| AC | Yes | Loan-Level | Loan-level disclosure |
| ML | Yes | Multifamily | Multifamily loan data |
| MI | Yes | Security/Pool | Security issuance data |
| AR | Yes | Security/Pool | Security issuance data |
| FD | Yes | Security/Pool | Security issuance data |
| XF | Yes | Security/Pool | Security issuance data |
| PF | Yes | Factor | Pool factor data |
| GE | **No** | Quantile | Distribution/quantile data |
| FQ | **No** | Quantile | Distribution/quantile data |
| MJ | **No** | Quantile | Multifamily quantile data |
| XS | **No** | Quantile | Distribution/quantile data |

### Compressed File Handling

All `.zip` files contain a single `.txt` file. Some files are very large when decompressed.

**Large Files (Handle with Care):**
- `FNM_MLLD_202512.zip` - 558 MB compressed
- `FNM_MS_202512.zip` - 389 MB compressed (1.6 GB uncompressed)
- `FNM_MF_202512.zip` - 37 MB compressed

**Streaming Recommendation:**
```python
import zipfile
import io

with zipfile.ZipFile('large_file.zip') as z:
    filename = z.namelist()[0]
    with z.open(filename) as f:
        # Read in chunks
        for line in io.TextIOWrapper(f, encoding='utf-8'):
            process_line(line)
```

### Data Content Categories

**Securities Data:**
- FNM_IS/ISS: Individual securities with LTV, FICO, rates, etc.
- FNM_RIS/RISS: **Reissued** IS/ISS — see [Reissue (R-) files](#reissue-r--files-and-the-silver-layer) below
- FNM_MS: Mega securities data (very large)
- FNM_ESF_MS/MSS: ESF Mega Securities with trust/class information
- FNM_GN_MEGA: GNMA mega pool securities

**REMIC Data:**
- FNM_REMIC: REMIC trust and class information
- FNM_REMIC_COMPONENT: Component class details
- FNM_REMIC_SHORT: Shortfall information
- REMICSUP: REMIC component class report (formatted)

**Loan-Level Data:**
- FNM_MLLD: Mega Loan-Level Data (558 MB - very detailed)
- FNM_ILLD: Individual Loan-Level Data
- FNM_RILLD: **Reissued** ILLD — see [Reissue (R-) files](#reissue-r--files-and-the-silver-layer) below

**Factor & Pool Data:**
- FNM_DPR_FCTR: DPR Factor data (principal reduction tracking)
- FNM_MF: Multifamily data

---

## Reissue (R-) files and the silver layer

GSE disclosure folders come in matched original/reissue pairs:

| Kind | FNMA original / reissue | FHLMC original / reissue |
|------|------------------------|-------------------------|
| Loan-level issuance | `FNM_ILLD` / `FNM_RILLD` | `FRE_ILLD` / `FRE_RILLD` |
| Security-level issuance | `FNM_IS` / `FNM_RIS` | `FRE_IS` / `FRE_RIS` |
| Issuance stratified | `FNM_ISS` / `FNM_RISS` | `FRE_ISS` / `FRE_RISS` |

The `R-` file is a **same-month republication of every pool/security in which any field was corrected**, not a delta. It republishes *all* loans in a touched pool — the `Loan/Security Correction Indicator` column is `Y` only for the loans whose fields actually changed, and `N` for the unchanged-but-republished siblings. Empirically (verified across 70+ months of `FNM_RIS` and `FRE_RIS` and all available `RILLD` months) **every R-file row matches a same-month original** — no cross-month corrections.

**Silver consolidates the pair into one dataset per kind.** For each month, the silver importer (`umbs.import_silver`) takes the original for any key not present in that month's R-file and the R-file's row otherwise, then adds a `_record_source` column (`'original'` or `'correction'`). The pairings live in `UMBSConfig.CORRECTION_PAIRS` — register new corrected files there, not in the importer.

```
silver/
├── FNMA/{ILLD,IS,ISS}/{ILLD,IS,ISS}_YYYYMM.parquet
└── FHLMC/{ILLD,IS,ISS}/{ILLD,IS,ISS}_YYYYMM.parquet
```

**Known issue (ISS/RISS):** The underlying `.txt` files have no header row (they're stratified `MAX/75/MED/25/MIN` files), but bronze currently imports them with `has_header=True`, consuming the first data row as column names. The schema therefore changes every month and the silver importer skips both ISS pairs until bronze is fixed. The pair is still registered in `CORRECTION_PAIRS` so silver picks them up automatically once the bronze bug is resolved.

---

## See Also

- [Core API](core.md) - Shared utilities and medallion architecture
- [Configuration Guide](../configuration.md) - Environment variables and paths
- [FNMA API](fnma.md) - Fannie Mae specific processing
- [FHLMC API](fhlmc.md) - Freddie Mac specific processing
