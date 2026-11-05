"""Validation for the HMDA-FHLB matching workflow.

Two kinds live here. **Pre-match data validation** (schema and unit-magnitude checks
the workflow runs on its cleaned inputs) and the **post-match validation report**:
headline statistics, match counts by year, match rates by loan amount from each
side, the HMDA purchaser-type distribution of the matches, and a lender (PFI)
summary, for both eras together in one figure.

Match rates in this workflow are a property of FHLB acquisition behaviour, not of
the matcher: FHLBs acquire only a subset of their members' originations, so a
member's unmatched loans are mostly loans that were never sold to an FHLB. The
informative output is which lenders sell to FHLBs and what they sell.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import numpy as np
import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.matching.match_hmda_fhlb.config import (
    HMDAFHLBMatchingConfig,
)
from mortgage_data_manager.matching.validation_common import (
    COLORS,
    Side,
    binned,
    get_pyplot,
    matched_count,
    matched_ids,
    panel_gridspec,
    print_section,
    rate_by,
    save_and_close,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

logger = get_logger(__name__)

# =============================================================================
# Configuration
# =============================================================================

# Year range for analysis
MIN_YEAR_POST2018 = 2018
MAX_YEAR_POST2018 = 2025
MIN_YEAR_PRE2018 = 2009
MAX_YEAR_PRE2018 = 2017

# Output directory for figures
VALIDATION_OUTPUT_DIR = HMDAFHLBMatchingConfig.PROJECT_DIR / "docs" / "matching" / "figures"

# Shared chart palette; CATEGORICAL below keeps this workflow's own ordering.
PALETTE = COLORS

# Categorical sequence (hunter green + gold as primary pair)
CATEGORICAL = [
    PALETTE["hunter"],
    PALETTE["gold"],
    PALETTE["midnight"],
    PALETTE["terracotta"],
    PALETTE["purple"],
    PALETTE["grey"],
    PALETTE["mulberry"],
]

# Dataset-specific aliases
HMDA_COLOR = PALETTE["hunter"]
FHLB_COLOR = PALETTE["gold"]


class Era(NamedTuple):
    """One analysis period: how it is keyed in ``results`` and how it is drawn."""

    key: str
    label: str
    color: str
    marker: str


ERAS = (
    Era(
        "pre2018",
        f"Pre-2018 ({MIN_YEAR_PRE2018}-{MAX_YEAR_PRE2018})",
        PALETTE["midnight"],
        "o",
    ),
    Era(
        "post2018",
        f"Post-2018 ({MIN_YEAR_POST2018}-{MAX_YEAR_POST2018})",
        PALETTE["gold"],
        "s",
    ),
)

# Standing interpretation printed at the end of every run: the match rate here is a
# property of FHLB acquisition behaviour, not of the matcher, and readers reliably
# mistake it for one.
INTERPRETATION = """
IMPORTANT CONTEXT FOR HMDA-FHLB MATCHING:

1. A partial match rate is EXPECTED and does NOT indicate a methodology problem.
   FHLBs acquire only a subset of their members' originations, so most unmatched
   member loans were never sold to an FHLB.

2. The informative output is which lenders (PFIs) sell loans to FHLBs, what types
   of loans FHLBs acquire, and how that varies over time.

3. Purchaser type: FHLBs have no dedicated HMDA purchaser code, so most matches
   report purchaser_type=9 ("Other"), with the remainder spread over the bank and
   not-applicable codes.
"""


# =============================================================================
# DATA VALIDATION (Pre-match) - Schema and Magnitude Checks
# =============================================================================

# Expected Magnitude Ranges (Min Mean, Max Mean)
# Used to verify if units are correct (e.g. dollars vs thousands, percent vs decimal)
MAGNITUDE_CHECKS = {
    # Dollars (Raw) - Expect average around 100k-500k
    "amount": (10_000, 5_000_000),
    # Income (Raw Annual) - Expect average around 50k-300k
    "income": (20_000, 1_000_000),
    # Rate (Percent) - Expect average around 2.0 - 10.0
    # If decimal (0.05), it will be < 1.0
    "rate": (1.0, 15.0),
    # Term (Months) - Expect average around 180-360
    "term": (60, 480),
    # LTV (Percent) - Expect average around 60-95
    "ltv": (10, 150),
    # DTI (Percent) - Expect average around 20-50
    "dti": (5, 80),
}


def check_magnitudes(df: pl.DataFrame, col_map: dict[str, str], dataset_name: str) -> bool:
    """Check if numeric columns fall within expected average magnitudes.

    Args:
        df: DataFrame to check
        col_map: Maps logical name (e.g. 'income') to dataframe column name
        dataset_name: Name for logging

    Returns:
        True if all checks pass
    """
    passed = True

    # Calculate means for numeric columns, ignoring nulls and common HMDA sentinels
    stats = df.select(
        [
            pl.col(actual_col)
            .fill_nan(None)
            .replace(-99999, None)  # Treat HMDA sentinel as null
            .mean()
            .alias(f"mean_{logic_col}")
            for logic_col, actual_col in col_map.items()
            if actual_col in df.columns
        ]
    ).to_dict(as_series=False)

    for logic_col, actual_col in col_map.items():
        if actual_col not in df.columns:
            continue

        key = f"mean_{logic_col}"
        if key not in stats or not stats[key]:
            continue

        mean_val = stats[key][0]
        if mean_val is None:
            continue

        expected_min, expected_max = MAGNITUDE_CHECKS.get(logic_col, (None, None))

        if expected_min is not None:
            if not (expected_min <= mean_val <= expected_max):
                logger.error(
                    "[%s] Magnitude Check FAILED for '%s' (%s). Mean: %s. Expected range: %s - %s.",
                    dataset_name,
                    actual_col,
                    logic_col,
                    f"{mean_val:,.2f}",
                    f"{expected_min:,.0f}",
                    f"{expected_max:,.0f}",
                )
                passed = False
            else:
                logger.info(
                    "[%s] Magnitude OK: '%s' mean = %s",
                    dataset_name,
                    actual_col,
                    f"{mean_val:,.2f}",
                )

    return passed


def check_schema(df: pl.DataFrame, required_cols: list[str], dataset_name: str) -> bool:
    """Verify existence of required columns.

    Args:
        df: The frame to validate.
        required_cols: Column names that must be present.
        dataset_name: Dataset name, used in the failure message.

    Returns:
        True when every required column is present.
    """
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        logger.error("[%s] Schema Check FAILED. Missing columns: %s", dataset_name, missing)
        return False
    return True


def validate_hmda_post2018(df: pl.DataFrame) -> bool:
    """Validate HMDA Post-2018 Data schema and magnitudes.

    Args:
        df: The frame to validate.

    Returns:
        True when the schema and magnitude checks pass.
    """
    logger.info("Validating HMDA Data (Post-2018)")

    # 1. Schema Check
    required_cols = [
        "activity_year",
        "census_tract",
        "loan_amount",
        "loan_type",
        "total_units",
        "occupancy_type",
        "income",
        "loan_term",
        "interest_rate",
        "combined_loan_to_value_ratio",
        "debt_to_income_ratio",
    ]
    if not check_schema(df, required_cols, "HMDA"):
        return False

    # 2. Magnitude Check
    col_map = {
        "amount": "loan_amount",
        "income": "income",
        "rate": "interest_rate",
        "term": "loan_term",
        "ltv": "combined_loan_to_value_ratio",
        "dti": "debt_to_income_ratio",
    }

    return check_magnitudes(df, col_map, "HMDA")


def validate_fhlb_post2018(df: pl.DataFrame) -> bool:
    """Validate FHLB Post-2018 Data schema and magnitudes.

    Args:
        df: The frame to validate.

    Returns:
        True when the schema and magnitude checks pass.
    """
    logger.info("Validating FHLB Data (Post-2018)")

    # 1. Schema Check
    required_cols = [
        "NoteDate",
        "Census Tract String",
        "NoteAmount",
        "Loan Type",
        "PropertyUnitCount",
        "PropertyUsageType",
        "TotalAnnualIncomeAmount",
        "LoanAmortizationMaxTermMonths",
        "NoteRatePercent",
        "LTVRatioPercent",
        "TotalDebtExpenseRatioPercent",
    ]
    if not check_schema(df, required_cols, "FHLB"):
        return False

    # 2. Magnitude Check
    col_map = {
        "amount": "NoteAmount",
        "income": "TotalAnnualIncomeAmount",
        "rate": "NoteRatePercent",
        "term": "LoanAmortizationMaxTermMonths",
        "ltv": "LTVRatioPercent",
        "dti": "TotalDebtExpenseRatioPercent",
    }

    return check_magnitudes(df, col_map, "FHLB")


def validate_inputs(match_folder: Path) -> None:
    """Run validation on cleaned parquet files before matching.

    Args:
        match_folder: Directory holding the cleaned parquet files.
    """
    hmda_path = match_folder / "hmda_fhlb_members_post2018.parquet"
    fhlb_path = match_folder / "fhlb_acquisitions_post2018.parquet"

    if not hmda_path.exists() or not fhlb_path.exists():
        logger.warning("Data validation skipped: One or more input files missing")
        return

    # Load samples to save memory (first 10,000 rows should be enough for magnitude)
    try:
        hmda = pl.scan_parquet(hmda_path).limit(10000).collect()
        if not validate_hmda_post2018(hmda):
            logger.warning("HMDA Validation FAILED. Proceeding with caution")
    except Exception as e:
        logger.error("Error validating HMDA data: %s", e)

    try:
        fhlb = pl.scan_parquet(fhlb_path).limit(10000).collect()
        if not validate_fhlb_post2018(fhlb):
            logger.warning("FHLB Validation FAILED. Proceeding with caution")
    except Exception as e:
        logger.error("Error validating FHLB data: %s", e)


# =============================================================================
# MATCH QUALITY VALIDATION (Post-match) - Data
# =============================================================================


def load_era_data(era: Era) -> tuple[pl.DataFrame, pl.DataFrame | None, pl.DataFrame | None]:
    """Load one era's matches and the two sides they were matched from.

    Args:
        era: Which analysis period to load; its ``key`` is the filename suffix.

    Returns:
        ``(matches, hmda_members, fhlb_acquisitions)``. The two side frames are None
        when the workflow did not persist them; the rates that need them are skipped.

    Raises:
        FileNotFoundError: If the era's matches file has not been built.
    """
    matches_path = (
        HMDAFHLBMatchingConfig.HMDA_FHLB_MATCHING_DIR / f"hmda_fhlb_matches_{era.key}.parquet"
    )
    hmda_path = (
        HMDAFHLBMatchingConfig.HMDA_FHLB_MATCHING_DIR / f"hmda_fhlb_members_{era.key}.parquet"
    )
    fhlb_path = (
        HMDAFHLBMatchingConfig.HMDA_FHLB_MATCHING_DIR / f"fhlb_acquisitions_{era.key}.parquet"
    )
    if not matches_path.exists():
        raise FileNotFoundError(f"Matches file not found: {matches_path}")
    return (
        pl.read_parquet(matches_path),
        pl.read_parquet(hmda_path) if hmda_path.exists() else None,
        pl.read_parquet(fhlb_path) if fhlb_path.exists() else None,
    )


def load_lender_statistics() -> pl.DataFrame | None:
    """The post-2018 per-LEI match statistics, or None when they have not been built.

    Only the post-2018 era is keyed by LEI; the pre-2018 file uses respondent ids,
    which the lender summary does not mix in.
    """
    stats_path = (
        HMDAFHLBMatchingConfig.HMDA_FHLB_MATCHING_DIR / "lender_match_statistics_post2018.csv"
    )
    return pl.read_csv(stats_path) if stats_path.exists() else None


def era_sides(
    era: Era, matches: pl.DataFrame, hmda: pl.DataFrame | None, fhlb: pl.DataFrame | None
) -> dict[str, Side]:
    """The measured sides one era's persisted inputs allow.

    The FHLB loan id is only unique within a year before 2018, so that era's FHLB key
    is ``(year, LoanCharacteristicsID)``; the matches carry the year as
    ``activity_year`` and the acquisitions as ``data_year``.

    The post-2018 acquisitions file is the matcher's candidate table, not a loan list:
    a loan whose note amount is an exact $10k multiple is copied (``i_Duplicate = 1``)
    so it can meet HMDA in either neighbouring amount bin. The copies are dropped here,
    or every such loan would count twice in the FHLB-side denominator and, when
    matched, twice in the numerator.

    Args:
        era: The analysis period.
        matches: The era's matched pairs (both sides' columns).
        hmda: The HMDA member originations, or None if not persisted.
        fhlb: The FHLB acquisitions, or None if not persisted.

    Returns:
        ``{"hmda": Side, "fhlb": Side}`` minus any side whose universe is missing.
    """
    out: dict[str, Side] = {}
    if hmda is not None:
        out["hmda"] = Side(hmda, matched_ids(matches, "HMDAIndex"), "HMDAIndex", "hmda")
    if fhlb is not None:
        if "i_Duplicate" in fhlb.columns:
            fhlb = fhlb.filter(pl.col("i_Duplicate") == 0)
        if era.key == "pre2018":
            key = ["data_year", "LoanCharacteristicsID"]
            matched = matched_ids(matches, ["activity_year", "LoanCharacteristicsID"], as_cols=key)
        else:
            key = ["LoanCharacteristicsID"]
            matched = matched_ids(matches, key)
        out["fhlb"] = Side(fhlb, matched, key, "fhlb")
    return out


# =============================================================================
# MATCH QUALITY VALIDATION (Post-match) - Statistics
# =============================================================================


def compute_overall_stats(matches: pl.DataFrame, sides: dict[str, Side]) -> dict[str, Any]:
    """Headline counts and the match rate from each available side.

    Args:
        matches: The matched pairs.
        sides: The era's measured sides, from `era_sides`.

    Returns:
        ``n_matches`` plus, per side, ``n_{side}``, ``n_{side}_matched`` and
        ``{side}_match_rate`` (None when that side's universe is missing).
    """
    stats: dict[str, Any] = {"n_matches": len(matches)}
    for name in ("hmda", "fhlb"):
        side = sides.get(name)
        n = (
            len(side.universe)
            if side is not None and isinstance(side.universe, pl.DataFrame)
            else None
        )
        matched = matched_count(side) if side is not None else None
        stats[f"n_{name}"] = n
        stats[f"n_{name}_matched"] = matched
        stats[f"{name}_match_rate"] = (matched / n if n else 0) if matched is not None else None
    return stats


def compute_yearly_match_counts(matches: pl.DataFrame) -> pl.DataFrame:
    """Match counts by year.

    Args:
        matches: The matched pairs.

    Returns:
        One row per year, with its match count; empty if no year column is present.
    """
    year_col = next(
        (c for c in ("activity_year", "year", "origination_year", "Year") if c in matches.columns),
        None,
    )
    if year_col is None:
        return pl.DataFrame({"year": [], "matches": []})
    return (
        matches.group_by(year_col)
        .agg(pl.len().alias("matches"))
        .sort(year_col)
        .rename({year_col: "year"})
    )


def compute_match_rates_by_loan_amount(
    side: Side, amount: pl.Expr, bin_size: int = 25_000, max_amount: int = 750_000
) -> pl.DataFrame:
    """One side's match rate by loan-amount bin (bin midpoints).

    Args:
        side: The side to measure.
        amount: Expression giving that side's loan amount in dollars.
        bin_size: Bin width in dollars.
        max_amount: Largest amount included.

    Returns:
        One row per bin with the side's counts and rate.
    """
    universe = (
        side.universe.lazy()
        .with_columns(amount.cast(pl.Int64).alias("loan_amount_dollars"))
        .filter(pl.col("loan_amount_dollars").is_between(0, max_amount))
        .with_columns(
            binned(
                "loan_amount_dollars",
                bin_size,
                mode="midpoint",
                dtype=pl.Int64,
                name="loan_amount_bin",
            )
        )
    )
    return rate_by(
        universe,
        side.matched,
        side.on,
        "loan_amount_bin",
        prefix=side.prefix,
        sort_by="loan_amount_bin",
        descending=False,
    ).collect()


#: How each era's FHLB acquisitions report the note amount.
FHLB_AMOUNT_DOLLARS = {
    "pre2018": pl.col("Round Loan Amount") * 1000,
    "post2018": pl.col("LoanAcquisitionActualUPBAmt"),
}

# HMDA purchaser_type across both eras: pre-2018 code 7 pooled life insurers, credit
# unions, mortgage banks and finance companies; from 2018 it splits into 71 and 72.
PURCHASER_LABELS = {
    0: "Not Applicable",
    1: "Fannie Mae",
    2: "Ginnie Mae",
    3: "Freddie Mac",
    4: "Farmer Mac",
    5: "Private Securitizer",
    6: "Commercial Bank/Savings",
    7: "Life Insurer/Credit Union/Mortgage Bank/Finance Co",
    71: "Credit Union/Mortgage Co/Finance Co",
    72: "Life Insurance Co",
    8: "Affiliate Institution",
    9: "Other",
}


def compute_purchaser_type_distribution(matches: pl.DataFrame) -> pl.DataFrame:
    """HMDA purchaser types among the matched records.

    Args:
        matches: The matched pairs.

    Returns:
        One row per purchaser type with its count and share, largest first; empty if
        the matches carry no purchaser column.
    """
    col = next(
        (
            c
            for c in ("purchaser_type", "purchaser_type_s", "purchaser_type_h")
            if c in matches.columns
        ),
        None,
    )
    if col is None:
        return pl.DataFrame()
    return (
        matches.group_by(col)
        .agg(pl.len().alias("count"))
        .with_columns(
            (pl.col("count") / pl.col("count").sum()).alias("pct"),
            pl.col(col)
            .replace_strict(PURCHASER_LABELS, default="Unknown")
            .alias("purchaser_label"),
        )
        .sort("count", descending=True)
        .rename({col: "purchaser_type"})
    )


def analyze_lender_statistics(stats_df: pl.DataFrame) -> dict[str, Any]:
    """Summarize the per-lender match statistics.

    Args:
        stats_df: Per-lender (and possibly per-year) match statistics.

    Returns:
        Active-lender count, the top 20 lenders by matches, and a yearly summary when
        the statistics carry a year column.
    """
    if stats_df is None or len(stats_df) == 0:
        return {}

    def first_present(candidates: tuple[str, ...]) -> str | None:
        return next((c for c in candidates if c in stats_df.columns), None)

    lei_col = first_present(("lei", "LEI", "lender_id"))
    match_col = first_present(("matched_loans", "total_matches", "matches"))
    year_col = first_present(("activity_year", "year", "Year"))
    if lei_col is None or match_col is None:
        return {"error": "Required columns not found in lender statistics"}

    active = stats_df.filter(pl.col(match_col) > 0)
    result: dict[str, Any] = {
        "n_active_lenders": active[lei_col].n_unique(),
        "top_lenders": (
            stats_df.group_by(lei_col)
            .agg(pl.col(match_col).sum().alias("total_matches"))
            .sort("total_matches", descending=True)
            .head(20)
        ),
    }
    if year_col is not None:
        result["yearly_summary"] = (
            active.group_by(year_col)
            .agg(
                pl.col(lei_col).n_unique().alias("active_lenders"),
                pl.col(match_col).sum().alias("total_matches"),
            )
            .sort(year_col)
        )
    return result


# =============================================================================
# MATCH QUALITY VALIDATION (Post-match) - Figure
#
# One figure, built by ``plot_validation_panel``. Each panel draws into an axis it is
# handed; a panel whose era or dataset did not load blanks its own axis.
# =============================================================================

# The two loan-amount panels: the results prefix they read, the column prefix inside
# those frames, and the axis label that names the direction.
LOAN_AMOUNT_SIDES = {
    "hmda": ("loan_amount_rates", "Match Rate (HMDA -> FHLB)"),
    "fhlb": ("loan_amount_rates_fhlb", "Match Rate (FHLB -> HMDA)"),
}

# Vertical inches allotted to one grid row; the canvas is sized from the row count.
PANEL_HEIGHT_IN = 5.0

PURCHASER_NOTE = (
    "FHLBs have no dedicated HMDA purchaser code; most matches show purchaser_type=9 ('Other')."
)


def era_frame(results: dict[str, Any], prefix: str, era: Era) -> pl.DataFrame | None:
    """One era's frame for a statistic, or None when that era has no rows.

    Args:
        results: The assembled validation results.
        prefix: Statistic name prefix to look up.
        era: Which era's frame to return.

    Returns:
        That era's frame, or None.
    """
    df: pl.DataFrame | None = results.get(f"{prefix}_{era.key}")
    return None if df is None or len(df) == 0 else df


def _panel_yearly_counts(ax: Any, results: dict[str, Any]) -> None:
    """Match counts per year, one bar series per era."""
    drawn = 0
    for era in ERAS:
        df = era_frame(results, "yearly", era)
        if df is None:
            continue
        years, counts = df["year"].to_list(), df["matches"].to_list()
        ax.bar(years, counts, color=era.color, alpha=0.8, label=era.label)
        for year, count in zip(years, counts):
            ax.text(year, count, f"{count:,}", ha="center", va="bottom", fontsize=7)
        drawn += 1
    if drawn == 0:
        ax.set_axis_off()
        return
    if drawn == len(ERAS):
        # Mark the reporting break: the two eras are matched on different HMDA schemas.
        boundary = MIN_YEAR_POST2018 - 0.5
        ax.axvline(boundary, color=PALETTE["grey"], linestyle="--", alpha=0.5, linewidth=1.5)
        ax.text(
            boundary,
            ax.get_ylim()[1] * 0.95,
            "2018 HMDA\nredesign",
            ha="center",
            va="top",
            fontsize=8,
            style="italic",
            color=PALETTE["grey"],
        )
    ax.set_xlabel("Year")
    ax.set_ylabel("Number of Matches")
    ax.set_title("Match counts by year")
    ax.grid(True, alpha=0.3, axis="y")


def _panel_loan_amount(ax: Any, results: dict[str, Any], side: str) -> None:
    """Match rates across the loan-amount distribution, with that era's density behind."""
    prefix, ylabel = LOAN_AMOUNT_SIDES[side]
    density_ax = None
    rates_seen: list[float] = []
    for era in ERAS:
        df = era_frame(results, prefix, era)
        if df is None:
            continue
        x = df["loan_amount_bin"].to_numpy() / 1000
        totals = df[f"{side}_total"].to_numpy().astype(float)
        rates = df[f"{side}_match_rate"].to_list()
        if totals.sum() > 0:
            density_ax = density_ax if density_ax is not None else ax.twinx()
            density_ax.fill_between(x, totals / totals.sum(), color=era.color, alpha=0.12)
        ax.plot(
            x,
            rates,
            color=era.color,
            linewidth=2,
            marker=era.marker,
            markersize=4,
            label=era.label,
            zorder=10,
        )
        rates_seen += [r for r in rates if r is not None and not np.isnan(r)]
    if not rates_seen:
        ax.set_axis_off()
        return
    if density_ax is not None:
        density_ax.set_ylabel("Density (PDF)", color=PALETTE["grey"], fontsize=9)
        density_ax.tick_params(axis="y", labelcolor=PALETTE["grey"], labelsize=8)
        density_ax.set_ylim(0, density_ax.get_ylim()[1] * 1.3)
    ax.set_xlabel("Loan Amount ($K)")
    ax.set_ylabel(ylabel)
    ax.set_title(f"Match rates by loan amount ({side.upper()} side)")
    ax.set_ylim(max(0.0, min(rates_seen) - 0.05), min(1.0, max(rates_seen) + 0.05))
    ax.grid(True, alpha=0.3)


def _panel_purchaser(ax: Any, results: dict[str, Any], era: Era) -> None:
    """Top-10 HMDA purchaser types among one era's matches."""
    df = era_frame(results, "purchaser_distribution", era)
    if df is None:
        ax.set_axis_off()
        return
    df = df.sort("pct", descending=True).head(10)
    pcts, counts = df["pct"].to_list(), df["count"].to_list()
    x = np.arange(len(df))
    ax.bar(x, pcts, color=era.color, alpha=0.8)
    for i, (pct, count) in enumerate(zip(pcts, counts)):
        ax.text(i, pct + 0.01, f"{pct:.1%}\n({count:,})", ha="center", va="bottom", fontsize=7)
    ax.set_xticks(x)
    ax.set_xticklabels(df["purchaser_label"].to_list(), rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Percentage of Matches")
    ax.set_ylim(0, max(pcts) * 1.25)
    ax.set_title(f"Purchaser type distribution -- {era.label}")
    ax.grid(True, alpha=0.3, axis="y")


def _panel_lender_histogram(ax: Any, lender_stats: dict[str, Any]) -> None:
    """How matches spread across the top PFIs."""
    top_lenders = lender_stats.get("top_lenders")
    if top_lenders is None or len(top_lenders) == 0:
        ax.set_axis_off()
        return
    ax.hist(
        top_lenders["total_matches"].to_numpy(),
        bins=20,
        color=HMDA_COLOR,
        alpha=0.8,
        edgecolor="white",
    )
    ax.set_xlabel("Total Matches per Lender")
    ax.set_ylabel("Number of Lenders")
    ax.set_title("Matches per lender (top 20 PFIs)")
    ax.grid(True, alpha=0.3)


def _panel_lender_concentration(ax: Any, lender_stats: dict[str, Any]) -> None:
    """Cumulative share of matches held by the largest PFIs."""
    top_lenders = lender_stats.get("top_lenders")
    if top_lenders is None or len(top_lenders) == 0:
        ax.set_axis_off()
        return
    sorted_counts = np.sort(top_lenders["total_matches"].to_numpy())[::-1]
    cumulative = np.cumsum(sorted_counts) / sorted_counts.sum()
    ax.plot(
        np.arange(1, len(cumulative) + 1), cumulative, color=HMDA_COLOR, linewidth=2, marker="o"
    )
    ax.axhline(0.5, color=PALETTE["grey"], linestyle="--", alpha=0.7, label="50% of matches")
    ax.axhline(0.9, color=PALETTE["grey"], linestyle=":", alpha=0.7, label="90% of matches")
    ax.set_xlabel("Number of Top Lenders")
    ax.set_ylabel("Cumulative Share of Matches")
    ax.set_title("Match concentration among top lenders")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)


def _figure_rows(results: dict[str, Any]) -> list[Sequence[Callable[[Any], None]]]:
    """The grid rows to draw, in order, skipping any with no data behind them.

    A row is one full-width panel or a side-by-side pair. Rows are dropped rather than
    drawn blank so a partial run still produces a figure with no dead space.

    Returns:
        The rows, each a sequence of panel drawers taking an axis.
    """
    rows: list[Sequence[Callable[[Any], None]]] = []
    if any(era_frame(results, "yearly", era) is not None for era in ERAS):
        rows.append([partial(_panel_yearly_counts, results=results)])
    loan_amount_panels = [
        partial(_panel_loan_amount, results=results, side=side)
        for side, (prefix, _) in LOAN_AMOUNT_SIDES.items()
        if any(era_frame(results, prefix, era) is not None for era in ERAS)
    ]
    if loan_amount_panels:
        rows.append(loan_amount_panels)
    purchaser_panels = [
        partial(_panel_purchaser, results=results, era=era)
        for era in ERAS
        if era_frame(results, "purchaser_distribution", era) is not None
    ]
    if purchaser_panels:
        rows.append(purchaser_panels)
    lender_stats = results.get("lender_analysis") or {}
    top_lenders = lender_stats.get("top_lenders")
    if top_lenders is not None and len(top_lenders) > 0:
        rows.append(
            [
                partial(_panel_lender_histogram, lender_stats=lender_stats),
                partial(_panel_lender_concentration, lender_stats=lender_stats),
            ]
        )
    return rows


def plot_validation_panel(
    results: dict[str, Any], output_path: Path | None = None, interactive: bool = False
) -> None:
    """Render the validation figure as one stacked grid.

    Rows, in order: match counts by year (full width), match rates by loan amount
    (HMDA side | FHLB side), purchaser-type distribution (one column per era), and
    lender concentration. Both eras share every rate panel so they can be read
    against each other; rows with no data are omitted and the canvas shrinks.

    Args:
        results: The accumulated ``run_validation`` results.
        output_path: Where to save the figure (PNG). Skipped if ``None``.
        interactive: Whether to display the figure interactively.
    """
    plt = get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return
    rows = _figure_rows(results)
    if not rows:
        print("No validation results to plot")
        return

    fig = plt.figure(figsize=(15, PANEL_HEIGHT_IN * len(rows) + 1.5))
    grid = panel_gridspec(
        fig,
        len(rows),
        2,
        title=f"HMDA-FHLB match validation ({MIN_YEAR_PRE2018}-{MAX_YEAR_POST2018})\n{PURCHASER_NOTE}",
        title_in=1.2,
        margin_in=0.9,
        # The bottom row's purchaser labels are rotated, and the era legend sits
        # below them; both are drawn into this margin.
        bottom_in=2.3,
        hspace=0.45,
    )
    for row, panels in enumerate(rows):
        if len(panels) == 1:
            panels[0](fig.add_subplot(grid[row, :]))
        else:
            for col, draw in enumerate(panels):
                draw(fig.add_subplot(grid[row, col]))
    handles = [
        plt.Line2D([], [], color=era.color, marker=era.marker, linewidth=2, label=era.label)
        for era in ERAS
    ]
    fig.legend(handles=handles, loc="lower center", ncol=len(ERAS), frameon=False, fontsize=10)
    save_and_close(fig, output_path, interactive)


# =============================================================================
# MATCH QUALITY VALIDATION (Post-match) - Report
# =============================================================================


def _analyze_era(era: Era, results: dict[str, Any]) -> None:
    """Compute and print one era's statistics, writing them into ``results``.

    Every key is suffixed with the era, which is also how the panels find them. An
    era whose matches have not been built is reported and skipped.
    """
    print_section(f"Match statistics -- {era.label}")
    try:
        matches, hmda, fhlb = load_era_data(era)
    except FileNotFoundError as e:
        print(f"{era.label} data not found: {e}")
        return
    print(f"Loaded {len(matches):,} matches")
    sides = era_sides(era, matches, hmda, fhlb)

    stats = compute_overall_stats(matches, sides)
    results[f"stats_{era.key}"] = stats
    print(f"\n  Total matched pairs: {stats['n_matches']:,}")
    for name, label in (("hmda", "HMDA member"), ("fhlb", "FHLB acquisition")):
        if stats[f"{name}_match_rate"] is not None:
            print(
                f"  {label} match rate: {stats[f'{name}_match_rate']:.1%} ({stats[f'n_{name}_matched']:,} / {stats[f'n_{name}']:,})"
            )

    print_section(f"Match counts by year -- {era.label}")
    yearly = compute_yearly_match_counts(matches)
    results[f"yearly_{era.key}"] = yearly
    print(yearly)

    if "hmda" in sides:
        print_section(f"Match rates by loan amount, HMDA side -- {era.label}")
        results[f"loan_amount_rates_{era.key}"] = compute_match_rates_by_loan_amount(
            sides["hmda"], pl.col("loan_amount")
        )
    if "fhlb" in sides:
        print_section(f"Match rates by loan amount, FHLB side -- {era.label}")
        results[f"loan_amount_rates_fhlb_{era.key}"] = compute_match_rates_by_loan_amount(
            sides["fhlb"], FHLB_AMOUNT_DOLLARS[era.key]
        )

    print_section(f"Purchaser type distribution -- {era.label}")
    purchaser = compute_purchaser_type_distribution(matches)
    results[f"purchaser_distribution_{era.key}"] = purchaser
    if len(purchaser) > 0:
        print(purchaser)
        print(f"\nNote: {PURCHASER_NOTE}")


def _analyze_lenders(results: dict[str, Any]) -> None:
    """Compute and print the PFI-level summary, writing it into ``results``."""
    print_section("Lender (PFI) analysis")
    stats_df = load_lender_statistics()
    if stats_df is None:
        print("Lender statistics file not found.")
        return
    analysis = analyze_lender_statistics(stats_df)
    results["lender_analysis"] = analysis
    if "yearly_summary" in analysis:
        print("Yearly summary of active PFIs:")
        print(analysis["yearly_summary"])
    print(f"\nTotal unique PFIs: {analysis.get('n_active_lenders', 'N/A')}")
    if "top_lenders" in analysis:
        print("\nTop 10 lenders by total matches:")
        print(analysis["top_lenders"].head(10))


def run_validation(
    output_dir: Path | None = None, save_plots: bool = True, show_plots: bool = False
) -> dict[str, Any]:
    """Run the post-match validation for both eras and draw the combined figure.

    Args:
        output_dir: Directory for the figure. Defaults to ``VALIDATION_OUTPUT_DIR``.
        save_plots: Whether to write the figure.
        show_plots: Whether to display the figure interactively.

    Returns:
        Every computed frame and statistic, keyed by statistic and era
        (``yearly_post2018``).
    """
    output_dir = output_dir or VALIDATION_OUTPUT_DIR
    results: dict[str, Any] = {}
    for era in ERAS:
        _analyze_era(era, results)
    _analyze_lenders(results)

    if save_plots or show_plots:
        print_section("Validation figure")
        plot_validation_panel(
            results,
            output_path=output_dir / "hmda_fhlb_validation.png" if save_plots else None,
            interactive=show_plots,
        )

    print_section("Interpretation & key findings")
    print(INTERPRETATION)
    print("\nValidation complete!")
    return results
