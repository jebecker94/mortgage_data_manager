"""County FIPS enrichment and data standardization utilities for FHA datasets."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl
import yaml

if TYPE_CHECKING:
    from polars.expr.whenthen import ChainedThen, Then

from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)

_COUNTY_MAPPINGS_PATH = Path(__file__).parent / "county_name_mappings.yaml"


@lru_cache(maxsize=1)
def _load_county_name_mappings() -> dict[str, Any]:
    """Load the FHA county-name mapping YAML once and cache the result."""
    with open(_COUNTY_MAPPINGS_PATH) as f:
        return yaml.safe_load(f) or {}


COUNTY_FIPS_OVERRIDES: dict[tuple[str, str], str] = {
    ("DC", "WASHINGTON"): "11001",
    ("DC", "DISTRICT OF COLUMBIA"): "11001",
    # Independent cities that get confused with their adjacent counties after
    # the " COUNTY" suffix is stripped by standardize_county_names()
    # St. Louis City (29510) vs St. Louis County (29189)
    ("MO", "ST. LOUIS"): "29189",
    ("MO", "ST. LOUIS CITY"): "29510",
    ("MO", "SAINT LOUIS"): "29189",
    ("MO", "SAINT LOUIS CITY"): "29510",
    # Baltimore City (24510) vs Baltimore County (24005)
    ("MD", "BALTIMORE"): "24005",
    ("MD", "BALTIMORE CITY"): "24510",
    # Virginia independent cities (addfips defaults to city, not county)
    # Bedford: City (51515, dissolved 2013) vs County (51019)
    ("VA", "BEDFORD"): "51019",
    ("VA", "BEDFORD CITY"): "51515",
    # Fairfax: City (51600) vs County (51059)
    ("VA", "FAIRFAX"): "51059",
    ("VA", "FAIRFAX CITY"): "51600",
    # Franklin: City (51620) vs County (51067)
    ("VA", "FRANKLIN"): "51067",
    ("VA", "FRANKLIN CITY"): "51620",
    # Roanoke: City (51770) vs County (51161)
    ("VA", "ROANOKE"): "51161",
    ("VA", "ROANOKE CITY"): "51770",
    # Richmond: City (51760) vs County (51159)
    ("VA", "RICHMOND"): "51159",
    ("VA", "RICHMOND CITY"): "51760",
    # US Virgin Islands — addfips has no VI mappings at all.
    # St. Croix (78010), St. John (78020), St. Thomas (78030).
    # The ST → ST. prefix transform in standardize_county_names() handles
    # the "ST CROIX" → "ST. CROIX" rewrite, so we only need the dot-form
    # plus the SAINT-prefixed forms (which the transform does not touch).
    ("VI", "ST. CROIX"): "78010",
    ("VI", "SAINT CROIX"): "78010",
    ("VI", "ST. JOHN"): "78020",
    ("VI", "SAINT JOHN"): "78020",
    ("VI", "ST. THOMAS"): "78030",
    ("VI", "SAINT THOMAS"): "78030",
    # Alaska — Chugach Census Area (02063) was carved out of Valdez-Cordova
    # in 2019 alongside Copper River (02066). addfips's bundled data
    # predates the split and knows Copper River but not Chugach; map the
    # name explicitly here. If a future addfips release adds Chugach this
    # override stays correct.
    ("AK", "CHUGACH"): "02063",
}


def standardize_county_names(
    df: pl.LazyFrame,
    county_col: str = "Property County",
    state_col: str = "Property State",
) -> pl.LazyFrame:
    """Standardise county names so they align with the FIPS dataset.

    Mapping tables for state corrections and county renames live in
    ``county_name_mappings.yaml`` (sibling to this module) and reflect
    FHA-specific quirks; they are not suitable for deployment in other
    projects. The trailing ``" COUNTY"`` suffix strip and ``ST ``/``STE ``
    prefix rewrites are generic transforms applied here in code.

    Args:
        df: The snapshot data containing county information.
        county_col: Column containing county names to standardise.
        state_col: Column containing the two-letter state abbreviation.

    Returns:
        A ``LazyFrame`` with harmonised county naming.
    """
    logger.info("Standardizing county names...")

    mappings = _load_county_name_mappings()

    # Absence is null, not "". The string spellings of a null ("nan", "none")
    # and the empty string all collapse to it; is_in() on a null yields null, so
    # a genuine null falls through the otherwise branch and stays null after
    # to_uppercase().
    df = df.with_columns(
        pl.when(pl.col(county_col).str.to_lowercase().is_in(["nan", "none", ""]))
        .then(pl.lit(None, dtype=pl.String))
        .otherwise(pl.col(county_col).str.to_uppercase())
        .alias(county_col)
    )

    # State corrections: when (county, state) matches a known mismatch, fix the state.
    for fix in mappings.get("state_corrections", []):
        df = df.with_columns(
            pl.when(
                (pl.col(county_col) == fix["county"]) & (pl.col(state_col) == fix["from_state"])
            )
            .then(pl.lit(fix["to_state"]))
            .otherwise(pl.col(state_col))
            .alias(state_col)
        )

    # County renames: build a single chained when/then expression that mirrors
    # the previous hand-written chain, then append the trailing-" COUNTY" strip
    # as the final fall-through branch (matches the prior ordering exactly).
    rename_branches = mappings.get("county_renames", []) or []
    expr: Then | ChainedThen | None = None
    for rule in rename_branches:
        cond = (pl.col(county_col) == rule["from"]) & (
            pl.col(state_col).is_in(list(rule["states"]))
        )
        expr = (
            pl.when(cond).then(pl.lit(rule["to"]))
            if expr is None
            else expr.when(cond).then(pl.lit(rule["to"]))
        )

    suffix_cond = pl.col(county_col).str.contains(" COUNTY$")
    suffix_value = pl.col(county_col).str.replace(" COUNTY$", "")
    expr = (
        pl.when(suffix_cond).then(suffix_value)
        if expr is None
        else expr.when(suffix_cond).then(suffix_value)
    )

    df = df.with_columns(expr.otherwise(pl.col(county_col)).alias(county_col))

    # Handle common prefixes after specific cases
    df = df.with_columns(
        pl.when(pl.col(county_col).str.starts_with("ST "))
        .then(pl.concat_str([pl.lit("ST. "), pl.col(county_col).str.slice(3)]))
        .when(pl.col(county_col).str.starts_with("STE "))
        .then(pl.concat_str([pl.lit("SAINTE "), pl.col(county_col).str.slice(4)]))
        .otherwise(pl.col(county_col))
        .alias(county_col)
    )

    return df


#: Cap on how many unresolved (state, county) pairs a single warning names, so a
#: pathological vintage cannot flood the log.
_UNRESOLVED_LOG_LIMIT = 25


def _format_unresolved(pairs: list[tuple[str, str]]) -> str:
    """Render unresolved (state, county) pairs for one log line, capped in length."""
    shown = sorted(set(pairs))
    suffix = ""
    if len(shown) > _UNRESOLVED_LOG_LIMIT:
        suffix = f", ... (+{len(shown) - _UNRESOLVED_LOG_LIMIT} more)"
        shown = shown[:_UNRESOLVED_LOG_LIMIT]
    return ", ".join(f"({state}, {county})" for state, county in shown) + suffix


def add_county_fips(
    df: pl.LazyFrame,
    state_col: str = "Property State",
    county_col: str = "Property County",
    fips_col: str = "FIPS",
    state_fips_col: str = "state_fips",
) -> pl.LazyFrame:
    """Add county and state FIPS codes to a dataset with state and county columns.

    ``state_fips_col`` is the leading two digits of ``fips_col``, so the two are
    null together. It exists because the only geography FHA publishes for the
    state is a postal abbreviation, which cannot join a FIPS-keyed source (e.g.
    pre-2018 HMDA, whose ``state_code`` is the 2-digit FIPS) directly.

    Args:
        df: Dataset containing the state and county columns to enrich.
        state_col: Name of the state column.
        county_col: Name of the county column.
        fips_col: Name of the output column that will receive the concatenated FIPS code.
        state_fips_col: Name of the output column for the 2-digit state FIPS.

    Returns:
        A ``LazyFrame`` containing ``fips_col`` (5-digit county FIPS) and
        ``state_fips_col`` (2-digit state FIPS).
    """
    logger.info("Starting FIPS code addition process...")

    # Standardize the main dataframe's county names. The unique pairs derived
    # below inherit this standardisation, so no second pass is needed.
    logger.info("Standardizing main dataframe county names...")
    df = standardize_county_names(df, state_col=state_col, county_col=county_col)

    logger.info("Getting unique county/state pairs...")
    unique_pairs = df.select([state_col, county_col]).unique().collect()

    if unique_pairs.is_empty():
        logger.warning("No valid county/state pairs found for FIPS lookup")
        return df.with_columns(
            pl.lit(None).cast(pl.Utf8).alias(fips_col),
            pl.lit(None).cast(pl.Utf8).alias(state_fips_col),
        )

    logger.info("Generating FIPS codes for %d unique counties...", len(unique_pairs))
    import addfips

    af = addfips.AddFIPS()
    fips_values: list[str | None] = []
    unresolved: list[tuple[str, str]] = []
    for state, county in unique_pairs.iter_rows():
        # An absent county is not a lookup failure -- nothing to resolve, so it
        # gets a null FIPS without being reported below.
        if not county:
            fips_values.append(None)
            continue
        # Check overrides first (handles independent cities like St. Louis, Baltimore)
        override_key = (state.upper() if state else "", county.upper())
        if override_key in COUNTY_FIPS_OVERRIDES:
            fips_values.append(COUNTY_FIPS_OVERRIDES[override_key])
            continue
        fips = af.get_county_fips(county, state)
        fips_values.append(fips)
        if fips is None:
            unresolved.append((state or "", county))

    if unresolved:
        # Name the misses, so they are not mistaken for an absent county: they are either a
        # county-name spelling addfips does not carry (retired Alaska census areas) or a
        # state/county contradiction in the source, and both are fixable in
        # county_name_mappings.yaml / COUNTY_FIPS_OVERRIDES once seen.
        logger.warning(
            "addfips left %d of %d (state, county) pair(s) unresolved; %s null for: %s",
            len(unresolved),
            len(unique_pairs),
            fips_col,
            _format_unresolved(unresolved),
        )

    county_map = unique_pairs.with_columns(pl.Series(fips_col, fips_values, dtype=pl.Utf8))

    logger.info("Joining FIPS codes back to main dataframe...")
    df = df.join(county_map.lazy(), on=[state_col, county_col], how="left")

    # State FIPS is the county code's own prefix rather than a second lookup, so
    # the two columns cannot disagree and are null together.
    return df.with_columns(pl.col(fips_col).str.slice(0, 2).alias(state_fips_col))
