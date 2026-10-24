"""Validation reports for the HMDA-FHFA crosswalks, both eras.

Post-2018 (`run_validation`, aliased as `run_validation`): a rich FHFA
fingerprint (rate, LTV, DTI, term, channel) and a five-round workflow. Pre-2018
(`run_validation_pre2018`): a sparse FHFA schema, a three-round workflow whose second
round reverses FHFA's documented AMFI income inflation, and the precision
diagnostics that evidence needs (demographic agreement against a random baseline,
amount and income agreement per round, the AMFI residual).

Both eras share one geometry: the FHFA side is keyed by ``(year, enterprise_flag,
record_number)``, the HMDA side by ``HMDAIndex``, and the HMDA rates that pair with
FHFA use only loans reported as sold to Fannie Mae or Freddie Mac.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.reference.states import FIPS_INT_TO_STATE
from mortgage_data_manager.core.scalars import scalar_float, scalar_int
from mortgage_data_manager.matching.match_hmda_fhfa.config import (
    HMDAFHFAMatchingConfig,
)
from mortgage_data_manager.matching.match_hmda_fhfa.utils import (
    assign_pre2018_hmda_index,
)
from mortgage_data_manager.matching.validation_common import (
    COLORS,
    HAS_MATPLOTLIB,
    NON_STATE_CODES,
    RateSeries,
    Side,
    StatePanel,
    TemporalSeries,
    analyze_state_size_correlation,
    binned,
    get_pyplot,
    matched_ids,
    overall_rate,
    panel_gridspec,
    panel_grouped_bars,
    panel_rate_vs_bin,
    panel_temporal,
    plot_state_choropleth,
    print_section,
    rate_by,
    rate_by_sides,
    save_and_close,
)
from mortgage_data_manager.sources.fhfa.config import FHFAConfig
from mortgage_data_manager.sources.hmda.load import scan_hmda, scan_hmda_for_index

#: Both eras write here; the era lives in the filename, not the directory.
VALIDATION_OUTPUT_DIR = MortgageDataConfig.PROJECT_DIR / "docs" / "matching" / "figures"

FHFA_COLOR = COLORS["hunter"]
HMDA_COLOR = COLORS["gold"]
MATCHED_COLOR = COLORS["midnight"]
DENSITY_COLOR = COLORS["grey"]

# Vertical inches allotted to one grid row; each canvas is sized from its row count.
PANEL_HEIGHT_IN = 4.5

#: The FHFA loan key, as the FHFA universe names it.
FHFA_KEY = ["year", "enterprise_flag", "record_number"]
GSE_PURCHASER_TYPES = [1, 3]
ENTERPRISE_NAMES = {1: "Fannie Mae", 2: "Freddie Mac"}
#: HMDA purchaser_type -> FHFA enterprise_flag.
HMDA_ENTERPRISE = {1: 1, 3: 2}

# The two sides of every paired rate panel: the HMDA side is the GSE-sold subset.
RATE_SERIES = (
    RateSeries("hmda_gse_match_rate", "hmda_gse_total", HMDA_COLOR, "HMDA GSE", "s"),
    RateSeries("fhfa_match_rate", "fhfa_total", FHFA_COLOR, "FHFA", "o"),
)

POST_AVAILABLE_YEARS = list(range(2018, 2026))
PRE_AVAILABLE_YEARS = list(range(2008, 2018))
PRE_ROUND_COLORS = {1: COLORS["midnight"], 2: COLORS["gold"], 3: COLORS["teal"]}
# FHFA's AMFI top-code sentinel, excluded in the pre-2018 AMFI cleanup.
PRE_FHFA_TOPCODE = 9_999_999
# Half-width of the AMFI-residual chart window; residuals beyond it are a thin tail.
AMFI_CHART_WINDOW = 0.15


# =============================================================================
# Sides (both eras)
# =============================================================================


def sides(
    crosswalk: pl.LazyFrame,
    fhfa: pl.LazyFrame,
    hmda: pl.LazyFrame,
    fhfa_key_in_crosswalk: list[str],
) -> dict[str, Side]:
    """The measured sides: FHFA, HMDA (every purchaser type) and HMDA GSE-sold.

    Args:
        crosswalk: The match crosswalk.
        fhfa: The FHFA side's loan universe.
        hmda: The HMDA side's loan universe.
        fhfa_key_in_crosswalk: How the crosswalk spells the FHFA key, in ``FHFA_KEY``
            order (the two eras' crosswalks differ).

    Returns:
        ``{"fhfa": Side, "hmda": Side, "hmda_gse": Side}``.
    """
    hmda_matched = matched_ids(crosswalk, "HMDAIndex")
    return {
        "fhfa": Side(
            fhfa, matched_ids(crosswalk, fhfa_key_in_crosswalk, as_cols=FHFA_KEY), FHFA_KEY, "fhfa"
        ),
        "hmda": Side(hmda, hmda_matched, "HMDAIndex", "hmda"),
        "hmda_gse": Side(
            hmda.filter(pl.col("purchaser_type").is_in(GSE_PURCHASER_TYPES)),
            hmda_matched,
            "HMDAIndex",
            "hmda_gse",
        ),
    }


def _post_fhfa_key(crosswalk: pl.LazyFrame) -> list[str]:
    """The post-2018 crosswalk's FHFA key (the year column was renamed once)."""
    year_col = "fhfa_year" if "fhfa_year" in crosswalk.collect_schema() else "activity_year"
    return [year_col, "enterprise_flag", "record_number"]


PRE_FHFA_KEY_IN_CROSSWALK = ["year", "Enterprise Flag", "Record Number"]


# =============================================================================
# Statistics (both eras)
# =============================================================================


def match_round_breakdown(crosswalk: pl.LazyFrame) -> pl.DataFrame:
    """Pair count and share per match round.

    Args:
        crosswalk: The match crosswalk.

    Returns:
        One row per match round.
    """
    return (
        crosswalk.group_by("match_round")
        .agg(pl.len().alias("count"))
        .with_columns((pl.col("count") / pl.col("count").sum()).alias("pct"))
        .sort("match_round")
        .collect()
    )


def yearly_rates(sides: dict[str, Side]) -> pl.DataFrame:
    """FHFA acquisition-year rates beside HMDA GSE-sold activity-year rates.

    Args:
        sides: From `sides`.

    Returns:
        One row per year with each side's counts and rate.
    """
    hmda = sides["hmda_gse"].prepared(lambda lf: lf.rename({"activity_year": "year"}))
    return rate_by_sides([sides["fhfa"], hmda], "year")


def hmda_yearly_rates(sides: dict[str, Side]) -> pl.DataFrame:
    """HMDA-side rates by activity year over every purchaser type that could match.

    Args:
        sides: From `sides`.

    Returns:
        One row per activity year.
    """
    hmda = sides["hmda"]
    return rate_by(
        hmda.universe,
        hmda.matched,
        hmda.on,
        "activity_year",
        prefix=hmda.prefix,
        sort_by="activity_year",
        descending=False,
    ).collect()


def enterprise_rates(sides: dict[str, Side]) -> pl.DataFrame:
    """Both sides' rates per enterprise (HMDA purchaser type mapped to enterprise).

    Args:
        sides: From `sides`.

    Returns:
        One row per enterprise with its display name.
    """
    hmda = sides["hmda_gse"].prepared(
        lambda lf: lf.with_columns(
            pl.col("purchaser_type")
            .replace_strict(HMDA_ENTERPRISE, default=None)
            .cast(pl.Int32)
            .alias("enterprise_flag")
        )
    )
    return rate_by_sides([sides["fhfa"], hmda], "enterprise_flag").with_columns(
        pl.col("enterprise_flag")
        .replace_strict(ENTERPRISE_NAMES, default="Unknown")
        .alias("enterprise_name")
    )


def state_rates(sides: dict[str, Side]) -> pl.DataFrame:
    """FHFA-side rates by state FIPS, largest states first.

    Args:
        sides: From `sides`.

    Returns:
        One row per state code.
    """
    fhfa = sides["fhfa"]
    return rate_by(fhfa.universe, fhfa.matched, fhfa.on, "state_code", prefix=fhfa.prefix).collect()


def compute_state_size_correlation(states: pl.DataFrame) -> dict[str, Any]:
    """Spearman correlation of FHFA state volume against state match rate.

    Small states are excluded (below a hundred loans the rate is mostly noise), and so
    are the territories and the unknown-state code: HMDA cannot place those loans, so
    their match rate is a structural zero, not a measurement.

    Args:
        states: From `state_rates`.

    Returns:
        The correlation statistics plus the filtered ``states`` frame; empty if
        fewer than three states clear the threshold.
    """
    abbrev = (
        pl.col("state_code")
        .cast(pl.Int64, strict=False)
        .replace_strict(FIPS_INT_TO_STATE, default=None)
    )
    filtered = states.filter(
        (pl.col("fhfa_total") >= 100)
        & pl.col("fhfa_match_rate").is_not_null()
        & abbrev.is_not_null()
        & ~abbrev.is_in(NON_STATE_CODES)
    )
    if len(filtered) < 3:
        return {}
    return analyze_state_size_correlation(filtered, "fhfa_total", "fhfa_match_rate") | {
        "states": filtered
    }


def _hmda_amount_bin(col: str, bin_size: int) -> pl.Expr:
    """HMDA reports amounts in $10k steps centred on $5k, so bins are centred there too."""
    return binned(col, bin_size, mode="round", offset=5000, name="loan_amount_bin")


def loan_amount_rates(
    sides: dict[str, Side], fhfa_amount_col: str, bin_size: int = 10_000, max_amount: int = 750_000
) -> pl.DataFrame:
    """FHFA and HMDA GSE-sold rates by loan-amount bin.

    Args:
        sides: From `sides`.
        fhfa_amount_col: The FHFA amount column (it differs between eras).
        bin_size: Bin width in dollars.
        max_amount: Upper edge of the last bin.

    Returns:
        One row per bin with each side's counts and rate.
    """

    def prepare(col: str) -> Any:
        return lambda lf: lf.with_columns(_hmda_amount_bin(col, bin_size)).filter(
            pl.col("loan_amount_bin").is_between(5000, max_amount)
        )

    return rate_by_sides(
        [
            sides["fhfa"].prepared(prepare(fhfa_amount_col)),
            sides["hmda_gse"].prepared(prepare("loan_amount")),
        ],
        "loan_amount_bin",
    ).filter(pl.col("loan_amount_bin").is_not_null())


def interest_rate_rates(
    sides: dict[str, Side], bin_size: float = 0.125, min_rate: float = 2.0, max_rate: float = 9.0
) -> pl.DataFrame:
    """FHFA and HMDA GSE-sold rates by interest-rate bin (post-2018 only).

    Args:
        sides: From `sides`.
        bin_size: Bin width in percentage points.
        min_rate: Lower edge of the first bin.
        max_rate: Upper edge of the last bin.

    Returns:
        One row per bin with each side's counts and rate.
    """

    def prepare(col: str) -> Any:
        return lambda lf: lf.with_columns(
            binned(col, bin_size, mode="round", name="interest_rate_bin")
        ).filter(
            pl.col("interest_rate_bin").is_between(min_rate, max_rate) & pl.col(col).is_not_null()
        )

    return rate_by_sides(
        [
            sides["fhfa"].prepared(prepare("interest_rate_at_origination")),
            sides["hmda_gse"].prepared(prepare("interest_rate")),
        ],
        "interest_rate_bin",
    ).filter(pl.col("interest_rate_bin").is_not_null())


# =============================================================================
# Figure panels (both eras)
# =============================================================================


def _panel_yearly_rates(ax: Any, yearly: pl.DataFrame, title: str) -> None:
    """Both sides' match rate per year, against their overall rates.

    Only years a side actually matched are drawn; a year with no matches is a gap in
    coverage, not a zero.
    """
    series = []
    for one, side in zip(RATE_SERIES, ("hmda_gse", "fhfa")):
        years = yearly.filter(pl.col(f"{side}_matched") > 0).sort("year")
        if len(years):
            series.append(
                TemporalSeries(
                    years["year"].to_list(),
                    years[one.rate_col].to_list(),
                    overall_rate(years, f"{side}_matched", f"{side}_total"),
                    one.color,
                    f"{one.label} Match Rate",
                    one.marker,
                )
            )
    panel_temporal(ax, series, xlabel="Year", title=title, legend_loc="lower right")


def _panel_state_size(ax: Any, corr: dict[str, Any]) -> None:
    """FHFA state loan volume against state match rate, on a log volume axis."""
    if not corr:
        ax.set_axis_off()
        return
    x = corr["states"]["fhfa_total"].to_numpy()
    y = corr["states"]["fhfa_match_rate"].to_numpy()
    ax.scatter(x, y, alpha=0.7, s=45, color=FHFA_COLOR)
    ax.set_xscale("log")
    ax.text(
        0.05,
        0.95,
        f"Spearman r = {corr['spearman_r']:.3f}\np = {corr['spearman_p']:.4f}\nn = {corr['n_states']} states",
        transform=ax.transAxes,
        fontsize=9,
        verticalalignment="top",
        bbox=dict(boxstyle="round", facecolor="white", alpha=0.8),
    )
    ax.set_xlabel("Total FHFA Loans (log scale)")
    ax.set_ylabel("Match Rate")
    ax.set_title("State size vs match rate")
    ax.set_ylim(max(0.0, float(y.min()) - 0.02), min(1.0, float(y.max()) + 0.02))
    ax.grid(True, alpha=0.3)


def _panel_enterprise(ax: Any, enterprise: pl.DataFrame) -> None:
    """Both sides' match rate per enterprise, as paired bars."""
    panel_grouped_bars(
        ax,
        enterprise,
        "enterprise_name",
        (
            RateSeries("fhfa_match_rate", None, FHFA_COLOR, "FHFA Match Rate"),
            RateSeries("hmda_gse_match_rate", None, HMDA_COLOR, "HMDA GSE Match Rate"),
        ),
        {name: name for name in enterprise["enterprise_name"].to_list()},
        xlabel="Enterprise",
        label_fontsize=10,
    )
    ax.set_title("Match rates by enterprise")


def _panel_binned(ax: Any, rates_df: pl.DataFrame | None, bin_col: str, xlabel: str) -> None:
    """Both sides' match rate across a binned dimension, over their densities."""
    if rates_df is None or len(rates_df) == 0:
        ax.set_axis_off()
        return
    panel_rate_vs_bin(
        ax,
        rates_df,
        bin_col,
        RATE_SERIES,
        xlabel=xlabel,
        x_scale=0.001 if bin_col == "loan_amount_bin" else 1.0,
        legend_loc="lower left",
    )
    ax.set_title(f"Match rates by {xlabel.split(' (')[0].lower()}")


def _state_panel(states: pl.DataFrame, title: str) -> StatePanel:
    """Turn the FHFA state frame (integer FIPS) into a choropleth panel."""
    with_abbrev = states.with_columns(
        pl.col("state_code")
        .cast(pl.Int64, strict=False)
        .replace_strict(FIPS_INT_TO_STATE, default=None)
        .alias("state_abbrev")
    ).filter(
        pl.col("state_abbrev").is_not_null()
        & ~pl.col("state_abbrev").is_in(NON_STATE_CODES)
        & pl.col("fhfa_match_rate").is_not_null()
    )
    return StatePanel(
        title, with_abbrev["state_abbrev"].to_list(), with_abbrev["fhfa_match_rate"].to_list()
    )


def plot_state_match_rate_map(
    states: pl.DataFrame, title: str, output_path: Path | None = None
) -> None:
    """Choropleth of FHFA-side match rates by state.

    Args:
        states: From `state_rates`.
        title: Figure title.
        output_path: Destination PNG; None renders nothing.
    """
    plot_state_choropleth([_state_panel(states, title)], title, output_path)


def _print_headline(sides: dict[str, Side], crosswalk: pl.LazyFrame, label: str) -> None:
    n = {
        k: lf.lazy().select(pl.len()).collect().item()
        for k, lf in (
            ("pairs", crosswalk),
            ("fhfa", sides["fhfa"].universe),
            ("hmda", sides["hmda"].universe),
        )
    }
    print(f"Crosswalk: {n['pairs']:,} matched pairs")
    print(f"FHFA data: {n['fhfa']:,} loans ({label})")
    print(f"HMDA data: {n['hmda']:,} loans (filtered to GSE-eligible)")


# =============================================================================
# POST-2018
# =============================================================================


def load_crosswalks() -> pl.LazyFrame:
    """The post-2018 crosswalk."""
    return pl.scan_parquet(HMDAFHFAMatchingConfig.get_crosswalk_path("post2018"))


def load_fhfa_data(years: list[int] | None = None) -> pl.LazyFrame:
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


def load_hmda_data(crosswalk: pl.LazyFrame) -> pl.LazyFrame:
    """Load the HMDA originations that could reach FHFA, from the crosswalk's releases.

    Reading the releases the crosswalk's ``HMDAIndex`` values came from keeps the
    denominator on the same rows the matcher saw, even after a newer release lands.

    Args:
        crosswalk: The post-2018 crosswalk.

    Returns:
        1-4 unit originations kept in portfolio or sold to a GSE or the secondary market.
    """
    return (
        scan_hmda_for_index(crosswalk.select("HMDAIndex"))
        .select(
            "HMDAIndex",
            "activity_year",
            "action_taken",
            "purchaser_type",
            "total_units",
            "state_code",
            "loan_amount",
            "interest_rate",
            "loan_purpose",
        )
        .filter(
            (pl.col("action_taken") == 1)
            & (pl.col("total_units") <= 4)  # FHFA only covers 1-4 units
            & (
                (pl.col("purchaser_type") == 0)  # Not sold at origination
                | pl.col("purchaser_type").is_in([1, 3])  # GSE
                | (pl.col("purchaser_type") >= 5)  # Secondary market
            )
        )
    )


def plot_validation_panel(
    results: dict[str, Any], output_path: Path | None = None, interactive: bool = False
) -> None:
    """Render the post-2018 validation figure.

    Rows: match rates by year (full width); loan amount | interest rate; state-size
    scatter | match rates by enterprise.

    Args:
        results: The accumulated ``run_validation`` results.
        output_path: Where to save the figure (PNG). Skipped if ``None``.
        interactive: Whether to display the figure interactively.
    """
    plt = get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return
    fig = plt.figure(figsize=(16, PANEL_HEIGHT_IN * 3 + 1.5))
    grid = panel_gridspec(
        fig,
        3,
        2,
        title=f"HMDA-FHFA match validation, post-2018 ({POST_AVAILABLE_YEARS[0]}-{POST_AVAILABLE_YEARS[-1]}, GSE loans)",
        title_fontsize=14,
    )
    _panel_yearly_rates(fig.add_subplot(grid[0, :]), results["yearly_rates"], "Match rates by year")
    _panel_binned(
        fig.add_subplot(grid[1, 0]),
        results.get("loan_amount_rates"),
        "loan_amount_bin",
        "Loan Amount ($K)",
    )
    _panel_binned(
        fig.add_subplot(grid[1, 1]),
        results.get("interest_rate_rates"),
        "interest_rate_bin",
        "Interest Rate (%)",
    )
    _panel_state_size(fig.add_subplot(grid[2, 0]), results.get("state_corr") or {})
    _panel_enterprise(fig.add_subplot(grid[2, 1]), results["enterprise_rates"])
    save_and_close(fig, output_path, interactive)


def run_validation(
    output_dir: Path | None = None, save_plots: bool = True, show_plots: bool = False
) -> dict[str, Any]:
    """Run every post-2018 validation analysis, print the report, and draw the figures.

    Args:
        output_dir: Directory for the figures. Defaults to ``VALIDATION_OUTPUT_DIR``.
        save_plots: Whether to write the figures.
        show_plots: Whether to display the matplotlib figure interactively.

    Returns:
        Every computed frame and statistic, keyed by name.
    """
    output_dir = output_dir or VALIDATION_OUTPUT_DIR
    if save_plots:
        output_dir.mkdir(parents=True, exist_ok=True)

    print("Loading data...")
    crosswalk = load_crosswalks()
    both = sides(
        crosswalk,
        load_fhfa_data(POST_AVAILABLE_YEARS),
        load_hmda_data(crosswalk),
        _post_fhfa_key(crosswalk),
    )
    _print_headline(both, crosswalk, f"years {POST_AVAILABLE_YEARS[0]}-{POST_AVAILABLE_YEARS[-1]}")

    print_section("1. Match Round Breakdown")
    round_breakdown = match_round_breakdown(crosswalk)
    print(round_breakdown)

    print_section("2. Match Rates by Year (FHFA acquisition year / HMDA GSE-sold activity year)")
    yearly = yearly_rates(both)
    print(yearly)
    overall_fhfa_rate = overall_rate(yearly, "fhfa_matched", "fhfa_total")
    overall_hmda_gse_rate = overall_rate(yearly, "hmda_gse_matched", "hmda_gse_total")
    print(f"\nOverall FHFA match rate: {overall_fhfa_rate:.1%}")
    print(f"Overall HMDA GSE match rate (PT 1,3 only): {overall_hmda_gse_rate:.1%}")

    print_section("3. HMDA Match Rates by Year (All Purchaser Types)")
    hmda_yearly = hmda_yearly_rates(both)
    print(hmda_yearly)
    overall_hmda_rate = overall_rate(hmda_yearly, "hmda_matched", "hmda_total")
    print(f"\nOverall HMDA match rate (all PT): {overall_hmda_rate:.1%}")
    print("Note: denominator includes PT=0 (not sold) and PT>=5 (non-GSE secondary)")

    print_section("4. Match Rates by Enterprise")
    enterprise = enterprise_rates(both)
    print(enterprise)

    print_section("5. FHFA Match Rates by State (Top 20 by Volume)")
    states = state_rates(both)
    print(states.head(20))
    print("\nLowest match rate states (min 1000 loans):")
    print(states.filter(pl.col("fhfa_total") >= 1000).sort("fhfa_match_rate").head(10))
    state_corr = compute_state_size_correlation(states)
    if state_corr:
        print(
            f"\nState size vs match rate: Spearman r = {state_corr['spearman_r']:.3f} (p={state_corr['spearman_p']:.4f}, n={state_corr['n_states']} states)"
        )

    print_section("6. Interpretation & Key Findings")
    print(f"  Overall FHFA match rate: {overall_fhfa_rate:.1%}")
    print(f"  Overall HMDA match rate (all PT): {overall_hmda_rate:.1%}")
    print(f"  Overall HMDA GSE match rate (PT 1,3 only): {overall_hmda_gse_rate:.1%}")
    for row in round_breakdown.iter_rows(named=True):
        print(f"  Round {row['match_round']}: {row['count']:,} ({row['pct']:.1%})")
    for row in enterprise.iter_rows(named=True):
        print(
            f"  {row['enterprise_name']}: FHFA {row['fhfa_match_rate']:.1%}, HMDA GSE {row['hmda_gse_match_rate']:.1%}"
        )

    results: dict[str, Any] = {
        "round_breakdown": round_breakdown,
        "yearly_rates": yearly,
        "hmda_yearly": hmda_yearly,
        "enterprise_rates": enterprise,
        "state_rates": states,
        "state_corr": state_corr,
        "overall_fhfa_rate": overall_fhfa_rate,
        "overall_hmda_rate": overall_hmda_rate,
        "overall_hmda_gse_rate": overall_hmda_gse_rate,
    }

    if (show_plots or save_plots) and HAS_MATPLOTLIB:
        print_section("7. Visualizations")
        print("Computing match rates by loan amount and interest rate...")
        results["loan_amount_rates"] = loan_amount_rates(both, "note_amount")
        results["interest_rate_rates"] = interest_rate_rates(both)
        print("Rendering the figures...")
        plot_validation_panel(
            results,
            output_path=output_dir / "hmda_fhfa_validation.png" if save_plots else None,
            interactive=show_plots,
        )
        plot_state_match_rate_map(
            states,
            "FHFA Match Rate by State",
            output_path=output_dir / "hmda_fhfa_state_match_rate_map.png" if save_plots else None,
        )
    return results


# =============================================================================
# PRE-2018
# =============================================================================


def load_crosswalk_pre2018(crosswalk_path: Path | str | None = None) -> pl.LazyFrame:
    """Load the combined crosswalk produced by HMDAFHFAPre2018Matcher.

    Looks in (in order):
    1. The explicit `crosswalk_path` argument.
    2. ``HMDA_FHFA_CROSSWALK_DIR/hmda_fhfa_crosswalk_pre2018.parquet`` (the
       published location).
    3. The workflow's working files under ``HMDA_FHFA_MATCHING_DIR/pre2018_silver``,
       most complete round first.

    Args:
        crosswalk_path: Path of the crosswalk to load; defaults to the configured output.

    Returns:
        The combined pre-2018 crosswalk.
    """
    candidates: list[Path] = []
    if crosswalk_path is not None:
        candidates.append(Path(crosswalk_path))
    candidates.append(HMDAFHFAMatchingConfig.get_crosswalk_path("pre2018"))
    for n in (3, 2):
        candidates.append(
            HMDAFHFAMatchingConfig.HMDA_FHFA_MATCHING_DIR
            / "pre2018_silver"
            / f"hmda_fhfa_round{n}_pre2018.parquet"
        )

    for candidate in candidates:
        if candidate.exists():
            return pl.scan_parquet(candidate)

    raise FileNotFoundError(
        "Could not locate the pre-2018 crosswalk. Expected one of:\n  "
        + "\n  ".join(str(c) for c in candidates)
    )


def load_fhfa_silver_pre2018(years: list[int] | None = None) -> pl.LazyFrame:
    """Load FHFA sf_c silver for pre-2018 with the columns validation needs.

    Goes directly to silver (not through the workflow's cleaned parquet)
    so we can pull fields the workflow drops, like ``tract_income_ratio``
    and ``state_code``.

    Args:
        years: Activity years to include.

    Returns:
        FHFA sf_c silver on the columns validation needs.
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
        "area_median_family_income",
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


def load_hmda_silver_pre2018(years: list[int] | None = None) -> pl.LazyFrame:
    """Load HMDA pre-2018 silver matched-cohort fields.

    Mirrors the workflow's filtering (action_taken=1, drop pt={2,4}) and assigns
    the same ``HMDAIndex`` the matcher uses via
    `assign_pre2018_hmda_index` — persisted for 2017, synthesized from the
    unique triple for 2007-2016. Because both sides derive the key identically,
    validation joins line up with the crosswalk regardless of row order (this
    replaces the former, fragile ``with_row_index`` reconstruction).

    Args:
        years: Activity years to include.

    Returns:
        HMDA pre-2018 silver on the matched-cohort fields.
    """
    if years is None:
        years = PRE_AVAILABLE_YEARS

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
        try:
            lf = scan_hmda("period_2007_2017", y, file_type="d")
        except FileNotFoundError:
            continue
        avail = lf.collect_schema().names()
        lf = lf.filter(
            (pl.col("action_taken") == 1) & ~pl.col("purchaser_type").is_in([2, 4])
        ).with_columns(
            pl.when(pl.col("co_applicant_race_1") == 8)
            .then(1)
            .otherwise(2)
            .alias("hmda_num_borrowers")
        )
        lf = assign_pre2018_hmda_index(lf)
        lf = lf.select([c for c in needed if c in avail] + ["hmda_num_borrowers", "HMDAIndex"])
        frames.append(lf)

    if not frames:
        raise FileNotFoundError("No HMDA silver partitions found for pre-2018 validation")

    combined = pl.concat(frames, how="diagonal_relaxed").collect(engine="streaming")
    return combined.lazy()


def compute_match_rates_by_round_and_year_pre2018(
    crosswalk: pl.LazyFrame, fhfa: pl.LazyFrame
) -> pl.DataFrame:
    """Round contribution per acquisition year (for the stacked-bar panel).

    Args:
        crosswalk: The match crosswalk.
        fhfa: The FHFA side's loan universe.

    Returns:
        One row per (acquisition year, round), with its pair count and share of FHFA loans.
    """
    fhfa_totals = fhfa.group_by("year").agg(pl.len().alias("total_fhfa_loans"))
    return (
        crosswalk.group_by(["year", "match_round"])
        .agg(pl.len().alias("matches"))
        .join(fhfa_totals, on="year", how="left")
        .with_columns((pl.col("matches") / pl.col("total_fhfa_loans")).alias("rate_of_fhfa"))
        .sort(["year", "match_round"])
        .collect()
    )


def compute_match_rates_by_tract_income_ratio_pre2018(
    sides: dict[str, Side], bin_size: float = 0.1
) -> pl.DataFrame:
    """FHFA-side match rates by tract income ratio (tract median / area AMI).

    Args:
        sides: From `sides`.
        bin_size: Bin width (10pp by default).

    Returns:
        One row per ratio bin.
    """
    fhfa = sides["fhfa"].prepared(
        lambda lf: lf.filter(
            pl.col("tract_income_ratio").is_not_null()
            & pl.col("tract_income_ratio").is_between(0, 5.0, closed="none")
        ).with_columns(binned("tract_income_ratio", bin_size, name="ratio_bin"))
    )
    return rate_by(
        fhfa.universe,
        fhfa.matched,
        fhfa.on,
        "ratio_bin",
        prefix=fhfa.prefix,
        sort_by="ratio_bin",
        descending=False,
    ).collect()


def compute_demographic_agreement_vs_random_pre2018(
    crosswalk: pl.LazyFrame, fhfa: pl.LazyFrame, hmda: pl.LazyFrame
) -> pl.DataFrame:
    """Per-round demographic agreement and random-pair baseline.

    For race1, sex, ethnicity, num_borrowers: compute the share of
    matched pairs that agree on that variable, vs the random-pair
    expectation (sum of squared marginal probabilities). Lift = agree
    minus baseline; ratio = agree / baseline.

    Args:
        crosswalk: The match crosswalk.
        fhfa: The FHFA side's loan universe.
        hmda: The HMDA side's loan universe.

    Returns:
        Per-round demographic agreement beside the random-pair baseline.
    """
    pairs_lf = (
        crosswalk.select(
            [
                "match_round",
                pl.col("year"),
                pl.col("Enterprise Flag").alias("enterprise_flag"),
                pl.col("Record Number").alias("record_number"),
                "HMDAIndex",
            ]
        )
        .join(
            fhfa.select(
                [
                    "year",
                    "enterprise_flag",
                    "record_number",
                    pl.col("borrower_race_1").alias("fhfa_race"),
                    pl.col("borrower_sex").alias("fhfa_sex"),
                    pl.col("borrower_ethnicity").alias("fhfa_ethnicity"),
                    pl.col("number_of_borrowers").alias("fhfa_num_borrowers"),
                ]
            ),
            on=["year", "enterprise_flag", "record_number"],
            how="inner",
        )
        .join(
            hmda.select(
                [
                    "HMDAIndex",
                    pl.col("applicant_race_1").alias("hmda_race"),
                    pl.col("applicant_sex").alias("hmda_sex"),
                    pl.col("applicant_ethnicity").alias("hmda_ethnicity"),
                    "hmda_num_borrowers",
                ]
            ),
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
        h_dist = (
            h.value_counts(sort=False)
            .rename({hcol: "v"})
            .with_columns(pl.col("count") / len(h))
            .rename({"count": "h_p"})
        )
        f_dist = (
            f.value_counts(sort=False)
            .rename({fcol: "v"})
            .with_columns(pl.col("count") / len(f))
            .rename({"count": "f_p"})
        )
        joint = h_dist.join(f_dist, on="v", how="inner")
        return float((joint["h_p"] * joint["f_p"]).sum()) if len(joint) else float("nan")

    rows = []
    variables = [
        ("race1", "hmda_race", "fhfa_race", "applicant_race_1", "borrower_race_1"),
        ("sex", "hmda_sex", "fhfa_sex", "applicant_sex", "borrower_sex"),
        (
            "ethnicity",
            "hmda_ethnicity",
            "fhfa_ethnicity",
            "applicant_ethnicity",
            "borrower_ethnicity",
        ),
        (
            "num_borrowers",
            "hmda_num_borrowers",
            "fhfa_num_borrowers",
            "hmda_num_borrowers",
            "number_of_borrowers",
        ),
    ]
    rounds_present = sorted(pairs["match_round"].unique().to_list())
    for label, h_pair_col, f_pair_col, h_pop_col, f_pop_col in variables:
        baseline = random_baseline(h_pop_col, f_pop_col)
        # Overall + per-round
        for tag, sub in [("overall", pairs)] + [
            (f"R{r}", pairs.filter(pl.col("match_round") == r)) for r in rounds_present
        ]:
            usable = sub.select([h_pair_col, f_pair_col]).drop_nulls()
            n = len(usable)
            if n == 0:
                rows.append(
                    {
                        "variable": label,
                        "scope": tag,
                        "n_pairs": 0,
                        "agree_pct": None,
                        "random_pct": baseline * 100,
                        "lift_pp": None,
                        "ratio": None,
                    }
                )
                continue
            agree = float((usable[h_pair_col] == usable[f_pair_col]).sum()) / n
            rows.append(
                {
                    "variable": label,
                    "scope": tag,
                    "n_pairs": n,
                    "agree_pct": agree * 100,
                    "random_pct": baseline * 100,
                    "lift_pp": (agree - baseline) * 100,
                    "ratio": agree / baseline if baseline else float("nan"),
                }
            )
    return pl.DataFrame(rows)


def compute_amount_diff_distribution_pre2018(
    crosswalk: pl.LazyFrame, fhfa: pl.LazyFrame, hmda: pl.LazyFrame
) -> pl.DataFrame:
    """Distribution of HMDA - FHFA loan-amount difference per round.

    Confirms the asymmetric `0 ≤ HMDA - FHFA ≤ max($2k, 1% × HMDA)`
    tolerance is binding rather than letting noise through.

    Args:
        crosswalk: The match crosswalk.
        fhfa: The FHFA side's loan universe.
        hmda: The HMDA side's loan universe.

    Returns:
        The HMDA-minus-FHFA amount-difference distribution, per round.
    """
    pairs = (
        crosswalk.select(
            [
                "match_round",
                "year",
                pl.col("Enterprise Flag").alias("enterprise_flag"),
                pl.col("Record Number").alias("record_number"),
                "HMDAIndex",
            ]
        )
        .join(
            fhfa.select(["year", "enterprise_flag", "record_number", "upb_acquisition"]),
            on=["year", "enterprise_flag", "record_number"],
            how="inner",
        )
        .join(
            hmda.select(["HMDAIndex", "loan_amount"]),
            on="HMDAIndex",
            how="inner",
        )
        .with_columns((pl.col("loan_amount") - pl.col("upb_acquisition")).alias("amt_diff"))
    ).collect()

    rounds_present = sorted(pairs["match_round"].unique().to_list())
    rows = []
    for label, sub in [("overall", pairs)] + [
        (f"R{r}", pairs.filter(pl.col("match_round") == r)) for r in rounds_present
    ]:
        if len(sub) == 0:
            continue
        d = sub["amt_diff"]
        rows.append(
            {
                "scope": label,
                "n_pairs": len(sub),
                "median": scalar_int(d.median()),
                "q01": scalar_int(d.quantile(0.01)),
                "q05": scalar_int(d.quantile(0.05)),
                "q95": scalar_int(d.quantile(0.95)),
                "q99": scalar_int(d.quantile(0.99)),
                "share_eq0_pct": (d == 0).sum() / len(sub) * 100,
                "share_le1k_pct": (d.abs() <= 1000).sum() / len(sub) * 100,
                "share_le2k_pct": (d.abs() <= 2000).sum() / len(sub) * 100,
            }
        )
    return pl.DataFrame(rows)


def compute_income_agreement_per_round_pre2018(
    crosswalk: pl.LazyFrame, fhfa: pl.LazyFrame, hmda: pl.LazyFrame
) -> pl.DataFrame:
    """Per-round income agreement (raw and AMFI-deflated for R2).

    Args:
        crosswalk: The match crosswalk.
        fhfa: The FHFA side's loan universe.
        hmda: The HMDA side's loan universe.

    Returns:
        Per-round income agreement, raw and AMFI-deflated.
    """
    fhfa_with_amfi = fhfa.select(
        [
            "year",
            "enterprise_flag",
            "record_number",
            pl.col("borrower_annual_income").alias("fhfa_inc"),
            pl.col("area_median_family_income").alias("amfi_acq"),
            "msa_code",
        ]
    )
    pairs = (
        crosswalk.select(
            [
                "match_round",
                "year",
                pl.col("Enterprise Flag").alias("enterprise_flag"),
                pl.col("Record Number").alias("record_number"),
                "HMDAIndex",
                "activity_year",
            ]
        )
        .join(fhfa_with_amfi, on=["year", "enterprise_flag", "record_number"], how="inner")
        .join(
            hmda.select(["HMDAIndex", "income"]).rename({"income": "hmda_inc"}),
            on="HMDAIndex",
            how="inner",
        )
    ).collect()

    # Build (msa, year) → AMFI for the orig year of each pair (needed
    # for R2 deflation comparison).
    panel = (
        fhfa.filter(
            pl.col("area_median_family_income").is_not_null()
            & (pl.col("area_median_family_income") < PRE_FHFA_TOPCODE)
            & (pl.col("area_median_family_income") > 0)
        )
        .group_by(["msa_code", "year"])
        .agg(pl.col("area_median_family_income").mode().first().alias("amfi"))
        .collect()
    )

    pairs = pairs.join(
        panel.rename({"year": "activity_year", "amfi": "amfi_orig"}),
        on=["msa_code", "activity_year"],
        how="left",
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
                (
                    (
                        pl.col("fhfa_inc").cast(pl.Float64)
                        * pl.col("amfi_orig").cast(pl.Float64)
                        / pl.col("amfi_acq").cast(pl.Float64)
                        / 1000.0
                    ).round(0)
                    * 1000
                )
                .cast(pl.Int64)
                .alias("fhfa_deflated")
            )
            ddiff = (d["fhfa_deflated"] - d["hmda_inc"].cast(pl.Int64)).abs()
            row["defl_eq0_pct"] = (ddiff == 0).sum() / len(d) * 100
            row["defl_le1k_pct"] = (ddiff <= 1000).sum() / len(d) * 100
            row["defl_le2k_pct"] = (ddiff <= 2000).sum() / len(d) * 100
        rows.append(row)
    return pl.DataFrame(rows)


def _pre_round2_income_pairs(
    crosswalk: pl.LazyFrame, fhfa: pl.LazyFrame, hmda: pl.LazyFrame
) -> pl.DataFrame:
    """Round-2 pairs with both incomes and both AMFIs (acquisition and origination year)."""
    pairs = (
        crosswalk.filter(pl.col("match_round") == 2)
        .select(
            "year",
            pl.col("Enterprise Flag").alias("enterprise_flag"),
            pl.col("Record Number").alias("record_number"),
            "HMDAIndex",
            "activity_year",
        )
        .join(
            fhfa.select(
                "year",
                "enterprise_flag",
                "record_number",
                pl.col("borrower_annual_income").alias("fhfa_inc"),
                pl.col("area_median_family_income").alias("amfi_acq"),
                "msa_code",
            ),
            on=FHFA_KEY,
            how="inner",
        )
        .join(
            hmda.select("HMDAIndex", pl.col("income").alias("hmda_inc")),
            on="HMDAIndex",
            how="inner",
        )
    ).collect()
    panel = (
        fhfa.filter(
            pl.col("area_median_family_income").is_not_null()
            & (pl.col("area_median_family_income") < PRE_FHFA_TOPCODE)
            & (pl.col("area_median_family_income") > 0)
        )
        .group_by(["msa_code", "year"])
        .agg(pl.col("area_median_family_income").mode().first().alias("amfi"))
        .collect()
    )
    return pairs.join(
        panel.rename({"year": "activity_year", "amfi": "amfi_orig"}),
        on=["msa_code", "activity_year"],
        how="left",
    ).filter(
        pl.col("hmda_inc").is_not_null()
        & (pl.col("hmda_inc") > 0)
        & pl.col("amfi_orig").is_not_null()
        & pl.col("amfi_acq").is_not_null()
        & (pl.col("amfi_acq") > 0)
    )


def compute_amfi_inflation_diagnostic_pre2018(
    crosswalk: pl.LazyFrame, fhfa: pl.LazyFrame, hmda: pl.LazyFrame
) -> pl.DataFrame:
    """Round 2: per-pair implied versus expected AMFI inflation ratio.

    For round-2 matches (the one-year-lag prior-year cohort), the implied
    ``FHFA_inc / HMDA_inc`` should track the expected MSA-level ``AMFI_acq /
    AMFI_orig`` if FHFA's documented inflation procedure is what the round reverses.

    Args:
        crosswalk: The match crosswalk.
        fhfa: The FHFA side's loan universe.
        hmda: The HMDA side's loan universe.

    Returns:
        The distribution of ``implied - expected``.
    """
    pairs = _pre_round2_income_pairs(crosswalk, fhfa, hmda)
    if len(pairs) == 0:
        return pl.DataFrame({"n_pairs": [0]})

    pairs = pairs.with_columns(
        (pl.col("fhfa_inc").cast(pl.Float64) / pl.col("hmda_inc").cast(pl.Float64)).alias(
            "implied_ratio"
        ),
        (pl.col("amfi_acq").cast(pl.Float64) / pl.col("amfi_orig").cast(pl.Float64)).alias(
            "expected_ratio"
        ),
    ).with_columns((pl.col("implied_ratio") - pl.col("expected_ratio")).alias("residual"))

    return pl.DataFrame(
        [
            {
                "n_pairs": len(pairs),
                "median_implied": scalar_float(pairs["implied_ratio"].median()),
                "median_expected": scalar_float(pairs["expected_ratio"].median()),
                "median_residual": scalar_float(pairs["residual"].median()),
                "mean_abs_residual": scalar_float(pairs["residual"].abs().mean()),
                "share_within_1pp_pct": (pairs["residual"].abs() <= 0.01).sum() / len(pairs) * 100,
                "share_within_2pp_pct": (pairs["residual"].abs() <= 0.02).sum() / len(pairs) * 100,
                "share_within_5pp_pct": (pairs["residual"].abs() <= 0.05).sum() / len(pairs) * 100,
            }
        ]
    )


def compute_amfi_residuals_pre2018(
    crosswalk: pl.LazyFrame, fhfa: pl.LazyFrame, hmda: pl.LazyFrame
) -> np.ndarray:
    """Per-pair (implied - expected) income-inflation residual for round-2 matches.

    Args:
        crosswalk: The match crosswalk.
        fhfa: The FHFA side's loan universe.
        hmda: The HMDA side's loan universe.

    Returns:
        The residuals, clipped to +/-0.5 for charting. Empty if no pair qualifies.
    """
    pairs = _pre_round2_income_pairs(crosswalk, fhfa, hmda)
    if len(pairs) == 0:
        return np.array([])

    residual = pairs.with_columns(
        (
            pl.col("fhfa_inc").cast(pl.Float64) / pl.col("hmda_inc").cast(pl.Float64)
            - pl.col("amfi_acq").cast(pl.Float64) / pl.col("amfi_orig").cast(pl.Float64)
        ).alias("residual")
    )["residual"].to_numpy()
    return residual[(residual > -0.5) & (residual < 0.5)]


def _panel_pre_round_contribution(ax: Any, round_year: pl.DataFrame, yearly: pl.DataFrame) -> None:
    """Stacked share of FHFA loans matched, split by the round that caught them."""
    years = sorted(round_year["year"].unique().to_list())
    if not years:
        ax.set_axis_off()
        return

    bottom = np.zeros(len(years))
    for match_round in sorted(round_year["match_round"].unique().to_list()):
        by_year = dict(
            round_year.filter(pl.col("match_round") == match_round)
            .select("year", "rate_of_fhfa")
            .iter_rows()
        )
        values = np.array([float(by_year.get(y, 0.0)) for y in years])
        ax.bar(
            years,
            values,
            bottom=bottom,
            label=f"Round {match_round}",
            color=PRE_ROUND_COLORS.get(match_round, MATCHED_COLOR),
            edgecolor="white",
            linewidth=0.5,
        )
        bottom += values

    totals_by_year = dict(yearly.select("year", "fhfa_match_rate").iter_rows())
    totals = [float(totals_by_year.get(y) or 0.0) for y in years]
    ax.plot(
        years,
        totals,
        color=MATCHED_COLOR,
        marker="o",
        linestyle="",
        markersize=5,
        label="Total",
    )

    ax.set_xlabel("Acquisition year")
    ax.set_ylabel("Match rate (share of FHFA loans)")
    ax.set_title("Round contribution by acquisition year")
    ax.set_ylim(0, max(totals) * 1.15 if totals else 1)
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(True, alpha=0.3, axis="y")


def _panel_pre_demographic_agreement(ax: Any, agreement: pl.DataFrame) -> None:
    """Matched-pair agreement on demographics, against a random-pair baseline.

    Demographics are not used in matching, so the gap between the two bars is the
    evidence that pairs are real rather than coincidental.
    """
    overall = agreement.filter(pl.col("scope") == "overall")
    if len(overall) == 0:
        ax.set_axis_off()
        return

    variables = overall["variable"].to_list()
    agree = overall["agree_pct"].to_list()
    base = overall["random_pct"].to_list()
    y = np.arange(len(variables))

    ax.barh(y - 0.2, agree, height=0.4, color=MATCHED_COLOR, label="Matched-pair agreement")
    ax.barh(y + 0.2, base, height=0.4, color=DENSITY_COLOR, label="Random-pair baseline")
    for i, (matched, random_baseline) in enumerate(zip(agree, base)):
        if matched is not None:
            ax.text(matched + 1, i - 0.2, f"{matched:.1f}%", va="center", fontsize=8)
        if random_baseline is not None:
            ax.text(
                random_baseline + 1, i + 0.2, f"{random_baseline:.1f}%", va="center", fontsize=8
            )

    ax.set_yticks(y)
    ax.set_yticklabels(variables, fontsize=9)
    ax.set_xlabel("Agreement (%)")
    ax.set_xlim(0, 112)
    ax.set_title("Demographic agreement: matches vs random baseline")
    # Upper right: the matched bars run to ~99%, so a lower-right legend covers one.
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3, axis="x")


def _panel_pre_amfi_diagnostic(ax: Any, residuals: np.ndarray) -> None:
    """Distribution of the round-2 income-inflation residual.

    Charted over +/-15pp rather than the wider window the residuals are computed on:
    the mass sits inside a couple of points of zero, which is the whole finding, and
    a wider axis renders it as one indistinguishable spike.
    """
    residuals = residuals[(residuals > -AMFI_CHART_WINDOW) & (residuals < AMFI_CHART_WINDOW)]
    if len(residuals) == 0:
        ax.set_axis_off()
        return

    # Burnt orange accent (intentionally outside PRE_ROUND_COLORS so it
    # contrasts visibly with the dashed Midnight Blue zero line below).
    ax.hist(
        residuals, bins=80, color=COLORS["terracotta"], alpha=0.85, edgecolor="white", linewidth=0.4
    )
    ax.axvline(0, color=MATCHED_COLOR, linestyle="--", linewidth=1)
    ax.axvspan(-0.02, 0.02, color=MATCHED_COLOR, alpha=0.08, label="+/-2pp band")
    ax.set_xlim(-AMFI_CHART_WINDOW, AMFI_CHART_WINDOW)
    ax.set_xlabel("Implied (FHFA/HMDA) - Expected (AMFI_acq/AMFI_orig)")
    ax.set_ylabel("R2 matched pairs")
    ax.set_title("R2 AMFI-inflation diagnostic")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3, axis="y")


def plot_validation_panel_pre2018(
    results: dict[str, Any], output_path: Path | None = None, interactive: bool = False
) -> None:
    """Render the pre-2018 validation figure.

    Rows: match rates by year (full width); round contribution | loan amount;
    demographic agreement | the round-2 AMFI-inflation diagnostic.

    Args:
        results: The accumulated ``run_validation_pre2018`` results.
        output_path: Where to save the figure (PNG). Skipped if ``None``.
        interactive: Whether to display the figure interactively.
    """
    plt = get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return
    fig = plt.figure(figsize=(16, PANEL_HEIGHT_IN * 3 + 1.5))
    grid = panel_gridspec(
        fig,
        3,
        2,
        title=f"HMDA-FHFA match validation, pre-2018 ({PRE_AVAILABLE_YEARS[0]}-{PRE_AVAILABLE_YEARS[-1]}, GSE loans)",
        title_fontsize=14,
        # The demographic panel's category names are drawn into the left margin.
        margin_in=1.1,
    )
    _panel_yearly_rates(fig.add_subplot(grid[0, :]), results["yearly_rates"], "Match rates by year")
    _panel_pre_round_contribution(
        fig.add_subplot(grid[1, 0]), results["round_year"], results["yearly_rates"]
    )
    _panel_binned(
        fig.add_subplot(grid[1, 1]),
        results.get("loan_amount_rates"),
        "loan_amount_bin",
        "Loan Amount ($K)",
    )
    _panel_pre_demographic_agreement(fig.add_subplot(grid[2, 0]), results["demographic_agreement"])
    _panel_pre_amfi_diagnostic(
        fig.add_subplot(grid[2, 1]), results.get("amfi_residuals", np.array([]))
    )
    save_and_close(fig, output_path, interactive)


def run_validation_pre2018(
    crosswalk_path: Path | str | None = None,
    output_dir: Path | None = None,
    save_plots: bool = True,
    show_plots: bool = False,
) -> dict[str, Any]:
    """Run every pre-2018 validation analysis, print the report, and draw the figures.

    Args:
        crosswalk_path: Explicit combined crosswalk; None looks in the conventional
            locations (see `load_crosswalk_pre2018`).
        output_dir: Directory for the figures. Defaults to ``VALIDATION_OUTPUT_DIR``.
        save_plots: Whether to write the figures.
        show_plots: Whether to display the matplotlib figure interactively.

    Returns:
        Every computed frame and statistic, keyed by name.
    """
    output_dir = output_dir or VALIDATION_OUTPUT_DIR
    if save_plots:
        output_dir.mkdir(parents=True, exist_ok=True)

    print("Loading data...")
    crosswalk = load_crosswalk_pre2018(crosswalk_path)
    fhfa, hmda = load_fhfa_silver_pre2018(), load_hmda_silver_pre2018()
    both = sides(crosswalk, fhfa, hmda, PRE_FHFA_KEY_IN_CROSSWALK)
    _print_headline(both, crosswalk, f"years {PRE_AVAILABLE_YEARS[0]}-{PRE_AVAILABLE_YEARS[-1]}")

    print_section("1. Match round breakdown")
    round_breakdown = match_round_breakdown(crosswalk)
    print(round_breakdown)

    print_section("2. Match rates by year (FHFA acquisition year / HMDA GSE-sold activity year)")
    yearly = yearly_rates(both)
    print(yearly)
    overall_fhfa = overall_rate(yearly, "fhfa_matched", "fhfa_total")
    overall_hmda = overall_rate(yearly, "hmda_gse_matched", "hmda_gse_total")
    print(f"\nOverall FHFA match rate: {overall_fhfa:.1%}")
    print(f"Overall HMDA GSE match rate: {overall_hmda:.1%}")

    print_section("3. Match rates by enterprise")
    enterprise = enterprise_rates(both)
    print(enterprise)

    print_section("4. FHFA match rates by state (top 20 by volume)")
    states = state_rates(both)
    print(states.head(20))

    print_section("5. Round contribution per year")
    round_year = compute_match_rates_by_round_and_year_pre2018(crosswalk, fhfa)
    print(round_year)

    print_section("6. Demographic agreement vs random baseline")
    agreement = compute_demographic_agreement_vs_random_pre2018(crosswalk, fhfa, hmda)
    print(agreement)

    print_section("7. Loan-amount diff distribution per round")
    amt_diff = compute_amount_diff_distribution_pre2018(crosswalk, fhfa, hmda)
    print(amt_diff)

    print_section("8. Income agreement per round (raw vs AMFI-deflated)")
    income_agree = compute_income_agreement_per_round_pre2018(crosswalk, fhfa, hmda)
    print(income_agree)

    print_section("9. R2 AMFI-inflation diagnostic")
    amfi_diag = compute_amfi_inflation_diagnostic_pre2018(crosswalk, fhfa, hmda)
    print(amfi_diag)

    print_section("10. Match rates by tract income ratio")
    tract_rates = compute_match_rates_by_tract_income_ratio_pre2018(both)
    print(tract_rates)

    results: dict[str, Any] = {
        "round_breakdown": round_breakdown,
        "yearly_rates": yearly,
        "enterprise_rates": enterprise,
        "state_rates": states,
        "round_year": round_year,
        "demographic_agreement": agreement,
        "amount_diff": amt_diff,
        "income_agreement": income_agree,
        "amfi_diagnostic": amfi_diag,
        "tract_income_rates": tract_rates,
        "loan_amount_rates": loan_amount_rates(both, "upb_acquisition"),
        "overall_fhfa_rate": overall_fhfa,
        "overall_hmda_gse_rate": overall_hmda,
    }

    if (show_plots or save_plots) and HAS_MATPLOTLIB:
        print_section("11. Visualizations")
        results["amfi_residuals"] = compute_amfi_residuals_pre2018(crosswalk, fhfa, hmda)
        print("Rendering the figures...")
        plot_validation_panel_pre2018(
            results,
            output_path=output_dir / "hmda_fhfa_pre2018_validation.png" if save_plots else None,
            interactive=show_plots,
        )
        plot_state_match_rate_map(
            states,
            "Pre-2018 FHFA Match Rate by State",
            output_path=output_dir / "hmda_fhfa_pre2018_state_match_rate_map.png"
            if save_plots
            else None,
        )
    return results
