"""Validation report for the FHLB-GNMA crosswalk (the FHLB-MBS workflow).

Links GNMA government-backed loans (FHA/VA/RD) to FHLB AMA acquisitions. Because
the two sources share exact loan terms, match rates are high and matched pairs agree
tightly on the fields the matcher did not key on exactly; the match-quality section
documents that agreement.

Sections: headline statistics, match quality (difference distributions), temporal,
geographic, loan characteristics (amount, rate, LTV, mortgage type), unmatched loans.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple

import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.matching.match_fhlb_mbs.config import FHLBMBSMatchingConfig as Config
from mortgage_data_manager.matching.validation_common import (
    COLORS,
    HAS_MATPLOTLIB,
    NON_STATE_CODES,
    RateSeries,
    Side,
    StatePanel,
    TemporalSeries,
    binned,
    get_pyplot,
    matched_count,
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

MIN_YEAR = 2018
MAX_YEAR = 2025
DEFAULT_GNMA_ISSUER_ID = 3937

FIGURES_DIR = MortgageDataConfig.PROJECT_DIR / "docs" / "matching" / "figures"

GNMA_COLOR = COLORS["hunter"]
FHLB_COLOR = COLORS["gold"]
QUALITY_COLOR = COLORS["terracotta"]

MORTGAGE_TYPE_LABELS = {1: "FHA", 2: "VA", 3: "RD"}

# The two sides of every rate panel in this workflow.
RATE_SERIES = (
    RateSeries("gnma_match_rate", "gnma_total", GNMA_COLOR, "GNMA", "o"),
    RateSeries("fhlb_match_rate", "fhlb_total", FHLB_COLOR, "FHLB", "s"),
)

# Vertical inches allotted to one grid row; the canvas is sized from the row count.
PANEL_HEIGHT_IN = 4.5


class QualityMetric(NamedTuple):
    """One match-quality difference: how it is keyed, drawn, and described.

    Attributes:
        column: Difference column in the match-detail frame.
        stat_key: Key in the quality statistics holding the share inside the threshold.
        threshold: The tight-match threshold, drawn as a reference line.
        threshold_label: How that threshold reads in the legend.
        xlabel: Axis label.
        title: Panel title, before the share is appended.
        x_cap: Upper bound on the x axis; the tails are long and uninformative.
    """

    column: str
    stat_key: str
    threshold: float
    threshold_label: str
    xlabel: str
    title: str
    x_cap: float


QUALITY_METRICS = (
    QualityMetric(
        "rate_diff",
        "tight_rate_pct",
        0.01,
        "0.01%",
        "Interest Rate Difference (%)",
        "Interest rate match quality",
        0.15,
    ),
    QualityMetric(
        "balance_diff",
        "tight_balance_pct",
        1000,
        "$1,000",
        "Balance Difference ($)",
        "Balance match quality",
        2500,
    ),
    QualityMetric(
        "ltv_diff", "tight_ltv_pct", 0.01, "0.01%", "LTV Difference (%)", "LTV match quality", 1.2
    ),
    QualityMetric(
        "dti_diff", "tight_dti_pct", 0.01, "0.01%", "DTI Difference (%)", "DTI match quality", 1.2
    ),
)

# The binned dimensions, in figure order: results key, bin column, axis label,
# panel noun, and the multiplier applied to the bin values before plotting.
CONTINUOUS_DIMENSIONS = (
    ("loan_amount_rates", "loan_amount_bin", "Loan Amount ($K)", "loan amount", 0.001),
    ("interest_rate_rates", "interest_rate_bin", "Interest Rate (%)", "interest rate", 1.0),
    ("ltv_rates", "ltv_bin", "LTV (%)", "LTV", 1.0),
)


# =============================================================================
# Data
# =============================================================================


def get_data_paths() -> tuple[Path, Path]:
    """The crosswalk path and the match-details path."""
    return (
        Config.FHLB_MBS_CROSSWALK_DIR / "fhlb_mbs_crosswalk_gnma.parquet",
        Config.FHLB_MBS_INTERMEDIATE_DIR / "fhlb_mbs_match_details_gnma.parquet",
    )


def load_match_data(
    crosswalk_path: Path | None = None, details_path: Path | None = None
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Load the FHLB-GNMA crosswalk and the per-pair match details.

    Args:
        crosswalk_path: Crosswalk parquet; defaults to the production location.
        details_path: Match-details parquet; defaults to the production location.

    Returns:
        ``(crosswalk, details)``.

    Raises:
        FileNotFoundError: If either file is missing (the matcher has not been run).
    """
    default_crosswalk, default_details = get_data_paths()
    crosswalk_path = crosswalk_path or default_crosswalk
    details_path = details_path or default_details
    for path, what in ((crosswalk_path, "Crosswalk"), (details_path, "Match details")):
        if not path.exists():
            raise FileNotFoundError(
                f"{what} file not found: {path}\n"
                "Run the FHLB-GNMA matching first: mortgage-data match fhlb-mbs run"
            )
    return pl.read_parquet(crosswalk_path), pl.read_parquet(details_path)


def load_source_data(
    min_year: int = MIN_YEAR,
    max_year: int = MAX_YEAR,
    issuer_id: int | None = DEFAULT_GNMA_ISSUER_ID,
    verbose: bool = True,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Load and prepare the two loan universes exactly as the matcher does.

    Args:
        min_year: First year to include.
        max_year: Last year to include.
        issuer_id: GNMA issuer filter; None or 0 keeps every issuer.
        verbose: Print the source directories.

    Returns:
        ``(gnma_prepared, fhlb_prepared)``.
    """
    from mortgage_data_manager.matching.match_fhlb_mbs.matching import (
        load_fhlb,
        load_gnma,
        prepare_fhlb,
        prepare_gnma,
    )

    gnma_dir = Config.get_gnma_silver_dir()
    fhlb_dir = Config.get_fhlb_ama_silver_dir()
    if verbose:
        print(f"Loading GNMA data from: {gnma_dir}")
        print(f"Loading FHLB data from: {fhlb_dir}")
    effective_issuer = issuer_id if issuer_id else None
    gnma = prepare_gnma(load_gnma(gnma_dir, min_year, max_year, effective_issuer))
    fhlb = prepare_fhlb(load_fhlb(fhlb_dir, min_year, max_year))
    return gnma, fhlb


def sides(crosswalk: pl.DataFrame, gnma: pl.DataFrame, fhlb: pl.DataFrame) -> tuple[Side, Side]:
    """The two measured sides, each with its matched-ID set computed once.

    Args:
        crosswalk: The GNMA<->FHLB crosswalk.
        gnma: The GNMA side's loan universe.
        fhlb: The FHLB side's loan universe.

    Returns:
        ``(gnma_side, fhlb_side)``.
    """
    return (
        Side(gnma, matched_ids(crosswalk, "gnma_loan_id"), "gnma_loan_id", "gnma"),
        Side(fhlb, matched_ids(crosswalk, "fhlb_loan_id"), "fhlb_loan_id", "fhlb"),
    )


def _not_null(col: str) -> Any:
    return lambda lf: lf.filter(pl.col(col).is_not_null())


# =============================================================================
# Statistics
# =============================================================================


def compute_match_quality_statistics(details: pl.DataFrame) -> dict[str, Any]:
    """Agreement between matched pairs on rate, balance, LTV and DTI.

    GNMA canonical silver is decoded to real units at build, matching the FHLB
    scale on every field, so the differences need no conversion.

    Args:
        details: The per-pair match details.

    Returns:
        Difference percentiles per field, the share of pairs inside each tight
        threshold, and the details frame with the difference columns added.
    """
    with_diff = details.with_columns(
        (pl.col("interest_rate") - pl.col("interest_rate_fhlb")).abs().alias("rate_diff"),
        (pl.col("original_balance") - pl.col("note_amount")).abs().alias("balance_diff"),
        (pl.col("ltv") - pl.col("ltv_fhlb")).abs().alias("ltv_diff"),
        (pl.col("dti") - pl.col("dti_fhlb")).abs().alias("dti_diff"),
    )
    percentiles = (0.50, 0.90, 0.95, 0.99)

    def quantiles(col: str, label: str) -> dict[str, float]:
        return (
            with_diff.filter(pl.col(col).is_not_null())
            .select(pl.col(col).quantile(p).alias(f"{label}_p{int(p * 100)}") for p in percentiles)
            .row(0, named=True)
        )

    n = len(with_diff)

    def share(predicate: pl.Expr) -> float:
        return float(with_diff.select(predicate.sum()).item()) / n

    return {
        "n_matches": n,
        "rate_stats": quantiles("rate_diff", "rate"),
        "balance_stats": quantiles("balance_diff", "balance"),
        "ltv_stats": quantiles("ltv_diff", "ltv"),
        "dti_stats": quantiles("dti_diff", "dti"),
        "tight_rate_pct": share(pl.col("rate_diff") < 0.01),
        "tight_balance_pct": share(pl.col("balance_diff") <= 1000),
        "tight_ltv_pct": share(pl.col("ltv_diff").is_null() | (pl.col("ltv_diff") < 0.01)),
        "tight_dti_pct": share(pl.col("dti_diff").is_null() | (pl.col("dti_diff") < 0.01)),
        "details_with_diff": with_diff,
    }


def compute_match_summary(
    crosswalk: pl.DataFrame, gnma: pl.DataFrame, fhlb: pl.DataFrame, both: tuple[Side, Side]
) -> dict[str, Any]:
    """Headline counts and the match rate from each side.

    The crosswalk on disk is built over a wider population than the universes loaded
    here (one issuer, ``MIN_YEAR``..``MAX_YEAR``), so only matched IDs inside that
    population count: dividing every matched ID by the narrower denominator would
    report rates above 100%.

    Args:
        crosswalk: The GNMA<->FHLB crosswalk.
        gnma: The GNMA side's loan universe.
        fhlb: The FHLB side's loan universe.
        both: The two sides, from `sides`.

    Returns:
        Totals, matched counts, both rates, and the out-of-scope matched counts.
    """
    n_gnma, n_fhlb = len(gnma), len(fhlb)
    gnma_matched, fhlb_matched = matched_count(both[0]), matched_count(both[1])
    return {
        "total_gnma_loans": n_gnma,
        "total_fhlb_loans": n_fhlb,
        "matched_pairs": len(crosswalk),
        "gnma_match_rate": gnma_matched / n_gnma if n_gnma else 0,
        "fhlb_match_rate": fhlb_matched / n_fhlb if n_fhlb else 0,
        "gnma_matched": gnma_matched,
        "fhlb_matched": fhlb_matched,
        "gnma_out_of_scope": crosswalk["gnma_loan_id"].n_unique() - gnma_matched,
        "fhlb_out_of_scope": crosswalk["fhlb_loan_id"].n_unique() - fhlb_matched,
    }


def yearly_rates(both: tuple[Side, Side]) -> pl.DataFrame:
    """Match rates by year: GNMA origination year against FHLB note year.

    Args:
        both: The two sides, from `sides`.

    Returns:
        One row per year with each side's counts and rate.
    """
    gnma, fhlb = both
    return rate_by_sides(
        [
            gnma.prepared(
                lambda lf: lf.filter(pl.col("origination_year").is_not_null()).rename(
                    {"origination_year": "year"}
                )
            ),
            fhlb.prepared(
                lambda lf: lf.filter(pl.col("note_year").is_not_null()).rename(
                    {"note_year": "year"}
                )
            ),
        ],
        "year",
    )


def state_rates(side: Side, state_col: str) -> pl.DataFrame:
    """One side's match rate by state, largest states first.

    The two sides code geography differently (GNMA postal codes, FHLB FIPS), so state
    rates stay one-sided.

    Args:
        side: The side to measure.
        state_col: Its state column.

    Returns:
        One row per state.
    """
    return rate_by(
        _not_null(state_col)(side.universe.lazy()),
        side.matched,
        side.on,
        state_col,
        prefix=side.prefix,
    ).collect()


def loan_amount_rates(
    both: tuple[Side, Side], bin_size: int = 25_000, max_amount: int = 750_000
) -> pl.DataFrame:
    """Match rates by loan-amount bin (bin midpoints), from both sides.

    Args:
        both: The two sides, from `sides`.
        bin_size: Bin width in dollars.
        max_amount: Upper edge of the last bin.

    Returns:
        One row per bin with each side's counts and rate.
    """
    gnma, fhlb = both

    def prepare(amount_col: str) -> Any:
        return lambda lf: lf.with_columns(
            binned(amount_col, bin_size, mode="midpoint", dtype=pl.Int64, name="loan_amount_bin")
        ).filter((pl.col("loan_amount_bin") > 0) & (pl.col("loan_amount_bin") <= max_amount))

    return rate_by_sides(
        [gnma.prepared(prepare("original_balance")), fhlb.prepared(prepare("note_amount"))],
        "loan_amount_bin",
    )


def interest_rate_rates(
    both: tuple[Side, Side], bin_size: float = 0.25, min_rate: float = 2.0, max_rate: float = 9.0
) -> pl.DataFrame:
    """Match rates by interest-rate bin, from both sides.

    Args:
        both: The two sides, from `sides`.
        bin_size: Bin width in percentage points.
        min_rate: Lower edge of the first bin.
        max_rate: Upper edge of the last bin.

    Returns:
        One row per bin with each side's counts and rate.
    """
    return rate_by_sides(
        both,
        "interest_rate_bin",
        prepare=lambda lf: lf.with_columns(
            binned("interest_rate", bin_size, mode="round", dtype=pl.Float64)
        ).filter(
            pl.col("interest_rate").is_not_null()
            & pl.col("interest_rate_bin").is_between(min_rate, max_rate)
        ),
    )


def ltv_rates(both: tuple[Side, Side], bin_size: int = 5) -> pl.DataFrame:
    """Match rates by LTV bin, from both sides.

    Args:
        both: The two sides, from `sides`.
        bin_size: Bin width in LTV points.

    Returns:
        One row per bin with each side's counts and rate.
    """
    return rate_by_sides(
        both,
        "ltv_bin",
        prepare=lambda lf: lf.with_columns(
            binned("ltv", bin_size, mode="round", dtype=pl.Int64)
        ).filter(
            pl.col("ltv").is_not_null() & (pl.col("ltv_bin") > 0) & (pl.col("ltv_bin") <= 100)
        ),
    )


def mortgage_type_rates(both: tuple[Side, Side]) -> pl.DataFrame:
    """Match rates by mortgage type (FHA=1, VA=2, RD=3), from both sides.

    Args:
        both: The two sides, from `sides`. GNMA codes the type as ``agency_code``.

    Returns:
        One row per mortgage type with each side's counts and rate.
    """
    gnma, fhlb = both
    return rate_by_sides(
        [
            gnma.prepared(
                lambda lf: lf.filter(pl.col("agency_code").is_not_null()).rename(
                    {"agency_code": "mortgage_type"}
                )
            ),
            fhlb.prepared(_not_null("mortgage_type")),
        ],
        "mortgage_type",
    )


def analyze_unmatched_loans(both: tuple[Side, Side]) -> dict[str, Any]:
    """Where the unmatched GNMA loans sit: by state, year and mortgage type.

    Args:
        both: The two sides, from `sides`.

    Returns:
        Unmatched counts per side and the three GNMA breakdowns.
    """
    gnma, fhlb = both
    unmatched_gnma = gnma.universe.lazy().join(gnma.matched, on=gnma.on, how="anti")
    n_unmatched_fhlb = (
        fhlb.universe.lazy()
        .join(fhlb.matched, on=fhlb.on, how="anti")
        .select(pl.len())
        .collect()
        .item()
    )

    def by(col: str, sort_by: str, descending: bool = False) -> pl.DataFrame:
        return (
            unmatched_gnma.filter(pl.col(col).is_not_null())
            .group_by(col)
            .agg(pl.len().alias("unmatched_count"))
            .sort(sort_by, descending=descending)
            .collect()
        )

    return {
        "n_unmatched_gnma": unmatched_gnma.select(pl.len()).collect().item(),
        "n_unmatched_fhlb": n_unmatched_fhlb,
        "unmatched_gnma_by_state": by("state", "unmatched_count", descending=True),
        "unmatched_gnma_by_year": by("origination_year", "origination_year"),
        "unmatched_gnma_by_type": by("agency_code", "agency_code"),
    }


# =============================================================================
# Figure
# =============================================================================


def _panel_quality_hist(
    ax: Any, details: pl.DataFrame, stats: dict[str, Any], metric: QualityMetric
) -> None:
    """Distribution of one matched-pair difference, against its tight threshold."""
    values = details[metric.column].drop_nulls().to_numpy()
    if len(values) == 0:
        ax.set_axis_off()
        return
    ax.hist(values, bins=50, color=QUALITY_COLOR, edgecolor="white", alpha=0.7)
    # Frame on the data, not on the threshold: where every difference already sits
    # inside it, stretching the axis out flattens the distribution into one spike.
    upper = min(float(values.max()), metric.x_cap)
    ax.set_xlim(0, upper)
    if metric.threshold <= upper:
        ax.axvline(
            metric.threshold,
            color=COLORS["midnight"],
            linestyle="--",
            label=f"Tight threshold ({metric.threshold_label})",
        )
        ax.legend(fontsize=8)
    ax.set_xlabel(metric.xlabel)
    ax.set_ylabel("Count")
    ax.set_title(f"{metric.title}\n({stats[metric.stat_key]:.1%} within {metric.threshold_label})")
    ax.grid(True, alpha=0.3, axis="y")


def _panel_mortgage_type(ax: Any, rates_df: pl.DataFrame) -> None:
    """Both sides' match rate per mortgage type, with the counts behind each."""
    if not panel_grouped_bars(
        ax,
        rates_df,
        "mortgage_type",
        RATE_SERIES,
        MORTGAGE_TYPE_LABELS,
        xlabel="Mortgage Type",
        label_fontsize=9,
    ):
        return
    df = rates_df.filter(pl.col("mortgage_type").is_not_null()).sort("mortgage_type")
    ax.set_xticklabels(
        [
            f"{MORTGAGE_TYPE_LABELS.get(t, str(t))}\nGNMA {g:,} / FHLB {f:,}"
            for t, g, f in zip(
                df["mortgage_type"].to_list(),
                df["gnma_total"].to_list(),
                df["fhlb_total"].to_list(),
            )
        ],
        fontsize=8,
    )
    ax.set_title("Match rates by mortgage type (FHA/VA/RD)")


def plot_validation_panel(
    results: dict[str, Any], output_path: Path | None = None, interactive: bool = False
) -> None:
    """Render the validation figure.

    Rows: match rates by year (full width); loan amount | interest rate; LTV |
    mortgage type; then the four match-quality difference distributions.

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
    fig = plt.figure(figsize=(16, PANEL_HEIGHT_IN * rows + 1.5))
    grid = panel_gridspec(
        fig, rows, 2, title=f"FHLB-GNMA match validation ({MIN_YEAR}-{MAX_YEAR})", title_fontsize=14
    )

    yearly = results["yearly_rates"]
    panel_temporal(
        fig.add_subplot(grid[0, :]),
        [
            TemporalSeries(
                yearly["year"],
                yearly[s.rate_col],
                overall_rate(yearly, f"{p}_matched", f"{p}_total"),
                s.color,
                s.label,
                s.marker,
            )
            for s, p in zip(RATE_SERIES, ("gnma", "fhlb"))
        ],
        legend_loc="lower left",
        headroom=0.02,
    )

    for i, (key, bin_col, xlabel, noun, x_scale) in enumerate(CONTINUOUS_DIMENSIONS):
        ax = fig.add_subplot(grid[1 + i // 2, i % 2])
        panel_rate_vs_bin(
            ax,
            results[key],
            bin_col,
            RATE_SERIES,
            xlabel=xlabel,
            x_scale=x_scale,
            legend_loc="lower left",
        )
        ax.set_title(f"Match rates by {noun}")

    _panel_mortgage_type(fig.add_subplot(grid[2, 1]), results["mortgage_type_rates"])

    quality_stats = results["quality_stats"]
    for i, metric in enumerate(QUALITY_METRICS):
        _panel_quality_hist(
            fig.add_subplot(grid[3 + i // 2, i % 2]),
            quality_stats["details_with_diff"],
            quality_stats,
            metric,
        )

    save_and_close(fig, output_path, interactive)


def plot_state_match_rate_map(gnma_by_state: pl.DataFrame, output_path: Path | None = None) -> None:
    """Choropleth of GNMA-side match rates by state.

    Args:
        gnma_by_state: GNMA-side per-state match rates.
        output_path: Destination PNG; None renders nothing.
    """
    kept = gnma_by_state.filter(
        ~pl.col("state").is_in(NON_STATE_CODES) & pl.col("gnma_match_rate").is_not_null()
    )
    panel = StatePanel(
        "FHLB-GNMA Match Rates by State", kept["state"].to_list(), kept["gnma_match_rate"].to_list()
    )
    # This workflow's rates sit high and tight; the 0.50 floor keeps the spread legible.
    plot_state_choropleth(
        [panel], "FHLB-GNMA Match Rates by State", output_path, color_floor=0.50, color_pad=0.05
    )


# =============================================================================
# Report
# =============================================================================


def _print_quality(quality_stats: dict[str, Any]) -> None:
    blocks = (
        ("Interest Rate Differences", "rate_stats", "{:.4f}%", "tight_rate_pct", "< 0.01%"),
        ("Balance Differences", "balance_stats", "${:,.0f}", "tight_balance_pct", "<= $1,000"),
        ("LTV Differences", "ltv_stats", "{:.4f}%", "tight_ltv_pct", "< 0.01%"),
        ("DTI Differences", "dti_stats", "{:.4f}%", "tight_dti_pct", "< 0.01%"),
    )
    for title, stats_key, fmt, tight_key, tight_label in blocks:
        print(f"{title}:")
        for k, v in quality_stats[stats_key].items():
            print(f"  {k}: {fmt.format(v)}")
        print(f"  Fraction {tight_label}: {quality_stats[tight_key]:.1%}\n")


def run_validation(
    min_year: int = MIN_YEAR,
    max_year: int = MAX_YEAR,
    issuer_id: int | None = DEFAULT_GNMA_ISSUER_ID,
    output_dir: Path | None = None,
    save_plots: bool = True,
    show_plots: bool = False,
) -> dict[str, Any]:
    """Run every validation analysis, print the report, and draw the figures.

    Args:
        min_year: First year to include.
        max_year: Last year to include.
        issuer_id: GNMA issuer filter; None or 0 keeps every issuer.
        output_dir: Directory for the figures. Defaults to ``FIGURES_DIR``.
        save_plots: Whether to write the figures.
        show_plots: Whether to display the matplotlib figure interactively.

    Returns:
        Every computed frame and statistic, keyed by name.
    """
    output_dir = output_dir or FIGURES_DIR
    if save_plots:
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"Saving figures to: {output_dir}")

    print("\nLoading FHLB-GNMA match data...")
    crosswalk, details = load_match_data()
    print(f"  Crosswalk: {len(crosswalk):,} matched pairs")
    print(f"  Match details: {len(details):,} records")
    issuer_str = f"issuer {issuer_id}" if issuer_id else "all issuers"
    print(f"\nLoading source data ({min_year}-{max_year}, {issuer_str})...")
    gnma, fhlb = load_source_data(min_year, max_year, issuer_id, verbose=True)
    print(f"  GNMA prepared: {len(gnma):,} loans")
    print(f"  FHLB prepared: {len(fhlb):,} records")
    both = sides(crosswalk, gnma, fhlb)

    print_section("1. Match Statistics Summary")
    match_summary = compute_match_summary(crosswalk, gnma, fhlb, both)
    print(f"Total GNMA loans:    {match_summary['total_gnma_loans']:,}")
    print(f"Total FHLB records:  {match_summary['total_fhlb_loans']:,}")
    print(f"Matched pairs:       {match_summary['matched_pairs']:,}")
    print(f"GNMA match rate:     {match_summary['gnma_match_rate']:.1%}")
    print(f"FHLB match rate:     {match_summary['fhlb_match_rate']:.1%}")
    if match_summary["gnma_out_of_scope"] or match_summary["fhlb_out_of_scope"]:
        print(
            "\nNote: the crosswalk covers a wider population than the rates above."
            f" {match_summary['gnma_out_of_scope']:,} matched GNMA loans and"
            f" {match_summary['fhlb_out_of_scope']:,} matched FHLB records fall outside"
            f" this run's scope (issuer filter, {min_year}-{max_year}) and are excluded"
            " from both the numerator and the denominator."
        )

    print_section("2. Match Quality Analysis")
    quality_stats = compute_match_quality_statistics(details)
    _print_quality(quality_stats)

    print_section("3. Temporal Analysis")
    yearly = yearly_rates(both)
    print("Match rates by year (GNMA origination year / FHLB note year):")
    print(yearly)

    print_section("4. Geographic Analysis")
    gnma_by_state = state_rates(both[0], "state")
    fhlb_by_state = state_rates(both[1], "state_fips")
    print("GNMA Match Rates by State (Top 10 by Volume):")
    print(gnma_by_state.head(10))
    print("\nLowest GNMA match rate states (min 50 loans):")
    print(gnma_by_state.filter(pl.col("gnma_total") >= 50).sort("gnma_match_rate").head(5))

    print_section("5. Loan Characteristics Analysis")
    by_type = mortgage_type_rates(both)
    print("Match Rates by Mortgage Type:")
    print(
        by_type.with_columns(
            pl.col("mortgage_type")
            .replace_strict(MORTGAGE_TYPE_LABELS, default=None)
            .alias("label")
        )
    )
    amount_rates = loan_amount_rates(both)
    rate_rates = interest_rate_rates(both)
    ltv = ltv_rates(both)

    print_section("6. Unmatched Loan Analysis")
    unmatched = analyze_unmatched_loans(both)
    print(f"Unmatched GNMA loans: {unmatched['n_unmatched_gnma']:,}")
    print(f"Unmatched FHLB records: {unmatched['n_unmatched_fhlb']:,}")
    print("\nUnmatched GNMA by Year:")
    print(unmatched["unmatched_gnma_by_year"])
    print("\nUnmatched GNMA by Mortgage Type:")
    print(unmatched["unmatched_gnma_by_type"])

    print_section("7. Key Findings")
    print(f"Overall Match Rate: {match_summary['gnma_match_rate']:.1%} (GNMA perspective)")
    print(f"Match Quality: {quality_stats['tight_rate_pct']:.1%} of matches have rate diff < 0.01%")

    results: dict[str, Any] = {
        "match_summary": match_summary,
        "quality_stats": quality_stats,
        "yearly_rates": yearly,
        "gnma_by_state": gnma_by_state,
        "fhlb_by_state": fhlb_by_state,
        "mortgage_type_rates": by_type,
        "loan_amount_rates": amount_rates,
        "interest_rate_rates": rate_rates,
        "ltv_rates": ltv,
        "unmatched": unmatched,
    }

    if (show_plots or save_plots) and HAS_MATPLOTLIB:
        print_section("8. Generating Visualizations")
        plot_validation_panel(
            results,
            output_path=output_dir / "fhlb_mbs_validation.png" if save_plots else None,
            interactive=show_plots,
        )
        plot_state_match_rate_map(
            gnma_by_state,
            output_path=output_dir / "fhlb_mbs_state_match_rate_map.png" if save_plots else None,
        )

    print("\nGNMA-FHLB validation complete!")
    return results
