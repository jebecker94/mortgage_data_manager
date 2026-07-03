"""Configuration management for HMDA Data Manager.

This module handles path configuration and HMDA-specific constants,
inheriting from the base MortgageDataConfig.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import polars as pl
from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.types import SilverSpec


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

    # Backward compatibility aliases
    RAW_DIR = HMDA_RAW_DIR
    BRONZE_DIR = HMDA_BRONZE_DIR
    SILVER_DIR = HMDA_SILVER_DIR

    # HMDA-specific constants
    HMDA_INDEX_COLUMN = "HMDAIndex"

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary HMDA directories if they don't exist."""
        for d in (cls.HMDA_RAW_DIR, cls.HMDA_BRONZE_DIR, cls.HMDA_SILVER_DIR):
            d.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_dataset_dir(
        cls,
        stage: Literal["bronze", "silver"],
        dataset: Literal["loans", "panel", "transmittal_series"],
        period: Literal["pre2007", "period_2007_2017", "post2018"] = "post2018",
    ) -> Path:
        """Return the directory for a given HMDA stage/dataset/period.

        HMDA medallion data is organized along extra axes (dataset family and
        era period) beyond the base layout, so this helper complements the
        inherited base ``get_medallion_dir(subpackage, stage)`` rather than
        overriding it.

        Args:
            stage: Medallion layer.
            dataset: Dataset family.
            period: Time period subfolder (defaults to post2018).

        Returns:
            The target directory path.
        """
        base = cls.HMDA_BRONZE_DIR if stage == "bronze" else cls.HMDA_SILVER_DIR
        return base / dataset / period


# ============================================================================
# Convenience exports for backward compatibility
# ============================================================================

PROJECT_DIR = HMDAConfig.PROJECT_DIR
DATA_DIR = HMDAConfig.HMDA_DATA_DIR
RAW_DIR = HMDAConfig.HMDA_RAW_DIR
BRONZE_DIR = HMDAConfig.HMDA_BRONZE_DIR
SILVER_DIR = HMDAConfig.HMDA_SILVER_DIR
HMDA_INDEX_COLUMN = HMDAConfig.HMDA_INDEX_COLUMN


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

# Census tract summary statistics columns (optionally dropped for size reduction)
POST2018_TRACT_COLUMNS: list[str] = [
    "tract_population",
    "tract_minority_population_percent",
    "ffiec_msa_md_median_family_income",
    "tract_to_msa_income_percentage",
    "tract_owner_occupied_units",
    "tract_one_to_four_family_homes",
    "tract_median_age_of_housing_units",
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

# Census tract summary statistics columns for 2007-2017 data
PERIOD_2007_2017_TRACT_COLUMNS: list[str] = [
    "tract_population",
    "tract_minority_population_percent",
    "ffiec_msa_md_median_family_income",
    "tract_to_msa_income_percentage",
    "tract_owner_occupied_units",
    "tract_one_to_four_family_units",
    "tract_median_age_of_housing_units",
]

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
# Per-(dataset, era) silver dtype specs (close-to-final types; see core/types.py
# + the 2026-06-27 silver type audit). HMDA feeds 5+ matchers that compare code
# fields to INTEGER literals (action_taken == 1, loan_purpose.is_in([31, 32])),
# so codes are right-sized to the smallest SIGNED int that holds their full range
# — NOT converted to pl.Enum (which would break those comparisons). There is no
# geo-as-int bug (state_code/county_code/census_tract are already zero-padded
# Utf8) and NO sentinel masking: the -99999 / 1111 / 9999 "Exempt"/NA markers stay
# LIVE (matchers + analyses detect them), so ints are sized to HOLD them (e.g.
# debt_to_income_ratio -> Int32, the 1111-family -> Int16). Money -> Int32/Int64;
# ratios/rates/costs -> Float64. HMDAIndex / lei / geo / names stay Utf8.
# Generated data-driven from the per-era full-year domains
# (investigations/scripts/hmda_domain_profile.py + hmda_gen_specs.py).
# --------------------------------------------------------------------------- #
_S = "str"  # keep/identifier Utf8 marker
_I8, _I16, _I32, _I64, _F64 = pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.Float64


def _hmda_spec(columns: list[tuple[str, Any]]) -> SilverSpec:
    """Build an HMDA SilverSpec from an ordered ``(name, role)`` list.

    ``role`` is the ``_S`` marker (keep as Utf8) or a polars numeric dtype (cast).
    The ordered list is the single source of truth for casts + the target schema,
    so conform-to-target unifies the cross-year / cross-file_type column drift.
    """
    casts: dict[str, Any] = {}
    idents: list[str] = []
    target: dict[str, Any] = {}
    for name, role in columns:
        if isinstance(role, str):
            idents.append(name)
            target[name] = pl.String
        else:
            casts[name] = role
            target[name] = role
    return SilverSpec(casts=casts, identifiers=tuple(idents), target=pl.Schema(target))


_HMDA_LOANS_PRE2007_COLS: list[tuple[str, Any]] = [
    ("activity_year", _I16),
    ("respondent_id", _S),
    ("agency_code", _S),
    ("loan_type", _I8),
    ("loan_purpose", _I8),
    ("occupancy_type", _I8),
    ("loan_amount", _I32),
    ("action_taken", _I8),
    ("msa_md", _S),
    ("state_code", _S),
    ("county_code", _S),
    ("census_tract", _S),
    ("applicant_race_1", _I8),
    ("co_applicant_race_1", _I8),
    ("applicant_sex", _I8),
    ("co_applicant_sex", _I8),
    ("income", _I32),
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

_HMDA_LOANS_PERIOD_2007_2017_COLS: list[tuple[str, Any]] = [
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
    ("ffiec_msa_md_median_family_income", _I32),
    ("tract_to_msa_income_percentage", _F64),
    ("tract_owner_occupied_units", _I16),
    ("tract_one_to_four_family_units", _I16),
    ("application_date_indicator", _I8),
    ("file_type", _S),
    ("HMDAIndex", _S),
]

_HMDA_LOANS_POST2018_COLS: list[tuple[str, Any]] = [
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
]

_HMDA_PANEL_PRE2007_COLS: list[tuple[str, Any]] = [
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

_HMDA_PANEL_POST2018_COLS: list[tuple[str, Any]] = [
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

_HMDA_TRANSMITTAL_SERIES_PRE2007_COLS: list[tuple[str, Any]] = [
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

_HMDA_TRANSMITTAL_SERIES_POST2018_COLS: list[tuple[str, Any]] = [
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

#: (dataset, era) -> its silver spec; applied by the era builders in import_silver.
HMDA_SILVER_SPECS: dict[tuple[str, str], SilverSpec] = {
    ("loans", "pre2007"): _hmda_spec(_HMDA_LOANS_PRE2007_COLS),
    ("loans", "period_2007_2017"): _hmda_spec(_HMDA_LOANS_PERIOD_2007_2017_COLS),
    ("loans", "post2018"): _hmda_spec(_HMDA_LOANS_POST2018_COLS),
    ("panel", "pre2007"): _hmda_spec(_HMDA_PANEL_PRE2007_COLS),
    ("panel", "post2018"): _hmda_spec(_HMDA_PANEL_POST2018_COLS),
    ("transmittal_series", "pre2007"): _hmda_spec(_HMDA_TRANSMITTAL_SERIES_PRE2007_COLS),
    ("transmittal_series", "post2018"): _hmda_spec(_HMDA_TRANSMITTAL_SERIES_POST2018_COLS),
}
