"""Harmonized loan-month panel for prepayment analytics.

Thin adapter over the package's existing GSE **bronze** layers. It harmonizes
FNMA SF Loan-Performance bronze (one merged orig+perf row per loan-month) and
FHLMC bronze (origination joined to performance) into the source-neutral
loan-month schema in :mod:`analytics.schema`, then:

- constructs the binary voluntary-prepayment outcome ``y``,
- drops 90+ DPD loan-months,
- merges the macro reference series (PMMS, yield curve, unemployment, HPI;
  see :mod:`core.reference.macro`),
- and applies a temporal train/test split.

The loan-month grain only exists at bronze in this package (FNMA silver is
issuance-only; FHLMC silver is origination-only), so this reads bronze
directly. It still reuses the package's download/bronze work — it does not
re-download or re-ingest.

By default a fraction of unique loans is sampled (``PANEL_SAMPLE_FRAC``) so the
panel is tractable in memory; pass ``sample_frac=1.0`` for the full universe.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

from mortgage_data_manager.analytics.config import AnalyticsConfig
from mortgage_data_manager.analytics.schema import (
    AGENCY,
    DLQ_STATUS,
    ELTV,
    FHLMC,
    FHLMC_ORIG_COL_MAP,
    FHLMC_PERF_COL_MAP,
    FHLMC_SENTINELS,
    FNMA,
    FNMA_COL_MAP,
    LOAN_ID,
    LOAN_PURPOSE,
    LOAN_PURPOSE_MAP,
    ORIG_DATE,
    ORIG_QUARTER,
    PERIOD,
    RATE_CHANGE_3M,
    SILVER_COLUMNS,
    SILVER_SCHEMA,
    SPLIT,
    STATE,
    ZERO_BAL_CODE,
    Y,
)
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.core.reference import (
    load_hpi,
    load_pmms,
    load_unemployment,
    load_yield_curve,
)
from mortgage_data_manager.fhlmc.config import FHLMCConfig
from mortgage_data_manager.fnma.config import FNMAConfig

logger = get_logger(__name__)

_FNMA_MMYYYY_COLS = ["Monthly Reporting Period", "Origination Date", "First Payment Date"]
_FHLMC_JOIN_KEY = "Loan Sequence Number"
SEED = 42


def _parse_mmyyyy_expr(col_name: str) -> pl.Expr:
    """Parse an FNMA ``MMYYYY`` string column into a first-of-month Date expr."""
    c = pl.col(col_name)
    return pl.date(c.str.slice(2, 4).cast(pl.Int32), c.str.slice(0, 2).cast(pl.Int32), 1).alias(
        col_name
    )


def _cast_to_schema(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Cast present columns to the harmonized dtypes and enforce column order."""
    schema = lf.collect_schema()
    cast_exprs = [
        pl.col(name).cast(dtype) if schema.get(name) != dtype else pl.col(name)
        for name, dtype in SILVER_SCHEMA.items()
        if name in schema.names()
    ]
    return lf.select(cast_exprs).select(SILVER_COLUMNS)


def harmonize_fnma_lf(path: Path) -> pl.LazyFrame:
    """Harmonize one FNMA bronze quarter file into the loan-month schema.

    Args:
        path: An FNMA bronze parquet (``{YYYYQn}.parquet``).

    Returns:
        A LazyFrame with the harmonized columns/types (loan IDs prefixed ``F``).
    """
    stripped_map = {k.strip(): v for k, v in FNMA_COL_MAP.items()}
    lf = pl.scan_parquet(path)

    raw_names = lf.collect_schema().names()
    rename_map = {n: n.strip() for n in raw_names if n != n.strip()}
    if rename_map:
        lf = lf.rename(rename_map)

    available = set(lf.collect_schema().names())
    select_cols = sorted(set(stripped_map) & available)
    lf = lf.select(select_cols)

    exprs: list[pl.Expr] = []
    for bcol in select_cols:
        scol = stripped_map[bcol]
        if bcol in _FNMA_MMYYYY_COLS:
            exprs.append(_parse_mmyyyy_expr(bcol).alias(scol))
        elif bcol == "Loan Identifier":
            exprs.append((pl.lit("F") + pl.col(bcol)).alias(scol))
        elif bcol in ("First Time Home Buyer Indicator", "Modification Flag"):
            exprs.append((pl.col(bcol) == "Y").alias(scol))
        elif bcol in ("Current Loan Delinquency Status", "Zero Balance Code"):
            exprs.append(pl.col(bcol).cast(pl.Int32, strict=False).alias(scol))
        elif bcol == "Loan Purpose":
            exprs.append(pl.col(bcol).replace(LOAN_PURPOSE_MAP).alias(scol))
        else:
            exprs.append(pl.col(bcol).alias(scol))

    exprs.append(pl.lit(FNMA).alias(AGENCY))
    exprs.append(pl.lit(None).cast(pl.Int32).alias(ELTV))
    return _cast_to_schema(lf.select(exprs))


def harmonize_fhlmc_lf(orig_path: Path, perf_path: Path) -> pl.LazyFrame:
    """Harmonize one FHLMC bronze quarter (origination + performance) into the schema.

    Args:
        orig_path: FHLMC origination bronze (``historical_data_{q}.parquet``).
        perf_path: FHLMC performance bronze (``historical_data_time_{q}.parquet``).

    Returns:
        A LazyFrame with the harmonized columns/types (loan IDs prefixed ``G``).
    """
    silver_to_bronze_orig = {v: k for k, v in FHLMC_ORIG_COL_MAP.items()}
    orig_cols = list(FHLMC_ORIG_COL_MAP.keys())

    lf_orig = pl.scan_parquet(orig_path).select(orig_cols)
    sentinel_exprs: list[pl.Expr] = []
    for bcol in orig_cols:
        scol = FHLMC_ORIG_COL_MAP[bcol]
        if scol in FHLMC_SENTINELS:
            sentinel_exprs.append(
                pl.when(pl.col(bcol).is_in(FHLMC_SENTINELS[scol]))
                .then(None)
                .otherwise(pl.col(bcol))
                .alias(bcol)
            )
        else:
            sentinel_exprs.append(pl.col(bcol))
    lf_orig = lf_orig.select(sentinel_exprs)

    loan_purpose_bronze = silver_to_bronze_orig[LOAN_PURPOSE]
    lf_orig = lf_orig.with_columns(
        pl.col(loan_purpose_bronze).replace(LOAN_PURPOSE_MAP).alias(loan_purpose_bronze)
    )

    lf_perf = pl.scan_parquet(perf_path).select(list(FHLMC_PERF_COL_MAP.keys()))
    lf = lf_perf.join(lf_orig, on=_FHLMC_JOIN_KEY, how="left")

    all_col_map = {**FHLMC_ORIG_COL_MAP, **FHLMC_PERF_COL_MAP}
    joined_cols = set(lf.collect_schema().names())

    exprs: list[pl.Expr] = []
    for bcol, scol in all_col_map.items():
        if bcol not in joined_cols:
            continue
        if bcol == _FHLMC_JOIN_KEY:
            exprs.append((pl.lit("G") + pl.col(bcol)).alias(scol))
        elif bcol == "First Time Homebuyer Flag":
            exprs.append((pl.col(bcol) == "Y").alias(scol))
        elif bcol == "Current Loan Delinquency Status":
            exprs.append(pl.col(bcol).cast(pl.Int32, strict=False).alias(scol))
        elif bcol == "Modification Flag":
            exprs.append(pl.col(bcol).is_in(["Y", "P"]).fill_null(False).alias(scol))
        elif bcol in ("Zero Balance Code", "Estimated Loan-to-Value (ELTV)"):
            exprs.append(pl.col(bcol).cast(pl.Int32, strict=False).alias(scol))
        elif bcol == "Original UPB":
            exprs.append(pl.col(bcol).cast(pl.Float64).alias(scol))
        else:
            exprs.append(pl.col(bcol).alias(scol))

    exprs.append(pl.lit(FHLMC).alias(AGENCY))
    # Freddie convention: origination ~ first payment date minus two months.
    exprs.append(pl.col("First Payment Date").dt.offset_by("-2mo").alias(ORIG_DATE))
    return _cast_to_schema(lf.select(exprs))


def _year_of(quarter_label: str) -> int:
    """Parse the 4-digit year from a ``YYYYQn`` quarter label."""
    return int(quarter_label[:4])


def _fnma_sources(min_year: int, max_year: int) -> list[tuple[str, pl.LazyFrame]]:
    """Harmonized FNMA LazyFrames keyed by quarter label, within the year range."""
    out: list[tuple[str, pl.LazyFrame]] = []
    for f in sorted(FNMAConfig.FNMA_BRONZE_DIR.glob("*.parquet")):
        label = f.stem
        try:
            yr = _year_of(label)
        except ValueError:
            continue
        if min_year <= yr <= max_year:
            out.append((f"FNMA/{label}", harmonize_fnma_lf(f)))
    return out


def _fhlmc_sources(min_year: int, max_year: int) -> list[tuple[str, pl.LazyFrame]]:
    """Harmonized FHLMC LazyFrames keyed by quarter label, within the year range."""
    perf_dir = FHLMCConfig.FHLMC_BRONZE_PERFORMANCE
    orig_dir = FHLMCConfig.FHLMC_BRONZE_ORIGINATION
    out: list[tuple[str, pl.LazyFrame]] = []
    for perf in sorted(perf_dir.glob("historical_data_time_*.parquet")):
        q = perf.stem.replace("historical_data_time_", "")
        try:
            yr = _year_of(q)
        except ValueError:
            continue
        if not (min_year <= yr <= max_year):
            continue
        orig = orig_dir / f"historical_data_{q}.parquet"
        if not orig.exists():
            logger.warning(f"FHLMC {q}: missing origination file {orig.name}, skipping")
            continue
        out.append((f"FHLMC/{q}", harmonize_fhlmc_lf(orig, perf)))
    return out


def _sample_loan_ids(
    sources: list[tuple[str, pl.LazyFrame]], sample_frac: float, seed: int
) -> set[str] | None:
    """Collect unique loan IDs across sources and sample a fraction (None = keep all)."""
    if sample_frac >= 1.0:
        return None
    all_ids: set[str] = set()
    for _, lf in sources:
        all_ids.update(lf.select(LOAN_ID).unique().collect().get_column(LOAN_ID).to_list())
    ordered = sorted(all_ids)
    n_sample = int(len(ordered) * sample_frac)
    rng = np.random.default_rng(seed)
    sampled = set(rng.choice(ordered, size=n_sample, replace=False).tolist())
    logger.info(f"Sampled {len(sampled):,} of {len(ordered):,} loans ({sample_frac:.0%})")
    return sampled


def _merge_macro(chunk: pl.DataFrame) -> pl.DataFrame:
    """Join the macro reference series onto a harmonized loan-month chunk."""
    pmms = load_pmms().sort("date")
    rate_change = pmms.with_columns(
        (pl.col("mortgage_rate") - pl.col("mortgage_rate").shift(3)).alias(RATE_CHANGE_3M)
    ).select("date", RATE_CHANGE_3M)

    chunk = chunk.join(
        pmms.rename({"date": "_pmms_date"}), left_on=PERIOD, right_on="_pmms_date", how="left"
    )
    chunk = chunk.join(
        rate_change.rename({"date": "_rc_date"}), left_on=PERIOD, right_on="_rc_date", how="left"
    )
    chunk = chunk.join(
        load_yield_curve().rename({"date": "_yc_date"}),
        left_on=PERIOD,
        right_on="_yc_date",
        how="left",
    )
    chunk = chunk.join(
        load_unemployment().rename({"date": "_u_date"}),
        left_on=[STATE, PERIOD],
        right_on=["state", "_u_date"],
        how="left",
    )
    chunk = chunk.with_columns(
        pl.date(
            pl.col(PERIOD).dt.year(),
            ((pl.col(PERIOD).dt.month() - 1) // 3 * 3 + 1),
            1,
        ).alias("_qtr_date")
    )
    chunk = chunk.join(
        load_hpi().rename({"date": "_hpi_date", "state": "_hpi_st"}),
        left_on=[STATE, "_qtr_date"],
        right_on=["_hpi_st", "_hpi_date"],
        how="left",
    ).drop("_qtr_date")
    return chunk


def build_panel(
    *,
    min_year: int = 2016,
    max_year: int = 2100,
    sample_frac: float | None = None,
    train_cutoff: str | None = None,
    overwrite: bool = False,
    seed: int = SEED,
) -> Path:
    """Build the harmonized loan-month prepayment panel and persist it.

    Args:
        min_year: Earliest origination/perf vintage year to include.
        max_year: Latest vintage year to include.
        sample_frac: Fraction of unique loans to sample. Defaults to
            ``AnalyticsConfig.PANEL_SAMPLE_FRAC``; pass ``1.0`` for the full
            universe.
        train_cutoff: ISO date; loan-months with ``period <= cutoff`` are
            labeled ``train``, the rest ``test``. Defaults to
            ``AnalyticsConfig.PANEL_TRAIN_CUTOFF``.
        overwrite: Rebuild even if ``panel.parquet`` already exists.
        seed: RNG seed for loan sampling.

    Returns:
        Path to the written ``panel.parquet``.

    Raises:
        FileNotFoundError: If no GSE bronze files are found in the year range.
    """
    sample_frac = sample_frac if sample_frac is not None else AnalyticsConfig.PANEL_SAMPLE_FRAC
    cutoff_date = date.fromisoformat(train_cutoff or AnalyticsConfig.PANEL_TRAIN_CUTOFF)

    AnalyticsConfig.ANALYTICS_PANEL_DIR.mkdir(parents=True, exist_ok=True)
    out_path = AnalyticsConfig.ANALYTICS_PANEL_DIR / "panel.parquet"
    if not should_process_file(out_path, overwrite):
        logger.info(f"Panel exists at {out_path}, skipping (use overwrite=True to rebuild)")
        return out_path

    sources = _fnma_sources(min_year, max_year) + _fhlmc_sources(min_year, max_year)
    if not sources:
        raise FileNotFoundError(
            f"No FNMA/FHLMC bronze files found for years [{min_year}, {max_year}]."
        )
    logger.info(f"Harmonizing {len(sources)} GSE bronze quarter(s)")

    sampled_ids = _sample_loan_ids(sources, sample_frac, seed)

    chunks: list[pl.DataFrame] = []
    for label, lf in sources:
        if sampled_ids is not None:
            lf = lf.filter(pl.col(LOAN_ID).is_in(sampled_ids))
        lf = lf.filter(pl.col(DLQ_STATUS).is_null() | (pl.col(DLQ_STATUS) < 3))
        lf = lf.with_columns(
            pl.when(pl.col(ZERO_BAL_CODE) == 1).then(1).otherwise(0).alias(Y),
            (
                pl.col(ORIG_DATE).dt.year().cast(pl.String)
                + "Q"
                + pl.col(ORIG_DATE).dt.quarter().cast(pl.String)
            ).alias(ORIG_QUARTER),
        )
        chunk = lf.collect()
        if chunk.height == 0:
            continue
        chunk = _merge_macro(chunk).with_columns(
            pl.when(pl.col(PERIOD) <= pl.lit(cutoff_date))
            .then(pl.lit("train"))
            .otherwise(pl.lit("test"))
            .alias(SPLIT)
        )
        chunks.append(chunk)
        logger.info(f"  {label}: {chunk.height:,} loan-months")

    if not chunks:
        raise FileNotFoundError("No loan-months survived filtering; nothing to write.")

    panel = pl.concat(chunks, how="diagonal_relaxed")
    panel.write_parquet(out_path)
    logger.info(
        f"Panel: {panel.height:,} loan-months, "
        f"{panel.get_column(LOAN_ID).n_unique():,} loans, "
        f"y-rate {panel.get_column(Y).mean():.4f} -> {out_path}"
    )
    return out_path
