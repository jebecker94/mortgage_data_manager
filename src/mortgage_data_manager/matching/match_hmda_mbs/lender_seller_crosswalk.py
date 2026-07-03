"""Lender/Seller Crosswalk Analysis.

Identifies HMDA lenders and UMBS sellers that frequently match but have
different names. These represent mergers, acquisitions, rebrands, affiliates,
and DBAs worth documenting.

This module focuses on Retail channel records where lender == seller is expected.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal

import polars as pl
import yaml

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.matching.match_hmda_mbs.config import HMDAMBSMatchingConfig as Config

logger = get_logger(__name__)


def light_clean_name(name: str | None) -> str:
    """Lightly clean name for similarity comparison.

    Only uppercases and removes commas/periods, preserving most of the original.
    """
    if name is None:
        return ""
    name = name.upper().strip()
    name = re.sub(r"[,.]", "", name)
    name = re.sub(r"\s+", " ", name)
    return name


def compute_name_similarity(name1: str, name2: str) -> float:
    """Compute similarity ratio between two names using SequenceMatcher.

    Args:
        name1: First name (should be lightly cleaned).
        name2: Second name (should be lightly cleaned).

    Returns:
        Similarity ratio between 0 and 1.
    """
    if not name1 or not name2:
        return 0.0
    return SequenceMatcher(None, name1, name2).ratio()


def check_name_containment(norm1: str, norm2: str) -> bool:
    """Check if one normalized name is entirely contained in the other.

    Args:
        norm1: First normalized name.
        norm2: Second normalized name.

    Returns:
        True if one name contains the other (and they're not equal).
    """
    if not norm1 or not norm2:
        return False
    # Don't count as containment if they're identical
    if norm1 == norm2:
        return False
    # Check containment (must be at least 3 chars to avoid false positives)
    if len(norm1) >= 3 and norm1 in norm2:
        return True
    if len(norm2) >= 3 and norm2 in norm1:
        return True
    return False


def normalize_name(name: str | None) -> str:
    """Normalize company name for comparison.

    Removes common suffixes, standardizes abbreviations, and lowercases.
    This is a copy of the function from build_master_crosswalk.py to avoid
    circular imports and allow potential customization.
    """
    if name is None:
        return ""
    name = name.lower().strip()
    if name.startswith("the "):
        name = name[4:]
    name = re.sub(r'[,.\'"\-()&]', " ", name)
    name = re.sub(r"\bco\b", "company", name)
    name = re.sub(r"\bcorp\b", "corporation", name)
    name = re.sub(r"\binc\b", "incorporated", name)
    name = re.sub(r"\bltd\b", "limited", name)
    name = re.sub(r"\bnatl\b", "national", name)
    name = re.sub(r"\bassn\b", "association", name)
    name = re.sub(r"\bassoc\b", "association", name)
    name = re.sub(r"\bn a\b", "", name)
    name = re.sub(r"\bna\b", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    suffixes = [
        "llc",
        "incorporated",
        "corporation",
        "company",
        "limited",
        "bank",
        "mortgage",
        "lending",
        "financial",
        "home loans",
        "home loan",
        "services",
        "group",
        "national association",
        "association",
    ]
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if name.endswith(" " + suffix):
                name = name[: -len(suffix) - 1].strip()
                changed = True
            elif name == suffix:
                break
    return name


def load_enriched_crosswalks(
    agencies: list[Literal["fnma", "fhlmc"]] | None = None,
) -> pl.LazyFrame:
    """Load and combine enriched crosswalks with agency indicator.

    Args:
        agencies: List of agencies to load. Defaults to ["fnma", "fhlmc"].

    Returns:
        Combined LazyFrame with agency column added.

    Raises:
        FileNotFoundError: If no enriched crosswalks exist.
    """
    if agencies is None:
        agencies = ["fnma", "fhlmc"]

    dfs = []
    for agency in agencies:
        path = Config.get_output_path(agency, enriched=True)
        if not path.exists():
            logger.warning(f"Enriched crosswalk not found for {agency}")
            continue
        df = pl.scan_parquet(path).with_columns(pl.lit(agency).alias("agency"))
        dfs.append(df)

    if not dfs:
        raise FileNotFoundError(
            "No enriched crosswalks found. Run 'mortgage-data match hmda-mbs run --enrich' first."
        )

    return pl.concat(dfs, how="diagonal")


def identify_mismatched_pairs(
    df: pl.LazyFrame,
    channel: str = "R",
) -> pl.LazyFrame:
    """Filter to retail channel and find pairs where normalized names don't match.

    Args:
        df: LazyFrame with respondent_name and Seller Name columns.
        channel: Channel to filter on (R=Retail, C=Correspondent, B=Broker).

    Returns:
        LazyFrame filtered to name mismatches.
    """
    # Filter to specified channel
    filtered = df.filter(pl.col("Channel") == channel)

    # Add normalized name columns
    filtered = filtered.with_columns(
        [
            pl.col("respondent_name")
            .map_elements(normalize_name, return_dtype=pl.Utf8)
            .alias("respondent_name_norm"),
            pl.col("Seller Name")
            .map_elements(normalize_name, return_dtype=pl.Utf8)
            .alias("seller_name_norm"),
        ]
    )

    # Keep only mismatches
    mismatched = filtered.filter(pl.col("respondent_name_norm") != pl.col("seller_name_norm"))

    return mismatched


def aggregate_lender_seller_pairs(mismatches: pl.LazyFrame) -> pl.DataFrame:
    """Aggregate mismatched pairs by (respondent_name, seller_name).

    Computes:
    - total_loans, fnma_loans, fhlmc_loans
    - year_2019, year_2020, ..., year_2024
    - first_year, last_year
    - representative LEI (most common)

    Args:
        mismatches: LazyFrame with mismatched name pairs.

    Returns:
        DataFrame aggregated by (respondent_name, Seller Name).
    """
    # Collect first to enable complex aggregations
    df = mismatches.collect()

    # Group and aggregate
    agg_exprs = [
        pl.len().alias("total_loans"),
        # Agency breakdown
        (pl.col("agency") == "fnma").sum().alias("fnma_loans"),
        (pl.col("agency") == "fhlmc").sum().alias("fhlmc_loans"),
        # Year breakdown
        (pl.col("activity_year") == 2019).sum().alias("year_2019"),
        (pl.col("activity_year") == 2020).sum().alias("year_2020"),
        (pl.col("activity_year") == 2021).sum().alias("year_2021"),
        (pl.col("activity_year") == 2022).sum().alias("year_2022"),
        (pl.col("activity_year") == 2023).sum().alias("year_2023"),
        (pl.col("activity_year") == 2024).sum().alias("year_2024"),
        # Year range
        pl.col("activity_year").min().cast(pl.Int16).alias("first_year"),
        pl.col("activity_year").max().cast(pl.Int16).alias("last_year"),
        # Most common LEI
        pl.col("lei").mode().first().alias("lei"),
        # Keep normalized names (take first since they're grouped)
        pl.col("respondent_name_norm").first(),
        pl.col("seller_name_norm").first(),
    ]

    aggregated = (
        df.group_by(["respondent_name", "Seller Name"])
        .agg(agg_exprs)
        .sort("total_loans", descending=True)
    )

    return aggregated


def classify_connection_type(hmda_name: str, umbs_name: str) -> str:
    """Classify the connection type between HMDA lender and UMBS seller names.

    Uses heuristics to identify:
    - merger: Completely different company names
    - rebrand: Same root with different branding
    - affiliate: Parent/subsidiary relationship indicators
    - dba: "Doing Business As" relationship
    - suffix_diff: Only differ in corporate suffixes (Inc, LLC, etc.)
    - unknown: Cannot classify automatically

    Args:
        hmda_name: Normalized HMDA lender name.
        umbs_name: Normalized UMBS seller name.

    Returns:
        Classification string.
    """
    # Check for DBA pattern
    if "dba" in hmda_name.lower() or "dba" in umbs_name.lower():
        return "dba"

    # Check for credit union suffix differences
    cu_patterns = ["credit union", "federal credit union", "fcu", "cu"]
    hmda_is_cu = any(p in hmda_name.lower() for p in cu_patterns)
    umbs_is_cu = any(p in umbs_name.lower() for p in cu_patterns)
    if hmda_is_cu and umbs_is_cu:
        # Both are credit unions - likely suffix difference
        hmda_base = hmda_name.lower()
        umbs_base = umbs_name.lower()
        for p in cu_patterns:
            hmda_base = hmda_base.replace(p, "").strip()
            umbs_base = umbs_base.replace(p, "").strip()
        if hmda_base == umbs_base:
            return "suffix_diff"

    # Check for affiliate patterns (parent/subsidiary indicators)
    affiliate_indicators = ["mortgage", "home loans", "funding", "capital"]
    hmda_has_indicator = any(ind in hmda_name.lower() for ind in affiliate_indicators)
    umbs_has_indicator = any(ind in umbs_name.lower() for ind in affiliate_indicators)
    if hmda_has_indicator != umbs_has_indicator:
        # One has mortgage/lending indicator, other doesn't
        hmda_base = hmda_name.lower()
        umbs_base = umbs_name.lower()
        for ind in affiliate_indicators:
            hmda_base = hmda_base.replace(ind, "").strip()
            umbs_base = umbs_base.replace(ind, "").strip()
        if hmda_base == umbs_base or hmda_base in umbs_base or umbs_base in hmda_base:
            return "affiliate"

    # Check for shared root (potential rebrand)
    hmda_words = set(hmda_name.lower().split())
    umbs_words = set(umbs_name.lower().split())
    shared = hmda_words & umbs_words
    if len(shared) >= 1 and len(hmda_words) <= 4 and len(umbs_words) <= 4:
        # Significant word overlap in short names
        return "rebrand"

    # Check for known rebrand patterns
    rebrand_pairs = [
        ("quicken", "rocket"),
        ("amerihome", "western"),
    ]
    for old, new in rebrand_pairs:
        if (old in hmda_name.lower() and new in umbs_name.lower()) or (
            new in hmda_name.lower() and old in umbs_name.lower()
        ):
            return "rebrand"

    # Check for merger patterns (completely different names)
    if not shared and len(hmda_name) > 3 and len(umbs_name) > 3:
        return "merger"

    return "unknown"


def load_annotations(annotations_path: Path | None = None) -> dict:
    """Load manual annotations from YAML file.

    Args:
        annotations_path: Path to annotations YAML. Defaults to config path.

    Returns:
        Dictionary of annotations keyed by (respondent_name, seller_name) tuple.
    """
    if annotations_path is None:
        annotations_path = Config.ANNOTATIONS_PATH

    if not annotations_path.exists():
        return {}

    with open(annotations_path) as f:
        data = yaml.safe_load(f) or {}

    # Convert list to dict keyed by name pair
    annotations = {}
    for entry in data.get("annotations", []):
        key = (entry.get("respondent_name"), entry.get("seller_name"))
        annotations[key] = entry

    return annotations


def compute_concentration_metrics(
    df: pl.LazyFrame,
    aggregated: pl.DataFrame,
    channel: str = "R",
) -> pl.DataFrame:
    """Compute concentration metrics to identify spurious matches.

    For each lender-seller pair, computes:
    - pct_of_lender: What % of this lender's retail loans go to this seller?
    - pct_of_seller: What % of this seller's retail loans come from this lender?
    - likely_spurious: Flag if both percentages are low (< 10%)

    Args:
        df: Full enriched crosswalk LazyFrame (not just mismatches).
        aggregated: Aggregated pair DataFrame.
        channel: Channel to filter on.

    Returns:
        DataFrame with concentration metrics added.
    """
    # Get channel-filtered data
    channel_df = df.filter(pl.col("Channel") == channel).collect()

    # Total loans per lender (respondent_name)
    lender_totals = channel_df.group_by("respondent_name").agg(pl.len().alias("lender_total_loans"))

    # Total loans per seller
    seller_totals = channel_df.group_by("Seller Name").agg(pl.len().alias("seller_total_loans"))

    # Join totals to aggregated pairs
    with_lender = aggregated.join(lender_totals, on="respondent_name", how="left")
    with_both = with_lender.join(
        seller_totals,
        left_on="Seller Name",
        right_on="Seller Name",
        how="left",
    )

    # Compute percentages
    enriched = with_both.with_columns(
        [
            (pl.col("total_loans") / pl.col("lender_total_loans") * 100)
            .round(2)
            .alias("pct_of_lender"),
            (pl.col("total_loans") / pl.col("seller_total_loans") * 100)
            .round(2)
            .alias("pct_of_seller"),
        ]
    )

    # Flag likely spurious matches (low concentration on both sides)
    # Threshold: < 10% on both sides suggests coincidental matching
    enriched = enriched.with_columns(
        ((pl.col("pct_of_lender") < 10) & (pl.col("pct_of_seller") < 10)).alias("likely_spurious")
    )

    return enriched


def build_lender_seller_crosswalk(
    min_loans: int = 100,
    channel: str = "R",
    agencies: list[Literal["fnma", "fhlmc"]] | None = None,
) -> pl.DataFrame:
    """Build lender/seller crosswalk for name mismatches.

    Main entry point that combines all processing steps.

    Args:
        min_loans: Minimum number of matched loans to include a pair.
        channel: Channel to filter on (R=Retail, C=Correspondent, B=Broker).
        agencies: List of agencies to include. Defaults to both.

    Returns:
        DataFrame with lender/seller pairs, loan counts, and classifications.
    """
    logger.info("Building Lender/Seller Crosswalk")
    logger.info(f"  Channel: {channel}")
    logger.info(f"  Minimum loans: {min_loans:,}")

    # Load enriched crosswalks
    logger.info("  Loading enriched crosswalks...")
    df = load_enriched_crosswalks(agencies)

    # Identify mismatched pairs
    logger.info("  Identifying name mismatches...")
    mismatches = identify_mismatched_pairs(df, channel=channel)

    # Aggregate by pair
    logger.info("  Aggregating by lender/seller pair...")
    aggregated = aggregate_lender_seller_pairs(mismatches)

    # Filter by minimum loans
    filtered = aggregated.filter(pl.col("total_loans") >= min_loans)
    logger.info(f"  Pairs with >= {min_loans} loans: {len(filtered):,}")

    # Add concentration metrics
    logger.info("  Computing concentration metrics...")
    filtered = compute_concentration_metrics(df, filtered, channel=channel)
    spurious_count = filtered.filter(pl.col("likely_spurious")).height
    logger.info(f"  Likely spurious matches: {spurious_count:,}")

    # Add name similarity and containment checks
    logger.info("  Computing name similarity metrics...")
    filtered = filtered.with_columns(
        [
            # Light-cleaned versions for similarity
            pl.col("respondent_name")
            .map_elements(light_clean_name, return_dtype=pl.Utf8)
            .alias("_resp_clean"),
            pl.col("Seller Name")
            .map_elements(light_clean_name, return_dtype=pl.Utf8)
            .alias("_seller_clean"),
        ]
    )

    # Compute similarity ratio
    filtered = filtered.with_columns(
        pl.struct(["_resp_clean", "_seller_clean"])
        .map_elements(
            lambda x: round(compute_name_similarity(x["_resp_clean"], x["_seller_clean"]), 3),
            return_dtype=pl.Float64,
        )
        .alias("name_similarity")
    )

    # Check containment
    filtered = filtered.with_columns(
        pl.struct(["respondent_name_norm", "seller_name_norm"])
        .map_elements(
            lambda x: check_name_containment(x["respondent_name_norm"], x["seller_name_norm"]),
            return_dtype=pl.Boolean,
        )
        .alias("name_contained")
    )

    # Drop temp columns
    filtered = filtered.drop(["_resp_clean", "_seller_clean"])

    # Flag unproblematic matches (containment OR high similarity)
    filtered = filtered.with_columns(
        (pl.col("name_contained") | (pl.col("name_similarity") >= 0.95)).alias("unproblematic")
    )

    unproblematic_count = filtered.filter(pl.col("unproblematic")).height
    logger.info(f"  Unproblematic (containment or >=95% similar): {unproblematic_count:,}")

    # Add automatic classification
    logger.info("  Classifying connection types...")
    filtered = filtered.with_columns(
        pl.struct(["respondent_name_norm", "seller_name_norm"])
        .map_elements(
            lambda x: classify_connection_type(x["respondent_name_norm"], x["seller_name_norm"]),
            return_dtype=pl.Utf8,
        )
        .alias("auto_class")
    )

    # Load and merge manual annotations
    annotations = load_annotations()
    if annotations:
        logger.info(f"  Merging {len(annotations)} manual annotations...")

        def get_annotation_field(row: dict, field: str) -> str | None:
            key = (row["respondent_name"], row["Seller Name"])
            ann = annotations.get(key, {})
            result = ann.get(field)
            return str(result) if result is not None else None

        filtered = filtered.with_columns(
            [
                pl.struct(["respondent_name", "Seller Name"])
                .map_elements(
                    lambda x: get_annotation_field(x, "connection_type"),
                    return_dtype=pl.Utf8,
                )
                .alias("connection_type"),
                pl.struct(["respondent_name", "Seller Name"])
                .map_elements(
                    lambda x: get_annotation_field(x, "notes"),
                    return_dtype=pl.Utf8,
                )
                .alias("notes"),
            ]
        )
    else:
        filtered = filtered.with_columns(
            [
                pl.lit(None).cast(pl.Utf8).alias("connection_type"),
                pl.lit(None).cast(pl.Utf8).alias("notes"),
            ]
        )

    # Reorder columns
    final = filtered.select(
        [
            "respondent_name",
            "respondent_name_norm",
            pl.col("Seller Name").alias("seller_name"),
            "seller_name_norm",
            "lei",
            "total_loans",
            "lender_total_loans",
            "seller_total_loans",
            "pct_of_lender",
            "pct_of_seller",
            "likely_spurious",
            "name_similarity",
            "name_contained",
            "unproblematic",
            "fnma_loans",
            "fhlmc_loans",
            "year_2019",
            "year_2020",
            "year_2021",
            "year_2022",
            "year_2023",
            "year_2024",
            "first_year",
            "last_year",
            "auto_class",
            "connection_type",
            "notes",
        ]
    )

    logger.info(f"  Total pairs: {len(final):,}")
    logger.info(f"  Total loans: {final['total_loans'].sum():,}")
    non_spurious = final.filter(~pl.col("likely_spurious"))
    logger.info(f"  Non-spurious pairs: {len(non_spurious):,}")
    needs_review = final.filter(~pl.col("likely_spurious") & ~pl.col("unproblematic"))
    logger.info(f"  Needs review (non-spurious & problematic): {len(needs_review):,}")

    return final


def generate_markdown_report(
    crosswalk: pl.DataFrame,
    output_path: Path | None = None,
) -> None:
    """Generate markdown documentation for the lender/seller crosswalk.

    Args:
        crosswalk: The crosswalk DataFrame to document.
        output_path: Path to write markdown file. Defaults to docs/matching/.
    """
    if output_path is None:
        output_path = (
            Path(__file__).parent.parent.parent.parent.parent
            / "docs"
            / "matching"
            / "lender_seller_crosswalk.md"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Compute statistics
    total_pairs = len(crosswalk)
    total_loans = crosswalk["total_loans"].sum()
    spurious_pairs = crosswalk.filter(pl.col("likely_spurious")).height
    non_spurious_pairs = total_pairs - spurious_pairs

    # Classification breakdown (non-spurious only)
    non_spurious = crosswalk.filter(~pl.col("likely_spurious"))
    class_counts = (
        non_spurious.group_by("auto_class")
        .agg(pl.len().alias("count"))
        .sort("count", descending=True)
    )

    # Top 20 non-spurious pairs
    top_pairs = non_spurious.head(20)

    # Build markdown content
    lines = [
        "# Lender/Seller Crosswalk Analysis",
        "",
        "## Overview",
        "",
        "This crosswalk identifies HMDA lenders and UMBS sellers that frequently match",
        "in the HMDA-MBS crosswalk but have different names. These represent mergers,",
        "acquisitions, rebrands, affiliates, and DBAs worth documenting.",
        "",
        "The analysis focuses on **Retail channel** records where the originating lender",
        "is expected to be the same as the seller to the GSEs.",
        "",
        "## Summary Statistics",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total name-mismatched pairs | {total_pairs:,} |",
        f"| Total loans in mismatched pairs | {total_loans:,} |",
        f"| Likely spurious matches | {spurious_pairs:,} |",
        f"| Non-spurious pairs | {non_spurious_pairs:,} |",
        "",
        "### Spurious Match Detection",
        "",
        "Pairs are flagged as **likely spurious** when:",
        "- Less than 10% of the lender's retail loans go to this seller, AND",
        "- Less than 10% of the seller's retail loans come from this lender",
        "",
        "This suggests coincidental matching between large institutions rather than",
        "a real business relationship (merger, rebrand, affiliate, or DBA).",
        "",
        "### Classification Breakdown (Non-Spurious Only)",
        "",
        "| Classification | Count |",
        "|----------------|-------|",
    ]

    for row in class_counts.iter_rows(named=True):
        lines.append(f"| {row['auto_class']} | {row['count']:,} |")

    lines.extend(
        [
            "",
            "## Top 20 Pairs by Loan Volume (Non-Spurious)",
            "",
            "| HMDA Lender | UMBS Seller | Loans | % Lender | % Seller | Class |",
            "|-------------|-------------|-------|----------|----------|-------|",
        ]
    )

    for row in top_pairs.iter_rows(named=True):
        class_type = row.get("connection_type") or row["auto_class"]
        lines.append(
            f"| {row['respondent_name'][:35]} | {row['seller_name'][:35]} | "
            f"{row['total_loans']:,} | {row['pct_of_lender']:.1f}% | "
            f"{row['pct_of_seller']:.1f}% | {class_type} |"
        )

    lines.extend(
        [
            "",
            "## Known Cases",
            "",
            "### Validated Rebrands",
            "",
            "- **Quicken Loans -> Rocket Mortgage**: July 2021 rebrand",
            "",
            "### Validated Mergers",
            "",
            "- **Iberiabank -> First Horizon Bank**: July 2020 merger",
            "",
            "### Affiliate Relationships",
            "",
            "- **Citibank, N.A. -> Citimortgage, Inc.**: Bank and mortgage affiliate",
            "",
            "## CLI Usage",
            "",
            "```bash",
            "# Build crosswalk with default settings",
            "mortgage-data match hmda-mbs lender-crosswalk",
            "",
            "# Custom minimum loan threshold",
            "mortgage-data match hmda-mbs lender-crosswalk --min-loans 500",
            "",
            "# Generate this report",
            "mortgage-data match hmda-mbs lender-crosswalk --report",
            "```",
            "",
            "## Output Files",
            "",
            "| File | Description |",
            "|------|-------------|",
            "| `output/lender_seller_crosswalk.parquet` | Main crosswalk with loan counts |",
            "| `annotations/lender_seller_annotations.yaml` | Manual research notes |",
            "| `docs/matching/lender_seller_crosswalk.md` | This documentation |",
            "",
            "## Schema",
            "",
            "| Column | Type | Description |",
            "|--------|------|-------------|",
            "| respondent_name | String | HMDA lender name (original) |",
            "| respondent_name_norm | String | HMDA lender name (normalized) |",
            "| seller_name | String | UMBS seller name (original) |",
            "| seller_name_norm | String | UMBS seller name (normalized) |",
            "| lei | String | Representative LEI |",
            "| total_loans | Int64 | Total matched loans in pair |",
            "| lender_total_loans | Int64 | Lender's total retail loans |",
            "| seller_total_loans | Int64 | Seller's total retail loans |",
            "| pct_of_lender | Float64 | % of lender's loans to this seller |",
            "| pct_of_seller | Float64 | % of seller's loans from this lender |",
            "| likely_spurious | Boolean | True if low concentration on both sides |",
            "| fnma_loans | Int64 | FNMA loans |",
            "| fhlmc_loans | Int64 | FHLMC loans |",
            "| year_2019..year_2024 | Int64 | Loans by year |",
            "| first_year | Int16 | First year seen |",
            "| last_year | Int16 | Last year seen |",
            "| auto_class | String | Automatic classification |",
            "| connection_type | String | Manual annotation |",
            "| notes | String | Research notes |",
            "",
        ]
    )

    content = "\n".join(lines)

    with open(output_path, "w") as f:
        f.write(content)

    logger.info(f"Report saved: {output_path}")
