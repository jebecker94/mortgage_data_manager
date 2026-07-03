"""FHFA Data Manager - Configuration.

Configuration for FHFA (Federal Housing Finance Agency) data processing pipeline.

This module provides:
    - FHFAConfig: Main configuration class inheriting from MortgageDataConfig
    - ImportOptions: Operational options for data loaders
    - PathsConfig: Legacy compatibility class for path access
    - ensure_parent_dir: Utility function for directory creation

The FHFAConfig class follows the project's configuration inheritance pattern,
supporting environment variable overrides and medallion architecture paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import polars as pl
import typer
from decouple import config
from rich.console import Console

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.types import SilverSpec

_console = Console()


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
        FHFA_CLEAN_DIR: Clean data directory (legacy)
        FHFA_SCHEMAS_DIR: Schema files directory

    Example:
        >>> from mortgage_data_manager.fhfa.config import FHFAConfig
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
    FHFA_CLEAN_DIR: Path = Path(config("FHFA_CLEAN_DIR", default=str(FHFA_DATA_DIR / "clean")))
    FHFA_SCHEMAS_DIR: Path = Path(
        config("FHFA_SCHEMAS_DIR", default=str(MortgageDataConfig.SCHEMAS_DIR / "fhfa"))
    )

    # Backward compatibility aliases (module-level constants expected by other modules)
    PROJECT_DIR = MortgageDataConfig.PROJECT_DIR
    RAW_DIR = FHFA_RAW_DIR
    BRONZE_DIR = FHFA_BRONZE_DIR
    SILVER_DIR = FHFA_SILVER_DIR
    CLEAN_DIR = FHFA_CLEAN_DIR
    SCHEMAS_DIR = FHFA_SCHEMAS_DIR

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
        :meth:`MortgageDataConfig.get_medallion_dir`.

        Args:
            stage: Medallion stage ('raw', 'bronze', 'silver', or 'gold')
            prefix: Data prefix naming the sub-directory

        Returns:
            Path to the prefix sub-directory under the stage

        Example:
            >>> FHFAConfig.get_prefix_dir('silver', 'sf_c')
            PosixPath('/path/to/data/fhfa/silver/sf_c')

        Note:
            ``get_medallion_dir`` is now inherited from
            :class:`MortgageDataConfig` unmodified (signature
            ``get_medallion_dir(subpackage, stage)``).
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
        # Create medallion directories
        for d in (cls.FHFA_RAW_DIR, cls.FHFA_BRONZE_DIR, cls.FHFA_SILVER_DIR):
            d.mkdir(parents=True, exist_ok=True)

        # Create additional directories
        cls.FHFA_CLEAN_DIR.mkdir(parents=True, exist_ok=True)
        cls.FHFA_SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)


# Module-level path constants for backward compatibility
PROJECT_DIR = FHFAConfig.PROJECT_DIR
DATA_ROOT = FHFAConfig.FHFA_DATA_DIR
FHFA_DATA = FHFAConfig.FHFA_DATA_DIR
DATA_DIR = FHFAConfig.FHFA_DATA_DIR
RAW_DIR = FHFAConfig.RAW_DIR
BRONZE_DIR = FHFAConfig.BRONZE_DIR
SILVER_DIR = FHFAConfig.SILVER_DIR
CLEAN_DIR = FHFAConfig.CLEAN_DIR
SCHEMAS_DIR = FHFAConfig.SCHEMAS_DIR


@dataclass(frozen=True)
class PathsConfig:
    """Legacy path configuration class for backward compatibility.

    Prefer using FHFAConfig class attributes or module-level constants instead.

    Example:
        >>> paths = PathsConfig.from_env()
        >>> paths.raw_dir
        PosixPath('/path/to/data/fhfa/raw')
    """

    project_dir: Path
    data_dir: Path
    raw_dir: Path
    clean_dir: Path

    @classmethod
    def from_env(cls) -> PathsConfig:
        """Create PathsConfig using FHFAConfig class."""
        return cls(
            project_dir=FHFAConfig.PROJECT_DIR,
            data_dir=FHFAConfig.FHFA_DATA_DIR,
            raw_dir=FHFAConfig.RAW_DIR,
            clean_dir=FHFAConfig.CLEAN_DIR,
        )


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
    """Ensure that the parent directory of ``path`` exists."""
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


# --------------------------------------------------------------------------- #
# Per-dataset silver dtype specs (close-to-final types; see core/types.py + the
# 2026-06-27 silver type audit). FHFA has 7 datasets with DIFFERENT schemas, and
# the SAME standardized name can be continuous in the loan-level file but a
# bucketed code in the summary files (e.g. tract_income_ratio is Float32 in sf_c
# but an Int8 bucket in sf_a/sf_b), so each dataset gets its OWN spec, derived
# data-driven from its full-year domain (investigations/scripts/fhfa_domain_profile.py).
# This replaces the former global SENTINELS_TO_NULL dict, which (a) silently never
# matched the MF datasets (their columns were not renamed to snake_case) and
# (b) could not distinguish the continuous vs bucketed meaning of a shared name.
#
# Geography (state_code/county/census_tract/msa_code) -> zero-padded Utf8 in the
# loan-level files (sf_c/mf_c), recovering the leading zeros Int storage drops.
# In the SUMMARY files (sf_a/sf_b) msa_code is a 0/1 metro flag, NOT a geo code,
# so it stays Int8 there. Category codes keep their in-band "missing" markers
# (9/99) live, HMDA-style. ``date_of_mortgage_note`` is a 1/2 category, not a date.
# --------------------------------------------------------------------------- #
_I8, _I16, _I32, _I64, _F32, _F64 = (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.Float32, pl.Float64)
#: Geographic identifier -> zero-pad width (Utf8), for the loan-level files.
_GEO_WIDTH = {"state_code": 2, "county": 3, "census_tract": 6, "msa_code": 5}
_GEO = "geo"  # sentinel marker in a column list -> zero-padded Utf8 via _GEO_WIDTH


def _fhfa_spec(
    columns: list[tuple[str, Any]],
    sentinels: dict[str, tuple] | None = None,
) -> SilverSpec:
    """Build a per-dataset FHFA SilverSpec from an ordered ``(name, dtype)`` list.

    ``dtype`` is a polars dtype (-> casts + target) or the marker ``_GEO`` (->
    zero-padded ``Utf8`` of the :data:`_GEO_WIDTH` width, in ``fips`` + target).
    The ordered list is the single source of truth for both the casts and the
    target schema, so conform-to-target unifies the cross-year column drift (an
    sf_c year may carry 42 of the 68 union columns; absent ones are null-filled).
    ``sentinels`` (same snake_case keys) null-map before the casts.
    """
    casts: dict[str, Any] = {}
    fips: dict[str, int] = {}
    target: dict[str, Any] = {}
    for name, dt in columns:
        if dt is _GEO:
            fips[name] = _GEO_WIDTH[name]
            target[name] = pl.String
        else:
            casts[name] = dt
            target[name] = dt
    return SilverSpec(sentinels=sentinels or {}, fips=fips, casts=casts, target=pl.Schema(target))


# --- sf_c: single-family loan-level (the geo + continuous-sentinel flagship) ---
_SF_C_COLS: list[tuple[str, object]] = [
    ("year", _I32),
    ("Census Year", _I32),
    ("tract_median_income", _I32),
    ("tract_percent_minority", _F32),
    ("local_area_median_income", _I32),
    ("upb_acquisition", _I32),
    ("age_borrower", _I16),
    ("age_co_borrower", _I16),
    ("area_median_family_income", _I32),
    ("borrower_ethnicity", _I8),
    ("borrower_income_ratio", _F32),
    ("borrower_race_1", _I8),
    ("borrower_race_2", _I8),
    ("borrower_race_3", _I8),
    ("borrower_race_4", _I8),
    ("borrower_race_5", _I8),
    ("borrower_sex", _I8),
    ("borrower_annual_income", _I64),
    ("census_tract", _GEO),
    ("co_borrower_ethnicity", _I8),
    ("co_borrower_race_1", _I8),
    ("co_borrower_race_2", _I8),
    ("co_borrower_race_3", _I8),
    ("co_borrower_race_4", _I8),
    ("co_borrower_race_5", _I8),
    ("co_borrower_sex", _I8),
    ("county", _GEO),
    ("enterprise_flag", _I8),
    ("federal_guarantee", _I8),
    ("first_time_home_buyer", _I8),
    ("hoepa_status", _I8),
    ("lien_status", _I8),
    ("msa_code", _GEO),
    ("number_of_borrowers", _I8),
    ("occupancy_code", _I8),
    ("property_type", _I8),
    ("loan_purpose", _I8),
    ("rate_spread", _F32),
    ("record_number", _I32),
    ("tract_income_ratio", _F32),
    ("state_code", _GEO),
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
    ("note_amount", _I32),
    ("number_of_units", _I8),
    ("persistent_poverty_county", _I8),
    ("preapproval", _I8),
    ("property_value", _I32),
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
FHFA_SF_C_SILVER = _fhfa_spec(_SF_C_COLS, _SF_C_SENT)

# --- sf_a / sf_b: single-family SUMMARY files (all bucketed Int8 codes; msa_code
# is a 0/1 metro flag, not geo; the "9" buckets are kept, so no sentinels) ---
_SF_A_COLS: list[tuple[str, object]] = [
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
FHFA_SF_A_SILVER = _fhfa_spec(_SF_A_COLS)

_SF_B_COLS: list[tuple[str, object]] = [
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
FHFA_SF_B_SILVER = _fhfa_spec(_SF_B_COLS)

# --- sf_d: SF summary with the lone continuous purchase_price + percent_repurchased ---
_SF_D_COLS: list[tuple[str, object]] = [
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
    ("purchase_price", _I32),
    ("loan_purpose", _I8),
    ("record_number", _I32),
    ("loan_term", _I8),
    ("tract_income_ratio", _I8),
]
FHFA_SF_D_SILVER = _fhfa_spec(_SF_D_COLS, {"purchase_price": (999999999,)})

# --- mf_c: multifamily loan-level (geo + continuous-sentinel; 1993-2024, so it
# also carries the HUD-era provisional fields + the bucketed upb_acquisition_range) ---
_MF_C_COLS: list[tuple[str, object]] = [
    ("enterprise_flag", _I8),
    ("record_number", _I32),
    ("year", _I32),
    ("Census Year", _I32),
    ("state_code", _GEO),
    ("msa_code", _GEO),
    ("county", _GEO),
    ("census_tract", _GEO),
    ("hud_provisional_census_field_1", _I8),
    ("hud_provisional_census_field_2", _F32),
    ("hud_provisional_census_field_3", _F32),
    ("tract_percent_minority", _F32),
    ("tract_median_income", _I32),
    ("local_area_median_income", _I32),
    ("tract_income_ratio", _F32),
    ("area_median_family_income", _I32),
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
    ("note_amount", _I32),
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
FHFA_MF_C_SILVER = _fhfa_spec(_MF_C_COLS, _MF_C_SENT)

# --- mf_property_b / mf_unit_b: MF summary files (bucketed Int8 codes + counts) ---
_MF_PROP_B_COLS: list[tuple[str, object]] = [
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
    ("hud_provisional_property_amount_1", _I32),
    ("hud_provisional_property_amount_2", _I32),
    ("underserved_areas_indicator", _I8),
    ("date_of_mortgage_note", _I8),
    ("type_of_seller_institution", _I8),
    ("federal_guarantee", _I8),
]
FHFA_MF_PROPERTY_B_SILVER = _fhfa_spec(_MF_PROP_B_COLS)

_MF_UNIT_B_COLS: list[tuple[str, object]] = [
    ("enterprise_flag", _I8),
    ("record_number", _I32),
    ("year", _I32),
    ("unit_bedrooms", _I8),
    ("unit_number_of_units", _I32),
    ("unit_affordability_level", _I8),
    ("unit_tenant_income_indicator", _I8),
]
FHFA_MF_UNIT_B_SILVER = _fhfa_spec(_MF_UNIT_B_COLS)

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


def validate_datasets(datasets: list[str]) -> None:
    """Validate that all datasets are recognized FHFA datasets.

    Args:
        datasets: List of dataset identifiers to validate.

    Raises:
        typer.Exit: If any dataset is invalid.
    """
    invalid = [ft for ft in datasets if ft not in DATASET_MAP]
    if invalid:
        _console.print(f"[red]Error: Invalid datasets:[/red] {', '.join(invalid)}")
        _console.print(f"[yellow]Valid types:[/yellow] {', '.join(VALID_DATASETS)}")
        raise typer.Exit(code=1)


def validate_years(years: list[int], min_year: int = 1993, max_year: int = 2024) -> None:
    """Validate that all years fall within the supported FHFA year range.

    The lower bound is 1993 because the HUD-era multifamily backfill extends the
    GSE PUDB series back to 1993 (the modern FHFA enterprise data begins in
    2008; the 1993-2007 years are served by the HUD-era pipeline).

    Args:
        years: List of years to validate.
        min_year: Minimum valid year. Defaults to 1993.
        max_year: Maximum valid year. Defaults to 2024.

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
