"""Configuration for Fannie Mae (FNMA) data.

Configuration for FNMA (Fannie Mae) data processing pipeline.

This module provides:
    - FNMAConfig: Main configuration class inheriting from MortgageDataConfig

The FNMAConfig class follows the project's configuration inheritance pattern,
supporting environment variable overrides and medallion architecture paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal

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
        >>> from mortgage_data_manager.sources.fnma.config import FNMAConfig
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

    #: Scratch space for extracting archive members. Lives on the data volume, not
    #: the boot disk: the back-history quarterly CSVs inside Performance_All.zip
    #: reach 40.5 GB (2003Q3), well past what a system temp dir can hold.
    FNMA_TMP_DIR: Path = Path(config("FNMA_TMP_DIR", default=str(FNMA_DATA_DIR / "tmp")))

    # Schema directory
    FNMA_SCHEMAS_DIR: Path = Path(
        config("FNMA_SCHEMAS_DIR", default=str(MortgageDataConfig.SCHEMAS_DIR / "fnma"))
    )
    #: Single-family field layout, transcribed from the "Combined Glossary" sheet of
    #: Fannie Mae's CRT file layout and glossary workbook.
    FNMA_SCHEMA_FILE: Path = FNMA_SCHEMAS_DIR / "sf_loan_layout.csv"

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
        or the inherited `MortgageDataConfig.get_medallion_dir`.

        Args:
            stage: Medallion stage ('raw', 'bronze', 'silver', or 'gold')
            prefix: Data prefix naming the sub-directory

        Returns:
            Path to the prefix sub-directory under the stage

        Example:
            >>> FNMAConfig.get_prefix_dir('silver', 'issuances')
            PosixPath('/path/to/data/fnma/silver/issuances')

        Note:
            ``get_medallion_dir`` is inherited from
            `MortgageDataConfig` unmodified (signature
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


# ----------------------------------------------------------------------------- #
# Dataset registries
# ----------------------------------------------------------------------------- #
# Bronze and silver do NOT decompose the same way, so each stage gets its own
# registry: bronze ingests raw artifact families, silver builds derived tables
# from them. `multifamily` is the one name common to both.

#: Bronze datasets -> what each ingests. Single source of truth for the
#: ``--datasets`` choices on ``fnma bronze``.
BRONZE_DATASETS: Final[dict[str, str]] = {
    "sf": "Per-quarter single-family loan-performance zips (YYYYQ[1-4]*.zip).",
    "exclusions": (
        "Exclusions.zip (loans left out of the standard dataset -- ARM, Alt-A, "
        "government-insured, bulk acquisitions -- for acquisitions 2000Q1-2020Q3)."
    ),
    "performance_all": (
        "Performance_All.zip (the bulk archive; sole source of the pre-2016 history). "
        "Not in the default set -- it restates every quarter back to 2000Q1."
    ),
    "harp": "HARP_Files.zip (one un-partitioned HARP panel + the pre-HARP loan bridge).",
    "multifamily": "Multifamily.zip / Multifamily_DSCR.zip (each restates full history).",
}

#: Bronze datasets built when ``--datasets`` is omitted. ``performance_all`` is
#: excluded: it overlaps ``sf`` on every recent quarter and walks a 64 GB archive,
#: so it is opt-in rather than something a bare ``fnma bronze`` triggers.
DEFAULT_BRONZE_DATASETS: Final[list[str]] = ["sf", "exclusions", "harp", "multifamily"]

#: Silver datasets -> what each builds from bronze.
SILVER_DATASETS: Final[dict[str, str]] = {
    "issuances": "First observation per loan (also writes the terminations table).",
    "performance": "The full loan x month SF panel (input to combined loan_performance).",
    "harp": "HARP performance / issuances / terminations + the pre-HARP loan bridge.",
    "multifamily": "MF performance / acquisitions / terminations / current_dscr.",
}

_STAGE_REGISTRIES: Final[dict[str, dict[str, str]]] = {
    "bronze": BRONZE_DATASETS,
    "silver": SILVER_DATASETS,
}


def resolve_datasets(
    datasets: list[str] | None,
    *,
    stage: Literal["bronze", "silver"],
) -> list[str]:
    """Default to every dataset for a stage; validate any explicit subset.

    Args:
        datasets: Dataset identifiers, or ``None`` for all of the stage's registry.
        stage: Which registry to resolve against.

    Returns:
        Validated list of dataset identifiers.

    Raises:
        ValueError: If an unknown dataset identifier is given.
    """
    registry = _STAGE_REGISTRIES[stage]
    if datasets is None:
        return list(DEFAULT_BRONZE_DATASETS) if stage == "bronze" else list(registry)
    unknown = [d for d in datasets if d not in registry]
    if unknown:
        raise ValueError(f"Unknown FNMA {stage} datasets: {unknown}; valid: {list(registry)}")
    return list(datasets)


# ----------------------------------------------------------------------------- #
# Silver schema spec (close-to-final dtypes; see core/types.py; derived data-driven from
# the full issuances domain). The issuances / performance / terminations datasets all
# share this ~117-col FNMA SF Loan-Performance schema; the EXCL non-standard variant is
# unified by apply_silver_types' conform-to-target. Column names preserve upstream
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

#: The ~117-column SF Loan-Performance schema, shared by the issuances,
#: terminations and performance silver datasets. Dates are MMYYYY month
#: encodings; Zip Code Short is a 3-digit ZIP prefix. Columns marked ``Declared``
#: arrive already correctly typed from bronze and are only recorded here so
#: conform-to-target keeps them in canonical order.
_SF_COLS: list[tuple[str, ColumnRole]] = [
    ("Loan Identifier", Declared(pl.String)),
    ("Monthly Reporting Period", "mmyyyy"),
    ("Reference Pool ID", Declared(pl.String)),
    ("Channel", _FNMA_CHANNEL),
    ("Seller Name", Declared(pl.String)),
    ("Servicer Name", Declared(pl.String)),
    ("Master Servicer", Declared(pl.String)),
    ("Original Interest Rate", pl.Float32),
    ("Current Interest Rate", pl.Float32),
    ("Original UPB", Declared(pl.Float64)),
    ("UPB at Issuance", Declared(pl.Float64)),
    ("Current Actual UPB", Declared(pl.Float64)),
    ("Original Loan Term", pl.Int16),
    ("Origination Date", "mmyyyy"),
    ("First Payment Date", "mmyyyy"),
    ("Loan Age", pl.Int16),
    ("Remaining Months to Legal Maturity", pl.Int16),
    ("Remaining Months To Maturity", pl.Int16),
    ("Maturity Date", "mmyyyy"),
    ("Original Loan to Value Ratio (LTV)", pl.Int16),
    ("Original Combined Loan to Value Ratio (CLTV)", pl.Int16),
    ("Number of Borrowers", pl.Int8),
    ("Debt-To-Income (DTI)", pl.Int8),
    ("Borrower Credit Score at Origination", pl.Int16),
    ("Co-Borrower Credit Score at Origination", pl.Int16),
    ("First Time Home Buyer Indicator", BOOL),
    ("Loan Purpose ", _FNMA_LOAN_PURPOSE),
    ("Property Type", _FNMA_PROPERTY_TYPE),
    ("Number of Units", pl.Int8),
    ("Occupancy Status", _FNMA_OCCUPANCY_STATUS),
    ("Property State", Declared(pl.String)),
    (
        "Metropolitan Statistical Area (MSA) or Metropolitan Statistical Division Area (MSDA)",
        Declared(pl.String),
    ),
    ("Zip Code Short", ZIP3),
    ("Mortgage Insurance Percentage", pl.Float32),
    ("Amortization Type", _FNMA_AMORTIZATION_TYPE),
    ("Prepayment Penalty Indicator", BOOL),
    ("Interest Only Loan Indicator", BOOL),
    ("Interest Only First Principal And Interest Payment Date", "mmyyyy"),
    ("Months to Amortization", pl.Int16),
    ("Current Loan Delinquency Status", Declared(pl.String)),
    ("Loan Payment History", Declared(pl.String)),
    ("Modification Flag", BOOL),
    ("Mortgage Insurance Cancellation Indicator", BOOL),
    ("Zero Balance Code", Declared(pl.String)),
    ("Zero Balance Effective Date", "mmyyyy"),
    ("UPB at the Time of Removal", Declared(pl.Float64)),
    ("Repurchase Date", "mmyyyy"),
    ("Scheduled Principal Current", Declared(pl.Float64)),
    ("Total Principal Current", Declared(pl.Float64)),
    ("Unscheduled Principal Current", Declared(pl.Float64)),
    ("Last Paid Installment Date", "mmyyyy"),
    ("Foreclosure Date", "mmyyyy"),
    ("Disposition Date", "mmyyyy"),
    ("Foreclosure Costs", Declared(pl.Float64)),
    ("Property Preservation and Repair Costs", Declared(pl.Float64)),
    ("Asset Recovery Costs", Declared(pl.Float64)),
    ("Miscellaneous Holding Expenses and Credits", Declared(pl.Float64)),
    ("Associated Taxes for Holding Property", Declared(pl.Float64)),
    ("Net Sales Proceeds", Declared(pl.Float64)),
    ("Credit Enhancement Proceeds", Declared(pl.Float64)),
    ("Repurchase Make Whole Proceeds", Declared(pl.Float64)),
    ("Other Foreclosure Proceeds", Declared(pl.Float64)),
    ("Modification-Related Non-Interest Bearing UPB", Declared(pl.Float64)),
    ("Principal Forgiveness Amount", Declared(pl.Float64)),
    ("Original List Start Date", "mmyyyy"),
    ("Original List Price", Declared(pl.Float64)),
    ("Current List Start Date", "mmyyyy"),
    ("Current List Price", Declared(pl.Float64)),
    ("Borrower Credit Score At Issuance", pl.Int16),
    ("Co-Borrower Credit Score At Issuance", pl.Int16),
    ("Borrower Credit Score Current ", pl.Int16),
    ("Co-Borrower Credit Score Current", pl.Int16),
    ("Mortgage Insurance Type", _FNMA_MORTGAGE_INSURANCE_TYPE),
    ("Servicing Activity Indicator", BOOL),
    ("Current Period Modification Loss Amount", Declared(pl.Float64)),
    ("Cumulative Modification Loss Amount", Declared(pl.Float64)),
    ("Current Period Credit Event Net Gain or Loss", Declared(pl.Float64)),
    ("Cumulative Credit Event Net Gain or Loss", Declared(pl.Float64)),
    ("Special Eligibility Program", Declared(pl.String)),
    ("Foreclosure Principal Write-off Amount", Declared(pl.Float64)),
    ("Relocation Mortgage Indicator", BOOL),
    ("Zero Balance Code Change Date", "mmyyyy"),
    ("Loan Holdback Indicator", BOOL),
    ("Loan Holdback Effective Date", "mmyyyy"),
    ("Delinquent Accrued Interest", Declared(pl.Float64)),
    ("Property Valuation Method ", _FNMA_PROPERTY_VALUATION_METHOD),
    ("High Balance Loan Indicator ", BOOL),
    ("ARM Initial Fixed-Rate Period  ≤ 5 YR Indicator", BOOL),
    ("ARM Product Type", _FNMA_ARM_PRODUCT_TYPE),
    ("Initial Fixed-Rate Period ", pl.Int16),
    ("Interest Rate Adjustment Frequency", pl.Int8),
    ("Next Interest Rate Adjustment Date", "mmyyyy"),
    ("Next Payment Change Date", "mmyyyy"),
    ("Index", _FNMA_INDEX),
    ("ARM Cap Structure", Declared(pl.String)),
    ("Initial Interest Rate Cap Up Percent", pl.Float32),
    ("Periodic Interest Rate Cap Up Percent", pl.Float32),
    ("Lifetime Interest Rate Cap Up Percent", pl.Float32),
    ("Mortgage Margin", pl.Float32),
    ("ARM Balloon Indicator", BOOL),
    ("ARM Plan Number", pl.Int32),
    ("Borrower Assistance Plan", Declared(pl.String)),
    ("High Loan to Value (HLTV) Refinance Option Indicator", BOOL),
    ("Deal Name", Declared(pl.String)),
    ("Repurchase Make Whole Proceeds Flag", BOOL),
    ("Alternative Delinquency Resolution", Declared(pl.String)),
    ("Alternative Delinquency  Resolution Count", pl.Int8),
    ("Total Deferral Amount ", Declared(pl.Float64)),
    ("Payment Deferral Modification Event Indicator", Declared(pl.String)),
    ("Interest Bearing UPB", Declared(pl.Float64)),
    ("Origination Classic FICO®", pl.Int16),
    ("Issuance Classic FICO®", pl.Int16),
    ("Current Classic FICO®", pl.Int16),
    ("Non-Standard Documentation Indicator", BOOL),
    ("Non-Standard Underwriting or Eligibility Indicator", BOOL),
    ("Government Insured/Guarantee Indicator", BOOL),
    ("Negative Amortization Indicator", BOOL),
]

FNMA_SF_SILVER = spec_from_columns(_SF_COLS)

#: The HARP original-loan bridge (``Loan_Mapping.txt``). The HARP refinance panel
#: itself shares ``_SF_COLS`` — Fannie publishes it in the same 113-field Primary
#: layout — so only this 1:1 id pair needs a spec of its own. Both ids stay text
#: (see ``import_bronze.HARP_MAPPING_COLUMNS`` for the column direction).
_HARP_MAPPING_COLS: list[tuple[str, ColumnRole]] = [
    ("Pre-HARP Loan Identifier", TEXT),
    ("Loan Identifier", TEXT),
]

FNMA_HARP_MAPPING_SILVER = spec_from_columns(_HARP_MAPPING_COLS)


# ----------------------------------------------------------------------------- #
# Multifamily Loan Performance Dataset silver specs (data-driven from the 202512
# release; see import_bronze.import_multifamily for the bronze layout). Each
# quarterly release restates the FULL loan x month history in one CSV, plus an
# annual current-DSCR supplement. Closed vocabularies per the MF glossary become
# Enums; growth-prone sets (Affordable Housing Type, DUS Prepayment
# Outcomes/Segments, Prepayment Provision) stay String. Gotchas: 'Prepayment
# Provision End Date' is TEXT (e.g. "YM(01/31/2026), See Issuance
# Documents(08/01/2026)"), never a date; 'Property Zip Code'/'Property City'
# carry a "Multiple Properties" sentinel value, so they stay String; column
# names preserve upstream spacing ('Transaction ID ' has a trailing space).
# ----------------------------------------------------------------------------- #
_FNMA_MF_AMORTIZATION_TYPE = pl.Enum(
    [
        "Amortizing Balloon",
        "Fully Amortizing",
        "Interest Only/Amortizing/Balloon",
        "Interest Only/Balloon",
        "Interest Only/Fully Amortizing",
    ]
)
_FNMA_MF_INTEREST_TYPE = pl.Enum(["ARM", "Fixed"])
_FNMA_MF_LOAN_PRODUCT_TYPE = pl.Enum(["Bulk Delivery", "Credit Facility", "DUS", "Non-DUS"])
_FNMA_MF_LIEN_POSITION = pl.Enum(["First", "Second", "Third", "Fourth or More Subordinate"])
_FNMA_MF_DSCR_TYPE = pl.Enum(
    ["Deal UW DSCR NCF", "Lender UW DSCR", "UW Actual DSCR", "UW DSCR NCF"]
)
_FNMA_MF_LOSS_SHARING_TYPE = pl.Enum(
    ["Full Lender Loss", "No Lender Loss Sharing", "Pari Passu", "Standard DUS"]
)
_FNMA_MF_PROPERTY_TYPE = pl.Enum(
    [
        "Cooperative",
        "Dedicated Student",
        "Manufactured Housing Community",
        "Military",
        "Multifamily",
        "Multiple Properties",
        "Other",
        "Seniors",
    ]
)
_FNMA_MF_LIQUIDATION_CODE = pl.Enum(
    [
        "Deed-in-Lieu",
        "Discounted Payoff",
        "Dissolution",
        "Foreclosure",
        "Fully Paid, Matured",
        "Fully Paid, Prepaid",
        "Fully Paid, Refinanced",
        "Other Liquidation",
        "Repurchase",
        "Substitution",
        "Third Party Sale",
    ]
)
_FNMA_MF_CREDIT_EVENT_TYPE = pl.Enum(["Non-REO", "REO"])
_FNMA_MF_PAYMENT_STATUS = pl.Enum(
    ["Current", "30-59 Days Delinquent", "60-89 Days Delinquent", "90+ Days Delinquent"]
)

#: The multifamily loan x month panel. Dates are ISO YYYY-MM-DD.
_MF_COLS: list[tuple[str, ColumnRole]] = [
    ("Loan Number", TEXT),
    ("Acquisition Date", "iso"),
    ("Note Date", "iso"),
    ("Maturity Date at Acquisition", "iso"),
    ("Loan Acquisition UPB", Declared(pl.Float64)),
    ("Amortization Type", _FNMA_MF_AMORTIZATION_TYPE),
    ("Interest Type", _FNMA_MF_INTEREST_TYPE),
    ("Loan Product Type", _FNMA_MF_LOAN_PRODUCT_TYPE),
    ("Original UPB", Declared(pl.Float64)),
    ("Amortization Term", pl.Int16),
    ("Original Interest Rate", pl.Float32),
    ("Lien Position", _FNMA_MF_LIEN_POSITION),
    ("Transaction ID ", TEXT),
    ("Issue Date", "iso"),
    ("Loan Acquisition LTV", pl.Float32),
    ("Underwritten DSCR", pl.Float32),
    ("Underwritten DSCR Type", _FNMA_MF_DSCR_TYPE),
    ("Original Term", pl.Int16),
    ("Original I/O Term", pl.Int16),
    ("I/O End Date", "iso"),
    ("Loan Ever 60+ Days Delinquent", BOOL),
    ("Loss Sharing Type", _FNMA_MF_LOSS_SHARING_TYPE),
    ("Modified Loss Sharing Percentage", pl.Float32),
    ("Number of Properties at Acquisition", pl.Int16),
    ("Property Acquisition Total Unit Count", pl.Int32),
    ("Specific Property Type", _FNMA_MF_PROPERTY_TYPE),
    ("Year Built", pl.Int16),
    ("Property City", Declared(pl.String)),
    ("Property State", Declared(pl.String)),
    ("Property Zip Code", TEXT),
    ("Metropolitan Statistical Area", Declared(pl.String)),
    ("Physical Occupancy %", pl.Float32),
    ("Liquidation/Prepayment Code", _FNMA_MF_LIQUIDATION_CODE),
    ("Liquidation/Prepayment Date", "iso"),
    ("Foreclosure Date", "iso"),
    ("Credit Event Date", "iso"),
    ("Foreclosure Value", Declared(pl.Float64)),
    ("Lifetime Net Credit Loss Amount", Declared(pl.Float64)),
    ("Sale Price", Declared(pl.Float64)),
    ("Default Amount", Declared(pl.Float64)),
    ("Credit Event Type", _FNMA_MF_CREDIT_EVENT_TYPE),
    ("Reporting Period Date", "iso"),
    ("Loan Active Property Count", pl.Int16),
    ("Note Rate", pl.Float32),
    ("Maturity Date - Current", "iso"),
    ("UPB - Current", Declared(pl.Float64)),
    ("Delinquency UPB", Declared(pl.Float64)),
    ("Loan Payment Status", _FNMA_MF_PAYMENT_STATUS),
    ("SDQ Indicator", BOOL),
    ("Most Recent Modification Date", "iso"),
    ("Modification Indicator", BOOL),
    ("Defeasance Date", "iso"),
    ("Prepayment Provision", Declared(pl.String)),
    ("Prepayment Provision End Date", Declared(pl.String)),
    ("Affordable Housing Type", Declared(pl.String)),
    ("MCIRT Deal ID", TEXT),
    ("MCAS Deal ID", TEXT),
    ("DUS Prepayment Outcomes", Declared(pl.String)),
    ("DUS Prepayment Segments", Declared(pl.String)),
    ("Loan Age", pl.Int16),
    ("Green Bond Indicator", BOOL),
    ("Social Bond Indicator", BOOL),
]

FNMA_MF_SILVER = spec_from_columns(_MF_COLS)

#: The annual loan x year current-DSCR supplement.
_MF_DSCR_COLS: list[tuple[str, ColumnRole]] = [
    ("Loan Number", TEXT),
    ("Year", pl.Int16),
    ("Year DSCR", pl.Float32),
]

FNMA_MF_DSCR_SILVER = spec_from_columns(_MF_DSCR_COLS)
