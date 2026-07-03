"""Feature engineering for the prepayment model.

Reads the harmonized loan-month panel (:mod:`analytics.panel`) and adds the
closed-form features the prepayment estimators consume: refinance incentive and
spread-at-origination (SATO), burnout measures, dynamic LTV, a pre-specified
S-curve, restricted-cubic-spline bases, categorical dummies, seasonality,
vintage fixed effects, and a COVID indicator. The market-rate and HPI inputs
come from the macro reference series (:mod:`core.reference.macro`).

Every transformation here is deterministic given the panel — no model is fit.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

from mortgage_data_manager.analytics.config import AnalyticsConfig
from mortgage_data_manager.analytics.schema import (
    AGENCY,
    BURNOUT_CUM,
    BURNOUT_X_INCENTIVE,
    CHANNEL,
    COVID,
    CURRENT_RATE,
    CURRENT_UPB,
    DYNAMIC_LTV,
    ELTV,
    FHLMC,
    FICO,
    HPI,
    INCENTIVE,
    LOAN_AGE,
    LOAN_ID,
    LOAN_PURPOSE,
    LOG_ORIG_UPB,
    MONTH_OF_YEAR,
    MONTHS_ITM,
    MORTGAGE_RATE,
    OCCUPANCY,
    ORIG_DATE,
    ORIG_LTV,
    ORIG_RATE,
    ORIG_UPB,
    PERIOD,
    PROPERTY_TYPE,
    SATO,
    SCURVE,
    STATE,
    VINTAGE_YEAR,
)
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.core.reference import load_hpi, load_pmms

logger = get_logger(__name__)


def _restricted_cubic_spline(values: np.ndarray, knots: list[float]) -> np.ndarray:
    """Restricted cubic spline basis (the ``len(knots) - 2`` nonlinear terms).

    The linear term is excluded — callers add the raw column separately.

    Args:
        values: 1-D array of the variable to expand.
        knots: Knot locations (at least 3).

    Returns:
        Array of shape ``(n, len(knots) - 2)``.

    Raises:
        ValueError: If fewer than 3 knots are supplied.
    """
    t = np.array(sorted(knots), dtype=np.float64)
    k = len(t)
    if k < 3:
        raise ValueError("Need at least 3 knots for a restricted cubic spline")
    x = values.astype(np.float64)

    def _h(t_j: float) -> np.ndarray:
        d_j = (np.maximum(x - t_j, 0) ** 3 - np.maximum(x - t[-2], 0) ** 3) / (t[-1] - t_j)
        d_k = (np.maximum(x - t[-2], 0) ** 3 - np.maximum(x - t[-1], 0) ** 3) / (t[-1] - t[-2])
        return d_j - d_k

    basis = np.zeros((len(x), k - 2), dtype=np.float64)
    for j in range(k - 2):
        basis[:, j] = _h(t[j])
    return basis


def _add_spline_features(
    df: pl.DataFrame, col_name: str, knots: list[float], prefix: str
) -> pl.DataFrame:
    """Append restricted-cubic-spline basis columns ``{prefix}_s{i}`` to ``df``."""
    values = df[col_name].to_numpy().astype(np.float64)
    mask = np.isnan(values)
    if mask.any():
        values[mask] = np.nanmedian(values)
    basis = _restricted_cubic_spline(values, knots)
    for i in range(basis.shape[1]):
        df = df.with_columns(pl.Series(f"{prefix}_s{i + 1}", basis[:, i]))
    return df


def _scurve(incentive: np.ndarray, k: float = 6.0, c: float = 0.75) -> np.ndarray:
    """Pre-specified logistic S-curve for refinance incentive."""
    return 1.0 / (1.0 + np.exp(-k * (incentive - c)))


def engineer_features(*, panel_path: Path | None = None, overwrite: bool = False) -> Path:
    """Engineer all prepayment features and persist the feature matrix.

    Args:
        panel_path: Source panel parquet. Defaults to the panel under
            ``AnalyticsConfig.ANALYTICS_PANEL_DIR``.
        overwrite: Rebuild even if ``features.parquet`` already exists.

    Returns:
        Path to the written ``features.parquet``.

    Raises:
        FileNotFoundError: If the source panel does not exist.
    """
    panel_path = panel_path or (AnalyticsConfig.ANALYTICS_PANEL_DIR / "panel.parquet")
    if not panel_path.exists():
        raise FileNotFoundError(f"{panel_path} not found. Build the panel first.")

    out_path = AnalyticsConfig.ANALYTICS_PANEL_DIR / "features.parquet"
    if not should_process_file(out_path, overwrite):
        logger.info(f"Features exist at {out_path}, skipping (use overwrite=True to rebuild)")
        return out_path

    df = pl.read_parquet(panel_path)
    logger.info(f"Loaded panel: {df.height:,} rows")

    # A. Refinance incentive (orig rate vs current market rate).
    if MORTGAGE_RATE in df.columns:
        df = df.with_columns((pl.col(ORIG_RATE) - pl.col(MORTGAGE_RATE)).alias(INCENTIVE))
    else:
        logger.warning("No mortgage_rate column; using orig_rate - current_rate for incentive")
        df = df.with_columns((pl.col(ORIG_RATE) - pl.col(CURRENT_RATE)).alias(INCENTIVE))

    # B. SATO (spread at origination): orig rate vs market rate at origination.
    if MORTGAGE_RATE in df.columns:
        pmms = load_pmms()
        df = df.join(
            pmms.rename({"date": "_orig_pmms_date", "mortgage_rate": "_orig_mkt_rate"}),
            left_on=ORIG_DATE,
            right_on="_orig_pmms_date",
            how="left",
        )
        df = df.with_columns((pl.col(ORIG_RATE) - pl.col("_orig_mkt_rate")).alias(SATO)).drop(
            "_orig_mkt_rate"
        )
    else:
        df = df.with_columns(pl.lit(0.0).alias(SATO))

    # C. Burnout measures (require sorting by loan x time).
    logger.info("Computing burnout measures")
    df = df.sort([LOAN_ID, PERIOD])
    df = df.with_columns(
        pl.col(INCENTIVE).clip(lower_bound=0.0).cum_sum().over(LOAN_ID).alias(BURNOUT_CUM),
        pl.when(pl.col(INCENTIVE) > 0)
        .then(1)
        .otherwise(0)
        .cum_sum()
        .over(LOAN_ID)
        .alias(MONTHS_ITM),
    )
    df = df.with_columns((pl.col(BURNOUT_CUM) * pl.col(INCENTIVE)).alias(BURNOUT_X_INCENTIVE))

    # D. Static loan size.
    df = df.with_columns(pl.col(ORIG_UPB).log().alias(LOG_ORIG_UPB))

    # E. Dynamic LTV (FHLMC: ELTV; FNMA: HPI-derived; fallback: orig LTV).
    df = _add_dynamic_ltv(df)

    # F. S-curve incentive.
    incentive_vals = np.nan_to_num(df[INCENTIVE].to_numpy().astype(np.float64), nan=0.0)
    df = df.with_columns(pl.Series(SCURVE, _scurve(incentive_vals)))

    # G. Spline bases.
    logger.info("Computing spline bases")
    df = _add_spline_features(df, LOAN_AGE, [6, 12, 24, 60, 120], "age")
    df = _add_spline_features(df, INCENTIVE, [-1.0, 0.0, 0.5, 1.0, 1.5, 2.5], "incen")
    df = _add_spline_features(df, FICO, [660, 700, 740, 780], "fico")
    df = _add_spline_features(df, DYNAMIC_LTV, [60, 80, 100, 120], "dltv")

    # H. Categorical dummies (reference category dropped).
    for val in ["R", "C"]:
        df = df.with_columns((pl.col(LOAN_PURPOSE) == val).cast(pl.Int8).alias(f"lp_{val}"))
    for val in ["CO", "CP", "MH", "PU"]:
        df = df.with_columns((pl.col(PROPERTY_TYPE) == val).cast(pl.Int8).alias(f"pt_{val}"))
    for val in ["S", "I"]:
        df = df.with_columns((pl.col(OCCUPANCY) == val).cast(pl.Int8).alias(f"occ_{val}"))
    for val in ["B", "C"]:
        df = df.with_columns((pl.col(CHANNEL) == val).cast(pl.Int8).alias(f"ch_{val}"))
    df = df.with_columns((pl.col(AGENCY) == FHLMC).cast(pl.Int8).alias("agency_fhlmc"))

    # I. Seasonality (Jan reference).
    df = df.with_columns(pl.col(PERIOD).dt.month().alias(MONTH_OF_YEAR))
    for m in range(2, 13):
        df = df.with_columns((pl.col(MONTH_OF_YEAR) == m).cast(pl.Int8).alias(f"month_{m}"))

    # J. Vintage fixed effects (earliest year reference).
    df = df.with_columns(pl.col(ORIG_DATE).dt.year().alias(VINTAGE_YEAR))
    vintage_years = sorted(df[VINTAGE_YEAR].drop_nulls().unique().to_list())
    for yr in vintage_years[1:]:
        df = df.with_columns((pl.col(VINTAGE_YEAR) == yr).cast(pl.Int8).alias(f"vint_{yr}"))

    # K. COVID indicator (Mar 2020 - Dec 2021).
    df = df.with_columns(
        ((pl.col(PERIOD) >= date(2020, 3, 1)) & (pl.col(PERIOD) <= date(2021, 12, 1)))
        .cast(pl.Int8)
        .alias(COVID)
    )

    df.write_parquet(out_path)
    logger.info(f"Feature matrix: {df.height:,} rows x {df.width} cols -> {out_path}")
    return out_path


def _add_dynamic_ltv(df: pl.DataFrame) -> pl.DataFrame:
    """Add ``dynamic_ltv``: FHLMC uses ELTV, FNMA derives from HPI drift."""
    if HPI not in df.columns:
        return df.with_columns(
            pl.when(pl.col(AGENCY) == FHLMC)
            .then(pl.col(ELTV).cast(pl.Float64))
            .otherwise(pl.col(ORIG_LTV).cast(pl.Float64))
            .alias(DYNAMIC_LTV)
        )

    hpi_data = load_hpi()
    df = df.with_columns(
        pl.date(
            pl.col(ORIG_DATE).dt.year(),
            ((pl.col(ORIG_DATE).dt.month() - 1) // 3 * 3 + 1),
            1,
        ).alias("_orig_qtr")
    )
    df = df.join(
        hpi_data.select("state", "date", "hpi").rename(
            {"date": "_orig_qtr2", "hpi": "_orig_hpi", "state": "_hpi_st"}
        ),
        left_on=[STATE, "_orig_qtr"],
        right_on=["_hpi_st", "_orig_qtr2"],
        how="left",
    ).drop("_orig_qtr")

    df = df.with_columns(
        pl.when(pl.col(AGENCY) == FHLMC)
        .then(pl.col(ELTV).cast(pl.Float64))
        .otherwise(
            pl.when(
                pl.col("_orig_hpi").is_not_null()
                & pl.col(HPI).is_not_null()
                & (pl.col(ORIG_LTV) > 0)
            )
            .then(
                pl.col(CURRENT_UPB)
                / (
                    pl.col(ORIG_UPB)
                    / (pl.col(ORIG_LTV).cast(pl.Float64) / 100.0)
                    * (pl.col(HPI) / pl.col("_orig_hpi"))
                )
                * 100.0
            )
            .otherwise(pl.col(ORIG_LTV).cast(pl.Float64))
        )
        .alias(DYNAMIC_LTV)
    )
    return df.drop("_orig_hpi")
