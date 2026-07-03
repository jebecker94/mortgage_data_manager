"""FHA Data Manager configuration.

This module provides configuration management for the FHA subpackage,
inheriting from the base MortgageDataConfig.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.types import SilverSpec


class FHAConfig(MortgageDataConfig):
    """FHA-specific configuration, inheriting from base MortgageDataConfig."""

    # FHA data directories
    FHA_DATA_DIR: Path = Path(
        config(
            "FHA_DATA_DIR",
            default=str(MortgageDataConfig.get_subpackage_data_dir("fha")),
        )
    )
    FHA_RAW_DIR: Path = Path(config("FHA_RAW_DIR", default=str(FHA_DATA_DIR / "raw")))
    FHA_BRONZE_DIR: Path = Path(config("FHA_BRONZE_DIR", default=str(FHA_DATA_DIR / "bronze")))
    FHA_SILVER_DIR: Path = Path(config("FHA_SILVER_DIR", default=str(FHA_DATA_DIR / "silver")))
    FHA_METADATA_DIR: Path = Path(
        config("FHA_METADATA_DIR", default=str(FHA_DATA_DIR / "metadata"))
    )

    # Dataset names
    SINGLE_FAMILY = "single_family"
    HECM = "hecm"

    # FHA Index column name
    FHA_INDEX_COLUMN = "FHA_Index"

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary FHA directories if they don't exist."""
        required_paths = [
            cls.FHA_RAW_DIR,
            cls.FHA_BRONZE_DIR,
            cls.FHA_SILVER_DIR,
            cls.FHA_METADATA_DIR,
        ]
        for dir_path in required_paths:
            dir_path.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# Silver schema specs (close-to-final dtypes; see core/types.py + the
# 2026-06-27 silver type audit). Stable code sets are pl.Enum; geographic codes
# are zero-padded Utf8; agency/institution numbers are Utf8 identifiers.
# --------------------------------------------------------------------------- #

# Single-family categorical level sets (canonical values).
_SF_DOWN_PAYMENT_SOURCE = pl.Enum(["Borrower", "Relative", "Gov Asst", "Non Profit", "Employer"])
_SF_LOAN_PURPOSE = pl.Enum(["Purchase", "Refi_FHA", "Refi_Conv_Curr"])
_SF_PROPERTY_TYPE = pl.Enum(["Single Family", "Condo", "Rehabilitation", "H4H", "Other"])
_SF_PRODUCT_TYPE = pl.Enum(["Fixed Rate", "Adjustable Rate"])

# HECM categorical level sets (Rate Type levels assume the "Fixed" -> "Fixed Rate"
# normalization applied in import_silver before the enum cast).
_HECM_TYPE = pl.Enum(
    ["Hecm", "Hecm Traditional", "Hecm for Purchase", "Hecm for Refinance", "Standard"]
)
_HECM_RATE_TYPE = pl.Enum(
    ["Adjustable", "Annual Adjustable", "Fixed Rate", "Monthly Adjustable", "Other"]
)
_HECM_REFINANCE_TYPE = pl.Enum(["Hecm", "Purchase", "Refinance", "Standard"])
_HECM_STANDARD_SAVER = pl.Enum(["Hecm New", "Saver", "Standard", "Other"])
_HECM_PURCHASE_REFINANCE = pl.Enum(["Purchase", "Refinance"])

SINGLE_FAMILY_SILVER = SilverSpec(
    zip5=("Property Zip",),
    dates={"Date": "from_datetime"},
    enums={
        "Down Payment Source": _SF_DOWN_PAYMENT_SOURCE,
        "Loan Purpose": _SF_LOAN_PURPOSE,
        "Property Type": _SF_PROPERTY_TYPE,
        "Product Type": _SF_PRODUCT_TYPE,
    },
    casts={
        "Interest Rate": pl.Float32,
        "Mortgage Amount": pl.Int32,
        "Year": pl.Int16,
        "Month": pl.Int16,
    },
    identifiers=("Originating Mortgagee Number", "Sponsor Number", "Non Profit Number"),
    target=pl.Schema(
        {
            "Property State": pl.String,
            "Property City": pl.String,
            "Property County": pl.String,
            "Property Zip": pl.String,
            "Originating Mortgagee": pl.String,
            "Originating Mortgagee Number": pl.String,
            "Sponsor Name": pl.String,
            "Sponsor Number": pl.String,
            "Down Payment Source": _SF_DOWN_PAYMENT_SOURCE,
            "Non Profit Number": pl.String,
            "Product Type": _SF_PRODUCT_TYPE,
            "Loan Purpose": _SF_LOAN_PURPOSE,
            "Property Type": _SF_PROPERTY_TYPE,
            "Interest Rate": pl.Float32,
            "Mortgage Amount": pl.Int32,
            "Year": pl.Int16,
            "Month": pl.Int16,
            "FHA_Index": pl.String,
            "FIPS": pl.String,
            "Date": pl.Date,
        }
    ),
)

HECM_SILVER = SilverSpec(
    zip5=("Property Zip",),
    dates={"Date": "from_datetime"},
    sentinels={"Previous Servicer ID": (0,)},  # 0 == no previous servicer
    enums={
        "HECM Type": _HECM_TYPE,
        "Rate Type": _HECM_RATE_TYPE,
        "RefinanceType": _HECM_REFINANCE_TYPE,
        "Standard/Saver": _HECM_STANDARD_SAVER,
        "Purchase/Refinance": _HECM_PURCHASE_REFINANCE,
    },
    casts={
        "Interest Rate": pl.Float32,
        "NMLS": pl.Int32,
        "Year": pl.Int16,
        "Month": pl.Int16,
    },
    identifiers=(
        "Originating Mortgagee Number",
        "Sponsor Number",
        "Current Servicer ID",
        "Previous Servicer ID",
    ),
    target=pl.Schema(
        {
            "FHA_Index": pl.String,
            "Property State": pl.String,
            "Property County": pl.String,
            "Property City": pl.String,
            "Property Zip": pl.String,
            "Originating Mortgagee": pl.String,
            "Originating Mortgagee Number": pl.String,
            "Sponsor Name": pl.String,
            "Sponsor Number": pl.String,
            "Sponsor Originator": pl.String,
            "Current Servicer ID": pl.String,
            "Previous Servicer ID": pl.String,
            "HECM Type": _HECM_TYPE,
            "Rate Type": _HECM_RATE_TYPE,
            "Interest Rate": pl.Float32,
            "Initial Principal Limit": pl.Float64,
            "Maximum Claim Amount": pl.Float64,
            "Year": pl.Int16,
            "Month": pl.Int16,
            "FIPS": pl.String,
            "Date": pl.Date,
            "NMLS": pl.Int32,
            "RefinanceType": _HECM_REFINANCE_TYPE,
            "Standard/Saver": _HECM_STANDARD_SAVER,
            "Purchase/Refinance": _HECM_PURCHASE_REFINANCE,
        }
    ),
)
