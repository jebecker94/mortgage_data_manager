"""Harmonized loan-month schema and column constants for analytics.

The analytics prepayment and credit work operates on a single source-neutral
loan-month grain. Every row is one loan-month. The column-name constants here
are imported by `analytics.panel`, `analytics.features`, and
`analytics.estimated` so the panel/feature/model contracts never drift apart.

The panel is a projection of the combined loan-level masters: static attributes
from ``combined/loan_issuance`` and the monthly history from
``combined/loan_performance``, restricted to the GSE single-family disclosure
universe. The maps at the bottom name the combined column behind each panel
column; the harmonization itself (sentinels, code spaces, the representative
credit score) is done once, in `combined`.
"""

from __future__ import annotations

import polars as pl

from mortgage_data_manager.combined.loan_performance_schema import ZBC_CREDIT_EVENT_CODES

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
SILVER_SCHEMA: dict[str, type[pl.DataType] | pl.DataType] = {
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

#: Zero-balance codes that are credit/default-driven terminal events (third-party
#: sale, short sale / charge-off, REO disposition), as integers — the harmonized
#: ``zero_bal_code`` column is Int32. Derived from the tri-agency taxonomy in
#: `combined.loan_performance_schema` so the two definitions cannot drift.
CREDIT_EVENT_ZB_CODES: frozenset[int] = frozenset(int(c) for c in ZBC_CREDIT_EVENT_CODES)

# ---------------------------------------------------------------------------
# Panel-level columns (added by analytics.panel)
# ---------------------------------------------------------------------------
Y = "y"
Y_DEFAULT = "y_default"
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
# Combined masters -> harmonized columns
# ---------------------------------------------------------------------------
#: ``combined/loan_issuance`` source datasets the panel draws on: the GSE
#: single-family disclosures (the UMBS-only and HARP universes are excluded).
PANEL_SOURCE_DATASETS: dict[str, str] = {FNMA: "fnma_sf_perf", FHLMC: "fhlmc_sf_orig"}

#: ``combined/loan_issuance`` column -> harmonized static column.
ISSUANCE_COL_MAP: dict[str, str] = {
    "loan_key": LOAN_ID,
    "agency": AGENCY,
    "origination_date": ORIG_DATE,
    "note_rate": ORIG_RATE,
    "original_upb": ORIG_UPB,
    "loan_term": ORIG_TERM,
    "ltv": ORIG_LTV,
    "cltv": ORIG_CLTV,
    "representative_credit_score": FICO,
    "dti": DTI,
    "loan_purpose": LOAN_PURPOSE,
    "property_type": PROPERTY_TYPE,
    "occupancy": OCCUPANCY,
    "channel": CHANNEL,
    "number_of_borrowers": NUM_BORROWERS,
    "first_time_homebuyer": FIRST_TIME_BUYER,
    "number_of_units": NUM_UNITS,
    "property_state": STATE,
    "first_payment_date": FIRST_PAYMENT_DATE,
}

#: ``combined/loan_performance`` column -> harmonized monthly column. ``period``,
#: ``zero_bal_code`` and ``mod_flag`` are derived in the panel builder
#: (``reporting_period``, ``event_raw_code`` and ``modification_flag``).
PERFORMANCE_COL_MAP: dict[str, str] = {
    "loan_key": LOAN_ID,
    "agency": AGENCY,
    "current_upb": CURRENT_UPB,
    "current_interest_rate": CURRENT_RATE,
    "loan_age": LOAN_AGE,
    "delinquency_months": DLQ_STATUS,
    "estimated_ltv": ELTV,
}
