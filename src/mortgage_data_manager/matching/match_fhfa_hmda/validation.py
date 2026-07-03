"""FHFA-HMDA match validation across both eras.

This module covers two regimes that share infrastructure but diverge
operationally:

- **Post-2018 (2018-2024)**: rich FHFA fingerprint (rate, LTV, DTI, term,
  channel). 4-round workflow. Headline FHFA-side match rate ≈ 89%.
- **Pre-2018 (2009-2017)**: sparse FHFA schema (no rate / LTV / DTI / term /
  channel). 2-round workflow with same-year exact-income merge plus a
  1-year-lag prior-year recovery round that reverses FHFA's documented
  AMFI inflation procedure for borrower income. Headline FHFA-side
  match rate ≈ 71%.

Function naming convention:

- ``post_*`` and ``plot_post_*``, ``compute_post_*``, ``load_post_*``:
  post-2018-only.
- ``pre_*``, ``plot_pre_*``, ``compute_pre_*``, ``load_pre_*``:
  pre-2018-only.
- ``_shared_*``: helpers used by both eras.
- Module-level constants are similarly prefixed
  (``POST_AVAILABLE_YEARS``, ``PRE_OUTPUT_DIR``, etc.).

Top-level entry points:

- :func:`run_validation_post` — full post-2018 validation suite.
- :func:`run_validation_pre` — full pre-2018 validation suite.
- :func:`run_validation` — backward-compatible alias for
  ``run_validation_post`` (the original module's only entry point).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.fhfa.config import FHFAConfig
from mortgage_data_manager.hmda.config import HMDAConfig
from mortgage_data_manager.matching.match_fhfa_hmda.config import (
    FHFAHMDAMatchingConfig,
)
from mortgage_data_manager.matching.match_fhfa_hmda.utils import (
    assign_pre2018_hmda_index,
)

# =============================================================================
# SHARED — palette, matplotlib helper, section printer.
# =============================================================================

# Colors (Jonathan's personal palette)
FHFA_COLOR = "#145A32"  # Hunter Green
HMDA_COLOR = "#D4AC0D"  # Burnished Gold
MATCHED_COLOR = "#0E2F44"  # Midnight Blue
DENSITY_COLOR = "#85929E"  # Blue-Grey

try:
    import matplotlib

    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    matplotlib = None  # type: ignore


def _get_pyplot(interactive: bool = True):
    """Get pyplot with appropriate backend."""
    if not HAS_MATPLOTLIB:
        return None
    if not interactive:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def _shared_print_section(title: str) -> None:
    """Print a section header."""
    print(f"\n{'=' * 70}")
    print(f" {title}")
    print(f"{'=' * 70}\n")


# =============================================================================
# POST-2018 — 2018-2024 workflow validation.
#
# Module-level constants (POST_*) and helpers (load_post_*, compute_post_*,
# plot_post_*) are scoped to this section. Top-level entry: run_validation_post().
# =============================================================================

POST_OUTPUT_DIR = (
    MortgageDataConfig.PROJECT_DIR / "docs" / "matching" / "figures" / "fhfa_hmda"
)
POST_AVAILABLE_YEARS = list(range(2018, 2025))  # 2018-2024


# =============================================================================
# Data Loading

# =============================================================================


def load_post_crosswalks(years: list[int] | None = None) -> pl.LazyFrame:
    """Load crosswalk file.

    Args:
        years: Years to load (unused, kept for API compatibility).

    Returns:
        LazyFrame with crosswalk data.
    """
    crosswalk_dir = FHFAHMDAMatchingConfig.FHFA_HMDA_CROSSWALK_DIR

    # Try the new combined crosswalk first
    combined_path = crosswalk_dir / "fhfa_hmda_crosswalk_2018_2024.parquet"
    if combined_path.exists():
        return pl.scan_parquet(combined_path)

    # Fall back to old per-year files
    if years is None:
        years = POST_AVAILABLE_YEARS

    frames = []
    for year in years:
        candidates = [
            crosswalk_dir / f"crosswalk_{year}.parquet",
            crosswalk_dir / f"crosswalk_all_rounds_{year}.parquet",
        ]
        path = None
        for candidate in candidates:
            if candidate.exists():
                path = candidate
                break

        if path is not None:
            lf = pl.scan_parquet(path)
            rename_map = {}
            schema = lf.collect_schema()
            if "Enterprise Flag" in schema:
                rename_map["Enterprise Flag"] = "enterprise_flag"
            if "Record Number" in schema:
                rename_map["Record Number"] = "record_number"
            if "MatchRound" in schema:
                rename_map["MatchRound"] = "match_round"
            if rename_map:
                lf = lf.rename(rename_map)
            frames.append(lf)
        else:
            print(f"Warning: Crosswalk not found for {year}")

    if not frames:
        raise FileNotFoundError("No crosswalk files found")

    return pl.concat(frames, how="diagonal")


def load_post_fhfa_data(years: list[int] | None = None) -> pl.LazyFrame:
    """Load FHFA sf_c data for specified years.

    Args:
        years: Years to load. Defaults to POST_AVAILABLE_YEARS.

    Returns:
        LazyFrame with FHFA data (only columns needed for validation).
    """
    if years is None:
        years = POST_AVAILABLE_YEARS

    fhfa_silver = FHFAConfig.FHFA_SILVER_DIR / "sf_c"

    # Select only columns needed for validation to avoid schema mismatch issues
    needed_cols = [
        "year",
        "enterprise_flag",
        "record_number",
        "state_code",
        "note_amount",
        "interest_rate_at_origination",
        "loan_purpose",
    ]

    frames = []
    for year in years:
        path = fhfa_silver / f"sf_c_{year}.parquet"
        if path.exists():
            lf = pl.scan_parquet(path).select(needed_cols)
            frames.append(lf)

    if not frames:
        raise FileNotFoundError("No FHFA data files found")

    return pl.concat(frames, how="diagonal")


def load_post_hmda_data(years: list[int] | None = None) -> pl.LazyFrame:
    """Load HMDA data for specified years.

    Args:
        years: Years to load. Defaults to POST_AVAILABLE_YEARS.

    Returns:
        LazyFrame with HMDA data filtered to best file type per year.
    """
    if years is None:
        years = POST_AVAILABLE_YEARS

    hmda_silver = HMDAConfig.HMDA_SILVER_DIR / "loans" / "post2018"

    # Select only columns needed for validation (plus file_type for filtering)
    needed_cols = [
        "HMDAIndex",
        "activity_year",
        "action_taken",
        "purchaser_type",
        "total_units",
        "state_code",
        "loan_amount",
        "interest_rate",
        "loan_purpose",
        "file_type",
    ]

    frames = []
    for year in years:
        path = hmda_silver / f"activity_year={year}"
        if path.exists():
            lf = pl.scan_parquet(path).select(needed_cols)

            # Select best file_type: a (three-year) > b (one-year) > c (snapshot)
            file_types = lf.select("file_type").unique().collect()["file_type"].to_list()
            best_type = "a" if "a" in file_types else ("b" if "b" in file_types else "c")
            lf = lf.filter(pl.col("file_type") == best_type)

            # Filter to originated loans
            lf = lf.filter(pl.col("action_taken") == 1)
            lf = lf.filter(pl.col("total_units") <= 4)  # FHFA only covers 1-4 units
            # Include all purchaser types that could match to FHFA
            lf = lf.filter(
                (pl.col("purchaser_type") == 0)  # Not sold at origination
                | pl.col("purchaser_type").is_in([1, 3])  # GSE
                | (pl.col("purchaser_type") >= 5)  # Secondary market
            )
            frames.append(lf)

    if not frames:
        raise FileNotFoundError("No HMDA data files found")

    return pl.concat(frames, how="diagonal")


# =============================================================================
# Match Rate Computations
# =============================================================================


def compute_post_match_rates_by_year(
    crosswalk: pl.LazyFrame,
    fhfa: pl.LazyFrame,
) -> pl.DataFrame:
    """Calculate FHFA-side match rates by year.

    FHFA loans are uniquely identified by (fhfa_year, enterprise_flag, record_number).

    Returns DataFrame with:
    - year: FHFA origination year
    - total_fhfa_loans: Total FHFA loans for that year
    - matched_loans: Unique FHFA loans matched to HMDA
    - match_rate: matched_loans / total_fhfa_loans
    """
    # Check if crosswalk has fhfa_year (new format) or needs activity_year (old format)
    schema = crosswalk.collect_schema()
    year_col = "fhfa_year" if "fhfa_year" in schema else "activity_year"

    # Get unique matched FHFA keys
    matched_fhfa_keys = crosswalk.select(
        [
            pl.col(year_col).alias("year"),
            "enterprise_flag",
            "record_number",
        ]
    ).unique()

    # Get FHFA totals by origination year
    fhfa_totals = fhfa.group_by("year").agg(pl.len().alias("total_fhfa_loans"))

    # Join matched keys with FHFA data using full key
    fhfa_with_key = fhfa.select(
        [
            "year",
            "enterprise_flag",
            "record_number",
        ]
    )

    fhfa_matched = (
        matched_fhfa_keys.join(
            fhfa_with_key, on=["year", "enterprise_flag", "record_number"], how="inner"
        )
        .group_by("year")
        .agg(pl.len().alias("matched_loans"))
    )

    result = (
        fhfa_totals.join(fhfa_matched, on="year", how="left")
        .with_columns(
            pl.col("matched_loans").fill_null(0),
            (pl.col("matched_loans") / pl.col("total_fhfa_loans")).alias("match_rate"),
        )
        .sort("year")
        .collect()
    )

    return result


def compute_post_hmda_match_rates_by_year(
    crosswalk: pl.LazyFrame,
    hmda: pl.LazyFrame,
) -> pl.DataFrame:
    """Calculate HMDA-side match rates by activity year."""
    matched_hmda = crosswalk.select("HMDAIndex").unique()

    hmda_totals = hmda.group_by("activity_year").agg(pl.len().alias("total_hmda_loans"))

    hmda_matched = (
        hmda.join(matched_hmda, on="HMDAIndex", how="inner")
        .group_by("activity_year")
        .agg(pl.len().alias("matched_loans"))
    )

    result = (
        hmda_totals.join(hmda_matched, on="activity_year", how="left")
        .with_columns(
            pl.col("matched_loans").fill_null(0),
            (pl.col("matched_loans") / pl.col("total_hmda_loans")).alias("match_rate"),
        )
        .sort("activity_year")
        .collect()
    )

    return result


def compute_post_hmda_gse_match_rates_by_year(
    crosswalk: pl.LazyFrame,
    hmda: pl.LazyFrame,
) -> pl.DataFrame:
    """Calculate HMDA-side match rates for GSE-sold loans only.

    This is a more meaningful metric than the broad HMDA match rate:
    - Denominator: HMDA loans with purchaser_type in [1, 3] (sold to Fannie/Freddie)
    - Numerator: Matched HMDA loans that had purchaser_type in [1, 3]

    This avoids inflating the numerator with matches from other purchaser types.

    The numerator is computed by inner-joining the crosswalk against the
    GSE-filtered HMDA pool, so we only count matches whose HMDA record
    is in the current silver. Without that join, the published crosswalk
    can reference stale HMDAIndex values that no longer exist in silver
    (e.g. HMDA 2018 file_type=a was retrimmed after the crosswalk was
    built), inflating the numerator above the denominator.
    """
    # Filter HMDA to only GSE-sold loans (purchaser_type 1=Fannie, 3=Freddie)
    hmda_gse = hmda.filter(pl.col("purchaser_type").is_in([1, 3]))

    hmda_totals = hmda_gse.group_by("activity_year").agg(
        pl.len().alias("total_hmda_gse_loans")
    )

    matched_hmda = crosswalk.select("HMDAIndex").unique()
    hmda_matched = (
        hmda_gse.join(matched_hmda, on="HMDAIndex", how="inner")
        .group_by("activity_year")
        .agg(pl.len().alias("matched_gse_loans"))
    )

    result = (
        hmda_totals.join(hmda_matched, on="activity_year", how="left")
        .with_columns(
            pl.col("matched_gse_loans").fill_null(0),
            (pl.col("matched_gse_loans") / pl.col("total_hmda_gse_loans")).alias(
                "match_rate"
            ),
        )
        .sort("activity_year")
        .collect()
    )

    return result


def compute_post_match_rates_by_state(
    crosswalk: pl.LazyFrame,
    fhfa: pl.LazyFrame,
) -> pl.DataFrame:
    """Calculate FHFA-side match rates by state.

    Returns DataFrame with:
    - state_code: State FIPS code
    - total_fhfa_loans: Total FHFA loans in that state
    - matched_loans: Number matched to HMDA
    - match_rate: matched_loans / total_fhfa_loans
    """
    schema = crosswalk.collect_schema()
    year_col = "fhfa_year" if "fhfa_year" in schema else "activity_year"

    matched_fhfa = crosswalk.select(
        [
            pl.col(year_col).alias("year"),
            "enterprise_flag",
            "record_number",
        ]
    ).unique()

    fhfa_totals = fhfa.group_by("state_code").agg(pl.len().alias("total_fhfa_loans"))

    fhfa_matched = (
        fhfa.join(
            matched_fhfa, on=["year", "enterprise_flag", "record_number"], how="inner"
        )
        .group_by("state_code")
        .agg(pl.len().alias("matched_loans"))
    )

    result = (
        fhfa_totals.join(fhfa_matched, on="state_code", how="left")
        .with_columns(
            pl.col("matched_loans").fill_null(0),
            (pl.col("matched_loans") / pl.col("total_fhfa_loans")).alias("match_rate"),
        )
        .sort("total_fhfa_loans", descending=True)
        .collect()
    )

    return result


def compute_post_match_rates_by_round(
    crosswalk: pl.LazyFrame,
) -> pl.DataFrame:
    """Analyze match round distribution."""
    result = (
        crosswalk.group_by("match_round")
        .agg(pl.len().alias("count"))
        .with_columns((pl.col("count") / pl.col("count").sum()).alias("pct"))
        .sort("match_round")
        .collect()
    )
    return result


def compute_post_match_rates_by_loan_amount(
    crosswalk: pl.LazyFrame,
    fhfa: pl.LazyFrame,
    hmda: pl.LazyFrame,
    bin_size: int = 10000,
    max_amount: int = 750000,
) -> pl.DataFrame:
    """Compute match rates by loan amount bins."""
    schema = crosswalk.collect_schema()
    year_col = "fhfa_year" if "fhfa_year" in schema else "activity_year"

    matched_fhfa = crosswalk.select(
        [
            pl.col(year_col).alias("year"),
            "enterprise_flag",
            "record_number",
        ]
    ).unique()
    matched_hmda = crosswalk.select("HMDAIndex").unique()

    def bin_expr(col_name: str) -> pl.Expr:
        return (((pl.col(col_name) - 5000) / bin_size).round() * bin_size + 5000).alias(
            "loan_amount_bin"
        )

    # HMDA side - filter to GSE-sold loans only (PT 1, 3)
    hmda_gse = hmda.filter(pl.col("purchaser_type").is_in([1, 3]))
    hmda_with_bin = hmda_gse.with_columns(bin_expr("loan_amount")).filter(
        (pl.col("loan_amount_bin") >= 5000) & (pl.col("loan_amount_bin") <= max_amount)
    )
    hmda_totals = hmda_with_bin.group_by("loan_amount_bin").agg(
        pl.len().alias("hmda_total")
    )
    hmda_matched_counts = (
        hmda_with_bin.join(matched_hmda, on="HMDAIndex", how="inner")
        .group_by("loan_amount_bin")
        .agg(pl.len().alias("hmda_matched"))
    )

    # FHFA side
    fhfa_with_bin = fhfa.with_columns(bin_expr("note_amount")).filter(
        (pl.col("loan_amount_bin") >= 5000) & (pl.col("loan_amount_bin") <= max_amount)
    )
    fhfa_totals = fhfa_with_bin.group_by("loan_amount_bin").agg(
        pl.len().alias("fhfa_total")
    )
    fhfa_matched_counts = (
        fhfa_with_bin.join(
            matched_fhfa, on=["year", "enterprise_flag", "record_number"], how="inner"
        )
        .group_by("loan_amount_bin")
        .agg(pl.len().alias("fhfa_matched"))
    )

    result = (
        hmda_totals.join(hmda_matched_counts, on="loan_amount_bin", how="left")
        .join(fhfa_totals, on="loan_amount_bin", how="outer_coalesce")
        .join(fhfa_matched_counts, on="loan_amount_bin", how="left")
        .filter(pl.col("loan_amount_bin").is_not_null())
        .with_columns(
            pl.col("hmda_matched").fill_null(0),
            pl.col("fhfa_matched").fill_null(0),
            (pl.col("hmda_matched") / pl.col("hmda_total")).alias("hmda_match_rate"),
            (pl.col("fhfa_matched") / pl.col("fhfa_total")).alias("fhfa_match_rate"),
        )
        .sort("loan_amount_bin")
        .collect()
    )

    return result


def compute_post_match_rates_by_interest_rate(
    crosswalk: pl.LazyFrame,
    fhfa: pl.LazyFrame,
    hmda: pl.LazyFrame,
    bin_size: float = 0.125,
    min_rate: float = 2.0,
    max_rate: float = 9.0,
) -> pl.DataFrame:
    """Compute match rates by interest rate bins."""
    schema = crosswalk.collect_schema()
    year_col = "fhfa_year" if "fhfa_year" in schema else "activity_year"

    matched_fhfa = crosswalk.select(
        [
            pl.col(year_col).alias("year"),
            "enterprise_flag",
            "record_number",
        ]
    ).unique()
    matched_hmda = crosswalk.select("HMDAIndex").unique()

    def bin_expr(col_name: str) -> pl.Expr:
        return ((pl.col(col_name) / bin_size).round() * bin_size).alias(
            "interest_rate_bin"
        )

    # HMDA side - filter to GSE-sold loans only (PT 1, 3)
    hmda_gse = hmda.filter(pl.col("purchaser_type").is_in([1, 3]))
    hmda_with_bin = hmda_gse.with_columns(bin_expr("interest_rate")).filter(
        (pl.col("interest_rate_bin") >= min_rate)
        & (pl.col("interest_rate_bin") <= max_rate)
        & (pl.col("interest_rate").is_not_null())
    )
    hmda_totals = hmda_with_bin.group_by("interest_rate_bin").agg(
        pl.len().alias("hmda_total")
    )
    hmda_matched_counts = (
        hmda_with_bin.join(matched_hmda, on="HMDAIndex", how="inner")
        .group_by("interest_rate_bin")
        .agg(pl.len().alias("hmda_matched"))
    )

    # FHFA side
    fhfa_with_bin = fhfa.with_columns(bin_expr("interest_rate_at_origination")).filter(
        (pl.col("interest_rate_bin") >= min_rate)
        & (pl.col("interest_rate_bin") <= max_rate)
        & (pl.col("interest_rate_at_origination").is_not_null())
    )
    fhfa_totals = fhfa_with_bin.group_by("interest_rate_bin").agg(
        pl.len().alias("fhfa_total")
    )
    fhfa_matched_counts = (
        fhfa_with_bin.join(
            matched_fhfa, on=["year", "enterprise_flag", "record_number"], how="inner"
        )
        .group_by("interest_rate_bin")
        .agg(pl.len().alias("fhfa_matched"))
    )

    result = (
        hmda_totals.join(hmda_matched_counts, on="interest_rate_bin", how="left")
        .join(fhfa_totals, on="interest_rate_bin", how="outer_coalesce")
        .join(fhfa_matched_counts, on="interest_rate_bin", how="left")
        .filter(pl.col("interest_rate_bin").is_not_null())
        .with_columns(
            pl.col("hmda_matched").fill_null(0),
            pl.col("fhfa_matched").fill_null(0),
            (pl.col("hmda_matched") / pl.col("hmda_total")).alias("hmda_match_rate"),
            (pl.col("fhfa_matched") / pl.col("fhfa_total")).alias("fhfa_match_rate"),
        )
        .sort("interest_rate_bin")
        .collect()
    )

    return result


def compute_post_match_rates_by_enterprise(
    crosswalk: pl.LazyFrame,
    fhfa: pl.LazyFrame,
) -> pl.DataFrame:
    """Compute match rates by enterprise (Fannie Mae vs Freddie Mac)."""
    schema = crosswalk.collect_schema()
    year_col = "fhfa_year" if "fhfa_year" in schema else "activity_year"

    matched_fhfa = crosswalk.select(
        [
            pl.col(year_col).alias("year"),
            "enterprise_flag",
            "record_number",
        ]
    ).unique()

    fhfa_totals = fhfa.group_by("enterprise_flag").agg(
        pl.len().alias("total_fhfa_loans")
    )

    fhfa_matched = (
        fhfa.join(
            matched_fhfa, on=["year", "enterprise_flag", "record_number"], how="inner"
        )
        .group_by("enterprise_flag")
        .agg(pl.len().alias("matched_loans"))
    )

    result = (
        fhfa_totals.join(fhfa_matched, on="enterprise_flag", how="left")
        .with_columns(
            pl.col("matched_loans").fill_null(0),
            (pl.col("matched_loans") / pl.col("total_fhfa_loans")).alias("match_rate"),
            pl.when(pl.col("enterprise_flag") == 1)
            .then(pl.lit("Fannie Mae"))
            .when(pl.col("enterprise_flag") == 2)
            .then(pl.lit("Freddie Mac"))
            .otherwise(pl.lit("Unknown"))
            .alias("enterprise_name"),
        )
        .sort("enterprise_flag")
        .collect()
    )

    return result


def compute_post_hmda_gse_match_rates_by_enterprise(
    crosswalk: pl.LazyFrame,
    hmda: pl.LazyFrame,
) -> pl.DataFrame:
    """Compute HMDA GSE match rates by enterprise.

    Maps HMDA purchaser_type to enterprise:
    - PT=1 -> Fannie Mae (enterprise_flag=1)
    - PT=3 -> Freddie Mac (enterprise_flag=2)
    """
    # Filter HMDA to GSE-sold loans only and map to enterprise
    hmda_gse = hmda.filter(pl.col("purchaser_type").is_in([1, 3])).with_columns(
        pl.when(pl.col("purchaser_type") == 1)
        .then(pl.lit(1))
        .otherwise(pl.lit(2))
        .cast(pl.Int32)
        .alias("hmda_enterprise")
    )

    # Total HMDA GSE loans by enterprise
    hmda_totals = hmda_gse.group_by("hmda_enterprise").agg(
        pl.len().alias("total_hmda_gse_loans")
    )

    # Get unique matched HMDA loans from crosswalk, then join with HMDA to get purchaser_type
    # This avoids issues with the crosswalk's enterprise_flag (which is from FHFA side)
    matched_hmda_keys = crosswalk.select(["HMDAIndex", "activity_year"]).unique()

    # Join with HMDA GSE to get only GSE loans that matched
    matched_hmda = matched_hmda_keys.join(
        hmda_gse.select(["HMDAIndex", "activity_year", "hmda_enterprise"]),
        on=["HMDAIndex", "activity_year"],
        how="inner",
    )

    hmda_matched = matched_hmda.group_by("hmda_enterprise").agg(
        pl.len().alias("matched_gse_loans")
    )

    result = (
        hmda_totals.join(hmda_matched, on="hmda_enterprise", how="left")
        .with_columns(
            pl.col("matched_gse_loans").fill_null(0),
            (pl.col("matched_gse_loans") / pl.col("total_hmda_gse_loans")).alias(
                "match_rate"
            ),
            pl.when(pl.col("hmda_enterprise") == 1)
            .then(pl.lit("Fannie Mae"))
            .when(pl.col("hmda_enterprise") == 2)
            .then(pl.lit("Freddie Mac"))
            .otherwise(pl.lit("Unknown"))
            .alias("enterprise_name"),
        )
        .rename({"hmda_enterprise": "enterprise_flag"})
        .sort("enterprise_flag")
        .collect()
    )

    return result


# =============================================================================
# Visualization Functions
# =============================================================================


def plot_post_match_rates_by_year(
    fhfa_yearly: pl.DataFrame,
    hmda_yearly: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
    hmda_label: str = "HMDA GSE Match Rate",
) -> None:
    """Plot match rates by year for both FHFA and HMDA perspectives."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(12, 6))

    # Only plot years present in crosswalks
    fhfa_years = fhfa_yearly.filter(pl.col("matched_loans") > 0)

    # Handle both column naming conventions (all PT vs GSE-only)
    matched_col = (
        "matched_gse_loans" if "matched_gse_loans" in hmda_yearly.columns else "matched_loans"
    )
    total_col = (
        "total_hmda_gse_loans"
        if "total_hmda_gse_loans" in hmda_yearly.columns
        else "total_hmda_loans"
    )
    hmda_years = hmda_yearly.filter(pl.col(matched_col) > 0)

    ax.plot(
        fhfa_years["year"].to_list(),
        fhfa_years["match_rate"].to_list(),
        color=FHFA_COLOR,
        linewidth=2.5,
        marker="o",
        markersize=8,
        label="FHFA Match Rate",
    )

    ax.plot(
        hmda_years["activity_year"].to_list(),
        hmda_years["match_rate"].to_list(),
        color=HMDA_COLOR,
        linewidth=2.5,
        marker="s",
        markersize=8,
        label=hmda_label,
    )

    # Overall rates
    overall_fhfa = fhfa_years["matched_loans"].sum() / fhfa_years["total_fhfa_loans"].sum()
    overall_hmda = hmda_years[matched_col].sum() / hmda_years[total_col].sum()

    ax.axhline(overall_fhfa, color=FHFA_COLOR, linestyle="--", alpha=0.5, linewidth=1)
    ax.axhline(overall_hmda, color=HMDA_COLOR, linestyle="--", alpha=0.5, linewidth=1)

    ax.set_xlabel("Year")
    ax.set_ylabel("Match Rate")
    ax.set_title("FHFA-HMDA Match Rates by Year (GSE Loans)")

    # Set y-axis limits
    all_rates = fhfa_years["match_rate"].to_list() + hmda_years["match_rate"].to_list()
    y_min = max(0.0, min(all_rates) - 0.05)
    y_max = min(1.0, max(all_rates) + 0.05)
    ax.set_ylim(y_min, y_max)

    ax.legend(loc="lower right")
    ax.grid(True, alpha=0.3)

    # Add overall rate annotations
    years = fhfa_years["year"].to_list()
    if years:
        max_year = max(years)
        ax.text(
            max_year + 0.3,
            overall_fhfa + 0.005,
            f"FHFA: {overall_fhfa:.1%}",
            color=FHFA_COLOR,
            fontsize=9,
            va="bottom",
        )
        ax.text(
            max_year + 0.3,
            overall_hmda - 0.005,
            f"HMDA: {overall_hmda:.1%}",
            color=HMDA_COLOR,
            fontsize=9,
            va="top",
        )

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_post_match_rates_by_loan_amount(
    loan_amount_rates: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot match rates by loan amount bin with density overlay."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(12, 6))

    x = loan_amount_rates["loan_amount_bin"].to_numpy() / 1000

    hmda_total = loan_amount_rates["hmda_total"].to_numpy().astype(float)
    fhfa_total = loan_amount_rates["fhfa_total"].to_numpy().astype(float)

    hmda_total = np.nan_to_num(hmda_total, nan=0)
    fhfa_total = np.nan_to_num(fhfa_total, nan=0)

    hmda_pdf = hmda_total / hmda_total.sum() if hmda_total.sum() > 0 else hmda_total
    fhfa_pdf = fhfa_total / fhfa_total.sum() if fhfa_total.sum() > 0 else fhfa_total

    ax2 = ax.twinx()

    ax2.fill_between(x, hmda_pdf, alpha=0.15, color=HMDA_COLOR, label="HMDA Density")
    ax2.fill_between(x, fhfa_pdf, alpha=0.15, color=FHFA_COLOR, label="FHFA Density")
    ax2.set_ylabel("Density (PDF)", color="gray")
    ax2.tick_params(axis="y", labelcolor="gray")
    max_pdf = max(
        max(hmda_pdf) if len(hmda_pdf) > 0 else 0,
        max(fhfa_pdf) if len(fhfa_pdf) > 0 else 0,
    )
    ax2.set_ylim(0, max_pdf * 1.3 if max_pdf > 0 else 1)

    ax.plot(
        x,
        loan_amount_rates["hmda_match_rate"].to_list(),
        color=HMDA_COLOR,
        linewidth=2,
        label="HMDA Match Rate",
        zorder=10,
    )

    ax.plot(
        x,
        loan_amount_rates["fhfa_match_rate"].to_list(),
        color=FHFA_COLOR,
        linewidth=2,
        label="FHFA Match Rate",
        zorder=10,
    )

    ax.set_xlabel("Loan Amount ($K)")
    ax.set_ylabel("Match Rate")
    ax.set_title("Match Rates by Loan Amount (with density overlay)")

    all_rates = (
        loan_amount_rates["hmda_match_rate"].to_list()
        + loan_amount_rates["fhfa_match_rate"].to_list()
    )
    valid_rates = [r for r in all_rates if r is not None and not np.isnan(r)]
    if valid_rates:
        y_min = max(0.0, min(valid_rates) - 0.05)
        y_max = min(1.0, max(valid_rates) + 0.05)
        ax.set_ylim(y_min, y_max)

    ax.legend(loc="lower left")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_post_match_rates_by_interest_rate(
    interest_rate_rates: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot match rates by interest rate bin with density overlay."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(12, 6))

    x = np.array(interest_rate_rates["interest_rate_bin"].to_list())

    hmda_total = interest_rate_rates["hmda_total"].to_numpy().astype(float)
    fhfa_total = interest_rate_rates["fhfa_total"].to_numpy().astype(float)

    hmda_total = np.nan_to_num(hmda_total, nan=0)
    fhfa_total = np.nan_to_num(fhfa_total, nan=0)

    hmda_pdf = hmda_total / hmda_total.sum() if hmda_total.sum() > 0 else hmda_total
    fhfa_pdf = fhfa_total / fhfa_total.sum() if fhfa_total.sum() > 0 else fhfa_total

    ax2 = ax.twinx()

    ax2.fill_between(x, hmda_pdf, alpha=0.15, color=HMDA_COLOR, label="HMDA Density")
    ax2.fill_between(x, fhfa_pdf, alpha=0.15, color=FHFA_COLOR, label="FHFA Density")
    ax2.set_ylabel("Density (PDF)", color="gray")
    ax2.tick_params(axis="y", labelcolor="gray")
    max_pdf = max(
        max(hmda_pdf) if len(hmda_pdf) > 0 else 0,
        max(fhfa_pdf) if len(fhfa_pdf) > 0 else 0,
    )
    ax2.set_ylim(0, max_pdf * 1.3 if max_pdf > 0 else 1)

    ax.plot(
        x,
        interest_rate_rates["hmda_match_rate"].to_list(),
        color=HMDA_COLOR,
        linewidth=2,
        label="HMDA Match Rate",
        zorder=10,
    )

    ax.plot(
        x,
        interest_rate_rates["fhfa_match_rate"].to_list(),
        color=FHFA_COLOR,
        linewidth=2,
        label="FHFA Match Rate",
        zorder=10,
    )

    ax.set_xlabel("Interest Rate (%)")
    ax.set_ylabel("Match Rate")
    ax.set_title("Match Rates by Interest Rate (with density overlay)")

    all_rates = (
        interest_rate_rates["hmda_match_rate"].to_list()
        + interest_rate_rates["fhfa_match_rate"].to_list()
    )
    valid_rates = [r for r in all_rates if r is not None and not np.isnan(r)]
    if valid_rates:
        y_min = max(0.0, min(valid_rates) - 0.05)
        y_max = min(1.0, max(valid_rates) + 0.05)
        ax.set_ylim(y_min, y_max)

    ax.legend(loc="lower left")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_post_match_rates_by_enterprise(
    fhfa_enterprise_rates: pl.DataFrame,
    hmda_enterprise_rates: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Plot match rates by enterprise (Fannie Mae vs Freddie Mac) with grouped bars."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    fig, ax = plt.subplots(figsize=(10, 6))

    names = fhfa_enterprise_rates["enterprise_name"].to_list()
    fhfa_rates = fhfa_enterprise_rates["match_rate"].to_list()
    hmda_rates = hmda_enterprise_rates["match_rate"].to_list()

    x = np.arange(len(names))
    width = 0.35

    # Grouped bars: FHFA and HMDA GSE side by side
    bars_fhfa = ax.bar(
        x - width / 2,
        fhfa_rates,
        width,
        label="FHFA Match Rate",
        color=FHFA_COLOR,
        alpha=0.85,
    )
    bars_hmda = ax.bar(
        x + width / 2,
        hmda_rates,
        width,
        label="HMDA GSE Match Rate",
        color=HMDA_COLOR,
        alpha=0.85,
    )

    ax.set_xticks(x)
    ax.set_xticklabels(names)

    # Add value labels on bars
    for bar, rate in zip(bars_fhfa, fhfa_rates):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            rate + 0.005,
            f"{rate:.1%}",
            ha="center",
            va="bottom",
            fontsize=10,
            color=FHFA_COLOR,
            fontweight="bold",
        )
    for bar, rate in zip(bars_hmda, hmda_rates):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            rate + 0.005,
            f"{rate:.1%}",
            ha="center",
            va="bottom",
            fontsize=10,
            color=HMDA_COLOR,
            fontweight="bold",
        )

    ax.set_ylabel("Match Rate")
    ax.set_title("Match Rates by Enterprise")

    all_rates = fhfa_rates + hmda_rates
    y_min = max(0.0, min(all_rates) - 0.1)
    y_max = min(1.0, max(all_rates) + 0.05)
    ax.set_ylim(y_min, y_max)

    ax.legend(loc="lower right")
    ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_post_state_match_rate_map(
    fhfa_states: pl.DataFrame,
    output_path: Path | None = None,
) -> None:
    """Plot choropleth map of FHFA match rates by state."""
    try:
        import plotly.graph_objects as go
    except ImportError:
        print("plotly not available, skipping state map")
        return

    # Map state FIPS to state codes
    state_fips_to_abbrev = {
        1: "AL",
        2: "AK",
        4: "AZ",
        5: "AR",
        6: "CA",
        8: "CO",
        9: "CT",
        10: "DE",
        11: "DC",
        12: "FL",
        13: "GA",
        15: "HI",
        16: "ID",
        17: "IL",
        18: "IN",
        19: "IA",
        20: "KS",
        21: "KY",
        22: "LA",
        23: "ME",
        24: "MD",
        25: "MA",
        26: "MI",
        27: "MN",
        28: "MS",
        29: "MO",
        30: "MT",
        31: "NE",
        32: "NV",
        33: "NH",
        34: "NJ",
        35: "NM",
        36: "NY",
        37: "NC",
        38: "ND",
        39: "OH",
        40: "OK",
        41: "OR",
        42: "PA",
        44: "RI",
        45: "SC",
        46: "SD",
        47: "TN",
        48: "TX",
        49: "UT",
        50: "VT",
        51: "VA",
        53: "WA",
        54: "WV",
        55: "WI",
        56: "WY",
        72: "PR",
        78: "VI",
        66: "GU",
    }

    territories = {"PR", "GU", "VI", "AS", "MP"}

    # Add state abbreviation
    fhfa_with_abbrev = fhfa_states.with_columns(
        pl.col("state_code")
        .map_elements(lambda x: state_fips_to_abbrev.get(x, ""), return_dtype=pl.Utf8)
        .alias("state_abbrev")
    ).filter(~pl.col("state_abbrev").is_in(territories) & (pl.col("state_abbrev") != ""))

    all_rates = fhfa_with_abbrev["match_rate"].to_list()
    if not all_rates:
        print("No state data available for mapping")
        return

    color_min = max(0.0, min(all_rates) - 0.02)
    color_max = min(1.0, max(all_rates) + 0.02)

    fig = go.Figure(
        go.Choropleth(
            locations=fhfa_with_abbrev["state_abbrev"].to_list(),
            z=fhfa_with_abbrev["match_rate"].to_list(),
            locationmode="USA-states",
            colorscale="RdYlGn",
            zmin=color_min,
            zmax=color_max,
            colorbar=dict(
                title="Match Rate",
                tickformat=".0%",
            ),
            hovertemplate="<b>%{location}</b><br>Match Rate: %{z:.1%}<extra></extra>",
        )
    )

    fig.update_geos(
        scope="usa",
        showlakes=False,
        showland=True,
        landcolor="lightgray",
    )

    fig.update_layout(
        title_text="FHFA Match Rate by State",
        title_x=0.5,
        width=1000,
        height=600,
        margin=dict(l=0, r=0, t=60, b=0),
    )

    if output_path:
        fig.write_image(str(output_path), scale=2)
        print(f"Saved: {output_path}")


def plot_post_state_size_vs_match_rate(
    state_df: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> dict:
    """Scatter plot of state size vs match rate."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return {}

    from scipy.stats import spearmanr

    # Filter to states with reasonable sample size and valid match rates
    filtered = state_df.filter(
        (pl.col("total_fhfa_loans") >= 100) & (pl.col("match_rate").is_not_null())
    )

    x = filtered["total_fhfa_loans"].to_numpy()
    y = filtered["match_rate"].to_numpy()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    y_min = max(0.0, y.min() - 0.02)
    y_max = min(1.0, y.max() + 0.02)

    ax1.scatter(x, y, alpha=0.7, s=50, color=FHFA_COLOR)
    ax1.set_xlabel("Total FHFA Loans")
    ax1.set_ylabel("Match Rate")
    ax1.set_title("State Size vs Match Rate (Linear Scale)")
    ax1.set_ylim(y_min, y_max)
    ax1.grid(True, alpha=0.3)

    ax2.scatter(x, y, alpha=0.7, s=50, color=FHFA_COLOR)
    ax2.set_xscale("log")
    ax2.set_xlabel("Total FHFA Loans (Log Scale)")
    ax2.set_ylabel("Match Rate")
    ax2.set_title("State Size vs Match Rate (Log Scale)")
    ax2.set_ylim(y_min, y_max)
    ax2.grid(True, alpha=0.3)

    # Calculate correlation
    spearman_r, spearman_p = spearmanr(x, y)
    ax2.text(
        0.05,
        0.95,
        f"Spearman r = {spearman_r:.3f}\np = {spearman_p:.4f}",
        transform=ax2.transAxes,
        fontsize=10,
        verticalalignment="top",
        bbox=dict(boxstyle="round", facecolor="white", alpha=0.8),
    )

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)

    return {
        "spearman_r": spearman_r,
        "spearman_p": spearman_p,
        "n_states": len(filtered),
    }


# =============================================================================
# Main Validation Runner
# =============================================================================


def _shared_print_section(title: str) -> None:
    """Print a section header."""
    print(f"\n{'=' * 70}")
    print(f" {title}")
    print(f"{'=' * 70}\n")


def run_validation_post(
    output_dir: Path | None = None,
    save_plots: bool = True,
    show_plots: bool = False,
) -> dict[str, Any]:
    """Run all validation analyses.

    Args:
        output_dir: Directory to save figures. Defaults to POST_OUTPUT_DIR.
        save_plots: Whether to save plots to disk.
        show_plots: Whether to display matplotlib plots interactively.

    Returns:
        Dictionary with all computed statistics and DataFrames.
    """
    if output_dir is None:
        output_dir = POST_OUTPUT_DIR

    if save_plots:
        output_dir.mkdir(parents=True, exist_ok=True)

    print("Loading data...")
    crosswalk = load_post_crosswalks()
    fhfa = load_post_fhfa_data(POST_AVAILABLE_YEARS)  # Only load years with crosswalks
    hmda = load_post_hmda_data(POST_AVAILABLE_YEARS)

    n_matches = crosswalk.select(pl.len()).collect().item()
    n_fhfa = fhfa.select(pl.len()).collect().item()
    n_hmda = hmda.select(pl.len()).collect().item()

    print(f"Crosswalk: {n_matches:,} matched pairs")
    print(f"FHFA data: {n_fhfa:,} loans (years: {POST_AVAILABLE_YEARS})")
    print(f"HMDA data: {n_hmda:,} loans (filtered to GSE-eligible)")

    # ------- Match Round Breakdown -------
    _shared_print_section("1. Match Round Breakdown")
    round_breakdown = compute_post_match_rates_by_round(crosswalk)
    print(round_breakdown)

    # ------- FHFA Yearly Match Rates -------
    _shared_print_section("2. FHFA Match Rates by Year")
    fhfa_yearly = compute_post_match_rates_by_year(crosswalk, fhfa)
    print(fhfa_yearly)

    overall_fhfa_rate = (
        fhfa_yearly["matched_loans"].sum() / fhfa_yearly["total_fhfa_loans"].sum()
    )
    print(f"\nOverall FHFA match rate: {overall_fhfa_rate:.1%}")

    # ------- HMDA Yearly Match Rates (all purchaser types) -------
    _shared_print_section("3. HMDA Match Rates by Year (All Purchaser Types)")
    hmda_yearly = compute_post_hmda_match_rates_by_year(crosswalk, hmda)
    print(hmda_yearly)

    overall_hmda_rate = (
        hmda_yearly["matched_loans"].sum() / hmda_yearly["total_hmda_loans"].sum()
    )
    print(f"\nOverall HMDA match rate (all PT): {overall_hmda_rate:.1%}")
    print("Note: Denominator includes PT=0 (not sold) and PT>=5 (non-GSE secondary)")

    # ------- HMDA GSE-Sold Match Rates -------
    _shared_print_section("3b. HMDA Match Rates by Year (GSE-Sold Only: PT 1,3)")
    hmda_gse_yearly = compute_post_hmda_gse_match_rates_by_year(crosswalk, hmda)
    print(hmda_gse_yearly)

    overall_hmda_gse_rate = (
        hmda_gse_yearly["matched_gse_loans"].sum()
        / hmda_gse_yearly["total_hmda_gse_loans"].sum()
    )
    print(f"\nOverall HMDA GSE match rate: {overall_hmda_gse_rate:.1%}")
    print("Note: Only HMDA loans reported as sold to Fannie (PT=1) or Freddie (PT=3)")

    # ------- Enterprise Match Rates -------
    _shared_print_section("4. Match Rates by Enterprise")
    fhfa_enterprise_rates = compute_post_match_rates_by_enterprise(crosswalk, fhfa)
    print("FHFA perspective:")
    print(fhfa_enterprise_rates)

    hmda_enterprise_rates = compute_post_hmda_gse_match_rates_by_enterprise(crosswalk, hmda)
    print("\nHMDA GSE perspective:")
    print(hmda_enterprise_rates)

    # ------- State Match Rates -------
    _shared_print_section("5. FHFA Match Rates by State (Top 20 by Volume)")
    fhfa_states = compute_post_match_rates_by_state(crosswalk, fhfa)
    print(fhfa_states.head(20))

    print("\nLowest match rate states (min 1000 loans):")
    filtered = fhfa_states.filter(pl.col("total_fhfa_loans") >= 1000)
    print(filtered.sort("match_rate").head(10))

    # ------- Interpretation -------
    _shared_print_section("6. Interpretation & Key Findings")

    print("Match Rate Summary:")
    print(f"  - Overall FHFA match rate: {overall_fhfa_rate:.1%}")
    print(f"  - Overall HMDA match rate (all PT): {overall_hmda_rate:.1%}")
    print(f"  - Overall HMDA GSE match rate (PT 1,3 only): {overall_hmda_gse_rate:.1%}")

    print("\nRound Distribution:")
    for row in round_breakdown.iter_rows(named=True):
        print(f"  - Round {row['match_round']}: {row['count']:,} ({row['pct']:.1%})")

    print("\nEnterprise Match Rates (FHFA perspective):")
    for row in fhfa_enterprise_rates.iter_rows(named=True):
        print(f"  - {row['enterprise_name']}: {row['match_rate']:.1%}")

    print("\nEnterprise Match Rates (HMDA GSE perspective):")
    for row in hmda_enterprise_rates.iter_rows(named=True):
        print(f"  - {row['enterprise_name']}: {row['match_rate']:.1%}")

    # ------- Visualizations -------
    state_corr = {}
    if (show_plots or save_plots) and HAS_MATPLOTLIB:
        _shared_print_section("7. Visualizations")

        print("Generating temporal match rate plot...")
        plot_post_match_rates_by_year(
            fhfa_yearly,
            hmda_gse_yearly,
            output_path=output_dir / "temporal_match_rates.png" if save_plots else None,
            interactive=show_plots,
        )

        print("Generating enterprise match rate plot...")
        plot_post_match_rates_by_enterprise(
            fhfa_enterprise_rates,
            hmda_enterprise_rates,
            output_path=output_dir / "match_rates_by_enterprise.png"
            if save_plots
            else None,
            interactive=show_plots,
        )

        print("Generating state size vs match rate plot...")
        state_corr = plot_post_state_size_vs_match_rate(
            fhfa_states,
            output_path=output_dir / "state_size_correlation.png"
            if save_plots
            else None,
            interactive=show_plots,
        )
        if state_corr:
            print(
                f"  Spearman r = {state_corr['spearman_r']:.3f} "
                f"(p={state_corr['spearman_p']:.4f})"
            )

        print("Generating state match rate map...")
        plot_post_state_match_rate_map(
            fhfa_states,
            output_path=output_dir / "state_match_rate_map.png" if save_plots else None,
        )

        print("Computing match rates by loan amount...")
        loan_amount_rates = compute_post_match_rates_by_loan_amount(crosswalk, fhfa, hmda)
        plot_post_match_rates_by_loan_amount(
            loan_amount_rates,
            output_path=output_dir / "match_rates_by_loan_amount.png"
            if save_plots
            else None,
            interactive=show_plots,
        )

        print("Computing match rates by interest rate...")
        interest_rate_rates = compute_post_match_rates_by_interest_rate(crosswalk, fhfa, hmda)
        plot_post_match_rates_by_interest_rate(
            interest_rate_rates,
            output_path=output_dir / "match_rates_by_interest_rate.png"
            if save_plots
            else None,
            interactive=show_plots,
        )

    return {
        "round_breakdown": round_breakdown,
        "fhfa_yearly": fhfa_yearly,
        "hmda_yearly": hmda_yearly,
        "hmda_gse_yearly": hmda_gse_yearly,
        "fhfa_enterprise_rates": fhfa_enterprise_rates,
        "hmda_enterprise_rates": hmda_enterprise_rates,
        "fhfa_states": fhfa_states,
        "state_corr": state_corr,
        "overall_fhfa_rate": overall_fhfa_rate,
        "overall_hmda_rate": overall_hmda_rate,
        "overall_hmda_gse_rate": overall_hmda_gse_rate,
    }


# =============================================================================
# PRE-2018 — 2009-2017 workflow validation.
#
# Module-level constants (PRE_*) and helpers (load_pre_*, compute_pre_*,
# plot_pre_*) are scoped to this section. Top-level entry: run_validation_pre().
# =============================================================================

PRE_OUTPUT_DIR = (
    MortgageDataConfig.PROJECT_DIR / "docs" / "matching" / "figures" / "fhfa_hmda_pre2018"
)
PRE_AVAILABLE_YEARS = list(range(2009, 2018))  # 2009-2017
PRE_ROUND_COLORS = {1: "#0E2F44", 2: "#D4AC0D"}  # Midnight, Gold

# FHFA top-codes / sentinels used by pre-2018 income / AMFI cleanup.
PRE_HMDA_TOPCODE = 9_999_000
PRE_FHFA_TOPCODE = 9_999_999
PRE_FHFA_JUNK_CAP = 50_000_000


# =============================================================================
# Data Loading

# =============================================================================


def load_pre_crosswalk(crosswalk_path: Path | str | None = None) -> pl.LazyFrame:
    """Load the combined R1+R2 crosswalk produced by FHFAPre2018Workflow.

    Looks in (in order):
    1. The explicit `crosswalk_path` argument.
    2. ``FHFA_HMDA_CROSSWALK_DIR/fhfa_hmda_crosswalk_2009_2017.parquet``
       (the conventional published location, if you've copied it there).
    3. ``FHFA_HMDA_MATCHING_DIR/pre2018_silver/hmda_fhfa_matches_pre2018_round2.parquet``
       (the workflow's default working file).
    """
    candidates: list[Path] = []
    if crosswalk_path is not None:
        candidates.append(Path(crosswalk_path))
    candidates.append(
        FHFAHMDAMatchingConfig.FHFA_HMDA_CROSSWALK_DIR
        / "fhfa_hmda_crosswalk_2009_2017.parquet"
    )
    candidates.append(
        FHFAHMDAMatchingConfig.FHFA_HMDA_MATCHING_DIR
        / "pre2018_silver"
        / "hmda_fhfa_matches_pre2018_round2.parquet"
    )

    for candidate in candidates:
        if candidate.exists():
            return pl.scan_parquet(candidate)

    raise FileNotFoundError(
        "Could not locate the pre-2018 crosswalk. Expected one of:\n  "
        + "\n  ".join(str(c) for c in candidates)
    )


def load_pre_fhfa_silver(years: list[int] | None = None) -> pl.LazyFrame:
    """Load FHFA sf_c silver for pre-2018 with the columns validation needs.

    Goes directly to silver (not through the workflow's cleaned parquet)
    so we can pull fields the workflow drops, like ``tract_income_ratio``
    and ``state_code``.
    """
    if years is None:
        years = PRE_AVAILABLE_YEARS

    fhfa_silver = FHFAConfig.FHFA_SILVER_DIR / "sf_c"

    needed = [
        "year",
        "enterprise_flag",
        "record_number",
        "state_code",
        "msa_code",
        "upb_acquisition",
        "tract_income_ratio",
        "borrower_race_1",
        "borrower_sex",
        "borrower_ethnicity",
        "number_of_borrowers",
        "loan_purpose",
        "borrower_annual_income",
        "local_area_median_income",
    ]
    frames = []
    for y in years:
        path = fhfa_silver / f"sf_c_{y}.parquet"
        if not path.exists():
            print(f"Warning: FHFA silver missing for {y}: {path}")
            continue
        lf = pl.scan_parquet(path).filter(pl.col("enterprise_flag").is_in([1, 2]))
        avail = lf.collect_schema().names()
        cols = [c for c in needed if c in avail]
        frames.append(lf.select(cols))

    if not frames:
        raise FileNotFoundError("No FHFA silver files found for pre-2018 validation")
    return pl.concat(frames, how="diagonal_relaxed")


def load_pre_hmda_silver(years: list[int] | None = None) -> pl.LazyFrame:
    """Load HMDA pre-2018 silver matched-cohort fields.

    Mirrors the workflow's filtering (action_taken=1, drop pt={2,4}) and assigns
    the same ``HMDAIndex`` the matcher uses via
    :func:`assign_pre2018_hmda_index` — persisted for 2017, synthesized from the
    unique triple for 2007-2016. Because both sides derive the key identically,
    validation joins line up with the crosswalk regardless of row order (this
    replaces the former, fragile ``with_row_index`` reconstruction).
    """
    if years is None:
        years = list(range(min(PRE_AVAILABLE_YEARS) - 1, max(PRE_AVAILABLE_YEARS) + 1))

    silver_root = (
        HMDAConfig.HMDA_SILVER_DIR / "loans" / "period_2007_2017"
    )
    needed = [
        "activity_year",
        "respondent_id",
        "agency_code",
        "sequence_number",
        "loan_amount",
        "loan_type",
        "loan_purpose",
        "occupancy_type",
        "property_type",
        "purchaser_type",
        "income",
        "applicant_sex",
        "co_applicant_sex",
        "applicant_race_1",
        "co_applicant_race_1",
        "applicant_ethnicity",
        "census_tract",
        "state_code",
    ]
    frames = []
    for y in years:
        base = silver_root / f"activity_year={y}" / "file_type=d"
        files = sorted(base.rglob("*.parquet")) if base.exists() else []
        if not files:
            continue
        lf = pl.concat([pl.scan_parquet(f) for f in files], how="diagonal_relaxed")
        avail = lf.collect_schema().names()
        lf = lf.filter(
            (pl.col("action_taken") == 1)
            & ~pl.col("purchaser_type").is_in([2, 4])
        ).with_columns(
            pl.when(pl.col("co_applicant_race_1") == 8).then(1).otherwise(2)
            .alias("hmda_num_borrowers")
        )
        lf = assign_pre2018_hmda_index(lf)
        lf = lf.select(
            [c for c in needed if c in avail] + ["hmda_num_borrowers", "HMDAIndex"]
        )
        frames.append(lf)

    if not frames:
        raise FileNotFoundError("No HMDA silver partitions found for pre-2018 validation")

    combined = pl.concat(frames, how="diagonal_relaxed").collect(engine="streaming")
    return combined.lazy()


# =============================================================================
# Match Rate Computations
# =============================================================================


def compute_pre_match_rates_by_year(
    crosswalk: pl.LazyFrame, fhfa: pl.LazyFrame
) -> pl.DataFrame:
    """FHFA-side match rates by acquisition year."""
    matched = crosswalk.select(
        pl.col("year"),
        pl.col("Enterprise Flag").alias("enterprise_flag"),
        pl.col("Record Number").alias("record_number"),
    ).unique()

    fhfa_totals = fhfa.group_by("year").agg(pl.len().alias("total_fhfa_loans"))
    fhfa_matched = (
        fhfa.select(["year", "enterprise_flag", "record_number"])
        .join(matched, on=["year", "enterprise_flag", "record_number"], how="inner")
        .group_by("year")
        .agg(pl.len().alias("matched_loans"))
    )
    return (
        fhfa_totals.join(fhfa_matched, on="year", how="left")
        .with_columns(
            pl.col("matched_loans").fill_null(0),
            (pl.col("matched_loans") / pl.col("total_fhfa_loans")).alias("match_rate"),
        )
        .sort("year")
        .collect()
    )


def compute_pre_hmda_gse_match_rates_by_year(
    crosswalk: pl.LazyFrame, hmda: pl.LazyFrame
) -> pl.DataFrame:
    """HMDA-side match rates restricted to GSE-sold loans (purchaser_type ∈ {1, 3})."""
    hmda_gse = hmda.filter(pl.col("purchaser_type").is_in([1, 3]))
    hmda_totals = hmda_gse.group_by("activity_year").agg(
        pl.len().alias("total_hmda_gse_loans")
    )

    matched_hmda = crosswalk.select("HMDAIndex").unique()
    hmda_matched = (
        hmda_gse.join(matched_hmda, on="HMDAIndex", how="inner")
        .group_by("activity_year")
        .agg(pl.len().alias("matched_gse_loans"))
    )
    return (
        hmda_totals.join(hmda_matched, on="activity_year", how="left")
        .with_columns(
            pl.col("matched_gse_loans").fill_null(0),
            (pl.col("matched_gse_loans") / pl.col("total_hmda_gse_loans"))
            .alias("match_rate"),
        )
        .sort("activity_year")
        .collect()
    )


def compute_pre_match_rates_by_round(crosswalk: pl.LazyFrame) -> pl.DataFrame:
    """Distribution of matches across rounds 1/2/3."""
    return (
        crosswalk.group_by("match_round")
        .agg(pl.len().alias("count"))
        .with_columns((pl.col("count") / pl.col("count").sum()).alias("pct"))
        .sort("match_round")
        .collect()
    )


def compute_pre_match_rates_by_round_and_year(
    crosswalk: pl.LazyFrame, fhfa: pl.LazyFrame
) -> pl.DataFrame:
    """Round contribution per acquisition year (for stacked-bar visualization)."""
    fhfa_totals = fhfa.group_by("year").agg(pl.len().alias("total_fhfa_loans"))

    by_year_round = (
        crosswalk
        .group_by(["year", "match_round"])
        .agg(pl.len().alias("matches"))
    )
    return (
        by_year_round.join(fhfa_totals, on="year", how="left")
        .with_columns(
            (pl.col("matches") / pl.col("total_fhfa_loans")).alias("rate_of_fhfa")
        )
        .sort(["year", "match_round"])
        .collect()
    )


def compute_pre_match_rates_by_state(
    crosswalk: pl.LazyFrame, fhfa: pl.LazyFrame
) -> pl.DataFrame:
    """FHFA-side match rates by state code."""
    matched = crosswalk.select(
        pl.col("year"),
        pl.col("Enterprise Flag").alias("enterprise_flag"),
        pl.col("Record Number").alias("record_number"),
    ).unique()

    fhfa_totals = fhfa.group_by("state_code").agg(pl.len().alias("total_fhfa_loans"))
    fhfa_matched = (
        fhfa.select(["year", "enterprise_flag", "record_number", "state_code"])
        .join(matched, on=["year", "enterprise_flag", "record_number"], how="inner")
        .group_by("state_code")
        .agg(pl.len().alias("matched_loans"))
    )
    return (
        fhfa_totals.join(fhfa_matched, on="state_code", how="left")
        .with_columns(
            pl.col("matched_loans").fill_null(0),
            (pl.col("matched_loans") / pl.col("total_fhfa_loans")).alias("match_rate"),
        )
        .sort("total_fhfa_loans", descending=True)
        .collect()
    )


def compute_pre_match_rates_by_enterprise(
    crosswalk: pl.LazyFrame, fhfa: pl.LazyFrame
) -> pl.DataFrame:
    """Match rates per enterprise (Fannie Mae vs Freddie Mac), FHFA side."""
    matched = crosswalk.select(
        pl.col("year"),
        pl.col("Enterprise Flag").alias("enterprise_flag"),
        pl.col("Record Number").alias("record_number"),
    ).unique()

    totals = fhfa.group_by("enterprise_flag").agg(pl.len().alias("total_fhfa_loans"))
    matched_counts = (
        fhfa.select(["year", "enterprise_flag", "record_number"])
        .join(matched, on=["year", "enterprise_flag", "record_number"], how="inner")
        .group_by("enterprise_flag")
        .agg(pl.len().alias("matched_loans"))
    )
    return (
        totals.join(matched_counts, on="enterprise_flag", how="left")
        .with_columns(
            pl.col("matched_loans").fill_null(0),
            (pl.col("matched_loans") / pl.col("total_fhfa_loans")).alias("match_rate"),
            pl.when(pl.col("enterprise_flag") == 1)
            .then(pl.lit("Fannie Mae"))
            .when(pl.col("enterprise_flag") == 2)
            .then(pl.lit("Freddie Mac"))
            .otherwise(pl.lit("Unknown"))
            .alias("enterprise_name"),
        )
        .sort("enterprise_flag")
        .collect()
    )


def compute_pre_hmda_gse_match_rates_by_enterprise(
    crosswalk: pl.LazyFrame, hmda: pl.LazyFrame
) -> pl.DataFrame:
    """HMDA-side match rates per enterprise (pt=1 → Fannie, pt=3 → Freddie)."""
    hmda_gse = hmda.filter(pl.col("purchaser_type").is_in([1, 3])).with_columns(
        pl.when(pl.col("purchaser_type") == 1).then(1).otherwise(2)
        .cast(pl.Int32).alias("hmda_enterprise")
    )
    totals = hmda_gse.group_by("hmda_enterprise").agg(
        pl.len().alias("total_hmda_gse_loans")
    )
    matched = crosswalk.select("HMDAIndex").unique()
    matched_counts = (
        hmda_gse.join(matched, on="HMDAIndex", how="inner")
        .group_by("hmda_enterprise")
        .agg(pl.len().alias("matched_gse_loans"))
    )
    return (
        totals.join(matched_counts, on="hmda_enterprise", how="left")
        .with_columns(
            pl.col("matched_gse_loans").fill_null(0),
            (pl.col("matched_gse_loans") / pl.col("total_hmda_gse_loans"))
            .alias("match_rate"),
            pl.when(pl.col("hmda_enterprise") == 1)
            .then(pl.lit("Fannie Mae"))
            .otherwise(pl.lit("Freddie Mac"))
            .alias("enterprise_name"),
        )
        .rename({"hmda_enterprise": "enterprise_flag"})
        .sort("enterprise_flag")
        .collect()
    )


def compute_pre_match_rates_by_loan_amount(
    crosswalk: pl.LazyFrame,
    fhfa: pl.LazyFrame,
    hmda: pl.LazyFrame,
    bin_size: int = 10000,
    max_amount: int = 750000,
) -> pl.DataFrame:
    """Match rates by loan-amount bin from both perspectives."""
    matched_fhfa = crosswalk.select(
        pl.col("year"),
        pl.col("Enterprise Flag").alias("enterprise_flag"),
        pl.col("Record Number").alias("record_number"),
    ).unique()
    matched_hmda = crosswalk.select("HMDAIndex").unique()

    def bin_expr(col: str) -> pl.Expr:
        return (((pl.col(col) - 5000) / bin_size).round() * bin_size + 5000).alias(
            "loan_amount_bin"
        )

    hmda_gse = hmda.filter(pl.col("purchaser_type").is_in([1, 3]))
    hmda_with_bin = hmda_gse.with_columns(bin_expr("loan_amount")).filter(
        (pl.col("loan_amount_bin") >= 5000) & (pl.col("loan_amount_bin") <= max_amount)
    )
    hmda_totals = hmda_with_bin.group_by("loan_amount_bin").agg(
        pl.len().alias("hmda_total")
    )
    hmda_matched_counts = (
        hmda_with_bin.join(matched_hmda, on="HMDAIndex", how="inner")
        .group_by("loan_amount_bin")
        .agg(pl.len().alias("hmda_matched"))
    )

    fhfa_with_bin = fhfa.with_columns(bin_expr("upb_acquisition")).filter(
        (pl.col("loan_amount_bin") >= 5000) & (pl.col("loan_amount_bin") <= max_amount)
    )
    fhfa_totals = fhfa_with_bin.group_by("loan_amount_bin").agg(
        pl.len().alias("fhfa_total")
    )
    fhfa_matched_counts = (
        fhfa_with_bin.join(
            matched_fhfa, on=["year", "enterprise_flag", "record_number"], how="inner"
        )
        .group_by("loan_amount_bin")
        .agg(pl.len().alias("fhfa_matched"))
    )

    return (
        hmda_totals
        .join(hmda_matched_counts, on="loan_amount_bin", how="left")
        .join(fhfa_totals, on="loan_amount_bin", how="full", coalesce=True)
        .join(fhfa_matched_counts, on="loan_amount_bin", how="left")
        .filter(pl.col("loan_amount_bin").is_not_null())
        .with_columns(
            pl.col("hmda_matched").fill_null(0),
            pl.col("fhfa_matched").fill_null(0),
            (pl.col("hmda_matched") / pl.col("hmda_total")).alias("hmda_match_rate"),
            (pl.col("fhfa_matched") / pl.col("fhfa_total")).alias("fhfa_match_rate"),
        )
        .sort("loan_amount_bin")
        .collect()
    )


def compute_pre_match_rates_by_tract_income_ratio(
    crosswalk: pl.LazyFrame, fhfa: pl.LazyFrame, bin_size: float = 0.1
) -> pl.DataFrame:
    """FHFA-side match rates by tract income ratio (tract median / area AMI).

    Useful for surfacing whether matches are biased toward higher- or
    lower-income tracts. Bins of 10pp by default.
    """
    matched = crosswalk.select(
        pl.col("year"),
        pl.col("Enterprise Flag").alias("enterprise_flag"),
        pl.col("Record Number").alias("record_number"),
    ).unique()

    binned = (
        fhfa.filter(
            pl.col("tract_income_ratio").is_not_null()
            & (pl.col("tract_income_ratio") > 0)
            & (pl.col("tract_income_ratio") < 5.0)
        )
        .with_columns(
            ((pl.col("tract_income_ratio") / bin_size).floor() * bin_size).alias("ratio_bin")
        )
    )
    totals = binned.group_by("ratio_bin").agg(pl.len().alias("total_fhfa_loans"))
    matched_counts = (
        binned.select(["ratio_bin", "year", "enterprise_flag", "record_number"])
        .join(matched, on=["year", "enterprise_flag", "record_number"], how="inner")
        .group_by("ratio_bin")
        .agg(pl.len().alias("matched_loans"))
    )
    return (
        totals.join(matched_counts, on="ratio_bin", how="left")
        .with_columns(
            pl.col("matched_loans").fill_null(0),
            (pl.col("matched_loans") / pl.col("total_fhfa_loans")).alias("match_rate"),
        )
        .sort("ratio_bin")
        .collect()
    )


# =============================================================================
# Precision Diagnostics
# =============================================================================


def compute_pre_demographic_agreement_vs_random(
    crosswalk: pl.LazyFrame, fhfa: pl.LazyFrame, hmda: pl.LazyFrame
) -> pl.DataFrame:
    """Per-round demographic agreement and random-pair baseline.

    For race1, sex, ethnicity, num_borrowers: compute the share of
    matched pairs that agree on that variable, vs the random-pair
    expectation (sum of squared marginal probabilities). Lift = agree
    minus baseline; ratio = agree / baseline.

    Borrowed from `investigation_hmda_fhfa_pre2018_quality_2026-05-07`.
    """
    pairs_lf = (
        crosswalk
        .select([
            "match_round",
            pl.col("year"),
            pl.col("Enterprise Flag").alias("enterprise_flag"),
            pl.col("Record Number").alias("record_number"),
            "HMDAIndex",
        ])
        .join(
            fhfa.select([
                "year", "enterprise_flag", "record_number",
                pl.col("borrower_race_1").alias("fhfa_race"),
                pl.col("borrower_sex").alias("fhfa_sex"),
                pl.col("borrower_ethnicity").alias("fhfa_ethnicity"),
                pl.col("number_of_borrowers").alias("fhfa_num_borrowers"),
            ]),
            on=["year", "enterprise_flag", "record_number"],
            how="inner",
        )
        .join(
            hmda.select([
                "HMDAIndex",
                pl.col("applicant_race_1").alias("hmda_race"),
                pl.col("applicant_sex").alias("hmda_sex"),
                pl.col("applicant_ethnicity").alias("hmda_ethnicity"),
                "hmda_num_borrowers",
            ]),
            on="HMDAIndex",
            how="inner",
        )
    )
    pairs = pairs_lf.collect()

    fhfa_collected = fhfa.select(
        "borrower_race_1", "borrower_sex", "borrower_ethnicity", "number_of_borrowers"
    ).collect()
    hmda_collected = hmda.select(
        "applicant_race_1", "applicant_sex", "applicant_ethnicity", "hmda_num_borrowers"
    ).collect()

    def random_baseline(hcol: str, fcol: str) -> float:
        h = hmda_collected.select(pl.col(hcol).drop_nulls()).to_series()
        f = fhfa_collected.select(pl.col(fcol).drop_nulls()).to_series()
        if len(h) == 0 or len(f) == 0:
            return float("nan")
        h_dist = h.value_counts(sort=False).rename({hcol: "v"}).with_columns(
            pl.col("count") / len(h)
        ).rename({"count": "h_p"})
        f_dist = f.value_counts(sort=False).rename({fcol: "v"}).with_columns(
            pl.col("count") / len(f)
        ).rename({"count": "f_p"})
        joint = h_dist.join(f_dist, on="v", how="inner")
        return float((joint["h_p"] * joint["f_p"]).sum()) if len(joint) else float("nan")

    rows = []
    variables = [
        ("race1", "hmda_race", "fhfa_race", "applicant_race_1", "borrower_race_1"),
        ("sex", "hmda_sex", "fhfa_sex", "applicant_sex", "borrower_sex"),
        ("ethnicity", "hmda_ethnicity", "fhfa_ethnicity", "applicant_ethnicity", "borrower_ethnicity"),
        ("num_borrowers", "hmda_num_borrowers", "fhfa_num_borrowers", "hmda_num_borrowers", "number_of_borrowers"),
    ]
    rounds_present = sorted(pairs["match_round"].unique().to_list())
    for label, h_pair_col, f_pair_col, h_pop_col, f_pop_col in variables:
        baseline = random_baseline(h_pop_col, f_pop_col)
        # Overall + per-round
        for tag, sub in [("overall", pairs)] + [
            (f"R{r}", pairs.filter(pl.col("match_round") == r))
            for r in rounds_present
        ]:
            usable = sub.select([h_pair_col, f_pair_col]).drop_nulls()
            n = len(usable)
            if n == 0:
                rows.append({
                    "variable": label, "scope": tag, "n_pairs": 0,
                    "agree_pct": None, "random_pct": baseline * 100,
                    "lift_pp": None, "ratio": None,
                })
                continue
            agree = (usable[h_pair_col] == usable[f_pair_col]).sum() / n
            rows.append({
                "variable": label, "scope": tag, "n_pairs": n,
                "agree_pct": agree * 100,
                "random_pct": baseline * 100,
                "lift_pp": (agree - baseline) * 100,
                "ratio": agree / baseline if baseline else float("nan"),
            })
    return pl.DataFrame(rows)


def compute_pre_amount_diff_distribution(
    crosswalk: pl.LazyFrame, fhfa: pl.LazyFrame, hmda: pl.LazyFrame
) -> pl.DataFrame:
    """Distribution of HMDA - FHFA loan-amount difference per round.

    Confirms the asymmetric `0 ≤ HMDA - FHFA ≤ max($2k, 1% × HMDA)`
    tolerance is binding rather than letting noise through.
    """
    pairs = (
        crosswalk.select([
            "match_round",
            "year",
            pl.col("Enterprise Flag").alias("enterprise_flag"),
            pl.col("Record Number").alias("record_number"),
            "HMDAIndex",
        ])
        .join(
            fhfa.select(["year", "enterprise_flag", "record_number", "upb_acquisition"]),
            on=["year", "enterprise_flag", "record_number"], how="inner",
        )
        .join(
            hmda.select(["HMDAIndex", "loan_amount"]),
            on="HMDAIndex", how="inner",
        )
        .with_columns(
            (pl.col("loan_amount") - pl.col("upb_acquisition")).alias("amt_diff")
        )
    ).collect()

    rounds_present = sorted(pairs["match_round"].unique().to_list())
    rows = []
    for label, sub in [("overall", pairs)] + [
        (f"R{r}", pairs.filter(pl.col("match_round") == r)) for r in rounds_present
    ]:
        if len(sub) == 0:
            continue
        d = sub["amt_diff"]
        rows.append({
            "scope": label,
            "n_pairs": len(sub),
            "median": int(d.median()),
            "q01": int(d.quantile(0.01)),
            "q05": int(d.quantile(0.05)),
            "q95": int(d.quantile(0.95)),
            "q99": int(d.quantile(0.99)),
            "share_eq0_pct": (d == 0).sum() / len(sub) * 100,
            "share_le1k_pct": (d.abs() <= 1000).sum() / len(sub) * 100,
            "share_le2k_pct": (d.abs() <= 2000).sum() / len(sub) * 100,
        })
    return pl.DataFrame(rows)


def compute_pre_income_agreement_per_round(
    crosswalk: pl.LazyFrame, fhfa: pl.LazyFrame, hmda: pl.LazyFrame
) -> pl.DataFrame:
    """Per-round income agreement (raw and AMFI-deflated for R2)."""
    fhfa_with_amfi = fhfa.select([
        "year", "enterprise_flag", "record_number",
        pl.col("borrower_annual_income").alias("fhfa_inc"),
        pl.col("local_area_median_income").alias("amfi_acq"),
        "msa_code",
    ])
    pairs = (
        crosswalk.select([
            "match_round",
            "year",
            pl.col("Enterprise Flag").alias("enterprise_flag"),
            pl.col("Record Number").alias("record_number"),
            "HMDAIndex",
            "activity_year",
        ])
        .join(fhfa_with_amfi, on=["year", "enterprise_flag", "record_number"], how="inner")
        .join(
            hmda.select(["HMDAIndex", "income"]).rename({"income": "hmda_inc"}),
            on="HMDAIndex", how="inner",
        )
    ).collect()

    # Build (msa, year) → AMFI for the orig year of each pair (needed
    # for R2 deflation comparison).
    panel = (
        fhfa.filter(
            pl.col("local_area_median_income").is_not_null()
            & (pl.col("local_area_median_income") < PRE_FHFA_TOPCODE)
            & (pl.col("local_area_median_income") > 0)
        )
        .group_by(["msa_code", "year"])
        .agg(pl.col("local_area_median_income").mode().first().alias("amfi"))
        .collect()
    )

    pairs = pairs.join(
        panel.rename({"year": "activity_year", "amfi": "amfi_orig"}),
        on=["msa_code", "activity_year"], how="left",
    )

    rounds_present = sorted(pairs["match_round"].unique().to_list())
    rows = []
    for label, sub in [("overall", pairs)] + [
        (f"R{r}", pairs.filter(pl.col("match_round") == r)) for r in rounds_present
    ]:
        if len(sub) == 0:
            continue
        usable = sub.filter(pl.col("hmda_inc").is_not_null() & pl.col("fhfa_inc").is_not_null())
        if len(usable) == 0:
            continue
        raw_diff = (usable["fhfa_inc"].cast(pl.Int64) - usable["hmda_inc"].cast(pl.Int64)).abs()
        row = {
            "scope": label,
            "n_pairs": len(usable),
            "raw_eq0_pct": (raw_diff == 0).sum() / len(usable) * 100,
            "raw_le1k_pct": (raw_diff <= 1000).sum() / len(usable) * 100,
            "raw_le10_pct": (raw_diff <= 10).sum() / len(usable) * 100,
        }
        # Deflated comparison only meaningful when both AMFIs are present
        defl = usable.filter(
            pl.col("amfi_orig").is_not_null()
            & pl.col("amfi_acq").is_not_null()
            & (pl.col("amfi_acq") > 0)
        )
        if len(defl):
            d = defl.with_columns(
                ((pl.col("fhfa_inc").cast(pl.Float64)
                  * pl.col("amfi_orig").cast(pl.Float64)
                  / pl.col("amfi_acq").cast(pl.Float64)
                  / 1000.0).round(0) * 1000).cast(pl.Int64).alias("fhfa_deflated")
            )
            ddiff = (d["fhfa_deflated"] - d["hmda_inc"].cast(pl.Int64)).abs()
            row["defl_eq0_pct"] = (ddiff == 0).sum() / len(d) * 100
            row["defl_le1k_pct"] = (ddiff <= 1000).sum() / len(d) * 100
            row["defl_le2k_pct"] = (ddiff <= 2000).sum() / len(d) * 100
        rows.append(row)
    return pl.DataFrame(rows)


def compute_pre_amfi_inflation_diagnostic(
    crosswalk: pl.LazyFrame, fhfa: pl.LazyFrame, hmda: pl.LazyFrame
) -> pl.DataFrame:
    """R2-specific: per-pair implied vs expected AMFI inflation ratio.

    For R2 matches (1-yr-lag prior-year cohort), the implied
    `FHFA_inc / HMDA_inc` should track the expected MSA-level
    `AMFI_acq / AMFI_orig` if FHFA's documented inflation procedure
    is what we're reversing. Reports the distribution of
    `implied - expected`.
    """
    pairs = (
        crosswalk
        .filter(pl.col("match_round") == 2)
        .select([
            "year",
            pl.col("Enterprise Flag").alias("enterprise_flag"),
            pl.col("Record Number").alias("record_number"),
            "HMDAIndex",
            "activity_year",
        ])
        .join(
            fhfa.select([
                "year", "enterprise_flag", "record_number",
                pl.col("borrower_annual_income").alias("fhfa_inc"),
                pl.col("local_area_median_income").alias("amfi_acq"),
                "msa_code",
            ]),
            on=["year", "enterprise_flag", "record_number"], how="inner",
        )
        .join(
            hmda.select(["HMDAIndex", "income"]).rename({"income": "hmda_inc"}),
            on="HMDAIndex", how="inner",
        )
    ).collect()

    panel = (
        fhfa.filter(
            pl.col("local_area_median_income").is_not_null()
            & (pl.col("local_area_median_income") < PRE_FHFA_TOPCODE)
            & (pl.col("local_area_median_income") > 0)
        )
        .group_by(["msa_code", "year"])
        .agg(pl.col("local_area_median_income").mode().first().alias("amfi"))
        .collect()
    )

    pairs = pairs.join(
        panel.rename({"year": "activity_year", "amfi": "amfi_orig"}),
        on=["msa_code", "activity_year"], how="left",
    ).filter(
        pl.col("hmda_inc").is_not_null()
        & (pl.col("hmda_inc") > 0)
        & pl.col("amfi_orig").is_not_null()
        & pl.col("amfi_acq").is_not_null()
        & (pl.col("amfi_acq") > 0)
    )

    if len(pairs) == 0:
        return pl.DataFrame({"n_pairs": [0]})

    pairs = pairs.with_columns(
        (pl.col("fhfa_inc").cast(pl.Float64) / pl.col("hmda_inc").cast(pl.Float64))
        .alias("implied_ratio"),
        (pl.col("amfi_acq").cast(pl.Float64) / pl.col("amfi_orig").cast(pl.Float64))
        .alias("expected_ratio"),
    ).with_columns(
        (pl.col("implied_ratio") - pl.col("expected_ratio")).alias("residual")
    )

    return pl.DataFrame([{
        "n_pairs": len(pairs),
        "median_implied": float(pairs["implied_ratio"].median()),
        "median_expected": float(pairs["expected_ratio"].median()),
        "median_residual": float(pairs["residual"].median()),
        "mean_abs_residual": float(pairs["residual"].abs().mean()),
        "share_within_1pp_pct": (pairs["residual"].abs() <= 0.01).sum() / len(pairs) * 100,
        "share_within_2pp_pct": (pairs["residual"].abs() <= 0.02).sum() / len(pairs) * 100,
        "share_within_5pp_pct": (pairs["residual"].abs() <= 0.05).sum() / len(pairs) * 100,
    }])


# =============================================================================
# Visualization
# =============================================================================


def plot_pre_match_rates_by_year(
    fhfa_yearly: pl.DataFrame,
    hmda_gse_yearly: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        return

    fig, ax = plt.subplots(figsize=(12, 6))
    fhfa_years = fhfa_yearly.filter(pl.col("matched_loans") > 0)
    hmda_years = hmda_gse_yearly.filter(pl.col("matched_gse_loans") > 0)

    ax.plot(
        fhfa_years["year"].to_list(), fhfa_years["match_rate"].to_list(),
        color=FHFA_COLOR, linewidth=2.5, marker="o", markersize=8,
        label="FHFA Match Rate",
    )
    ax.plot(
        hmda_years["activity_year"].to_list(), hmda_years["match_rate"].to_list(),
        color=HMDA_COLOR, linewidth=2.5, marker="s", markersize=8,
        label="HMDA GSE Match Rate",
    )

    overall_fhfa = fhfa_years["matched_loans"].sum() / fhfa_years["total_fhfa_loans"].sum()
    overall_hmda = hmda_years["matched_gse_loans"].sum() / hmda_years["total_hmda_gse_loans"].sum()
    ax.axhline(overall_fhfa, color=FHFA_COLOR, linestyle="--", alpha=0.5, linewidth=1)
    ax.axhline(overall_hmda, color=HMDA_COLOR, linestyle="--", alpha=0.5, linewidth=1)

    ax.set_xlabel("Year")
    ax.set_ylabel("Match Rate")
    ax.set_title("Pre-2018 FHFA-HMDA Match Rates by Year (GSE Loans)")
    all_rates = fhfa_years["match_rate"].to_list() + hmda_years["match_rate"].to_list()
    ax.set_ylim(max(0.0, min(all_rates) - 0.05), min(1.0, max(all_rates) + 0.05))
    ax.legend(loc="lower right")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_pre_round_contribution_by_year(
    round_year: pl.DataFrame,
    fhfa_yearly: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Stacked-bar of round contributions (% of FHFA) per acq year."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        return

    fig, ax = plt.subplots(figsize=(12, 6))
    years = sorted(round_year["year"].unique().to_list())
    rounds_present = sorted(round_year["match_round"].unique().to_list())

    bottom = np.zeros(len(years))
    for r in rounds_present:
        vals = []
        for y in years:
            row = round_year.filter(
                (pl.col("year") == y) & (pl.col("match_round") == r)
            )
            vals.append(float(row["rate_of_fhfa"][0]) if len(row) else 0.0)
        ax.bar(years, vals, bottom=bottom, label=f"Round {r}",
               color=PRE_ROUND_COLORS.get(r, MATCHED_COLOR),
               edgecolor="white", linewidth=0.5)
        bottom += np.array(vals)

    # Total points for reference
    totals = []
    for y in years:
        row = fhfa_yearly.filter(pl.col("year") == y)
        totals.append(float(row["match_rate"][0]) if len(row) else 0.0)
    ax.plot(years, totals, color=MATCHED_COLOR, marker="o",
            linestyle="", markersize=5, label="Total (R1+R2)")

    ax.set_xlabel("Acquisition year")
    ax.set_ylabel("Match rate (share of FHFA loans)")
    ax.set_title("Pre-2018: round contribution by acquisition year")
    ax.legend(loc="lower right")
    ax.grid(True, alpha=0.3, axis="y")
    ax.set_ylim(0, max(totals) * 1.1 if totals else 1)

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_pre_demographic_agreement_vs_random(
    agreement: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        return

    overall = agreement.filter(pl.col("scope") == "overall")
    variables = overall["variable"].to_list()
    agree = overall["agree_pct"].to_list()
    base = overall["random_pct"].to_list()

    fig, ax = plt.subplots(figsize=(10, 5))
    y = np.arange(len(variables))
    ax.barh(y - 0.2, agree, height=0.4, color=MATCHED_COLOR, label="Matched-pair agreement")
    ax.barh(y + 0.2, base, height=0.4, color=DENSITY_COLOR, label="Random-pair baseline")
    ax.set_yticks(y)
    ax.set_yticklabels(variables)
    for i, (a, b) in enumerate(zip(agree, base)):
        if a is not None:
            ax.text(a + 1, i - 0.2, f"{a:.1f}%", va="center", fontsize=9, color=MATCHED_COLOR)
        if b is not None:
            ax.text(b + 1, i + 0.2, f"{b:.1f}%", va="center", fontsize=9, color=DENSITY_COLOR)
    ax.set_xlabel("Agreement (%)")
    ax.set_xlim(0, 105)
    ax.set_title("Pre-2018 demographic agreement: matches vs random baseline")
    ax.legend(loc="lower right")
    ax.grid(True, alpha=0.3, axis="x")

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_pre_match_rates_by_loan_amount(
    loan_amount_rates: pl.DataFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        return

    fig, ax = plt.subplots(figsize=(12, 6))
    x = loan_amount_rates["loan_amount_bin"].to_numpy() / 1000

    hmda_total = np.nan_to_num(
        loan_amount_rates["hmda_total"].to_numpy().astype(float), nan=0
    )
    fhfa_total = np.nan_to_num(
        loan_amount_rates["fhfa_total"].to_numpy().astype(float), nan=0
    )
    hmda_pdf = hmda_total / hmda_total.sum() if hmda_total.sum() > 0 else hmda_total
    fhfa_pdf = fhfa_total / fhfa_total.sum() if fhfa_total.sum() > 0 else fhfa_total

    ax2 = ax.twinx()
    ax2.fill_between(x, hmda_pdf, alpha=0.15, color=HMDA_COLOR, label="HMDA Density")
    ax2.fill_between(x, fhfa_pdf, alpha=0.15, color=FHFA_COLOR, label="FHFA Density")
    ax2.set_ylabel("Density (PDF)", color="gray")
    ax2.tick_params(axis="y", labelcolor="gray")
    max_pdf = max(max(hmda_pdf) if len(hmda_pdf) else 0, max(fhfa_pdf) if len(fhfa_pdf) else 0)
    ax2.set_ylim(0, max_pdf * 1.3 if max_pdf > 0 else 1)

    ax.plot(x, loan_amount_rates["hmda_match_rate"].to_list(),
            color=HMDA_COLOR, linewidth=2, label="HMDA Match Rate", zorder=10)
    ax.plot(x, loan_amount_rates["fhfa_match_rate"].to_list(),
            color=FHFA_COLOR, linewidth=2, label="FHFA Match Rate", zorder=10)

    ax.set_xlabel("Loan Amount ($K)")
    ax.set_ylabel("Match Rate")
    ax.set_title("Pre-2018: Match Rates by Loan Amount")
    rates = (loan_amount_rates["hmda_match_rate"].to_list()
             + loan_amount_rates["fhfa_match_rate"].to_list())
    valid = [r for r in rates if r is not None and not np.isnan(r)]
    if valid:
        ax.set_ylim(max(0.0, min(valid) - 0.05), min(1.0, max(valid) + 0.05))
    ax.legend(loc="lower left")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


def plot_pre_state_match_rate_map(
    fhfa_states: pl.DataFrame, output_path: Path | None = None
) -> None:
    try:
        import plotly.graph_objects as go
    except ImportError:
        print("plotly not available, skipping state map")
        return

    state_fips_to_abbrev = {
        1:"AL",2:"AK",4:"AZ",5:"AR",6:"CA",8:"CO",9:"CT",10:"DE",11:"DC",12:"FL",
        13:"GA",15:"HI",16:"ID",17:"IL",18:"IN",19:"IA",20:"KS",21:"KY",22:"LA",
        23:"ME",24:"MD",25:"MA",26:"MI",27:"MN",28:"MS",29:"MO",30:"MT",31:"NE",
        32:"NV",33:"NH",34:"NJ",35:"NM",36:"NY",37:"NC",38:"ND",39:"OH",40:"OK",
        41:"OR",42:"PA",44:"RI",45:"SC",46:"SD",47:"TN",48:"TX",49:"UT",50:"VT",
        51:"VA",53:"WA",54:"WV",55:"WI",56:"WY",
    }
    territories = {"PR", "GU", "VI", "AS", "MP"}
    fhfa_with_abbrev = fhfa_states.with_columns(
        pl.col("state_code")
        .map_elements(lambda x: state_fips_to_abbrev.get(int(x) if x is not None else -1, ""),
                      return_dtype=pl.Utf8)
        .alias("state_abbrev")
    ).filter(~pl.col("state_abbrev").is_in(territories) & (pl.col("state_abbrev") != ""))

    rates = fhfa_with_abbrev["match_rate"].to_list()
    if not rates:
        return

    fig = go.Figure(go.Choropleth(
        locations=fhfa_with_abbrev["state_abbrev"].to_list(),
        z=rates,
        locationmode="USA-states",
        colorscale="RdYlGn",
        zmin=max(0.0, min(rates) - 0.02), zmax=min(1.0, max(rates) + 0.02),
        colorbar=dict(title="Match Rate", tickformat=".0%"),
        hovertemplate="<b>%{location}</b><br>Match Rate: %{z:.1%}<extra></extra>",
    ))
    fig.update_geos(scope="usa", showlakes=False, showland=True, landcolor="lightgray")
    fig.update_layout(
        title_text="Pre-2018 FHFA Match Rate by State",
        title_x=0.5, width=1000, height=600, margin=dict(l=0, r=0, t=60, b=0),
    )
    if output_path:
        fig.write_image(str(output_path), scale=2)
        print(f"Saved: {output_path}")


def plot_pre_amfi_inflation_diagnostic(
    crosswalk: pl.LazyFrame, fhfa: pl.LazyFrame, hmda: pl.LazyFrame,
    output_path: Path | None = None,
    interactive: bool = True,
) -> None:
    """Histogram of (implied - expected) inflation residual for R2 matches."""
    plt = _get_pyplot(interactive=interactive)
    if plt is None:
        return

    pairs = (
        crosswalk
        .filter(pl.col("match_round") == 2)
        .select([
            "year",
            pl.col("Enterprise Flag").alias("enterprise_flag"),
            pl.col("Record Number").alias("record_number"),
            "HMDAIndex", "activity_year",
        ])
        .join(fhfa.select([
            "year", "enterprise_flag", "record_number",
            pl.col("borrower_annual_income").alias("fhfa_inc"),
            pl.col("local_area_median_income").alias("amfi_acq"),
            "msa_code",
        ]), on=["year", "enterprise_flag", "record_number"], how="inner")
        .join(hmda.select(["HMDAIndex", "income"]).rename({"income": "hmda_inc"}),
              on="HMDAIndex", how="inner")
    ).collect()

    panel = (
        fhfa.filter(
            pl.col("local_area_median_income").is_not_null()
            & (pl.col("local_area_median_income") < PRE_FHFA_TOPCODE)
            & (pl.col("local_area_median_income") > 0)
        )
        .group_by(["msa_code", "year"])
        .agg(pl.col("local_area_median_income").mode().first().alias("amfi"))
        .collect()
    )
    pairs = pairs.join(
        panel.rename({"year": "activity_year", "amfi": "amfi_orig"}),
        on=["msa_code", "activity_year"], how="left",
    ).filter(
        pl.col("hmda_inc").is_not_null()
        & (pl.col("hmda_inc") > 0)
        & pl.col("amfi_orig").is_not_null()
        & pl.col("amfi_acq").is_not_null()
        & (pl.col("amfi_acq") > 0)
    )
    if len(pairs) == 0:
        return

    pairs = pairs.with_columns(
        (pl.col("fhfa_inc").cast(pl.Float64) / pl.col("hmda_inc").cast(pl.Float64)
         - pl.col("amfi_acq").cast(pl.Float64) / pl.col("amfi_orig").cast(pl.Float64))
        .alias("residual")
    )
    resid = pairs["residual"].to_numpy()
    resid = resid[(resid > -0.5) & (resid < 0.5)]  # clip outliers for the chart

    fig, ax = plt.subplots(figsize=(10, 5))
    # Burnt orange accent (intentionally outside PRE_ROUND_COLORS so it
    # contrasts visibly with the dashed Midnight Blue zero line below).
    ax.hist(resid, bins=80, color="#BA4A00", alpha=0.85,
            edgecolor="white", linewidth=0.4)
    ax.axvline(0, color=MATCHED_COLOR, linestyle="--", linewidth=1)
    ax.axvspan(-0.02, 0.02, color=MATCHED_COLOR, alpha=0.08, label="±2pp band")
    ax.set_xlabel("Implied (FHFA/HMDA) − Expected (AMFI_acq/AMFI_orig)")
    ax.set_ylabel("R2 matched pairs")
    ax.set_title("Pre-2018 R2: AMFI-inflation diagnostic\n"
                 "Per-pair implied vs expected inflation ratio")
    ax.legend(loc="upper right")
    ax.grid(True, alpha=0.3, axis="y")

    plt.tight_layout()
    if output_path:
        plt.savefig(output_path, dpi=150)
        print(f"Saved: {output_path}")
    if interactive:
        plt.show()
    plt.close(fig)


# =============================================================================
# Main runner
# =============================================================================


def _shared_print_section(title: str) -> None:
    print(f"\n{'=' * 70}\n {title}\n{'=' * 70}\n")


def run_validation_pre(
    crosswalk_path: Path | str | None = None,
    output_dir: Path | None = None,
    save_plots: bool = True,
    show_plots: bool = False,
) -> dict[str, Any]:
    """Run all pre-2018 validation analyses.

    Args:
        crosswalk_path: Optional explicit path to the combined R1+R2
            crosswalk. If None, looks in the conventional locations
            (see ``load_pre_crosswalk``).
        output_dir: Where to save figures. Defaults to
            ``docs/matching/figures/fhfa_hmda_pre2018/``.
        save_plots: Whether to save plots to disk.
        show_plots: Whether to display plots interactively.

    Returns:
        Dictionary with all computed DataFrames and summary statistics.
    """
    if output_dir is None:
        output_dir = PRE_OUTPUT_DIR
    if save_plots:
        output_dir.mkdir(parents=True, exist_ok=True)

    print("Loading data...")
    crosswalk = load_pre_crosswalk(crosswalk_path)
    fhfa = load_pre_fhfa_silver()
    hmda = load_pre_hmda_silver()

    n_matches = crosswalk.select(pl.len()).collect().item()
    n_fhfa = fhfa.select(pl.len()).collect().item()
    n_hmda = hmda.select(pl.len()).collect().item()
    print(f"Crosswalk: {n_matches:,} matched pairs")
    print(f"FHFA: {n_fhfa:,} loans (years {PRE_AVAILABLE_YEARS})")
    print(f"HMDA: {n_hmda:,} loans (filtered to GSE-eligible cohort)")

    _shared_print_section("1. Match round breakdown")
    round_breakdown = compute_pre_match_rates_by_round(crosswalk)
    print(round_breakdown)

    _shared_print_section("2. FHFA match rates by year")
    fhfa_yearly = compute_pre_match_rates_by_year(crosswalk, fhfa)
    print(fhfa_yearly)
    overall_fhfa = (
        fhfa_yearly["matched_loans"].sum() / fhfa_yearly["total_fhfa_loans"].sum()
    )
    print(f"\nOverall FHFA match rate: {overall_fhfa:.1%}")

    _shared_print_section("3. HMDA GSE-sold match rates by year (PT 1, 3)")
    hmda_gse_yearly = compute_pre_hmda_gse_match_rates_by_year(crosswalk, hmda)
    print(hmda_gse_yearly)
    overall_hmda = (
        hmda_gse_yearly["matched_gse_loans"].sum()
        / hmda_gse_yearly["total_hmda_gse_loans"].sum()
    )
    print(f"\nOverall HMDA GSE match rate: {overall_hmda:.1%}")

    _shared_print_section("4. Match rates by enterprise")
    fhfa_enterprise = compute_pre_match_rates_by_enterprise(crosswalk, fhfa)
    hmda_enterprise = compute_pre_hmda_gse_match_rates_by_enterprise(crosswalk, hmda)
    print("FHFA perspective:")
    print(fhfa_enterprise)
    print("\nHMDA GSE perspective:")
    print(hmda_enterprise)

    _shared_print_section("5. FHFA match rates by state (top 20 by volume)")
    fhfa_states = compute_pre_match_rates_by_state(crosswalk, fhfa)
    print(fhfa_states.head(20))

    _shared_print_section("6. Round contribution per year")
    round_year = compute_pre_match_rates_by_round_and_year(crosswalk, fhfa)
    print(round_year)

    _shared_print_section("7. Demographic agreement vs random baseline")
    agreement = compute_pre_demographic_agreement_vs_random(crosswalk, fhfa, hmda)
    print(agreement)

    _shared_print_section("8. Loan-amount diff distribution per round")
    amt_diff = compute_pre_amount_diff_distribution(crosswalk, fhfa, hmda)
    print(amt_diff)

    _shared_print_section("9. Income agreement per round (raw vs AMFI-deflated)")
    income_agree = compute_pre_income_agreement_per_round(crosswalk, fhfa, hmda)
    print(income_agree)

    _shared_print_section("10. R2 AMFI-inflation diagnostic")
    amfi_diag = compute_pre_amfi_inflation_diagnostic(crosswalk, fhfa, hmda)
    print(amfi_diag)

    _shared_print_section("11. Match rates by tract income ratio")
    tract_rates = compute_pre_match_rates_by_tract_income_ratio(crosswalk, fhfa)
    print(tract_rates)

    loan_amount_rates = compute_pre_match_rates_by_loan_amount(crosswalk, fhfa, hmda)

    if (show_plots or save_plots) and HAS_MATPLOTLIB:
        _shared_print_section("12. Visualizations")
        plot_pre_match_rates_by_year(
            fhfa_yearly, hmda_gse_yearly,
            output_path=output_dir / "temporal_match_rates.png" if save_plots else None,
            interactive=show_plots,
        )
        plot_pre_round_contribution_by_year(
            round_year, fhfa_yearly,
            output_path=output_dir / "round_contribution_by_year.png" if save_plots else None,
            interactive=show_plots,
        )
        plot_pre_demographic_agreement_vs_random(
            agreement,
            output_path=output_dir / "demographic_agreement.png" if save_plots else None,
            interactive=show_plots,
        )
        plot_pre_match_rates_by_loan_amount(
            loan_amount_rates,
            output_path=output_dir / "match_rates_by_loan_amount.png" if save_plots else None,
            interactive=show_plots,
        )
        plot_pre_state_match_rate_map(
            fhfa_states,
            output_path=output_dir / "state_match_rate_map.png" if save_plots else None,
        )
        plot_pre_amfi_inflation_diagnostic(
            crosswalk, fhfa, hmda,
            output_path=output_dir / "amfi_inflation_diagnostic.png" if save_plots else None,
            interactive=show_plots,
        )

    return {
        "round_breakdown": round_breakdown,
        "fhfa_yearly": fhfa_yearly,
        "hmda_gse_yearly": hmda_gse_yearly,
        "fhfa_enterprise": fhfa_enterprise,
        "hmda_enterprise": hmda_enterprise,
        "fhfa_states": fhfa_states,
        "round_year": round_year,
        "agreement": agreement,
        "amount_diff": amt_diff,
        "income_agreement": income_agree,
        "amfi_diagnostic": amfi_diag,
        "tract_income_rates": tract_rates,
        "loan_amount_rates": loan_amount_rates,
        "overall_fhfa_rate": overall_fhfa,
        "overall_hmda_gse_rate": overall_hmda,
    }

# =============================================================================
# Backward-compatibility alias.
# =============================================================================

# `run_validation` historically referred to the post-2018 runner; preserve
# that meaning for any downstream callers that imported it directly.
run_validation = run_validation_post
