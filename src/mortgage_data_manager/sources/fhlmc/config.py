"""FHLMC (Freddie Mac) data processing configuration.

This module provides FHLMCConfig, the main configuration class for FHLMC
data processing, following the project's configuration inheritance pattern.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

import polars as pl
from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.types import (
    BOOL,
    TEXT,
    ZIP3,
    ColumnRole,
    Declared,
    spec_from_columns,
)

if TYPE_CHECKING:
    from polars._typing import ParquetCompression


class FHLMCConfig(MortgageDataConfig):
    """FHLMC-specific configuration.

    Inherits base configuration from MortgageDataConfig and adds
    FHLMC-specific paths and constants.

    Environment Variables:
        FHLMC_DATA_DIR: Root FHLMC data directory
        FHLMC_RAW_DIR: Raw data directory
        FHLMC_BRONZE_DIR: Bronze layer directory (staged parquet)
        FHLMC_SILVER_DIR: Silver layer directory (transformed data)
        FHLMC_GOLD_DIR: Gold layer directory (aggregations)

    Example:
        >>> from mortgage_data_manager.sources.fhlmc.config import FHLMCConfig
        >>> print(FHLMCConfig.FHLMC_DATA_DIR)
        PosixPath('/path/to/data/fhlmc')
        >>> print(FHLMCConfig.FHLMC_BRONZE_DIR)
        PosixPath('/path/to/data/fhlmc/bronze')
    """

    # FHLMC data directories
    FHLMC_DATA_DIR: Path = Path(
        config("FHLMC_DATA_DIR", default=str(MortgageDataConfig.get_subpackage_data_dir("fhlmc")))
    )
    FHLMC_RAW_DIR: Path = Path(config("FHLMC_RAW_DIR", default=str(FHLMC_DATA_DIR / "raw")))
    FHLMC_BRONZE_DIR: Path = Path(
        config("FHLMC_BRONZE_DIR", default=str(FHLMC_DATA_DIR / "bronze"))
    )
    FHLMC_SILVER_DIR: Path = Path(
        config("FHLMC_SILVER_DIR", default=str(FHLMC_DATA_DIR / "silver"))
    )
    FHLMC_GOLD_DIR: Path = Path(config("FHLMC_GOLD_DIR", default=str(FHLMC_DATA_DIR / "gold")))

    # Schema directory
    FHLMC_SCHEMAS_DIR: Path = Path(
        config("FHLMC_SCHEMAS_DIR", default=str(MortgageDataConfig.SCHEMAS_DIR / "fhlmc"))
    )

    # Bronze subdirectories (FHLMC-specific)
    FHLMC_BRONZE_ORIGINATION: Path = FHLMC_BRONZE_DIR / "origination"
    FHLMC_BRONZE_PERFORMANCE: Path = FHLMC_BRONZE_DIR / "performance"
    FHLMC_BRONZE_REPERFORMING: Path = FHLMC_BRONZE_DIR / "reperforming"

    # Schema file locations. The pre-R47 layout still describes the non-standard
    # (``historical_data_excl_*``) bundle, which Freddie did not reissue; the R47
    # layout describes the whole reissued standard dataset (1999Q1-2026Q1).
    FHLMC_SCHEMA_FILE: Path = FHLMC_SCHEMAS_DIR / "sf_file_layout.csv"
    FHLMC_SCHEMA_FILE_R47: Path = FHLMC_SCHEMAS_DIR / "sf_file_layout_july_2026.csv"

    # FHLMC datasets (3 types: simpler than FHFA's 7)
    VALID_DATASETS: list[str] = ["origination", "performance", "reperforming"]

    # Year ranges for FHLMC historical data
    MIN_YEAR: int = 1999
    MAX_YEAR: int = 2026

    # Parquet settings
    PARQUET_COMPRESSION: ParquetCompression = "snappy"
    PARQUET_ROW_GROUP_SIZE = 100000
    PARQUET_STATISTICS = True

    @classmethod
    def get_prefix_dir(
        cls,
        stage: Literal["raw", "bronze", "silver", "gold"],
        prefix: str,
    ) -> Path:
        """Get the prefix sub-directory under an FHLMC medallion stage.

        FHLMC organizes each medallion stage into per-prefix sub-directories
        (e.g. ``origination``, ``performance``, ``reperforming``). This helper
        resolves the prefix axis; for the bare stage directory use the per-dir
        attributes (``FHLMC_SILVER_DIR``, etc.) or the inherited
        `MortgageDataConfig.get_medallion_dir`.

        Args:
            stage: Medallion stage ('raw', 'bronze', 'silver', or 'gold')
            prefix: Data prefix naming the sub-directory

        Returns:
            Path to the prefix sub-directory under the stage

        Example:
            >>> FHLMCConfig.get_prefix_dir('silver', 'origination')
            PosixPath('/path/to/data/fhlmc/silver/origination')

        Note:
            ``get_medallion_dir`` is inherited from
            `MortgageDataConfig` unmodified (signature
            ``get_medallion_dir(subpackage, stage)``).
        """
        stage_dirs = {
            "raw": cls.FHLMC_RAW_DIR,
            "bronze": cls.FHLMC_BRONZE_DIR,
            "silver": cls.FHLMC_SILVER_DIR,
            "gold": cls.FHLMC_GOLD_DIR,
        }
        return stage_dirs[stage] / prefix

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary directories for FHLMC data processing.

        Example:
            >>> FHLMCConfig.ensure_directories()
            >>> # Creates: data/fhlmc/raw, data/fhlmc/bronze, etc.
        """
        # Create medallion directories
        for d in (cls.FHLMC_RAW_DIR, cls.FHLMC_BRONZE_DIR, cls.FHLMC_SILVER_DIR):
            d.mkdir(parents=True, exist_ok=True)

        # Create bronze subdirectories
        cls.FHLMC_BRONZE_ORIGINATION.mkdir(parents=True, exist_ok=True)
        cls.FHLMC_BRONZE_PERFORMANCE.mkdir(parents=True, exist_ok=True)
        cls.FHLMC_BRONZE_REPERFORMING.mkdir(parents=True, exist_ok=True)


# Re-exports for ergonomic imports
VALID_DATASETS = FHLMCConfig.VALID_DATASETS
MIN_YEAR = FHLMCConfig.MIN_YEAR
MAX_YEAR = FHLMCConfig.MAX_YEAR


# ----------------------------------------------------------------------------- #
# Silver schema specs (close-to-final dtypes; see core/types.py). Unlike FNMA's single
# shared loan-performance schema, Freddie splits the SF Loan-Level Dataset into TWO
# schemas, so FHLMC declares two specs. Both were derived data-driven from the FULL
# silver domain (all 9 origination vintages + a spread of performance quarters), not a
# newest-partition sample. Column names are already clean (no trailing/double spaces),
# so they are used verbatim with no name stripping.
# ----------------------------------------------------------------------------- #
# Closed Freddie code sets. NOTE: Loan Purpose levels (C/N/P) genuinely differ
# from Fannie's (C/P/R) — C=cash-out refi, N=no-cash-out refi, P=purchase — so
# this is FHLMC-native, not the FNMA enum. Channel adds T (third-party origination,
# unspecified broker or correspondent; 8.7M standard-dataset loans, mostly 1999-2009), which Fannie
# never publishes. Occupancy/Property Type happen to share Fannie's levels; declared
# locally to keep the modules decoupled (Enum dtype equality is by level set).
_FHLMC_CHANNEL = pl.Enum(["B", "C", "R", "T"])
_FHLMC_OCCUPANCY_STATUS = pl.Enum(["I", "P", "S"])
_FHLMC_PROPERTY_TYPE = pl.Enum(["CO", "CP", "MH", "PU", "SF"])
_FHLMC_LOAN_PURPOSE = pl.Enum(["C", "N", "P"])
_FHLMC_AMORTIZATION_TYPE = pl.Enum(["ARM", "FRM"])
_FHLMC_PROGRAM_INDICATOR = pl.Enum(["F", "H", "R"])
# '7' = Not Applicable (no MI on loan) is a REAL value distinct from '9' = Not
# Disclosed (a sentinel, null-mapped below) — so this is an Enum, not a Boolean.
_FHLMC_MI_CANCELLATION = pl.Enum(["7", "N", "Y"])
# Performance-side closed sets.
_FHLMC_MODIFICATION_FLAG = pl.Enum(["P", "Y"])
_FHLMC_DEFERRED_PAYMENT_PLAN = pl.Enum(["C", "P", "Y"])
_FHLMC_BORROWER_ASSISTANCE = pl.Enum(["F", "R", "T"])

# Missing-value sentinels (per Freddie's SF LLD user guide), null-mapped before
# any cast. Numeric cols are Int64 in bronze (int sentinels); coded alpha cols are
# String (string sentinels). MI % 0 = "no MI" is a REAL value (only 999 is the
# sentinel); MI Cancellation '7' is real (only '9' is the sentinel).
_FHLMC_ORIG_SENTINELS = {
    "Credit Score": (9999,),
    # New at R47. Same 300-850 domain and 9999 = Not Available as Classic FICO.
    "VantageScore 4.0": (9999,),
    "Mortgage Insurance Percentage (MI %)": (999,),
    "Original Combined Loan-to-Value (CLTV)": (999,),
    "Original Debt-to-Income (DTI) Ratio": (999,),
    "Original Loan-to-Value (LTV)": (999,),
    "Number of Units": (99,),
    "Number of Borrowers": (99,),
    "Property Valuation Method": (9,),
    "Channel": ("9",),
    "First Time Homebuyer Flag": ("9",),
    "Occupancy Status": ("9",),
    "Loan Purpose": ("9",),
    "Property Type": ("99",),
    "Program Indicator": ("9",),
    "Mortgage Insurance Cancellation Indicator": ("9",),
}

#: One row per Loan Sequence Number (origination grain), hive-partitioned by
#: vintage_year. ``First Payment Date`` / ``Maturity Date`` arrive already typed
#: as pl.Date from bronze, so they are ``Declared`` (recorded in the target,
#: never re-parsed). Postal Code = ZIP3x100 is recovered to ZIP3 in import_silver
#: (the //100 is Freddie-specific encoding) and zero-padded to 3 chars here.
_ORIGINATION_COLS: list[tuple[str, ColumnRole]] = [
    ("Credit Score", pl.Int16),
    ("First Payment Date", Declared(pl.Date)),
    ("First Time Homebuyer Flag", BOOL),
    ("Maturity Date", Declared(pl.Date)),
    ("Metropolitan Statistical Area (MSA) Or Metropolitan Division", TEXT),
    ("Mortgage Insurance Percentage (MI %)", pl.Int8),
    ("Number of Units", pl.Int8),
    ("Occupancy Status", _FHLMC_OCCUPANCY_STATUS),
    ("Original Combined Loan-to-Value (CLTV)", pl.Int16),
    ("Original Debt-to-Income (DTI) Ratio", pl.Int8),
    ("Original UPB", pl.Int64),
    ("Original Loan-to-Value (LTV)", pl.Int16),
    ("Original Interest Rate", pl.Float32),
    ("Channel", _FHLMC_CHANNEL),
    ("Prepayment Penalty Mortgage (PPM) Flag", BOOL),
    ("Amortization Type (Formerly Product Type)", _FHLMC_AMORTIZATION_TYPE),
    ("Property State", TEXT),
    ("Property Type", _FHLMC_PROPERTY_TYPE),
    ("Postal Code", ZIP3),
    ("Loan Sequence Number", TEXT),
    ("Loan Purpose", _FHLMC_LOAN_PURPOSE),
    ("Original Loan Term", pl.Int16),
    ("Number of Borrowers", pl.Int8),
    ("Seller Name", Declared(pl.String)),
    ("Servicer Name", Declared(pl.String)),
    ("Super Conforming Flag", BOOL),
    ("Pre-HARP Loan Sequence Number", TEXT),
    ("Program Indicator", _FHLMC_PROGRAM_INDICATOR),
    ("HARP Indicator", BOOL),
    ("Property Valuation Method", pl.Int8),
    ("Interest Only (I/O) Indicator", BOOL),
    ("Mortgage Insurance Cancellation Indicator", _FHLMC_MI_CANCELLATION),
    ("VantageScore 4.0", pl.Int16),
    ("vintage_year", Declared(pl.Int32)),
    ("vintage_quarter", Declared(pl.Int8)),
]

FHLMC_ORIGINATION_SILVER = spec_from_columns(
    _ORIGINATION_COLS,
    sentinels=_FHLMC_ORIG_SENTINELS,
)

#: The loan x month performance panel, one parquet per origination quarter. All
#: four date columns (Monthly Reporting Period, Defect Settlement Date, Zero
#: Balance Effective Date, DDLPI) arrive already pl.Date from bronze. ``Current
#: Loan Delinquency Status`` stays String (carries the non-numeric 'RA' = REO
#: Acquisition code). ELTV gets the 999 = "not available" sentinel null-mapped
#: (the old passthrough builder did no sentinel cleanup); Net Sales Proceeds is
#: stored as numeric strings and cast to Float64 (verified: no non-numeric codes).
_PERFORMANCE_SENTINELS = {
    "Estimated Loan-to-Value (ELTV)": (999,),
    # Moved here from origination at R47; '9' = Not Disclosed stays the
    # sentinel, while '7' = Not Applicable remains a real level.
    "Mortgage Insurance Cancellation Indicator": ("9",),
}

_PERFORMANCE_COLS: list[tuple[str, ColumnRole]] = [
    ("Loan Sequence Number", TEXT),
    ("Monthly Reporting Period", Declared(pl.Date)),
    ("Current Actual UPB", Declared(pl.Float64)),
    ("Current Loan Delinquency Status", Declared(pl.String)),
    ("Loan Age", pl.Int16),
    ("Remaining Months to Legal Maturity", pl.Int16),
    ("Defect Settlement Date", Declared(pl.Date)),
    ("Modification Flag", _FHLMC_MODIFICATION_FLAG),
    ("Zero Balance Code", pl.Int8),
    ("Zero Balance Effective Date", Declared(pl.Date)),
    ("Current Interest Rate", pl.Float32),
    ("Current Deferred UPB", pl.Float64),
    ("Due Date of Last Paid Installment (DDLPI)", Declared(pl.Date)),
    ("MI Recoveries", Declared(pl.Float64)),
    ("Net Sales Proceeds", pl.Float64),
    ("Non MI Recoveries", Declared(pl.Float64)),
    ("Expenses", Declared(pl.Float64)),
    ("Legal Costs", Declared(pl.Float64)),
    ("Maintenance and Preservation Costs", Declared(pl.Float64)),
    ("Taxes and Insurance", Declared(pl.Float64)),
    ("Miscellaneous Expenses", Declared(pl.Float64)),
    ("Actual Loss Calculation", Declared(pl.Float64)),
    ("Modification Cost", Declared(pl.Float64)),
    ("Step Modification Flag", BOOL),
    ("Deferred Payment Plan", _FHLMC_DEFERRED_PAYMENT_PLAN),
    ("Estimated Loan-to-Value (ELTV)", pl.Int16),
    ("Zero Balance Removal UPB", Declared(pl.Float64)),
    ("Delinquent Accrued Interest", Declared(pl.Float64)),
    ("Delinquency Due to Disaster", BOOL),
    ("Borrower Assistance Status Code", _FHLMC_BORROWER_ASSISTANCE),
    ("Current Month Modification Cost", Declared(pl.Float64)),
    ("Interest Bearing UPB", Declared(pl.Float64)),
    ("Mortgage Insurance Cancellation Indicator", _FHLMC_MI_CANCELLATION),
    ("Servicer Name", Declared(pl.String)),
    ("Bankruptcy Cramdown Costs", Declared(pl.Float64)),
]

FHLMC_PERFORMANCE_SILVER = spec_from_columns(
    _PERFORMANCE_COLS,
    sentinels=_PERFORMANCE_SENTINELS,
)
