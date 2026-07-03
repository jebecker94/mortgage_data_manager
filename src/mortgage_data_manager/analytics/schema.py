"""Harmonized loan-month schema and column constants for analytics.

The analytics prepayment work operates on a single source-neutral loan-month
grain harmonized from the GSE single-family datasets. Every row is one
loan-month. The column-name constants here are imported by
:mod:`analytics.panel`, :mod:`analytics.features`, and
:mod:`analytics.estimated` so the panel/feature/model contracts never drift
apart.

The harmonization maps the main package's FNMA **bronze** (SF Loan Performance,
one merged orig+perf row per loan-month) and FHLMC **bronze** (separate
origination + performance, joined on Loan Sequence Number) into this schema.
The loan-month grain only exists at bronze in this package — FNMA silver is
issuance-only and FHLMC silver is origination-only — so the panel adapter reads
bronze directly (it still reuses the package's download/bronze work; it does
not re-ingest).
"""

from __future__ import annotations

from datetime import date

import polars as pl

# ---------------------------------------------------------------------------
# Harmonized loan-month column names
# ---------------------------------------------------------------------------
LOAN_ID = "loan_id"
AGENCY = "agency"
PERIOD = "period"
ORIG_DATE = "orig_date"
ORIG_RATE = "orig_rate"
ORIG_UPB = "orig_upb"
ORIG_TERM = "orig_term"
ORIG_LTV = "orig_ltv"
ORIG_CLTV = "orig_cltv"
FICO = "fico"
DTI = "dti"
LOAN_PURPOSE = "loan_purpose"
PROPERTY_TYPE = "property_type"
OCCUPANCY = "occupancy"
CHANNEL = "channel"
NUM_BORROWERS = "num_borrowers"
FIRST_TIME_BUYER = "first_time_buyer"
NUM_UNITS = "num_units"
STATE = "state"
FIRST_PAYMENT_DATE = "first_payment_date"
CURRENT_UPB = "current_upb"
CURRENT_RATE = "current_rate"
LOAN_AGE = "loan_age"
DLQ_STATUS = "dlq_status"
ZERO_BAL_CODE = "zero_bal_code"
MOD_FLAG = "mod_flag"
ELTV = "eltv"

#: Harmonized loan-month schema (column name -> Polars dtype).
SILVER_SCHEMA: dict[str, pl.DataType] = {
    LOAN_ID: pl.String,
    AGENCY: pl.String,
    PERIOD: pl.Date,
    ORIG_DATE: pl.Date,
    ORIG_RATE: pl.Float64,
    ORIG_UPB: pl.Float64,
    ORIG_TERM: pl.Int32,
    ORIG_LTV: pl.Int32,
    ORIG_CLTV: pl.Int32,
    FICO: pl.Int32,
    DTI: pl.Int32,
    LOAN_PURPOSE: pl.String,
    PROPERTY_TYPE: pl.String,
    OCCUPANCY: pl.String,
    CHANNEL: pl.String,
    NUM_BORROWERS: pl.Int32,
    FIRST_TIME_BUYER: pl.Boolean,
    NUM_UNITS: pl.Int32,
    STATE: pl.String,
    FIRST_PAYMENT_DATE: pl.Date,
    CURRENT_UPB: pl.Float64,
    CURRENT_RATE: pl.Float64,
    LOAN_AGE: pl.Int32,
    DLQ_STATUS: pl.Int32,
    ZERO_BAL_CODE: pl.Int32,
    MOD_FLAG: pl.Boolean,
    ELTV: pl.Int32,
}

SILVER_COLUMNS = list(SILVER_SCHEMA.keys())

# Agency labels.
FNMA = "FNMA"
FHLMC = "FHLMC"

# ---------------------------------------------------------------------------
# Panel-level columns (added by analytics.panel)
# ---------------------------------------------------------------------------
Y = "y"
ORIG_QUARTER = "orig_quarter"
MORTGAGE_RATE = "mortgage_rate"
RATE_CHANGE_3M = "rate_change_3m"
YIELD_SPREAD = "yield_spread"
UNEMP_RATE = "unemp_rate"
HPI = "hpi"
HPA_4Q = "hpa_4q"
SPLIT = "split"

# ---------------------------------------------------------------------------
# Feature columns (added by analytics.features)
# ---------------------------------------------------------------------------
INCENTIVE = "incentive"
SATO = "sato"
BURNOUT_CUM = "burnout_cum"
MONTHS_ITM = "months_itm"
BURNOUT_X_INCENTIVE = "burnout_x_incentive"
LOG_ORIG_UPB = "log_orig_upb"
DYNAMIC_LTV = "dynamic_ltv"
SCURVE = "scurve_incentive"
COVID = "covid_indicator"
MONTH_OF_YEAR = "month_of_year"
VINTAGE_YEAR = "vintage_year"

# ---------------------------------------------------------------------------
# FNMA bronze (SF Loan Performance) -> harmonized columns
# ---------------------------------------------------------------------------
FNMA_COL_MAP: dict[str, str] = {
    "Loan Identifier": LOAN_ID,
    "Monthly Reporting Period": PERIOD,
    "Origination Date": ORIG_DATE,
    "Original Interest Rate": ORIG_RATE,
    "Original UPB": ORIG_UPB,
    "Original Loan Term": ORIG_TERM,
    "Original Loan to Value Ratio (LTV)": ORIG_LTV,
    "Original Combined Loan to Value Ratio (CLTV)": ORIG_CLTV,
    "Borrower Credit Score at Origination": FICO,
    "Debt-To-Income (DTI)": DTI,
    "Loan Purpose": LOAN_PURPOSE,
    "Property Type": PROPERTY_TYPE,
    "Occupancy Status": OCCUPANCY,
    "Channel": CHANNEL,
    "Number of Borrowers": NUM_BORROWERS,
    "First Time Home Buyer Indicator": FIRST_TIME_BUYER,
    "Number of Units": NUM_UNITS,
    "Property State": STATE,
    "First Payment Date": FIRST_PAYMENT_DATE,
    "Current Actual UPB": CURRENT_UPB,
    "Current Interest Rate": CURRENT_RATE,
    "Loan Age": LOAN_AGE,
    "Current Loan Delinquency Status": DLQ_STATUS,
    "Zero Balance Code": ZERO_BAL_CODE,
    "Modification Flag": MOD_FLAG,
}

# FHLMC origination bronze -> harmonized columns.
FHLMC_ORIG_COL_MAP: dict[str, str] = {
    "Loan Sequence Number": LOAN_ID,
    "Credit Score": FICO,
    "First Payment Date": FIRST_PAYMENT_DATE,
    "First Time Homebuyer Flag": FIRST_TIME_BUYER,
    "Original Combined Loan-to-Value (CLTV)": ORIG_CLTV,
    "Original Debt-to-Income (DTI) Ratio": DTI,
    "Original UPB": ORIG_UPB,
    "Original Loan-to-Value (LTV)": ORIG_LTV,
    "Original Interest Rate": ORIG_RATE,
    "Channel": CHANNEL,
    "Property State": STATE,
    "Property Type": PROPERTY_TYPE,
    "Loan Purpose": LOAN_PURPOSE,
    "Original Loan Term": ORIG_TERM,
    "Number of Borrowers": NUM_BORROWERS,
    "Number of Units": NUM_UNITS,
    "Occupancy Status": OCCUPANCY,
}

# FHLMC performance bronze -> harmonized columns.
FHLMC_PERF_COL_MAP: dict[str, str] = {
    "Loan Sequence Number": LOAN_ID,
    "Monthly Reporting Period": PERIOD,
    "Current Actual UPB": CURRENT_UPB,
    "Current Loan Delinquency Status": DLQ_STATUS,
    "Loan Age": LOAN_AGE,
    "Modification Flag": MOD_FLAG,
    "Zero Balance Code": ZERO_BAL_CODE,
    "Current Interest Rate": CURRENT_RATE,
    "Estimated Loan-to-Value (ELTV)": ELTV,
}

#: FHLMC sentinel values mapped to null during harmonization.
FHLMC_SENTINELS: dict[str, list[int]] = {
    FICO: [9999],
    DTI: [999],
    ORIG_CLTV: [999],
    ORIG_LTV: [999],
}

#: Loan-purpose harmonization: FHLMC 'N' (no-cash-out refi) -> 'R' (rate-term).
LOAN_PURPOSE_MAP: dict[str, str] = {
    "P": "P",  # Purchase
    "C": "C",  # Cash-out refinance
    "R": "R",  # Rate-term refinance (FNMA)
    "N": "R",  # No-cash-out refinance (FHLMC) -> rate-term refi
}


def parse_mmyyyy(s: str | None) -> date | None:
    """Parse an FNMA ``MMYYYY`` date string to the first of that month.

    Args:
        s: A 6-character ``MMYYYY`` string, or None.

    Returns:
        The first-of-month date, or None if the string is missing/malformed.
    """
    if s is None or len(s) < 6:
        return None
    try:
        return date(int(s[2:]), int(s[:2]), 1)
    except (ValueError, IndexError):
        return None


def validate_silver(df: pl.DataFrame) -> None:
    """Check that a DataFrame conforms to the harmonized loan-month schema.

    Args:
        df: The harmonized DataFrame to validate.

    Raises:
        ValueError: If schema columns are missing.
        TypeError: If a column has an unexpected dtype.
    """
    missing = set(SILVER_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"Harmonized schema missing columns: {missing}")
    for col, expected in SILVER_SCHEMA.items():
        actual = df.schema[col]
        if actual != expected:
            raise TypeError(f"Column '{col}': expected {expected}, got {actual}")
