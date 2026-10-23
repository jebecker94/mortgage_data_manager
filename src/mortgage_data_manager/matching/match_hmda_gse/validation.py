"""Validation report for the HMDA-GSE (UMBS) linkage.

Two directions, because the two sides report different clocks: HMDA retail
GSE-sold loans matched to UMBS **by activity year**, and UMBS loans matched back to
HMDA **by first payment date**. Both count the spine's conventional edge per
enterprise: the chain master crosswalk plus the admitted direct HMDA<->issuance
increment, the same two sources ``match master build`` composes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, cast

import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.matching.match_hmda_gse.config import HMDAGSEMatchingConfig as Config
from mortgage_data_manager.matching.validation_common import (
    COLORS,
    HAS_MATPLOTLIB,
    Side,
    get_pyplot,
    matched_ids,
    panel_gridspec,
    print_section,
    rate_by_sides,
    save_and_close,
)
from mortgage_data_manager.sources.hmda.load import scan_hmda_for_index

VALIDATION_OUTPUT_DIR = MortgageDataConfig.PROJECT_DIR / "docs" / "matching" / "figures"

type Agency = Literal["fnma", "fhlmc"]

#: HMDA purchaser_type code per enterprise.
PURCHASER_TYPE: dict[Agency, int] = {"fnma": 1, "fhlmc": 3}

# The two series every panel draws: rate column, color, marker, label.
GSE_SERIES = (
    ("fnma_match_rate", COLORS["hunter"], "o", "FNMA"),
    ("fhlmc_match_rate", COLORS["gold"], "s", "FHLMC"),
)

#: Columns every crosswalk frame here carries.
CROSSWALK_SCHEMA: dict[str, type[pl.DataType]] = {
    "HMDAIndex": pl.Utf8,
    "activity_year": pl.Int64,
    "umbs_loan_id": pl.Utf8,
}

# Vertical inches allotted to one grid row; the canvas is sized from the row count.
PANEL_HEIGHT_IN = 4.5

MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# At most this many tick labels on the monthly panel, which spans dozens of months.
MAX_MONTH_TICKS = 12


# =============================================================================
# Data
# =============================================================================


def load_hmda_retail_gse_loans(min_year: int = 2019, max_year: int = 2025) -> pl.DataFrame:
    """HMDA retail conventional originations sold to Fannie Mae or Freddie Mac.

    Reads the HMDA releases the chain crosswalks' HMDAIndex values name, then keeps
    ``action_taken=1``, ``loan_type=1``, ``purchaser_type in (1, 3)`` and
    ``submission_of_application=1``.

    Args:
        min_year: First activity year.
        max_year: Last activity year.

    Returns:
        ``HMDAIndex``, ``activity_year``, ``purchaser_type``.
    """
    index = pl.concat(
        [
            pl.scan_parquet(Config.get_chain_crosswalk_path(agency)).select("HMDAIndex")
            for agency in ("fnma", "fhlmc")
        ],
        how="diagonal_relaxed",
    )
    return (
        scan_hmda_for_index(index, "post2018", root=Config.HMDA_SILVER_DIR)
        .filter(
            pl.col("activity_year").is_between(min_year, max_year)
            & (pl.col("action_taken") == 1)
            & (pl.col("loan_type") == 1)
            & pl.col("purchaser_type").is_in(list(PURCHASER_TYPE.values()))
            & (pl.col("submission_of_application") == 1)
        )
        .select("HMDAIndex", "activity_year", "purchaser_type")
        .collect(engine="streaming")
    )


def load_chain_crosswalk(
    agency: Agency, min_year: int = 2019, max_year: int = 2025
) -> pl.DataFrame:
    """The chain master crosswalk for one enterprise.

    Args:
        agency: Which enterprise.
        min_year: First activity year.
        max_year: Last activity year.

    Returns:
        ``HMDAIndex``, ``activity_year``, ``umbs_loan_id``, one row per HMDA loan.
    """
    path = Config.get_chain_crosswalk_path(agency, enriched=False)
    if not path.exists():
        return pl.DataFrame(schema=CROSSWALK_SCHEMA)
    return (
        pl.scan_parquet(str(path))
        .filter(pl.col("activity_year").is_between(min_year, max_year))
        .select(*CROSSWALK_SCHEMA)
        .collect()
        .cast(CROSSWALK_SCHEMA)  # type: ignore[arg-type]
    )


def load_direct_increment(min_year: int = 2019, max_year: int = 2025) -> pl.DataFrame:
    """The direct HMDA<->issuance links the spine admits beyond the chain, both enterprises.

    Reuses the spine's own loader, so the admission rule, the re-check against the
    current chain and the global one-target-one-loan screen are the production ones.
    ``umbs_loan_id`` is null where the UMBS hop missed; such a row counts on the HMDA
    side only.

    Args:
        min_year: First activity year.
        max_year: Last activity year.

    Returns:
        ``HMDAIndex``, ``activity_year``, ``umbs_loan_id``, ``agency`` (lowercase).
    """
    # Deferred: the spine builder imports this package's config, so a module-level
    # import would tie the two packages' import order together.
    from mortgage_data_manager.matching.master.build import load_direct_edge

    increment, _ = load_direct_edge()
    return (
        increment.filter(pl.col("activity_year").is_between(min_year, max_year))
        .select(*CROSSWALK_SCHEMA, "agency")
        .cast(CROSSWALK_SCHEMA)  # type: ignore[arg-type]
    )


def load_conventional_edge(
    agency: Agency, direct: pl.DataFrame, min_year: int = 2019, max_year: int = 2025
) -> pl.DataFrame:
    """Chain plus direct increment for one enterprise.

    Args:
        agency: Which enterprise.
        direct: Both enterprises' increment, from `load_direct_increment`.
        min_year: First activity year.
        max_year: Last activity year.

    Returns:
        ``HMDAIndex``, ``activity_year``, ``umbs_loan_id``, ``source`` (``chain`` or
        ``direct``), one row per HMDA loan.

    Raises:
        ValueError: If a HMDA loan appears in both sources, which the spine's
            re-check against the current chain rules out.
    """
    edge = pl.concat(
        [
            load_chain_crosswalk(agency, min_year, max_year).with_columns(source=pl.lit("chain")),
            direct.filter(pl.col("agency") == agency)
            .drop("agency")
            .with_columns(source=pl.lit("direct")),
        ]
    )
    if edge["HMDAIndex"].n_unique() != edge.height:
        raise ValueError(f"{agency}: a HMDA loan is linked by both the chain and the direct edge")
    return edge


def load_umbs_data(agency: Agency, min_year: int = 2019, max_year: int = 2025) -> pl.DataFrame:
    """One enterprise's UMBS loans with their first payment date.

    Args:
        agency: Which enterprise.
        min_year: First first-payment year.
        max_year: Last first-payment year (one extra year is read, since a loan
            originated in ``max_year`` first pays in the next).

    Returns:
        ``umbs_loan_id``, ``first_payment_date`` (packed MMYYYY), one row per loan.
    """
    empty = pl.DataFrame(schema={"umbs_loan_id": pl.Utf8, "first_payment_date": pl.Int64})
    illd_dir = Config.get_umbs_illd_dir(agency)
    files = list(illd_dir.glob("*.parquet")) if illd_dir.exists() else []
    if not files:
        return empty
    return (
        pl.scan_parquet([str(f) for f in files], missing_columns="insert", extra_columns="ignore")
        .filter((pl.col("First Payment Date") % 10000).is_between(min_year, max_year + 1))
        .select(
            pl.col("Loan Identifier").cast(pl.Utf8).alias("umbs_loan_id"),
            pl.col("First Payment Date").alias("first_payment_date"),
        )
        .unique(subset=["umbs_loan_id"])
        .collect()
    )


def hmda_sides(hmda: pl.DataFrame, crosswalks: dict[Agency, pl.DataFrame]) -> list[Side]:
    """The HMDA side per enterprise: loans sold to it, against its crosswalk.

    Args:
        hmda: Retail GSE-sold loans, from `load_hmda_retail_gse_loans`.
        crosswalks: Conventional edge per enterprise.

    Returns:
        One side per enterprise, in ``PURCHASER_TYPE`` order.
    """
    return [
        Side(
            hmda.filter(pl.col("purchaser_type") == ptype),
            matched_ids(crosswalks[agency], "HMDAIndex"),
            "HMDAIndex",
            agency,
        )
        for agency, ptype in PURCHASER_TYPE.items()
    ]


def umbs_sides(
    umbs: dict[Agency, pl.DataFrame], crosswalks: dict[Agency, pl.DataFrame]
) -> list[Side]:
    """The UMBS side per enterprise: its loans, against its crosswalk.

    Args:
        umbs: UMBS loans per enterprise, from `load_umbs_data`.
        crosswalks: Conventional edge per enterprise.

    Returns:
        One side per enterprise, in ``PURCHASER_TYPE`` order.
    """
    return [
        Side(umbs[agency], matched_ids(crosswalks[agency], "umbs_loan_id"), "umbs_loan_id", agency)
        for agency in PURCHASER_TYPE
    ]


# =============================================================================
# Statistics
# =============================================================================


def compute_hmda_yearly_match_rates(sides: list[Side]) -> pl.DataFrame:
    """Each enterprise's HMDA-to-UMBS match rate by activity year.

    Args:
        sides: The HMDA sides, from `hmda_sides`.

    Returns:
        One row per activity year with each enterprise's counts and rate.
    """
    return rate_by_sides(sides, "activity_year")


def compute_umbs_monthly_match_rates(sides: list[Side]) -> pl.DataFrame:
    """Each enterprise's UMBS-to-HMDA match rate by first payment month.

    Args:
        sides: The UMBS sides, from `umbs_sides`.

    Returns:
        One row per first payment date (packed MMYYYY), in chronological order.
    """
    date = pl.col("first_payment_date")
    return rate_by_sides(sides, "first_payment_date").sort(date % 10000, date // 10000)


# =============================================================================
# Figure
# =============================================================================


def _month_label(packed: int | None) -> str:
    """Render a packed MMYYYY first-payment date as ``Mon YYYY``."""
    if packed is None:
        return ""
    month, year = packed // 10000, packed % 10000
    return f"{MONTH_NAMES[month - 1] if 1 <= month <= 12 else month} {year}"


def _panel_rates_over_period(
    ax: Any, data: pl.DataFrame, period_col: str, xlabel: str, title: str, *, monthly: bool = False
) -> None:
    """Each enterprise's match rate across a period.

    Yearly panels label every point; monthly panels span dozens of periods, so they
    plot against an index and thin the tick labels.
    """
    periods = data[period_col].to_list()
    if not periods:
        ax.set_axis_off()
        return
    x = list(range(len(periods))) if monthly else periods
    all_rates: list[float | None] = []
    for rate_col, color, marker, label in GSE_SERIES:
        rates = [r * 100 if r is not None else None for r in data[rate_col].to_list()]
        all_rates += rates
        ax.plot(
            x,
            rates,
            color=color,
            linewidth=1.5 if monthly else 2,
            marker=None if monthly else marker,
            markersize=7,
            alpha=0.85,
            label=label,
        )
    if monthly:
        ticks = list(range(0, len(periods), max(1, len(periods) // MAX_MONTH_TICKS)))
        ax.set_xticks(ticks)
        ax.set_xticklabels([_month_label(periods[i]) for i in ticks], rotation=45, ha="right")
    else:
        ax.set_xticks(periods)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Match Rate (%)")
    ax.set_title(title)
    valid = [r for r in all_rates if r is not None]
    if valid:
        ax.set_ylim(max(0.0, min(valid) - 5), min(100.0, max(valid) + 5))
    ax.legend(loc="best", fontsize=9)
    ax.grid(True, alpha=0.3)


def plot_validation_panel(
    results: dict[str, Any], output_path: Path | None = None, interactive: bool = False
) -> None:
    """Render the validation figure: the two directions, stacked.

    Args:
        results: The accumulated ``run_validation`` results.
        output_path: Where to save the figure (PNG). Skipped if ``None``.
        interactive: Whether to display the figure interactively.
    """
    plt = get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return
    fig = plt.figure(figsize=(15, PANEL_HEIGHT_IN * 2 + 1.5))
    grid = panel_gridspec(
        fig,
        2,
        1,
        title="HMDA-UMBS match validation (retail GSE-sold conventional loans)",
        title_fontsize=14,
        bottom_in=1.3,
    )
    _panel_rates_over_period(
        fig.add_subplot(grid[0, 0]),
        results["yearly_data"],
        "activity_year",
        "Activity Year",
        "HMDA-to-UMBS match rate by activity year",
    )
    _panel_rates_over_period(
        fig.add_subplot(grid[1, 0]),
        results["monthly_data"],
        "first_payment_date",
        "First Payment Date",
        "UMBS-to-HMDA match rate by first payment date",
        monthly=True,
    )
    save_and_close(fig, output_path, interactive)


# =============================================================================
# Report
# =============================================================================


def _print_means(frame: pl.DataFrame) -> None:
    for rate_col, _, _, label in GSE_SERIES:
        mean = frame[rate_col].mean()
        if mean is not None:
            print(f"{label} average match rate: {cast(float, mean):.1%}")


def run_validation(
    min_year: int = 2019,
    max_year: int = 2025,
    output_dir: Path | None = None,
    save_plots: bool = True,
    show_plots: bool = False,
) -> dict[str, Any]:
    """Run both directions of the validation, print the report, and draw the figure.

    Args:
        min_year: First year to analyze.
        max_year: Last year to analyze.
        output_dir: Directory for the figure. Defaults to ``VALIDATION_OUTPUT_DIR``.
        save_plots: Whether to write the figure.
        show_plots: Whether to display the figure interactively.

    Returns:
        The two rate frames, keyed ``yearly_data`` and ``monthly_data``.
    """
    output_dir = output_dir or VALIDATION_OUTPUT_DIR
    if save_plots:
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"Saving figures to: {output_dir}")

    print_section("1. Loading Data")
    hmda = load_hmda_retail_gse_loans(min_year, max_year)
    print(f"HMDA retail GSE-sold loans: {len(hmda):,}")
    for agency, ptype in PURCHASER_TYPE.items():
        print(
            f"  {agency.upper()} (purchaser_type={ptype}): {hmda.filter(pl.col('purchaser_type') == ptype).height:,}"
        )
    direct = load_direct_increment(min_year, max_year)
    crosswalks: dict[Agency, pl.DataFrame] = {}
    umbs: dict[Agency, pl.DataFrame] = {}
    for agency in PURCHASER_TYPE:
        crosswalks[agency] = load_conventional_edge(agency, direct, min_year, max_year)
        umbs[agency] = load_umbs_data(agency, min_year, max_year)
        n_direct = crosswalks[agency].filter(pl.col("source") == "direct").height
        print(
            f"{agency.upper()}: {len(crosswalks[agency]):,} matched HMDA loans "
            f"({len(crosswalks[agency]) - n_direct:,} chain + {n_direct:,} direct); "
            f"{len(umbs[agency]):,} UMBS loans"
        )

    print_section("2. HMDA Match Rates by Year (Figure 1)")
    yearly = compute_hmda_yearly_match_rates(hmda_sides(hmda, crosswalks))
    print(yearly)
    _print_means(yearly)

    print_section("3. UMBS Match Rates by Month (Figure 2)")
    monthly = compute_umbs_monthly_match_rates(umbs_sides(umbs, crosswalks))
    print(f"Monthly data points: {len(monthly)}")
    print(monthly.head(10))
    _print_means(monthly)

    results: dict[str, Any] = {"yearly_data": yearly, "monthly_data": monthly}
    if (save_plots or show_plots) and HAS_MATPLOTLIB:
        print_section("4. Generating Visualizations")
        plot_validation_panel(
            results,
            output_path=output_dir / "hmda_gse_validation.png" if save_plots else None,
            interactive=show_plots,
        )
    print("\nValidation complete.")
    return results
