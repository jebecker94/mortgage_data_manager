"""FNMA Data Manager - Configuration.

Configuration for FNMA (Fannie Mae) data processing pipeline.

This module provides:
    - FNMAConfig: Main configuration class inheriting from MortgageDataConfig

The FNMAConfig class follows the project's configuration inheritance pattern,
supporting environment variable overrides and medallion architecture paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import polars as pl
from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.types import SilverSpec


class FNMAConfig(MortgageDataConfig):
    """FNMA-specific configuration.

    Inherits base configuration from MortgageDataConfig and adds
    FNMA-specific paths and constants.

    Environment Variables:
        FNMA_DATA_DIR: Root FNMA data directory
        FNMA_RAW_DIR: Raw data directory
        FNMA_BRONZE_DIR: Bronze layer directory (staged parquet)
        FNMA_SILVER_DIR: Silver layer directory (transformed data)
        FNMA_GOLD_DIR: Gold layer directory (aggregations)

    Example:
        >>> from mortgage_data_manager.fnma.config import FNMAConfig
        >>> print(FNMAConfig.FNMA_DATA_DIR)
        PosixPath('/path/to/data/fnma')
        >>> print(FNMAConfig.FNMA_BRONZE_DIR)
        PosixPath('/path/to/data/fnma/bronze')
    """

    # FNMA data directories
    FNMA_DATA_DIR: Path = Path(
        config("FNMA_DATA_DIR", default=str(MortgageDataConfig.get_subpackage_data_dir("fnma")))
    )
    FNMA_RAW_DIR: Path = Path(config("FNMA_RAW_DIR", default=str(FNMA_DATA_DIR / "raw")))
    FNMA_BRONZE_DIR: Path = Path(config("FNMA_BRONZE_DIR", default=str(FNMA_DATA_DIR / "bronze")))
    FNMA_SILVER_DIR: Path = Path(config("FNMA_SILVER_DIR", default=str(FNMA_DATA_DIR / "silver")))
    FNMA_GOLD_DIR: Path = Path(config("FNMA_GOLD_DIR", default=str(FNMA_DATA_DIR / "gold")))

    # Schema directory
    FNMA_SCHEMAS_DIR: Path = Path(
        config("FNMA_SCHEMAS_DIR", default=str(MortgageDataConfig.SCHEMAS_DIR / "fnma"))
    )
    FNMA_SCHEMA_FILE: Path = FNMA_SCHEMAS_DIR / "docs" / "display.xlsx"

    # Backward compatibility aliases
    RAW_DIR = FNMA_RAW_DIR
    BRONZE_DIR = FNMA_BRONZE_DIR
    SILVER_DIR = FNMA_SILVER_DIR

    # Processing constants
    DEFAULT_DELIMITER: str = "|"
    DEFAULT_ENCODING: str = "utf-8"

    @classmethod
    def get_prefix_dir(
        cls,
        stage: Literal["raw", "bronze", "silver", "gold"],
        prefix: str,
    ) -> Path:
        """Get the prefix sub-directory under an FNMA medallion stage.

        FNMA organizes each medallion stage into per-prefix sub-directories
        (e.g. ``issuances``). This helper resolves the prefix axis; for the bare
        stage directory use the per-dir attributes (``FNMA_SILVER_DIR``, etc.)
        or the inherited :meth:`MortgageDataConfig.get_medallion_dir`.

        Args:
            stage: Medallion stage ('raw', 'bronze', 'silver', or 'gold')
            prefix: Data prefix naming the sub-directory

        Returns:
            Path to the prefix sub-directory under the stage

        Example:
            >>> FNMAConfig.get_prefix_dir('silver', 'issuances')
            PosixPath('/path/to/data/fnma/silver/issuances')

        Note:
            ``get_medallion_dir`` is now inherited from
            :class:`MortgageDataConfig` unmodified (signature
            ``get_medallion_dir(subpackage, stage)``).
        """
        stage_dirs = {
            "raw": cls.FNMA_RAW_DIR,
            "bronze": cls.FNMA_BRONZE_DIR,
            "silver": cls.FNMA_SILVER_DIR,
            "gold": cls.FNMA_GOLD_DIR,
        }
        return stage_dirs[stage] / prefix

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary directories for FNMA data processing.

        Example:
            >>> FNMAConfig.ensure_directories()
            >>> # Creates: data/fnma/raw, data/fnma/bronze, etc.
        """
        # Create medallion directories
        for d in (cls.FNMA_RAW_DIR, cls.FNMA_BRONZE_DIR, cls.FNMA_SILVER_DIR):
            d.mkdir(parents=True, exist_ok=True)

        # Create schemas directory
        cls.FNMA_SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)


# Backward compatibility module-level exports
PROJECT_ROOT = FNMAConfig.PROJECT_DIR
DATA_ROOT = FNMAConfig.DATA_DIR
FNMA_DATA = FNMAConfig.FNMA_DATA_DIR


# ----------------------------------------------------------------------------- #
# Silver schema spec (close-to-final dtypes; see core/types.py + the 2026-06-27
# silver type audit, derived data-driven from the full issuances domain). The
# issuances / performance / terminations datasets all share this ~117-col FNMA SF
# Loan-Performance schema; the EXCL non-standard variant is unified by
# apply_silver_types' conform-to-target. Column names preserve upstream
# trailing/double spaces (combined/ consumers depend on the exact names).
# ----------------------------------------------------------------------------- #
# Stable GSE code sets only (closed vocabularies). Growing/contaminated sets —
# seller/servicer names, delinquency status, zero-balance code, the '7'-family —
# stay String to avoid silently nulling unseen values (precision-first).
_FNMA_CHANNEL = pl.Enum(["B", "C", "R"])
_FNMA_LOAN_PURPOSE = pl.Enum(["C", "P", "R"])
_FNMA_PROPERTY_TYPE = pl.Enum(["CO", "CP", "MH", "PU", "SF"])
_FNMA_OCCUPANCY_STATUS = pl.Enum(["I", "P", "S"])
_FNMA_AMORTIZATION_TYPE = pl.Enum(["ARM", "FRM"])
_FNMA_MORTGAGE_INSURANCE_TYPE = pl.Enum(["1", "2", "3"])
_FNMA_PROPERTY_VALUATION_METHOD = pl.Enum(["A", "C", "P", "R", "W"])
_FNMA_ARM_PRODUCT_TYPE = pl.Enum(
    [
        "ARM3_1FixedPeriodARM30Year",
        "ARM5_1FixedPeriodARM30Year",
        "ARM7_1FixedPeriodARM30Year",
        "ARM10_1FixedPeriodARM30Year",
        "OtherARM",
    ]
)
_FNMA_INDEX = pl.Enum(
    [
        "5YearTreasuryConstantMaturitiesWeeklyAverage",
        "LIBOR_OneYearWallStreetJournalDaily",
        "WeeklyOneYearTreasurySecuritiesConstantMaturityFRB_H15",
        "Unknown",
    ]
)

#: Date columns (all MMYYYY month encodings).
_FNMA_DATE_COLS = (
    "Monthly Reporting Period",
    "Origination Date",
    "First Payment Date",
    "Maturity Date",
    "Interest Only First Principal And Interest Payment Date",
    "Zero Balance Effective Date",
    "Repurchase Date",
    "Last Paid Installment Date",
    "Foreclosure Date",
    "Disposition Date",
    "Original List Start Date",
    "Current List Start Date",
    "Zero Balance Code Change Date",
    "Loan Holdback Effective Date",
    "Next Interest Rate Adjustment Date",
    "Next Payment Change Date",
)

FNMA_SF_SILVER = SilverSpec(
    dates={c: "mmyyyy" for c in _FNMA_DATE_COLS},
    enums={
        "Channel": _FNMA_CHANNEL,
        "Loan Purpose ": _FNMA_LOAN_PURPOSE,
        "Property Type": _FNMA_PROPERTY_TYPE,
        "Occupancy Status": _FNMA_OCCUPANCY_STATUS,
        "Amortization Type": _FNMA_AMORTIZATION_TYPE,
        "Mortgage Insurance Type": _FNMA_MORTGAGE_INSURANCE_TYPE,
        "Property Valuation Method ": _FNMA_PROPERTY_VALUATION_METHOD,
        "ARM Product Type": _FNMA_ARM_PRODUCT_TYPE,
        "Index": _FNMA_INDEX,
    },
    bools=(
        "First Time Home Buyer Indicator",
        "Prepayment Penalty Indicator",
        "Interest Only Loan Indicator",
        "Modification Flag",
        "Mortgage Insurance Cancellation Indicator",
        "Servicing Activity Indicator",
        "Relocation Mortgage Indicator",
        "Loan Holdback Indicator",
        "High Balance Loan Indicator ",
        "ARM Initial Fixed-Rate Period  ≤ 5 YR Indicator",
        "ARM Balloon Indicator",
        "High Loan to Value (HLTV) Refinance Option Indicator",
        "Repurchase Make Whole Proceeds Flag",
        "Non-Standard Documentation Indicator",
        "Non-Standard Underwriting or Eligibility Indicator",
        "Government Insured/Guarantee Indicator",
        "Negative Amortization Indicator",
    ),
    zip3=("Zip Code Short",),
    casts={
        "Original Interest Rate": pl.Float32,
        "Current Interest Rate": pl.Float32,
        "Original Loan Term": pl.Int16,
        "Loan Age": pl.Int16,
        "Remaining Months to Legal Maturity": pl.Int16,
        "Remaining Months To Maturity": pl.Int16,
        "Original Loan to Value Ratio (LTV)": pl.Int16,
        "Original Combined Loan to Value Ratio (CLTV)": pl.Int16,
        "Number of Borrowers": pl.Int8,
        "Debt-To-Income (DTI)": pl.Int8,
        "Borrower Credit Score at Origination": pl.Int16,
        "Co-Borrower Credit Score at Origination": pl.Int16,
        "Number of Units": pl.Int8,
        "Mortgage Insurance Percentage": pl.Float32,
        "Months to Amortization": pl.Int16,
        "Borrower Credit Score At Issuance": pl.Int16,
        "Co-Borrower Credit Score At Issuance": pl.Int16,
        "Borrower Credit Score Current ": pl.Int16,
        "Co-Borrower Credit Score Current": pl.Int16,
        "Initial Fixed-Rate Period ": pl.Int16,
        "Interest Rate Adjustment Frequency": pl.Int8,
        "Initial Interest Rate Cap Up Percent": pl.Float32,
        "Periodic Interest Rate Cap Up Percent": pl.Float32,
        "Lifetime Interest Rate Cap Up Percent": pl.Float32,
        "Mortgage Margin": pl.Float32,
        "ARM Plan Number": pl.Int32,
        "Alternative Delinquency  Resolution Count": pl.Int8,
        "Origination Classic FICO®": pl.Int16,
        "Issuance Classic FICO®": pl.Int16,
        "Current Classic FICO®": pl.Int16,
    },
    target=pl.Schema(
        {
            "Loan Identifier": pl.String,
            "Monthly Reporting Period": pl.Date,
            "Reference Pool ID": pl.String,
            "Channel": _FNMA_CHANNEL,
            "Seller Name": pl.String,
            "Servicer Name": pl.String,
            "Master Servicer": pl.String,
            "Original Interest Rate": pl.Float32,
            "Current Interest Rate": pl.Float32,
            "Original UPB": pl.Float64,
            "UPB at Issuance": pl.Float64,
            "Current Actual UPB": pl.Float64,
            "Original Loan Term": pl.Int16,
            "Origination Date": pl.Date,
            "First Payment Date": pl.Date,
            "Loan Age": pl.Int16,
            "Remaining Months to Legal Maturity": pl.Int16,
            "Remaining Months To Maturity": pl.Int16,
            "Maturity Date": pl.Date,
            "Original Loan to Value Ratio (LTV)": pl.Int16,
            "Original Combined Loan to Value Ratio (CLTV)": pl.Int16,
            "Number of Borrowers": pl.Int8,
            "Debt-To-Income (DTI)": pl.Int8,
            "Borrower Credit Score at Origination": pl.Int16,
            "Co-Borrower Credit Score at Origination": pl.Int16,
            "First Time Home Buyer Indicator": pl.Boolean,
            "Loan Purpose ": _FNMA_LOAN_PURPOSE,
            "Property Type": _FNMA_PROPERTY_TYPE,
            "Number of Units": pl.Int8,
            "Occupancy Status": _FNMA_OCCUPANCY_STATUS,
            "Property State": pl.String,
            "Metropolitan Statistical Area (MSA) or Metropolitan Statistical Division Area (MSDA)": pl.String,
            "Zip Code Short": pl.String,
            "Mortgage Insurance Percentage": pl.Float32,
            "Amortization Type": _FNMA_AMORTIZATION_TYPE,
            "Prepayment Penalty Indicator": pl.Boolean,
            "Interest Only Loan Indicator": pl.Boolean,
            "Interest Only First Principal And Interest Payment Date": pl.Date,
            "Months to Amortization": pl.Int16,
            "Current Loan Delinquency Status": pl.String,
            "Loan Payment History": pl.String,
            "Modification Flag": pl.Boolean,
            "Mortgage Insurance Cancellation Indicator": pl.Boolean,
            "Zero Balance Code": pl.String,
            "Zero Balance Effective Date": pl.Date,
            "UPB at the Time of Removal": pl.Float64,
            "Repurchase Date": pl.Date,
            "Scheduled Principal Current": pl.Float64,
            "Total Principal Current": pl.Float64,
            "Unscheduled Principal Current": pl.Float64,
            "Last Paid Installment Date": pl.Date,
            "Foreclosure Date": pl.Date,
            "Disposition Date": pl.Date,
            "Foreclosure Costs": pl.Float64,
            "Property Preservation and Repair Costs": pl.Float64,
            "Asset Recovery Costs": pl.Float64,
            "Miscellaneous Holding Expenses and Credits": pl.Float64,
            "Associated Taxes for Holding Property": pl.Float64,
            "Net Sales Proceeds": pl.Float64,
            "Credit Enhancement Proceeds": pl.Float64,
            "Repurchase Make Whole Proceeds": pl.Float64,
            "Other Foreclosure Proceeds": pl.Float64,
            "Modification-Related Non-Interest Bearing UPB": pl.Float64,
            "Principal Forgiveness Amount": pl.Float64,
            "Original List Start Date": pl.Date,
            "Original List Price": pl.Float64,
            "Current List Start Date": pl.Date,
            "Current List Price": pl.Float64,
            "Borrower Credit Score At Issuance": pl.Int16,
            "Co-Borrower Credit Score At Issuance": pl.Int16,
            "Borrower Credit Score Current ": pl.Int16,
            "Co-Borrower Credit Score Current": pl.Int16,
            "Mortgage Insurance Type": _FNMA_MORTGAGE_INSURANCE_TYPE,
            "Servicing Activity Indicator": pl.Boolean,
            "Current Period Modification Loss Amount": pl.Float64,
            "Cumulative Modification Loss Amount": pl.Float64,
            "Current Period Credit Event Net Gain or Loss": pl.Float64,
            "Cumulative Credit Event Net Gain or Loss": pl.Float64,
            "Special Eligibility Program": pl.String,
            "Foreclosure Principal Write-off Amount": pl.Float64,
            "Relocation Mortgage Indicator": pl.Boolean,
            "Zero Balance Code Change Date": pl.Date,
            "Loan Holdback Indicator": pl.Boolean,
            "Loan Holdback Effective Date": pl.Date,
            "Delinquent Accrued Interest": pl.Float64,
            "Property Valuation Method ": _FNMA_PROPERTY_VALUATION_METHOD,
            "High Balance Loan Indicator ": pl.Boolean,
            "ARM Initial Fixed-Rate Period  ≤ 5 YR Indicator": pl.Boolean,
            "ARM Product Type": _FNMA_ARM_PRODUCT_TYPE,
            "Initial Fixed-Rate Period ": pl.Int16,
            "Interest Rate Adjustment Frequency": pl.Int8,
            "Next Interest Rate Adjustment Date": pl.Date,
            "Next Payment Change Date": pl.Date,
            "Index": _FNMA_INDEX,
            "ARM Cap Structure": pl.String,
            "Initial Interest Rate Cap Up Percent": pl.Float32,
            "Periodic Interest Rate Cap Up Percent": pl.Float32,
            "Lifetime Interest Rate Cap Up Percent": pl.Float32,
            "Mortgage Margin": pl.Float32,
            "ARM Balloon Indicator": pl.Boolean,
            "ARM Plan Number": pl.Int32,
            "Borrower Assistance Plan": pl.String,
            "High Loan to Value (HLTV) Refinance Option Indicator": pl.Boolean,
            "Deal Name": pl.String,
            "Repurchase Make Whole Proceeds Flag": pl.Boolean,
            "Alternative Delinquency Resolution": pl.String,
            "Alternative Delinquency  Resolution Count": pl.Int8,
            "Total Deferral Amount ": pl.Float64,
            "Payment Deferral Modification Event Indicator": pl.String,
            "Interest Bearing UPB": pl.Float64,
            "Origination Classic FICO®": pl.Int16,
            "Issuance Classic FICO®": pl.Int16,
            "Current Classic FICO®": pl.Int16,
            "Non-Standard Documentation Indicator": pl.Boolean,
            "Non-Standard Underwriting or Eligibility Indicator": pl.Boolean,
            "Government Insured/Guarantee Indicator": pl.Boolean,
            "Negative Amortization Indicator": pl.Boolean,
        }
    ),
)
