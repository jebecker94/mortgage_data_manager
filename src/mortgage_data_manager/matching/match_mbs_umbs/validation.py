"""MBS Match Validation Module.

This module provides validation analyses for the MBS matching workflow for both
FNMA and FHLMC MBS-UMBS matching. This workflow links the same GSE loans across two
different disclosure formats:
- MBS: Monthly disclosure data
- UMBS: Loan-level security disclosure (ILLD)

Key validation analyses:
1. Match Statistics Summary
2. Temporal Analysis (match/unmatched rates by month)
3. Match Rates by Interest Rate, FICO, LTV, DTI (interesting behavior at extremes expected)

Output is a single combined figure (``mbs_umbs_match_rates_panel.png``): five rows
(months, interest rate, FICO, LTV, DTI) by two columns (FNMA, FHLMC), with the
characteristic panels showing both the greyed-out loan distribution and the standard
underwriting cut points.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, cast

import numpy as np
import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.matching.match_mbs_umbs.config import MBSUMBSMatchingConfig as Config
from mortgage_data_manager.matching.validation_common import (
    COLORS,
    HAS_MATPLOTLIB,
    Side,
    binned,
    get_pyplot,
    matched_ids,
    print_section,
    rate_by_sides,
    save_and_close,
)

if TYPE_CHECKING:
    from collections.abc import Callable

# First month of ILLD issuance disclosure; pre-this loans come from the monthly snapshot.
ISSUANCE_START_YM = Config.UMBS_ISSUANCE_START[:7]  # "2019-06"

# =============================================================================
# Configuration
# =============================================================================

# Project directories
PROJECT_DIR = MortgageDataConfig.PROJECT_DIR
DATA_DIR = Config.MBS_UMBS_CROSSWALK_DIR

# FNMA data paths
FNMA_MBS_DIR = MortgageDataConfig.get_subpackage_data_dir("fnma") / "silver" / "issuances"
# Silver, as the matcher reads it: one file per month, with any R-prefixed reissue already
# merged in. Bronze also holds a revised-month file beside its original (FNM_ILLD_202507 and
# _202507_REV), so globbing bronze counts that month's loans twice.
FNMA_UMBS_DIR = Config.get_umbs_illd_dir("fnma")

# FHLMC data paths
FHLMC_CONFIG = Config.get_fhlmc_config()
FHLMC_MBS_DIR = FHLMC_CONFIG.FHLMC_SILVER_DIR / "origination"
FHLMC_UMBS_DIR = Config.get_umbs_illd_dir("fhlmc")

# Output directory for figures
VALIDATION_OUTPUT_DIR = PROJECT_DIR / "docs" / "matching" / "figures"


def get_gse_paths(gse: str) -> tuple[Path, Path, Path]:
    """Get MBS dir, UMBS dir, and crosswalk path for the specified GSE.

    Args:
        gse: Either "fnma" or "fhlmc"

    Returns:
        Tuple of (mbs_dir, umbs_dir, crosswalk_path)
    """
    gse = gse.lower()
    if gse == "fnma":
        return (
            FNMA_MBS_DIR,
            FNMA_UMBS_DIR,
            Config.MBS_UMBS_CROSSWALK_DIR / "mbs_umbs_crosswalk_fnma.parquet",
        )
    elif gse == "fhlmc":
        return (
            FHLMC_MBS_DIR,
            FHLMC_UMBS_DIR,
            Config.MBS_UMBS_CROSSWALK_DIR / "mbs_umbs_crosswalk_fhlmc.parquet",
        )
    else:
        raise ValueError(f"Unknown GSE: {gse}. Must be 'fnma' or 'fhlmc'")


# Color palette
HUNTER_GREEN = COLORS["hunter"]
BURNISHED_GOLD = COLORS["gold"]
DEEP_TEAL = COLORS["teal"]
BLUE_GREY = COLORS["grey"]
SLATE = COLORS["slate"]

# Dataset-specific colors (primary: Hunter Green & Gold)
MBS_COLOR = HUNTER_GREEN
UMBS_COLOR = BURNISHED_GOLD
DENSITY_COLOR = BLUE_GREY

# =============================================================================
# Data Loading
# =============================================================================


def load_crosswalk(gse: str = "fnma") -> pl.DataFrame:
    """Load the MBS-UMBS crosswalk for the specified GSE.

    Args:
        gse: Either "fnma" or "fhlmc"

    Returns:
        That GSE's MBS-UMBS crosswalk.
    """
    _, _, crosswalk_path = get_gse_paths(gse)
    if not crosswalk_path.exists():
        raise FileNotFoundError(
            f"Crosswalk not found: {crosswalk_path}\n"
            f"Run the matching first: mortgage-data match mbs-umbs {gse}"
        )
    return pl.read_parquet(crosswalk_path)


def scan_mbs_universe(gse: str) -> pl.LazyFrame:
    """Scan every loan the matcher's MBS side sees, as one frame.

    For Freddie that is the quarterly origination files alone. For Fannie it is those
    plus the separately-released HARP/Refi Plus population, whose silver layout is
    column- and dtype-identical, so it unions directly. Validation has to read the same
    universe the matcher matched or the denominator omits loans the crosswalk contains.

    Args:
        gse: ``"fnma"`` or ``"fhlmc"``.

    Returns:
        LazyFrame over the GSE's full MBS-side universe, source columns unrenamed.
    """
    mbs_dir, _, _ = get_gse_paths(gse)
    lf = pl.scan_parquet(
        str(mbs_dir / "**/*.parquet"), missing_columns="insert", extra_columns="ignore"
    )
    harp_file = Config.get_mbs_harp_file(gse)
    if harp_file is not None:
        harp = pl.scan_parquet(str(harp_file), missing_columns="insert", extra_columns="ignore")
        lf = pl.concat([lf, harp], how="diagonal_relaxed")
    return lf


def load_mbs_data(gse: str = "fnma") -> pl.LazyFrame:
    """Load MBS data with key columns for analysis.

    Args:
        gse: Either "fnma" or "fhlmc"

    Returns:
        LazyFrame with standardized column names: Loan Identifier, LTV, CLTV, DTI, FICO
    """
    mbs_dir, _, _ = get_gse_paths(gse)

    if not mbs_dir.exists():
        raise FileNotFoundError(f"MBS data not found: {mbs_dir}")

    if gse.lower() == "fnma":
        # FNMA column names
        cols = [
            "Loan Identifier",
            "Monthly Reporting Period",
            "First Payment Date",
            "Original Loan to Value Ratio (LTV)",
            "Original Combined Loan to Value Ratio (CLTV)",
            "Debt-To-Income (DTI)",
            "Borrower Credit Score at Origination",
            "Original Interest Rate",
            "Original UPB",
            "Origination Date",
            "Property State",
        ]
        rename_map = {
            "Original Loan to Value Ratio (LTV)": "LTV",
            "Original Combined Loan to Value Ratio (CLTV)": "CLTV",
            "Debt-To-Income (DTI)": "DTI",
            "Borrower Credit Score at Origination": "FICO",
            "Original Interest Rate": "Rate",
        }
    else:  # fhlmc
        # FHLMC column names
        cols = [
            "Loan Sequence Number",
            "First Payment Date",
            "Original Loan-to-Value (LTV)",
            "Original Combined Loan-to-Value (CLTV)",
            "Original Debt-to-Income (DTI) Ratio",
            "Credit Score",
            "Original Interest Rate",
            "Original UPB",
            "Property State",
        ]
        rename_map = {
            "Loan Sequence Number": "Loan Identifier",
            "Original Loan-to-Value (LTV)": "LTV",
            "Original Combined Loan-to-Value (CLTV)": "CLTV",
            "Original Debt-to-Income (DTI) Ratio": "DTI",
            "Credit Score": "FICO",
            "Original Interest Rate": "Rate",
        }

    lf = scan_mbs_universe(gse)

    # Get available columns
    available = set(lf.collect_schema().names())
    use_cols = [c for c in cols if c in available]

    lf = lf.select(use_cols).rename(rename_map)
    # Characteristic columns must be numeric for binning; dtype can drift to string
    # across import-layer rebuilds.
    lf = lf.with_columns(
        [
            pl.col(c).cast(pl.Float64, strict=False)
            for c in ("LTV", "CLTV", "DTI", "FICO", "Rate")
            if c in lf.collect_schema().names()
        ]
    )

    # Normalized First Payment year-month ("YYYY-MM") so non-temporal validation stats can be
    # restricted to the clean full-data window (First Payment Date >= 2019-06). The column's
    # dtype drifts with silver rebuilds (packed MMYYYY string vs pl.Date), so branch on the
    # actual dtype, not the GSE.
    schema = lf.collect_schema()
    if "First Payment Date" in schema.names():
        if schema["First Payment Date"] == pl.Date:
            ym = pl.col("First Payment Date").dt.strftime("%Y-%m")
        else:
            fpd = pl.col("First Payment Date").cast(pl.Utf8).str.zfill(6)
            ym = fpd.str.slice(2, 4) + "-" + fpd.str.slice(0, 2)
        lf = lf.with_columns(ym.alias("first_payment_ym"))

    return lf


def load_umbs_data(gse: str = "fnma") -> pl.DataFrame:
    """Load UMBS ILLD data with key columns for analysis.

    Args:
        gse: Either "fnma" or "fhlmc"

    Returns:
        DataFrame (not LazyFrame) due to schema inconsistencies across files, one row per
        loan, with columns Loan Identifier, year_month, LTV, CLTV, DTI, FICO, Rate.

    Filters:
    - Excludes modifications (Loan Purpose = 'M')
    - Combines the post-2019 ILLD population with the first monthly snapshot (FNM_MLLD / FU),
      mirroring the matcher's universe: ILLD keeps First Payment Date >= 2019-06; the snapshot
      contributes every loan NOT already in ILLD (anti-join, ILLD preferred) — the pre-2019
      cohorts plus the 2019-H2 seam cohort (FPD >= 2019-06 in pools issued before June 2019).
    - A loan disclosed in more than one ILLD month (~2.4k Fannie loans from 2019-20 reappear
      in later files) keeps one row when the copies are identical and is dropped when they
      differ, as the matcher's ``drop_conflicting_duplicates`` drops it.
    """
    _, umbs_dir, _ = get_gse_paths(gse)

    if not umbs_dir.exists():
        raise FileNotFoundError(f"UMBS data not found: {umbs_dir}")

    # ILLD monthly files (post-2019) plus the first monthly snapshot (pre-2019 recovery). Each
    # source is tagged so we can apply the complementary First Payment Date filter below.
    sources = [(f, False) for f in sorted(umbs_dir.glob("*.parquet"))]
    snapshot_file = Config.get_umbs_snapshot_file(gse)
    if snapshot_file is not None:
        sources.append((snapshot_file, True))
    files = sources

    # Columns we want to load (same for both FNMA and FHLMC)
    cols = [
        "Loan Identifier",
        "First Payment Date",
        "Loan-To-Value (LTV)",
        "Combined Loan-To-Value (CLTV)",
        "Debt-To-Income (DTI)",
        "Borrower Credit Score",
        "Original Interest Rate",
        "Loan Purpose",  # For filtering out modifications
    ]

    # Expected final columns with standardized names
    final_cols = [
        "Loan Identifier",
        "year_month",
        "LTV",
        "CLTV",
        "DTI",
        "FICO",
        "Rate",
    ]

    illd_dfs: list[pl.DataFrame] = []
    snapshot_dfs: list[pl.DataFrame] = []
    for file, is_snapshot in files:
        df = pl.read_parquet(file)

        # Select columns that exist
        available = [c for c in cols if c in df.columns]
        if "Loan Identifier" not in available or "First Payment Date" not in available:
            continue

        df_subset = df.select(available)

        # Filter out modifications (Loan Purpose = 'M')
        if "Loan Purpose" in df_subset.columns:
            df_subset = df_subset.filter(
                (pl.col("Loan Purpose") != "M") | pl.col("Loan Purpose").is_null()
            )

        # Silver stores First Payment Date as pl.Date for both GSEs; report it as "YYYY-MM".
        df_subset = df_subset.with_columns(
            pl.col("First Payment Date").dt.strftime("%Y-%m").alias("year_month")
        ).drop("First Payment Date")

        # ILLD keeps its own coverage window; the snapshot is NOT date-filtered — it is
        # deduplicated against ILLD below (matcher parity: ILLD preferred, snapshot adds the
        # pre-2019 population plus the 2019-H2 seam cohort).
        if not is_snapshot:
            df_subset = df_subset.filter(pl.col("year_month") >= ISSUANCE_START_YM)

        # Drop Loan Purpose if it exists (no longer needed)
        if "Loan Purpose" in df_subset.columns:
            df_subset = df_subset.drop("Loan Purpose")

        # Rename columns to standardized names (only rename columns that exist)
        rename_map = {
            "Loan-To-Value (LTV)": "LTV",
            "Combined Loan-To-Value (CLTV)": "CLTV",
            "Debt-To-Income (DTI)": "DTI",
            "Borrower Credit Score": "FICO",
            "Original Interest Rate": "Rate",
        }
        rename_map = {k: v for k, v in rename_map.items() if k in df_subset.columns}
        df_subset = df_subset.rename(rename_map)

        # Ensure all files have same columns (with nulls for missing ones)
        for col in final_cols:
            if col not in df_subset.columns:
                df_subset = df_subset.with_columns(pl.lit(None).alias(col))

        # Characteristic columns are binned as Float64; silver stores them as Int16/Float32,
        # and the whole-row dedup below needs one dtype per column across files.
        df_subset = df_subset.with_columns(
            [
                pl.col(c).cast(pl.Float64, strict=False)
                for c in ("LTV", "CLTV", "DTI", "FICO", "Rate")
            ]
        )

        (snapshot_dfs if is_snapshot else illd_dfs).append(df_subset.select(final_cols))

    illd = pl.concat(illd_dfs, how="diagonal")
    if snapshot_dfs:
        snapshot = pl.concat(snapshot_dfs, how="diagonal").join(
            illd.select("Loan Identifier").unique(), on="Loan Identifier", how="anti"
        )
        illd = pl.concat([illd, snapshot], how="diagonal")
    # Identical re-disclosures collapse (whole-row unique picks nothing); a loan whose
    # copies still differ is dropped rather than counted under one copy's values.
    return illd.unique().unique(subset=["Loan Identifier"], keep="none")


# =============================================================================
# Sides and dimensions
# =============================================================================

MBS_ID = "Loan Sequence Number (MBS)"
UMBS_ID = "Loan Sequence Number (UMBS)"


def sides(crosswalk: pl.DataFrame, mbs: pl.LazyFrame, umbs: pl.DataFrame) -> tuple[Side, Side]:
    """The two measured sides, keyed on ``Loan Identifier`` as a string.

    UMBS stores its loan identifier as Int64 while the crosswalk stores strings, so
    the cast is load-bearing: drop it and every UMBS loan reads as unmatched.

    Args:
        crosswalk: The MBS-UMBS crosswalk.
        mbs: The MBS side's loan rows (monthly, so a loan repeats).
        umbs: The UMBS side's loan universe.

    Returns:
        ``(mbs_side, umbs_side)``.
    """
    as_text = pl.col("Loan Identifier").cast(pl.Utf8)
    return (
        Side(
            mbs.with_columns(as_text),
            matched_ids(crosswalk, MBS_ID, as_cols="Loan Identifier"),
            "Loan Identifier",
            "mbs",
        ),
        Side(
            umbs.lazy().with_columns(as_text),
            matched_ids(crosswalk, UMBS_ID, as_cols="Loan Identifier"),
            "Loan Identifier",
            "umbs",
        ),
    )


class Characteristic(NamedTuple):
    """One loan characteristic the report bins match rates over.

    Attributes:
        key: Results key (``{key}_rates``).
        column: The standardized characteristic column on both sides.
        bin_size: Bin width, in the column's units.
        lo: Smallest value included.
        hi: Largest value included.
        xlabel: Axis label.
        cut_points: Standard underwriting thresholds drawn as dashed lines.
    """

    key: str
    column: str
    bin_size: float
    lo: float
    hi: float
    xlabel: str
    cut_points: tuple[float, ...] = ()


CHARACTERISTICS = (
    Characteristic("rate", "Rate", 0.125, 1.5, 8.5, "Interest Rate (%)"),
    Characteristic("fico", "FICO", 1, 580, 850, "FICO Score", (620, 740)),
    Characteristic("ltv", "LTV", 1, 20, 105, "LTV (%)", (80, 95)),
    Characteristic("dti", "DTI", 1, 0, 65, "DTI (%)", (43, 50)),
)


def _one_row_per_loan(column: str) -> Callable[[pl.LazyFrame], pl.LazyFrame]:
    """Collapse monthly rows to one per loan carrying ``column``; a null month is not a value."""
    return lambda lf: (
        lf.filter(pl.col(column).is_not_null())
        .select("Loan Identifier", column)
        .unique(subset=["Loan Identifier"])
    )


# =============================================================================
# Statistics
# =============================================================================


def compute_match_statistics(
    crosswalk: pl.DataFrame, mbs_data: pl.LazyFrame, umbs_data: pl.DataFrame
) -> dict[str, Any]:
    """Overall match statistics within the provided (already windowed) data.

    Match counts are the intersection of the provided loans with the crosswalk, so the
    rate is matched-in-window / total-in-window. Callers pass clean-window data (First
    Payment Date >= 2019-06) for the headline stats, giving an interpretable denominator.

    Args:
        crosswalk: The MBS-UMBS crosswalk.
        mbs_data: The MBS side's loan rows (monthly; deduplicated here).
        umbs_data: The UMBS side's loan universe.

    Returns:
        Headline counts and the match rate from each side.
    """
    matched_mbs = set(crosswalk[MBS_ID].to_list())
    matched_umbs = set(crosswalk[UMBS_ID].to_list())
    mbs_ids = (
        mbs_data.select(pl.col("Loan Identifier").cast(pl.Utf8))
        .unique()
        .collect()["Loan Identifier"]
    )
    umbs_ids = umbs_data.select(pl.col("Loan Identifier").cast(pl.Utf8)).unique()["Loan Identifier"]
    n_total_mbs, n_total_umbs = mbs_ids.len(), umbs_ids.len()
    n_matched_mbs, n_matched_umbs = (
        int(mbs_ids.is_in(matched_mbs).sum()),
        int(umbs_ids.is_in(matched_umbs).sum()),
    )
    return {
        # 1:1 matches, so matched MBS loans in window == pairs in window.
        "n_matched_pairs": n_matched_mbs,
        "n_matched_mbs": n_matched_mbs,
        "n_matched_umbs": n_matched_umbs,
        "n_total_mbs": n_total_mbs,
        "n_total_umbs": n_total_umbs,
        "mbs_match_rate": n_matched_mbs / n_total_mbs if n_total_mbs else 0,
        "umbs_match_rate": n_matched_umbs / n_total_umbs if n_total_umbs else 0,
    }


def compute_match_rates_by_characteristic(
    both: tuple[Side, Side], characteristic: Characteristic
) -> pl.DataFrame:
    """Both sides' match rates across one binned loan characteristic.

    Args:
        both: The two sides, from `sides`.
        characteristic: Which characteristic, with its bin width and range.

    Returns:
        One row per bin (column ``bin``) the MBS side populates -- its density is the
        panel's background -- with each side's counts and rate.
    """
    col = characteristic.column
    return rate_by_sides(
        both,
        "bin",
        prepare=lambda lf: (
            _one_row_per_loan(col)(lf)
            .filter(pl.col(col).is_between(characteristic.lo, characteristic.hi))
            .with_columns(binned(col, characteristic.bin_size, name="bin"))
        ),
    ).filter(pl.col("bin").is_not_null() & pl.col("mbs_total").is_not_null())


def _mbs_first_payment_months(gse: str) -> pl.LazyFrame:
    """MBS loans with their First Payment year-month, one row per loan.

    Re-read from the issuance files with just the two columns: the temporal axis is
    the origination cohort, which is where the pre-2019 survivorship gradient shows.
    The date dtype drifts with silver rebuilds (packed MMYYYY string vs pl.Date), so
    branch on the actual dtype.
    """
    id_col = "Loan Identifier" if gse == "fnma" else "Loan Sequence Number"
    lf = (
        scan_mbs_universe(gse)
        .select([id_col, "First Payment Date"])
        .rename({id_col: "Loan Identifier"})
        .unique()
    )
    if lf.collect_schema()["First Payment Date"] == pl.Date:
        ym = pl.col("First Payment Date").dt.strftime("%Y-%m")
    else:
        fpd = pl.col("First Payment Date").cast(pl.Utf8).str.zfill(6)
        ym = fpd.str.slice(2, 4) + "-" + fpd.str.slice(0, 2)
    return lf.with_columns(ym.alias("year_month"))


def compute_match_rates_by_month(
    crosswalk: pl.DataFrame, umbs_data: pl.DataFrame, gse: str = "fnma"
) -> pl.DataFrame:
    """Both sides' match rates by First Payment month, over months both sides carry.

    Args:
        crosswalk: The MBS-UMBS crosswalk.
        umbs_data: UMBS loans with ``year_month``, from `load_umbs_data`.
        gse: Either "fnma" or "fhlmc".

    Returns:
        One row per month, chronological, with ``first_payment_date`` (``YYYY-MM-01``)
        for the figure's axis.
    """
    mbs_side, umbs_side = sides(
        crosswalk,
        _mbs_first_payment_months(gse.lower()),
        umbs_data,
    )
    return (
        rate_by_sides((mbs_side, umbs_side), "year_month")
        .filter(pl.col("mbs_total").is_not_null() & pl.col("umbs_total").is_not_null())
        .with_columns((pl.col("year_month") + "-01").alias("first_payment_date"))
    )


def compute_mbs_coverage_end(
    monthly_data: pl.DataFrame, threshold: float = 0.95, min_umbs: int = 5_000
) -> str:
    """Last First-Payment month with full MBS-side coverage (the analysis window end).

    The MBS SF loan-level files are released on a longer lag than UMBS ILLD, so the
    UMBS side always frontruns the MBS side: the final MBS months are only partially
    populated and their match rates are recency artifacts, not matcher quality. The
    window end is the last month (>= 2019-06) where the MBS-side loan count keeps pace
    with the UMBS side. Empirically the ratio sits at ~0.98-1.00 and then falls off a
    cliff, so the cut is insensitive to the threshold.

    Args:
        monthly_data: Output of `compute_match_rates_by_month`.
        threshold: Minimum MBS/UMBS monthly count ratio to call a month fully covered.
        min_umbs: Minimum UMBS loans in the month for the ratio to be meaningful.

    Returns:
        The last fully-covered month as ``"YYYY-MM"``.
    """
    ok = monthly_data.filter(
        (pl.col("year_month") >= ISSUANCE_START_YM)
        & (pl.col("umbs_total") >= min_umbs)
        & (pl.col("mbs_total") >= threshold * pl.col("umbs_total"))
    )
    return ISSUANCE_START_YM if ok.is_empty() else str(ok["year_month"].max())


# =============================================================================
# Figure
# =============================================================================


GSE_LABEL = {"fnma": "Fannie Mae (FNMA)", "fhlmc": "Freddie Mac (FHLMC)"}


def _plot_month_panel(ax, monthly_data: pl.DataFrame, coverage_end: str) -> None:
    """One panel of match rates by First Payment Date month (both perspectives).

    The full-coverage analysis window (2019-06 through the agency's MBS coverage end)
    is shaded in the background, recession-shading style — the characteristic panels
    below are computed on exactly this window.
    """
    dates: list[str] = []
    mbs_rates: list[float | None] = []
    umbs_rates: list[float | None] = []
    for row in monthly_data.iter_rows(named=True):
        if row.get("first_payment_date") is None:
            continue
        dates.append(str(row["first_payment_date"]))
        mbs = row.get("mbs_match_rate")
        umbs = row.get("umbs_match_rate")
        mbs_rates.append(mbs * 100 if mbs is not None else None)
        umbs_rates.append(umbs * 100 if umbs is not None else None)
    if not dates:
        return

    # shade the analysis window (dates are sorted "YYYY-MM-01" strings, plotted
    # categorically, so window edges map to list positions)
    window = [
        i for i, d in enumerate(dates) if f"{ISSUANCE_START_YM}-01" <= d <= f"{coverage_end}-01"
    ]
    if window:
        ax.axvspan(window[0] - 0.5, window[-1] + 0.5, color=DEEP_TEAL, alpha=0.10, linewidth=0)

    ax.plot(dates, mbs_rates, color=MBS_COLOR, linewidth=1.6)
    ax.plot(dates, umbs_rates, color=UMBS_COLOR, linewidth=1.6)
    step = max(1, len(dates) // 10)
    ax.set_xticks(range(0, len(dates), step))
    ax.set_xticklabels(
        [dates[i] for i in range(0, len(dates), step)], rotation=45, ha="right", fontsize=8
    )
    ax.set_xlabel("First Payment Date")
    ax.set_ylim(80, 100.5)


def _plot_characteristic_panel(
    ax, rates_df: pl.DataFrame, xlabel: str, cut_points: tuple[float, ...]
) -> None:
    """One panel of match rates by a loan characteristic.

    Combines the two previous figure styles: the loan distribution greyed out in the
    background (MBS totals, right axis without ticks — context only) and dashed
    vertical lines at the standard underwriting cut points.
    """
    x = np.array(rates_df["bin"].to_list())
    mbs_y = np.array(
        [r * 100 if r is not None else np.nan for r in rates_df["mbs_match_rate"].to_list()]
    )
    umbs_y = np.array(
        [r * 100 if r is not None else np.nan for r in rates_df["umbs_match_rate"].to_list()]
    )
    totals = np.array(rates_df["mbs_total"].to_list(), dtype=float)

    ax2 = ax.twinx()
    pdf = totals / totals.sum() if totals.sum() else totals
    ax2.fill_between(x, pdf, alpha=0.2, color=DENSITY_COLOR, linewidth=0)
    ax2.set_ylim(0, max(float(pdf.max()), 1e-9) * 1.6)
    ax2.set_yticks([])

    for cp in cut_points:
        ax.axvline(cp, color=SLATE, linestyle="--", linewidth=1.0, alpha=0.7)

    ax.plot(x, mbs_y, color=MBS_COLOR, linewidth=1.5)
    ax.plot(x, umbs_y, color=UMBS_COLOR, linewidth=1.5)
    ax.set_xlabel(xlabel)
    valid = np.concatenate([mbs_y[~np.isnan(mbs_y)], umbs_y[~np.isnan(umbs_y)]])
    if valid.size:
        ax.set_ylim(max(0, valid.min() - 5), min(100.0, valid.max() + 5) + 0.5)
    # rates draw above the background density
    ax.set_zorder(ax2.get_zorder() + 1)
    ax.patch.set_visible(False)


def plot_match_rates_panel(
    results: dict[str, dict[str, Any]],
    output_path: Path | None = None,
    interactive: bool = False,
) -> None:
    """Render the combined validation figure: 5 rows x 2 columns.

    Columns are FNMA (left) and FHLMC (right); rows are match rates by First Payment
    Date month, interest rate, FICO, LTV, and DTI. The characteristic panels carry
    both the greyed-out loan distribution and the dashed underwriting cut points.

    Args:
        results: ``{"fnma": ..., "fhlmc": ...}`` as returned by the per-GSE
            computation, each carrying ``monthly_data`` and the ``*_rates`` frames.
        output_path: Where to save the figure (PNG). Skipped if ``None``.
        interactive: Whether to display the figure interactively.
    """
    plt = get_pyplot(interactive=interactive)
    if plt is None:
        print("matplotlib not available, skipping plots")
        return

    fig, axes = plt.subplots(5, 2, figsize=(14, 18))
    for col, gse in enumerate(("fnma", "fhlmc")):
        r = results[gse]
        coverage_end = r.get("coverage_end", ISSUANCE_START_YM)
        ax = axes[0][col]
        _plot_month_panel(ax, r["monthly_data"], coverage_end)
        ax.set_title(
            f"{GSE_LABEL[gse]} — by First Payment Date"
            f" (window: {ISSUANCE_START_YM} .. {coverage_end})",
            fontsize=11,
        )
        ax.set_ylabel("Match Rate (%)")
        ax.grid(True, alpha=0.3)
        for row_i, c in enumerate(CHARACTERISTICS, start=1):
            ax = axes[row_i][col]
            _plot_characteristic_panel(ax, r[f"{c.key}_rates"], c.xlabel, c.cut_points)
            ax.set_title(f"{GSE_LABEL[gse]} — by {c.xlabel.split(' (')[0]}", fontsize=11)
            ax.set_ylabel("Match Rate (%)")
            ax.grid(True, alpha=0.3)

    handles = [
        plt.Line2D([], [], color=MBS_COLOR, linewidth=2, label="MBS match rate"),
        plt.Line2D([], [], color=UMBS_COLOR, linewidth=2, label="UMBS match rate"),
        plt.Rectangle(
            (0, 0), 1, 1, color=DEEP_TEAL, alpha=0.10, label="full-coverage analysis window"
        ),
        plt.Rectangle(
            (0, 0), 1, 1, color=DENSITY_COLOR, alpha=0.2, label="loan distribution (MBS)"
        ),
        plt.Line2D(
            [], [], color=SLATE, linestyle="--", linewidth=1.2, label="underwriting cut points"
        ),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=5, frameon=False, fontsize=10)
    fig.suptitle(
        "MBS-UMBS match-rate validation"
        " (characteristic panels use the shaded full-coverage window)",
        fontsize=13,
    )
    fig.tight_layout(rect=(0, 0.02, 1, 0.985))

    save_and_close(fig, output_path, interactive)


# =============================================================================
# Report
# =============================================================================

#: Where each characteristic's report calls out the extremes: (label, low bound, high bound).
EXTREMES = {"ltv": (60, 90), "dti": (25, 45), "fico": (660, 760)}


def _print_extremes(key: str, rates: pl.DataFrame) -> None:
    low_hi = EXTREMES.get(key)
    if low_hi is None:
        return
    low, high = low_hi
    print(f"\n{key.upper()} extremes:")
    for label, part in (
        (f"<= {low}", rates.filter(pl.col("bin") <= low)),
        (f">= {high}", rates.filter(pl.col("bin") >= high)),
    ):
        if len(part):
            print(
                f"  {label}: MBS={cast(float, part['mbs_match_rate'].mean()):.1%}, UMBS={cast(float, part['umbs_match_rate'].mean()):.1%}"
            )


def compute_gse_validation(gse: str) -> dict[str, Any]:
    """Compute every validation statistic for one GSE (no plotting).

    Args:
        gse: Which GSE to load.

    Returns:
        ``monthly_data``, ``coverage_end``, ``stats`` and one ``{key}_rates`` frame per
        characteristic, as `plot_match_rates_panel` consumes them.
    """
    gse = gse.lower()
    print_section(f"1. Loading Data ({gse.upper()})")
    crosswalk = load_crosswalk(gse)
    print(f"  Crosswalk: {len(crosswalk):,} matched pairs")
    mbs_data = load_mbs_data(gse)
    umbs_data = load_umbs_data(gse)

    print_section("2. Temporal Analysis (Match Rates by Month)")
    monthly_data = compute_match_rates_by_month(crosswalk, umbs_data, gse=gse)
    print(f"  {len(monthly_data)} monthly periods")

    # Clean full-data window for every non-temporal statistic: bounded below by the
    # UMBS issuance start (before it ILLD does not exist and the snapshot / MBS series
    # have asymmetric coverage) and above by the MBS coverage end (the last MBS months
    # are only partially populated and would read as recency artifacts). The by-month
    # frame deliberately keeps the full range; the window is shaded on its panel.
    coverage_end = compute_mbs_coverage_end(monthly_data)
    print(f"  Full-coverage analysis window: {ISSUANCE_START_YM} .. {coverage_end}")
    mbs_clean = mbs_data.filter(
        pl.col("first_payment_ym").is_between(pl.lit(ISSUANCE_START_YM), pl.lit(coverage_end))
    )
    umbs_clean = umbs_data.filter(
        pl.col("year_month").is_between(pl.lit(ISSUANCE_START_YM), pl.lit(coverage_end))
    )

    print_section("3. Match Statistics Summary")
    stats = compute_match_statistics(crosswalk, mbs_clean, umbs_clean)
    print(f"(analysis window: {ISSUANCE_START_YM} <= First Payment Date <= {coverage_end})")
    print(f"Matched pairs (in window): {stats['n_matched_pairs']:,}")
    print(
        f"MBS:  {stats['n_matched_mbs']:,} of {stats['n_total_mbs']:,} loans matched ({stats['mbs_match_rate']:.1%})"
    )
    print(
        f"UMBS: {stats['n_matched_umbs']:,} of {stats['n_total_umbs']:,} loans matched ({stats['umbs_match_rate']:.1%})"
    )

    results: dict[str, Any] = {
        "monthly_data": monthly_data,
        "coverage_end": coverage_end,
        "stats": stats,
    }
    both = sides(crosswalk, mbs_clean, umbs_clean)
    for i, c in enumerate(CHARACTERISTICS, start=4):
        print_section(f"{i}. Match Rates by {c.xlabel.split(' (')[0]}")
        results[f"{c.key}_rates"] = compute_match_rates_by_characteristic(both, c)
        print(results[f"{c.key}_rates"])

    print_section(f"{4 + len(CHARACTERISTICS)}. Key Findings")
    for c in CHARACTERISTICS:
        _print_extremes(c.key, results[f"{c.key}_rates"])
    print(f"\n{gse.upper()} statistics complete.")
    return results


def run_validation(
    output_dir: Path | None = None, save_plots: bool = True, show_plots: bool = False
) -> dict[str, Any]:
    """Run the validation for both GSEs and render the combined figure.

    Args:
        output_dir: Directory for the figure. Defaults to ``VALIDATION_OUTPUT_DIR``.
        save_plots: Whether to write the figure.
        show_plots: Whether to display the figure interactively.

    Returns:
        ``{"fnma": ..., "fhlmc": ...}`` with each GSE's statistics and rate frames.
    """
    output_dir = output_dir or VALIDATION_OUTPUT_DIR
    results = {gse: compute_gse_validation(gse) for gse in ("fnma", "fhlmc")}
    if (show_plots or save_plots) and HAS_MATPLOTLIB:
        print_section("Generating Visualization")
        plot_match_rates_panel(
            results,
            output_path=(output_dir / "mbs_umbs_match_rates_panel.png") if save_plots else None,
            interactive=show_plots,
        )
    print("\nValidation complete.")
    return results
