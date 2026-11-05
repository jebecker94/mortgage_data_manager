"""Configuration management for HMDA Data Manager.

This module handles path configuration and HMDA-specific constants,
inheriting from the base MortgageDataConfig.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Final, Literal, cast

import polars as pl
from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.types import (
    TEXT,
    ColumnRole,
    SilverSpec,
    spec_from_columns,
)


class HMDAConfig(MortgageDataConfig):
    """HMDA-specific configuration.

    Inherits base configuration from MortgageDataConfig and adds
    HMDA-specific paths and constants.
    """

    # HMDA data directories
    HMDA_DATA_DIR: Path = Path(
        config("HMDA_DATA_DIR", default=str(MortgageDataConfig.get_subpackage_data_dir("hmda")))
    )
    HMDA_RAW_DIR: Path = Path(config("HMDA_RAW_DIR", default=str(HMDA_DATA_DIR / "raw")))
    HMDA_BRONZE_DIR: Path = Path(config("HMDA_BRONZE_DIR", default=str(HMDA_DATA_DIR / "bronze")))
    HMDA_SILVER_DIR: Path = Path(config("HMDA_SILVER_DIR", default=str(HMDA_DATA_DIR / "silver")))
    #: Latest panel name per LEI, derived from the post-2018 panel silver. A sibling of
    #: the dataset folders, so no era scan of ``panel/`` ever picks it up.
    HMDA_LEI_NAME_FILE: Path = HMDA_SILVER_DIR / "lender_names" / "lei_latest_name.parquet"

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary HMDA directories if they don't exist."""
        for d in (cls.HMDA_RAW_DIR, cls.HMDA_BRONZE_DIR, cls.HMDA_SILVER_DIR):
            d.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_dataset_dir(
        cls,
        stage: Literal["bronze", "silver"],
        dataset: HMDADataset,
        period: HMDAPeriod | None = None,
        *,
        strict: bool = True,
    ) -> Path:
        """Return the directory for a given HMDA stage/dataset(/period).

        HMDA medallion data is organized along extra axes (dataset family and,
        in silver, era period) beyond the base layout, so this helper
        complements the inherited base ``get_medallion_dir(subpackage, stage)``
        rather than overriding it.

        **Bronze is flat**: one directory per dataset holding every era's
        parquet (``bronze/loans/*.parquet``). The era is already recoverable
        from each filename's year, so it is not a path segment. Silver keeps
        the era split because the three eras carry genuinely different schemas
        (38 / 47 / 99 columns, 30 shared) and different code spaces
        (``loan_purpose`` is 1-3 pre-2018, 1/2/4/5/31/32 after), so a single
        silver root would let an era-specific filter silently return no rows.

        Args:
            stage: Medallion layer.
            dataset: Dataset family.
            period: Era subfolder. Required for silver; must be omitted (or
                passed with ``strict=False``) for the flat bronze layer.
            strict: When False, a ``period`` passed for bronze is ignored
                instead of raising. For callers forwarding a period they do not
                control.

        Returns:
            The target directory path.

        Raises:
            ValueError: If ``period`` is given for bronze while ``strict``, or
                omitted for silver.
        """
        if stage == "bronze":
            if period is not None and strict:
                raise ValueError(
                    "HMDA bronze is flat (bronze/<dataset>/); it takes no period "
                    f"subfolder, but got {period!r}. Pass strict=False to ignore."
                )
            return cls.HMDA_BRONZE_DIR / dataset
        if period is None:
            raise ValueError("period is required for the era-split HMDA silver layer")
        return cls.HMDA_SILVER_DIR / dataset / period


#: Name of the synthesized row identifier carried from bronze into every silver table.
HMDA_INDEX_COLUMN: Final = "HMDAIndex"


# ============================================================================
# Dataset registry
# ============================================================================

#: One HMDA dataset family identifier — the literal type shared by the registry
#: below, ``resolve_datasets``, and the per-era bronze/silver builders.
type HMDADataset = Literal["loans", "panel", "transmittal_series"]

#: One silver era subfolder. Silver keeps the era split because the three eras'
#: schemas and code spaces differ (see `HMDAConfig.get_dataset_dir`).
type HMDAPeriod = Literal["pre2007", "period_2007_2017", "post2018"]

#: The three HMDA dataset families, and what each holds. Single source of truth
#: for the ``--datasets/-t`` choices and for the default set.
#:
#: Which datasets a given era has *built* is deliberately not encoded here — it
#: is build state, not a property of the source, and putting it in config would
#: go stale as coverage fills in.
DATASETS: Final[dict[HMDADataset, str]] = {
    "loans": "Loan Application Register — one row per application/origination.",
    "panel": "Reporter panel — the institution roster for a filing year.",
    "transmittal_series": "Transmittal sheet — per-institution filing metadata.",
}

#: Sorted list view (single source: `DATASETS`).
VALID_DATASETS: Final[list[HMDADataset]] = list(DATASETS)


def resolve_datasets(datasets: Sequence[str] | None) -> list[HMDADataset]:
    """Default to every dataset; validate any explicit subset.

    Args:
        datasets: Dataset identifiers, or ``None`` for all of `VALID_DATASETS`.

    Returns:
        Validated list of dataset identifiers.

    Raises:
        ValueError: If an unknown dataset identifier is given.
    """
    if datasets is None:
        return list(VALID_DATASETS)
    unknown = [d for d in datasets if d not in DATASETS]
    if unknown:
        raise ValueError(f"Unknown HMDA datasets: {unknown}; valid: {VALID_DATASETS}")
    return cast(list[HMDADataset], list(datasets))


# ============================================================================
# Download registry
# ============================================================================

#: One record type as CFPB publishes it. A superset of `HMDADataset` by
#: ``msamd`` — the MSA/MD description files are downloaded and kept in the raw
#: layer but have no bronze builder, so they are selectable on ``download`` and
#: nowhere else. The names double as the ``raw/`` subfolder names.
type HMDARecordType = Literal["loans", "panel", "transmittal_series", "msamd"]

#: One CFPB publication product. Each is a distinct release of the same record
#: types on its own schedule, not a different kind of data.
type HMDAProduct = Literal["snapshot", "one_year", "three_year", "historic", "mlar"]

#: The four downloadable record types. Single source for the ``--datasets/-t``
#: choices on ``hmda download``.
DOWNLOAD_DATASETS: Final[dict[HMDARecordType, str]] = {
    "loans": "Loan Application Register — one row per application/origination.",
    "panel": "Reporter panel — the institution roster for a filing year.",
    "transmittal_series": "Transmittal sheet — per-institution filing metadata.",
    "msamd": "MSA/MD descriptions — geography lookup for the filing year.",
}

#: Sorted list view (single source: `DOWNLOAD_DATASETS`).
VALID_DOWNLOAD_DATASETS: Final[list[HMDARecordType]] = list(DOWNLOAD_DATASETS)

#: The five publication products, and what each is. Single source for the
#: ``--products`` choices on ``hmda download``.
PRODUCTS: Final[dict[HMDAProduct, str]] = {
    "snapshot": "Annual snapshot — the year's data as of its March freeze.",
    "one_year": "One-year refresh — resubmissions through the following year.",
    "three_year": "Three-year refresh — the final restatement of a filing year.",
    "historic": "Pre-2018 archive (2007-2017) on files.consumerfinance.gov.",
    "mlar": "Combined Modified LAR — all filers' modified records in one file.",
}

#: Sorted list view (single source: `PRODUCTS`).
VALID_PRODUCTS: Final[list[HMDAProduct]] = list(PRODUCTS)

#: Products fetched when ``--products`` is not given. ``mlar`` is excluded: it
#: restates records already in ``snapshot`` with the public redactions applied,
#: so it is opt-in rather than part of a default pull.
DEFAULT_PRODUCTS: Final[list[HMDAProduct]] = ["snapshot", "one_year", "three_year", "historic"]


def resolve_products(products: Sequence[str] | None) -> list[HMDAProduct]:
    """Default to `DEFAULT_PRODUCTS`; validate any explicit subset.

    Args:
        products: Product identifiers, or ``None`` for `DEFAULT_PRODUCTS`.

    Returns:
        Validated list of product identifiers.

    Raises:
        ValueError: If an unknown product identifier is given.
    """
    if products is None:
        return list(DEFAULT_PRODUCTS)
    unknown = [p for p in products if p not in PRODUCTS]
    if unknown:
        raise ValueError(f"Unknown HMDA products: {unknown}; valid: {VALID_PRODUCTS}")
    return cast(list[HMDAProduct], list(products))


def resolve_download_datasets(datasets: Sequence[str] | None) -> list[HMDARecordType]:
    """Default to every record type; validate any explicit subset.

    Args:
        datasets: Record type identifiers, or ``None`` for all of
            `VALID_DOWNLOAD_DATASETS`.

    Returns:
        Validated list of record type identifiers.

    Raises:
        ValueError: If an unknown record type identifier is given.
    """
    if datasets is None:
        return list(VALID_DOWNLOAD_DATASETS)
    unknown = [d for d in datasets if d not in DOWNLOAD_DATASETS]
    if unknown:
        raise ValueError(f"Unknown HMDA record types: {unknown}; valid: {VALID_DOWNLOAD_DATASETS}")
    return cast(list[HMDARecordType], list(datasets))


# ============================================================================
# Post-2018 Data Constants
# ============================================================================

# Derived columns that are dropped in bronze layer
DERIVED_COLUMNS: list[str] = [
    "derived_loan_product_type",
    "derived_race",
    "derived_ethnicity",
    "derived_sex",
    "derived_dwelling_category",
]


# Float columns for post-2018 data
POST2018_FLOAT_COLUMNS: list[str] = [
    "interest_rate",
    "combined_loan_to_value_ratio",
    "rate_spread",
    "total_loan_costs",
    "total_points_and_fees",
    "origination_charges",
    "discount_points",
    "lender_credits",
    "tract_minority_population_percent",
    "ffiec_msa_md_median_family_income",
    "tract_to_msa_income_percentage",
]

# Integer columns for post-2018 data
POST2018_INTEGER_COLUMNS: list[str] = [
    "activity_year",
    "loan_type",
    "loan_purpose",
    "occupancy_type",
    "loan_amount",
    "action_taken",
    "msa_md",
    "loan_term",
    "derived_msa_md",
    "applicant_race_1",
    "applicant_race_2",
    "applicant_race_3",
    "applicant_race_4",
    "applicant_race_5",
    "co_applicant_race_1",
    "co_applicant_race_2",
    "co_applicant_race_3",
    "co_applicant_race_4",
    "co_applicant_race_5",
    "applicant_ethnicity_1",
    "applicant_ethnicity_2",
    "applicant_ethnicity_3",
    "applicant_ethnicity_4",
    "applicant_ethnicity_5",
    "co_applicant_ethnicity_1",
    "co_applicant_ethnicity_2",
    "co_applicant_ethnicity_3",
    "co_applicant_ethnicity_4",
    "co_applicant_ethnicity_5",
    "applicant_sex",
    "co_applicant_sex",
    "income",
    "multifamily_affordable_units",
    "property_value",
    "prepayment_penalty_term",
    "intro_rate_period",
    "purchaser_type",
    "submission_of_application",
    "initially_payable_to_institution",
    "preapproval",
    "lien_status",
    "reverse_mortgage",
    "open_end_line_of_credit",
    "business_or_commercial_purpose",
    "hoepa_status",
    "negative_amortization",
    "interest_only_payment",
    "balloon_payment",
    "other_nonamortizing_features",
    "construction_method",
    "manufactured_home_secured_property_type",
    "manufactured_home_land_property_interest",
    "applicant_credit_score_type",
    "co_applicant_credit_score_type",
    "applicant_race_observed",
    "co_applicant_race_observed",
    "applicant_ethnicity_observed",
    "co_applicant_ethnicity_observed",
    "applicant_sex_observed",
    "co_applicant_sex_observed",
    "aus_1",
    "aus_2",
    "aus_3",
    "aus_4",
    "aus_5",
    "denial_reason_1",
    "denial_reason_2",
    "denial_reason_3",
    "denial_reason_4",
    "tract_population",
    "tract_owner_occupied_units",
    "tract_one_to_four_family_homes",
    "tract_median_age_of_housing_units",
    "conforming_loan_limit",
    "agency_code",
    "assets",
    "other_lender_code",
    "lar_count",
    "calendar_quarter",
]

# Columns that may contain "Exempt" values requiring special handling
POST2018_EXEMPT_COLUMNS: list[str] = [
    "combined_loan_to_value_ratio",
    "interest_rate",
    "rate_spread",
    "loan_term",
    "prepayment_penalty_term",
    "intro_rate_period",
    "income",
    "multifamily_affordable_units",
    "property_value",
    "total_loan_costs",
    "total_points_and_fees",
    "origination_charges",
    "discount_points",
    "lender_credits",
]

#: Post-2018 coded fields whose partial-exemption answer is the code 1111 (FIG Table 2).
POST2018_EXEMPT_CODED_COLUMNS: list[str] = [
    "reverse_mortgage",
    "open_end_line_of_credit",
    "business_or_commercial_purpose",
    "negative_amortization",
    "interest_only_payment",
    "balloon_payment",
    "other_nonamortizing_features",
    "manufactured_home_secured_property_type",
    "manufactured_home_land_property_interest",
    "applicant_credit_score_type",
    "co_applicant_credit_score_type",
    "submission_of_application",
    "initially_payable_to_institution",
    "aus_1",
    "denial_reason_1",
]

#: Silver code -> null, per era: the codes that stand for an absent value, as silver
#: stores them. Applied by `sources.hmda.load.null_sentinel_codes`; silver keeps them
#: live so a consumer can still tell "exempt" from "not reported".
#:
#: - post-2018 numeric fields: ``Exempt`` (silver writes -99999), plus the
#:   conforming-loan-limit ``U``/``NA`` that silver also writes as -99999.
#: - post-2018 coded fields: 1111 = Exempt.
#: - age: 8888 = not applicable (the applicant is not a natural person), 9999 = no
#:   co-applicant (documented for the co-applicant only; 2018-2021 filers also put it in
#:   ``applicant_age``, 42-916 rows per release, where it is not an age either). Neither
#:   is an age bin.
#: - pre-2018 income: Applicant Income (000s) is a 4-character field (CFPB LAR record
#:   format), so 9999 is its ceiling ("$9,999,000 or more", and a filer placeholder),
#:   not an amount. Silver nulls it from this build on; the entry keeps the accessor
#:   correct on silver built before that (where it reads 9,999,000 dollars).
#:
#: Category codes that are real answers stay live: purchaser_type 0, co_applicant_sex 5
#: (no co-applicant), hoepa_status 3, denial_reason_1 10, aus_1 6 and the like. So do
#: fee amounts that happen to read 1111/8888/9999 dollars.
SENTINEL_CODES: Final[dict[HMDAPeriod, dict[str, tuple[int, ...]]]] = {
    "post2018": {
        **{
            c: (-99999,)
            for c in (*POST2018_EXEMPT_COLUMNS, "debt_to_income_ratio", "conforming_loan_limit")
        },
        **dict.fromkeys(POST2018_EXEMPT_CODED_COLUMNS, (1111,)),
        "applicant_age": (8888, 9999),
        "co_applicant_age": (8888, 9999),
    },
    "period_2007_2017": {"income": (9_999_000,)},
    "pre2007": {"income": (9_999_000,)},
}

#: Pre-2018 Applicant Income (000s) ceiling: the largest value its 4-character field holds.
PRE2018_INCOME_CEILING_000S: Final = 9999

#: ``co_applicant_sex`` codes that report a co-applicant, per era (CFPB code sheet 2007-2017;
#: FIG 2018+, where 6 = selected both). 5 = no co-applicant; 4 (not applicable) says nothing.
CO_APPLICANT_PRESENT_CODES: Final[dict[HMDAPeriod, tuple[int, ...]]] = {
    "pre2007": (1, 2, 3),
    "period_2007_2017": (1, 2, 3),
    "post2018": (1, 2, 3, 6),
}

#: ``co_applicant_sex`` code for "no co-applicant", in every era.
NO_CO_APPLICANT_CODE: Final = 5


# ============================================================================
# Pre-2007 Data Constants
# ============================================================================

# Columns to convert to integers in pre-2007 silver layer
PRE2007_INTEGER_COLUMNS: list[str] = [
    "activity_year",
    "loan_amount",  # Will be multiplied by 1000
    "income",  # Will be multiplied by 1000
    "occupancy_type",
    "edit_status",  # 5=Validity edit failure(s), 6=Quality edit failure(s), 7=Both
    "sequence_number",
    "lien_status",
    "hoepa_status",
    "preapproval",
    "property_type",
    "loan_type",
    "loan_purpose",
    "action_taken",
    "purchaser_type",
    # Demographic columns (non-numeric values treated as errors, converted to null)
    "applicant_sex",
    "co_applicant_sex",
    "applicant_ethnicity",
    "co_applicant_ethnicity",
    "applicant_race_1",
    "applicant_race_2",
    "applicant_race_3",
    "applicant_race_4",
    "applicant_race_5",
    "co_applicant_race_1",
    "co_applicant_race_2",
    "co_applicant_race_3",
    "co_applicant_race_4",
    "co_applicant_race_5",
    # Denial reasons (non-numeric values treated as errors, converted to null)
    "denial_reason_1",
    "denial_reason_2",
    "denial_reason_3",
]

# Columns to convert to floats in pre-2007 silver layer
PRE2007_FLOAT_COLUMNS: list[str] = [
    "rate_spread",
]


# ============================================================================
# 2007-2017 Data Constants
# ============================================================================


# Integer columns for 2007-2017 data
PERIOD_2007_2017_INTEGER_COLUMNS: list[str] = [
    "activity_year",
    "agency_code",
    "loan_type",
    "property_type",
    "loan_purpose",
    "occupancy_type",
    "loan_amount",
    "preapproval",
    "action_taken",
    # "msa_md",
    "applicant_ethnicity",
    "co_applicant_ethnicity",
    "applicant_race_1",
    "applicant_race_2",
    "applicant_race_3",
    "applicant_race_4",
    "applicant_race_5",
    "co_applicant_race_1",
    "co_applicant_race_2",
    "co_applicant_race_3",
    "co_applicant_race_4",
    "co_applicant_race_5",
    "applicant_ethnicity_1",
    "applicant_ethnicity_2",
    "applicant_ethnicity_3",
    "applicant_ethnicity_4",
    "applicant_ethnicity_5",
    "co_applicant_ethnicity_1",
    "co_applicant_ethnicity_2",
    "co_applicant_ethnicity_3",
    "co_applicant_ethnicity_4",
    "co_applicant_ethnicity_5",
    "applicant_sex",
    "co_applicant_sex",
    "hoepa_status",
    "lien_status",
    "income",
    "purchaser_type",
    "denial_reason_1",
    "denial_reason_2",
    "denial_reason_3",
    "denial_reason_4",
    "edit_status",
    "sequence_number",
    "application_date_indicator",
    "tract_population",
    "ffiec_msa_md_median_family_income",
    "tract_owner_occupied_units",
    "tract_one_to_four_family_units",
    "tract_median_age_of_housing_units",
]

# Float columns for 2007-2017 data
PERIOD_2007_2017_FLOAT_COLUMNS: list[str] = [
    "rate_spread",
    "tract_minority_population_percent",
    "tract_to_msa_income_percentage",
]


# ============================================================================
# Column Rename Dictionary (Legacy → Modern Names)
# ============================================================================

# Comprehensive dictionary mapping legacy HMDA column names to modern standardized names.
# Applied across all time periods during silver layer construction.
# Only columns that exist in the data will be renamed (safe application).

RENAME_DICTIONARY: dict[str, str] = {
    # Core field renames (2007-2017 period)
    "as_of_year": "activity_year",
    "applicant_income_000s": "income",
    "loan_amount_000s": "loan_amount",
    "census_tract_number": "census_tract",
    # Occupancy variants (pre-2007 and 2007-2017)
    "occupancy": "occupancy_type",  # Pre-2007 format
    "owner_occupancy": "occupancy_type",  # 2007-2017 format
    # MSA/MD standardization (pre-2007 and 2007-2017)
    "msamd": "msa_md",
    # Tract variable renames (2007-2017 period)
    "population": "tract_population",
    "minority_population": "tract_minority_population_percent",
    "hud_median_family_income": "ffiec_msa_md_median_family_income",
    "tract_to_msamd_income": "tract_to_msa_income_percentage",
    "number_of_owner_occupied_units": "tract_owner_occupied_units",
    "number_of_1_to_4_family_units": "tract_one_to_four_family_units",
    # Post-2018 corrections
    "loan_to_value_ratio": "combined_loan_to_value_ratio",
}


# --------------------------------------------------------------------------- #
# Per-(dataset, era) silver dtype specs (close-to-final types; see core/types.py).
# HMDA feeds 5+ matchers that compare code
# fields to INTEGER literals (action_taken == 1, loan_purpose.is_in([31, 32])),
# so codes are right-sized to the smallest SIGNED int that holds their full range
# — NOT converted to pl.Enum (which would break those comparisons). There is no
# geo-as-int bug (state_code/county_code/census_tract are already zero-padded
# Utf8) and NO sentinel masking: the -99999 / 1111 / 9999 "Exempt"/NA markers stay
# LIVE (matchers + analyses detect them), so ints are sized to HOLD them (e.g.
# debt_to_income_ratio -> Int32, the 1111-family -> Int16). Money -> Int64, never
# Int32 (see core/types.py); ratios/rates/costs -> Float64. HMDAIndex / lei / geo / names stay Utf8.
# Generated data-driven from the per-era full-year domains
# (derived data-driven from the full observed domain).
# --------------------------------------------------------------------------- #
_S = TEXT  # keep/identifier Utf8 marker
_I8, _I16, _I32, _I64, _F64 = pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.Float64


_HMDA_LOANS_PRE2007_COLS: list[tuple[str, ColumnRole]] = [
    ("activity_year", _I16),
    ("respondent_id", _S),
    ("agency_code", _S),
    ("loan_type", _I8),
    ("loan_purpose", _I8),
    ("occupancy_type", _I8),
    ("loan_amount", _I64),
    ("action_taken", _I8),
    ("msa_md", _S),
    ("state_code", _S),
    ("county_code", _S),
    ("census_tract", _S),
    ("applicant_race_1", _I8),
    ("co_applicant_race_1", _I8),
    ("applicant_sex", _I8),
    ("co_applicant_sex", _I8),
    ("income", _I64),
    ("purchaser_type", _I8),
    ("denial_reason_1", _I8),
    ("denial_reason_2", _I8),
    ("denial_reason_3", _I8),
    ("edit_status", _I8),
    ("sequence_number", _I32),
    ("property_type", _I8),
    ("preapproval", _I8),
    ("applicant_ethnicity", _I8),
    ("co_applicant_ethnicity", _I8),
    ("applicant_race_2", _I8),
    ("applicant_race_3", _I8),
    ("applicant_race_4", _I8),
    ("applicant_race_5", _I8),
    ("co_applicant_race_2", _I8),
    ("co_applicant_race_3", _I8),
    ("co_applicant_race_4", _I8),
    ("co_applicant_race_5", _I8),
    ("rate_spread", _F64),
    ("hoepa_status", _I8),
    ("lien_status", _I8),
]

_HMDA_LOANS_PERIOD_2007_2017_COLS: list[tuple[str, ColumnRole]] = [
    ("activity_year", _I16),
    ("respondent_id", _S),
    ("agency_code", _I8),
    ("loan_type", _I8),
    ("property_type", _I8),
    ("loan_purpose", _I8),
    ("occupancy_type", _I8),
    ("loan_amount", _I64),
    ("preapproval", _I8),
    ("action_taken", _I8),
    ("msa_md", _S),
    ("state_code", _S),
    ("county_code", _S),
    ("census_tract", _S),
    ("applicant_ethnicity", _I8),
    ("co_applicant_ethnicity", _I8),
    ("applicant_race_1", _I8),
    ("applicant_race_2", _I8),
    ("applicant_race_3", _I8),
    ("applicant_race_4", _I8),
    ("applicant_race_5", _I8),
    ("co_applicant_race_1", _I8),
    ("co_applicant_race_2", _I8),
    ("co_applicant_race_3", _I8),
    ("co_applicant_race_4", _I8),
    ("co_applicant_race_5", _I8),
    ("applicant_sex", _I8),
    ("co_applicant_sex", _I8),
    ("income", _I64),
    ("purchaser_type", _I8),
    ("denial_reason_1", _I8),
    ("denial_reason_2", _I8),
    ("denial_reason_3", _I8),
    ("rate_spread", _F64),
    ("hoepa_status", _I8),
    ("lien_status", _I8),
    ("edit_status", _I8),
    ("sequence_number", _I32),
    ("tract_population", _I32),
    ("tract_minority_population_percent", _F64),
    ("ffiec_msa_md_median_family_income", _I64),
    ("tract_to_msa_income_percentage", _F64),
    ("tract_owner_occupied_units", _I16),
    ("tract_one_to_four_family_units", _I16),
    ("application_date_indicator", _I8),
    ("file_type", _S),
    ("HMDAIndex", _S),
    # Derived in silver (see import_silver): one definition for every consumer.
    ("respondent_key", _S),
    ("number_of_borrowers", _I8),
    ("purchaser_type_label", _S),
]

_HMDA_LOANS_POST2018_COLS: list[tuple[str, ColumnRole]] = [
    ("HMDAIndex", _S),
    ("activity_year", _I16),
    ("lei", _S),
    ("derived_msa_md", _I32),
    ("state_code", _S),
    ("county_code", _S),
    ("census_tract", _S),
    ("conforming_loan_limit", _I32),
    ("action_taken", _I8),
    ("purchaser_type", _I8),
    ("preapproval", _I8),
    ("loan_type", _I8),
    ("loan_purpose", _I8),
    ("lien_status", _I8),
    ("reverse_mortgage", _I16),
    ("open_end_line_of_credit", _I16),
    ("business_or_commercial_purpose", _I16),
    ("loan_amount", _I64),
    ("combined_loan_to_value_ratio", _F64),
    ("interest_rate", _F64),
    ("rate_spread", _F64),
    ("hoepa_status", _I8),
    ("total_loan_costs", _F64),
    ("total_points_and_fees", _F64),
    ("origination_charges", _F64),
    ("discount_points", _F64),
    ("lender_credits", _F64),
    ("loan_term", _I32),
    ("prepayment_penalty_term", _I32),
    ("intro_rate_period", _I32),
    ("negative_amortization", _I16),
    ("interest_only_payment", _I16),
    ("balloon_payment", _I16),
    ("other_nonamortizing_features", _I16),
    ("property_value", _I64),
    ("construction_method", _I8),
    ("occupancy_type", _I8),
    ("manufactured_home_secured_property_type", _I16),
    ("manufactured_home_land_property_interest", _I16),
    ("total_units", _I8),
    ("multifamily_affordable_units", _I32),
    ("income", _I64),
    ("debt_to_income_ratio", _I32),
    ("applicant_credit_score_type", _I16),
    ("co_applicant_credit_score_type", _I16),
    ("applicant_ethnicity_1", _I8),
    ("applicant_ethnicity_2", _I8),
    ("applicant_ethnicity_3", _I8),
    ("applicant_ethnicity_4", _I8),
    ("applicant_ethnicity_5", _I8),
    ("co_applicant_ethnicity_1", _I8),
    ("co_applicant_ethnicity_2", _I8),
    ("co_applicant_ethnicity_3", _I8),
    ("co_applicant_ethnicity_4", _I8),
    ("co_applicant_ethnicity_5", _I8),
    ("applicant_ethnicity_observed", _I8),
    ("co_applicant_ethnicity_observed", _I8),
    ("applicant_race_1", _I8),
    ("applicant_race_2", _I8),
    ("applicant_race_3", _I8),
    ("applicant_race_4", _I8),
    ("applicant_race_5", _I8),
    ("co_applicant_race_1", _I8),
    ("co_applicant_race_2", _I8),
    ("co_applicant_race_3", _I8),
    ("co_applicant_race_4", _I8),
    ("co_applicant_race_5", _I8),
    ("applicant_race_observed", _I8),
    ("co_applicant_race_observed", _I8),
    ("applicant_sex", _I8),
    ("co_applicant_sex", _I8),
    ("applicant_sex_observed", _I8),
    ("co_applicant_sex_observed", _I8),
    ("applicant_age", _I16),
    ("co_applicant_age", _I16),
    ("applicant_age_above_62", _I8),
    ("co_applicant_age_above_62", _I8),
    ("submission_of_application", _I16),
    ("initially_payable_to_institution", _I16),
    ("aus_1", _I16),
    ("aus_2", _I8),
    ("aus_3", _I8),
    ("aus_4", _I8),
    ("aus_5", _I8),
    ("denial_reason_1", _I16),
    ("denial_reason_2", _I8),
    ("denial_reason_3", _I8),
    ("denial_reason_4", _I8),
    ("tract_population", _I32),
    ("tract_minority_population_percent", _F64),
    ("ffiec_msa_md_median_family_income", _F64),
    ("tract_to_msa_income_percentage", _F64),
    ("tract_owner_occupied_units", _I16),
    ("tract_one_to_four_family_homes", _I16),
    ("tract_median_age_of_housing_units", _I8),
    ("file_type", _S),
    ("applicant_credit_scoring_model", _S),
    ("co_applicant_credit_scoring_model", _S),
    ("other_non_amortizing_features", _S),
    # Derived in silver (see import_silver).
    ("number_of_borrowers", _I8),
    ("purchaser_type_label", _S),
]

_HMDA_PANEL_PRE2007_COLS: list[tuple[str, ColumnRole]] = [
    ("respondent_id", _S),
    ("msa_md", _S),
    ("agency_code", _S),
    ("agency_group", _S),
    ("respondent_name", _S),
    ("respondent_city", _S),
    ("respondent_state", _S),
    ("reporter_fips", _S),
    ("assets", _S),
    ("other_lender_code", _S),
    ("parent_id", _S),
    ("parent_name", _S),
    ("parent_city", _S),
    ("parent_state", _S),
    ("activity_year", _I16),
    ("respondent_fips", _S),
    ("respondent_rssd", _S),
]

_HMDA_PANEL_POST2018_COLS: list[tuple[str, ColumnRole]] = [
    ("activity_year", _I16),
    ("lei", _S),
    ("tax_id", _S),
    ("agency_code", _I8),
    ("id_2017", _S),
    ("respondent_rssd", _S),
    ("respondent_name", _S),
    ("respondent_state", _S),
    ("respondent_city", _S),
    ("assets", _I64),
    ("other_lender_code", _I8),
    ("parent_rssd", _S),
    ("parent_name", _S),
    ("top_holder_rssd", _S),
    ("top_holder_name", _S),
    ("file_type", _S),
    ("arid_2017", _S),
]

_HMDA_TRANSMITTAL_SERIES_PRE2007_COLS: list[tuple[str, ColumnRole]] = [
    ("activity_year", _I16),
    ("agency_code", _S),
    ("respondent_id", _S),
    ("respondent_name", _S),
    ("respondent_addr", _S),
    ("respondent_city", _S),
    ("respondent_state", _S),
    ("respondent_zip_code", _S),
    ("parent_name", _S),
    ("parent_addr", _S),
    ("parent_city", _S),
    ("parent_state", _S),
    ("parent_zip_code", _S),
    ("edit_status", _I8),
    ("tax_id", _S),
]

_HMDA_TRANSMITTAL_SERIES_POST2018_COLS: list[tuple[str, ColumnRole]] = [
    ("activity_year", _I16),
    ("calendar_quarter", _I16),
    ("lei", _S),
    ("tax_id", _S),
    ("agency_code", _I8),
    ("respondent_name", _S),
    ("respondent_state", _S),
    ("respondent_city", _S),
    ("respondent_zip_code", _S),
    ("lar_count", _I32),
    ("file_type", _S),
]

# --------------------------------------------------------------------------- #
# 2007-2017 panel / transmittal series
# --------------------------------------------------------------------------- #
# Both datasets change layout mid-era, and the raw headers differ in wording
# between the halves, so the renames below map BOTH spellings onto one set of
# house names. Absent columns become typed nulls via conform-to-target, so a
# single spec covers the whole era.
#
#   panel               2007-2009: 16 cols   2010-2017: 21 cols
#   transmittal series  2007-2016: 22 cols   2017:      15 cols
#
# The institution key here is (respondent_id, agency_code) — LEI does not exist
# until 2018, where id_2017/arid_2017 carry the bridge back to respondent_id.

#: Raw panel header -> house name, covering both the 2007-2009 and 2010-2017
#: spellings ("Respondent Identification Number" vs "Respondent ID", and the
#: "(Panel)" suffixes the later files add).
PANEL_2007_2017_RENAMES: Final[dict[str, str]] = {
    "Activity Year": "activity_year",
    "Respondent Identification Number": "respondent_id",
    "Respondent ID": "respondent_id",
    "Agency Code": "agency_code",
    "Parent Identification Number": "parent_id",
    "Parent Respondent ID": "parent_id",
    "Parent Name": "parent_name",
    "Parent Name (Panel)": "parent_name",
    "Parent City": "parent_city",
    "Parent City (Panel)": "parent_city",
    "Parent State": "parent_state",
    "Parent State (Panel)": "parent_state",
    "Region": "region",
    "Assets": "assets",
    "Other Lender Code": "other_lender_code",
    "Respondent Name": "respondent_name",
    "Respondent Name (Panel)": "respondent_name",
    "Respondent Address": "respondent_addr",
    "Respondent City": "respondent_city",
    "Respondent City (Panel)": "respondent_city",
    "Respondent State": "respondent_state",
    "Respondent State (Panel)": "respondent_state",
    "Respondent Zip Code": "respondent_zip_code",
    "Tax ID": "tax_id",
    "Top Holder RSSD ID": "top_holder_rssd",
    "Top Holder Name": "top_holder_name",
    "Top Holder City": "top_holder_city",
    "Top Holder State": "top_holder_state",
    "Top Holder Country": "top_holder_country",
    "Respondent RSSD ID": "respondent_rssd",
    "Parent RSSD ID": "parent_rssd",
    "Respondent FIPS State Number": "respondent_fips",
}

#: Raw transmittal-series header -> house name. The 2007-2016 file is a TS/panel
#: hybrid: it carries the panel's respondent name/city/state alongside the TS
#: values because the two can disagree (the TS reports the filing address, the
#: panel the home office), so those keep a ``_panel`` suffix.
TRANSMITTAL_SERIES_2007_2017_RENAMES: Final[dict[str, str]] = {
    "Activity Year": "activity_year",
    "Respondent ID": "respondent_id",
    "Agency Code": "agency_code",
    "Federal Tax ID": "tax_id",
    "Respondent Name": "respondent_name",
    "Respondent Mailing Address": "respondent_addr",
    "Respondent City": "respondent_city",
    "Respondent State": "respondent_state",
    "Respondent ZIP Code": "respondent_zip_code",
    "Parent Name": "parent_name",
    "Parent Address": "parent_addr",
    "Parent City": "parent_city",
    "Parent State": "parent_state",
    "Parent ZIP Code": "parent_zip_code",
    "Respondent Name (Panel)": "respondent_name_panel",
    "Respondent City (Panel)": "respondent_city_panel",
    "Respondent State (Panel)": "respondent_state_panel",
    "Assets": "assets",
    "Other Lender Code": "other_lender_code",
    "Region Code": "region",
    "LAR Count": "lar_count",
    "Validity Error": "validity_error",
}

#: Union of the 2007-2009 (16-col) and 2010-2017 (21-col) panel layouts.
#: ``region`` is a zero-padded regulator region code that goes 100% blank in
#: 2017; the RSSD ids and ``respondent_fips`` are identifiers, so all stay Utf8.
_HMDA_PANEL_PERIOD_2007_2017_COLS: list[tuple[str, ColumnRole]] = [
    ("activity_year", _I16),
    ("respondent_id", _S),
    ("agency_code", _I8),
    ("parent_id", _S),
    ("parent_name", _S),
    ("parent_city", _S),
    ("parent_state", _S),
    ("region", _S),
    ("assets", _I64),
    ("other_lender_code", _I8),
    ("respondent_name", _S),
    ("respondent_addr", _S),
    ("respondent_city", _S),
    ("respondent_state", _S),
    ("respondent_zip_code", _S),
    ("tax_id", _S),
    ("top_holder_rssd", _S),
    ("top_holder_name", _S),
    ("top_holder_city", _S),
    ("top_holder_state", _S),
    ("top_holder_country", _S),
    ("respondent_rssd", _S),
    ("parent_rssd", _S),
    ("respondent_fips", _S),
    # Derived in silver (see import_silver): the join key the loans silver also carries.
    ("respondent_key", _S),
]

#: Union of the 2007-2016 (22-col) and 2017 (15-col) transmittal-series layouts.
_HMDA_TRANSMITTAL_SERIES_PERIOD_2007_2017_COLS: list[tuple[str, ColumnRole]] = [
    ("activity_year", _I16),
    ("respondent_id", _S),
    ("agency_code", _I8),
    ("tax_id", _S),
    ("respondent_name", _S),
    ("respondent_addr", _S),
    ("respondent_city", _S),
    ("respondent_state", _S),
    ("respondent_zip_code", _S),
    ("parent_name", _S),
    ("parent_addr", _S),
    ("parent_city", _S),
    ("parent_state", _S),
    ("parent_zip_code", _S),
    ("respondent_name_panel", _S),
    ("respondent_city_panel", _S),
    ("respondent_state_panel", _S),
    ("assets", _I64),
    ("other_lender_code", _I8),
    ("region", _S),
    ("lar_count", _I32),
    ("validity_error", _S),
]

#: Post-2018 loans target columns some vintages legitimately omit, audited
#: against every bronze file 2026-08-31: the three *_scoring_model /
#: other_non_amortizing_features names appear only in the MLAR vocabulary (all
#: 19 standard LAR files fill them), and the MLAR release carries neither the
#: LAR spellings nor the derived/census enrichment columns. Any OTHER absent
#: target column now fails the build instead of shipping 100% null.
_LOANS_POST2018_OPTIONAL: tuple[str, ...] = (
    # absent from every standard LAR release (MLAR-vocabulary names)
    "applicant_credit_scoring_model",
    "co_applicant_credit_scoring_model",
    "other_non_amortizing_features",
    # absent from the MLAR release (LAR-vocabulary names + derived/census fields)
    "applicant_credit_score_type",
    "co_applicant_credit_score_type",
    "other_nonamortizing_features",
    "conforming_loan_limit",
    "derived_msa_md",
    "ffiec_msa_md_median_family_income",
    "tract_median_age_of_housing_units",
    "tract_minority_population_percent",
    "tract_one_to_four_family_homes",
    "tract_owner_occupied_units",
    "tract_population",
    "tract_to_msa_income_percentage",
)

#: (dataset, era) -> its silver spec; applied by the era builders in import_silver.
HMDA_SILVER_SPECS: dict[tuple[str, str], SilverSpec] = {
    ("loans", "pre2007"): spec_from_columns(_HMDA_LOANS_PRE2007_COLS),
    ("loans", "period_2007_2017"): spec_from_columns(_HMDA_LOANS_PERIOD_2007_2017_COLS),
    ("loans", "post2018"): spec_from_columns(
        _HMDA_LOANS_POST2018_COLS, optional_target=_LOANS_POST2018_OPTIONAL
    ),
    ("panel", "pre2007"): spec_from_columns(_HMDA_PANEL_PRE2007_COLS),
    ("panel", "period_2007_2017"): spec_from_columns(_HMDA_PANEL_PERIOD_2007_2017_COLS),
    # arid_2017 is the 2017-transition identifier; only the 2018/2019
    # snapshot-era panels carry it (audited 2026-08-31).
    ("panel", "post2018"): spec_from_columns(
        _HMDA_PANEL_POST2018_COLS, optional_target=("arid_2017",)
    ),
    ("transmittal_series", "pre2007"): spec_from_columns(_HMDA_TRANSMITTAL_SERIES_PRE2007_COLS),
    ("transmittal_series", "period_2007_2017"): spec_from_columns(
        _HMDA_TRANSMITTAL_SERIES_PERIOD_2007_2017_COLS
    ),
    ("transmittal_series", "post2018"): spec_from_columns(_HMDA_TRANSMITTAL_SERIES_POST2018_COLS),
}
