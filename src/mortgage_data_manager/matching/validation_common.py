"""Shared scaffolding for the per-workflow validation modules.

Every ``matching/{workflow}/validation.py`` is a one-shot report generator: it loads a
crosswalk plus its source datasets, prints a sectioned summary, and writes figures under
``docs/matching/figures/`` (the workflow lives in the filename, not a subdirectory). Each
had grown its own copy of the same four pieces -- the optional-matplotlib guard, the chart
palette, the section header, and the save/show/close tail repeated after every figure. They
live here instead so a change lands once.

Validation modules are the sanctioned exception to the "library code never prints" rule,
so the helpers here print directly rather than logging.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

import numpy as np
import polars as pl

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from matplotlib.figure import Figure

try:
    import matplotlib

    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    matplotlib = None  # type: ignore[assignment]


__all__ = [
    "HAS_MATPLOTLIB",
    "COLORS",
    "NON_STATE_CODES",
    "get_pyplot",
    "print_section",
    "save_and_close",
    "panel_gridspec",
    "RateSeries",
    "finite_rates",
    "set_rate_ylim",
    "panel_rate_vs_bin",
    "panel_grouped_bars",
    "TemporalSeries",
    "panel_temporal",
    "Frame",
    "matched_ids",
    "binned",
    "Side",
    "rate_by",
    "matched_count",
    "rate_by_sides",
    "overall_rate",
    "analyze_state_size_correlation",
    "StatePanel",
    "plot_state_choropleth",
]


# =============================================================================
# Palette
# =============================================================================

#: Canonical chart colors for every matching validation figure. Workflow modules
#: build their own categorical *sequences* from these -- the orderings differ per
#: workflow on purpose, so only the base colors are shared.
COLORS: dict[str, str] = {
    # Primary anchors
    "midnight": "#0E2F44",  # Headers, dominant elements
    "hunter": "#145A32",  # Large datasets, key series
    # Secondary highlights
    "gold": "#D4AC0D",  # Focus data, callouts
    "terracotta": "#BA4A00",  # Trend lines, emphasis
    # Tertiary extensions
    "mulberry": "#900C3F",  # High-cardinality categories
    "purple": "#5B2C6F",  # Additional categories
    "teal": "#117864",  # Additional categories
    # Neutrals
    "grey": "#85929E",  # Context, axes, benchmarks
    "slate": "#566573",  # Borders, secondary text
    # Background & text
    "canvas": "#F8F9F9",  # Background (off-white)
    "ink": "#1B2631",  # All standard text
}


# =============================================================================
# Geography
# =============================================================================

#: Values in a state column that are not a mappable US jurisdiction: the five
#: territories (plotly's ``locationmode="USA-states"`` has no geometry for them) plus
#: HMDA's ``"NA"`` not-applicable sentinel.
#:
#: DC is deliberately **not** in this set. It is a real jurisdiction plotly can draw,
#: so every map/filter site includes it and it appears in every figure alike.
#:
#: ``"NA"`` matters beyond the maps. It carries ~5.2k HMDA loans that no matcher can
#: place, so its match rate is a structural zero rather than a measurement. Zero-match
#: groups report 0.0, so it has to be excluded from the state-size correlations on
#: purpose, or it drags a 53-point correlation into a different sign.
NON_STATE_CODES: frozenset[str] = frozenset({"PR", "GU", "VI", "AS", "MP", "NA"})

# =============================================================================
# Output helpers
# =============================================================================


def get_pyplot(interactive: bool = True):
    """Get pyplot with the appropriate backend, or None if matplotlib is missing.

    Args:
        interactive: Use an interactive backend rather than Agg.
    """
    if not HAS_MATPLOTLIB:
        return None
    if not interactive:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def print_section(title: str) -> None:
    """Print a section header.

    Args:
        title: The section title to print.
    """
    print(f"\n{'=' * 70}")
    print(f" {title}")
    print(f"{'=' * 70}\n")


def panel_gridspec(
    fig: Figure,
    nrows: int,
    ncols: int,
    *,
    title: str | None = None,
    title_fontsize: int = 13,
    title_in: float = 0.75,
    margin_in: float = 0.7,
    bottom_in: float | None = None,
    hspace: float = 0.45,
    wspace: float = 0.3,
    height_ratios: Sequence[float] | None = None,
) -> Any:
    """Lay a grid over a figure, reserving fixed margins measured in inches.

    ``Figure.tight_layout`` refuses to run once any panel has twinned its axis for a
    density overlay -- it warns and falls back to the default subplot parameters,
    which on a tall multi-panel figure leaves a wide band of dead space under the
    title. Positioning the grid directly avoids that, and keeps the title band the
    same physical size whether the figure has three rows or eight.

    Because the helper owns the reserved band it also places the figure title, which
    otherwise has to be positioned against the same arithmetic at every call site.

    Args:
        fig: The figure to lay the grid over; its size fixes the margin fractions.
        nrows: Grid rows.
        ncols: Grid columns.
        title: Figure title, drawn centred in the reserved top band. Give
            ``title_in`` enough room for a title that wraps to several lines.
        title_fontsize: Point size for that title.
        title_in: Inches reserved at the top for the figure title.
        margin_in: Inches reserved on the other three sides.
        bottom_in: Inches reserved at the bottom, overriding ``margin_in``. Raise it
            when the bottom row carries rotated tick labels or a figure legend, both
            of which are drawn into this margin rather than inside the axes.
        hspace: Row spacing, as a fraction of panel height.
        wspace: Column spacing, as a fraction of panel width.
        height_ratios: Relative row heights, one per row. Use it when a row holds
            something that needs the space, such as a bar per state.

    Returns:
        The GridSpec, ready for ``fig.add_subplot(grid[row, col])``.
    """
    width_in, height_in = fig.get_size_inches()
    if title is not None:
        fig.suptitle(title, fontsize=title_fontsize, y=1 - 0.25 / height_in, va="top")
    return fig.add_gridspec(
        nrows,
        ncols,
        hspace=hspace,
        wspace=wspace,
        height_ratios=height_ratios,
        top=1 - title_in / height_in,
        bottom=(margin_in if bottom_in is None else bottom_in) / height_in,
        left=margin_in / width_in,
        right=1 - margin_in / width_in,
    )


def save_and_close(
    fig: Figure,
    output_path: Path | None,
    interactive: bool = False,
    *,
    dpi: int = 150,
    **savefig_kwargs: Any,
) -> None:
    """Write a figure to ``output_path``, optionally show it, then close it.

    Args:
        fig: The figure to write.
        output_path: Destination; parent directories are created. None skips the write.
        interactive: Whether to display the figure before closing.
        dpi: Raster resolution.
        **savefig_kwargs: Passed through to ``savefig`` (e.g. ``bbox_inches``,
            ``facecolor``) -- these vary per figure and are not standardized here.
    """
    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=dpi, **savefig_kwargs)
        print(f"Saved: {output_path}")

    if not HAS_MATPLOTLIB:
        return

    # Import pyplot without re-selecting a backend: the caller already did that when it
    # built the figure, and switching backends with live figures open is not safe.
    import matplotlib.pyplot as plt

    if interactive:
        plt.show()
    plt.close(fig)


# =============================================================================
# Panel primitives
#
# Two panel shapes recur in nearly every workflow's figure: a match rate across a
# binned continuous variable with each side's loan density behind it, and a paired
# bar chart of both sides' rates per category. They are written once here and handed
# an axis, so a workflow's figure composes them rather than restating them.
# =============================================================================


class RateSeries(NamedTuple):
    """One side's match rate across a dimension, and the counts behind it.

    Attributes:
        rate_col: Match-rate column in the rates frame.
        total_col: Denominator column, drawn as a density behind the rate. None
            draws no density for this side.
        color: Line, fill, and bar color.
        label: Legend label.
        marker: Line marker; ignored by the bar panel.
    """

    rate_col: str
    total_col: str | None
    color: str
    label: str
    marker: str = "o"


def finite_rates(values: Sequence[float | None]) -> list[float]:
    """The plottable subset of a rate column: no nulls, no NaNs.

    Args:
        values: The rate column to filter.

    Returns:
        The finite values, with nulls and NaNs removed.
    """
    return [v for v in values if v is not None and not np.isnan(v)]


def set_rate_ylim(ax: Any, rates: Sequence[float | None], headroom: float = 0.05) -> bool:
    """Frame an axis around the rates actually present. False if there are none.

    The top is deliberately not clamped to 1.0. Several of these workflows match in
    the high nineties, and clamping there clips the value printed above a bar.

    Args:
        ax: Axis to set limits on.
        rates: Every rate the panel will draw, nulls and NaNs included.
        headroom: Space left above the highest rate; raise it on panels that print
            values on top of their bars.

    Returns:
        Whether any rate was plottable.
    """
    valid = finite_rates(rates)
    if not valid:
        return False
    ax.set_ylim(max(0.0, min(valid) - 0.05), max(valid) + headroom)
    return True


def panel_rate_vs_bin(
    ax: Any,
    rates_df: pl.DataFrame,
    bin_col: str,
    series: Sequence[RateSeries],
    *,
    xlabel: str,
    x_scale: float = 1.0,
    headroom: float = 0.02,
    legend_loc: str = "upper left",
) -> bool:
    """Draw each side's match rate across a binned dimension, densities behind.

    The densities share one twinned axis so they stay comparable; the rates own the
    primary axis, framed by ``set_rate_ylim``.

    Args:
        ax: Axis to draw into. Blanked if nothing is plottable.
        rates_df: One row per bin, carrying ``bin_col`` and each series' columns.
        bin_col: The bin column.
        series: The sides to draw, in legend order.
        xlabel: Axis label for the binned dimension.
        x_scale: Multiplier on the bin values (e.g. 0.001 to plot dollars as $K).
        headroom: Passed to ``set_rate_ylim``.
        legend_loc: Matplotlib legend location.

    Returns:
        Whether anything was drawn.
    """
    all_rates = [r for s in series for r in rates_df[s.rate_col].to_list()]
    if not set_rate_ylim(ax, all_rates, headroom=headroom):
        ax.set_axis_off()
        return False

    x = np.array(rates_df[bin_col].to_list(), dtype=float) * x_scale

    density_ax = None
    max_pdf = 0.0
    for one in series:
        if one.total_col is None:
            continue
        totals = np.nan_to_num(rates_df[one.total_col].to_numpy().astype(float), nan=0)
        if totals.sum() <= 0:
            continue
        pdf = totals / totals.sum()
        density_ax = density_ax if density_ax is not None else ax.twinx()
        density_ax.fill_between(x, pdf, alpha=0.15, color=one.color)
        max_pdf = max(max_pdf, float(pdf.max()))

    if density_ax is not None:
        density_ax.set_ylabel("Density (PDF)", color="gray", fontsize=9)
        density_ax.tick_params(axis="y", labelcolor="gray", labelsize=8)
        density_ax.set_ylim(0, max_pdf * 1.3 if max_pdf > 0 else 1)

    for one in series:
        ax.plot(
            x,
            rates_df[one.rate_col].to_list(),
            color=one.color,
            linewidth=2,
            marker=one.marker,
            markersize=4,
            label=one.label,
            zorder=10,
        )

    ax.set_xlabel(xlabel)
    ax.set_ylabel("Match Rate")
    ax.legend(loc=legend_loc, fontsize=8)
    ax.grid(True, alpha=0.3)
    return True


def panel_grouped_bars(
    ax: Any,
    rates_df: pl.DataFrame,
    category_col: str,
    series: Sequence[RateSeries],
    labels_by_code: dict[Any, str],
    *,
    xlabel: str,
    headroom: float = 0.08,
    label_fontsize: int = 7,
    tick_rotation: float = 0,
) -> bool:
    """Draw each side's match rate per category, as paired bars with printed values.

    Categories no side has a rate for are dropped -- a code that exists in one
    source's dictionary but is unused by both would otherwise draw as a labelless
    empty slot.

    Args:
        ax: Axis to draw into. Blanked if nothing is plottable.
        rates_df: One row per category.
        category_col: The category column.
        series: The sides to draw, in legend order.
        labels_by_code: Code to display label; unmapped codes print their code.
        xlabel: Axis label for the category dimension.
        headroom: Passed to ``set_rate_ylim``; the default leaves room for the
            values printed above the bars.
        label_fontsize: Point size for those printed values.
        tick_rotation: Degrees to rotate the category tick labels; use it when the
            labels are long enough to collide.

    Returns:
        Whether anything was drawn.
    """
    has_any_rate = pl.any_horizontal(
        pl.col(one.rate_col).is_not_nan().fill_null(False) for one in series
    )
    df = rates_df.filter(pl.col(category_col).is_not_null() & has_any_rate).sort(category_col)

    all_rates = [r for s in series for r in df[s.rate_col].to_list()]
    if not set_rate_ylim(ax, all_rates, headroom=headroom):
        ax.set_axis_off()
        return False

    labels = [labels_by_code.get(c, str(c)) for c in df[category_col].to_list()]
    x = np.arange(len(labels))
    width = 0.8 / len(series)
    start = -0.4 + width / 2

    for i, one in enumerate(series):
        offset = start + i * width
        rates = df[one.rate_col].to_list()
        heights = [r if r is not None and not np.isnan(r) else 0 for r in rates]
        ax.bar(x + offset, heights, width, label=one.label, color=one.color, alpha=0.85)
        for pos, rate in enumerate(rates):
            if rate is not None and not np.isnan(rate):
                ax.text(
                    pos + offset,
                    rate + 0.005,
                    f"{rate:.1%}",
                    ha="center",
                    va="bottom",
                    fontsize=label_fontsize,
                )

    ax.set_xticks(x)
    ax.set_xticklabels(
        labels,
        fontsize=9,
        rotation=tick_rotation,
        ha="right" if tick_rotation else "center",
    )
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Match Rate")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3, axis="y")
    return True


class TemporalSeries(NamedTuple):
    """One side's match rate over time, with the overall rate drawn as a reference.

    Attributes:
        x: Period values (years, or positions along a period axis).
        rates: Match rate per period, aligned with ``x``.
        overall: Whole-window rate, drawn as a dashed line and named in the legend.
            None draws no reference line.
        color: Line color.
        label: Legend label for the series; the reference line is labelled
            ``"{label} overall (rate)"``.
        marker: Line marker.
    """

    x: Sequence[Any]
    rates: Sequence[float | None]
    overall: float | None
    color: str
    label: str
    marker: str = "o"


def panel_temporal(
    ax: Any,
    series: Sequence[TemporalSeries],
    *,
    xlabel: str = "Origination Year",
    title: str | None = "Match rates by origination year",
    legend_loc: str = "best",
    headroom: float = 0.05,
    linewidth: float = 2.5,
) -> bool:
    """Draw each side's match rate over time, each with its overall rate as a reference.

    Args:
        ax: Axis to draw into. Blanked if nothing is plottable.
        series: The sides to draw, in legend order.
        xlabel: Axis label for the period dimension.
        title: Panel title; None leaves it unset.
        legend_loc: Matplotlib legend location.
        headroom: Passed to ``set_rate_ylim``.
        linewidth: Width of the rate lines.

    Returns:
        Whether anything was drawn.
    """
    all_rates = [r for one in series for r in one.rates]
    if not set_rate_ylim(ax, all_rates, headroom=headroom):
        ax.set_axis_off()
        return False

    for one in series:
        ax.plot(
            list(one.x),
            list(one.rates),
            color=one.color,
            linewidth=linewidth,
            marker=one.marker,
            markersize=7,
            label=one.label,
        )
    for one in series:
        if one.overall is not None:
            ax.axhline(
                one.overall,
                color=one.color,
                linestyle="--",
                alpha=0.6,
                linewidth=1.2,
                label=f"{one.label} overall ({one.overall:.1%})",
            )

    ax.set_xlabel(xlabel)
    ax.set_ylabel("Match Rate")
    if title is not None:
        ax.set_title(title)
    ax.legend(loc=legend_loc, fontsize=8)
    ax.grid(True, alpha=0.3)
    return True


# =============================================================================
# Match-rate computation
#
# Almost every per-dimension rate in these modules is the same shape: take the
# distinct matched IDs for one side of a crosswalk, count that side's rows per
# dimension value, count the matched subset the same way, divide, and full-join the
# sides on the dimension. The helpers below express that once. Inputs may be eager
# or lazy; ``rate_by`` stays lazy so several rates can share one ``collect_all``,
# and ``rate_by_sides`` collects because it is the frame the report prints.
# =============================================================================

#: A polars frame in either evaluation mode. Validation inputs arrive both ways.
type Frame = pl.DataFrame | pl.LazyFrame


def _lazy(frame: pl.DataFrame | pl.LazyFrame) -> pl.LazyFrame:
    return frame.lazy() if isinstance(frame, pl.DataFrame) else frame


def _cols(cols: str | Sequence[str]) -> list[str]:
    return [cols] if isinstance(cols, str) else list(cols)


def matched_ids(
    crosswalk: pl.DataFrame | pl.LazyFrame,
    cols: str | Sequence[str],
    *,
    as_cols: str | Sequence[str] | None = None,
) -> pl.LazyFrame:
    """The distinct matched identifiers for one side of a crosswalk.

    Hoist this to the top of a validation run and reuse it: recomputing it per
    dimension re-scans the whole crosswalk each time.

    Args:
        crosswalk: The match crosswalk.
        cols: Identifier column, or columns for a composite key, in the crosswalk.
        as_cols: Names those columns carry in the side's universe, when the crosswalk
            spells them differently (``HMDAIndex_s`` for ``HMDAIndex``).

    Returns:
        One row per distinct matched identifier, named as the universe names it.
    """
    cols = _cols(cols)
    out = _lazy(crosswalk).select(cols).unique()
    if as_cols is not None:
        out = out.rename(dict(zip(cols, _cols(as_cols))))
    return out


def binned(
    col: str,
    width: float,
    *,
    mode: Literal["floor", "round", "midpoint"] = "floor",
    offset: float = 0.0,
    name: str | None = None,
    dtype: pl.DataType | type[pl.DataType] | None = None,
) -> pl.Expr:
    """Bin a continuous column into fixed-width intervals.

    The three modes reproduce the conventions the workflows settled on: ``floor``
    labels a bin by its lower edge, ``round`` by its nearest multiple, ``midpoint``
    by its centre. ``offset`` shifts the grid (HMDA amounts are reported in $10k
    steps centred on $5k).

    Args:
        col: The continuous column.
        width: Bin width, in the column's units.
        mode: How the bin label is derived from the value.
        offset: Grid origin; bins are ``offset + k * width``.
        name: Output column name; defaults to ``{col}_bin``.
        dtype: Optional cast for the bin label.

    Returns:
        The aliased bin expression.
    """
    scaled = (pl.col(col) - offset) / width
    steps = scaled.round() if mode == "round" else scaled.floor()
    expr = steps * width + offset
    if mode == "midpoint":
        expr = expr + width / 2
    if dtype is not None:
        expr = expr.cast(dtype)
    return expr.alias(name or f"{col}_bin")


def rate_by(
    source: pl.DataFrame | pl.LazyFrame,
    matched: pl.DataFrame | pl.LazyFrame,
    on: str | Sequence[str],
    dim: str | Sequence[str],
    *,
    prefix: str,
    total_col: str | None = None,
    matched_col: str | None = None,
    rate_col: str | None = None,
    sort_by: str | None = None,
    descending: bool = True,
) -> pl.LazyFrame:
    """Match rate for one side of a crosswalk, grouped by ``dim``.

    Args:
        source: The side being measured -- its row count is the denominator.
        matched: Distinct matched IDs for that side, from `matched_ids`.
        on: Join key, or composite key, shared by ``source`` and ``matched``.
        dim: Column, or columns, to group by.
        prefix: Names the output columns ``{prefix}_total`` / ``{prefix}_matched`` /
            ``{prefix}_match_rate``.
        total_col: Override for the denominator column name.
        matched_col: Override for the numerator column name.
        rate_col: Override for the rate column name.
        sort_by: Column to sort on; defaults to the denominator column.
        descending: Sort direction.

    Returns:
        LazyFrame of ``dim``, the two counts, and the rate. Dimension values with no
        matches are kept with a zero numerator rather than dropped.
    """
    total_col = total_col or f"{prefix}_total"
    matched_col = matched_col or f"{prefix}_matched"
    rate_col = rate_col or f"{prefix}_match_rate"
    dims = _cols(dim)
    source = _lazy(source)

    totals = source.group_by(dims).agg(pl.len().alias(total_col))
    hits = (
        source.join(_lazy(matched), on=_cols(on), how="inner")
        .group_by(dims)
        .agg(pl.len().alias(matched_col))
    )

    # Fill before dividing. Expressions inside one with_columns all see the *input*
    # frame, so filling the numerator in the same call the rate is computed in would
    # leave a zero-match group with matched=0 but match_rate=null. A group with no
    # matches has a match rate of zero.
    filled = pl.col(matched_col).fill_null(0)
    return (
        totals.join(hits, on=dims, how="left")
        .with_columns(filled.alias(matched_col), (filled / pl.col(total_col)).alias(rate_col))
        .sort(sort_by or total_col, descending=descending)
    )


@dataclass(frozen=True)
class Side:
    """One side of a crosswalk, as a validation measures it.

    The universe must hold one row per loan: every rate counts universe rows, so a
    loan that appears twice counts twice in the denominator and, when matched, twice
    in the numerator. Construction checks this and raises rather than let a fanned-out
    universe (a matcher's candidate table, a per-ZIP pool) skew every rate quietly.
    Rows whose key is null are exempt -- they can never join to a match, so they
    count once each as unmatched loans.

    Attributes:
        universe: The side's loan universe -- the denominator.
        matched: Its distinct matched identifiers, from `matched_ids`.
        on: The identifier column, or composite key, in ``universe``.
        prefix: Output column prefix (``fha`` gives ``fha_total`` / ``fha_matched`` /
            ``fha_match_rate``).
    """

    universe: pl.DataFrame | pl.LazyFrame
    matched: pl.LazyFrame
    on: str | Sequence[str]
    prefix: str

    def __post_init__(self) -> None:
        """Reject a universe with more than one row for any non-null key.

        Raises:
            ValueError: If a key value appears on more than one universe row.
        """
        keys = _cols(self.on)
        keyed = _lazy(self.universe).select(keys).drop_nulls()
        rows, distinct = keyed.select(pl.len(), pl.struct(keys).n_unique()).collect().row(0)
        if rows == distinct:
            return
        examples = (
            keyed.group_by(keys).agg(pl.len().alias("rows")).filter(pl.col("rows") > 1).head(3)
        ).collect()
        raise ValueError(
            f"Side '{self.prefix}': universe has {rows:,} keyed rows but {distinct:,} "
            f"distinct {keys} -- collapse it to one row per loan before measuring. "
            f"Examples:\n{examples}"
        )

    def prepared(self, fn: Callable[[pl.LazyFrame], pl.LazyFrame]) -> Side:
        """This side with ``fn`` applied to its universe (a filter, a bin column).

        The transformed universe is checked like any other, so an ``fn`` that fans rows
        out (a join) is caught here.

        Args:
            fn: Transformation of the universe, applied lazily.

        Returns:
            A new side over the transformed universe.
        """
        return replace(self, universe=fn(_lazy(self.universe)))


def matched_count(side: Side) -> int:
    """How many of a side's universe rows are matched -- the headline numerator.

    Counted over universe rows joined to the matched ids, exactly as `rate_by` counts
    them, so the headline equals the sum of any per-dimension frame's matched column.
    Counting against the universe rather than the crosswalk also means a crosswalk
    built over a wider population than the universe loaded here cannot push the rate
    past 100%.

    Args:
        side: The side to count.

    Returns:
        Universe rows whose identifier is matched.
    """
    return int(
        _lazy(side.universe)
        .join(side.matched, on=_cols(side.on), how="inner")
        .select(pl.len())
        .collect()
        .item()
    )


def rate_by_sides(
    sides: Sequence[Side],
    dim: str | Sequence[str],
    *,
    prepare: Callable[[pl.LazyFrame], pl.LazyFrame] | None = None,
    sort_by: str | Sequence[str] | None = None,
    descending: bool = False,
) -> pl.DataFrame:
    """Every side's match rate over one dimension, joined on the dimension values.

    A match rate only means something against a stated denominator, and these
    workflows almost always want both sides. ``dim`` must carry the same values on
    every side; use ``prepare`` (or `Side.prepared` when the sides differ) to derive
    it.

    Args:
        sides: The sides to measure, in output-column order.
        dim: Dimension column, or columns, present on every prepared universe.
        prepare: Transformation applied to every side's universe first -- typically
            a filter and a `binned` column.
        sort_by: Column(s) to sort the result by; defaults to ``dim``.
        descending: Sort direction.

    Returns:
        One row per dimension value, carrying each side's counts and rate. A side
        with no loans at a value has nulls there.
    """
    dims = _cols(dim)
    frames = [
        rate_by(
            prepare(_lazy(side.universe)) if prepare else side.universe,
            side.matched,
            side.on,
            dims,
            prefix=side.prefix,
        )
        for side in sides
    ]
    out = frames[0]
    for frame in frames[1:]:
        out = out.join(frame, on=dims, how="full", coalesce=True)
    return out.sort(_cols(sort_by) if sort_by else dims, descending=descending).collect()


def overall_rate(df: pl.DataFrame, matched_col: str, total_col: str) -> float:
    """The pooled match rate behind a per-period frame: summed matched over summed total.

    Args:
        df: Frame carrying the two count columns.
        matched_col: Numerator column.
        total_col: Denominator column.

    Returns:
        The pooled rate, or NaN when the denominator is zero.
    """
    total = float(df[total_col].sum())
    return float(df[matched_col].sum()) / total if total else float("nan")


# =============================================================================
# Choropleths
# =============================================================================


@dataclass(frozen=True)
class StatePanel:
    """One choropleth panel: postal codes and the rate to shade each one."""

    title: str
    states: list[str] = field(default_factory=list)
    rates: list[float] = field(default_factory=list)


def plot_state_choropleth(
    panels: Sequence[StatePanel],
    title: str,
    output_path: Path | None = None,
    *,
    color_floor: float = 0.0,
    color_pad: float = 0.02,
    colorbar_title: str = "Match Rate",
) -> None:
    """Write a one- or two-panel US state choropleth via plotly.

    Panels share one color scale so they are visually comparable -- the range spans
    every panel's rates, clamped below by ``color_floor`` and padded by ``color_pad``.

    Args:
        panels: One or two panels; each supplies its own subplot title.
        title: Figure title.
        output_path: Destination PNG. None renders nothing (the figure is not shown).
        color_floor: Lower clamp on the color range, to keep a narrow spread readable.
        color_pad: Padding added either side of the observed range.
        colorbar_title: Legend label.
    """
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        print("plotly not available, skipping state map")
        return

    rates = [r for p in panels for r in p.rates if r is not None]
    if not rates:
        print(f"No state data available for mapping: {title}")
        return

    zmin = max(color_floor, min(rates) - color_pad)
    zmax = min(1.0, max(rates) + color_pad)
    single = len(panels) == 1

    def trace(panel: StatePanel, colorbar: dict[str, Any]) -> Any:
        return go.Choropleth(
            locations=panel.states,
            z=panel.rates,
            locationmode="USA-states",
            colorscale="RdYlGn",
            zmin=zmin,
            zmax=zmax,
            colorbar=colorbar,
            hovertemplate="<b>%{location}</b><br>" + colorbar_title + ": %{z:.1%}<extra></extra>",
        )

    if single:
        fig = go.Figure(trace(panels[0], dict(title=colorbar_title, tickformat=".0%")))
    else:
        fig = make_subplots(
            rows=1,
            cols=len(panels),
            subplot_titles=tuple(p.title for p in panels),
            specs=[[{"type": "choropleth"}] * len(panels)],
            horizontal_spacing=0.02,
        )
        for i, panel in enumerate(panels, start=1):
            bar = dict(
                title=colorbar_title,
                x=0.45 if i == 1 else 1.0,
                len=0.8,
                tickformat=".0%",
            )
            fig.add_trace(trace(panel, bar), row=1, col=i)

    fig.update_geos(scope="usa", showlakes=False, showland=True, landcolor="lightgray")
    fig.update_layout(
        title_text=title,
        title_x=0.5,
        width=1000 if single else 1400,
        height=600,
        margin=dict(l=0, r=0, t=60, b=0),
    )

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.write_image(str(output_path), scale=2)
        print(f"Saved: {output_path}")


# =============================================================================
# Shared statistics
# =============================================================================


def analyze_state_size_correlation(
    state_df: pl.DataFrame, loan_col: str, rate_col: str = "match_rate"
) -> dict[str, Any]:
    """Analyze correlation between state size (number of loans) and match rates.

    Args:
        state_df: State-level frame carrying ``rate_col`` and ``loan_col``.
        loan_col: Column holding each state's loan count.
        rate_col: Column holding each state's match rate.

    Returns:
        Correlation statistics, or ``{"error": ...}`` when fewer than three states have
        non-null values.
    """
    from scipy.stats import spearmanr

    x = state_df[loan_col].to_numpy().astype(float)
    y = state_df[rate_col].to_numpy()

    mask = ~(np.isnan(x) | np.isnan(y))
    x, y = x[mask], y[mask]

    if len(x) < 3:
        return {"error": "Not enough data points"}

    pearson_r = np.corrcoef(x, y)[0, 1]

    # Log-transformed Pearson (better for this relationship)
    log_x = np.log10(x + 1)
    log_pearson_r = np.corrcoef(log_x, y)[0, 1]

    # Spearman rank correlation (robust to outliers)
    spearman_r, spearman_p = spearmanr(x, y)

    # Slope of the log-linear fit, in percentage points of match rate per 10x volume.
    # This is the number a reader needs to size the bias, and hardcoding it in prose
    # is how it goes stale.
    log_slope_pp = float(np.polyfit(log_x, y, 1)[0]) * 100

    return {
        "n_states": len(x),
        "pearson_r": pearson_r,
        "log_pearson_r": log_pearson_r,
        "spearman_r": spearman_r,
        "spearman_p": spearman_p,
        "log_slope_pp": log_slope_pp,
        "min_match_rate": y.min(),
        "max_match_rate": y.max(),
        "mean_match_rate": y.mean(),
        "std_match_rate": y.std(),
    }
