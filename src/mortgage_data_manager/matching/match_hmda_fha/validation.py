"""Validation reports for the HMDA-FHA crosswalks, both eras.

Post-2018 (`run_validation`): match rates over time (HMDA activity year, FHA
endorsement year and month), by geography (state, county) with size-correlation
checks, and by loan characteristics (amount, rate, purpose, rate type, channel).

Pre-2018 (`run_validation_pre2018`): the same geography and loan-amount views, plus
the era's own evidence -- per-round contribution, the lender-bridge placebo
controls, held-out county and lender agreement, selection bias, and agreement with
the frozen reference artifact. Both entry points are registered on the CLI.

Post-2018 HMDA covers 2018-2024; the FHA intermediate also carries late-2017 and
early-2025 endorsements that no HMDA year could match, so every FHA-side
denominator is restricted to the overlapping window.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import numpy as np
import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.reference.states import FIPS_TO_STATE
from mortgage_data_manager.core.scalars import scalar_float
from mortgage_data_manager.matching.match_hmda_fha.config import HMDAFHAMatchingConfig as Config
from mortgage_data_manager.matching.match_hmda_fha.lender_bridge import load_bridge
from mortgage_data_manager.matching.match_hmda_fha.matching_pre2018 import (
    endorsement_window,
    hmda_fha_origination_counts,
    run_rounds_pre2018,
)
from mortgage_data_manager.matching.match_hmda_fha.rounds import ROUNDS_PRE2018
from mortgage_data_manager.matching.validation_common import (
    COLORS,
    HAS_MATPLOTLIB,
    NON_STATE_CODES,
    RateSeries,
    Side,
    StatePanel,
    analyze_state_size_correlation,
    binned,
    get_pyplot,
    matched_ids,
    overall_rate,
    panel_gridspec,
    panel_grouped_bars,
    panel_rate_vs_bin,
    plot_state_choropleth,
    print_section,
    rate_by,
    rate_by_sides,
    save_and_close,
    set_rate_ylim,
)
from mortgage_data_manager.sources.hmda.load import scan_originations

VALIDATION_OUTPUT_DIR = MortgageDataConfig.PROJECT_DIR / "docs" / "matching" / "figures"

# County FIPS state prefixes for territories. Their county codes do not line up
# across the two sources, so their rates are an artefact, not a geographic finding.
COUNTY_TERRITORY_PREFIXES = ["72", "78", "66", "60", "69"]

FHA_COLOR = COLORS["hunter"]
HMDA_COLOR = COLORS["gold"]

# The two sides of every rate panel in this workflow.
RATE_SERIES = (
    RateSeries("hmda_match_rate", "hmda_total", HMDA_COLOR, "HMDA", "s"),
    RateSeries("fha_match_rate", "fha_total", FHA_COLOR, "FHA", "o"),
)
HMDA_ONLY = (RATE_SERIES[0],)

# Vertical inches allotted to one grid row; each canvas is sized from its row count.
PANEL_HEIGHT_IN = 4.5
# Six columns so a row can hold two half-width panels or three third-width ones.
GRID_COLUMNS = 6
# Column spacing is a fraction of one column's width; the density panels' right-hand
# axis labels are drawn into this gap and would otherwise land on the next panel.
GRID_WSPACE = 0.75
# How many states to label at each extreme of the state-size scatter.
STATE_LABEL_COUNT = 3

#: The pre-2018 rounds' numbers, in run order.
PRE2018_ROUND_NUMBERS = tuple(range(1, len(ROUNDS_PRE2018) + 1))
#: The endorsement months each pre-2018 activity year is credited with on the FHA side:
#: Feb Y - Jan Y+1, the base rounds' window. Consecutive years' windows abut, so every
#: endorsement is credited to one year and the per-year denominators sum to the era's.
PRE2018_YEAR_WINDOW = (1, 12)

ARM_LABELS = {0: "Fixed Rate", 1: "ARM"}
CHANNEL_LABELS = {1: "Direct to Lender", 2: "Broker/Correspondent"}
#: The purpose vocabulary both sides can be folded onto for a paired comparison.
FHA_PURPOSE_FOLD = {"Purchase": "Purchase", "Refi_FHA": "Refinance", "Refi_Conv_Curr": "Refinance"}
HMDA_PURPOSE_FOLD = {1: "Purchase", 31: "Refinance", 32: "Refinance"}


# =============================================================================
# Shared geography helpers (both eras build the same two-sided frames)
# =============================================================================


def _real_states(states: pl.DataFrame, side: str) -> pl.DataFrame:
    """One side's mappable jurisdictions with a usable denominator.

    The size-vs-rate correlations are about real states. HMDA's ``"NA"`` sentinel and
    the territories are not, and ``"NA"`` in particular is a structural zero (no
    matcher can place a loan with no state) that would otherwise swing the correlation.
    """
    return states.filter((pl.col(f"{side}_total") >= 100) & ~pl.col("state").is_in(NON_STATE_CODES))


def _county_side(counties: pl.DataFrame, side: str, min_loans: int) -> pl.DataFrame:
    """One side's counties with enough loans to read, territories dropped."""
    return counties.filter(
        (pl.col(f"{side}_total") >= min_loans)
        & pl.col(f"{side}_match_rate").is_not_null()
        & ~pl.col("county").str.slice(0, 2).is_in(COUNTY_TERRITORY_PREFIXES)
    )


def compute_county_size_correlation(counties: pl.DataFrame, min_loans: int = 50) -> dict[str, Any]:
    """Spearman correlation of county loan volume against county match rate, per side.

    Args:
        counties: Two-sided per-county rates, from `county_rates`.
        min_loans: Minimum loan count for a county to enter the correlation.

    Returns:
        Per side, the filtered frame and its Spearman statistics. Empty if either
        side has too few counties above ``min_loans`` to correlate.
    """
    from scipy.stats import spearmanr

    filtered = {side: _county_side(counties, side, min_loans) for side in ("fha", "hmda")}
    if any(len(df) < 3 for df in filtered.values()):
        return {}
    out: dict[str, Any] = {}
    for side, df in filtered.items():
        r, p = spearmanr(df[f"{side}_total"].to_numpy(), df[f"{side}_match_rate"].to_numpy())
        out |= {
            f"{side}_counties": df,
            f"{side}_spearman_r": float(r),
            f"{side}_spearman_p": float(p),
            f"{side}_n_counties": len(df),
        }
    return out


def state_correlations(states: pl.DataFrame) -> dict[str, dict[str, Any]]:
    """Both sides' state-size correlations, on the frames the figure plots."""
    return {
        side: analyze_state_size_correlation(
            _real_states(states, side), f"{side}_total", f"{side}_match_rate"
        )
        for side in ("fha", "hmda")
    }


# =============================================================================
# Post-2018: data and statistics
# =============================================================================


def load_data(
    crosswalk_path: Path | None = None, fha_path: Path | None = None, hmda_path: Path | None = None
) -> tuple[pl.LazyFrame, pl.LazyFrame, pl.LazyFrame]:
    """Load the post-2018 crosswalk and its two prepared intermediates.

    Args:
        crosswalk_path: Crosswalk parquet; defaults to the production location.
        fha_path: Prepared FHA loans; defaults to the production location.
        hmda_path: Prepared HMDA loans; defaults to the production location.

    Returns:
        ``(crosswalk, fha, hmda)`` as LazyFrames.
    """
    return (
        pl.scan_parquet(crosswalk_path or Config.get_crosswalk_path("post2018")),
        pl.scan_parquet(
            fha_path or Config.HMDA_FHA_INTERMEDIATE_DIR / "fha_match_data_post2018.parquet"
        ),
        pl.scan_parquet(
            hmda_path or Config.HMDA_FHA_INTERMEDIATE_DIR / "hmda_match_data_post2018.parquet"
        ),
    )


def sides(crosswalk: pl.LazyFrame, fha: pl.LazyFrame, hmda: pl.LazyFrame) -> tuple[Side, Side]:
    """The two measured sides; the FHA side is restricted to the HMDA years.

    The prepared HMDA pool carries one row per (loan, ZIP): the matcher attaches every
    ZIP a loan's tract touches, and ``ZIP`` is the only column that varies within a
    loan. Dropping it collapses the pool to one row per loan, so each HMDA loan counts
    once in every HMDA-side rate.

    Args:
        crosswalk: The FHA<->HMDA crosswalk.
        fha: The FHA side's loan universe (endorsements, spanning beyond the window).
        hmda: The HMDA side's prepared, ZIP-fanned pool.

    Returns:
        ``(fha_side, hmda_side)``.
    """
    in_window = fha.filter(pl.col("Year").is_between(Config.MIN_YEAR, Config.MAX_YEAR))
    hmda_loans = hmda.drop("ZIP", strict=False).unique()
    return (
        Side(in_window, matched_ids(crosswalk, "FHA_Index"), "FHA_Index", "fha"),
        Side(hmda_loans, matched_ids(crosswalk, "HMDAIndex"), "HMDAIndex", "hmda"),
    )


def yearly_rates(both: tuple[Side, Side]) -> pl.DataFrame:
    """Match rates by year: FHA endorsement year against HMDA activity year.

    Args:
        both: The two sides, from `sides`.

    Returns:
        One row per year with each side's counts and rate.
    """
    fha, hmda = both
    return rate_by_sides(
        [
            fha.prepared(lambda lf: lf.rename({"Year": "year"})),
            hmda.prepared(lambda lf: lf.rename({"activity_year": "year"})),
        ],
        "year",
    )


def fha_monthly_match_rates(fha: Side) -> pl.DataFrame:
    """FHA-side match rates by endorsement year and month.

    Args:
        fha: The FHA side, from `sides`.

    Returns:
        One row per year-month, chronological.
    """
    return (
        rate_by(fha.universe, fha.matched, fha.on, ["Year", "Month"], prefix="fha")
        .sort("Year", "Month")
        .collect()
    )


def state_rates(both: tuple[Side, Side]) -> pl.DataFrame:
    """Both sides' match rates by state (postal abbreviation), largest FHA states first.

    Args:
        both: The two sides, from `sides`.

    Returns:
        One row per state.
    """
    fha, hmda = both
    return rate_by_sides(
        [
            fha.prepared(lambda lf: lf.rename({"Property State": "state"})),
            hmda.prepared(lambda lf: lf.rename({"state_code": "state"})),
        ],
        "state",
        sort_by="fha_total",
        descending=True,
    )


def county_rates(both: tuple[Side, Side]) -> pl.DataFrame:
    """Both sides' match rates by 5-digit county FIPS.

    Args:
        both: The two sides, from `sides`.

    Returns:
        One row per county.
    """
    fha, hmda = both
    return rate_by_sides(
        [
            fha.prepared(
                lambda lf: lf.filter(pl.col("FIPS").is_not_null()).rename({"FIPS": "county"})
            ),
            hmda.prepared(
                lambda lf: lf.filter(pl.col("county_code").is_not_null()).rename(
                    {"county_code": "county"}
                )
            ),
        ],
        "county",
    )


def match_round_breakdown(crosswalk: pl.LazyFrame) -> pl.DataFrame:
    """Analyze the distribution of match rounds.

    Args:
        crosswalk: The match crosswalk.

    Returns:
        One row per match round, with its pair count and share.
    """
    result = (
        crosswalk.group_by("match_round")
        .agg(pl.len().alias("count"))
        .with_columns((pl.col("count") / pl.col("count").sum()).alias("pct"))
        .sort("match_round")
        .collect()
    )
    return result


def match_round_by_year(crosswalk: pl.LazyFrame, fha: pl.LazyFrame) -> pl.DataFrame:
    """Analyze match round distribution by FHA year.

    Args:
        crosswalk: The match crosswalk.
        fha: The FHA side's loan universe.

    Returns:
        One row per (FHA year, match round), with its pair count.
    """
    enriched = crosswalk.join(fha.select("FHA_Index", "Year"), on="FHA_Index", how="left")

    result = (
        enriched.group_by("Year", "match_round")
        .agg(pl.len().alias("count"))
        .sort("Year", "match_round")
        .collect()
    )

    pivoted = result.pivot(
        on="match_round",
        index="Year",
        values="count",
    ).sort("Year")

    return pivoted


def _hmda_amount_bin(col: str, bin_size: int) -> pl.Expr:
    """HMDA reports amounts in $10k steps centred on $5k, so bins are centred there too."""
    return binned(col, bin_size, mode="round", offset=5000, name="loan_amount_bin")


def loan_amount_rates(
    both: tuple[Side, Side], bin_size: int = 10_000, max_amount: int = 750_000
) -> pl.DataFrame:
    """Match rates by loan-amount bin, from both sides.

    Args:
        both: The two sides, from `sides`.
        bin_size: Bin width in dollars.
        max_amount: Upper edge of the last bin.

    Returns:
        One row per bin with each side's counts and rate.
    """
    fha, hmda = both

    def prepare(col: str) -> Any:
        return lambda lf: lf.with_columns(_hmda_amount_bin(col, bin_size)).filter(
            pl.col("loan_amount_bin").is_between(5000, max_amount)
        )

    return rate_by_sides(
        [fha.prepared(prepare("Mortgage Amount")), hmda.prepared(prepare("loan_amount"))],
        "loan_amount_bin",
    )


def interest_rate_rates(
    both: tuple[Side, Side], bin_size: float = 0.125, min_rate: float = 2.0, max_rate: float = 9.0
) -> pl.DataFrame:
    """Match rates by interest-rate bin (nearest eighth), from both sides.

    Args:
        both: The two sides, from `sides`.
        bin_size: Bin width in percentage points.
        min_rate: Lower edge of the first bin.
        max_rate: Upper edge of the last bin.

    Returns:
        One row per bin with each side's counts and rate.
    """
    fha, hmda = both

    def prepare(col: str) -> Any:
        return lambda lf: lf.with_columns(
            binned(col, bin_size, mode="round", name="interest_rate_bin")
        ).filter(
            pl.col("interest_rate_bin").is_between(min_rate, max_rate) & pl.col(col).is_not_null()
        )

    return rate_by_sides(
        [fha.prepared(prepare("Interest Rate")), hmda.prepared(prepare("interest_rate"))],
        "interest_rate_bin",
    )


def purpose_rates(both: tuple[Side, Side]) -> pl.DataFrame:
    """Match rates for purchase and refinance loans, from both sides.

    FHA's two refinance codes are folded together to line up with HMDA's; loans with
    any other purpose are outside the comparison.

    Args:
        both: The two sides, from `sides`.

    Returns:
        One row per purpose label, purchase first.
    """
    fha, hmda = both
    return rate_by_sides(
        [
            fha.prepared(
                lambda lf: lf.with_columns(
                    pl.col("Loan Purpose")
                    .replace_strict(FHA_PURPOSE_FOLD, default=None)
                    .alias("purpose_label")
                ).drop_nulls("purpose_label")
            ),
            hmda.prepared(
                lambda lf: lf.with_columns(
                    pl.col("loan_purpose")
                    .replace_strict(HMDA_PURPOSE_FOLD, default=None)
                    .alias("purpose_label")
                ).drop_nulls("purpose_label")
            ),
        ],
        "purpose_label",
    )


def arm_rates(both: tuple[Side, Side]) -> pl.DataFrame:
    """Match rates for ARM and fixed-rate loans, from both sides.

    Args:
        both: The two sides, from `sides`.

    Returns:
        One row per rate type.
    """
    fha, hmda = both
    label = lambda col: pl.col(col).replace_strict(ARM_LABELS, default="Unknown").alias("rate_type")  # noqa: E731
    return rate_by_sides(
        [
            fha.prepared(lambda lf: lf.with_columns(label("i_ARM"))),
            hmda.prepared(
                lambda lf: lf.filter(pl.col("1(ARM)").is_not_null()).with_columns(label("1(ARM)"))
            ),
        ],
        "rate_type",
    )


def channel_rates(hmda: Side) -> pl.DataFrame:
    """HMDA-side match rates by submission channel.

    Args:
        hmda: The HMDA side, from `sides`.

    Returns:
        One row per channel with its display label.
    """
    return (
        rate_by(
            hmda.universe.filter(pl.col("submission_of_application").is_not_null()),
            hmda.matched,
            hmda.on,
            "submission_of_application",
            prefix="hmda",
        )
        .with_columns(
            pl.col("submission_of_application")
            .replace_strict(CHANNEL_LABELS, default="Unknown")
            .alias("channel")
        )
        .collect()
    )


# =============================================================================
# Figure panels shared by both eras
# =============================================================================


def _panel_temporal(ax: Any, yearly: pl.DataFrame, fha_monthly: pl.DataFrame) -> None:
    """Both sides' yearly rates over a monthly FHA fill.

    Yearly points are centred on July so they sit mid-year against the monthly fill.
    """

    def to_x(year: int, month: int) -> float:
        return (year - Config.MIN_YEAR) * 12 + (month - 1)

    monthly_x = [to_x(row["Year"], row["Month"]) for row in fha_monthly.iter_rows(named=True)]
    monthly_rates = fha_monthly["fha_match_rate"].to_list()
    ax.fill_between(monthly_x, monthly_rates, alpha=0.3, color=FHA_COLOR, label="FHA Monthly")
    ax.plot(monthly_x, monthly_rates, color=FHA_COLOR, alpha=0.5, linewidth=0.8)
    for series in RATE_SERIES:
        side = series.rate_col.split("_")[0]
        points = yearly.filter(pl.col(series.rate_col).is_not_null())
        ax.plot(
            [to_x(int(y), 7) for y in points["year"].to_list()],
            points[series.rate_col].to_list(),
            color=series.color,
            linewidth=2.5,
            marker=series.marker,
            markersize=7,
            label=f"{series.label} Yearly (centred at July)",
        )
        pooled = overall_rate(points, f"{side}_matched", f"{side}_total")
        ax.axhline(
            pooled,
            color=series.color,
            linestyle="--",
            alpha=0.6,
            linewidth=1.2,
            label=f"{series.label} overall ({pooled:.1%})",
        )
    years = sorted(yearly["year"].to_list())
    ax.set_xticks([to_x(year, 1) for year in years])
    ax.set_xticklabels([str(year) for year in years], fontsize=9)
    for year in years:
        ax.axvline(to_x(year, 1), color="gray", alpha=0.2, linewidth=0.5)
    ax.set_xlim(to_x(min(years), 1) - 1, to_x(max(years), 12) + 1)
    # Framed on the yearly series, not the monthly one: the first months of the
    # window match far below the rest while FHA endorsement coverage is ramping.
    set_rate_ylim(ax, yearly["hmda_match_rate"].to_list() + yearly["fha_match_rate"].to_list())
    ax.set_xlabel("Year")
    ax.set_ylabel("Match Rate")
    ax.set_title("Match rates over time")
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(True, alpha=0.3, axis="y")


def _panel_state_size(ax: Any, states: pl.DataFrame, side: str, title: str) -> None:
    """One side's state loan volume against its match rate, on a log volume axis.

    Only the extremes are labelled: a blanket "below 80%" rule labels nearly every
    state in the pre-2018 era, where the whole distribution sits in the seventies.
    """
    filtered = _real_states(states, side)
    if len(filtered) == 0:
        ax.set_axis_off()
        return
    x = filtered[f"{side}_total"].to_numpy()
    y = filtered[f"{side}_match_rate"].to_numpy()
    ax.scatter(x, y, alpha=0.7, s=40, color=FHA_COLOR)
    ax.set_xscale("log")
    labels = filtered["state"].to_list()
    by_volume = sorted(range(len(x)), key=lambda i: x[i], reverse=True)[:STATE_LABEL_COUNT]
    by_rate = sorted(range(len(y)), key=lambda i: y[i])[:STATE_LABEL_COUNT]
    for i in set(by_volume) | set(by_rate):
        ax.annotate(
            labels[i],
            (x[i], y[i]),
            fontsize=8,
            alpha=0.8,
            xytext=(5, 0),
            textcoords="offset points",
        )
    ax.set_xlabel("Total Loans (log scale)")
    ax.set_ylabel("Match Rate")
    ax.set_title(title)
    ax.set_ylim(max(0.0, float(y.min()) - 0.02), min(1.0, float(y.max()) + 0.02))
    ax.grid(True, alpha=0.3)


def _panel_county_size(ax: Any, corr: dict[str, Any], side: str) -> None:
    """One side's county volume against county match rate, on a log volume axis."""
    if not corr:
        ax.set_axis_off()
        return

    label = side.upper()
    counties = corr[f"{side}_counties"]
    x = counties[f"{side}_total"].to_numpy()
    y = counties[f"{side}_match_rate"].to_numpy()
    color = FHA_COLOR if side == "fha" else HMDA_COLOR

    ax.scatter(x, y, alpha=0.3, s=12, color=color)
    ax.set_xscale("log")
    ax.text(
        0.05,
        0.95,
        f"Spearman r = {corr[f'{side}_spearman_r']:.3f}\np = {corr[f'{side}_spearman_p']:.4f}",
        transform=ax.transAxes,
        fontsize=9,
        verticalalignment="top",
        bbox=dict(boxstyle="round", facecolor="white", alpha=0.8),
    )
    ax.set_xlabel(f"Total {label} Loans (log scale)")
    ax.set_ylabel("Match Rate")
    ax.set_title(f"{label} county size vs match rate (n={corr[f'{side}_n_counties']:,})")
    ax.set_ylim(max(0.0, float(y.min()) - 0.05), min(1.0, float(y.max()) + 0.05))
    ax.grid(True, alpha=0.3)


def _panel_binned(
    ax: Any,
    rates_df: pl.DataFrame | None,
    bin_col: str,
    xlabel: str,
    title: str,
    x_scale: float = 1.0,
) -> None:
    """Both sides' match rate across a binned dimension, over their densities."""
    if rates_df is None or len(rates_df) == 0:
        ax.set_axis_off()
        return
    panel_rate_vs_bin(
        ax, rates_df, bin_col, RATE_SERIES, xlabel=xlabel, x_scale=x_scale, legend_loc="lower left"
    )
    ax.set_title(title)


def _panel_category(
    ax: Any,
    rates_df: pl.DataFrame | None,
    category_col: str,
    series: tuple[RateSeries, ...],
    title: str,
) -> None:
    """Match rates per category, one bar per side."""
    if rates_df is None or len(rates_df) == 0:
        ax.set_axis_off()
        return
    labels = {c: str(c) for c in rates_df[category_col].to_list()}
    panel_grouped_bars(
        ax,
        rates_df,
        category_col,
        [RateSeries(s.rate_col, None, s.color, s.label) for s in series],
        labels,
        xlabel="",
        label_fontsize=8,
        headroom=0.06,
    )
    ax.set_title(title)


def _geography_row(fig: Any, grid: Any, row: int, results: dict[str, Any]) -> None:
    """State size | FHA county size | HMDA county size."""
    _panel_state_size(
        fig.add_subplot(grid[row, 0:2]),
        results["state_rates"],
        "fha",
        "FHA state size vs match rate",
    )
    county_corr = results.get("county_corr") or {}
    _panel_county_size(fig.add_subplot(grid[row, 2:4]), county_corr, "fha")
    _panel_county_size(fig.add_subplot(grid[row, 4:6]), county_corr, "hmda")


def plot_validation_panel(
    results: dict[str, Any], output_path: Path | None = None, interactive: bool = False
) -> None:
    """Render the post-2018 validation figure.

    Rows: match rates over time (full width); loan amount | interest rate; loan
    purpose | rate type | submission channel; state size | FHA county size | HMDA
    county size.

    Args:
        results: The accumulated ``run_validation`` results.
        output_path: Where to save the figure (PNG). Skipped if ``None``.
        interactive: Whether to display the figure interactively.
    """
    plt = get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return
    fig = plt.figure(figsize=(19, PANEL_HEIGHT_IN * 4 + 1.5))
    grid = panel_gridspec(
        fig,
        4,
        GRID_COLUMNS,
        title=f"HMDA-FHA match validation, post-2018 ({Config.MIN_YEAR}-{Config.MAX_YEAR})",
        title_fontsize=14,
        wspace=GRID_WSPACE,
    )
    _panel_temporal(fig.add_subplot(grid[0, :]), results["yearly_rates"], results["fha_monthly"])
    _panel_binned(
        fig.add_subplot(grid[1, 0:3]),
        results.get("loan_amount_rates"),
        "loan_amount_bin",
        "Loan Amount ($K)",
        "Match rates by loan amount",
        x_scale=0.001,
    )
    _panel_binned(
        fig.add_subplot(grid[1, 3:6]),
        results.get("interest_rate_rates"),
        "interest_rate_bin",
        "Interest Rate (%)",
        "Match rates by interest rate",
    )
    _panel_category(
        fig.add_subplot(grid[2, 0:2]),
        results.get("purpose_rates"),
        "purpose_label",
        RATE_SERIES,
        "By loan purpose",
    )
    _panel_category(
        fig.add_subplot(grid[2, 2:4]),
        results.get("arm_rates"),
        "rate_type",
        RATE_SERIES,
        "By rate type (ARM vs fixed)",
    )
    _panel_category(
        fig.add_subplot(grid[2, 4:6]),
        results.get("channel_rates"),
        "channel",
        HMDA_ONLY,
        "HMDA, by submission channel",
    )
    _geography_row(fig, grid, 3, results)
    save_and_close(fig, output_path, interactive)


def plot_state_match_rate_map(states: pl.DataFrame, output_path: Path | None = None) -> None:
    """Two-panel choropleth of state match rates, one panel per denominator.

    Args:
        states: Two-sided per-state rates, from `state_rates`.
        output_path: Destination PNG; None renders nothing.
    """

    def panel(side: str, title: str) -> StatePanel:
        kept = states.filter(
            ~pl.col("state").is_in(NON_STATE_CODES) & pl.col(f"{side}_match_rate").is_not_null()
        )
        return StatePanel(title, kept["state"].to_list(), kept[f"{side}_match_rate"].to_list())

    # Both sides sit in the 80s-90s; floor at 0.70 so the spread is not washed out.
    plot_state_choropleth(
        [panel("fha", "FHA Match Rate by State"), panel("hmda", "HMDA Match Rate by State")],
        "Match Rates by State",
        output_path,
        color_floor=0.70,
    )


def plot_county_match_rate_map(
    counties: pl.DataFrame,
    output_path: Path | None = None,
    min_loans: int = 50,
) -> None:
    """Two-panel choropleth of county match rates, one panel per denominator.

    Args:
        counties: Two-sided per-county rates, from `county_rates`.
        output_path: Destination PNG; None renders nothing.
        min_loans: Minimum loans on a side for its county to be drawn.
    """
    try:
        import json
        from urllib.request import urlopen

        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        print("plotly not available, skipping county map")
        return

    with urlopen(
        "https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json"
    ) as response:
        counties_geojson = json.load(response)

    fha_filtered = _county_side(counties, "fha", min_loans)
    hmda_filtered = _county_side(counties, "hmda", min_loans)
    all_rates = [
        r
        for r in (
            fha_filtered["fha_match_rate"].to_list() + hmda_filtered["hmda_match_rate"].to_list()
        )
        if r is not None
    ]

    if not all_rates:
        print("No county data available for mapping")
        return

    color_min = max(0.50, min(all_rates) - 0.02)
    color_max = min(1.0, max(all_rates) + 0.02)

    fig = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=(
            f"FHA Match Rate by County (n={len(fha_filtered):,})",
            f"HMDA Match Rate by County (n={len(hmda_filtered):,})",
        ),
        specs=[[{"type": "choropleth"}, {"type": "choropleth"}]],
        horizontal_spacing=0.02,
    )

    fig.add_trace(
        go.Choropleth(
            geojson=counties_geojson,
            locations=fha_filtered["county"].to_list(),
            z=fha_filtered["fha_match_rate"].to_list(),
            colorscale="RdYlGn",
            zmin=color_min,
            zmax=color_max,
            marker_line_width=0.1,
            marker_line_color="white",
            colorbar=dict(
                title="Match Rate",
                x=0.45,
                len=0.8,
                tickformat=".0%",
            ),
            hovertemplate="<b>FIPS: %{location}</b><br>Match Rate: %{z:.1%}<extra></extra>",
        ),
        row=1,
        col=1,
    )

    fig.add_trace(
        go.Choropleth(
            geojson=counties_geojson,
            locations=hmda_filtered["county"].to_list(),
            z=hmda_filtered["hmda_match_rate"].to_list(),
            colorscale="RdYlGn",
            zmin=color_min,
            zmax=color_max,
            marker_line_width=0.1,
            marker_line_color="white",
            colorbar=dict(
                title="Match Rate",
                x=1.0,
                len=0.8,
                tickformat=".0%",
            ),
            hovertemplate="<b>FIPS: %{location}</b><br>Match Rate: %{z:.1%}<extra></extra>",
        ),
        row=1,
        col=2,
    )

    fig.update_geos(
        scope="usa",
        showlakes=False,
        showland=True,
        landcolor="lightgray",
        showsubunits=True,
        subunitcolor="white",
    )

    fig.update_layout(
        title_text=f"Match Rates by County (min {min_loans} loans)",
        title_x=0.5,
        width=1600,
        height=700,
        margin=dict(l=0, r=0, t=80, b=0),
    )

    if output_path:
        fig.write_image(str(output_path), scale=2)
        print(f"Saved: {output_path}")


# =============================================================================
# Post-2018: report
# =============================================================================


def _print_correlation(label: str, corr: dict[str, Any]) -> None:
    print(f"{label}:")
    for k, v in corr.items():
        print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")


def run_validation(
    output_dir: Path | None = None,
    save_plots: bool = True,
    show_plots: bool = False,
    min_loans: int = 50,
) -> dict[str, Any]:
    """Run every post-2018 validation analysis, print the report, and draw the figures.

    Args:
        output_dir: Directory for the figures. Defaults to ``VALIDATION_OUTPUT_DIR``.
        save_plots: Whether to write the figures.
        show_plots: Whether to display the matplotlib figure interactively.
        min_loans: Minimum loans for a county to enter the county analyses.

    Returns:
        Every computed frame and statistic, keyed by name.
    """
    output_dir = output_dir or VALIDATION_OUTPUT_DIR
    if save_plots:
        output_dir.mkdir(parents=True, exist_ok=True)

    print("Loading data...")
    crosswalk, fha, hmda = load_data()
    both = sides(crosswalk, fha, hmda)
    n_matches, n_fha, n_hmda = (
        lf.select(pl.len()).collect().item() for lf in (crosswalk, fha, hmda)
    )
    print(f"Crosswalk: {n_matches:,} matched pairs")
    print(
        f"FHA intermediate: {n_fha:,} loans (FHA-side rates use {Config.MIN_YEAR}-{Config.MAX_YEAR} only)"
    )
    print(f"HMDA intermediate: {n_hmda:,} loans")

    print_section("1. Match Round Breakdown")
    round_breakdown = match_round_breakdown(crosswalk)
    print(round_breakdown)

    print_section("2. Match Rates by Year (FHA endorsement year / HMDA activity year)")
    yearly = yearly_rates(both)
    print(yearly)
    overall_hmda_rate = overall_rate(yearly, "hmda_matched", "hmda_total")
    overall_fha_rate = overall_rate(yearly, "fha_matched", "fha_total")
    print(f"\nOverall HMDA match rate: {overall_hmda_rate:.1%}")
    print(f"Overall FHA match rate ({Config.MIN_YEAR}-{Config.MAX_YEAR}): {overall_fha_rate:.1%}")

    print_section("3. FHA Monthly Match Rate Summary")
    fha_monthly = fha_monthly_match_rates(both[0])
    rate = fha_monthly["fha_match_rate"]
    print(
        f"Months: {len(fha_monthly)}; min {cast(float, rate.min()):.1%}, max {cast(float, rate.max()):.1%}, mean {cast(float, rate.mean()):.1%}, std {rate.std():.3f}"
    )
    print("\nLowest match rate months:")
    print(fha_monthly.sort("fha_match_rate").head(5))
    print("\nHighest match rate months:")
    print(fha_monthly.sort("fha_match_rate", descending=True).head(5))

    print_section("4. Match Rates by State (Top 20 by FHA Volume)")
    states = state_rates(both)
    print(states.head(20))
    print("\nLowest FHA match rate states (min 1000 loans):")
    print(states.filter(pl.col("fha_total") >= 1000).sort("fha_match_rate").head(10))

    print_section("5. State Size vs Match Rate Correlation")
    corr = state_correlations(states)
    _print_correlation("FHA side", corr["fha"])
    _print_correlation("\nHMDA side", corr["hmda"])

    print_section("6. Match Round Distribution by FHA Year")
    round_by_year = match_round_by_year(crosswalk, fha).filter(
        pl.col("Year").is_between(Config.MIN_YEAR, Config.MAX_YEAR)
    )
    print(round_by_year)

    print_section("7. Interpretation & Key Findings")
    print(f"  Overall FHA match rate ({Config.MIN_YEAR}-{Config.MAX_YEAR}): {overall_fha_rate:.1%}")
    print(f"  Overall HMDA match rate: {overall_hmda_rate:.1%}")
    print(
        f"  Round 1 (strict) share of matches: {round_breakdown.filter(pl.col('match_round') == 1)['pct'][0]:.1%}"
    )
    print(
        f"  Yearly FHA match rate std dev: {yearly['fha_match_rate'].std():.3f}; monthly: {rate.std():.3f}"
    )
    r = corr["fha"]["spearman_r"]
    strength = (
        "negligible"
        if abs(r) < 0.1
        else "weak"
        if abs(r) < 0.3
        else "moderate"
        if abs(r) < 0.5
        else "strong"
    )
    print(
        f"  State size vs match rate: FHA Spearman r = {r:.3f} (p={corr['fha']['spearman_p']:.4f}), HMDA r = {corr['hmda']['spearman_r']:.3f}"
    )
    print(
        f"  A {strength} {'negative' if r < 0 else 'positive'} correlation; below 0.3 in magnitude, match quality is not driven by state size."
    )

    results: dict[str, Any] = {
        "round_breakdown": round_breakdown,
        "yearly_rates": yearly,
        "fha_monthly": fha_monthly,
        "state_rates": states,
        "fha_corr": corr["fha"],
        "hmda_corr": corr["hmda"],
        "county_corr": {},
        "round_by_year": round_by_year,
        "overall_fha_rate": overall_fha_rate,
        "overall_hmda_rate": overall_hmda_rate,
    }

    if (show_plots or save_plots) and HAS_MATPLOTLIB:
        print_section("8. Visualizations")
        print("Computing county match rates...")
        counties = county_rates(both)
        county_corr = results["county_corr"] = compute_county_size_correlation(counties, min_loans)
        if county_corr:
            for side in ("fha", "hmda"):
                print(
                    f"  {side.upper()}: Spearman r = {county_corr[f'{side}_spearman_r']:.3f} (p={county_corr[f'{side}_spearman_p']:.4f}, n={county_corr[f'{side}_n_counties']:,})"
                )
        print("Computing match rates by loan characteristics...")
        results["loan_amount_rates"] = loan_amount_rates(both)
        results["interest_rate_rates"] = interest_rate_rates(both)
        results["purpose_rates"] = purpose_rates(both)
        results["arm_rates"] = arm_rates(both)
        results["channel_rates"] = channel_rates(both[1])
        print("Rendering the figures...")
        plot_validation_panel(
            results,
            output_path=output_dir / "hmda_fha_validation.png" if save_plots else None,
            interactive=show_plots,
        )
        plot_state_match_rate_map(
            states,
            output_path=output_dir / "hmda_fha_state_match_rate_map.png" if save_plots else None,
        )
        plot_county_match_rate_map(
            counties,
            output_path=output_dir / "hmda_fha_county_match_rate_map.png" if save_plots else None,
            min_loans=min_loans,
        )

    return results


# =============================================================================
# PRE-2018 ERA (2010-2017)
# =============================================================================
#
# The pre-2018 era is validated separately rather than by parameterising the
# post-2018 functions: its two sides carry different columns (FIPS on both
# sides instead of a postal abbreviation and a rate), it has no interest rate
# to bucket, and its decisive evidence -- the bridge placebo, the frozen
# artifact, the selection-bias tables -- has no post-2018 counterpart. The
# geography and loan-amount plotters ARE shared, because their input contract
# is a plain rate frame that both eras can build.

#: Pre-2018 carries state as a 2-digit FIPS on BOTH sides (HMDA ``state_code``,
#: FHA ``state_fips``); the choropleth plotters need postal abbreviations.
_STATE_FIPS_TO_ABBREV = FIPS_TO_STATE

#: The frozen 2010-2017 reference artifact numbers endorsements from 0 within a
#: snapshot date; current FHA silver numbers them from 1. Comparing the two
#: without this offset reports 0.0% agreement instead of 99.9% -- it is a
#: structural difference between two builds, not a fitted parameter.
FROZEN_FHA_INDEX_OFFSET = 1


def load_data_pre2018(
    crosswalk_path: Path | None = None,
    fha_path: Path | None = None,
    hmda_path: Path | None = None,
) -> tuple[pl.LazyFrame, pl.LazyFrame, pl.LazyFrame]:
    """Load the pre-2018 crosswalk and its two prepared intermediates.

    Args:
        crosswalk_path: Published pre-2018 crosswalk. Defaults to the standard location.
        fha_path: Prepared FHA endorsement pool. Defaults to the standard location.
        hmda_path: Prepared, ZIP-fanned HMDA pool. Defaults to the standard location.

    Returns:
        ``(crosswalk, fha, hmda)`` LazyFrames.
    """
    if crosswalk_path is None:
        crosswalk_path = Config.get_crosswalk_path("pre2018")
    if fha_path is None:
        fha_path = Config.HMDA_FHA_INTERMEDIATE_DIR / "fha_match_data_pre2018.parquet"
    if hmda_path is None:
        hmda_path = Config.HMDA_FHA_INTERMEDIATE_DIR / "hmda_match_data_pre2018.parquet"
    return pl.scan_parquet(crosswalk_path), pl.scan_parquet(fha_path), pl.scan_parquet(hmda_path)


def _crosswalk_years(crosswalk: pl.LazyFrame) -> pl.LazyFrame:
    """Tag each pair with its HMDA activity year, read off the ``HMDAIndex``.

    The published crosswalk deliberately carries no year column (it is
    schema-identical to the post-2018 file); the index prefix is the year.

    Returns:
        The crosswalk with an ``activity_year`` column attached.
    """
    return crosswalk.with_columns(
        pl.col("HMDAIndex").str.slice(0, 4).cast(pl.Int32).alias("activity_year")
    )


def sides_pre2018(
    crosswalk: pl.LazyFrame, fha: pl.LazyFrame, hmda: pl.LazyFrame
) -> tuple[Side, Side]:
    """The pre-2018 sides: FHA restricted to the union of endorsement windows.

    The HMDA intermediate carries one row per (loan, ZIP) fan-out, and ``ZIP`` is the
    only column that varies within a loan (22.2M rows for 9.38M loans; dropping ZIP
    leaves exactly one row per loan). Dropping it collapses the pool, as `sides` does
    post-2018, so each HMDA loan counts once in every HMDA-side rate.

    Args:
        crosswalk: The pre-2018 crosswalk.
        fha: Prepared FHA endorsement pool.
        hmda: Prepared, ZIP-fanned HMDA pool.

    Returns:
        ``(fha_side, hmda_side)``.
    """
    hmda_loans = hmda.drop("ZIP", strict=False).unique()
    return (
        Side(
            pre2018_endorsement_window_union(fha),
            matched_ids(crosswalk, "FHA_Index"),
            "FHA_Index",
            "fha",
        ),
        Side(hmda_loans, matched_ids(crosswalk, "HMDAIndex"), "HMDAIndex", "hmda"),
    )


def pre2018_per_year_counts(
    crosswalk: pl.LazyFrame,
    fha: pl.LazyFrame,
    hmda_dir: Path = Config.HMDA_PRE2018_SILVER_DIR,
    min_year: int = Config.PRE2018_MIN_YEAR,
    max_year: int = Config.PRE2018_MAX_YEAR,
) -> pl.DataFrame:
    """Per-source counts and both-sided match rates, by activity year.

    The HMDA denominator is the **raw** origination count, not the ZIP-joinable
    subset (which is ~0.4pp smaller and inflates every rate). The FHA
    denominator is that year's `PRE2018_YEAR_WINDOW`, Feb Y - Jan Y+1, and
    ``window_months`` makes 2010's nine-month source floor visible rather than
    leaving it to be mistaken for a matcher failure.

    Args:
        crosswalk: Published pre-2018 crosswalk.
        fha: Prepared FHA endorsement pool.
        hmda_dir: HMDA ``period_2007_2017`` silver loans directory.
        min_year: First activity year.
        max_year: Last activity year.

    Returns:
        One row per activity year, with per-source counts and both match rates.
    """
    denominators = hmda_fha_origination_counts(hmda_dir, min_year, max_year)
    tagged = _crosswalk_years(crosswalk)

    pairs = (
        tagged.group_by("activity_year")
        .agg(
            pl.len().alias("matched_pairs"),
            *[(pl.col("match_round") == r).sum().alias(f"round{r}") for r in PRE2018_ROUND_NUMBERS],
        )
        .collect()
    )

    rows = []
    for year in range(min_year, max_year + 1):
        window = (
            fha.filter(endorsement_window(year, PRE2018_YEAR_WINDOW))
            .select("Year", "Month")
            .collect()
        )
        row = pairs.filter(pl.col("activity_year") == year)
        matched = int(row["matched_pairs"][0]) if row.height else 0
        rows.append(
            {
                "activity_year": year,
                "hmda_fha_originations": denominators.get(year, 0),
                "fha_endorsements_in_window": window.height,
                "window_months": int(
                    window.select((pl.col("Year") * 100 + pl.col("Month")).n_unique()).item()
                ),
                "matched_pairs": matched,
                **{
                    f"round{r}": int(row[f"round{r}"][0]) if row.height else 0
                    for r in PRE2018_ROUND_NUMBERS
                },
            }
        )

    return (
        pl.DataFrame(rows)
        .with_columns(
            (pl.col("fha_endorsements_in_window") / pl.col("hmda_fha_originations")).alias(
                "pool_ratio"
            ),
            (pl.col("matched_pairs") / pl.col("hmda_fha_originations")).alias("hmda_match_rate"),
            (pl.col("matched_pairs") / pl.col("fha_endorsements_in_window")).alias(
                "fha_match_rate"
            ),
        )
        .sort("activity_year")
    )


def pre2018_endorsement_window_union(
    fha: pl.LazyFrame,
    min_year: int = Config.PRE2018_MIN_YEAR,
    max_year: int = Config.PRE2018_MAX_YEAR,
) -> pl.LazyFrame:
    """Restrict the FHA pool to the union of the per-year endorsement windows.

    The prepared FHA intermediate deliberately spans ``min_year`` through
    ``max_year + 1`` in full, because the per-year window is applied at match
    time. Every FHA-side *denominator* must therefore be re-restricted to
    Feb ``min_year`` - Jan ``max_year + 1`` (each year's `PRE2018_YEAR_WINDOW`
    abuts the next, so their union is that span). Skipping this silently adds eleven
    months of endorsements that no activity year could ever have matched -- 886,299 of
    them at the shipped bounds, which understates the FHA-side match rate by
    about 6.7pp.

    Args:
        fha: The FHA side's loan universe.
        min_year: First activity year to include, inclusive.
        max_year: Last activity year to include, inclusive.

    Returns:
        The FHA pool restricted to the union of the per-year windows.
    """
    lo, hi = PRE2018_YEAR_WINDOW
    return fha.filter(endorsement_window(min_year, (lo, (max_year - min_year) * 12 + hi)))


def pre2018_state_match_rates(both: tuple[Side, Side]) -> pl.DataFrame:
    """Both sides' match rates by state postal abbreviation, largest FHA states first.

    Both sides hold a 2-digit state FIPS pre-2018, so both are mapped through
    `_STATE_FIPS_TO_ABBREV`; the frame then has the post-2018 shape.

    Args:
        both: The two sides, from `sides_pre2018`.

    Returns:
        One row per state.
    """
    fha, hmda = both

    def to_abbrev(col: str) -> Any:
        return lambda lf: lf.with_columns(
            pl.col(col).replace_strict(_STATE_FIPS_TO_ABBREV, default=None).alias("state")
        ).drop_nulls("state")

    return rate_by_sides(
        [
            fha.prepared(to_abbrev("state_fips")),
            hmda.prepared(to_abbrev("state_code")),
        ],
        "state",
        sort_by="fha_total",
        descending=True,
    )


def pre2018_county_match_rates(both: tuple[Side, Side]) -> pl.DataFrame:
    """Both sides' match rates by 5-digit county FIPS.

    County is **not** a match key in this era -- it is deliberately held out -- so
    county agreement is a genuine precision check rather than a tautology.

    Args:
        both: The two sides, from `sides_pre2018`.

    Returns:
        One row per county.
    """
    fha, hmda = both
    return rate_by_sides(
        [
            fha.prepared(lambda lf: lf.drop_nulls("FIPS").rename({"FIPS": "county"})),
            hmda.prepared(
                lambda lf: lf.drop_nulls("county_code").rename({"county_code": "county"})
            ),
        ],
        "county",
    )


def pre2018_county_agreement(
    crosswalk: pl.LazyFrame, fha: pl.LazyFrame, hmda: pl.LazyFrame
) -> float:
    """Share of matched pairs whose two sides agree on county FIPS.

    County is held out of the base key (ZIP + amount + purpose), so this is a
    real precision check on every round.

    Args:
        crosswalk: The match crosswalk.
        fha: The FHA side's loan universe.
        hmda: The HMDA side's loan universe.

    Returns:
        The share of matched pairs whose sides agree on county, by round.
    """
    fha = pre2018_endorsement_window_union(fha)
    pairs = (
        crosswalk.join(fha.select("FHA_Index", "FIPS"), on="FHA_Index", how="left")
        .join(
            hmda.select("HMDAIndex", "county_code").unique(subset=["HMDAIndex"]),
            on="HMDAIndex",
            how="left",
        )
        .drop_nulls(["FIPS", "county_code"])
        .select((pl.col("FIPS") == pl.col("county_code")).mean().alias("agree"))
        .collect()
    )
    return float(pairs["agree"][0])


def pre2018_match_rates_by_loan_amount(
    both: tuple[Side, Side], bin_size: int = 10_000, max_amount: int = 750_000
) -> pl.DataFrame:
    """Match rates by loan-amount bin, both sides.

    Amounts are reconstructed from ``amount_key`` (the $1k bucket the era matches on,
    an identity relabelling of an exact-thousands dollar figure), because the
    intermediates drop the raw dollar columns.

    Args:
        both: The two sides, from `sides_pre2018`.
        bin_size: Bin width in dollars.
        max_amount: Upper edge of the last bin.

    Returns:
        One row per bin with each side's counts and rate.
    """
    fha, hmda = both
    dollars = (pl.col("amount_key") * 1000).alias("loan_amount")

    def prepare(lf: pl.LazyFrame) -> pl.LazyFrame:
        return (
            lf.with_columns(dollars)
            .with_columns(_hmda_amount_bin("loan_amount", bin_size))
            .filter(pl.col("loan_amount_bin").is_between(5000, max_amount))
        )

    return rate_by_sides(
        [fha.prepared(prepare), hmda.prepared(prepare)],
        "loan_amount_bin",
    )


def pre2018_match_rates_by_purpose(hmda: Side) -> pl.DataFrame:
    """HMDA-side match rates by the shared purpose vocabulary.

    Args:
        hmda: The HMDA side, from `sides_pre2018`.

    Returns:
        One row per loan purpose.
    """
    return rate_by(hmda.universe, hmda.matched, hmda.on, "purpose", prefix="hmda").collect()


def pre2018_placebo_controls(
    fha_path: Path | None = None,
    hmda_path: Path | None = None,
    min_year: int = Config.PRE2018_MIN_YEAR,
    max_year: int = Config.PRE2018_MAX_YEAR,
    seeds: tuple[int, ...] = (0, 1, 2),
) -> pl.DataFrame:
    """Re-run the matcher's 1:1 rounds against identity-destroying bridges.

    This is the decisive test that the bridge's lift is **identity** and not
    cell density: a permuted bridge preserves both marginals (the same FHA
    lenders, the same respondent keys, the same number of entries) and destroys
    only the pairing. Key relaxation in general counts cell density rather
    than recoverable matches; a control that
    relaxes the cell exactly as much as the real bridge does, and recovers
    almost nothing, is what discharges it.

    Every control runs the rounds without margin tiers (the baseline only those
    without the bridge filter), so the comparison isolates the bridge.

    Two controls:

    - ``permuted``: respondent keys shuffled across all entries.
    - ``size_matched``: shuffled **within** FHA-volume deciles, so a large
      lender is re-paired with another large lender's respondent. This closes
      the "the bridge only helps big lenders" reading.

    Args:
        fha_path: Prepared FHA endorsement pool.
        hmda_path: Prepared, ZIP-fanned HMDA pool.
        min_year: First activity year.
        max_year: Last activity year.
        seeds: Permutation seeds; the flat control is run once per seed.

    Returns:
        The placebo match rates for each control, beside the real rate.
    """
    if fha_path is None:
        fha_path = Config.HMDA_FHA_INTERMEDIATE_DIR / "fha_match_data_pre2018.parquet"
    if hmda_path is None:
        hmda_path = Config.HMDA_FHA_INTERMEDIATE_DIR / "hmda_match_data_pre2018.parquet"

    hmda_all = pl.read_parquet(hmda_path)
    fha_all = pl.read_parquet(fha_path)
    bridge_full = load_bridge("merged")
    real = bridge_full.select("fha_id_type", "fha_id", "resp_key")

    hmda_all = hmda_all.filter(pl.col("activity_year").is_between(min_year, max_year))
    one_to_one = tuple(r for r in ROUNDS_PRE2018 if not r.tiers)

    def total_pairs(bridge: pl.DataFrame | None) -> int:
        rounds = (
            one_to_one
            if bridge is not None
            else tuple(r for r in one_to_one if not r.bridge_filter)
        )
        return run_rounds_pre2018(hmda_all, fha_all, rounds, bridge).height

    baseline = total_pairs(None)
    rows = [
        {"control": "none (baseline)", "pairs": baseline},
        {"control": "real bridge", "pairs": total_pairs(real)},
    ]
    for seed in seeds:
        rows.append(
            {
                "control": f"permuted (seed {seed})",
                "pairs": total_pairs(real.with_columns(pl.col("resp_key").shuffle(seed=seed))),
            }
        )
    size_matched = (
        bridge_full.with_columns(
            pl.col("fha_loans")
            .rank("ordinal")
            .qcut(10, labels=[str(i) for i in range(10)])
            .alias("_decile")
        )
        .with_columns(pl.col("resp_key").shuffle(seed=seeds[0]).over("_decile"))
        .select("fha_id_type", "fha_id", "resp_key")
    )
    rows.append({"control": "size-matched permutation", "pairs": total_pairs(size_matched)})

    return pl.DataFrame(rows).with_columns(
        (pl.col("pairs") - baseline).alias("recovered_vs_baseline"),
    )


def pre2018_lender_agreement(
    crosswalk: pl.LazyFrame,
    fha: pl.LazyFrame,
    hmda: pl.LazyFrame,
    years: tuple[int, ...] = (2012, 2015),
) -> pl.DataFrame:
    """Modal-respondent agreement per FHA lender, split by match round.

    **This statistic is held out for every round without ``bridge_filter``.**
    Round-1 pairs are 1:1 on the base key alone, and the margin tiers select on
    county, ZIP share and rate, so lender identity played no part in choosing
    either. For a bridge round the bridge *is* a match key, so agreement there is
    true by construction and must never be quoted as validation. Its row is
    reported anyway -- with the shuffled control beside it -- precisely so the
    reader can see that it carries no information.

    Args:
        crosswalk: The match crosswalk.
        fha: The FHA side's loan universe.
        hmda: The HMDA side's loan universe.
        years: Activity years to include.

    Returns:
        The share of matched pairs whose sides agree on lender identity.
    """
    tagged = _crosswalk_years(crosswalk)
    rows = []
    for year in years:
        pairs = (
            tagged.filter(pl.col("activity_year") == year)
            .join(
                hmda.select("HMDAIndex", "resp_key").unique(subset=["HMDAIndex"]),
                on="HMDAIndex",
                how="left",
            )
            .join(fha.select("FHA_Index", "orig_id", "sponsor_id"), on="FHA_Index", how="left")
            .with_columns(
                pl.coalesce(
                    pl.when(pl.col("orig_id").is_not_null() & (pl.col("orig_id") != "")).then(
                        pl.col("orig_id")
                    ),
                    pl.col("sponsor_id"),
                ).alias("fha_lender")
            )
            .drop_nulls(["fha_lender", "resp_key"])
            .collect()
        )
        for round_number, round_ in enumerate(ROUNDS_PRE2018, start=1):
            subset = pairs.filter(pl.col("match_round") == round_number)
            if subset.height == 0:
                continue
            rows.append(
                {
                    "activity_year": year,
                    "match_round": round_number,
                    "pairs": subset.height,
                    "modal_share": _modal_respondent_share(subset),
                    "modal_share_shuffled": _modal_respondent_share(
                        subset.with_columns(pl.col("fha_lender").shuffle(seed=7))
                    ),
                    "held_out": not round_.bridge_filter,
                }
            )
    return pl.DataFrame(rows)


def _modal_respondent_share(pairs: pl.DataFrame) -> float:
    """Share of pairs whose HMDA respondent is the modal one for its FHA lender.

    Ties break on ``resp_key`` so the statistic is reproducible across runs.

    Returns:
        The modal respondent's share within each FHA lender.
    """
    modal = (
        pairs.group_by(["fha_lender", "resp_key"])
        .len()
        .sort(["fha_lender", "len", "resp_key"], descending=[False, True, False])
        .group_by("fha_lender")
        .first()
        .select("fha_lender", pl.col("resp_key").alias("_modal"))
    )
    joined = pairs.join(modal, on="fha_lender", how="left")
    return scalar_float((joined["resp_key"] == joined["_modal"]).mean() or 0.0)


def pre2018_selection_bias(
    crosswalk: pl.LazyFrame,
    hmda_dir: Path = Config.HMDA_PRE2018_SILVER_DIR,
    years: tuple[int, ...] = (2012, 2015),
) -> pl.DataFrame:
    """Demographic and income profile of matched vs unmatched originations.

    Reported at **two** configurations, because they differ materially and
    quoting only one misleads:

    - ``round1`` vs its complement -- the drop-all-ambiguous baseline.
    - ``matched`` (rounds 1+2, i.e. the published crosswalk) vs unmatched.

    The bridge-recovered round *picks* where the baseline *drops*, so the
    published set's skew is roughly half the baseline's. It is not zero:
    anyone doing distributional work on these matches must weight for it.

    Args:
        crosswalk: The match crosswalk.
        hmda_dir: Root of the HMDA silver layer.
        years: Activity years to include.

    Returns:
        Matched-versus-unmatched covariate means, by activity year.
    """
    tagged = _crosswalk_years(crosswalk).select("HMDAIndex", "match_round").collect()
    rows = []
    for year in years:
        loans = (
            scan_originations("period_2007_2017", year, year, loan_types=[2], root=hmda_dir)
            .select(
                "HMDAIndex",
                "applicant_race_1",
                "applicant_ethnicity",
                "applicant_sex",
                "income",
            )
            .collect()
            .join(tagged, on="HMDAIndex", how="left")
        )
        groups = {
            "round1": pl.col("match_round") == 1,
            "round1_complement": pl.col("match_round").is_null() | (pl.col("match_round") != 1),
            "matched": pl.col("match_round").is_not_null(),
            "unmatched": pl.col("match_round").is_null(),
        }
        for label, predicate in groups.items():
            subset = loans.filter(predicate)
            rows.append(
                {
                    "activity_year": year,
                    "group": label,
                    "loans": subset.height,
                    "white_pct": scalar_float((subset["applicant_race_1"] == 5).mean() or 0.0),
                    "not_hispanic_pct": scalar_float(
                        (subset["applicant_ethnicity"] == 2).mean() or 0.0
                    ),
                    "male_pct": scalar_float((subset["applicant_sex"] == 1).mean() or 0.0),
                    "median_income": scalar_float(subset["income"].median() or 0.0),
                }
            )
    return pl.DataFrame(rows)


def pre2018_frozen_agreement(
    crosswalk: pl.LazyFrame,
    legacy_path: Path | None = None,
    hmda_dir: Path = Config.HMDA_PRE2018_SILVER_DIR,
    min_year: int = Config.PRE2018_MIN_YEAR,
    max_year: int = Config.PRE2018_MAX_YEAR,
) -> pl.DataFrame:
    """Agreement with the frozen 2010-2017 reference artifact.

    The artifact has no generating code and cannot be rebuilt, so it is the
    only independent reference this matcher can be scored against. Two traps
    make a naive comparison read 0.0%:

    - **Split key.** Its ``HMDAIndex`` is the literal string ``'nan'`` for all
      2010-2016 rows; those years must be keyed on the legacy composite
      ``(respondent_id, agency_code, sequence_number)``. Only 2017 is
      ``HMDAIndex``-resolvable.
    - **Index offset.** See `FROZEN_FHA_INDEX_OFFSET`.

    Read the result as *replication*, not truth: the reference presumably used
    a similar geography-and-amount method, so agreement bounds reproducibility
    rather than correctness.

    Args:
        crosswalk: Published pre-2018 crosswalk.
        legacy_path: Frozen artifact. Defaults to the archived location.
        hmda_dir: HMDA ``period_2007_2017`` silver loans directory.
        min_year: First activity year.
        max_year: Last activity year.

    Returns:
        Pair-level agreement against the frozen legacy crosswalk.
    """
    if legacy_path is None:
        legacy_path = (
            Config.HMDA_FHA_MATCHING_DIR / "legacy" / "fha_hmda_matches_pre2018_202506.parquet"
        )
    if not Path(legacy_path).exists():
        return pl.DataFrame(
            schema={
                "activity_year": pl.Int32,
                "comparable_pairs": pl.Int64,
                "agreement": pl.Float64,
            }
        )

    frozen = pl.read_parquet(legacy_path)
    shipped = _crosswalk_years(crosswalk).collect()
    adjusted = (
        pl.col("_frozen").str.slice(0, 9)
        + (pl.col("_frozen").str.slice(9).cast(pl.Int64) + FROZEN_FHA_INDEX_OFFSET)
        .cast(pl.Utf8)
        .str.zfill(7)
    ).alias("_frozen_adjusted")

    rows = []
    for year in range(min_year, max_year + 1):
        if year == max_year:
            reference = frozen.filter(pl.col("HMDAIndex").str.starts_with(str(year))).select(
                "HMDAIndex", pl.col("FHA_Index").alias("_frozen")
            )
        else:
            keys = (
                scan_originations("period_2007_2017", year, year, loan_types=[2], root=hmda_dir)
                .select("HMDAIndex", "respondent_id", "agency_code", "sequence_number")
                .collect()
                .with_columns(pl.col("agency_code").cast(pl.Int64))
            )
            reference = (
                frozen.filter(pl.col("activity_year") == year)
                .select(
                    "respondent_id",
                    "agency_code",
                    "sequence_number",
                    pl.col("FHA_Index").alias("_frozen"),
                )
                .join(keys, on=["respondent_id", "agency_code", "sequence_number"], how="inner")
                .select("HMDAIndex", "_frozen")
                # A composite key that resolves to several HMDA loans is not
                # evidence either way; drop it rather than credit or blame it.
                .unique(subset=["HMDAIndex"], keep="none")
            )

        joined = (
            shipped.filter(pl.col("activity_year") == year)
            .join(reference, on="HMDAIndex", how="inner")
            .with_columns(adjusted)
            .with_columns((pl.col("FHA_Index") == pl.col("_frozen_adjusted")).alias("_agree"))
        )
        if joined.height == 0:
            continue
        round2 = joined.filter(pl.col("match_round") == 2)
        rows.append(
            {
                "activity_year": year,
                "frozen_rows": int(frozen.filter(pl.col("activity_year") == year).height),
                "comparable_pairs": joined.height,
                "agreement": scalar_float(joined["_agree"].mean() or 0.0),
                "round2_pairs": round2.height,
                "round2_agreement": scalar_float(round2["_agree"].mean() or 0.0)
                if round2.height
                else 0.0,
            }
        )
    return pl.DataFrame(rows)


def _panel_pre2018_temporal(ax: Any, per_year: pl.DataFrame) -> None:
    """Both sides' match rate by HMDA activity year.

    Years whose FHA endorsement window is short of twelve months are annotated: the
    FHA side of those years is measured against a partial denominator.
    """
    years = per_year["activity_year"].to_list()
    hmda_rates = per_year["hmda_match_rate"].to_list()

    ax.plot(
        years,
        hmda_rates,
        marker="o",
        color=HMDA_COLOR,
        linewidth=2.5,
        label="HMDA side (raw FHA originations)",
    )
    ax.plot(
        years,
        per_year["fha_match_rate"].to_list(),
        marker="s",
        color=FHA_COLOR,
        linewidth=2.5,
        label="FHA side (endorsements in window)",
    )

    for row in per_year.filter(pl.col("window_months") < 12).iter_rows(named=True):
        ax.annotate(
            f"{row['window_months']}-month\nFHA window",
            xy=(row["activity_year"], row["hmda_match_rate"]),
            xytext=(row["activity_year"] + 0.2, row["hmda_match_rate"] - 0.18),
            fontsize=8,
            arrowprops={"arrowstyle": "->", "alpha": 0.5},
        )

    ax.set_xlabel("HMDA activity year")
    ax.set_ylabel("Match rate")
    ax.set_title("Match rates by year")
    ax.set_ylim(0.0, 1.0)
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(True, alpha=0.3)


def _panel_pre2018_round_contribution(ax: Any, per_year: pl.DataFrame) -> None:
    """Each round's contribution to the match rate, stacked by year."""
    years = per_year["activity_year"].to_list()
    denominator = per_year["hmda_fha_originations"].to_numpy().astype(float)
    palette = (FHA_COLOR, HMDA_COLOR, COLORS["terracotta"], COLORS["mulberry"], COLORS["teal"])

    bottom = np.zeros(len(years))
    for round_number, round_ in enumerate(ROUNDS_PRE2018, start=1):
        share = per_year[f"round{round_number}"].to_numpy().astype(float) / denominator
        color = palette[(round_number - 1) % len(palette)]
        label = f"Round {round_number} - {round_.description}"
        ax.bar(years, share, bottom=bottom, color=color, label=label)
        bottom += share
    ax.set_xlabel("HMDA activity year")
    ax.set_ylabel("Share of HMDA FHA originations")
    ax.set_title("Match rate by round")
    ax.set_ylim(0.0, 1.0)
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(True, alpha=0.3, axis="y")


def _panel_pre2018_placebo(ax: Any, placebo: pl.DataFrame | None, denominator: int) -> None:
    """Each control's recovery above the baseline, in percentage points.

    The real bridge is drawn in the FHA colour; the placebos, which should recover
    close to nothing, in the HMDA colour.
    """
    if placebo is None:
        ax.set_axis_off()
        return

    rows = placebo.filter(pl.col("control") != "none (baseline)")
    if len(rows) == 0:
        ax.set_axis_off()
        return

    labels = rows["control"].to_list()
    lift = (rows["recovered_vs_baseline"].to_numpy().astype(float) / denominator) * 100
    colors = [FHA_COLOR if label == "real bridge" else HMDA_COLOR for label in labels]

    bars = ax.barh(labels, lift, color=colors)
    for bar, value in zip(bars, lift, strict=True):
        ax.text(
            bar.get_width() + 0.4,
            bar.get_y() + bar.get_height() / 2,
            f"{value:+.2f}pp",
            va="center",
            fontsize=8,
        )
    ax.set_xlabel("Percentage points recovered above the baseline")
    ax.set_title("Lender bridge: placebo controls")
    ax.tick_params(axis="y", labelsize=8)
    ax.grid(True, alpha=0.3, axis="x")


def plot_pre2018_validation_panel(
    results: dict[str, Any], output_path: Path | None = None, interactive: bool = False
) -> None:
    """Render the pre-2018 validation figure.

    Rows: match rates by year (full width); round contribution | placebo controls;
    loan amount | loan purpose; state size | FHA county size | HMDA county size.
    There is no interest-rate panel: pre-2018 HMDA carries no interest rate, which is
    a structural limit of the era rather than an omission.

    Args:
        results: The accumulated ``run_validation_pre2018`` results.
        output_path: Where to save the figure (PNG). Skipped if ``None``.
        interactive: Whether to display the figure interactively.
    """
    plt = get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return
    fig = plt.figure(figsize=(19, PANEL_HEIGHT_IN * 4 + 1.5))
    grid = panel_gridspec(
        fig,
        4,
        GRID_COLUMNS,
        title=f"HMDA-FHA match validation, pre-2018 ({Config.PRE2018_MIN_YEAR}-{Config.PRE2018_MAX_YEAR})",
        title_fontsize=14,
        wspace=GRID_WSPACE,
    )
    _panel_pre2018_temporal(fig.add_subplot(grid[0, :]), results["per_year"])
    _panel_pre2018_round_contribution(fig.add_subplot(grid[1, 0:3]), results["per_year"])
    _panel_pre2018_placebo(
        fig.add_subplot(grid[1, 3:6]), results.get("placebo"), results["total_hmda"]
    )
    _panel_binned(
        fig.add_subplot(grid[2, 0:3]),
        results.get("loan_amount_rates"),
        "loan_amount_bin",
        "Loan Amount ($K)",
        "Match rates by loan amount",
        x_scale=0.001,
    )
    _panel_category(
        fig.add_subplot(grid[2, 3:6]),
        results.get("purpose_rates"),
        "purpose",
        HMDA_ONLY,
        "HMDA, by loan purpose",
    )
    _geography_row(fig, grid, 3, results)
    save_and_close(fig, output_path, interactive)


def run_validation_pre2018(
    output_dir: Path | None = None,
    save_plots: bool = True,
    show_plots: bool = False,
    min_loans: int = 50,
    skip_placebo: bool = False,
) -> dict[str, Any]:
    """Run every pre-2018 validation analysis and write the figures.

    Args:
        output_dir: Figure directory. Defaults to ``VALIDATION_OUTPUT_DIR``.
        save_plots: Write figures to disk.
        show_plots: Display figures interactively.
        min_loans: Minimum loans for county filtering.
        skip_placebo: Skip the placebo controls, which re-run the matcher several
            times and dominate the runtime.

    Returns:
        Every computed table, keyed by name.
    """
    output_dir = output_dir or VALIDATION_OUTPUT_DIR
    if save_plots:
        output_dir.mkdir(parents=True, exist_ok=True)

    print("Loading pre-2018 data...")
    crosswalk, fha, hmda = load_data_pre2018()
    both = sides_pre2018(crosswalk, fha, hmda)

    print_section("1. Per-source counts and both-sided match rates")
    per_year = pre2018_per_year_counts(crosswalk, fha)
    print(per_year)
    total_pairs = int(per_year["matched_pairs"].sum())
    total_hmda = int(per_year["hmda_fha_originations"].sum())
    total_fha = int(per_year["fha_endorsements_in_window"].sum())
    overall_hmda_rate, overall_fha_rate = total_pairs / total_hmda, total_pairs / total_fha
    print(f"\nPairs: {total_pairs:,}")
    print(f"HMDA-side match rate: {overall_hmda_rate:.1%} of {total_hmda:,} originations")
    print(f"FHA-side match rate:  {overall_fha_rate:.1%} of {total_fha:,} in-window endorsements")
    print(
        "   ".join(
            f"Round {r}: {int(per_year[f'round{r}'].sum()):,}" for r in PRE2018_ROUND_NUMBERS
        )
    )

    print_section("2. Held-out county agreement")
    county_agreement = pre2018_county_agreement(crosswalk, fha, hmda)
    print(f"Matched pairs agreeing on county FIPS: {county_agreement:.2%}")

    print_section("3. Geography")
    states = pre2018_state_match_rates(both)
    print(states.head(15))
    corr = state_correlations(states)
    print(f"\nFHA state size vs match rate:  Spearman r = {corr['fha']['spearman_r']:.3f}")
    print(f"HMDA state size vs match rate: Spearman r = {corr['hmda']['spearman_r']:.3f}")

    print_section("4. Lender agreement (held out for every round without the bridge filter)")
    lender_agreement = pre2018_lender_agreement(crosswalk, fha, hmda)
    print(lender_agreement)
    print(
        "\nRound 2 is bridged on lender identity, so its agreement is true by\nconstruction and is NOT evidence. Rounds 1 and 3 are held out."
    )

    print_section("5. Selection bias")
    selection_bias = pre2018_selection_bias(crosswalk)
    print(selection_bias)

    print_section("6. Frozen-artifact agreement")
    frozen_agreement = pre2018_frozen_agreement(crosswalk)
    print(frozen_agreement)

    placebo = None
    if not skip_placebo:
        print_section("7. Placebo controls")
        placebo = pre2018_placebo_controls()
        print(
            placebo.with_columns(
                (pl.col("recovered_vs_baseline") / total_hmda * 100).round(2).alias("lift_pp")
            )
        )

    results: dict[str, Any] = {
        "per_year": per_year,
        "total_hmda": total_hmda,
        "overall_hmda_rate": overall_hmda_rate,
        "overall_fha_rate": overall_fha_rate,
        "county_agreement": county_agreement,
        "state_rates": states,
        "fha_corr": corr["fha"],
        "hmda_corr": corr["hmda"],
        "county_corr": {},
        "lender_agreement": lender_agreement,
        "selection_bias": selection_bias,
        "frozen_agreement": frozen_agreement,
        "placebo": placebo,
    }

    if (show_plots or save_plots) and HAS_MATPLOTLIB:
        print_section("8. Figures")
        print("Computing county match rates...")
        counties = pre2018_county_match_rates(both)
        results["county_corr"] = compute_county_size_correlation(counties, min_loans)
        print("Computing match rates by loan amount and purpose...")
        results["loan_amount_rates"] = pre2018_match_rates_by_loan_amount(both)
        results["purpose_rates"] = pre2018_match_rates_by_purpose(both[1])
        print("Rendering the figures...")
        plot_pre2018_validation_panel(
            results,
            output_path=output_dir / "hmda_fha_pre2018_validation.png" if save_plots else None,
            interactive=show_plots,
        )
        plot_state_match_rate_map(
            states,
            output_path=output_dir / "hmda_fha_pre2018_state_match_rate_map.png"
            if save_plots
            else None,
        )
        plot_county_match_rate_map(
            counties,
            output_path=output_dir / "hmda_fha_pre2018_county_match_rate_map.png"
            if save_plots
            else None,
            min_loans=min_loans,
        )
        print(
            "\nNo match-rate-by-interest-rate panel exists for this era: pre-2018\nHMDA carries no interest_rate. That absence is the era's structural\nceiling, not an omission."
        )

    return results
