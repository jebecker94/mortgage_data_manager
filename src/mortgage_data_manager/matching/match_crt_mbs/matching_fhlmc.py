"""FHLMC CRT reference-pool to SF Loan-Level Dataset matching.

Freddie's CRT loan-level disclosure identifies loans only by deal-scoped Loan
Identifiers (``19DNA1000001``). This matcher pins each CRT loan to its SF
Loan-Level Dataset ``Loan Sequence Number`` with per-deal rounds of exact joins
on at-origination characteristics (``rounds.py``), then confirms pins against the SF monthly
performance panel: a pin is *confirmed* when the panel has a month within
+/-1 of the CRT ``Period`` whose ``Current Actual UPB`` equals the CRT
``UPB at Issuance`` to the cent (~99.8% of CRT loans pin 1:1 and 100.0% of
sampled pins were cent-exact).

Rounds run strict -> loose over each deal's FPD-windowed SF origination
pool; a loan pins when the join is 1:1 in both directions, and SF loans
claimed by an earlier round leave the pool. HRP relief-refi deals pin against
the standard silver like the rest; the one outlier is 20AF01, where ~45% of the
pool has no standard-dataset candidate at all.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger, log_execution_time
from mortgage_data_manager.core.medallion import atomic_output
from mortgage_data_manager.matching.match_crt_mbs.config import CRTMBSMatchingConfig
from mortgage_data_manager.matching.match_crt_mbs.rounds import ROUNDS_FHLMC, CrtRound
from mortgage_data_manager.matching.resolve import drop_ambiguous

logger = get_logger(__name__)


def _crt_deal_index(crt_dir: Path) -> pl.DataFrame:
    """One row per deal: Period and the FPD window (drives the SF pool scan)."""
    return (
        pl.scan_parquet(crt_dir / "**" / "*.parquet", hive_partitioning=False)
        .group_by("Reference Pool Number")
        .agg(
            pl.col("Period").min(),  # orders the deal loop only; no pin depends on it
            pl.col("First Payment Date").min().alias("fpd_min"),
            pl.col("First Payment Date").max().alias("fpd_max"),
            pl.len().alias("n_loans"),
        )
        .sort("Period", "Reference Pool Number")
        .collect()
    )


def _load_crt_deal(crt_dir: Path, deal: str) -> pl.DataFrame:
    """Load one deal's CRT loans with harmonized match-key columns."""
    return (
        pl.scan_parquet(crt_dir / "**" / "*.parquet", hive_partitioning=False)
        .filter(pl.col("Reference Pool Number") == deal)
        .select(
            pl.col("Loan Identifier").alias("crt_id"),
            pl.col("Reference Pool Number").alias("reference_pool"),
            pl.col("Period"),
            pl.col("UPB at Issuance").alias("upb_at_issuance"),
            pl.col("First Payment Date").alias("fpd"),
            pl.col("Maturity Date").alias("maturity"),
            pl.col("Property State").alias("state"),
            pl.col("Postal Code (3-Digit)").alias("zip3"),
            pl.col("Original UPB").cast(pl.Int64).alias("upb_k"),
            (pl.col("Original Interest Rate").cast(pl.Float64) * 1000)
            .round(0)
            .cast(pl.Int32)
            .alias("rate_m"),
            pl.col("Classic FICO").cast(pl.Int32).alias("fico"),
            pl.col("Original Loan-To-Value (LTV)").cast(pl.Int32).alias("ltv"),
            pl.col("Original Combined Loan-To-Value (CLTV)").cast(pl.Int32).alias("cltv"),
            pl.col("Original Debt-To-Income (DTI)").cast(pl.Int32).alias("dti"),
            pl.col("Loan Purpose").alias("purpose"),
            pl.col("Property Type").alias("prop_type"),
            # CRT codes owner-occupied 'O' in early deals; the SF dataset uses 'P'.
            pl.col("Occupancy Status").replace({"O": "P"}).alias("occ"),
            pl.col("Number of Units").cast(pl.Int32).alias("units"),
            pl.col("First Time Homebuyer Indicator").alias("fthb"),
        )
        .collect()
    )


def _load_sf_window(origination_dir: Path, fpd_min, fpd_max) -> pl.DataFrame:
    """Load the FPD-windowed SF origination pool with harmonized columns."""
    files = [
        f
        for y in range(fpd_min.year - 1, fpd_max.year + 2)
        if (f := origination_dir / f"vintage_year={y}" / "data.parquet").exists()
    ]
    if not files:
        return pl.DataFrame()
    return (
        pl.scan_parquet(files, hive_partitioning=False)
        .filter(pl.col("First Payment Date").is_between(fpd_min, fpd_max))
        .select(
            pl.col("Loan Sequence Number").alias("lsn"),
            pl.col("First Payment Date").alias("fpd"),
            pl.col("Maturity Date").alias("maturity"),
            pl.col("Property State").alias("state"),
            pl.col("Postal Code").alias("zip3"),
            pl.col("Original UPB").cast(pl.Int64).alias("upb_k"),
            (pl.col("Original Interest Rate").cast(pl.Float64) * 1000)
            .round(0)
            .cast(pl.Int32)
            .alias("rate_m"),
            pl.col("Credit Score").cast(pl.Int32).alias("fico"),
            pl.col("Original Loan-to-Value (LTV)").cast(pl.Int32).alias("ltv"),
            pl.col("Original Combined Loan-to-Value (CLTV)").cast(pl.Int32).alias("cltv"),
            pl.col("Original Debt-to-Income (DTI) Ratio").cast(pl.Int32).alias("dti"),
            pl.col("Loan Purpose").cast(pl.String).alias("purpose"),
            pl.col("Property Type").cast(pl.String).alias("prop_type"),
            pl.col("Occupancy Status").cast(pl.String).alias("occ"),
            pl.col("Number of Units").cast(pl.Int32).alias("units"),
            pl.col("First Time Homebuyer Flag").alias("fthb"),
        )
        .collect()
    )


def _pin_round(crt: pl.DataFrame, sf: pl.DataFrame, keys: list[str]) -> pl.DataFrame:
    """Exact join on ``keys``; return pairs that are 1:1 in both directions."""
    cand = crt.select("crt_id", *keys).join(sf.select("lsn", *keys), on=keys, how="inner")
    return drop_ambiguous(cand, "crt_id", "lsn").select("crt_id", "lsn")


def _match_deal(
    crt: pl.DataFrame, sf: pl.DataFrame, rounds: tuple[CrtRound, ...] = ROUNDS_FHLMC
) -> pl.DataFrame:
    """Run ``rounds`` in order for one deal; return (crt_id, lsn, match_round)."""
    pieces = []
    remaining_crt, remaining_sf = crt, sf
    for match_round, round_ in enumerate(rounds, start=1):
        if remaining_crt.is_empty() or remaining_sf.is_empty():
            break
        pinned = _pin_round(remaining_crt, remaining_sf, list(round_.keys))
        if pinned.is_empty():
            continue
        pieces.append(pinned.with_columns(pl.lit(match_round, dtype=pl.UInt8).alias("match_round")))
        remaining_crt = remaining_crt.join(pinned.select("crt_id"), on="crt_id", how="anti")
        remaining_sf = remaining_sf.join(pinned.select("lsn"), on="lsn", how="anti")
    if not pieces:
        return pl.DataFrame(schema={"crt_id": pl.String, "lsn": pl.String, "match_round": pl.UInt8})
    return pl.concat(pieces, how="diagonal_relaxed")


def _perf_quarter(lsn_expr: pl.Expr) -> pl.Expr:
    """SF perf quarter label from the LSN vintage prefix (``FYYQn...``)."""
    yy = lsn_expr.str.slice(1, 2).cast(pl.Int32)
    century = pl.when(yy >= 80).then(1900 + yy).otherwise(2000 + yy)
    return (century.cast(pl.String) + pl.lit("Q") + lsn_expr.str.slice(4, 1)).alias("perf_quarter")


@log_execution_time
def confirm_pairs(pairs: pl.DataFrame, performance_dir: Path) -> pl.DataFrame:
    """Confirm pins against the SF monthly performance panel.

    For each pair, look for a panel month within ``CONFIRM_MONTH_TOLERANCE``
    of the CRT Period whose ``Current Actual UPB`` equals ``upb_at_issuance``
    within ``CONFIRM_UPB_TOLERANCE``. Scans each origination-quarter perf file
    once, restricted to that quarter's pairs.

    Args:
        pairs: Crosswalk pairs with ``lsn``, ``Period``, ``upb_at_issuance``.
        performance_dir: SF silver performance directory.

    Returns:
        ``pairs`` plus ``upb_confirmed``: True/False when the perf quarter was
        available, null when it was not (e.g. pre-2017 vintages).
    """
    tol = CRTMBSMatchingConfig.CONFIRM_MONTH_TOLERANCE
    pairs = pairs.with_columns(_perf_quarter(pl.col("lsn")))
    confirmed_ids: list[pl.DataFrame] = []
    checked = []
    for (quarter,), qp in pairs.partition_by("perf_quarter", as_dict=True).items():
        perf_file = performance_dir / f"historical_data_time_{quarter}.parquet"
        if not perf_file.exists():
            continue
        checked.append(quarter)
        lo = qp["Period"].min()
        hi = qp["Period"].max()
        hits = (
            pl.scan_parquet(perf_file)
            .select(
                pl.col("Loan Sequence Number").alias("lsn"),
                pl.col("Monthly Reporting Period").dt.month_start().alias("month"),
                pl.col("Current Actual UPB").alias("perf_upb"),
            )
            .filter(
                pl.col("month").is_between(
                    pl.lit(lo).dt.offset_by(f"-{tol}mo"), pl.lit(hi).dt.offset_by(f"{tol}mo")
                )
            )
            .join(qp.lazy().select("lsn", "Period", "upb_at_issuance"), on="lsn", how="inner")
            .filter(
                pl.col("month").is_between(
                    pl.col("Period").dt.offset_by(f"-{tol}mo"),
                    pl.col("Period").dt.offset_by(f"{tol}mo"),
                ),
                (pl.col("perf_upb") - pl.col("upb_at_issuance")).abs()
                < CRTMBSMatchingConfig.CONFIRM_UPB_TOLERANCE,
            )
            .select("lsn")
            .unique()
            .collect()
        )
        confirmed_ids.append(hits)
    logger.info("UPB confirmation ran over %s perf quarters", len(checked))

    confirmed = (
        pl.concat(confirmed_ids, how="diagonal_relaxed")
        if confirmed_ids
        else pl.DataFrame(schema={"lsn": pl.String})
    )
    quarter_checked = pl.Series("perf_quarter", checked, dtype=pl.String)
    return pairs.with_columns(
        pl.when(~pl.col("perf_quarter").is_in(quarter_checked))
        .then(None)
        .otherwise(pl.col("lsn").is_in(confirmed["lsn"]))
        .alias("upb_confirmed")
    ).drop("perf_quarter")


@log_execution_time
def run_matching_fhlmc(
    crt_dir: Path | None = None,
    origination_dir: Path | None = None,
    performance_dir: Path | None = None,
    crosswalk_file: Path | None = None,
    deals: list[str] | None = None,
    confirm: bool = True,
) -> pl.DataFrame:
    """Match Freddie CRT reference-pool loans to SF Loan Sequence Numbers.

    Per deal: the rounds in ``ROUNDS_FHLMC`` (exact joins, 1:1 both directions) against
    the FPD-windowed SF origination silver, then (optionally) UPB-at-issuance
    confirmation against the SF performance panel. Writes the crosswalk and a
    per-deal summary, and returns the summary.

    Args:
        crt_dir: CRT silver directory. Defaults to fhlmc ``silver/crt``.
        origination_dir: SF origination silver. Defaults to fhlmc
            ``silver/origination``.
        performance_dir: SF performance silver. Defaults to fhlmc
            ``silver/performance``.
        crosswalk_file: Output parquet. Defaults to
            ``crosswalk/crt_mbs/crt_mbs_crosswalk_fhlmc.parquet``.
        deals: If provided, only match these Reference Pool Numbers.
        confirm: Run the UPB-at-issuance confirmation pass (default True).

    Returns:
        Per-deal summary DataFrame (loans, pins by round as ``n_t{k}``, pin/confirm rates).
    """
    fhlmc = CRTMBSMatchingConfig.get_fhlmc_config()
    crt_dir = crt_dir or (fhlmc.FHLMC_SILVER_DIR / "crt")
    origination_dir = origination_dir or (fhlmc.FHLMC_SILVER_DIR / "origination")
    performance_dir = performance_dir or (fhlmc.FHLMC_SILVER_DIR / "performance")
    crosswalk_file = crosswalk_file or CRTMBSMatchingConfig.FHLMC_CROSSWALK_FILE
    CRTMBSMatchingConfig.ensure_directories()

    index = _crt_deal_index(crt_dir)
    if deals is not None:
        index = index.filter(pl.col("Reference Pool Number").is_in(deals))
    logger.info("Matching %s CRT deals against %s", index.height, origination_dir)

    all_pairs: list[pl.DataFrame] = []
    summaries: list[dict] = []
    for row in index.iter_rows(named=True):
        deal = row["Reference Pool Number"]
        crt = _load_crt_deal(crt_dir, deal)
        sf = _load_sf_window(origination_dir, row["fpd_min"], row["fpd_max"])
        if sf.is_empty():
            logger.warning("%s: no SF origination coverage for FPD window, skipping", deal)
            summaries.append({"deal": deal, "n_loans": crt.height, "n_pinned": 0})
            continue
        matched = _match_deal(crt, sf)
        pairs = matched.join(
            crt.select("crt_id", "reference_pool", "Period", "upb_at_issuance"), on="crt_id"
        )
        all_pairs.append(pairs)
        summary = {"deal": deal, "n_loans": crt.height, "n_pinned": matched.height}
        for match_round in range(1, len(ROUNDS_FHLMC) + 1):
            summary[f"n_t{match_round}"] = matched.filter(
                pl.col("match_round") == match_round
            ).height
        summaries.append(summary)
        logger.info(
            "%s: %s/%s pinned (%.1f%%)",
            deal,
            f"{matched.height:,}",
            f"{crt.height:,}",
            matched.height / crt.height * 100,
        )

    if not all_pairs:
        logger.warning("No deals produced any pins; nothing written")
        return pl.DataFrame(summaries)

    crosswalk = pl.concat(all_pairs, how="diagonal_relaxed")
    if confirm:
        crosswalk = confirm_pairs(crosswalk, performance_dir)
    else:
        crosswalk = crosswalk.with_columns(pl.lit(None, dtype=pl.Boolean).alias("upb_confirmed"))

    crosswalk = crosswalk.select(
        pl.col("reference_pool").alias("Reference Pool Number"),
        pl.col("crt_id").alias("Loan Identifier"),
        pl.col("lsn").alias("Loan Sequence Number"),
        "Period",
        "match_round",
        "upb_confirmed",
    ).sort("Reference Pool Number", "Loan Identifier")
    with atomic_output(crosswalk_file) as tmp:
        crosswalk.write_parquet(tmp)
    logger.info("Wrote %s pairs -> %s", f"{crosswalk.height:,}", crosswalk_file)

    # Fold confirmation counts into the per-deal summary.
    conf = (
        crosswalk.group_by("Reference Pool Number")
        .agg(
            (pl.col("upb_confirmed") == True).sum().alias("n_confirmed"),  # noqa: E712
            pl.col("upb_confirmed").is_null().sum().alias("n_unverifiable"),
        )
        .rename({"Reference Pool Number": "deal"})
    )
    summary_df = (
        pl.DataFrame(summaries)
        .join(conf, on="deal", how="left")
        .with_columns((pl.col("n_pinned") / pl.col("n_loans") * 100).round(2).alias("pct_pinned"))
        .sort("deal")
    )
    summary_df.write_parquet(CRTMBSMatchingConfig.FHLMC_DEAL_SUMMARY_FILE)
    total, pinned = int(summary_df["n_loans"].sum()), int(summary_df["n_pinned"].sum())
    logger.info("Overall: %s/%s pinned (%.2f%%)", f"{pinned:,}", f"{total:,}", pinned / total * 100)
    return summary_df
