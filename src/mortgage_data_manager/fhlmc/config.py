"""FHLMC (Freddie Mac) data processing configuration.

This module provides FHLMCConfig, the main configuration class for FHLMC
data processing, following the project's configuration inheritance pattern.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import polars as pl
import typer
from decouple import config
from rich.console import Console

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.types import SilverSpec

_console = Console()


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
        >>> from mortgage_data_manager.fhlmc.config import FHLMCConfig
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

    # Schema file location
    FHLMC_SCHEMA_FILE: Path = FHLMC_SCHEMAS_DIR / "file_layout.xlsx"

    # FHLMC datasets (3 types: simpler than FHFA's 7)
    VALID_DATASETS: list[str] = ["origination", "performance", "reperforming"]

    # Year ranges for FHLMC historical data
    MIN_YEAR: int = 1999
    MAX_YEAR: int = 2025

    # Parquet settings
    PARQUET_COMPRESSION = "snappy"
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
        :meth:`MortgageDataConfig.get_medallion_dir`.

        Args:
            stage: Medallion stage ('raw', 'bronze', 'silver', or 'gold')
            prefix: Data prefix naming the sub-directory

        Returns:
            Path to the prefix sub-directory under the stage

        Example:
            >>> FHLMCConfig.get_prefix_dir('silver', 'origination')
            PosixPath('/path/to/data/fhlmc/silver/origination')

        Note:
            ``get_medallion_dir`` is now inherited from
            :class:`MortgageDataConfig` unmodified (signature
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


def validate_datasets(datasets: list[str]) -> None:
    """Validate that all datasets are valid FHLMC datasets.

    Args:
        datasets: List of datasets to validate.

    Raises:
        typer.Exit: If any dataset is invalid.
    """
    invalid = [dt for dt in datasets if dt not in VALID_DATASETS]
    if invalid:
        _console.print(f"[red]Error: Invalid datasets:[/red] {', '.join(invalid)}")
        _console.print(f"[yellow]Valid datasets:[/yellow] {', '.join(VALID_DATASETS)}")
        raise typer.Exit(code=1)


def validate_years(
    years: list[int],
    min_year: int = MIN_YEAR,
    max_year: int = MAX_YEAR,
) -> None:
    """Validate that all years fall within the supported FHLMC year range.

    Args:
        years: List of years to validate.
        min_year: Minimum valid year. Defaults to FHLMCConfig.MIN_YEAR.
        max_year: Maximum valid year. Defaults to FHLMCConfig.MAX_YEAR.

    Raises:
        typer.Exit: If any year is out of range.
    """
    invalid = [y for y in years if y < min_year or y > max_year]
    if invalid:
        _console.print(
            f"[red]Error: Years out of valid range:[/red] {', '.join(map(str, invalid))}"
        )
        _console.print(f"[yellow]Valid range:[/yellow] {min_year}-{max_year}")
        raise typer.Exit(code=1)


# ----------------------------------------------------------------------------- #
# Silver schema specs (close-to-final dtypes; see core/types.py + the 2026-06-27
# silver type audit). Unlike FNMA's single shared loan-performance schema, Freddie
# splits the SF Loan-Level Dataset into TWO schemas, so FHLMC declares two specs.
# Both were derived data-driven from the FULL silver domain (all 9 origination
# vintages + a spread of performance quarters), not the audit's newest-partition
# sample — see investigations/scripts/fhlmc_domain_profile.py and the summary in
# silver_type_audit/fhlmc_domains.json. Column names are already clean (no
# trailing/double spaces), so they are used verbatim with no name stripping.
# ----------------------------------------------------------------------------- #
# Closed Freddie code sets. NOTE: Loan Purpose levels (C/N/P) genuinely differ
# from Fannie's (C/P/R) — C=cash-out refi, N=no-cash-out refi, P=purchase — so
# this is FHLMC-native, not the FNMA enum. Channel/Occupancy/Property Type happen
# to share Fannie's levels; declared locally to keep the modules decoupled (Enum
# dtype equality is by level set, so cross-GSE concat still matches).
_FHLMC_CHANNEL = pl.Enum(["B", "C", "R"])
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
_FHLMC_DEFERRED_PAYMENT_PLAN = pl.Enum(["P", "Y"])
_FHLMC_BORROWER_ASSISTANCE = pl.Enum(["F", "R", "T"])

# Missing-value sentinels (per Freddie's SF LLD user guide), null-mapped before
# any cast. Numeric cols are Int64 in bronze (int sentinels); coded alpha cols are
# String (string sentinels). MI % 0 = "no MI" is a REAL value (only 999 is the
# sentinel); MI Cancellation '7' is real (only '9' is the sentinel).
_FHLMC_ORIG_SENTINELS = {
    "Credit Score": (9999,),
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
#: as pl.Date from bronze (kept out of ``dates`` so they are not re-parsed).
#: Postal Code = ZIP3x100 is recovered to ZIP3 in import_silver (the //100 is
#: Freddie-specific encoding) and zero-padded to 3 chars here via ``zip3``.
FHLMC_ORIGINATION_SILVER = SilverSpec(
    sentinels=_FHLMC_ORIG_SENTINELS,
    enums={
        "Channel": _FHLMC_CHANNEL,
        "Occupancy Status": _FHLMC_OCCUPANCY_STATUS,
        "Property Type": _FHLMC_PROPERTY_TYPE,
        "Loan Purpose": _FHLMC_LOAN_PURPOSE,
        "Amortization Type (Formerly Product Type)": _FHLMC_AMORTIZATION_TYPE,
        "Program Indicator": _FHLMC_PROGRAM_INDICATOR,
        "Mortgage Insurance Cancellation Indicator": _FHLMC_MI_CANCELLATION,
    },
    bools=(
        "First Time Homebuyer Flag",
        "Prepayment Penalty Mortgage (PPM) Flag",
        "Super Conforming Flag",
        "HARP Indicator",
        "Interest Only (I/O) Indicator",
    ),
    zip3=("Postal Code",),
    casts={
        "Credit Score": pl.Int16,
        "Mortgage Insurance Percentage (MI %)": pl.Int8,
        "Number of Units": pl.Int8,
        "Original Combined Loan-to-Value (CLTV)": pl.Int16,
        "Original Debt-to-Income (DTI) Ratio": pl.Int8,
        "Original UPB": pl.Int32,
        "Original Loan-to-Value (LTV)": pl.Int16,
        "Original Interest Rate": pl.Float32,
        "Original Loan Term": pl.Int16,
        "Number of Borrowers": pl.Int8,
        "Property Valuation Method": pl.Int8,
    },
    identifiers=(
        "Loan Sequence Number",
        "Property State",
        "Metropolitan Statistical Area (MSA) Or Metropolitan Division",
        "Pre-HARP Loan Sequence Number",
    ),
    target=pl.Schema(
        {
            "Credit Score": pl.Int16,
            "First Payment Date": pl.Date,
            "First Time Homebuyer Flag": pl.Boolean,
            "Maturity Date": pl.Date,
            "Metropolitan Statistical Area (MSA) Or Metropolitan Division": pl.String,
            "Mortgage Insurance Percentage (MI %)": pl.Int8,
            "Number of Units": pl.Int8,
            "Occupancy Status": _FHLMC_OCCUPANCY_STATUS,
            "Original Combined Loan-to-Value (CLTV)": pl.Int16,
            "Original Debt-to-Income (DTI) Ratio": pl.Int8,
            "Original UPB": pl.Int32,
            "Original Loan-to-Value (LTV)": pl.Int16,
            "Original Interest Rate": pl.Float32,
            "Channel": _FHLMC_CHANNEL,
            "Prepayment Penalty Mortgage (PPM) Flag": pl.Boolean,
            "Amortization Type (Formerly Product Type)": _FHLMC_AMORTIZATION_TYPE,
            "Property State": pl.String,
            "Property Type": _FHLMC_PROPERTY_TYPE,
            "Postal Code": pl.String,
            "Loan Sequence Number": pl.String,
            "Loan Purpose": _FHLMC_LOAN_PURPOSE,
            "Original Loan Term": pl.Int16,
            "Number of Borrowers": pl.Int8,
            "Seller Name": pl.String,
            "Servicer Name": pl.String,
            "Super Conforming Flag": pl.Boolean,
            "Pre-HARP Loan Sequence Number": pl.String,
            "Program Indicator": _FHLMC_PROGRAM_INDICATOR,
            "HARP Indicator": pl.Boolean,
            "Property Valuation Method": pl.Int8,
            "Interest Only (I/O) Indicator": pl.Boolean,
            "Mortgage Insurance Cancellation Indicator": _FHLMC_MI_CANCELLATION,
            "vintage_year": pl.Int32,
            "vintage_quarter": pl.Int8,
        }
    ),
)

#: The loan x month performance panel, one parquet per origination quarter. All
#: four date columns (Monthly Reporting Period, Defect Settlement Date, Zero
#: Balance Effective Date, DDLPI) arrive already pl.Date from bronze. ``Current
#: Loan Delinquency Status`` stays String (carries the non-numeric 'RA' = REO
#: Acquisition code). ELTV gets the 999 = "not available" sentinel null-mapped
#: (the old passthrough builder did no sentinel cleanup); Net Sales Proceeds is
#: stored as numeric strings and cast to Float64 (verified: no non-numeric codes).
FHLMC_PERFORMANCE_SILVER = SilverSpec(
    sentinels={"Estimated Loan-to-Value (ELTV)": (999,)},
    enums={
        "Modification Flag": _FHLMC_MODIFICATION_FLAG,
        "Deferred Payment Plan": _FHLMC_DEFERRED_PAYMENT_PLAN,
        "Borrower Assistance Status Code": _FHLMC_BORROWER_ASSISTANCE,
    },
    bools=(
        "Step Modification Flag",
        "Delinquency Due to Disaster",
    ),
    casts={
        "Loan Age": pl.Int16,
        "Remaining Months to Legal Maturity": pl.Int16,
        "Zero Balance Code": pl.Int8,
        "Current Interest Rate": pl.Float32,
        "Current Deferred UPB": pl.Float64,
        "Net Sales Proceeds": pl.Float64,
        "Estimated Loan-to-Value (ELTV)": pl.Int16,
    },
    identifiers=("Loan Sequence Number",),
    target=pl.Schema(
        {
            "Loan Sequence Number": pl.String,
            "Monthly Reporting Period": pl.Date,
            "Current Actual UPB": pl.Float64,
            "Current Loan Delinquency Status": pl.String,
            "Loan Age": pl.Int16,
            "Remaining Months to Legal Maturity": pl.Int16,
            "Defect Settlement Date": pl.Date,
            "Modification Flag": _FHLMC_MODIFICATION_FLAG,
            "Zero Balance Code": pl.Int8,
            "Zero Balance Effective Date": pl.Date,
            "Current Interest Rate": pl.Float32,
            "Current Deferred UPB": pl.Float64,
            "Due Date of Last Paid Installment (DDLPI)": pl.Date,
            "MI Recoveries": pl.Float64,
            "Net Sales Proceeds": pl.Float64,
            "Non MI Recoveries": pl.Float64,
            "Expenses": pl.Float64,
            "Legal Costs": pl.Float64,
            "Maintenance and Preservation Costs": pl.Float64,
            "Taxes and Insurance": pl.Float64,
            "Miscellaneous Expenses": pl.Float64,
            "Actual Loss Calculation": pl.Float64,
            "Modification Cost": pl.Float64,
            "Step Modification Flag": pl.Boolean,
            "Deferred Payment Plan": _FHLMC_DEFERRED_PAYMENT_PLAN,
            "Estimated Loan-to-Value (ELTV)": pl.Int16,
            "Zero Balance Removal UPB": pl.Float64,
            "Delinquent Accrued Interest": pl.Float64,
            "Delinquency Due to Disaster": pl.Boolean,
            "Borrower Assistance Status Code": _FHLMC_BORROWER_ASSISTANCE,
            "Current Month Modification Cost": pl.Float64,
            "Interest Bearing UPB": pl.Float64,
        }
    ),
)
