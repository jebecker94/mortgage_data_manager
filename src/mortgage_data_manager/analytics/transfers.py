"""Loan-level MSR (mortgage servicing rights) transfer detection.

Detects servicer/issuer changes in the agency loan-level disclosures and
aggregates them into a seller -> buyer -> transition-month event table. Two
deterministic detection strategies, one per data shape (this is *calculated*
analytics in the sense of :mod:`analytics.calculated` — given the inputs the
output is exact):

- **diff** (FNMA, FHLMC) -- the UMBS monthly loan-level files carry a per-month
  ``Servicer Name`` but no servicer ID, so a transfer is inferred by
  inner-joining consecutive months on ``Loan Identifier`` and flagging loans
  whose servicer name changed. Processed as a sliding two-month window to keep
  peak memory near one month's projected columns rather than the full history.
- **flag** (GNMA) -- the Ginnie loan-level files carry an explicit
  ``Seller Issuer ID`` populated *only* in the month a transfer occurs, so no
  month-over-month differencing is needed; one pass per month suffices. Issuer
  IDs are resolved to names via :func:`resolve_gnma_issuer_names`.

Both strategies emit the same common schema (``servicer_from``,
``servicer_to``, ``transition_month``, ``n_loans``, ``total_upb``, and four
seller/buyer portfolio fractions); GNMA additionally carries the numeric
``seller_issuer_id`` / ``issuer_id``. See the ``msr_transfers`` README for the
fraction definitions and the rebrand/transfer interpretation matrix.

Because FNMA/FHLMC identify servicers by name only, corporate **rebrands**
(Quicken -> Rocket) surface as spurious transfers; use
:func:`analytics.servicer_names.add_servicer_parents` (or ``consolidate=True``)
to fold variants under parent entities and flag the intercompany rows.

The GNMA seller-side portfolio fractions are an **approximation**: the data
only shows the post-transfer state, so the seller's pre-transfer book is taken
as loans it transferred away *plus* loans it still services that month.
"""

from __future__ import annotations

import gc
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import polars as pl

from mortgage_data_manager.analytics.config import AnalyticsConfig
from mortgage_data_manager.analytics.servicer_names import add_servicer_parents
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.gnma.config import GNMAConfig
from mortgage_data_manager.umbs.config import UMBSConfig

logger = get_logger(__name__)

Agency = Literal["FNMA", "FHLMC", "GNMA"]

#: Common output columns shared by all three agencies (in CSV order).
COMMON_COLUMNS: list[str] = [
    "servicer_from",
    "servicer_to",
    "transition_month",
    "n_loans",
    "total_upb",
    "frac_seller_n",
    "frac_seller_upb",
    "frac_buyer_n",
    "frac_buyer_upb",
]

# ---------------------------------------------------------------------------
# UMBS diff strategy (FNMA / FHLMC)
# ---------------------------------------------------------------------------
_DIFF_LOAN_COL = "Loan Identifier"
_DIFF_SERVICER_COL = "Servicer Name"
_DIFF_UPB_COL = "Current Investor Loan UPB"


def _month_fnma(stem: str) -> str:
    """``FNM_MLLD_202501`` -> ``202501``."""
    return stem.split("_")[-1]


def _month_fhlmc(stem: str) -> str:
    """``fu190606`` (YYMMDD) -> ``201906``."""
    raw = stem.replace("fu", "")
    return f"20{raw[:2]}{raw[2:4]}"


@dataclass(frozen=True)
class _DiffSpec:
    """Static config for a name-diff agency (FNMA/FHLMC)."""

    agency: str
    gse_folder: str  # UMBS bronze GSE subdir, e.g. "FNMA"
    file_folder: str  # disclosure family subdir, e.g. "FNM_MLLD"
    glob: str
    month_of_stem: Callable[[str], str]

    def dir(self) -> Path:
        return UMBSConfig.UMBS_BRONZE_DIR / self.gse_folder / self.file_folder


_DIFF_SPECS: dict[str, _DiffSpec] = {
    "FNMA": _DiffSpec("FNMA", "FNMA", "FNM_MLLD", "FNM_MLLD_*.parquet", _month_fnma),
    "FHLMC": _DiffSpec("FHLMC", "FHLMC", "FU", "fu*.parquet", _month_fhlmc),
}


def detect_servicer_transfers_diff(
    prev: pl.LazyFrame,
    curr: pl.LazyFrame,
    *,
    transition_month: str,
    loan_id_col: str = _DIFF_LOAN_COL,
    servicer_col: str = _DIFF_SERVICER_COL,
    upb_col: str = _DIFF_UPB_COL,
) -> pl.LazyFrame:
    """Month-pair name-diff kernel (FNMA/FHLMC).

    Inner-joins two consecutive monthly frames on ``loan_id_col``, flags loans
    whose servicer name changed, and aggregates to one row per
    ``(servicer_from, servicer_to)`` with loan count, destination-month UPB,
    and the four seller/buyer portfolio fractions. UPB is cast to ``Float64``
    (UMBS bronze numeric columns can drift to string).

    Args:
        prev: Earlier month's loan-level frame (the seller book).
        curr: Later month's loan-level frame (the buyer book).
        transition_month: ``YYYYMM`` label for the later month.
        loan_id_col: Loan identifier column (join key).
        servicer_col: Servicer-name column.
        upb_col: Current-UPB column.

    Returns:
        A LazyFrame in the :data:`COMMON_COLUMNS` schema (unsorted).
    """
    prev = prev.select(
        pl.col(loan_id_col),
        pl.col(servicer_col).alias("servicer_from"),
        pl.col(upb_col).cast(pl.Float64, strict=False).alias("upb_from"),
    )
    curr = curr.select(
        pl.col(loan_id_col),
        pl.col(servicer_col).alias("servicer_to"),
        pl.col(upb_col).cast(pl.Float64, strict=False).alias("upb_to"),
    )

    seller_totals = prev.group_by("servicer_from").agg(
        pl.len().alias("seller_total_n"),
        pl.col("upb_from").sum().alias("seller_total_upb"),
    )
    buyer_totals = curr.group_by("servicer_to").agg(
        pl.len().alias("buyer_total_n"),
        pl.col("upb_to").sum().alias("buyer_total_upb"),
    )

    changed = prev.join(curr, on=loan_id_col, how="inner").filter(
        pl.col("servicer_from") != pl.col("servicer_to")
    )

    return (
        changed.group_by("servicer_from", "servicer_to")
        .agg(
            pl.len().alias("n_loans"),
            # Seller-side UPB (origin month) drives the seller UPB fraction;
            # destination-month UPB is the reported total and the buyer fraction.
            pl.col("upb_from").sum().alias("_total_upb_from"),
            pl.col("upb_to").sum().alias("total_upb"),
        )
        .with_columns(pl.lit(transition_month).alias("transition_month"))
        .join(seller_totals, on="servicer_from", how="left")
        .join(buyer_totals, on="servicer_to", how="left")
        .with_columns(
            (pl.col("n_loans") / pl.col("seller_total_n")).alias("frac_seller_n"),
            (pl.col("_total_upb_from") / pl.col("seller_total_upb")).alias("frac_seller_upb"),
            (pl.col("n_loans") / pl.col("buyer_total_n")).alias("frac_buyer_n"),
            (pl.col("total_upb") / pl.col("buyer_total_upb")).alias("frac_buyer_upb"),
        )
        .select(COMMON_COLUMNS)
    )


def _run_diff(
    spec: _DiffSpec,
    *,
    min_month: int | None,
    max_month: int | None,
) -> pl.DataFrame:
    """Sliding two-month-window driver for a name-diff agency."""
    files = sorted(spec.dir().glob(spec.glob))
    if not files:
        raise FileNotFoundError(f"No {spec.agency} files under {spec.dir()} matching {spec.glob}")

    months = [spec.month_of_stem(f.stem) for f in files]
    pairs = list(zip(files, months, strict=True))

    # Keep the file immediately before min_month as the baseline so the first
    # in-range transition (INTO min_month) is computed.
    lo = f"{min_month:06d}" if min_month is not None else None
    hi = f"{max_month:06d}" if max_month is not None else None
    if lo is not None:
        in_range = [i for i, (_, m) in enumerate(pairs) if m >= lo]
        start = max(in_range[0] - 1, 0) if in_range else len(pairs)
    else:
        start = 0
    selected = [
        (f, m) for f, m in pairs[start:] if hi is None or m <= hi
    ]
    if len(selected) < 2:
        raise ValueError(
            f"{spec.agency}: need >=2 monthly files in range; got {len(selected)}"
        )

    logger.info(
        "%s diff: %d files, %s -> %s (%d transitions)",
        spec.agency,
        len(selected),
        selected[0][1],
        selected[-1][1],
        len(selected) - 1,
    )

    results: list[pl.DataFrame] = []
    df_prev = pl.read_parquet(
        selected[0][0], columns=[_DIFF_LOAN_COL, _DIFF_SERVICER_COL, _DIFF_UPB_COL]
    )
    for f, month in selected[1:]:
        df_curr = pl.read_parquet(
            f, columns=[_DIFF_LOAN_COL, _DIFF_SERVICER_COL, _DIFF_UPB_COL]
        )
        agg = detect_servicer_transfers_diff(
            df_prev.lazy(), df_curr.lazy(), transition_month=month
        ).collect()
        if agg.height:
            results.append(agg)
        logger.debug("%s %s: %d changed pairs", spec.agency, month, agg.height)
        df_prev = df_curr
        gc.collect()

    return _finalize(results)


# ---------------------------------------------------------------------------
# GNMA flag strategy
# ---------------------------------------------------------------------------
_GNMA_FLOOR = "201504"  # Seller Issuer ID populated from Apr 2015
_GN_ISSUER = "Issuer ID (including for loan packages in MIP pool)"
_GN_SELLER = "Seller Issuer ID"
_GN_UPB = "Unpaid Principal Balance (UPB of the loan)"
_GN_POOL = "Pool ID"
_GN_LOAN_NEW = "Disclosure Sequence Number (A sequence number unique to loan level)"
_GN_LOAN_OLD = "Disclosure Sequence Number (A sequence number unique to loan level )"  # trailing space


def resolve_gnma_issuer_names() -> pl.DataFrame:
    """Build a GNMA issuer-ID -> issuer-name lookup.

    Seeds from the ``nissues`` D silver files (older), then overlays the
    ``issrcutoff`` bronze files (newer names win) by parsing the fixed-width
    ``text_content`` (issuer ID = chars 0-4, name = chars 4-60). Returns a
    typed two-column DataFrame for a vectorized join, replacing the per-row
    ``map_elements`` lookup used in the original script.

    Returns:
        DataFrame with ``issuer_id`` (str) and ``issuer_name`` (str), one row
        per issuer (newest name retained).
    """
    nissues_dir = GNMAConfig.get_prefix_dir("silver", "nissues") / "D"
    issrcutoff_dir = GNMAConfig.GNMA_BRONZE_DIR / "issrcutoff"

    frames: list[pl.DataFrame] = []
    # Older source first; later concat + last-wins dedup lets issrcutoff override.
    for f in sorted(nissues_dir.glob("*.parquet")):
        frames.append(
            pl.scan_parquet(f)
            .select(
                pl.col("Issuer Number").cast(pl.Utf8).str.strip_chars().alias("issuer_id"),
                pl.col("Issuer Name").cast(pl.Utf8).alias("issuer_name"),
            )
            .collect()
        )
    for f in sorted(issrcutoff_dir.glob("*.parquet")):
        frames.append(
            pl.scan_parquet(f)
            .select(
                pl.col("text_content").str.slice(0, 4).str.strip_chars().alias("issuer_id"),
                pl.col("text_content").str.slice(4, 56).str.strip_chars().alias("issuer_name"),
            )
            .collect()
        )

    if not frames:
        logger.warning("No GNMA issuer-name sources found; names will fall back to ID:<id>")
        return pl.DataFrame(schema={"issuer_id": pl.Utf8, "issuer_name": pl.Utf8})

    # last-wins: keep the final occurrence of each issuer_id (issrcutoff last).
    return (
        pl.concat(frames, how="diagonal")
        .filter(pl.col("issuer_id").is_not_null() & (pl.col("issuer_id") != ""))
        .unique(subset=["issuer_id"], keep="last")
    )


def detect_servicer_transfers_flag(
    month: pl.LazyFrame,
    *,
    transition_month: str,
    issuer_names: pl.DataFrame | None = None,
    issuer_col: str = _GN_ISSUER,
    seller_col: str = _GN_SELLER,
    upb_col: str = _GN_UPB,
) -> pl.LazyFrame:
    """Single-month flag kernel (GNMA).

    Filters to rows with a populated ``Seller Issuer ID`` (the transfer flag),
    aggregates by ``(seller_id, buyer_id)``, and computes portfolio fractions
    against the buyer's current book and the seller's approximated pre-transfer
    book (transferred + still-serviced). Issuer IDs are resolved to names by a
    left join to ``issuer_names`` (unmatched -> ``ID:<id>``).

    Args:
        month: One month's deduplicated llmon1+llmon2 L frame, with a numeric
            ``upb_dollars`` column already derived.
        transition_month: ``YYYYMM`` label for this month.
        issuer_names: Output of :func:`resolve_gnma_issuer_names`, or ``None``.
        issuer_col: Current-issuer (buyer) ID column.
        seller_col: Seller-issuer ID column (the transfer flag).
        upb_col: Source UPB column name (retained for symmetry; the kernel uses
            the pre-derived ``upb_dollars``).

    Returns:
        A LazyFrame with the GNMA schema (``COMMON_COLUMNS`` plus
        ``seller_issuer_id`` and ``issuer_id``).
    """
    servicer_totals = month.group_by(issuer_col).agg(
        pl.len().alias("svc_n"),
        pl.col("upb_dollars").sum().alias("svc_upb"),
    )

    transfers = month.filter(
        pl.col(seller_col).is_not_null() & (pl.col(seller_col).str.strip_chars() != "")
    )

    buyer_totals = servicer_totals.select(
        pl.col(issuer_col),
        pl.col("svc_n").alias("buyer_total_n"),
        pl.col("svc_upb").alias("buyer_total_upb"),
    )
    seller_remaining = servicer_totals.select(
        pl.col(issuer_col).alias(seller_col),
        pl.col("svc_n").alias("seller_remain_n"),
        pl.col("svc_upb").alias("seller_remain_upb"),
    )

    agg = transfers.group_by(seller_col, issuer_col).agg(
        pl.len().alias("n_loans"),
        pl.col("upb_dollars").sum().alias("total_upb"),
    )
    seller_transferred = transfers.group_by(seller_col).agg(
        pl.len().alias("seller_xfer_n"),
        pl.col("upb_dollars").sum().alias("seller_xfer_upb"),
    )
    seller_book = (
        seller_transferred.join(seller_remaining, on=seller_col, how="left")
        .with_columns(
            (pl.col("seller_xfer_n") + pl.col("seller_remain_n").fill_null(0)).alias(
                "seller_total_n"
            ),
            (pl.col("seller_xfer_upb") + pl.col("seller_remain_upb").fill_null(0)).alias(
                "seller_total_upb"
            ),
        )
        .select(seller_col, "seller_total_n", "seller_total_upb")
    )

    names = (issuer_names if issuer_names is not None else pl.DataFrame(
        schema={"issuer_id": pl.Utf8, "issuer_name": pl.Utf8}
    )).lazy()

    return (
        agg.join(buyer_totals, on=issuer_col, how="left")
        .join(seller_book, on=seller_col, how="left")
        .with_columns(
            (pl.col("n_loans") / pl.col("seller_total_n")).alias("frac_seller_n"),
            (pl.col("total_upb") / pl.col("seller_total_upb")).alias("frac_seller_upb"),
            (pl.col("n_loans") / pl.col("buyer_total_n")).alias("frac_buyer_n"),
            (pl.col("total_upb") / pl.col("buyer_total_upb")).alias("frac_buyer_upb"),
        )
        .with_columns(pl.lit(transition_month).alias("transition_month"))
        .rename({seller_col: "seller_issuer_id", issuer_col: "issuer_id"})
        # Resolve names via join (seller_from / buyer_to)
        .join(
            names.select(
                pl.col("issuer_id").alias("seller_issuer_id"),
                pl.col("issuer_name").alias("servicer_from"),
            ),
            on="seller_issuer_id",
            how="left",
        )
        .join(
            names.select(
                pl.col("issuer_id"),
                pl.col("issuer_name").alias("servicer_to"),
            ),
            on="issuer_id",
            how="left",
        )
        .with_columns(
            pl.col("servicer_from").fill_null(
                pl.format("ID:{}", pl.col("seller_issuer_id"))
            ),
            pl.col("servicer_to").fill_null(pl.format("ID:{}", pl.col("issuer_id"))),
        )
        .select(
            "seller_issuer_id",
            "servicer_from",
            "issuer_id",
            "servicer_to",
            *COMMON_COLUMNS[2:],  # transition_month, n_loans, total_upb, fractions
        )
    )


def _run_gnma_flag(*, min_month: int | None, max_month: int | None) -> pl.DataFrame:
    """Per-month driver for the GNMA flag strategy (llmon1 + llmon2 L)."""
    issuer_names = resolve_gnma_issuer_names()
    logger.info("GNMA issuer lookup: %d issuers", issuer_names.height)

    llmon1_dir = GNMAConfig.get_prefix_dir("silver", "llmon1") / "L"
    llmon2_dir = GNMAConfig.get_prefix_dir("silver", "llmon2") / "L"

    def _month(f: Path) -> str:
        return f.stem.split("_")[1]

    lo = max(_GNMA_FLOOR, f"{min_month:06d}") if min_month is not None else _GNMA_FLOOR
    hi = f"{max_month:06d}" if max_month is not None else None

    files_by_month: dict[str, list[Path]] = {}
    for d, pat in ((llmon1_dir, "llmon1_*_L.parquet"), (llmon2_dir, "llmon2_*_L.parquet")):
        for f in d.glob(pat):
            m = _month(f)
            if m < lo or (hi is not None and m > hi):
                continue
            files_by_month.setdefault(m, []).append(f)

    months = sorted(files_by_month)
    if not months:
        raise FileNotFoundError("No GNMA llmon L files found in requested range")
    logger.info("GNMA flag: %d months, %s -> %s", len(months), months[0], months[-1])

    results: list[pl.DataFrame] = []
    for month in months:
        frames: list[pl.DataFrame] = []
        for f in files_by_month[month]:
            cols = pl.scan_parquet(f).collect_schema().names()
            loan_col = _GN_LOAN_NEW if _GN_LOAN_NEW in cols else _GN_LOAN_OLD
            if loan_col not in cols:
                continue
            frames.append(
                pl.scan_parquet(f)
                .select(
                    pl.col(_GN_POOL),
                    pl.col(loan_col).alias("loan_seq"),
                    pl.col(_GN_ISSUER),
                    pl.col(_GN_SELLER),
                    pl.col(_GN_UPB),
                )
                .collect()
            )
        if not frames:
            continue
        df_month = (
            pl.concat(frames, how="diagonal")
            .unique(subset=[_GN_POOL, "loan_seq"])
            .with_columns(
                (
                    pl.col(_GN_UPB).str.strip_chars().replace("", None).cast(pl.Float64, strict=False)
                    / 100
                ).alias("upb_dollars")
            )
        )
        agg = detect_servicer_transfers_flag(
            df_month.lazy(), transition_month=month, issuer_names=issuer_names
        ).collect()
        if agg.height:
            results.append(agg)
        logger.debug("GNMA %s: %d transfer pairs", month, agg.height)
        del df_month, frames
        gc.collect()

    return _finalize(results, gnma=True)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def _finalize(results: list[pl.DataFrame], *, gnma: bool = False) -> pl.DataFrame:
    """Concatenate per-period aggregates and sort by (month, n_loans desc)."""
    if not results:
        cols = (["seller_issuer_id", "servicer_from", "issuer_id", "servicer_to"] + COMMON_COLUMNS[2:]
                ) if gnma else COMMON_COLUMNS
        return pl.DataFrame(schema={c: pl.Utf8 for c in cols})
    return pl.concat(results, how="diagonal").sort(
        ["transition_month", "n_loans"], descending=[False, True]
    )


def detect_servicer_transfers(
    agency: Agency,
    *,
    min_month: int | None = None,
    max_month: int | None = None,
    consolidate: bool = False,
    overwrite: bool = False,
    write: bool = True,
) -> pl.DataFrame:
    """Detect MSR servicer/issuer transfers for one agency.

    Dispatches to the name-diff (FNMA/FHLMC) or flag (GNMA) strategy, returns
    the aggregated seller -> buyer -> month event table, and optionally
    persists it as parquet under ``ANALYTICS_TRANSFERS_DIR``.

    Args:
        agency: One of ``"FNMA"``, ``"FHLMC"``, ``"GNMA"``.
        min_month: Earliest transition month to include, as ``YYYYMM`` int
            (e.g. ``201906``). ``None`` = from the start of the data.
        max_month: Latest transition month, as ``YYYYMM`` int. ``None`` = end.
        consolidate: If ``True``, attach parent/sector columns and an
            ``intercompany`` flag via
            :func:`analytics.servicer_names.add_servicer_parents`.
        overwrite: Rebuild the persisted parquet even if it exists. When the
            full range is requested and the output exists, detection is skipped
            and the cached parquet returned unless ``overwrite``.
        write: Persist the result to ``ANALYTICS_TRANSFERS_DIR``.

    Returns:
        A DataFrame of detected transfers (common schema; GNMA adds the two
        issuer-ID columns; ``consolidate`` adds parent/sector/intercompany).

    Raises:
        ValueError: If ``agency`` is not recognized.
        FileNotFoundError: If no source files are found for the agency.
    """
    if agency not in ("FNMA", "FHLMC", "GNMA"):
        raise ValueError(f"Unknown agency {agency!r}; expected FNMA, FHLMC, or GNMA")

    out_path = AnalyticsConfig.ANALYTICS_TRANSFERS_DIR / f"servicer_transfers_{agency.lower()}.parquet"
    full_range = min_month is None and max_month is None
    if write and full_range and not should_process_file(out_path, overwrite):
        logger.info("%s transfers exist at %s; loading (overwrite=False)", agency, out_path)
        df = pl.read_parquet(out_path)
        return add_servicer_parents(df) if consolidate else df

    if agency == "GNMA":
        df = _run_gnma_flag(min_month=min_month, max_month=max_month)
    else:
        df = _run_diff(_DIFF_SPECS[agency], min_month=min_month, max_month=max_month)

    logger.info(
        "%s: %d transfer rows, %d loans, $%.1fB UPB",
        agency,
        df.height,
        int(df["n_loans"].sum()) if df.height else 0,
        (df["total_upb"].sum() / 1e9) if df.height else 0.0,
    )

    if write:
        AnalyticsConfig.ANALYTICS_TRANSFERS_DIR.mkdir(parents=True, exist_ok=True)
        df.write_parquet(out_path)
        logger.info("Wrote %s", out_path)

    return add_servicer_parents(df) if consolidate else df
