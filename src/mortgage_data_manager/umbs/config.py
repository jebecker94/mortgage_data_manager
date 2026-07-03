"""UMBS Data Manager - Configuration.

Configuration for UMBS (Uniform Mortgage-Backed Securities) data processing pipeline.

This module provides:
    - UMBSConfig: Main configuration class inheriting from MortgageDataConfig

The UMBSConfig class follows the project's configuration inheritance pattern,
supporting environment variable overrides and medallion architecture paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, TypedDict

import polars as pl
from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.types import SilverSpec


class CorrectionPair(TypedDict):
    """Pairing of an original GSE disclosure file with its reissue (correction) sibling.

    GSEs publish R-prefixed reissue files when post-publication errors are found.
    The reissue file republishes every loan/security in every affected pool — the
    `Loan/Security Correction Indicator` field is 'Y' for the loans whose fields
    actually changed and 'N' for the unchanged loans that came along because their
    pool was touched. R-files are 100% same-month with their originals (empirically
    verified across 70+ months); they do not contain cross-month corrections.

    The silver layer uses these pairings to produce one unified dataset per pair:
    records from `correction` win over `original` when the same key appears in both.
    """

    original: str  # Bronze folder name for the original file
    correction: str  # Bronze folder name for the reissue file
    key: list[str]  # Columns that uniquely identify a record within a month
    description: str  # Short human description


# Original/correction file pairs for the silver layer.
# Outer key = GSE; inner key = canonical short name used for silver output folder.
# Update here (not in import_silver.py) when new correction-paired files are added.
CORRECTION_PAIRS: dict[str, dict[str, CorrectionPair]] = {
    "FNMA": {
        "ILLD": {
            "original": "FNM_ILLD",
            "correction": "FNM_RILLD",
            "key": ["Loan Identifier"],
            "description": "Issuance loan-level disclosure",
        },
        "IS": {
            "original": "FNM_IS",
            "correction": "FNM_RIS",
            "key": ["Security Identifier"],
            "description": "Security-level issuance",
        },
        # ISS/RISS are stratified MAX/75/MED/25/MIN files with NO header row;
        # the bronze importer currently treats the first data row as headers,
        # producing a corrupted schema. Defined here for completeness but skipped
        # by the silver importer until the bronze import is fixed.
        "ISS": {
            "original": "FNM_ISS",
            "correction": "FNM_RISS",
            "key": ["Security Identifier", "Strata"],
            "description": "Issuance stratified (BLOCKED: bronze header bug)",
        },
    },
    "FHLMC": {
        "ILLD": {
            "original": "FRE_ILLD",
            "correction": "FRE_RILLD",
            "key": ["Loan Identifier"],
            "description": "Issuance loan-level disclosure",
        },
        "IS": {
            "original": "FRE_IS",
            "correction": "FRE_RIS",
            "key": ["Security Identifier"],
            "description": "Security-level issuance",
        },
        "ISS": {
            "original": "FRE_ISS",
            "correction": "FRE_RISS",
            "key": ["Security Identifier", "Strata"],
            "description": "Issuance stratified (BLOCKED: bronze header bug)",
        },
    },
}


class UMBSConfig(MortgageDataConfig):
    """UMBS-specific configuration.

    Inherits base configuration from MortgageDataConfig and adds
    UMBS-specific paths and constants.

    Environment Variables:
        UMBS_DATA_DIR: Root UMBS data directory
        UMBS_RAW_DIR: Raw data directory
        UMBS_BRONZE_DIR: Bronze layer directory (staged parquet)
        UMBS_SILVER_DIR: Silver layer directory (transformed data)
        UMBS_GOLD_DIR: Gold layer directory (aggregations)

    Example:
        >>> from mortgage_data_manager.umbs.config import UMBSConfig
        >>> print(UMBSConfig.UMBS_DATA_DIR)
        PosixPath('/path/to/data/umbs')
        >>> print(UMBSConfig.UMBS_BRONZE_DIR)
        PosixPath('/path/to/data/umbs/bronze')
    """

    # UMBS data directories
    UMBS_DATA_DIR: Path = Path(
        config("UMBS_DATA_DIR", default=str(MortgageDataConfig.get_subpackage_data_dir("umbs")))
    )
    UMBS_RAW_DIR: Path = Path(config("UMBS_RAW_DIR", default=str(UMBS_DATA_DIR / "raw")))
    UMBS_BRONZE_DIR: Path = Path(config("UMBS_BRONZE_DIR", default=str(UMBS_DATA_DIR / "bronze")))
    UMBS_SILVER_DIR: Path = Path(config("UMBS_SILVER_DIR", default=str(UMBS_DATA_DIR / "silver")))
    UMBS_GOLD_DIR: Path = Path(config("UMBS_GOLD_DIR", default=str(UMBS_DATA_DIR / "gold")))

    # Backward compatibility aliases
    PROJECT_DIR = MortgageDataConfig.PROJECT_DIR
    RAW_DIR = UMBS_RAW_DIR
    BRONZE_DIR = UMBS_BRONZE_DIR
    SILVER_DIR = UMBS_SILVER_DIR

    @classmethod
    def get_prefix_dir(
        cls,
        stage: Literal["raw", "bronze", "silver", "gold"],
        prefix: str,
    ) -> Path:
        """Get the prefix sub-directory under a UMBS medallion stage.

        UMBS organizes each medallion stage into per-prefix sub-directories
        (e.g. ``issuances``). This helper resolves the prefix axis; for the bare
        stage directory use the per-dir attributes (``UMBS_SILVER_DIR``, etc.)
        or the inherited :meth:`MortgageDataConfig.get_medallion_dir`.

        Args:
            stage: Medallion stage ('raw', 'bronze', 'silver', or 'gold')
            prefix: Data prefix naming the sub-directory

        Returns:
            Path to the prefix sub-directory under the stage

        Example:
            >>> UMBSConfig.get_prefix_dir('silver', 'issuances')
            PosixPath('/path/to/data/umbs/silver/issuances')

        Note:
            ``get_medallion_dir`` is now inherited from
            :class:`MortgageDataConfig` unmodified (signature
            ``get_medallion_dir(subpackage, stage)``).
        """
        stage_dirs = {
            "raw": cls.UMBS_RAW_DIR,
            "bronze": cls.UMBS_BRONZE_DIR,
            "silver": cls.UMBS_SILVER_DIR,
            "gold": cls.UMBS_GOLD_DIR,
        }
        return stage_dirs[stage] / prefix

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary directories for UMBS data processing.

        Example:
            >>> UMBSConfig.ensure_directories()
            >>> # Creates: data/umbs/raw, data/umbs/bronze, etc.
        """
        # Create medallion directories
        for d in (cls.UMBS_RAW_DIR, cls.UMBS_BRONZE_DIR, cls.UMBS_SILVER_DIR):
            d.mkdir(parents=True, exist_ok=True)


# Module-level path constants for backward compatibility
PROJECT_ROOT = UMBSConfig.PROJECT_DIR
DATA_ROOT = UMBSConfig.UMBS_DATA_DIR
UMBS_DATA = UMBSConfig.UMBS_DATA_DIR
DATA_DIR = UMBSConfig.UMBS_DATA_DIR
RAW_DIR = UMBSConfig.RAW_DIR
BRONZE_DIR = UMBSConfig.BRONZE_DIR
SILVER_DIR = UMBSConfig.SILVER_DIR


# --------------------------------------------------------------------------- #
# Per-kind silver dtype specs (close-to-final types; see core/types.py + the
# 2026-06-27 silver type audit). The four loan-level feeds (FNMA/FHLMC x ILLD
# issuance + FNMA MLLD / FHLMC FU monthly) share ONE 117-col schema (verified
# identical), so they share one spec; IS (security/pool-level) is a second spec.
# Derived data-driven from a GSE x time spread (investigations/scripts/umbs_domain_profile.py).
#
# UNLIKE the SF/FHFA files there is NO geo-as-int here (Property/Seller/Servicer
# State are USPS 2-char strings) and NO sentinel masking: the loan-level credit
# scores (9999/7777), LTV/CLTV/DTI (999) and rate (99.999/77.777) sentinels stay
# LIVE — the mbs_umbs and Optimal-Blue matchers detect them, so masking would
# break matching (same precision-first convention as the committed FNMA SF spec).
# Ints are right-sized to HOLD the sentinels. Dates (already parsed to pl.Date by
# the prior _apply_dates) move into spec.dates; the look-alike "Months to Next ...
# Date" columns are month COUNTS, not dates, and stay integers.
# --------------------------------------------------------------------------- #
_M, _MDY, _B, _S = "date_m", "date_mdy", "bool", "str"  # column-role markers
_I8, _I16, _I32, _F32, _F64 = pl.Int8, pl.Int16, pl.Int32, pl.Float32, pl.Float64


def _enum(*levels: str) -> pl.Enum:
    return pl.Enum(list(levels))


def _umbs_spec(columns: list[tuple[str, Any]]) -> SilverSpec:
    """Build a UMBS SilverSpec from an ordered ``(name, role)`` list.

    ``role`` is one of the string markers (``_M`` month-date, ``_MDY`` day-date,
    ``_B`` boolean, ``_S`` keep/identifier Utf8), a ``pl.Enum`` instance, or a
    polars numeric dtype (-> cast). The ordered list is the single source of truth
    for the per-stage maps and the target schema, so conform-to-target unifies the
    cross-month column drift across the disclosure-format vintages.
    """
    dates: dict[str, str] = {}
    enums: dict[str, pl.Enum] = {}
    bools: list[str] = []
    casts: dict[str, Any] = {}
    idents: list[str] = []
    target: dict[str, Any] = {}
    for name, role in columns:
        if isinstance(role, str):
            if role == _M:
                dates[name] = "mmyyyy"
                target[name] = pl.Date
            elif role == _MDY:
                dates[name] = "mmddyyyy"
                target[name] = pl.Date
            elif role == _B:
                bools.append(name)
                target[name] = pl.Boolean
            else:  # _S
                idents.append(name)
                target[name] = pl.String
        elif isinstance(role, pl.Enum):
            enums[name] = role
            target[name] = role
        else:
            casts[name] = role
            target[name] = role
    return SilverSpec(
        dates=dates,
        enums=enums,
        bools=tuple(bools),
        casts=casts,
        identifiers=tuple(idents),
        target=pl.Schema(target),
    )


# --- Loan-level (ILLD issuance + MLLD/FU monthly), one shared 117-col schema ---
_LLD_COLS: list[tuple[str, Any]] = [
    ("Loan Identifier", _S),
    ("Loan Correction Indicator", _enum("A", "N", "Y")),
    ("Prefix", _S),
    ("Security Identifier", _S),
    ("CUSIP", _S),
    ("Mortgage Loan Amount", _F64),
    ("Issuance Investor Loan UPB", _F64),
    ("Current Investor Loan UPB", _F64),
    ("Amortization Type", _enum("ARM", "FRM")),
    ("Original Interest Rate", _F32),
    ("Issuance Interest Rate", _F32),
    ("Current Interest Rate", _F32),
    ("Issuance Net Interest Rate", _F32),
    ("Current Net Interest Rate", _F32),
    ("First Payment Date", _M),
    ("Maturity Date", _M),
    ("Loan Term", _I16),
    ("Remaining Months to Maturity", _I16),
    ("Loan Age", _I16),
    ("Loan-To-Value (LTV)", _I16),
    ("Combined Loan-To-Value (CLTV)", _I16),
    ("Debt-To-Income (DTI)", _I16),
    ("Borrower Credit Score", _I16),
    ("Filler", _S),
    ("Filler_duplicated_0", _S),
    ("Filler_duplicated_1", _S),
    ("Number of Borrowers", _I8),
    ("First Time Home Buyer Indicator", _enum("9", "N", "Y")),
    ("Loan Purpose", _enum("9", "C", "M", "N", "P", "R")),
    ("Occupancy Status", _enum("9", "I", "P", "S")),
    ("Number of Units", _I8),
    ("Property Type", _enum("CO", "CP", "MH", "PU", "SF")),
    ("Channel", _enum("9", "B", "C", "R", "T")),
    ("Property State", _S),
    ("Seller Name", _S),
    ("Servicer Name", _S),
    ("Mortgage Insurance Percent", _F32),
    ("Mortgage Insurance Cancellation Indicator", _enum("7", "N", "Y")),
    ("Government Insured Guarantee", _S),
    ("Assumability Indicator", _B),
    ("Interest Only Loan Indicator", _B),
    ("Interest Only First Principal and Interest Payment Date", _M),
    ("Months to Amortization", _I16),
    ("Prepayment Penalty Indicator", _B),
    ("Prepayment Penalty Total Term", _S),
    ("Index", _S),
    ("Mortgage Margin", _F32),
    ("MBS PC Margin", _F32),
    ("Interest Rate Adjustment Frequency", _I8),
    ("Interest Rate Lookback", _I8),
    ("Interest Rate Rounding Method", _S),
    ("Interest Rate Rounding Method Percent", _S),
    ("Convertibility Indicator", _S),
    ("Initial Fixed Rate Period", _S),
    ("Next Interest Rate Adjustment Date", _M),
    ("Months to Next Interest Rate Adjustment Date", _I16),
    ("Life Ceiling Interest Rate", _F32),
    ("Life Ceiling Net Interest Rate", _F32),
    ("Life Floor Interest Rate", _F32),
    ("Life Floor Net Interest Rate", _F32),
    ("Initial Interest Rate Cap Up Percent", _F32),
    ("Initial Interest Rate Cap Down Percent", _F32),
    ("Periodic Interest Rate Cap Up Percent", _F32),
    ("Periodic Interest Rate Cap Down Percent", _F32),
    ("Modification Program", _S),
    ("Modification Type", _S),
    ("Number of Modifications", _I8),
    ("Total Capitalized Amount", _F64),
    ("Interest Bearing Mortgage Loan Amount", _F64),
    ("Original Deferred Amount", _F64),
    ("Current Deferred UPB", _F64),
    ("Loan Age As Of Modification", _I16),
    ("Estimated Loan-To-Value (ELTV)", _I16),
    ("Updated Credit Score", _I16),
    ("Filler_duplicated_2", _S),
    ("Interest Rate Step Indicator", _S),
    ("Initial Step Fixed-Rate Period", _S),
    ("Total Number of Steps", _I8),
    ("Number of Remaining Steps", _I8),
    ("Next Step Rate", _F32),
    ("Terminal Step Rate", _F32),
    ("Terminal Step Date", _M),
    ("Step Rate Adjustment Frequency", _I8),
    ("Next Step Rate Adjustment Date", _M),
    ("Months to Next Step Rate Adjustment Date", _I16),
    ("Periodic Step Cap Up Percent", _F32),
    ("Origination Mortgage Loan Amount", _F64),
    ("Origination Interest Rate", _F32),
    ("Origination Amortization Type", _S),
    ("Origination Interest Only Loan Indicator", _S),
    ("Origination First Payment Date", _M),
    ("Origination Maturity Date", _M),
    ("Origination Loan Term", _I16),
    ("Origination Loan-To-Value (LTV)", _I16),
    ("Origination Combined Loan-To-Value (CLTV)", _I16),
    ("Origination Debt-To-Income Ratio", _I16),
    ("Origination Credit Score", _I16),
    ("Filler_duplicated_3", _S),
    ("Filler_duplicated_4", _S),
    ("Filler_duplicated_5", _S),
    ("Origination Loan Purpose", _S),
    ("Origination Occupancy Status", _S),
    ("Origination Channel", _S),
    ("Days Delinquent", _I16),
    ("Loan Performance History", _S),
    ("Loan Participation Percent", _F32),
    ("_record_source", _S),
    ("Property Valuation Method", _enum("7", "9", "A", "C", "O", "P", "R", "W")),
    ("Alternative Delinquency Resolution", _S),
    ("Number of Alternative Delinquency Resolutions", _I8),
    ("Total Deferral Amount", _F64),
    ("Borrower Assistance Plan", _S),
    ("Seller City", _S),
    ("Seller State", _S),
    ("Servicer City", _S),
    ("Servicer State", _S),
    ("Special Eligibility Program", _S),
    ("Classic FICO", _I16),
    ("VS4", _I16),
    ("Updated Classic FICO", _I16),
    ("Origination Classic FICO", _I16),
    ("Origination VS4", _I16),
    ("Updated VS4", _I16),
]
UMBS_LLD_SILVER = _umbs_spec(_LLD_COLS)

# --- IS (security/pool-level issuance), one shared 99-col schema ---
_IS_COLS: list[tuple[str, Any]] = [
    ("Prefix", _S),
    ("Security Identifier", _S),
    ("CUSIP", _S),
    ("Security Factor Date", _M),
    ("Security Factor", _F64),
    ("Payment Delay Days", _I8),
    ("Security Data Correction Indicator", _B),
    ("Security Status Indicator", _enum("A", "C", "D")),
    ("Security Notification Indicator", _S),
    ("Security Description", _S),
    ("Issuer", _enum("FNM", "FRE")),
    ("Issue Date", _MDY),
    ("Maturity Date", _M),
    ("Updated Longest Maturity Date", _M),
    ("Issuance Investor Security UPB", _F64),
    ("Current Investor Security UPB", _F64),
    ("WA Net Interest Rate", _F32),
    ("WA Issuance Interest Rate", _F32),
    ("WA Current Interest Rate", _F32),
    ("WA Net Accrual Interest Rate", _F32),
    ("WA Loan Term", _I16),
    ("WA Issuance Remaining Months to Maturity", _I16),
    ("WA Current Remaining Months to Maturity", _I16),
    ("WA Loan Age", _I16),
    ("WA Mortgage Loan Amount", _F64),
    ("Average Mortgage Loan Amount", _F64),
    ("WA Loan-To-Value (LTV)", _I16),
    ("WA Combined Loan-To-Value (CLTV)", _I16),
    ("WA Debt-To-Income (DTI)", _I16),
    ("WA Borrower Credit Score", _I16),
    ("Filler", _S),
    ("Filler_duplicated_0", _S),
    ("Loan Count", _I32),
    ("Third Party Origination UPB Percent", _F32),
    ("Seller Name", _S),
    ("Seller City", _S),
    ("Seller State", _S),
    ("Servicer Name", _S),
    ("Servicer City", _S),
    ("Servicer State", _S),
    ("Delinquent Loans Purchased (Prior Month UPB)", _S),
    ("Delinquent Loans Purchased (Loan Count)", _S),
    ("Eligible for Resecuritization", _S),
    ("Notes", _S),
    ("Notes Ongoing", _S),
    ("Interest Only Security Indicator", _S),
    ("WA Months to Amortization", _I16),
    ("Prepayment Penalty Indicator", _S),
    ("Reduced Minimum Servicing Indicator", _B),
    ("Subtype", _S),
    ("Index", _S),
    ("WA Mortgage Margin", _F32),
    ("WA MBS PC Margin", _F32),
    ("Interest Rate Adjustment Frequency", _I16),
    ("Interest Rate Lookback", _I16),
    ("Payment Adjustment Frequency", _I16),
    ("Payment Lookback", _I16),
    ("Convertibility Indicator", _S),
    ("Negative Amortization Indicator", _S),
    ("Negative Amortization Factor", _F64),
    ("WA Negative Amortization Limit", _F64),
    ("Initial Fixed Rate Period", _S),
    ("First Rate Adjustment Date", _M),
    ("First Payment Adjustment Date", _M),
    ("WA Months to Next Rate Adjustment Date", _I16),
    ("WA Life Interest Rate Ceiling", _F32),
    ("WA Net Life Interest Rate Ceiling", _F32),
    ("WA Life Interest Rate Floor", _F32),
    ("WA Net Life Interest Rate Floor", _F32),
    ("Initial Interest Rate Cap Up %", _F32),
    ("Initial Interest Rate Cap Down %", _F32),
    ("Periodic Interest Rate Cap Up %", _F32),
    ("Periodic Interest Rate Cap Down %", _F32),
    ("Initial Step Fixed-Rate Period", _S),
    ("Step Rate Adjustment Frequency", _I8),
    ("Next Step Rate Adjustment Date", _M),
    ("WA Months to Next Step Rate Adjustment", _I16),
    ("Periodic Step Rate Cap Up %", _F32),
    ("WA Origination Mortgage Loan Amount", _F64),
    ("Average Origination Mortgage Loan Amount", _F64),
    ("WA Origination Interest Rate", _F32),
    ("WA Origination Loan Term", _I16),
    ("WA Origination Loan-To-Value (LTV)", _I16),
    ("WA Origination Combined Loan-To-Value (CLTV)", _I16),
    ("WA Origination Debt-To-Income (DTI)", _I16),
    ("WA Origination Credit Score", _I16),
    ("Filler_duplicated_1", _S),
    ("Filler_duplicated_2", _S),
    ("Origination Third Party Origination UPB Percent", _F32),
    ("WA Estimated Loan-To-Value (ELTV)", _I16),
    ("WA Updated Credit Score", _I16),
    ("_record_source", _S),
    ("WA Classic FICO", _I16),
    ("WA VS4", _I16),
    ("Involuntary Loan Removal (Prior Month UPB)", _F64),
    ("Involuntary Loan Removal (Loan Count)", _I32),
    ("WA Origination Classic FICO", _I16),
    ("WA Origination VS4", _I16),
    ("WA Updated Classic FICO", _I16),
    ("Mission Density Score", _F32),
    ("Mission Criteria Share", _F32),
    ("Green Indicator", _B),
    ("Social Indicator", _B),
    # Social Density Score / Social Criteria Share appear only in a 2023-2024 band
    # of IS months (parallel to the Mission pair); absent months are null-filled.
    ("Social Density Score", _F32),
    ("Social Criteria Share", _F32),
    ("Mission Index Version", _S),
    ("WA Updated VS4", _I16),
]
UMBS_IS_SILVER = _umbs_spec(_IS_COLS)

# --- GN_MEGA (Ginnie-Mae-backed Fannie Megas, security-level, monthly) -------
# Stable 15-col schema across all vintages. Dates verified on live bronze:
# ``Issue Date`` is day-granular ``mmddyyyy``; ``Maturity Date`` and
# ``Scheduled Distribution Date`` are month-level ``mmyyyy``.
_GN_MEGA_COLS: list[tuple[str, Any]] = [
    ("Security Identifier", _S),
    ("Issue Date", _MDY),
    ("Issuance Investor  Security UPB", _F64),  # sic: double space in the source header
    ("WA Current Interest Rate", _F32),
    ("WA Current Remaining Months to Maturity", _I16),
    ("Prefix", _S),
    ("Product", _S),
    ("CUSIP", _S),
    ("WA Net Interest Rate", _F32),
    ("Maturity Date", _M),
    ("Security Factor", _F64),
    ("WA Loan Age", _I16),
    ("Scheduled Distribution Date", _M),
    ("WA Issuance Interest Rate", _F32),
    ("WA Issuance Remaining Months to Maturity", _I16),
    ("_record_source", _S),
]
UMBS_GN_MEGA_SILVER = _umbs_spec(_GN_MEGA_COLS)

# --- MF (security-level Monthly Factor) --------------------------------------
# The monthly-factor twin of IS: same security-level schema family, so roles
# mirror ``_IS_COLS``. Cross-vintage drift (91 -> 106 cols) is conformed to this
# union: the Dec-2025 VS4 split (``WA Borrower Credit Score`` -> ``WA Classic
# FICO`` + ``WA VS4``), the ``Delinquent Loans Purchased`` -> ``Involuntary Loan
# Removal`` rename, and the ``Social`` -> ``Mission`` score pair all keep BOTH
# names (null-filled where absent), as IS does. Deviations from IS, verified on
# live bronze: ``Security Status Indicator`` is A/P here (not IS's A/C/D) so it
# stays Utf8; the Delinquent/Involuntary columns are real numerics here (IS
# carries them as text). Dates: ``Security Factor Date`` and every adjustment/
# maturity date are ``mmyyyy``; only ``Issue Date`` is day-granular ``mmddyyyy``.
_MF_COLS: list[tuple[str, Any]] = [
    ("Prefix", _S),
    ("Security Identifier", _S),
    ("CUSIP", _S),
    ("Security Factor Date", _M),
    ("Security Factor", _F64),
    ("Payment Delay Days", _I8),
    ("Security Data Correction Indicator", _B),
    ("Security Status Indicator", _S),
    ("Security Notification Indicator", _S),
    ("Security Description", _S),
    ("Issuer", _enum("FNM", "FRE")),
    ("Issue Date", _MDY),
    ("Maturity Date", _M),
    ("Updated Longest Maturity Date", _M),
    ("Issuance Investor Security UPB", _F64),
    ("Current Investor Security UPB", _F64),
    ("WA Net Interest Rate", _F32),
    ("WA Issuance Interest Rate", _F32),
    ("WA Current Interest Rate", _F32),
    ("WA Net Accrual Interest Rate", _F32),
    ("WA Loan Term", _I16),
    ("WA Issuance Remaining Months to Maturity", _I16),
    ("WA Current Remaining Months to Maturity", _I16),
    ("WA Loan Age", _I16),
    ("WA Mortgage Loan Amount", _F64),
    ("Average Mortgage Loan Amount", _F64),
    ("WA Loan-To-Value (LTV)", _I16),
    ("WA Combined Loan-To-Value (CLTV)", _I16),
    ("WA Debt-To-Income (DTI)", _I16),
    ("WA Classic FICO", _I16),
    ("Filler", _S),
    ("WA VS4", _I16),
    ("Loan Count", _I32),
    ("Third Party Origination UPB Percent", _F32),
    ("Seller Name", _S),
    ("Seller City", _S),
    ("Seller State", _S),
    ("Servicer Name", _S),
    ("Servicer City", _S),
    ("Servicer State", _S),
    ("Involuntary Loan Removal (Prior Month UPB)", _F64),
    ("Involuntary Loan Removal (Loan Count)", _I32),
    ("Eligible for Resecuritization", _S),
    ("Notes", _S),
    ("Notes Ongoing", _S),
    ("Interest Only Security Indicator", _S),
    ("WA Months to Amortization", _I16),
    ("Prepayment Penalty Indicator", _S),
    ("Reduced Minimum Servicing Indicator", _B),
    ("Subtype", _S),
    ("Index", _S),
    ("WA Mortgage Margin", _F32),
    ("WA MBS PC Margin", _F32),
    ("Interest Rate Adjustment Frequency", _I16),
    ("Interest Rate Lookback", _I16),
    ("Payment Adjustment Frequency", _I16),
    ("Payment Lookback", _I16),
    ("Convertibility Indicator", _S),
    ("Negative Amortization Indicator", _S),
    ("Negative Amortization Factor", _F64),
    ("WA Negative Amortization Limit", _F64),
    ("Initial Fixed Rate Period", _S),
    ("First Rate Adjustment Date", _M),
    ("First Payment Adjustment Date", _M),
    ("WA Months to Next Rate Adjustment Date", _I16),
    ("WA Life Interest Rate Ceiling", _F32),
    ("WA Net Life Interest Rate Ceiling", _F32),
    ("WA Life Interest Rate Floor", _F32),
    ("WA Net Life Interest Rate Floor", _F32),
    ("Initial Interest Rate Cap Up %", _F32),
    ("Initial Interest Rate Cap Down %", _F32),
    ("Periodic Interest Rate Cap Up %", _F32),
    ("Periodic Interest Rate Cap Down %", _F32),
    ("Initial Step Fixed-Rate Period", _S),
    ("Step Rate Adjustment Frequency", _I8),
    ("Next Step Rate Adjustment Date", _M),
    ("WA Months to Next Step Rate Adjustment", _I16),
    ("Periodic Step Rate Cap Up %", _F32),
    ("WA Origination Mortgage Loan Amount", _F64),
    ("Average Origination Mortgage Loan Amount", _F64),
    ("WA Origination Interest Rate", _F32),
    ("WA Origination Loan Term", _I16),
    ("WA Origination Loan-To-Value (LTV)", _I16),
    ("WA Origination Combined Loan-To-Value (CLTV)", _I16),
    ("WA Origination Debt-To-Income (DTI)", _I16),
    ("WA Origination Classic FICO", _I16),
    ("Filler_duplicated_0", _S),
    ("WA Origination VS4", _I16),
    ("Origination Third Party Origination UPB Percent", _F32),
    ("WA Estimated Loan-To-Value (ELTV)", _I16),
    ("WA Updated Classic FICO", _I16),
    ("Mission Density Score", _F32),
    ("Mission Criteria Share", _F32),
    ("Green Indicator", _B),
    ("Social Indicator", _B),
    ("Mission Index Version", _S),
    ("Filler_duplicated_1", _S),
    ("WA Updated VS4", _I16),
    # Pre-rename / dropped columns from earlier vintages (null-filled when absent):
    ("WA Borrower Credit Score", _I16),
    ("Delinquent Loans Purchased (Prior Month UPB)", _F64),
    ("Delinquent Loans Purchased (Loan Count)", _I32),
    ("WA Origination Credit Score", _I16),
    ("Filler_duplicated_2", _S),
    ("WA Updated Credit Score", _I16),
    ("Social Density Score", _F32),
    ("Social Criteria Share", _F32),
    ("_record_source", _S),
]
UMBS_MF_SILVER = _umbs_spec(_MF_COLS)

#: Silver kind -> its spec. The loan-level kinds (ILLD/MLLD/FU) share one spec.
UMBS_SILVER_SPECS: dict[str, SilverSpec] = {
    "ILLD": UMBS_LLD_SILVER,
    "MLLD": UMBS_LLD_SILVER,
    "FU": UMBS_LLD_SILVER,
    "IS": UMBS_IS_SILVER,
    "GN_MEGA": UMBS_GN_MEGA_SILVER,
    "MF": UMBS_MF_SILVER,
}
