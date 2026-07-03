# FNMA API Reference

API reference for the FNMA (Fannie Mae - Federal National Mortgage Association) subpackage.

## Table of Contents

- [Configuration](#configuration)
- [Import (Bronze Layer)](#import-bronze-layer)
- [Import (Silver Layer)](#import-silver-layer)
- [CLI](#cli)

---

## Configuration

### `FNMAConfig`

Configuration class for FNMA data management.

**Module:** `mortgage_data_manager.fnma.config`

**Inherits from:** `MortgageDataConfig`

#### Class Attributes

| Attribute | Type | Description | Environment Variable |
|-----------|------|-------------|---------------------|
| `FNMA_DATA_DIR` | `Path` | Root directory for FNMA data | `FNMA_DATA_DIR` |
| `FNMA_RAW_DIR` | `Path` | Raw data directory | `FNMA_RAW_DIR` |
| `FNMA_BRONZE_DIR` | `Path` | Bronze layer directory | `FNMA_BRONZE_DIR` |
| `FNMA_SILVER_DIR` | `Path` | Silver layer directory | `FNMA_SILVER_DIR` |
| `FNMA_GOLD_DIR` | `Path` | Gold layer directory | `FNMA_GOLD_DIR` |
| `FNMA_DICTIONARY_DIR` | `Path` | Dictionary files directory | `FNMA_DICTIONARY_DIR` |
| `FNMA_DICTIONARY_FILE` | `Path` | Data dictionary Excel file | - |

#### Processing Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `DEFAULT_DELIMITER` | `\|` | Default field delimiter |
| `DEFAULT_ENCODING` | `utf-8` | Default file encoding |

#### Methods

##### `get_prefix_dir(stage: Literal["raw", "bronze", "silver", "gold"], prefix: str) -> Path`

Get the prefix sub-directory under an FNMA medallion stage.

**Parameters:**
- `stage`: Medallion stage ('raw', 'bronze', 'silver', or 'gold')
- `prefix`: Data prefix naming the sub-directory (e.g., 'issuances')

**Returns:**
- `Path`: Path to the prefix sub-directory under the stage

**Example:**
```python
from mortgage_data_manager.fnma.config import FNMAConfig

silver_dir = FNMAConfig.get_prefix_dir('silver', 'issuances')
print(silver_dir)  # /path/to/data/fnma/silver/issuances
```

For the bare stage directory (no prefix), use the per-dir attributes
(`FNMA_BRONZE_DIR`, etc.) or the inherited base API
`get_medallion_dir('fnma', 'bronze')`.

##### `ensure_directories() -> None`

Create necessary directories for FNMA data processing.

**Example:**
```python
from mortgage_data_manager.fnma.config import FNMAConfig

FNMAConfig.ensure_directories()
# Creates: data/fnma/raw, data/fnma/bronze, data/fnma/silver, etc.
```

---

## Import (Bronze Layer)

### Bronze Import Functions

**Module:** `mortgage_data_manager.fnma.import_bronze`

Import FNMA loan performance data from zipped CSVs to Parquet files.

#### `read_data_dictionary(excel_path: Path) -> pd.DataFrame`

Read the Excel data dictionary and extract field definitions.

**Parameters:**
- `excel_path`: Path to the Excel data dictionary file

**Returns:**
- `pd.DataFrame`: DataFrame with field definitions

**Example:**
```python
from mortgage_data_manager.fnma.import_bronze import read_data_dictionary

dict_df = read_data_dictionary(FNMAConfig.FNMA_DICTIONARY_FILE)
print(f"Found {len(dict_df)} field definitions")
```

#### `create_column_schema(dict_df: pd.DataFrame) -> tuple[list[str], dict]`

Create Polars schema with column names and dtypes.

**Parameters:**
- `dict_df`: DataFrame from `read_data_dictionary`

**Returns:**
- Tuple of (column_names, schema_dict)

**Example:**
```python
from mortgage_data_manager.fnma.import_bronze import read_data_dictionary, create_column_schema

dict_df = read_data_dictionary(FNMAConfig.FNMA_DICTIONARY_FILE)
column_names, schema = create_column_schema(dict_df)
```

#### `read_csv_from_zip_and_save(zip_path: Path, column_names: list, schema: dict, output_path: Path) -> None`

Read CSV from zip file and save directly to parquet using streaming.

**Parameters:**
- `zip_path`: Path to the input zip file
- `column_names`: List of column names from schema
- `schema`: Polars schema dictionary
- `output_path`: Path for output parquet file

**Example:**
```python
from mortgage_data_manager.fnma.import_bronze import (
    read_data_dictionary,
    create_column_schema,
    read_csv_from_zip_and_save,
)
from mortgage_data_manager.fnma.config import FNMAConfig

# Setup
dict_df = read_data_dictionary(FNMAConfig.FNMA_DICTIONARY_FILE)
column_names, schema = create_column_schema(dict_df)

# Process a zip file
zip_path = FNMAConfig.FNMA_RAW_DIR / "2023Q1.zip"
output_path = FNMAConfig.FNMA_BRONZE_DIR / "2023Q1.parquet"

read_csv_from_zip_and_save(zip_path, column_names, schema, output_path)
```

#### `main() -> None`

Main execution function that processes all zip files in the raw directory.

---

## Import (Silver Layer)

### Silver Import Functions

**Module:** `mortgage_data_manager.fnma.import_silver`

Transform bronze data to cleaned, standardized silver layer.

Silver layer features:
- Data type standardization
- Missing value handling
- Derived fields
- Parquet compression

---

## CLI

### Commands

**Module:** `mortgage_data_manager.fnma.cli`

#### `mortgage-data fnma info`

Display FNMA configuration information.

```bash
mortgage-data fnma info
```

#### `mortgage-data fnma download`

Download FNMA data files.

```bash
# Show download instructions
mortgage-data fnma download

# Note: FNMA requires manual registration at fanniemae.com
```

**Note:** FNMA data requires manual registration and download from the Fannie Mae website. The CLI provides guidance on the download process.

#### `mortgage-data fnma bronze`

Import raw FNMA loan-performance zip files to bronze parquet.

```bash
mortgage-data fnma bronze
mortgage-data fnma bronze --overwrite
```

Processes per-quarter zips (`YYYYQ[1-4]*.zip`) in the raw directory and writes one parquet per quarter to the bronze directory.

#### `mortgage-data fnma silver`

Extract issuances and terminations from bronze loan-performance parquet.

```bash
mortgage-data fnma silver
```

Reads each quarterly parquet in the bronze directory and writes two silver tables: `silver/issuances/` (first observation per loan) and `silver/terminations/` (all observations where the loan has terminated).

---

## Complete Example

Full pipeline from raw to bronze:

```python
import polars as pl
from pathlib import Path
from mortgage_data_manager.fnma.config import FNMAConfig
from mortgage_data_manager.fnma.import_bronze import (
    read_data_dictionary,
    create_column_schema,
    read_csv_from_zip_and_save,
)
from mortgage_data_manager.core.logging import configure_logging, get_logger

# Setup logging
configure_logging(level="INFO")
logger = get_logger(__name__)

# Ensure directories exist
FNMAConfig.ensure_directories()

# Read data dictionary
dict_path = FNMAConfig.FNMA_DICTIONARY_FILE
dict_df = read_data_dictionary(dict_path)
logger.info(f"Loaded {len(dict_df)} field definitions")

# Create column schema
column_names, schema = create_column_schema(dict_df)
logger.info(f"Created schema with {len(column_names)} columns")

# Get list of zip files
raw_dir = FNMAConfig.FNMA_RAW_DIR
bronze_dir = FNMAConfig.FNMA_BRONZE_DIR
zip_files = sorted(raw_dir.glob("*.zip"))

logger.info(f"Found {len(zip_files)} files to process")

# Process each zip file
for zip_path in zip_files:
    output_path = bronze_dir / f"{zip_path.stem}.parquet"

    if output_path.exists():
        logger.info(f"Skipping {zip_path.name} (already exists)")
        continue

    logger.info(f"Processing {zip_path.name}...")
    read_csv_from_zip_and_save(zip_path, column_names, schema, output_path)
    logger.info(f"Saved {output_path.name}")

logger.info("Processing complete!")

# Read and verify bronze data
parquet_files = list(bronze_dir.glob("*.parquet"))
logger.info(f"Created {len(parquet_files)} parquet files")

# Sample verification
df = pl.scan_parquet(bronze_dir / "*.parquet")
row_count = df.select(pl.len()).collect().item()
logger.info(f"Total rows: {row_count:,}")
```

---

## Data Structure

### Directory Layout

```
{FNMA_DATA_DIR}/
├── raw/
│   ├── 2020Q1.zip         # Quarterly data files
│   ├── 2020Q2.zip
│   └── ...
├── bronze/
│   ├── 2020Q1.parquet     # Converted parquet files
│   ├── 2020Q2.parquet
│   └── ...
├── silver/
│   └── ...                # Cleaned, standardized data
└── dictionary_files/
    └── crt-file-layout-and-glossary.xlsx
```

### Data Dictionary

The FNMA data dictionary is an Excel file containing:
- Field Position: Column order (1-110)
- Field Name: Column name
- Type: Data type (NUMERIC, DATE, ALPHA-NUMERIC)
- Max Length: Field length specification

### File Format

FNMA loan performance files are:
- Pipe-delimited (`|`)
- No header row
- 110 columns per record
- Zipped CSV format

### Medallion Architecture

1. **Raw**: Downloaded ZIP files from Fannie Mae
2. **Bronze**: Converted to Parquet with proper schema
3. **Silver**: Cleaned, typed, and standardized

---

## Best Practices

1. **Manual Download Required**: Register at fanniemae.com for data access
2. **Use Data Dictionary**: Always use the official data dictionary for schema
3. **Stream Large Files**: Use `scan_csv` and `sink_parquet` for memory efficiency
4. **Check Disk Space**: Parquet files are compressed but still large
5. **Verify Schema**: Confirm column count matches dictionary

## Data Dictionary

### Field Mapping

**Data Dictionary:** 110 fields defined in `crt-file-layout-and-glossary.xlsx`
**CSV Files:** 108 fields per record
**Compatibility:** COMPATIBLE (with 2 excluded fields)

The CSV data maps to fields **2-109** of the data dictionary, excluding:
- **Field 1:** Reference Pool ID (CAS/CIRT specific, not applicable to Single-Family Loan Performance data)
- **Field 110:** Interest Bearing UPB (not present in these files)

### Sample Data Mapping

| CSV Position | Value | Dictionary Field # | Field Name | Match |
|--------------|-------|-------------------|------------|-------|
| 1 | 000140038760 | 2 | Loan Identifier | (12-digit ID) |
| 2 | 042025 | 3 | Monthly Reporting Period | (MMYYYY format) |
| 3 | R | 4 | Channel | (R=Retail) |
| 4 | Other | 5 | Seller Name | |
| 5 | Other | 6 | Servicer Name | |
| 6 | (empty) | 7 | Master Servicer | |
| 7 | 6.125 | 8 | Original Interest Rate | (percentage) |
| 8 | 6.125 | 9 | Current Interest Rate | (percentage) |
| 9 | 91000.00 | 10 | Original UPB | (dollar amount) |
| 10 | (empty) | 11 | UPB at Issuance | (CAS/CIRT field, empty for SF) |
| 11 | 91000.00 | 12 | Current Actual UPB | (dollar amount) |
| 12 | 180 | 13 | Original Loan Term | (months) |
| 13 | 042025 | 14 | Origination Date | (MMYYYY) |
| 14 | 062025 | 15 | First Payment Date | (MMYYYY) |
| 15 | -1 | 16 | Loan Age | (-1 indicates pre-first payment) |
| 16 | 181 | 17 | Remaining Months to Legal Maturity | |
| 17 | 180 | 18 | Remaining Months To Maturity | |
| 18 | 052040 | 19 | Maturity Date | (MMYYYY) |
| 19 | 42 | 20 | Original LTV | (percentage) |
| 20 | 42 | 21 | Original CLTV | (percentage) |
| 21 | 1 | 22 | Number of Borrowers | |
| 22 | 42 | 23 | Debt-To-Income (DTI) | (percentage) |
| 23 | 689 | 24 | Borrower Credit Score at Origination | (FICO score) |
| 24 | (empty) | 25 | Co-Borrower Credit Score at Origination | (no co-borrower) |
| 25 | N | 26 | First Time Home Buyer Indicator | (N=No) |
| 26 | R | 27 | Loan Purpose | (R=Refinance) |
| 27 | SF | 28 | Property Type | (SF=Single-Family) |
| 28 | 1 | 29 | Number of Units | |
| 29 | P | 30 | Occupancy Status | (P=Principal) |
| 30 | IL | 31 | Property State | (Illinois) |

### Enumeration Codes

Sample enumerations verified:
- **Channel:** "R", "C", "B" (Retail, Correspondent, Broker)
- **Property Type:** "SF", "PU", "CO", "MH", "CP"
- **Loan Purpose:** "P", "R", "C" (Purchase, Refinance, Cash-out Refinance)
- **Occupancy Status:** "P", "I", "S" (Principal, Investor, Second)
- **First Time Home Buyer:** "Y", "N", blank

### Data Loading Recommendations

1. **Skip Field 1:** Do not expect Reference Pool ID in the data
2. **Use Fields 2-109:** Map CSV columns to dictionary fields 2-109
3. **Create Header File:** Generate a CSV header row using field names from positions 2-109

### Sample Header Row

```
Loan Identifier,Monthly Reporting Period,Channel,Seller Name,Servicer Name,Master Servicer,Original Interest Rate,Current Interest Rate,Original UPB,UPB at Issuance,Current Actual UPB,Original Loan Term,Origination Date,First Payment Date,Loan Age,Remaining Months to Legal Maturity,Remaining Months To Maturity,Maturity Date,Original Loan to Value Ratio (LTV),Original Combined Loan to Value Ratio (CLTV),Number of Borrowers,Debt-To-Income (DTI),Borrower Credit Score at Origination,Co-Borrower Credit Score at Origination,First Time Home Buyer Indicator,Loan Purpose,Property Type,Number of Units,Occupancy Status,Property State,Metropolitan Statistical Area (MSA),Zip Code Short,Mortgage Insurance Percentage,Amortization Type,Prepayment Penalty Indicator,Interest Only Loan Indicator,Interest Only First Principal And Interest Payment Date,Months to Amortization,Current Loan Delinquency Status,Loan Payment History,Modification Flag,Mortgage Insurance Cancellation Indicator,Zero Balance Code,Zero Balance Effective Date,UPB at the Time of Removal,Repurchase Date,Scheduled Principal Current,Total Principal Current,Unscheduled Principal Current,Last Paid Installment Date,Foreclosure Date,Disposition Date,Foreclosure Costs,Property Preservation and Repair Costs,Asset Recovery Costs,Miscellaneous Holding Expenses and Credits,Associated Taxes for Holding Property,Net Sales Proceeds,Credit Enhancement Proceeds,Repurchase Make Whole Proceeds,Other Foreclosure Proceeds,Modification-Related Non-Interest Bearing UPB,Principal Forgiveness Amount,Original List Start Date,Original List Price,Current List Start Date,Current List Price,Borrower Credit Score At Issuance,Co-Borrower Credit Score At Issuance,Borrower Credit Score Current,Co-Borrower Credit Score Current,Mortgage Insurance Type,Servicing Activity Indicator,Current Period Modification Loss Amount,Cumulative Modification Loss Amount,Current Period Credit Event Net Gain or Loss,Cumulative Credit Event Net Gain or Loss,Special Eligibility Program,Foreclosure Principal Write-off Amount,Relocation Mortgage Indicator,Zero Balance Code Change Date,Loan Holdback Indicator,Loan Holdback Effective Date,Delinquent Accrued Interest,Property Valuation Method,High Balance Loan Indicator,ARM Initial Fixed-Rate Period 5 YR Indicator,ARM Product Type,Initial Fixed-Rate Period,Interest Rate Adjustment Frequency,Next Interest Rate Adjustment Date,Next Payment Change Date,Index,ARM Cap Structure,Initial Interest Rate Cap Up Percent,Periodic Interest Rate Cap Up Percent,Lifetime Interest Rate Cap Up Percent,Mortgage Margin,ARM Balloon Indicator,ARM Plan Number,Borrower Assistance Plan,High Loan to Value (HLTV) Refinance Option Indicator,Deal Name,Repurchase Make Whole Proceeds Flag,Alternative Delinquency Resolution,Alternative Delinquency Resolution Count,Total Deferral Amount,Payment Deferral Modification Event Indicator
```

### Data Quality Notes

- All 108 fields in CSV files map correctly to expected dictionary positions
- Data types match expectations (dates in MMYYYY, numerics, codes match enumerations)
- Field order is consistent across all 26 quarterly files
- No schema drift detected from 2019Q1 through 2025Q2

---

## See Also

- [Core API](core.md) - Shared utilities and medallion architecture
- [Configuration Guide](../configuration.md) - Environment variables and paths
- [FHLMC API](fhlmc.md) - Similar GSE data processing
