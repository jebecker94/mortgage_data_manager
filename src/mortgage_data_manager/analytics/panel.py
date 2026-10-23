"""Harmonized loan-month panel for prepayment and credit analytics.

A projection of the combined loan-level masters onto the source-neutral
loan-month schema in `analytics.schema`: static attributes from
``combined/loan_issuance`` joined to the monthly history in
``combined/loan_performance``, for the GSE single-family disclosure universe
(``schema.PANEL_SOURCE_DATASETS``) and its SF-reported months. All field
harmonization happens once, in `combined`; this module only selects, renames
and then:

- constructs the binary voluntary-prepayment outcome ``y`` and the
  credit-event outcome ``y_default`` (zero-balance-code waterfall; see
  `analytics.schema.CREDIT_EVENT_ZB_CODES`),
- drops 90+ DPD loan-months (prepayment panel) — or keeps them when
  ``include_delinquent=True`` (credit panel, written to
  ``panel_credit.parquet``, since a default model needs the delinquent
  risk set),
- merges the macro reference series (PMMS, yield curve, unemployment, HPI;
  see `core.reference.macro`),
- and applies a temporal train/test split.

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
    CREDIT_EVENT_ZB_CODES,
    DLQ_STATUS,
    FIRST_PAYMENT_DATE,
    ISSUANCE_COL_MAP,
    LOAN_AGE,
    LOAN_ID,
    MOD_FLAG,
    ORIG_DATE,
    ORIG_QUARTER,
    PANEL_SOURCE_DATASETS,
    PERFORMANCE_COL_MAP,
    PERIOD,
    RATE_CHANGE_3M,
    SILVER_COLUMNS,
    SILVER_SCHEMA,
    SPLIT,
    STATE,
    Y_DEFAULT,
    ZERO_BAL_CODE,
    Y,
)
from mortgage_data_manager.combined.config import CombinedConfig
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.core.reference import (
    load_hpi,
    load_pmms,
    load_unemployment,
    load_yield_curve,
)

logger = get_logger(__name__)

SEED = 42


def _fill_terminal_loan_age(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Fill null ``loan_age`` as scheduled payments elapsed since first payment.

    FNMA blanks ``Loan Age`` (with most other dynamic fields) on the
    zero-balance terminal row, so EVERY FNMA termination event would carry a
    null age — which breaks any age-indexed hazard aggregation downstream.
    The disclosed convention is exactly ``months(period - first_payment_date)
    + 1`` (matches 100.0% of non-null FNMA and 99.5% of FHLMC loan-months),
    so nulls are filled deterministically. Non-null ages are never touched.

    Returns:
        The frame with null ``loan_age`` filled from the first-payment date.
    """
    derived = (
        (pl.col(PERIOD).dt.year() * 12 + pl.col(PERIOD).dt.month())
        - (pl.col(FIRST_PAYMENT_DATE).dt.year() * 12 + pl.col(FIRST_PAYMENT_DATE).dt.month())
        + 1
    ).cast(pl.Int32)
    return lf.with_columns(pl.col(LOAN_AGE).fill_null(derived))


def _cast_to_schema(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Cast present columns to the harmonized dtypes and enforce column order."""
    schema = lf.collect_schema()
    cast_exprs = [
        pl.col(name).cast(dtype) if schema.get(name) != dtype else pl.col(name)
        for name, dtype in SILVER_SCHEMA.items()
        if name in schema.names()
    ]
    return lf.select(cast_exprs).select(SILVER_COLUMNS)


def _origination_date() -> pl.Expr:
    """``combined/loan_issuance`` origination date, with Freddie's convention filled in.

    Freddie's SF disclosure has no origination date; by its convention a loan
    originates two months before its first payment.
    """
    return pl.coalesce(
        pl.col("origination_date"), pl.col("first_payment_date").dt.offset_by("-2mo")
    )


def _loan_keys(min_year: int, max_year: int) -> pl.DataFrame:
    """Every panel-universe loan originated in ``[min_year, max_year]`` (``loan_id``, ``agency``)."""
    lf = pl.scan_parquet(
        str(CombinedConfig.dataset_path("loan_issuance") / "**/*.parquet"), hive_partitioning=True
    )
    universe = pl.any_horizontal(
        [
            (pl.col("agency") == agency) & (pl.col("source_dataset") == dataset)
            for agency, dataset in PANEL_SOURCE_DATASETS.items()
        ]
    )
    return (
        lf.filter(universe & _origination_date().dt.year().is_between(min_year, max_year))
        .select(pl.col("loan_key").alias(LOAN_ID), pl.col("agency").alias(AGENCY))
        .collect(engine="streaming")
    )


def _sample_loans(keys: pl.DataFrame, sample_frac: float, seed: int) -> pl.DataFrame:
    """Sample a fraction of the loans (all of them when ``sample_frac >= 1``)."""
    if sample_frac >= 1.0:
        return keys
    ordered = keys.sort(AGENCY, LOAN_ID)
    n_sample = int(ordered.height * sample_frac)
    rng = np.random.default_rng(seed)
    sampled = ordered[np.sort(rng.choice(ordered.height, size=n_sample, replace=False))]
    logger.info(
        "Sampled %s of %s loans (%s)",
        f"{sampled.height:,}",
        f"{ordered.height:,}",
        f"{sample_frac:.0%}",
    )
    return sampled


def _static_attributes(loans: pl.DataFrame) -> pl.DataFrame:
    """The harmonized static columns for the given loans, one row per loan."""
    lf = pl.scan_parquet(
        str(CombinedConfig.dataset_path("loan_issuance") / "**/*.parquet"), hive_partitioning=True
    )
    return (
        lf.join(
            loans.lazy().rename({LOAN_ID: "loan_key", AGENCY: "agency"}),
            on=["loan_key", "agency"],
            how="semi",
        )
        .with_columns(_origination_date().alias("origination_date"))
        .select(pl.col(src).alias(dst) for src, dst in ISSUANCE_COL_MAP.items())
        .collect(engine="streaming")
    )


def _performance_partitions(min_year: int) -> list[tuple[str, Path]]:
    """``(label, directory)`` for every GSE ``loan_performance`` partition from ``min_year`` on."""
    root = CombinedConfig.dataset_path("loan_performance")
    out: list[tuple[str, Path]] = []
    for agency in PANEL_SOURCE_DATASETS:
        for d in sorted((root / f"agency={agency}").glob("reporting_year=*")):
            year = int(d.name.split("=", 1)[1])
            if year >= min_year:
                out.append((f"{agency}/{year}", d))
    return out


def _loan_months(partition: Path, static: pl.DataFrame) -> pl.LazyFrame:
    """One performance partition's SF-reported months for the given loans, harmonized."""
    rp = pl.col("reporting_period")
    return (
        pl.scan_parquet(str(partition / "*.parquet"))
        .filter(pl.col("month_source") != "umbs_only")
        .join(static.lazy().select(pl.col(LOAN_ID).alias("loan_key")), on="loan_key", how="semi")
        .select(
            *(
                pl.col(src).alias(dst)
                for src, dst in PERFORMANCE_COL_MAP.items()
                if src != "agency"
            ),
            pl.date(rp // 100, rp % 100, 1).alias(PERIOD),
            pl.col("event_raw_code").cast(pl.Int32, strict=False).alias(ZERO_BAL_CODE),
            pl.col("modification_flag").is_in(["Y", "P"]).fill_null(False).alias(MOD_FLAG),
        )
        .join(static.lazy(), on=LOAN_ID, how="inner")
    )


def outcome_exprs() -> list[pl.Expr]:
    """Panel outcome/cohort expressions: ``y``, ``y_default``, ``orig_quarter``.

    ``y`` flags voluntary prepayment (zero-balance code 01) and ``y_default``
    flags a credit-event termination (codes in
    `analytics.schema.CREDIT_EVENT_ZB_CODES`); both are 0 on
    non-terminal loan-months, so each is a one-month hazard outcome.

    Returns:
        The ``y``, ``y_default`` and ``orig_quarter`` expressions, in that order.
    """
    return [
        pl.when(pl.col(ZERO_BAL_CODE) == 1).then(1).otherwise(0).alias(Y),
        pl.when(pl.col(ZERO_BAL_CODE).is_in(sorted(CREDIT_EVENT_ZB_CODES)))
        .then(1)
        .otherwise(0)
        .alias(Y_DEFAULT),
        (
            pl.col(ORIG_DATE).dt.year().cast(pl.String)
            + "Q"
            + pl.col(ORIG_DATE).dt.quarter().cast(pl.String)
        ).alias(ORIG_QUARTER),
    ]


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
    include_delinquent: bool = False,
    overwrite: bool = False,
    seed: int = SEED,
) -> Path:
    """Build the harmonized loan-month panel and persist it.

    The default (prepayment) panel drops 90+ DPD loan-months — seriously
    delinquent loans are not in the voluntary-refi risk set. The credit panel
    (``include_delinquent=True``) keeps them, because the default hazard is
    concentrated there; it is written to ``panel_credit.parquet`` so the two
    artifacts never mix. Both carry the ``y`` (voluntary prepay) and
    ``y_default`` (credit event) outcomes.

    Args:
        min_year: Earliest origination year to include.
        max_year: Latest origination year to include.
        sample_frac: Fraction of unique loans to sample. Defaults to
            ``AnalyticsConfig.PANEL_SAMPLE_FRAC``; pass ``1.0`` for the full
            universe.
        train_cutoff: ISO date; loan-months with ``period <= cutoff`` are
            labeled ``train``, the rest ``test``. Defaults to
            ``AnalyticsConfig.PANEL_TRAIN_CUTOFF``.
        include_delinquent: Keep 90+ DPD loan-months (credit panel) instead of
            dropping them (prepayment panel).
        overwrite: Rebuild even if the output parquet already exists.
        seed: RNG seed for loan sampling.

    Returns:
        Path to the written ``panel.parquet`` (or ``panel_credit.parquet``).

    Raises:
        FileNotFoundError: If no panel-universe loan was originated in the year range,
            or no loan-month survives filtering.
    """
    sample_frac = sample_frac if sample_frac is not None else AnalyticsConfig.PANEL_SAMPLE_FRAC
    cutoff_date = date.fromisoformat(train_cutoff or AnalyticsConfig.PANEL_TRAIN_CUTOFF)

    AnalyticsConfig.ANALYTICS_PANEL_DIR.mkdir(parents=True, exist_ok=True)
    out_name = "panel_credit.parquet" if include_delinquent else "panel.parquet"
    out_path = AnalyticsConfig.ANALYTICS_PANEL_DIR / out_name
    if not should_process_file(out_path, overwrite):
        logger.info("Panel exists at %s, skipping (use overwrite=True to rebuild)", out_path)
        return out_path

    keys = _loan_keys(min_year, max_year)
    if keys.height == 0:
        raise FileNotFoundError(
            f"No GSE single-family loans originated in [{min_year}, {max_year}] in "
            "combined/loan_issuance."
        )
    static = _static_attributes(_sample_loans(keys, sample_frac, seed))
    partitions = _performance_partitions(min_year)
    logger.info(
        "Projecting %s loans over %s loan_performance partition(s)",
        f"{static.height:,}",
        len(partitions),
    )

    chunks: list[pl.DataFrame] = []
    for label, partition in partitions:
        lf = _fill_terminal_loan_age(_loan_months(partition, static))
        lf = _cast_to_schema(lf)
        if not include_delinquent:
            lf = lf.filter(pl.col(DLQ_STATUS).is_null() | (pl.col(DLQ_STATUS) < 3))
        lf = lf.with_columns(outcome_exprs())
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
        logger.info("  %s: %s loan-months", label, f"{chunk.height:,}")

    if not chunks:
        raise FileNotFoundError("No loan-months survived filtering; nothing to write.")

    panel = pl.concat(chunks, how="diagonal_relaxed")
    panel.write_parquet(out_path)
    y_rate, y_default_rate = panel.select(pl.col(Y).mean(), pl.col(Y_DEFAULT).mean()).row(0)
    logger.info(
        "Panel: %s loan-months, %s loans, y-rate %.4f, y_default-rate %.6f -> %s",
        f"{panel.height:,}",
        f"{panel.get_column(LOAN_ID).n_unique():,}",
        y_rate,
        y_default_rate,
        out_path,
    )
    return out_path
