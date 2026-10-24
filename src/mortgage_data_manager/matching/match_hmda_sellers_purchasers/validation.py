"""Validation report for the HMDA sellers/purchasers crosswalk.

The crosswalk links loan originations (sellers) to loan purchases (purchasers)
within HMDA, in eight rounds of progressively relaxed criteria.

Two denominators, because the two sides are different populations:

- **Purchase match rate**: share of HMDA purchases (``action_taken=6``) linked to at
  least one origination -- coverage from the purchaser's perspective.
- **Origination match rate**: share of originations with ``purchaser_type >= 5``
  linked to at least one purchase. GSE sales (Fannie=1, Ginnie=2, Freddie=3,
  Farmer Mac=4) are excluded because they never appear as HMDA purchases.

Sections: statistics by round, temporal (seller year, cross-year gaps), geographic,
loan characteristics (amount, type, purpose), and the purchaser-type view.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.matching.match_hmda_sellers_purchasers.config import (
    HMDASellersPurchasersMatchingConfig as Config,
)
from mortgage_data_manager.matching.validation_common import (
    COLORS,
    HAS_MATPLOTLIB,
    RateSeries,
    Side,
    binned,
    get_pyplot,
    matched_count,
    matched_ids,
    panel_gridspec,
    panel_grouped_bars,
    panel_rate_vs_bin,
    print_section,
    rate_by,
    rate_by_sides,
    save_and_close,
)
from mortgage_data_manager.sources.hmda.load import scan_hmda_years

VALIDATION_OUTPUT_DIR = MortgageDataConfig.PROJECT_DIR / "docs" / "matching" / "figures"

MIN_YEAR = Config.MIN_YEAR_POST2018
MAX_YEAR = Config.MAX_YEAR_POST2018

#: Originations sold to one of these purchaser types can appear as HMDA purchases.
ELIGIBLE_PURCHASER = pl.col("purchaser_type") >= 5
GSE_PURCHASER_TYPES = (1, 2, 3, 4)

ROUND_DESCRIPTIONS = {
    1: "Same-year exact",
    2: "Cross-year strict",
    3: "Secondary sales (1-to-many)",
    4: "Without loan purpose",
    5: "Loan amount tolerance",
    6: "Exclude portfolio/NA",
    7: "Minimal + fee requirement",
    8: "Purchaser-type constrained",
}

PURCHASER_TYPE_LABELS = {
    0: "Not applicable/Not sold",
    1: "Fannie Mae",
    2: "Ginnie Mae",
    3: "Freddie Mac",
    4: "Farmer Mac",
    5: "Private securitizer",
    6: "Commercial bank/savings",
    71: "Credit union/mortgage co/finance co",
    72: "Life insurance company",
    8: "Affiliate institution",
    9: "Other type of purchaser",
}
LOAN_TYPE_LABELS = {1: "Conventional", 2: "FHA", 3: "VA", 4: "RHS/FSA"}
LOAN_PURPOSE_LABELS = {
    1: "Home purchase",
    2: "Home improvement",
    31: "Cash-out refinancing",
    32: "Other refinancing",
    4: "Other purpose",
    5: "Not applicable",
}

# Categorical sequence (alternates dark/light, warm/cool for accessibility)
CATEGORICAL_COLORS = [
    COLORS["midnight"],
    COLORS["gold"],
    COLORS["hunter"],
    COLORS["terracotta"],
    COLORS["purple"],
    COLORS["grey"],
    COLORS["mulberry"],
]

# The two sides of this workflow: the seller's origination and the purchaser's
# purchase record. Every rate panel reports both.
RATE_SERIES = (
    RateSeries(
        "origination_match_rate", "origination_total", COLORS["hunter"], "Originations", "o"
    ),
    RateSeries("purchase_match_rate", "purchase_total", COLORS["gold"], "Purchases", "s"),
)

# Vertical inches allotted to one grid row; the canvas is sized from the row count.
PANEL_HEIGHT_IN = 4.5


# =============================================================================
# Data
# =============================================================================


def load_crosswalk(crosswalk_dir: Path | None = None) -> pl.LazyFrame:
    """The seller-purchaser crosswalk.

    Args:
        crosswalk_dir: Directory holding the crosswalk; defaults to the configured output.

    Returns:
        The crosswalk, lazily.
    """
    return pl.scan_parquet((crosswalk_dir or Config.get_crosswalk_dir("post2018")) / "**/*.parquet")


def load_hmda_originations(hmda_dir: Path | None = None) -> pl.LazyFrame:
    """HMDA originations (``action_taken=1``).

    Args:
        hmda_dir: Root of the HMDA silver layer; defaults to the configured layout.

    Returns:
        The originations, lazily.
    """
    return scan_hmda_years("post2018", root=hmda_dir or Config.HMDA_SILVER_DIR_POST2018).filter(
        pl.col("action_taken") == 1
    )


def load_hmda_purchases(hmda_dir: Path | None = None) -> pl.LazyFrame:
    """HMDA purchases (``action_taken=6``).

    Args:
        hmda_dir: Root of the HMDA silver layer; defaults to the configured layout.

    Returns:
        The purchases, lazily.
    """
    return scan_hmda_years("post2018", root=hmda_dir or Config.HMDA_SILVER_DIR_POST2018).filter(
        pl.col("action_taken") == 6
    )


def sides(
    crosswalk: pl.LazyFrame, originations: pl.LazyFrame, purchases: pl.LazyFrame
) -> tuple[Side, Side]:
    """The two measured sides: eligible originations and all purchases.

    Args:
        crosswalk: The seller-purchaser crosswalk.
        originations: HMDA originations; only the eligible ones are measured.
        purchases: HMDA purchases.

    Returns:
        ``(origination_side, purchase_side)``.
    """
    return (
        Side(
            originations.filter(ELIGIBLE_PURCHASER),
            matched_ids(crosswalk, "HMDAIndex_s", as_cols="HMDAIndex"),
            "HMDAIndex",
            "origination",
        ),
        Side(
            purchases,
            matched_ids(crosswalk, "HMDAIndex_p", as_cols="HMDAIndex"),
            "HMDAIndex",
            "purchase",
        ),
    )


def _with_purchaser_year(crosswalk: pl.LazyFrame, purchases: pl.LazyFrame) -> pl.LazyFrame:
    """The crosswalk with the purchase record's activity year joined on."""
    years = purchases.select("HMDAIndex", pl.col("activity_year").alias("activity_year_p"))
    return crosswalk.join(years, left_on="HMDAIndex_p", right_on="HMDAIndex", how="left")


# =============================================================================
# Statistics
# =============================================================================


def compute_match_statistics_by_round(crosswalk: pl.LazyFrame) -> pl.DataFrame:
    """Pair count, share, and cumulative coverage per match round.

    Args:
        crosswalk: The seller-purchaser crosswalk.

    Returns:
        One row per match round, with its description.
    """
    return (
        crosswalk.group_by("match_round")
        .agg(pl.len().alias("match_count"))
        .sort("match_round")
        .with_columns(
            (pl.col("match_count") / pl.col("match_count").sum()).alias("pct_of_total"),
            pl.col("match_count").cum_sum().alias("cumulative_count"),
        )
        .with_columns(
            (pl.col("cumulative_count") / pl.col("match_count").sum()).alias("cumulative_pct"),
            pl.col("match_round")
            .replace_strict(ROUND_DESCRIPTIONS, default="Unknown")
            .alias("description"),
        )
        .collect()
    )


def compute_matches_by_seller_year(crosswalk: pl.LazyFrame) -> pl.DataFrame:
    """Match count and mean round by the seller's activity year.

    Args:
        crosswalk: The seller-purchaser crosswalk.

    Returns:
        One row per seller year.
    """
    return (
        crosswalk.group_by("activity_year_s")
        .agg(pl.len().alias("match_count"), pl.col("match_round").mean().alias("avg_round"))
        .sort("activity_year_s")
        .collect()
    )


def compute_cross_year_patterns(crosswalk: pl.LazyFrame, purchases: pl.LazyFrame) -> pl.DataFrame:
    """Distribution of purchase-year minus origination-year gaps.

    Args:
        crosswalk: The seller-purchaser crosswalk.
        purchases: HMDA purchases (for the purchase record's year).

    Returns:
        One row per year gap with its match count and share.
    """
    return (
        _with_purchaser_year(crosswalk, purchases)
        .group_by((pl.col("activity_year_p") - pl.col("activity_year_s")).alias("year_diff"))
        .agg(pl.len().alias("match_count"))
        .with_columns((pl.col("match_count") / pl.col("match_count").sum()).alias("pct"))
        .sort("year_diff")
        .collect()
    )


def compute_cross_year_by_round(crosswalk: pl.LazyFrame, purchases: pl.LazyFrame) -> pl.DataFrame:
    """Same-year versus cross-year match counts, per round.

    Args:
        crosswalk: The seller-purchaser crosswalk.
        purchases: HMDA purchases (for the purchase record's year).

    Returns:
        One row per (round, same_year).
    """
    return (
        _with_purchaser_year(crosswalk, purchases)
        .group_by(
            "match_round",
            (pl.col("activity_year_p") == pl.col("activity_year_s")).alias("same_year"),
        )
        .agg(pl.len().alias("match_count"))
        .sort("match_round", "same_year")
        .collect()
    )


def compute_match_rates_by_state(both: tuple[Side, Side]) -> pl.DataFrame:
    """Both sides' match rates by state (from the census tract), largest states first.

    Args:
        both: The two sides, from `sides`.

    Returns:
        One row per state FIPS.
    """
    return rate_by_sides(
        both,
        "state_fips",
        prepare=lambda lf: lf.filter(pl.col("census_tract").is_not_null()).with_columns(
            pl.col("census_tract").str.slice(0, 2).alias("state_fips")
        ),
        sort_by="purchase_total",
        descending=True,
    )


def compute_match_rates_by_category(
    both: tuple[Side, Side], category: str, labels: dict[int, str]
) -> pl.DataFrame:
    """Both sides' match rates per category value, largest categories first.

    Args:
        both: The two sides, from `sides`.
        category: The categorical column (``loan_type``, ``loan_purpose``).
        labels: Code to display label, added as ``{category}_label``.

    Returns:
        One row per category value.
    """
    return rate_by_sides(both, category, sort_by="purchase_total", descending=True).with_columns(
        pl.col(category).replace_strict(labels, default="Unknown").alias(f"{category}_label")
    )


def compute_match_rates_by_loan_amount_bin(
    both: tuple[Side, Side], bin_size: int = 50_000, max_amount: int = 1_000_000
) -> pl.DataFrame:
    """Both sides' match rates by loan-amount bin.

    Args:
        both: The two sides, from `sides`.
        bin_size: Bin width in dollars.
        max_amount: Largest amount included.

    Returns:
        One row per bin.
    """
    return rate_by_sides(
        both,
        "loan_amount_bin",
        prepare=lambda lf: lf.filter(
            pl.col("loan_amount").is_not_null() & (pl.col("loan_amount") <= max_amount)
        ).with_columns(binned("loan_amount", bin_size)),
    )


def compute_match_rates_by_purchaser_type(origination: Side) -> pl.DataFrame:
    """Origination-side match rate per purchaser type, largest types first.

    Args:
        origination: The eligible-origination side, from `sides`.

    Returns:
        One row per purchaser type, with its display label.
    """
    return (
        rate_by(
            origination.universe,
            origination.matched,
            origination.on,
            "purchaser_type",
            prefix=origination.prefix,
        )
        .with_columns(
            pl.col("purchaser_type")
            .replace_strict(PURCHASER_TYPE_LABELS, default="Unknown")
            .alias("purchaser_type_label")
        )
        .collect()
    )


def compute_matches_by_purchaser_type(
    crosswalk: pl.LazyFrame, originations: pl.LazyFrame
) -> pl.DataFrame:
    """Match count and mean round by the seller's purchaser type.

    Args:
        crosswalk: The seller-purchaser crosswalk.
        originations: HMDA originations (for the seller's purchaser type).

    Returns:
        One row per purchaser type, largest first.
    """
    seller_types = originations.select(
        "HMDAIndex", pl.col("purchaser_type").alias("purchaser_type_seller")
    )
    return (
        crosswalk.join(seller_types, left_on="HMDAIndex_s", right_on="HMDAIndex", how="left")
        .group_by("purchaser_type_seller")
        .agg(pl.len().alias("match_count"), pl.col("match_round").mean().alias("avg_round"))
        .with_columns(
            (pl.col("match_count") / pl.col("match_count").sum()).alias("pct"),
            pl.col("purchaser_type_seller")
            .replace_strict(PURCHASER_TYPE_LABELS, default="Unknown")
            .alias("purchaser_type_label"),
        )
        .sort("match_count", descending=True)
        .collect()
    )


# =============================================================================
# Figure
# =============================================================================


def _millions(plt: Any, ax: Any) -> None:
    """Format an axis in millions; every count here runs to seven or eight figures."""
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x / 1e6:.1f}M"))


def _panel_round_counts(ax: Any, plt: Any, round_stats: pl.DataFrame) -> None:
    """Matches contributed by each round."""
    rounds = round_stats["match_round"].to_list()
    colors = [CATEGORICAL_COLORS[i % len(CATEGORICAL_COLORS)] for i in range(len(rounds))]
    ax.bar(
        rounds,
        round_stats["match_count"].to_list(),
        color=colors,
        edgecolor=COLORS["ink"],
        alpha=0.9,
    )
    ax.set_xlabel("Match Round")
    ax.set_ylabel("Match Count")
    ax.set_title("Match counts by round")
    ax.set_xticks(rounds)
    _millions(plt, ax)
    ax.grid(True, alpha=0.3, axis="y")


def _panel_round_cumulative(ax: Any, plt: Any, round_stats: pl.DataFrame) -> None:
    """Coverage reached after each round, cumulatively."""
    rounds = round_stats["match_round"].to_list()
    cumulative = round_stats["cumulative_count"].to_list()
    ax.plot(rounds, cumulative, "o-", color=COLORS["hunter"], linewidth=2, markersize=7)
    ax.fill_between(rounds, cumulative, alpha=0.3, color=COLORS["hunter"])
    ax.set_xlabel("Match Round")
    ax.set_ylabel("Cumulative Match Count")
    ax.set_title("Cumulative match coverage")
    ax.set_xticks(rounds)
    _millions(plt, ax)
    ax.grid(True, alpha=0.3)


def _panel_matches_by_year(ax: Any, yearly_stats: pl.DataFrame) -> None:
    """Matched loans by the seller's origination year."""
    years = yearly_stats["activity_year_s"].to_list()
    counts = yearly_stats["match_count"].to_list()
    ax.bar(
        years, [c / 1e6 for c in counts], color=COLORS["hunter"], edgecolor=COLORS["ink"], alpha=0.9
    )
    for year, count in zip(years, counts):
        ax.text(year, count / 1e6, f"{count / 1e6:.2f}M", ha="center", va="bottom", fontsize=8)
    ax.set_xlabel("Seller (Origination) Year")
    ax.set_ylabel("Matches (Millions)")
    ax.set_title("Matched loans by origination year")
    ax.set_xticks(years)
    ax.set_ylim(0, max(counts) / 1e6 * 1.12)
    ax.grid(True, alpha=0.3, axis="y")


def _panel_cross_year(ax: Any, cross_year_stats: pl.DataFrame) -> None:
    """How far apart the two sides' activity years are.

    A purchase reported a year or more after the origination is normal -- loans sold
    late in a year land in the next year's purchaser file -- so the spread here is a
    property of HMDA reporting, not of the matcher.
    """
    from matplotlib.patches import Patch

    filtered = cross_year_stats.filter(pl.col("year_diff").is_between(0, 6))
    year_diffs = filtered["year_diff"].to_list()
    pcts = [p * 100 for p in filtered["pct"].to_list()]
    colors = [COLORS["hunter"] if diff == 0 else COLORS["gold"] for diff in year_diffs]
    ax.bar(year_diffs, pcts, color=colors, edgecolor=COLORS["ink"], alpha=0.9)
    for diff, pct in zip(year_diffs, pcts):
        ax.text(diff, pct, f"{pct:.1f}%", ha="center", va="bottom", fontsize=8)
    ax.set_xlabel("Year difference (purchaser year - seller year)")
    ax.set_ylabel("Percentage of Matches (%)")
    ax.set_title("Same-year vs cross-year matching")
    ax.set_xticks(year_diffs)
    ax.set_ylim(0, max(pcts) * 1.12 if pcts else 1)
    ax.legend(
        handles=[
            Patch(facecolor=COLORS["hunter"], edgecolor=COLORS["ink"], label="Same year"),
            Patch(facecolor=COLORS["gold"], edgecolor=COLORS["ink"], label="Cross year"),
        ],
        loc="upper right",
        fontsize=8,
    )
    ax.grid(True, alpha=0.3, axis="y")


def _purchaser_labels(purchaser_type_rates: pl.DataFrame) -> tuple[pl.DataFrame, list[str]]:
    """The purchaser-type frame with nulls dropped, and its display labels."""
    filtered = purchaser_type_rates.filter(pl.col("purchaser_type").is_not_null())
    return filtered, [
        PURCHASER_TYPE_LABELS.get(pt, f"Type {pt}") for pt in filtered["purchaser_type"].to_list()
    ]


def _panel_purchaser_volume(ax: Any, purchaser_type_rates: pl.DataFrame) -> None:
    """Eligible origination volume behind each purchaser type."""
    filtered, labels = _purchaser_labels(purchaser_type_rates)
    if len(filtered) == 0:
        ax.set_axis_off()
        return
    y_pos = np.arange(len(labels))
    ax.barh(
        y_pos,
        [t / 1e6 for t in filtered["origination_total"].to_list()],
        color=COLORS["gold"],
        edgecolor=COLORS["ink"],
        alpha=0.9,
    )
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel("Total Eligible Originations (Millions)")
    ax.set_title("Eligible originations by purchaser type\n(purchaser_type >= 5 only)")
    ax.invert_yaxis()
    ax.grid(True, alpha=0.3, axis="x")


def _panel_purchaser_rates(
    ax: Any, purchaser_type_rates: pl.DataFrame, overall_rate: float
) -> None:
    """Origination-side match rate per purchaser type, against the overall origination rate."""
    filtered, labels = _purchaser_labels(purchaser_type_rates)
    if len(filtered) == 0:
        ax.set_axis_off()
        return
    rates = filtered["origination_match_rate"].to_list()
    y_pos = np.arange(len(labels))
    ax.barh(
        y_pos,
        [(r or 0) * 100 for r in rates],
        color=COLORS["gold"],
        edgecolor=COLORS["ink"],
        alpha=0.9,
    )
    ax.axvline(
        overall_rate * 100,
        color=COLORS["hunter"],
        linestyle="--",
        linewidth=2,
        label=f"Overall origination match rate ({overall_rate * 100:.1f}%)",
    )
    for pos, rate in enumerate(rates):
        ax.text(
            (rate or 0) * 100 + 2,
            pos,
            f"{rate * 100:.1f}%" if rate else "0%",
            ha="left",
            va="center",
            fontsize=8,
        )
    # The volume panel beside this one already labels these categories, in the same
    # order; repeating them here only crowds the gap between the two panels.
    ax.set_yticks(y_pos)
    ax.set_yticklabels([])
    ax.set_xlabel("Match Rate (%)")
    ax.set_title("Match rates by purchaser type")
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(True, alpha=0.3, axis="x")


def plot_validation_panel(
    results: dict[str, Any], output_path: Path | None = None, interactive: bool = False
) -> None:
    """Render the validation figure.

    Rows: match counts by round | cumulative coverage; matches by year | cross-year
    patterns; purchaser-type volume | purchaser-type match rates; match rates by loan
    amount (full width); by loan type | by loan purpose.

    Args:
        results: The accumulated ``run_validation`` results.
        output_path: Where to save the figure (PNG). Skipped if ``None``.
        interactive: Whether to display the figure interactively.
    """
    plt = get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    rows = 5
    fig = plt.figure(figsize=(16, PANEL_HEIGHT_IN * rows + 1.5), facecolor=COLORS["canvas"])
    grid = panel_gridspec(
        fig,
        rows,
        2,
        title=f"HMDA sellers/purchasers match validation ({MIN_YEAR}-{MAX_YEAR})",
        title_fontsize=14,
        # The purchaser-type panel's category names are drawn into the left margin.
        margin_in=2.5,
    )

    _panel_round_counts(fig.add_subplot(grid[0, 0]), plt, results["round_stats"])
    _panel_round_cumulative(fig.add_subplot(grid[0, 1]), plt, results["round_stats"])
    _panel_matches_by_year(fig.add_subplot(grid[1, 0]), results["yearly_stats"])
    _panel_cross_year(fig.add_subplot(grid[1, 1]), results["cross_year_stats"])
    _panel_purchaser_volume(fig.add_subplot(grid[2, 0]), results["purchaser_type_rates"])
    _panel_purchaser_rates(
        fig.add_subplot(grid[2, 1]),
        results["purchaser_type_rates"],
        results["origination_match_rate"],
    )

    ax = fig.add_subplot(grid[3, :])
    panel_rate_vs_bin(
        ax,
        results["loan_amount_rates"],
        "loan_amount_bin",
        RATE_SERIES,
        xlabel="Loan Amount ($K)",
        x_scale=0.001,
        legend_loc="lower right",
    )
    ax.set_title("Match rates by loan amount")

    for col, (key, category, labels, noun, rotation) in enumerate(
        (
            ("loan_type_rates", "loan_type", LOAN_TYPE_LABELS, "Loan Type", 0),
            ("loan_purpose_rates", "loan_purpose", LOAN_PURPOSE_LABELS, "Loan Purpose", 25),
        )
    ):
        ax = fig.add_subplot(grid[4, col])
        panel_grouped_bars(
            ax,
            results[key],
            category,
            RATE_SERIES,
            labels,
            xlabel=noun,
            label_fontsize=8,
            tick_rotation=rotation,
        )
        ax.set_title(f"Match rates by {noun.lower()}")

    save_and_close(fig, output_path, interactive, facecolor=COLORS["canvas"])


# =============================================================================
# Report
# =============================================================================


def _describe_rates(label: str, rates: pl.Series) -> None:
    valid = rates.drop_nulls().drop_nans().to_numpy()
    if len(valid):
        print(
            f"{label} range: {valid.min():.1%} - {valid.max():.1%}; mean {valid.mean():.1%}, std {valid.std():.3f}"
        )


def run_validation(
    output_dir: Path | None = None, save_plots: bool = True, show_plots: bool = False
) -> dict[str, Any]:
    """Run every validation analysis, print the report, and draw the figure.

    Args:
        output_dir: Directory for the figure. Defaults to ``VALIDATION_OUTPUT_DIR``.
        save_plots: Whether to write the figure.
        show_plots: Whether to display the figure interactively.

    Returns:
        Every computed frame and statistic, keyed by name.
    """
    output_dir = output_dir or VALIDATION_OUTPUT_DIR
    if save_plots:
        output_dir.mkdir(parents=True, exist_ok=True)

    print("Loading data...")
    crosswalk = load_crosswalk()
    originations = load_hmda_originations()
    purchases = load_hmda_purchases()
    origination, purchase = both = sides(crosswalk, originations, purchases)

    n_matches, n_originations, n_eligible, n_purchases = (
        lf.select(pl.len()).collect().item()
        for lf in (crosswalk, originations, originations.filter(ELIGIBLE_PURCHASER), purchases)
    )
    n_matched_originations, n_matched_purchases = (
        matched_count(origination),
        matched_count(purchase),
    )
    origination_match_rate = n_matched_originations / n_eligible if n_eligible else 0
    purchase_match_rate = n_matched_purchases / n_purchases if n_purchases else 0
    print(f"Crosswalk: {n_matches:,} matched pairs")
    print(f"HMDA originations: {n_originations:,} total, {n_eligible:,} eligible (ptype>=5)")
    print(f"HMDA purchases: {n_purchases:,}")
    print(f"Overall origination match rate (ptype>=5): {origination_match_rate:.1%}")
    print(f"Overall purchase match rate: {purchase_match_rate:.1%}")

    results: dict[str, Any] = {
        "n_matches": n_matches,
        "n_originations": n_originations,
        "n_eligible_originations": n_eligible,
        "n_purchases": n_purchases,
        "origination_match_rate": origination_match_rate,
        "purchase_match_rate": purchase_match_rate,
    }

    print_section("1. Match Statistics by Round")
    round_stats = results["round_stats"] = compute_match_statistics_by_round(crosswalk)
    print(round_stats)
    if len(round_stats):
        by_round = dict(
            zip(round_stats["match_round"].to_list(), round_stats["pct_of_total"].to_list())
        )
        print(f"\nRound 1 captures: {by_round.get(1, 0):.1%} of all matches")
        print(
            f"Rounds 1-3 capture: {sum(v for k, v in by_round.items() if k <= 3):.1%} of all matches"
        )

    print_section("2. Temporal Analysis")
    yearly_stats = results["yearly_stats"] = compute_matches_by_seller_year(crosswalk)
    print("Matches by origination year:")
    print(yearly_stats)
    cross_year_stats = results["cross_year_stats"] = compute_cross_year_patterns(
        crosswalk, purchases
    )
    print("\nCross-year matching patterns:")
    print(cross_year_stats)
    same_year = cross_year_stats.filter(pl.col("year_diff") == 0)["pct"].to_list()
    if same_year:
        print(f"\nSame-year matches: {same_year[0]:.1%}; cross-year: {1 - same_year[0]:.1%}")
    results["cross_year_by_round"] = compute_cross_year_by_round(crosswalk, purchases)
    print("\nCross-year matching by round:")
    print(results["cross_year_by_round"].head(20))

    print_section("3. Geographic Analysis")
    state_rates = results["state_match_rates"] = compute_match_rates_by_state(both)
    print("Match rates by state (top 20 by purchase volume):")
    print(state_rates.head(20))
    _describe_rates("\nOrigination match rate (ptype>=5)", state_rates["origination_match_rate"])
    _describe_rates("Purchase match rate", state_rates["purchase_match_rate"])

    print_section("4. Loan Characteristics Analysis")
    results["loan_type_rates"] = compute_match_rates_by_category(
        both, "loan_type", LOAN_TYPE_LABELS
    )
    print("Match rates by loan type:")
    print(results["loan_type_rates"])
    results["loan_purpose_rates"] = compute_match_rates_by_category(
        both, "loan_purpose", LOAN_PURPOSE_LABELS
    )
    print("\nMatch rates by loan purpose:")
    print(results["loan_purpose_rates"])
    results["loan_amount_rates"] = compute_match_rates_by_loan_amount_bin(both)
    print("\nMatch rates by loan amount (bins):")
    print(results["loan_amount_rates"].head(20))

    print_section("5. Purchaser Type Analysis (Key Insight)")
    results["purchaser_type_rates"] = compute_match_rates_by_purchaser_type(origination)
    print("Match rates by purchaser type (eligible originations only, ptype>=5):")
    print(results["purchaser_type_rates"])
    by_type = results["purchaser_type_matches"] = compute_matches_by_purchaser_type(
        crosswalk, originations
    )
    print("\nMatches by purchaser type:")
    print(by_type)
    gse = by_type.filter(pl.col("purchaser_type_seller").is_in(GSE_PURCHASER_TYPES))[
        "match_count"
    ].sum()
    print(
        f"\nGSE-purchaser matches: {gse:,} ({gse / n_matches:.1%}); non-GSE: {n_matches - gse:,} ({(n_matches - gse) / n_matches:.1%})"
    )

    print_section("6. Key Findings Summary")
    print(f"  Total matched pairs: {n_matches:,}")
    print(
        f"  Purchase match rate: {purchase_match_rate:.1%} of purchases find a matching origination"
    )
    print(
        f"  Origination match rate (ptype>=5): {origination_match_rate:.1%} of eligible originations find a match"
    )
    print(
        "  Denominators: purchases are HMDA action_taken=6; eligible originations have purchaser_type >= 5"
    )
    print(
        "  (GSE sales -- Fannie=1, Ginnie=2, Freddie=3, Farmer Mac=4 -- never appear as HMDA purchases)"
    )

    if (show_plots or save_plots) and HAS_MATPLOTLIB:
        print_section("7. Generating Visualizations")
        plot_validation_panel(
            results,
            output_path=output_dir / "hmda_sellers_purchasers_validation.png"
            if save_plots
            else None,
            interactive=show_plots,
        )

    return results
