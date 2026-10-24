"""Configuration for FHFA (Federal Housing Finance Agency) data.

This module provides:
    - FHFAConfig: Main configuration class inheriting from MortgageDataConfig
    - ImportOptions: Operational options for data loaders
    - ensure_parent_dir: Utility function for directory creation

The FHFAConfig class follows the project's configuration inheritance pattern,
supporting environment variable overrides and medallion architecture paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import polars as pl
from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.types import (
    ColumnRole,
    Fips,
    SilverSpec,
    spec_from_columns,
)

#: FHFA's Public Use Database index page.
#:
#: Defined here rather than in ``download.py`` because it is not FHFA's alone:
#: the FHLB Acquired Member Assets files are published on the same page, so
#: ``fhlb.download`` scrapes it too. Restating the URL in both modules let a
#: site move be fixed on one side only — and the losing side fails quietly, with
#: a "No files found to download" warning and an empty result rather than an
#: error.
FHFA_PUDB_URL = "https://www.fhfa.gov/data/pudb"


class FHFAConfig(MortgageDataConfig):
    """FHFA-specific configuration.

    Inherits base configuration from MortgageDataConfig and adds
    FHFA-specific paths and constants.

    Environment Variables:
        FHFA_DATA_DIR: Root FHFA data directory
        FHFA_RAW_DIR: Raw data directory
        FHFA_BRONZE_DIR: Bronze layer directory (staged parquet)
        FHFA_SILVER_DIR: Silver layer directory (transformed data)
        FHFA_GOLD_DIR: Gold layer directory (aggregations)
        FHFA_SCHEMAS_DIR: Schema files directory

    Example:
        >>> from mortgage_data_manager.sources.fhfa.config import FHFAConfig
        >>> print(FHFAConfig.FHFA_DATA_DIR)
        PosixPath('/path/to/data/fhfa')
        >>> print(FHFAConfig.FHFA_BRONZE_DIR)
        PosixPath('/path/to/data/fhfa/bronze')
    """

    # FHFA data directories
    FHFA_DATA_DIR: Path = Path(
        config("FHFA_DATA_DIR", default=str(MortgageDataConfig.get_subpackage_data_dir("fhfa")))
    )
    FHFA_RAW_DIR: Path = Path(config("FHFA_RAW_DIR", default=str(FHFA_DATA_DIR / "raw")))
    FHFA_BRONZE_DIR: Path = Path(config("FHFA_BRONZE_DIR", default=str(FHFA_DATA_DIR / "bronze")))
    FHFA_SILVER_DIR: Path = Path(config("FHFA_SILVER_DIR", default=str(FHFA_DATA_DIR / "silver")))
    FHFA_GOLD_DIR: Path = Path(config("FHFA_GOLD_DIR", default=str(FHFA_DATA_DIR / "gold")))
    FHFA_SCHEMAS_DIR: Path = Path(
        config("FHFA_SCHEMAS_DIR", default=str(MortgageDataConfig.SCHEMAS_DIR / "fhfa"))
    )

    @classmethod
    def get_prefix_dir(
        cls,
        stage: Literal["raw", "bronze", "silver", "gold"],
        prefix: str,
    ) -> Path:
        """Get the prefix sub-directory under an FHFA medallion stage.

        FHFA organizes each medallion stage into per-prefix sub-directories
        (e.g. ``sf_c``, ``hpi``). This helper resolves the prefix axis; for the
        bare stage directory use the per-dir attributes (``FHFA_SILVER_DIR``,
        etc.) or the inherited
        `MortgageDataConfig.get_medallion_dir`.

        Args:
            stage: Medallion stage ('raw', 'bronze', 'silver', or 'gold')
            prefix: Data prefix naming the sub-directory

        Returns:
            Path to the prefix sub-directory under the stage

        Example:
            >>> FHFAConfig.get_prefix_dir('silver', 'sf_c')
            PosixPath('/path/to/data/fhfa/silver/sf_c')

        Note:
            ``get_medallion_dir`` is inherited from `MortgageDataConfig`
            unmodified (signature ``get_medallion_dir(subpackage, stage)``).
        """
        stage_dirs = {
            "raw": cls.FHFA_RAW_DIR,
            "bronze": cls.FHFA_BRONZE_DIR,
            "silver": cls.FHFA_SILVER_DIR,
            "gold": cls.FHFA_GOLD_DIR,
        }
        return stage_dirs[stage] / prefix

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary directories for FHFA data processing.

        Example:
            >>> FHFAConfig.ensure_directories()
            >>> # Creates: data/fhfa/raw, data/fhfa/bronze, etc.
        """
        for d in (cls.FHFA_RAW_DIR, cls.FHFA_BRONZE_DIR, cls.FHFA_SILVER_DIR, cls.FHFA_SCHEMAS_DIR):
            d.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class ImportOptions:
    """Common operational options for data loaders."""

    overwrite: bool = False
    # Separate overwrite flags for dictionary artifacts (no fallback)
    # - raw dictionaries: PDFs scraped or extracted from zips
    # - clean dictionaries: parsed/combined CSV/Parquet next to PDFs
    overwrite_raw_dicts: bool = False
    overwrite_clean_dicts: bool = False
    excel_engine: Literal["xlrd", "openpyxl", "odf", "pyxlsb", "calamine"] | None = "openpyxl"


def ensure_parent_dir(path: Path) -> None:
    """Ensure that the parent directory of ``path`` exists.

    Args:
        path: Path whose parent directory should be created.
    """
    path.parent.mkdir(parents=True, exist_ok=True)


# Dataset to (family, letter, suffix) mapping
# Used by both bronze and silver import modules
DATASET_MAP = {
    "sf_a": ("sf", "a", "loans"),
    "sf_b": ("sf", "b", "units"),
    "sf_c": ("sf", "c", "loans"),
    "sf_d": ("sf", "d", "loans"),
    "mf_property_b": ("mf", "b", "loans"),
    "mf_unit_b": ("mf", "b", "units"),
    "mf_c": ("mf", "c", "loans"),
}

# Sorted list view of valid dataset identifiers (single source: DATASET_MAP)
VALID_DATASETS: list[str] = list(DATASET_MAP.keys())

#: First acquisition year covered: the HUD-era multifamily backfill extends the
#: GSE PUDB series to 1993 (the modern enterprise data begins in 2008).
FIRST_DATA_YEAR = 1993

#: Latest acquisition year FHFA has published.
LATEST_DATA_YEAR = 2025

#: First year whose data dictionary is published as Excel rather than PDF.
FIRST_EXCEL_DICTIONARY_YEAR = 2024

#: First year published as headed CSVs, one zip per file with both Enterprises
#: stacked (``{year}_pudb_{stem}.zip``), replacing the fixed-width
#: ``{year}_enterprise_pudb.zip`` bundle of per-Enterprise text files.
FIRST_CSV_YEAR = 2025

#: Last year published fixed-width; the master dictionaries cover only this era.
LAST_FIXED_WIDTH_YEAR = FIRST_CSV_YEAR - 1

#: Dataset -> CSV-era file stem. ``sf_a`` (Single-Family National File A) is absent:
#: FHFA discontinued that file with the 2025 release.
CSV_FILE_STEMS: dict[str, str] = {
    "sf_b": "sf_nfb",
    "sf_c": "sf_ctf",
    "sf_d": "sf_nfc",
    "mf_property_b": "mf_nfp",
    "mf_unit_b": "mf_nfu",
    "mf_c": "mf_ctf",
}


# --------------------------------------------------------------------------- #
# Per-dataset silver dtype specs (close-to-final types; see core/types.py). FHFA has 7 datasets with
# DIFFERENT schemas, and the SAME standardized name can be continuous in the loan-level file but a
# bucketed code in the summary files (e.g. tract_income_ratio is Float32 in sf_c but an Int8 bucket
# in sf_a/sf_b), so each dataset gets its OWN spec, derived data-driven from its full-year domain.
# A single global sentinel map could not tell the continuous and bucketed meanings of a shared
# name apart.
#
# Geography (state_code/county/census_tract/msa_code) -> zero-padded Utf8 in the
# loan-level files (sf_c/mf_c), recovering the leading zeros Int storage drops.
# In the SUMMARY files (sf_a/sf_b) msa_code is a 0/1 metro flag, NOT a geo code,
# so it stays Int8 there. Category codes keep their in-band "missing" markers
# (9/99) live, HMDA-style. ``date_of_mortgage_note`` is a 1/2 category, not a date.
# --------------------------------------------------------------------------- #
_I8, _I16, _I32, _I64, _F32, _F64 = (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.Float32, pl.Float64)
# Geographic identifiers carry their zero-pad width inline as ``Fips(width)``:
# state_code 2, county 3, census_tract 6, msa_code 5 — but ONLY in the loan-level
# files (see the note above about msa_code in sf_a/sf_b).


# --- sf_c: single-family loan-level (the geo + continuous-sentinel flagship) ---
_SF_C_COLS: list[tuple[str, ColumnRole]] = [
    ("year", _I32),
    ("Census Year", _I32),
    ("tract_median_income", _I64),
    ("tract_percent_minority", _F32),
    ("local_area_median_income", _I64),
    ("upb_acquisition", _I64),
    ("age_borrower", _I16),
    ("age_co_borrower", _I16),
    ("area_median_family_income", _I64),
    ("borrower_ethnicity", _I8),
    ("borrower_income_ratio", _F32),
    ("borrower_race_1", _I8),
    ("borrower_race_2", _I8),
    ("borrower_race_3", _I8),
    ("borrower_race_4", _I8),
    ("borrower_race_5", _I8),
    ("borrower_sex", _I8),
    ("borrower_annual_income", _I64),
    ("census_tract", Fips(6)),
    ("co_borrower_ethnicity", _I8),
    ("co_borrower_race_1", _I8),
    ("co_borrower_race_2", _I8),
    ("co_borrower_race_3", _I8),
    ("co_borrower_race_4", _I8),
    ("co_borrower_race_5", _I8),
    ("co_borrower_sex", _I8),
    ("county", Fips(3)),
    ("enterprise_flag", _I8),
    ("federal_guarantee", _I8),
    ("first_time_home_buyer", _I8),
    ("hoepa_status", _I8),
    ("lien_status", _I8),
    ("msa_code", Fips(5)),
    ("number_of_borrowers", _I8),
    ("occupancy_code", _I8),
    ("property_type", _I8),
    ("loan_purpose", _I8),
    ("rate_spread", _F32),
    ("record_number", _I32),
    ("tract_income_ratio", _F32),
    ("state_code", Fips(2)),
    ("underserved_areas_indicator", _I8),
    ("application_channel", _I8),
    ("area_concentrated_poverty", _I8),
    ("aus_name", _I8),
    ("borrower_age_62_plus", _I8),
    ("co_borrower_age_62_plus", _I8),
    ("credit_score_model_borrower", _I8),
    ("credit_score_model_co_borrower", _I8),
    ("date_of_mortgage_note", _I8),
    ("dti_ratio", _I8),
    ("discount_points", _F64),
    ("high_opportunity_area", _I8),
    ("interest_rate_at_origination", _F32),
    ("introductory_rate_period", _I16),
    ("ltv_at_origination", _F32),
    ("lower_mississippi_delta_county", _I8),
    ("manufactured_home_land_property_interest", _I8),
    ("middle_appalachia_county", _I8),
    ("note_amount", _I64),
    ("number_of_units", _I8),
    ("persistent_poverty_county", _I8),
    ("preapproval", _I8),
    ("property_value", _I64),
    ("qoz_census_tract", _I8),
    ("rural_census_tract", _I8),
    ("loan_term", _I16),
    ("colonias_tract", _I8),
]
_SF_C_SENT = {
    # documented continuous "not available" codes
    "rate_spread": (99.99,),
    "interest_rate_at_origination": (99,),
    "dti_ratio": (99,),
    "note_amount": (999999999,),
    "property_value": (999999999,),
    "borrower_annual_income": (999999998, 999999999),
    "discount_points": (999999,),
    "tract_median_income": (999999,),
    "local_area_median_income": (999999,),
    "area_median_family_income": (999999,),
    "tract_percent_minority": (9999.0,),
    "tract_income_ratio": (9999.0,),
    # approved undocumented missing markers (clearly "not available")
    "age_borrower": (999,),
    "age_co_borrower": (999,),
    "ltv_at_origination": (999,),
    # KEPT (structural "not applicable", not missing): introductory_rate_period
    # 999 = no intro period (fixed-rate); msa_code 99999 = non-metro.
}
FHFA_SF_C_SILVER = spec_from_columns(_SF_C_COLS, sentinels=_SF_C_SENT)

# --- sf_a / sf_b: single-family SUMMARY files (all bucketed Int8 codes; msa_code
# is a 0/1 metro flag, not geo; the "9" buckets are kept, so no sentinels) ---
_SF_A_COLS: list[tuple[str, ColumnRole]] = [
    ("year", _I32),
    ("Census Year", _I32),
    ("tract_percent_minority", _I8),
    ("borrower_income_ratio", _I8),
    ("borrower_race_and_ethnicity", _I8),
    ("borrower_sex", _I8),
    ("co_borrower_race_and_ethnicity", _I8),
    ("co_borrower_sex", _I8),
    ("enterprise_flag", _I8),
    ("federal_guarantee", _I8),
    ("ltv_at_origination", _I8),
    ("msa_code", _I8),
    ("number_of_units", _I8),
    ("loan_purpose", _I8),
    ("record_number", _I32),
    ("tract_income_ratio", _I8),
    ("underserved_areas_indicator", _I8),
    ("unit_affordability_category", _I8),
]
FHFA_SF_A_SILVER = spec_from_columns(_SF_A_COLS)

_SF_B_COLS: list[tuple[str, ColumnRole]] = [
    ("year", _I32),
    ("Census Year", _I32),
    ("tract_percent_minority", _I8),
    ("borrower_income_ratio_rent_affordability_category", _I8),
    ("borrower_race_and_ethnicity", _I8),
    ("borrower_sex", _I8),
    ("co_borrower_race_and_ethnicity", _I8),
    ("co_borrower_sex", _I8),
    ("date_of_mortgage_note", _I8),
    ("enterprise_flag", _I8),
    ("federal_guarantee", _I8),
    ("msa_code", _I8),
    ("number_of_units", _I8),
    ("occupancy_code", _I8),
    ("loan_purpose", _I8),
    ("record_number", _I32),
    ("tract_income_ratio", _I8),
    ("type_of_seller_institution", _I8),
    ("underserved_areas_indicator", _I8),
    ("unit_affordability_category", _I8),
    ("unit_owner_occupied", _I8),
]
FHFA_SF_B_SILVER = spec_from_columns(_SF_B_COLS)

# --- sf_d: SF summary with the lone continuous purchase_price + percent_repurchased ---
_SF_D_COLS: list[tuple[str, ColumnRole]] = [
    ("year", _I32),
    ("Census Year", _I32),
    ("tract_percent_minority", _I8),
    ("amortization_term", _I8),
    ("borrower_income_ratio", _I8),
    ("credit_score", _I8),
    ("enterprise_flag", _I8),
    ("federal_guarantee", _I8),
    ("interest_rate_at_origination", _I8),
    ("ltv_at_origination", _I8),
    ("percent_repurchased", _F32),
    ("portfolio_flag", _I8),
    ("product_type", _I8),
    ("purchase_price", _I64),
    ("loan_purpose", _I8),
    ("record_number", _I32),
    ("loan_term", _I8),
    ("tract_income_ratio", _I8),
]
FHFA_SF_D_SILVER = spec_from_columns(_SF_D_COLS, sentinels={"purchase_price": (999999999,)})

# --- mf_c: multifamily loan-level (geo + continuous-sentinel; 1993-2024, so it
# also carries the HUD-era provisional fields + the bucketed upb_acquisition_range) ---
_MF_C_COLS: list[tuple[str, ColumnRole]] = [
    ("enterprise_flag", _I8),
    ("record_number", _I32),
    ("year", _I32),
    ("Census Year", _I32),
    ("state_code", Fips(2)),
    ("msa_code", Fips(5)),
    ("county", Fips(3)),
    ("census_tract", Fips(6)),
    ("hud_provisional_census_field_1", _I8),
    ("hud_provisional_census_field_2", _F32),
    ("hud_provisional_census_field_3", _F32),
    ("tract_percent_minority", _F32),
    ("tract_median_income", _I64),
    ("local_area_median_income", _I64),
    ("tract_income_ratio", _F32),
    ("area_median_family_income", _I64),
    ("upb_acquisition_range", _I8),
    ("type_of_seller_institution", _I8),
    ("underserved_areas_indicator", _I8),
    ("upb_acquisition", _F64),
    ("loan_purpose", _I8),
    ("federal_guarantee", _I8),
    ("lien_status", _I8),
    ("ltv_at_origination", _F32),
    ("date_of_mortgage_note", _I8),
    ("loan_term", _I16),
    ("number_of_units", _I8),
    ("interest_rate_at_origination", _F32),
    ("note_amount", _I64),
    ("property_value", _F64),
    ("prepayment_penalty_term", _I16),
    ("balloon_payment", _I8),
    ("interest_only_payment", _I8),
    ("negative_amortization", _I8),
    ("other_non_amortizing", _I8),
    ("multifamily_affordable_units_percent", _I16),
    ("construction_method", _I8),
    ("rural_census_tract", _I8),
    ("lower_mississippi_delta_county", _I8),
    ("middle_appalachia_county", _I8),
    ("persistent_poverty_county", _I8),
    ("area_concentrated_poverty", _I8),
    ("high_opportunity_area", _I8),
    ("qoz_census_tract", _I8),
    ("colonias_tract", _I8),
]
_MF_C_SENT = {
    "tract_median_income": (999999,),
    "local_area_median_income": (999999,),
    "area_median_family_income": (999999,),
    "tract_percent_minority": (9999.0,),
    "tract_income_ratio": (9999.0,),
    "note_amount": (999999999,),
    "property_value": (999999999,),
    "interest_rate_at_origination": (99,),
    "ltv_at_origination": (999,),
    # lien_status carries a '.' missing marker in HUD-era years; the Int8 cast
    # (strict=False) maps it to null, so no explicit sentinel is needed.
}
FHFA_MF_C_SILVER = spec_from_columns(_MF_C_COLS, sentinels=_MF_C_SENT)

# --- mf_property_b / mf_unit_b: MF summary files (bucketed Int8 codes + counts) ---
_MF_PROP_B_COLS: list[tuple[str, ColumnRole]] = [
    ("enterprise_flag", _I8),
    ("record_number", _I32),
    ("year", _I32),
    ("Census Year", _I32),
    ("tract_percent_minority", _I8),
    ("tract_income_ratio", _I8),
    ("affordability_category", _I8),
    ("loan_purpose", _I8),
    ("government_insurance", _I8),
    ("total_units", _I32),
    ("hud_provisional_property_amount_1", _I64),
    ("hud_provisional_property_amount_2", _I64),
    ("underserved_areas_indicator", _I8),
    ("date_of_mortgage_note", _I8),
    ("type_of_seller_institution", _I8),
    ("federal_guarantee", _I8),
]
FHFA_MF_PROPERTY_B_SILVER = spec_from_columns(_MF_PROP_B_COLS)

_MF_UNIT_B_COLS: list[tuple[str, ColumnRole]] = [
    ("enterprise_flag", _I8),
    ("record_number", _I32),
    ("year", _I32),
    ("unit_bedrooms", _I8),
    # Float32, not an Int count: the PUDB documents fractional units, which arise when
    # affordability is estimated per 12 CFR 1282.15(e). An Int target truncated 22,275 of
    # 10,342,924 rows (0.215%) across 2008-2024.
    ("unit_number_of_units", _F32),
    ("unit_affordability_level", _I8),
    ("unit_tenant_income_indicator", _I8),
]
FHFA_MF_UNIT_B_SILVER = spec_from_columns(_MF_UNIT_B_COLS)

#: Dataset -> its silver spec. ``save_to_silver`` applies the matching spec.
FHFA_SILVER_SPECS: dict[str, SilverSpec] = {
    "sf_a": FHFA_SF_A_SILVER,
    "sf_b": FHFA_SF_B_SILVER,
    "sf_c": FHFA_SF_C_SILVER,
    "sf_d": FHFA_SF_D_SILVER,
    "mf_c": FHFA_MF_C_SILVER,
    "mf_property_b": FHFA_MF_PROPERTY_B_SILVER,
    "mf_unit_b": FHFA_MF_UNIT_B_SILVER,
}
