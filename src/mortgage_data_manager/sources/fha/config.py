"""Configuration for FHA (Federal Housing Administration) data.

This module provides configuration management for the FHA subpackage,
inheriting from the base MortgageDataConfig.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal

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
    #: The silver *root*; the per-dataset trees are ``single_family/`` and ``hecm/``
    #: beneath it. This class owns the ``FHA_SILVER_DIR`` env key and defines its
    #: meaning -- consumers that want one dataset read
    #: ``FHAConfig.FHA_SILVER_DIR / "single_family"`` (the matching configs do)
    #: rather than re-reading the key with a deeper default.
    FHA_SILVER_DIR: Path = Path(config("FHA_SILVER_DIR", default=str(FHA_DATA_DIR / "silver")))

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary FHA directories if they don't exist."""
        required_paths = [
            cls.FHA_RAW_DIR,
            cls.FHA_BRONZE_DIR,
            cls.FHA_SILVER_DIR,
        ]
        for dir_path in required_paths:
            dir_path.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# Dataset registry
# --------------------------------------------------------------------------- #

#: The two FHA snapshot datasets, spelled as they appear on disk. Shared by the
#: bronze and silver builders so the two layers cannot drift apart.
type SnapshotType = Literal["single_family", "hecm"]

#: CLI-facing dataset name -> snapshot type, which doubles as the directory name
#: used under each medallion layer root (``raw/hecm``, ``silver/single_family``).
DATASET_MAP: Final[dict[str, SnapshotType]] = {
    "single-family": "single_family",
    "hecm": "hecm",
}

VALID_DATASETS: Final[list[str]] = list(DATASET_MAP)


def resolve_datasets(datasets: list[str] | None) -> list[str]:
    """Default to all FHA datasets; validate any explicit subset.

    Args:
        datasets: Dataset identifiers, or ``None`` for all of `VALID_DATASETS`.

    Returns:
        Validated dataset identifiers, in registry order.

    Raises:
        ValueError: If an unknown identifier is given.
    """
    if datasets is None:
        return list(VALID_DATASETS)
    unknown = [d for d in datasets if d not in DATASET_MAP]
    if unknown:
        raise ValueError(f"Unknown FHA datasets: {unknown}; valid: {VALID_DATASETS}")
    return [d for d in VALID_DATASETS if d in set(datasets)]


# --------------------------------------------------------------------------- #
# Silver schema specs (close-to-final dtypes; see core/types.py). Stable code sets are pl.Enum;
# geographic codes are zero-padded Utf8; agency/institution numbers are Utf8 identifiers.
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

_SF_TARGET_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
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
    "Mortgage Amount": pl.Int64,
    "Year": pl.Int16,
    "Month": pl.Int16,
    "FHA_Index": pl.String,
    "FIPS": pl.String,
    "state_fips": pl.String,
    "Date": pl.Date,
}

_HECM_TARGET_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
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
    "state_fips": pl.String,
    "Date": pl.Date,
    "NMLS": pl.Int32,
    "RefinanceType": _HECM_REFINANCE_TYPE,
    "Standard/Saver": _HECM_STANDARD_SAVER,
    "Purchase/Refinance": _HECM_PURCHASE_REFINANCE,
}

# Canonical zero-pad widths for the FHA identifier columns. The two mortgagee
# columns are HUD's 5-digit mortgagee ID -- the same identifier whose padded form
# lender_list.py derives as mort_id[:5], and which bronze's Int32 round-trip
# strips the leading zeros from. "Non Profit Number" is a *different namespace*:
# its observed lengths are exclusively 8 and 9, i.e. an EIN, so its canonical
# width is 9 and padding it to 5 would be a no-op that leaves the 8-char values
# still stripped.
_MORTGAGEE_ID_WIDTH = 5
_EIN_WIDTH = 9

SINGLE_FAMILY_SILVER = SilverSpec(
    zip5=("Property Zip",),
    dates={"Date": "from_datetime"},
    fips={"state_fips": 2},
    enums={
        "Down Payment Source": _SF_DOWN_PAYMENT_SOURCE,
        "Loan Purpose": _SF_LOAN_PURPOSE,
        "Property Type": _SF_PROPERTY_TYPE,
        "Product Type": _SF_PRODUCT_TYPE,
    },
    casts={
        "Interest Rate": pl.Float32,
        "Mortgage Amount": pl.Int64,
        "Year": pl.Int16,
        "Month": pl.Int16,
    },
    identifiers_padded={
        "Originating Mortgagee Number": _MORTGAGEE_ID_WIDTH,
        "Sponsor Number": _MORTGAGEE_ID_WIDTH,
        "Non Profit Number": _EIN_WIDTH,
    },
    target=pl.Schema(_SF_TARGET_SCHEMA),
    # Every SF target column is present in all 194 bronze vintages (audited
    # 2026-08-31), so any conform-to-target fill is an upstream rename/drop
    # and fails the build instead of shipping a silently all-null column.
    optional_target=(),
)

HECM_SILVER = SilverSpec(
    zip5=("Property Zip",),
    dates={"Date": "from_datetime"},
    fips={"state_fips": 2},
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
    # Servicer IDs stay unpadded. Their observed widths run 6-10 characters; the
    # canonical form is the 10-digit one (a 5-digit HUD mortgagee number plus a
    # 5-digit branch) and the shorter values are its leading-zero-stripped form,
    # so padding them to 10 would be defensible -- it is just not done here,
    # because it would rewrite values consumers already hold.
    identifiers=("Current Servicer ID", "Previous Servicer ID"),
    identifiers_padded={
        "Originating Mortgagee Number": _MORTGAGEE_ID_WIDTH,
        "Sponsor Number": _MORTGAGEE_ID_WIDTH,
    },
    target=pl.Schema(_HECM_TARGET_SCHEMA),
    # The HECM layout genuinely churned across eras — these eight columns are
    # absent from between 15 and 139 of the 162 bronze vintages (audited
    # 2026-08-31). Anything else absent is an upstream rename/drop and fatal.
    optional_target=(
        "Current Servicer ID",
        "HECM Type",
        "NMLS",
        "Previous Servicer ID",
        "Purchase/Refinance",
        "RefinanceType",
        "Sponsor Originator",
        "Standard/Saver",
    ),
)
