"""Schema loading and parsing functions.

The Single-Family Loan-Level Dataset layouts are transcribed from Freddie Mac's file
layout workbooks (published on the Single-Family Loan-Level Dataset page,
https://www.freddiemac.com/research/datasets/sf-loanlevel-dataset) into
``schemas/fhlmc/sf_file_layout_july_2026.csv`` (Release 47) and
``schemas/fhlmc/sf_file_layout.csv`` (the earlier layout). Each CSV has one row per
field: ``File`` (the workbook's sheet, e.g. ``Origination Data File``),
``Field Position``, ``Attribute Name``, ``Data Type & Format`` and ``Max Length``.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import polars as pl

from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Release 47 (July 2026) standard-dataset layout change
# ---------------------------------------------------------------------------
# Freddie reissued the WHOLE standard Single-Family Loan-Level Dataset
# (1999Q1-2026Q1) at Release 47 under new file names and new layouts:
#
#   orig_YYYYQn.txt  31 fields (was historical_data_YYYYQn.txt,      32)
#   perf_YYYYQn.txt  35 fields (was historical_data_time_YYYYQn.txt, 32)
#
# Two attributes MOVED out of origination into the monthly file (Servicer Name,
# Mortgage Insurance Cancellation Indicator), two are NEW (origination
# VantageScore 4.0, performance Bankruptcy Cramdown Costs), and ~10 were
# renamed. The renames adopt the same vocabulary the CRT reference-pool layout
# already uses here (``Classic FICO``, ``Loan Identifier``, ``Special
# Eligibility Program``, ...), so bronze now agrees with ``CRT_LLD_LAYOUT``.
#
# Bronze stores the R47 names verbatim (bronze stays faithful to source); the
# silver builders rename to the established names so existing consumers keep
# working. See Freddie's "Disclosure Changes Summary (Effective July 2026)" at
# https://www.freddiemac.com/research/datasets/sf-loanlevel-dataset.
#
# The non-standard (``historical_data_excl_*``) bundle was NOT reissued and
# still carries the pre-R47 layout, so both eras must load side by side.

#: R47 origination name -> pre-R47 name. Names not listed are unchanged.
#: ``VantageScore® 4.0`` is new (no pre-R47 counterpart) but is de-symbolled to
#: match the ``VantageScore 4.0`` spelling ``CRT_LLD_LAYOUT`` already uses.
R47_ORIGINATION_RENAMES: dict[str, str] = {
    "Classic FICO®": "Credit Score",
    "First Time Homebuyer Indicator": "First Time Homebuyer Flag",
    "Prepayment Penalty Indicator": "Prepayment Penalty Mortgage (PPM) Flag",
    "Amortization Type": "Amortization Type (Formerly Product Type)",
    "Loan Identifier": "Loan Sequence Number",
    "Special Eligibility Program": "Program Indicator",
    "VantageScore® 4.0": "VantageScore 4.0",
}

#: R47 performance name -> pre-R47 name. The three appended attributes
#: (Mortgage Insurance Cancellation Indicator, Servicer Name, Bankruptcy
#: Cramdown Costs) are absent here — they keep their R47 names into silver.
R47_PERFORMANCE_RENAMES: dict[str, str] = {
    "Loan Identifier": "Loan Sequence Number",
    "Period": "Monthly Reporting Period",
    "Underwriting Defect and Major Servicing Defect Settlement Date": "Defect Settlement Date",
    "Current Non-Interest Bearing UPB": "Current Deferred UPB",
    "Total Expenses": "Expenses",
    "Actual Loss": "Actual Loss Calculation",
    "Cumulative Modification Costs": "Modification Cost",
    "Interest Rate Step Indicator": "Step Modification Flag",
    "Payment Deferral Flag": "Deferred Payment Plan",
    "Borrower Assistance Plan": "Borrower Assistance Status Code",
    "Current Period Modification Costs": "Current Month Modification Cost",
    "Current Interest Bearing UPB": "Interest Bearing UPB",
}

#: Attributes that R47 moved OUT of the origination file into the monthly
#: performance file. Origination silver keeps the columns (consumers select
#: them) but they arrive as typed nulls for R47-sourced vintages — the values
#: now live per-month in the performance panel.
R47_MOVED_OFF_ORIGINATION: tuple[str, ...] = (
    "Servicer Name",
    "Mortgage Insurance Cancellation Indicator",
)


def is_r47_origination(column_names: Iterable[str]) -> bool:
    """Return True if these bronze origination columns are the R47 layout.

    Detected from the column set rather than the filename so a part-migrated
    bronze directory (some quarters rebuilt, some not) still resolves per file.

    Args:
        column_names: Column names of a bronze origination parquet.

    Returns:
        True when the columns are the R47 origination layout.
    """
    return "Loan Identifier" in set(column_names)


def is_r47_performance(column_names: Iterable[str]) -> bool:
    """Return True if these bronze performance columns are the R47 layout.

    Args:
        column_names: Column names of a bronze performance parquet.

    Returns:
        True when the columns are the R47 performance layout.
    """
    return "Period" in set(column_names)


def load_standard_schemas(
    legacy_path: Path,
    r47_path: Path,
) -> dict[str, dict]:
    """Load both standard-dataset layout eras, keyed for the bronze importer.

    Args:
        legacy_path: Pre-R47 layout CSV (still used by the non-standard
            ``historical_data_excl_*`` bundle).
        r47_path: Release 47 layout CSV (the reissued standard dataset).

    Returns:
        Schemas keyed ``origination`` / ``performance`` (pre-R47) and
        ``origination_r47`` / ``performance_r47``.
    """
    schemas = load_all_schemas(legacy_path)
    for key, schema in load_all_schemas(r47_path).items():
        schemas[f"{key}_r47"] = schema
    return schemas


#: Column types of the layout CSVs.
LAYOUT_CSV_SCHEMA: dict[str, type[pl.DataType]] = {
    "File": pl.Utf8,
    "Field Position": pl.Int64,
    "Attribute Name": pl.Utf8,
    "Data Type & Format": pl.Utf8,
    "Max Length": pl.Int64,
}


def load_all_schemas(schema_path: Path) -> dict[str, dict]:
    """Load the schema of every data file described in one layout CSV.

    Args:
        schema_path: Path to the layout CSV (see the module docstring).

    Returns:
        Dict with keys: 'origination', 'performance', 'reperforming'
        Each containing column mappings and type conversions.
    """
    logger.info("Loading schemas from %s", schema_path)

    layout = pl.read_csv(schema_path, schema=LAYOUT_CSV_SCHEMA)
    files = layout["File"].unique(maintain_order=True).to_list()
    logger.info("Available files: %s", files)

    schemas = {}

    for sheet_name in files:
        df_schema = layout.filter(pl.col("File") == sheet_name).drop("File")
        schema_key = map_sheet_to_type(sheet_name)

        if schema_key:
            schemas[schema_key] = parse_schema_dataframe(df_schema)
            logger.info(
                "Loaded schema for %s from sheet '%s' (%s columns)",
                schema_key,
                sheet_name,
                len(schemas[schema_key]["column_names"]),
            )

    return schemas


def map_sheet_to_type(sheet_name: str) -> str | None:
    """Map a layout's ``File`` name (the workbook sheet name) to a data type.

    Args:
        sheet_name: The ``File`` value, e.g. ``Origination Data File``

    Returns:
        'origination', 'performance', 'reperforming', or None
    """
    sheet_lower = sheet_name.lower()

    if "origination" in sheet_lower:
        return "origination"
    elif "performance" in sheet_lower or "monthly" in sheet_lower:
        return "performance"
    elif "rpl" in sheet_lower or "reperform" in sheet_lower:
        return "reperforming"

    return None


def parse_schema_dataframe(df_schema: pl.DataFrame) -> dict:
    """Parse schema dataframe to extract column names and types.

    Expected columns in schema df:
    - FIELD POSITION
    - ATTRIBUTE NAME (column name)
    - DATA TYPE & FORMAT (type specification)
    - MAX LENGTH (field length)

    Args:
        df_schema: DataFrame containing schema information

    Returns:
        Dict with:
        - 'column_names': List[str]
        - 'column_types': Dict[str, pl.DataType]
        - 'date_columns': List[str]
        - 'date_formats': Dict[str, str]

    Raises:
        ValueError: If no column holds the attribute names.
    """
    schema_info: dict[str, Any] = {
        "column_names": [],
        "column_types": {},
        "date_columns": [],
        "date_formats": {},
    }

    # Detect column name, type, and length columns
    name_col, type_col, length_col = detect_schema_columns(df_schema)
    if name_col is None:
        raise ValueError(f"No attribute-name column among {df_schema.columns}")

    # Extract schema information
    for row in df_schema.iter_rows(named=True):
        col_name = row[name_col]

        if col_name is None:
            continue

        col_name = str(col_name).strip()
        schema_info["column_names"].append(col_name)

        # Parse data type if available
        if type_col and row[type_col] is not None:
            col_type_str = str(row[type_col]).strip()

            # Get length if available (useful for date format detection)
            col_length = None
            if length_col and row[length_col] is not None:
                try:
                    col_length = int(row[length_col])
                except (ValueError, TypeError):
                    pass

            polars_type = parse_type_string(col_type_str)
            schema_info["column_types"][col_name] = polars_type

            # Track date columns
            if "date" in col_type_str.lower():
                schema_info["date_columns"].append(col_name)
                date_format = extract_date_format(col_type_str, col_length)
                if date_format:
                    schema_info["date_formats"][col_name] = date_format

    return schema_info


def detect_schema_columns(df_schema: pl.DataFrame) -> tuple[str | None, str | None, str | None]:
    """Detect which columns contain name, type, and length information.

    Args:
        df_schema: DataFrame containing schema

    Returns:
        Tuple of (name_column, type_column, length_column); an element is None
        when the corresponding column could not be detected.
    """
    name_col: str | None = None
    type_col: str | None = None
    length_col: str | None = None

    for col in df_schema.columns:
        col_lower = str(col).lower()

        # Look for attribute/field name column
        if "attribute" in col_lower or "field" in col_lower:
            if "name" in col_lower:
                name_col = col
        elif "name" in col_lower and not name_col:
            name_col = col

        # Look for type/format column
        if ("type" in col_lower or "format" in col_lower) and not type_col:
            type_col = col

        # Look for length column
        if "length" in col_lower and not length_col:
            length_col = col

    # Fallback: assume second column is name (after FIELD POSITION)
    if name_col is None and len(df_schema.columns) > 1:
        name_col = df_schema.columns[1]

    # Fallback: assume third column is type
    if type_col is None and len(df_schema.columns) > 2:
        type_col = df_schema.columns[2]

    # Fallback: assume fourth column is length
    if length_col is None and len(df_schema.columns) > 3:
        length_col = df_schema.columns[3]

    return name_col, type_col, length_col


def parse_type_string(type_str: str) -> type[pl.DataType] | pl.DataType:
    """Convert type string from schema to Polars data type.

    Examples:
    - "Numeric - 12,2" -> Float64
    - "Numeric" -> Int64
    - "Date" -> Utf8 (will convert after parsing format)
    - "Alpha" -> Utf8
    - "Alpha Numeric" -> Utf8

    Args:
        type_str: Type specification from schema

    Returns:
        Polars DataType
    """
    type_lower = type_str.lower()

    # Date types
    if "date" in type_lower:
        return pl.Utf8  # Load as string, convert after parsing format

    # Alpha or Alpha Numeric -> string (check this BEFORE numeric)
    if "alpha" in type_lower:
        return pl.Utf8

    # Numeric types with decimal places (e.g., "Numeric - 12,2")
    if "numeric" in type_lower:
        # If it has a comma in the format (X,Y), it's a decimal/float
        if "," in type_str:
            return pl.Float64
        else:
            # Plain numeric without decimals
            return pl.Int64

    # Default to string
    return pl.Utf8


# ---------------------------------------------------------------------------
# CRT (Reference Pool) disclosure schemas — authored in code from the official
# "Reference Pool Disclosure File Layouts" doc (v4.2, effective July 2026), published
# on Freddie Mac's CRT Disclosure Guides page
# (https://capitalmarkets.freddiemac.com/crt/resources/disclosure-guides/disclosure-guides-overview).
# Unlike the SF LLD schemas above
# (read from the layout CSVs), the CRT layout is authored here from a PDF.
# ---------------------------------------------------------------------------

# The at-issuance/monthly Loan Level file layout: (attribute name, dtype) in
# field-position order. Files carry a *prefix* of this list — the layout grew
# over time (88 fields through 2021-11, 89 from 2021-12 [+Distressed Principal
# Balance Flag], 90 from 2024-09 [+Temporary Subsidy Buydown Plan Type], 93
# from 2026-07 [+VantageScore 4.0, Actual Loss, Cumulative Modification
# Costs]) — so bronze names columns by position with the first N entries.
# Date-typed fields are all CCYYMM. Two v4.2 renames to be aware of when
# reading older layout docs: "Classic FICO" was "Credit Score" until Apr-2026,
# and "Special Eligibility Program" was "Program Indicator" until Mar-2024.
# "Filler (Field 61)" carried Forecast Standard Deviation before Oct-2019.
CRT_LLD_LAYOUT: list[tuple[str, type[pl.DataType]]] = [
    ("Period", pl.Date),
    ("Reference Pool Number", pl.Utf8),
    ("Loan Identifier", pl.Utf8),
    ("Amortization Type", pl.Utf8),
    ("Seller Name", pl.Utf8),
    ("Property State", pl.Utf8),
    ("Postal Code (3-Digit)", pl.Int64),
    ("Metropolitan Statistical Area (MSA) or Metropolitan Division", pl.Int64),
    ("First Payment Date", pl.Date),
    ("Maturity Date", pl.Date),
    ("Original Loan Term", pl.Int64),
    ("Original Interest Rate", pl.Float64),
    ("Original UPB", pl.Float64),
    ("UPB at Issuance", pl.Float64),
    ("Loan Purpose", pl.Utf8),
    ("Channel", pl.Utf8),
    ("Property Type", pl.Utf8),
    ("Number of Units", pl.Int64),
    ("Occupancy Status", pl.Utf8),
    ("Number of Borrowers", pl.Int64),
    ("First Time Homebuyer Indicator", pl.Utf8),
    ("Prepayment Penalty Indicator", pl.Utf8),
    ("Classic FICO", pl.Int64),
    ("Original Loan-To-Value (LTV)", pl.Int64),
    ("Original Combined Loan-To-Value (CLTV)", pl.Int64),
    ("Original Debt-To-Income (DTI)", pl.Int64),
    ("Mortgage Insurance Percent (MI%)", pl.Int64),
    ("Updated Credit Score at Issuance", pl.Int64),
    ("Special Eligibility Program", pl.Utf8),
    ("Mortgage Insurance Type", pl.Int64),
    ("Filler (Field 31)", pl.Utf8),
    ("Disaster Grace Period", pl.Int64),
    ("Servicer Name", pl.Utf8),
    ("Loan Age", pl.Int64),
    ("Remaining Months to Legal Maturity", pl.Int64),
    ("Adjusted Remaining Months to Maturity (RMM)", pl.Int64),
    ("Current Loan Delinquency Status", pl.Utf8),
    ("Payment History", pl.Utf8),
    ("Current Interest Rate", pl.Float64),
    ("Current Actual UPB", pl.Float64),
    ("Current Interest Bearing UPB", pl.Float64),
    ("UPB at Time of Removal from the Reference Pool", pl.Float64),
    ("Zero Balance Code", pl.Int64),
    ("Zero Balance Effective Date", pl.Date),
    ("Underwriting Defect and Major Servicing Defect Settlement Date", pl.Date),
    ("Modification Flag", pl.Utf8),
    ("Delinquency Due to Disaster", pl.Utf8),
    ("Due Date of Last Paid Installment (DDLPI)", pl.Date),
    ("Bankruptcy Flag", pl.Utf8),
    ("Date Referred to Foreclosure", pl.Date),
    ("Net Sales Proceeds", pl.Float64),
    ("MI Credit", pl.Float64),
    ("Taxes and Insurance", pl.Float64),
    ("Legal Costs", pl.Float64),
    ("Maintenance and Preservation Costs", pl.Float64),
    ("Bankruptcy Cramdown Costs", pl.Float64),
    ("Miscellaneous Expenses", pl.Float64),
    ("Miscellaneous Credits", pl.Float64),
    ("Mortgage Insurance Cancellation Indicator", pl.Utf8),
    ("Estimated Loan-To-Value (ELTV) - Monthly", pl.Int64),
    ("Filler (Field 61)", pl.Utf8),
    ("Updated Credit Score #1 - Quarterly", pl.Int64),
    ("Updated Credit Score #2 - Quarterly", pl.Int64),
    ("Number of Modifications", pl.Int64),
    ("Modification Program", pl.Utf8),
    ("Modification Type", pl.Utf8),
    ("Modification First Payment Date", pl.Date),
    ("Modification Debt-To-Income (DTI)", pl.Int64),
    ("Total Capitalized Amount", pl.Float64),
    ("Interest Rate Step Indicator", pl.Utf8),
    ("First Step Rate Adjustment Date", pl.Date),
    ("First Step Rate", pl.Float64),
    ("Second Step Rate Adjustment Date", pl.Date),
    ("Second Step Rate", pl.Float64),
    ("Third Step Rate Adjustment Date", pl.Date),
    ("Third Step Rate", pl.Float64),
    ("Fourth Step Rate Adjustment Date", pl.Date),
    ("Fourth Step Rate", pl.Float64),
    ("Fifth Step Rate Adjustment Date", pl.Date),
    ("Fifth Step Rate", pl.Float64),
    ("Delinquent Accrued Interest", pl.Float64),
    ("Current Period Modification Costs", pl.Float64),
    ("Updated Credit Score #3 - Quarterly", pl.Int64),
    ("Property Valuation Method", pl.Int64),
    ("Group Number", pl.Int64),
    ("Enhanced Relief Refi Indicator", pl.Int64),
    ("Borrower Assistance Plan", pl.Utf8),
    ("Payment Deferral Flag", pl.Utf8),
    ("Distressed Principal Balance Flag", pl.Utf8),
    ("Temporary Subsidy Buydown Plan Type", pl.Int64),
    ("VantageScore 4.0", pl.Int64),
    ("Actual Loss", pl.Float64),
    ("Cumulative Modification Costs", pl.Float64),
]

# Aggregated Postal Code Disclosure File (at issuance only, Apr-2015+ pools).
CRT_AGG_ZIPCODE_LAYOUT: list[tuple[str, type[pl.DataType]]] = [
    ("Reference Pool Number", pl.Utf8),
    ("Postal Code Designation", pl.Utf8),
    ("Aggregate UPB at Issuance", pl.Float64),
]


def _layout_to_schema(layout: list[tuple[str, type[pl.DataType]]]) -> dict:
    """Convert a (name, dtype) layout list to the module's schema-dict shape."""
    schema: dict = {"column_names": [], "column_types": {}, "date_columns": [], "date_formats": {}}
    for name, dtype in layout:
        schema["column_names"].append(name)
        schema["column_types"][name] = pl.Utf8 if dtype is pl.Date else dtype
        if dtype is pl.Date:
            schema["date_columns"].append(name)
            schema["date_formats"][name] = "%Y%m"
    return schema


def crt_schemas() -> dict[str, dict]:
    """Return the CRT disclosure schemas, keyed like the layout-CSV ones.

    Keys: ``crt_lld`` (the at-issuance Loan Level Reference Pool file),
    ``crt_agg_zipcode`` (the aggregated postal-code supplement), and
    ``crt_pre_harp`` (the HRP original-loan supplement). The layout doc says
    pre-HARP files carry lld positions 2-30, but the record-'20' loan rows
    empirically start at position 3 (Loan Identifier) — the Reference Pool
    Number sits in the '10' deal record instead. The headered two-column
    supplements (``life_to_date_pay_hist``, ``temp_buydown``) carry their own
    headers and need no schema.

    Returns:
        The CRT schemas, keyed by disclosure name.
    """
    lld = _layout_to_schema(CRT_LLD_LAYOUT)
    pre_harp = _layout_to_schema(CRT_LLD_LAYOUT[2:30])
    return {
        "crt_lld": lld,
        "crt_agg_zipcode": _layout_to_schema(CRT_AGG_ZIPCODE_LAYOUT),
        "crt_pre_harp": pre_harp,
    }


def extract_date_format(type_str: str, length: int | None = None) -> str | None:
    """Extract date format from type string and length.

    Examples:
    - "Date" with length 6 -> "%Y%m" (YYYYMM)
    - "Date" with length 8 -> "%Y%m%d" (YYYYMMDD)
    - "Date YYYYMM" -> "%Y%m"
    - "Date YYYYMMDD" -> "%Y%m%d"

    Args:
        type_str: Type specification containing date format
        length: Optional field length to infer format

    Returns:
        Python strftime format string, or None if not detected
    """
    type_str = type_str.upper()

    # Check for explicit format in the string
    if "YYYYMMDD" in type_str:
        return "%Y%m%d"
    elif "YYYYMM" in type_str:
        return "%Y%m"
    elif "YYYY-MM-DD" in type_str:
        return "%Y-%m-%d"
    elif "MM/DD/YYYY" in type_str:
        return "%m/%d/%Y"

    # Infer from length if "Date" with no explicit format
    if "DATE" in type_str and length:
        if length == 6:
            return "%Y%m"  # YYYYMM
        elif length == 8:
            return "%Y%m%d"  # YYYYMMDD

    return None
