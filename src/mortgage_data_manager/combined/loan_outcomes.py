"""The ``loan_outcomes`` master: one row per loan, collapsed from ``loan_performance``."""

from __future__ import annotations

import shutil
from pathlib import Path

import polars as pl

from mortgage_data_manager.combined._common import (
    _build_dataset,
    _gnma_num,
    _gnma_silver_dir,
    _period_index,
)
from mortgage_data_manager.combined.config import CombinedConfig
from mortgage_data_manager.combined.loan_performance import _GNMA_LLMON_PROGRAMS
from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import recreate_dir

logger = get_logger(__name__)


# ===================== loan_outcomes (one row per loan) =======================

# loan_outcomes: number of loan_key-hash shards for the reduce step, so peak memory
# is ~1/N of an agency's per-file partials (themselves ~10x smaller than the panel).
_LOAN_OUTCOMES_SHARDS = 8


#: Modification-flag letters that mean "modified" (FNMA Y/N Boolean-as-text, FHLMC
#: Y = modified / P = prior modification, both null when not modified).
_MODIFIED_FLAGS: tuple[str, ...] = ("Y", "P")


#: Borrower-assistance plan letters that mean a plan was in effect (F forbearance,
#: R repayment, T trial-period modification); N = none, 7/9 are scrubbed upstream.
_ASSISTANCE_PLANS: tuple[str, ...] = ("F", "R", "T")


#: Outcome columns that are only meaningful where the agency discloses the input.
#: Each maps to the partial-aggregate flag saying "this loan carried the input at
#: least once"; an agency with no such loan gets the whole column nulled (GNMA has
#: no modification flag or assistance plan). Within a disclosing agency, absence
#: means no -- the GSEs leave the flag null on unmodified loans rather than 'N'.
_OUTCOME_INFO_GATES: dict[str, str] = {
    "ever_modified": "_mod_info",
    "ever_assistance_plan": "_plan_info",
    "ever_forbearance": "_plan_info",
}


def _only(values: pl.Expr, n: pl.Expr) -> pl.Expr:
    """``values``' single element when ``n`` is exactly 1, else null -- never a pick among ties."""
    # tie-safe: taken only when exactly one row qualifies
    return pl.when(n == 1).then(values.first()).otherwise(None)


def _sole_value(col: str) -> pl.Expr:
    """``col``'s value when the group carries exactly one distinct value, else null."""
    # tie-safe: taken only when the group holds a single distinct value
    return pl.when(pl.col(col).n_unique() == 1).then(pl.col(col).first()).otherwise(None)


def loan_outcome_partial_aggs() -> list[pl.Expr]:
    """Map step: per-loan partial aggregates over ONE ``loan_performance`` file.

    Every panel file holds one agency x reporting_year slice, so a loan's history
    is spread over several files; these partials are all decomposable (min/max/
    any/len, "value at the latest month", "value on the event row") and
    `loan_outcome_reduce_aggs` folds them per loan. Applied inside
    ``group_by("loan_key")``.

    Returns:
        The partial aggregate expressions, to apply per loan per file.
    """
    period = pl.col("reporting_period")
    dq = pl.col("delinquency_months")
    mod_flag = pl.col("modification_flag").cast(pl.String).str.to_uppercase()
    n_mods = pl.col("number_of_modifications")
    plan = pl.col("borrower_assistance_plan")
    ev = pl.col("event_flag").fill_null(value=False)
    upb = pl.col("current_upb")
    source = pl.col("month_source")

    def last_where(col: str, valid: pl.Expr) -> pl.Expr:
        # the value on the latest month where `valid` holds; null if two rows share it.
        # Terminal rows leave balance, age and status blank, so "latest" means latest
        # disclosed, not the terminal row itself.
        at_latest = valid & (period == period.filter(valid).max())
        return _only(pl.col(col).filter(at_latest), at_latest.sum())

    def at_event(col: str) -> pl.Expr:
        return last_where(col, ev)

    return [
        _sole_value("loan_key_kind").alias("loan_key_kind"),
        _sole_value("source_universe").alias("source_universe"),
        period.min().alias("first_reporting_period"),
        period.max().alias("last_reporting_period"),
        pl.len().cast(pl.Int32).alias("n_months_observed"),
        last_where("current_upb", upb > 0).alias("last_upb"),
        period.filter(upb > 0).max().alias("last_upb_period"),
        last_where("loan_age", pl.col("loan_age").is_not_null()).alias("_age"),
        period.filter(pl.col("loan_age").is_not_null()).max().alias("_age_period"),
        last_where("delinquency_months", dq.is_not_null()).alias("last_delinquency_months"),
        last_where("servicer_name", pl.col("servicer_name").is_not_null()).alias(
            "last_servicer_name"
        ),
        # GNMA has one stream (null month_source); a GSE loan's SF and UMBS months end
        # at different frontiers.
        period.filter(source.ne_missing("umbs_only")).max().alias("_last_sf_period"),
        period.filter(source.is_in(["both", "umbs_only"])).max().alias("_last_umbs_period"),
        dq.max().alias("peak_delinquency_months"),
        period.filter(dq >= 3).min().alias("first_90_period"),
        (mod_flag.is_in(list(_MODIFIED_FLAGS)).any() | (n_mods > 0).any()).alias("_modified"),
        (mod_flag.is_not_null().any() | n_mods.is_not_null().any()).alias("_mod_info"),
        plan.is_in(list(_ASSISTANCE_PLANS)).any().alias("_plan"),
        (plan == "F").any().alias("_forbearance"),
        plan.is_not_null().any().alias("_plan_info"),
        ev.any().alias("terminated"),
        period.filter(ev).max().alias("termination_period"),
        at_event("event_type").alias("event_type"),
        at_event("event_class").alias("event_class"),
        at_event("is_credit_event").alias("is_credit_event"),
        at_event("zero_balance_effective_date").alias("zero_balance_effective_date"),
    ]


def loan_outcome_reduce_aggs() -> list[pl.Expr]:
    """Reduce step: fold a loan's per-file partials into one outcome row.

    Applied inside ``group_by("loan_key")`` over the output of
    `loan_outcome_partial_aggs`. "Latest" columns follow the partial with the
    greatest ``last_reporting_period``; event columns take the single non-null
    partial (a loan has at most one terminal row).

    Returns:
        The reduce expressions, folding a loan's partials into one outcome row.
    """
    last_period = pl.col("last_reporting_period")

    def latest(col: str, by: str = "last_reporting_period") -> pl.Expr:
        # the partial with the greatest `by` period; null if two partials tie
        at_latest = pl.col(by) == pl.col(by).max()
        return _only(pl.col(col).filter(at_latest), at_latest.sum())

    def event(col: str) -> pl.Expr:
        # the single non-null partial value; null if the partials disagree
        values = pl.col(col).drop_nulls()
        return _only(values, values.n_unique())

    return [
        _sole_value("loan_key_kind").alias("loan_key_kind"),
        _sole_value("source_universe").alias("source_universe"),
        pl.col("first_reporting_period").min().alias("first_reporting_period"),
        last_period.max().alias("last_reporting_period"),
        pl.col("n_months_observed").sum().cast(pl.Int32).alias("n_months_observed"),
        latest("last_upb", "last_upb_period").alias("last_upb"),
        pl.col("last_upb_period").max().alias("last_upb_period"),
        latest("_age", "_age_period").alias("_age"),
        pl.col("_age_period").max().alias("_age_period"),
        latest("last_delinquency_months").alias("last_delinquency_months"),
        latest("last_servicer_name").alias("last_servicer_name"),
        pl.col("_last_sf_period").max().alias("_last_sf_period"),
        pl.col("_last_umbs_period").max().alias("_last_umbs_period"),
        pl.col("peak_delinquency_months").max().alias("peak_delinquency_months"),
        pl.col("first_90_period").min().alias("first_90_period"),
        pl.col("_modified").any().alias("ever_modified"),
        pl.col("_mod_info").any().alias("_mod_info"),
        pl.col("_plan").any().alias("ever_assistance_plan"),
        pl.col("_forbearance").any().alias("ever_forbearance"),
        pl.col("_plan_info").any().alias("_plan_info"),
        pl.col("terminated").any().alias("terminated"),
        pl.col("termination_period").max().alias("termination_period"),
        event("event_type").alias("event_type"),
        event("event_class").alias("event_class"),
        event("is_credit_event").alias("is_credit_event"),
        event("zero_balance_effective_date").alias("zero_balance_effective_date"),
    ]


def _outcome_frontiers(partials: pl.LazyFrame) -> pl.LazyFrame:
    """The last SF and UMBS month of each ``source_universe`` (each is its own release)."""
    return partials.group_by("source_universe").agg(
        pl.col("_last_sf_period").max().alias("_frontier_sf"),
        pl.col("_last_umbs_period").max().alias("_frontier_umbs"),
    )


def _finalize_loan_outcomes(
    lf: pl.LazyFrame, agency_info: dict[str, bool], frontiers: pl.LazyFrame
) -> pl.LazyFrame:
    """Derive the delinquency, age and frontier columns; gate undisclosed outcomes to null.

    Loan age advances one per calendar month, so the latest disclosed age is carried
    forward to the loan's last month and to its event month. A loan is observed at the
    frontier when its SF or UMBS months reach the last month of that stream in its own
    ``source_universe`` (`_outcome_frontiers`).

    Args:
        lf: Reduced outcome rows (`loan_outcome_reduce_aggs`).
        agency_info: Whether the agency discloses each gated input (``_mod_info``,
            ``_plan_info``).
        frontiers: `_outcome_frontiers` of the agency's partials.

    Returns:
        The finalized outcome frame, helper columns dropped.
    """
    peak = pl.col("peak_delinquency_months")
    gated = [
        (pl.col(col) if agency_info[gate] else pl.lit(None, dtype=pl.Boolean)).alias(col)
        for col, gate in _OUTCOME_INFO_GATES.items()
    ]

    def age_at(period: str) -> pl.Expr:
        return pl.col("_age") + _period_index(pl.col(period)) - _period_index(pl.col("_age_period"))

    at_frontier = [
        (pl.col(f"_last_{stream}_period") == pl.col(f"_frontier_{stream}")).fill_null(value=False)
        for stream in ("sf", "umbs")
    ]
    lf = lf.join(frontiers, on="source_universe", how="left", nulls_equal=True)
    return lf.with_columns(
        (peak >= 1).alias("ever_30"),
        (peak >= 2).alias("ever_60"),
        (peak >= 3).alias("ever_90"),
        (peak >= 6).alias("ever_180"),
        age_at("last_reporting_period").alias("last_loan_age"),
        age_at("termination_period").alias("loan_age_at_termination"),
        # False for a loan that left the disclosure without a terminal event.
        pl.any_horizontal(at_frontier).alias("observed_at_frontier"),
        *gated,
    ).drop(
        "_mod_info",
        "_plan_info",
        "_age",
        "_age_period",
        "_last_sf_period",
        "_last_umbs_period",
        "_frontier_sf",
        "_frontier_umbs",
    )


def _fhlmc_rpl_map() -> pl.LazyFrame:
    """Freddie's SF loan id -> seasoned-deal (SCRT/SLST) loan id map, standard + non-standard."""
    rpl_dir = MortgageDataConfig.get_subpackage_data_dir("fhlmc") / "bronze" / "reperforming"
    files = sorted(rpl_dir.glob("SFLLD_RPL_ID_Match*.parquet"))
    if not files:
        logger.warning("No FHLMC RPL id map under %s; is_rpl will be null", rpl_dir)
        return pl.LazyFrame(
            schema={
                "loan_key": pl.String,
                "rpl_deal_loan_id": pl.String,
                "rpl_deal_type": pl.String,
            }
        )
    return (
        pl.concat([pl.scan_parquet(f) for f in files], how="diagonal_relaxed")
        .select(
            pl.col("id_loan").alias("loan_key"),
            pl.col("loan_identifier").alias("rpl_deal_loan_id"),
            pl.col("loan_identifier").str.slice(4, 4).alias("rpl_deal_type"),
        )
        # one RPL deal id per loan; a loan mapped to two different ids gets neither
        .unique()
        .unique(subset="loan_key", keep="none")
    )


def _months_between(later: pl.Expr, earlier: pl.Expr) -> pl.Expr:
    """Whole months from an ``earlier`` Date to a ``later`` YYYYMM int period."""
    return _period_index(later) - (earlier.dt.year() * 12 + earlier.dt.month())


def _gnma_first_month_statics() -> pl.LazyFrame:
    """First-payment date and original balance of the loans in Ginnie's first llmon month.

    Ginnie's issuance feed starts 2013-09, so the loans already outstanding then have
    no ``loan_issuance`` row. The first monthly panel file carries both fields for them.
    A loan whose rows disagree gets neither.

    Returns:
        ``agency``, ``loan_key``, ``first_payment_date``, ``original_upb``.
    """
    frames = []
    for family, _ in _GNMA_LLMON_PROGRAMS:
        files = sorted(_gnma_silver_dir(family, "L").glob(f"{family}_*_L.parquet"))
        if files:
            frames.append(
                pl.scan_parquet(str(files[0])).select(
                    pl.col("disclosure_sequence_number").cast(pl.String).alias("loan_key"),
                    pl.col("first_payment_date"),
                    _gnma_num(pl.col("original_principal_balance")).alias("original_upb"),
                )
            )
    if not frames:
        logger.warning("No GNMA llmon files; pre-2013-09 loans keep null issuance statics")
        return pl.LazyFrame(
            schema={
                "agency": pl.String,
                "loan_key": pl.String,
                "first_payment_date": pl.Date,
                "original_upb": pl.Float64,
            }
        )
    return (
        pl.concat(frames)
        .unique()
        .unique(subset="loan_key", keep="none")
        .select(pl.lit("GNMA").alias("agency"), pl.all())
    )


def _loan_outcomes_partials(agency_dir: Path, tmp_dir: Path) -> list[Path]:
    """Map step over one agency: one partial parquet per panel file."""
    files = sorted(agency_dir.glob("reporting_year=*/*.parquet"))
    out: list[Path] = []
    for i, f in enumerate(files):
        part = tmp_dir / f"{agency_dir.name}_{i:04d}.parquet"
        pl.scan_parquet(str(f)).group_by("loan_key").agg(loan_outcome_partial_aggs()).sink_parquet(
            part
        )
        out.append(part)
    return out


def build_loan_outcomes(*, overwrite: bool = False) -> pl.LazyFrame:
    """Build the loan-grain outcome summary (one row per loan, tri-agency).

    Collapses each loan's ``loan_performance`` history to the facts most analyses
    want without touching the ~8B-row panel: observation window and whether it reaches
    the disclosure frontier (``observed_at_frontier``; a GSE loan can leave the UMBS
    disclosure without a terminal event), the latest disclosed balance (with its
    period), age, status and servicer, peak delinquency and the ``ever_30/60/90/180``
    flags, modification and assistance-plan flags, and the terminal event (type,
    class, credit flag, period, loan age at termination). The GSE loss waterfall is
    NOT repeated here -- ``loan_terminations`` carries it and joins on ``loan_key``;
    only ``zero_balance_code``, ``upb_at_removal`` and ``actual_loss`` ride along.
    ``first_payment_date`` / ``original_upb`` come from ``loan_issuance`` (for Ginnie
    loans outstanding before its issuance feed starts, from the first monthly panel
    file) and give ``months_to_termination``; Freddie loans later sold into a seasoned
    deal carry ``is_rpl`` plus the SCRT/SLST deal loan id from the RPL id map.

    Two-level aggregation: every panel file (one agency x reporting_year slice) is
    collapsed to per-loan partials once, then the partials are folded per loan in
    hash shards -- the panel is read exactly once. Output is hive-partitioned by
    ``agency`` and ``orig_year`` (first-payment year, else first observed year).

    Args:
        overwrite: Rebuild if the output already exists.

    Returns:
        A LazyFrame scanning the persisted combined output.
    """
    return _build_dataset("loan_outcomes", _write_loan_outcomes, overwrite=overwrite)


def _write_loan_outcomes(out_dir: Path) -> None:
    """Write the ``loan_outcomes`` tree into ``out_dir`` (see `build_loan_outcomes`)."""
    perf_dir = CombinedConfig.dataset_path("loan_performance")
    term_dir = CombinedConfig.dataset_path("loan_terminations")
    iss_dir = CombinedConfig.dataset_path("loan_issuance")
    for required in (perf_dir, term_dir, iss_dir):
        if not required.exists():
            raise FileNotFoundError(f"loan_outcomes needs {required.name} built first: {required}")

    tmp_dir = CombinedConfig.COMBINED_DATA_DIR / "_tmp_loan_outcomes"
    recreate_dir(tmp_dir)

    terminations = pl.scan_parquet(str(term_dir), hive_partitioning=True).select(
        "agency", "loan_key", "zero_balance_code", "upb_at_removal", "actual_loss"
    )
    issuance = pl.scan_parquet(str(iss_dir), hive_partitioning=True).select(
        "agency", "loan_key", "first_payment_date", "original_upb"
    )
    gnma_seed = (  # collected once: the shard loop re-reads `issuance` per shard
        _gnma_first_month_statics()
        .join(issuance, on=["agency", "loan_key"], how="anti")
        .collect(engine="streaming")
    )
    issuance = pl.concat([issuance, gnma_seed.lazy()], how="vertical_relaxed")
    rpl = _fhlmc_rpl_map()

    agency_dirs = sorted(perf_dir.glob("agency=*"))
    logger.info(
        "Building combined loan_outcomes (%s) -> %s",
        "+".join(d.name.split("=", 1)[1] for d in agency_dirs),
        out_dir,
    )
    total = 0
    for agency_dir in agency_dirs:
        agency = agency_dir.name.split("=", 1)[1]
        partial_files = _loan_outcomes_partials(agency_dir, tmp_dir)
        partials = pl.scan_parquet([str(p) for p in partial_files])
        agency_info = (
            partials.select(pl.col("_mod_info").any(), pl.col("_plan_info").any())
            .collect(engine="streaming")
            .row(0, named=True)
        )
        frontiers = _outcome_frontiers(partials).collect(engine="streaming").lazy()
        logger.info(
            "  %s: %d partial files; discloses modification=%s assistance=%s",
            agency,
            len(partial_files),
            agency_info["_mod_info"],
            agency_info["_plan_info"],
        )
        for shard in range(_LOAN_OUTCOMES_SHARDS):
            reduced = (
                partials.filter(pl.col("loan_key").hash(seed=0) % _LOAN_OUTCOMES_SHARDS == shard)
                .group_by("loan_key")
                .agg(loan_outcome_reduce_aggs())
                .with_columns(pl.lit(agency).alias("agency"))
            )
            frame = (
                _finalize_loan_outcomes(reduced, agency_info, frontiers)
                .join(terminations, on=["agency", "loan_key"], how="left")
                .join(issuance, on=["agency", "loan_key"], how="left")
                .join(rpl, on="loan_key", how="left")
                .with_columns(
                    pl.when(pl.col("agency") == "FHLMC")
                    .then(pl.col("rpl_deal_loan_id").is_not_null())
                    .otherwise(None)
                    .alias("is_rpl"),
                    _months_between(pl.col("termination_period"), pl.col("first_payment_date"))
                    .cast(pl.Int32)
                    .alias("months_to_termination"),
                    pl.coalesce(
                        pl.col("first_payment_date").dt.year().cast(pl.Int64),
                        pl.col("first_reporting_period") // 100,
                    ).alias("orig_year"),
                )
                .collect(engine="streaming")
            )
            for key, part in frame.partition_by("agency", "orig_year", as_dict=True).items():
                ag, year = key
                part_dir = out_dir / f"agency={ag}" / f"orig_year={year}"
                part_dir.mkdir(parents=True, exist_ok=True)
                part.drop("agency", "orig_year").write_parquet(
                    part_dir / f"{ag.lower()}_{year}_s{shard}.parquet"
                )
            total += frame.height
            logger.debug("    %s/shard %d: %d loans", agency, shard, frame.height)
        for p in partial_files:
            p.unlink(missing_ok=True)
        logger.info("  %s: done", agency)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    logger.info("loan_outcomes built: %d loans -> %s", total, out_dir)
