"""Configuration for the HUD multifamily housing subpackage.

HUD publishes a family of FHA multifamily and assisted-housing extracts at
https://www.hud.gov/hud-partners/multifamily-data as monthly point-in-time
Excel/Access snapshots. This subpackage ingests the tabular extracts into the
medallion layout (raw → bronze → silver).

The files fall into two families joined by one bridge:

- **insured** — FHA-insured multifamily *mortgages*, keyed by the 8-character
  FHA project number (a.k.a. "HUD project number"). Active and terminated
  insured mortgages are disjoint on this key; Section 202 direct loans use an
  *alphanumeric* project-number namespace (e.g. ``053HH018``) disjoint from the
  numeric insured one.
- **assisted** — Section 8 / assisted-housing contracts, properties, rents,
  renewals, and the active portfolio, keyed by the 9-digit REMS ``property_id``.
- **reference** — the Section-of-the-Act (SoA) code lookup.

``property_id`` is the universal spine; ``fha_number`` links the insured subset
(≈21% of assisted properties also carry an FHA-insured mortgage). The
property↔FHA bridge lives in the address file and the active-portfolio FHA sheet.

Environment Variables:
    HUD_MF_DATA_DIR: Root directory (default: {DATA_DIR}/hud_mf)
    HUD_MF_RAW_DIR / HUD_MF_BRONZE_DIR / HUD_MF_SILVER_DIR: Per-layer overrides
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final, TypedDict

import typer
from decouple import config

from mortgage_data_manager.core.cli.formatting import console as _console
from mortgage_data_manager.core.config import MortgageDataConfig

# ---------------------------------------------------------------------------
# Source pages and files
# ---------------------------------------------------------------------------

#: HUD multifamily-data index pages. Re-scrape these if a hard-coded artifact
#: URL starts returning 404 (HUD periodically renames/rolls files).
INDEX_PAGES: Final[tuple[str, ...]] = (
    "https://www.hud.gov/hud-partners/multifamily-data",
    "https://www.hud.gov/hud-partners/multifamily-preservation",
    "https://www.hud.gov/hud-partners/multifamily-assist-section8-database",
    "https://www.hud.gov/hud-partners/multifamily-fhasl-active",
    "https://www.hud.gov/hud-partners/multifamily-202-direct-loans",
)

#: Index page carrying the (monthly, date-stamped) Section 202 direct-loans link.
SECTION202_INDEX_URL: Final[str] = "https://www.hud.gov/hud-partners/multifamily-202-direct-loans"

#: Source artifact downloads: friendly local filename → URL. One file can feed
#: more than one logical table (the active-portfolio workbook feeds two), so the
#: download layer iterates these unique files while :data:`DATASET_MAP` maps
#: logical tables back to their source via the ``source`` key.
#:
#: The Section 202 URL is date-stamped in the filename and HUD rolls it forward
#: monthly; ``download.resolve_section202_url`` re-scrapes the index page when
#: the pinned URL 404s. The Access ``.zip`` is deliberately omitted — it bundles
#: the exact same two Section 8 Excel extracts we already fetch plus a 65 MB
#: ``.accdb``, so it is pure redundancy.
SOURCE_FILES: Final[dict[str, str]] = {
    "active_insured_mortgages.xlsx": "https://www.hud.gov/sites/default/files/Housing/documents/FHA-BF90-RM-A.xlsx",
    "terminated_insured_mortgages.xlsx": "https://www.hud.gov/sites/default/files/Housing/documents/FHA-BF90-RM-T.xlsx",
    "soa_list.xlsx": "https://www.hud.gov/sites/documents/soa_list.xlsx",
    "202_direct_loans.xlsx": "https://www.hud.gov/sites/default/files/Housing/documents/202directloans06012026.xlsx",
    "mf_assistance_sec8_contracts.xlsx": "https://www.hud.gov/sites/dfiles/Housing/documents/MF-Assistance-Sec8-Contracts1.xlsx",
    "mf_properties_assistance_sec8.xlsx": "https://www.hud.gov/sites/dfiles/Housing/documents/MF-Properties-with-Assistance-Sec8-Contracts1.xlsx",
    "contract_renewal_all.xls": "https://www.hud.gov/sites/dfiles/Housing/documents/contractrenewaallcontracts.xls",
    "contracts_rent_utility.xlsx": "https://www.hud.gov/sites/dfiles/Housing/documents/contractsrentutilityamt.xlsx",
    "active_portfolio_property.xlsx": "https://www.hud.gov/sites/dfiles/Housing/documents/activeportfoliopropdata.xlsx",
    "insured_active_addresses.xlsx": "https://www.hud.gov/sites/dfiles/Housing/documents/InsuredActiveMultifamilyFHAPropertyAddresses.xlsx",
}

#: Data Element Dictionaries (PDFs) — ground-truth field definitions for the
#: assisted-housing tables. Kept as raw documentation under raw/dictionaries/;
#: they are prose, not tabular, so they are not converted to parquet.
DEDS: Final[dict[str, str]] = {
    "ded_contract_renewal_info.pdf": "https://www.hud.gov/sites/documents/contractrenewalinfoded.pdf",
    "ded_contracts_rent_utility.pdf": "https://www.hud.gov/sites/documents/contractsrentutilityallded.pdf",
    "ded_active_mf_property.pdf": "https://www.hud.gov/sites/documents/activemfpropertypded.pdf",
}


# ---------------------------------------------------------------------------
# Logical-table registry
# ---------------------------------------------------------------------------


class TableSpec(TypedDict):
    """Specification for one logical HUD-MF table.

    Attributes:
        source: Friendly source filename (key into :data:`SOURCE_FILES`).
        family: ``"insured"``, ``"assisted"`` or ``"reference"``.
        sheets: Sheet names to read and vertically union, or ``None`` for the
            first/only sheet.
        header_row: 0-based row index of the real header (several HUD files
            carry a title/count or instruction block above it).
        keys: Identifier columns (snake_case) used as join keys downstream.
        description: One-line "what this is".
    """

    source: str
    family: str
    sheets: tuple[str, ...] | None
    header_row: int
    keys: tuple[str, ...]
    description: str


#: Registry of the 11 logical tables. ``DATASET_MAP`` is the single source of
#: truth; ``VALID_DATASETS`` is derived from it. Column names are normalized to
#: snake_case by the bronze layer, so ``keys`` are given in snake_case.
DATASET_MAP: Final[dict[str, TableSpec]] = {
    # -- insured FHA multifamily mortgages (FHA-number spine) ----------------
    "insured_mortgages_active": {
        "source": "active_insured_mortgages.xlsx",
        "family": "insured",
        "sheets": None,
        "header_row": 1,  # row 0 is a "FHA_BF90_RM_A | <count>" banner
        "keys": ("hud_project_number",),
        "description": "Active FHA-insured multifamily mortgages (1 row/FHA#).",
    },
    "insured_mortgages_terminated": {
        "source": "terminated_insured_mortgages.xlsx",
        "family": "insured",
        "sheets": None,
        "header_row": 1,  # row 0 is a "FHA_BF90_RM_T | <count>" banner
        "keys": ("hud_project_number",),
        "description": "Terminated FHA-insured multifamily mortgages (disjoint from active).",
    },
    "section202_direct_loans": {
        "source": "202_direct_loans.xlsx",
        "family": "insured",
        "sheets": None,
        "header_row": 0,
        "keys": ("project_fha_number", "project_fha_number_suffix"),
        "description": "Section 202 elderly/disabled direct loans (alphanumeric FHA#).",
    },
    "insured_active_addresses": {
        "source": "insured_active_addresses.xlsx",
        "family": "insured",
        "sheets": None,
        "header_row": 0,
        "keys": ("property_id", "fha_number"),
        "description": "Insured active multifamily property addresses (property↔FHA bridge).",
    },
    # -- assisted / Section 8 housing (property_id spine) -------------------
    "sec8_contracts": {
        "source": "mf_assistance_sec8_contracts.xlsx",
        "family": "assisted",
        "sheets": None,
        "header_row": 0,
        "keys": ("contract_number", "property_id"),
        "description": "Section 8 / assisted-housing contracts (TRACS dates, rents, FMR).",
    },
    "sec8_properties": {
        "source": "mf_properties_assistance_sec8.xlsx",
        "family": "assisted",
        "sheets": None,
        "header_row": 0,
        "keys": ("property_id",),
        "description": "Section 8 / assisted-housing properties (owner, mgmt agent, flags).",
    },
    "contract_rents_utility": {
        "source": "contracts_rent_utility.xlsx",
        "family": "assisted",
        # Two near-disjoint halves of one long table → vertically unioned.
        "sheets": (
            "Contracts_with_Rent_amt_and_Uti",
            "Contracts_w_Rent_amt_and_Uti -2",
        ),
        "header_row": 0,
        "keys": ("property_id", "contract_number", "assistance_bedroom_count"),
        "description": "Contract rents + FMR + utility allowance, one row per bedroom size.",
    },
    "contract_renewals": {
        "source": "contract_renewal_all.xls",
        "family": "assisted",
        "sheets": None,
        "header_row": 0,
        "keys": ("contract_number", "property_id", "renewal_id"),
        "description": "Section 8 contract renewal / TRACS lifecycle, all contracts.",
    },
    "portfolio_property": {
        "source": "active_portfolio_property.xlsx",
        "family": "assisted",
        "sheets": ("Step_01_Property_Level_data",),
        "header_row": 0,
        "keys": ("property_id",),
        "description": "Full active multifamily portfolio, property level (superset).",
    },
    "portfolio_property_fha": {
        "source": "active_portfolio_property.xlsx",
        "family": "assisted",
        "sheets": ("All active Properties with FHA ",),  # trailing space is intentional
        "header_row": 0,
        "keys": ("property_id", "fha_number"),
        "description": "Active-portfolio property↔FHA-number bridge with SoA.",
    },
    # -- reference ----------------------------------------------------------
    "soa_codes": {
        "source": "soa_list.xlsx",
        "family": "reference",
        "sheets": ("SoA List",),
        "header_row": 3,  # rows 0-2 are title + instructions; data has banner rows
        "keys": ("soa",),
        "description": "Section-of-the-Act (SoA) code lookup dimension.",
    },
}

#: Sorted list view of valid logical-table identifiers (single source: DATASET_MAP).
VALID_DATASETS: Final[list[str]] = list(DATASET_MAP.keys())

#: Tables whose header row must be promoted manually rather than via calamine's
#: ``header_row`` inference. The SoA lookup sheet has instruction/title rows with
#: a single populated cell above its real header; calamine's width detection
#: latches onto those sparse rows, so we read the sheet headerless and slice the
#: real header row ourselves.
MANUAL_HEADER_TABLES: Final[frozenset[str]] = frozenset({"soa_codes"})

#: Identifier-like columns that must stay strings (zero-padded codes, ids, zips)
#: even when a sheet happens to hold all-numeric values. Matched against the
#: snake_case column name by membership or suffix.
IDENTIFIER_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "hud_project_number",
        "project_fha_number",
        "project_fha_number_suffix",
        "fha_number",
        "property_id",
        "contract_number",
        "rems_proerty_id",  # sic: HUD's own typo in the 202 file header
        "soa",
        "soa_code",
        "section_of_act_code",
        "section_act_code",
        "property_zip",
        "zip",
        "zip_code",
        "zip4_code",
        "owner_zip_code",
        "county_code",
        "msa_code",
        "congressional_district_code",
        "project_zip_code",
        "project_zip_code_plus_4",
    }
)


def validate_datasets(datasets: list[str]) -> None:
    """Validate that all identifiers are recognized HUD-MF tables.

    Args:
        datasets: Logical-table identifiers to validate.

    Raises:
        typer.Exit: If any identifier is invalid.
    """
    invalid = [d for d in datasets if d not in DATASET_MAP]
    if invalid:
        _console.print(f"[red]Error: Invalid datasets:[/red] {', '.join(invalid)}")
        _console.print(f"[yellow]Valid datasets:[/yellow] {', '.join(VALID_DATASETS)}")
        raise typer.Exit(code=1)


def resolve_datasets(datasets: list[str] | None) -> list[str]:
    """Default to all tables; validate any explicit subset.

    Args:
        datasets: Logical-table identifiers, or ``None`` for all of
            :data:`VALID_DATASETS`.

    Returns:
        Validated list of logical-table identifiers.

    Raises:
        ValueError: If an unknown identifier is given.
    """
    if datasets is None:
        return list(VALID_DATASETS)
    unknown = [d for d in datasets if d not in DATASET_MAP]
    if unknown:
        raise ValueError(f"Unknown HUD-MF datasets: {unknown}; valid: {VALID_DATASETS}")
    return list(datasets)


def source_files_for(datasets: list[str] | None) -> dict[str, str]:
    """Return the unique source-file → URL map needed for ``datasets``.

    Args:
        datasets: Logical-table identifiers, or ``None`` for all.

    Returns:
        Mapping of friendly source filename to download URL (deduplicated,
        since several tables can share one workbook).
    """
    wanted = resolve_datasets(datasets)
    sources = {DATASET_MAP[d]["source"] for d in wanted}
    return {name: SOURCE_FILES[name] for name in SOURCE_FILES if name in sources}


# ---------------------------------------------------------------------------
# Column-name normalization
# ---------------------------------------------------------------------------

_SNAKE_NONALNUM = re.compile(r"[^0-9a-zA-Z]+")
_SNAKE_REPEAT = re.compile(r"_+")


def to_snake(name: str) -> str:
    """Normalize a raw HUD column name to snake_case.

    Lowercases, collapses any run of non-alphanumeric characters (spaces,
    slashes, parentheses) to a single underscore, and trims leading/trailing
    underscores. Leading-digit names (e.g. ``0BR_count`` → ``0br_count``) are
    left intact — Polars accepts them.

    Args:
        name: Raw column name as read from the workbook.

    Returns:
        snake_case column name.
    """
    s = _SNAKE_NONALNUM.sub("_", str(name).strip())
    s = _SNAKE_REPEAT.sub("_", s).strip("_")
    return s.lower()


# ---------------------------------------------------------------------------
# HudMfConfig
# ---------------------------------------------------------------------------


class HudMfConfig(MortgageDataConfig):
    """Configuration for the HUD multifamily pipeline.

    Data lands under ``data/hud_mf/`` matching the flat top-level layout of
    sibling subpackages. Distinct from the ``hud`` subpackage (USPS ZIP↔Census
    crosswalk) and the ``fha`` subpackage (single-family + HECM loan snapshots).
    """

    SUBPACKAGE: str = "hud_mf"

    HUD_MF_DATA_DIR: Path = Path(
        config(
            "HUD_MF_DATA_DIR",
            default=str(MortgageDataConfig.get_subpackage_data_dir("hud_mf")),
        )
    )
    HUD_MF_RAW_DIR: Path = Path(config("HUD_MF_RAW_DIR", default=str(HUD_MF_DATA_DIR / "raw")))
    HUD_MF_BRONZE_DIR: Path = Path(
        config("HUD_MF_BRONZE_DIR", default=str(HUD_MF_DATA_DIR / "bronze"))
    )
    HUD_MF_SILVER_DIR: Path = Path(
        config("HUD_MF_SILVER_DIR", default=str(HUD_MF_DATA_DIR / "silver"))
    )

    @classmethod
    def ensure_directories(cls) -> None:
        """Create raw/bronze/silver directories if they don't exist."""
        for d in (cls.HUD_MF_RAW_DIR, cls.HUD_MF_BRONZE_DIR, cls.HUD_MF_SILVER_DIR):
            d.mkdir(parents=True, exist_ok=True)

    # -- raw layer ----------------------------------------------------------

    @classmethod
    def raw_path(cls, source_filename: str) -> Path:
        """Local raw path for a source artifact (e.g. ``soa_list.xlsx``)."""
        return cls.HUD_MF_RAW_DIR / source_filename

    @classmethod
    def raw_dictionary_path(cls, pdf_name: str) -> Path:
        """Local raw path for a Data Element Dictionary PDF."""
        return cls.HUD_MF_RAW_DIR / "dictionaries" / pdf_name

    @classmethod
    def manifest_path(cls) -> Path:
        """Path to the download manifest (per-file URL/Last-Modified/bytes)."""
        return cls.HUD_MF_RAW_DIR / "download_manifest.json"

    # -- bronze / silver layers --------------------------------------------

    @classmethod
    def bronze_path(cls, dataset: str) -> Path:
        """Bronze typed-parquet output path for a logical table."""
        return cls.HUD_MF_BRONZE_DIR / f"{dataset}.parquet"

    @classmethod
    def silver_path(cls, dataset: str) -> Path:
        """Silver cleaned-parquet output path for a logical table."""
        return cls.HUD_MF_SILVER_DIR / f"{dataset}.parquet"
