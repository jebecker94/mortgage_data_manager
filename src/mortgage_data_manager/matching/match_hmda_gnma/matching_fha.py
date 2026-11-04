"""HMDA-GNMA matching for FHA loans (the FHA half of the spine's GNMA edge).

One direct match writes ``hmda_gnma_crosswalk_fha.parquet``. Each FHA-matched HMDA loan
inherits its FHA record through the hmda_fha crosswalk: the exact dollar amount (a hard key
against GNMA's $1,000-floored balance) and the endorsement month, alongside HMDA's exact
rate and term. Candidates are GNMA FHA loans, scored on units, borrowers, MIP-aware CLTV,
month proximity, LEI-issuer dyads and one-sided DTI consistency; a pair must be the strictly
unique best on both sides. The rounds (block keys, the score each needs, the month window)
are `ROUNDS_FHA` in ``rounds.py``. The dyad map is learned from a first pass without it.

A pair is admitted on the score floor, or when every present piece of evidence agrees and
CLTV and DTI are missing rather than contradicted (streamline refinances carry neither).

There is no separate FHA -> GNMA leg: it would add little, because the direct match already
reaches the same GNMA loan for nearly every pair such a leg finds, and where the two disagree
the direct pick is the better one. See ``docs/matching/hmda_gnma_matching.md``.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.harmonized import (
    HMDA_LOAN_PURPOSE,
    PURPOSE_CLASS,
    encode,
    gnma_loan_purpose,
    recode,
)
from mortgage_data_manager.core.logging import get_logger, log_execution_time
from mortgage_data_manager.core.medallion import atomic_output
from mortgage_data_manager.matching.match_hmda_gnma.config import HMDAGNMAMatchingConfig as Config
from mortgage_data_manager.matching.match_hmda_gnma.rounds import ROUNDS_FHA
from mortgage_data_manager.matching.resolve import unique_best
from mortgage_data_manager.sources.hmda.load import scan_hmda_for_index

logger = get_logger(__name__)

# Matcher alias -> GNMA dailyllmni canonical column id.
_GNMA_COLS: dict[str, str] = {
    "gnma_loan_id": "disclosure_sequence_number",
    "issuer_id": "issuer_id",
    "agency": "agency",
    "gnma_state": "state",
    "gnma_opb": "original_principal_balance",
    "gnma_rate": "loan_interest_rate",
    "gnma_term": "original_loan_term_in_months",
    "gnma_purpose": "loan_purpose",
    "gnma_orig_date": "loan_origination_date",
    "gnma_cltv": "combined_ltv",
    "gnma_dti": "total_debt_expense_ratio_percent",
    "gnma_units": "property_type",
    "gnma_num_borrowers": "number_of_borrowers",
}

_GNMA_FLOAT_COLS = ["gnma_opb", "gnma_rate", "gnma_cltv", "gnma_dti"]
_GNMA_INT_COLS = ["gnma_term", "gnma_units", "gnma_num_borrowers"]

_HMDA_COLS = [
    "HMDAIndex",
    "lei",
    "state_code",
    "interest_rate",
    "loan_term",
    "loan_purpose",
    "combined_loan_to_value_ratio",
    "debt_to_income_ratio",
    "total_units",
    "co_applicant_sex",
]


def _hmda_purpose_cat() -> pl.Expr:
    """Coarse purpose category from HMDA loan_purpose (P / R / O)."""
    return recode(pl.col("loan_purpose"), HMDA_LOAN_PURPOSE, PURPOSE_CLASS).fill_null("O")


def _gnma_purpose_cat() -> pl.Expr:
    """Coarse purpose category from GNMA Loan Purpose (P / R; null otherwise).

    Ginnie's loan-level layout codes 3 / 4 as loan modifications and 5 as re-performing:
    re-pooled loans, not originations, so they get no category and never join an HMDA loan.
    """
    return encode(gnma_loan_purpose(pl.col("gnma_purpose"), None), PURPOSE_CLASS)


def _dti_one_sided() -> pl.Expr:
    """One-sided DTI consistency (HMDA integer DTI is a floor; GNMA carries decimals)."""
    h = pl.col("debt_to_income_ratio")
    g = pl.col("gnma_dti")
    return (
        ((h == 10) & (g < 20))
        | ((h == 20) & (g >= 20) & (g < 30))
        | ((h == 30) & (g >= 30) & (g < 36))
        | ((h >= 36) & (h <= 49) & (g >= h) & (g < h + 1))
        | ((h == 50) & (g >= 50) & (g < 60))
        | ((h == 60) & (g >= 60))
    )


def load_hmda_fha_universe(min_year: int, max_year: int) -> pl.DataFrame:
    """HMDA FHA originations for the year range, on the releases the hmda_fha crosswalk used.

    ``HMDAIndex`` embeds its release, so the universe is read from exactly the partitions
    the hmda_fha crosswalk's indices name.

    Args:
        min_year: First activity year to include, inclusive.
        max_year: Last activity year to include, inclusive.

    Returns:
        HMDA FHA originations on the releases the crosswalk used.
    """
    crosswalk = pl.scan_parquet(Config.HMDA_FHA_CROSSWALK_PATH).select("HMDAIndex")
    hmda = (
        scan_hmda_for_index(crosswalk, "post2018", root=Config.HMDA_SILVER_DIR)
        .filter(
            pl.col("activity_year").is_between(min_year, max_year)
            & (pl.col("loan_type") == 2)
            & (pl.col("action_taken") == 1)
        )
        .select(*_HMDA_COLS, pl.col("activity_year").cast(pl.Int32))
        .collect(engine="streaming")
    )
    logger.info("HMDA FHA universe: %s", f"{len(hmda):,}")
    return hmda


def load_gnma_fha_universe(
    min_pool_yyyymm: str = "201801", agencies: tuple[str, ...] = ("F",)
) -> pl.DataFrame:
    """GNMA loans (by Agency code) from dailyllmni new-issuance files, deduped on DSN.

    Args:
        min_pool_yyyymm: Earliest pool month to read, as ``YYYYMM``.
        agencies: GNMA agency codes to keep.

    Returns:
        GNMA loans for those agency codes, deduped on DSN.
    """
    files = sorted(Config.GNMA_SILVER_DIR.glob("dailyllmni_*_L.parquet"))
    files = [f for f in files if f.stem.split("_")[1] >= min_pool_yyyymm]
    sel: list[pl.Expr] = []
    for alias, src in _GNMA_COLS.items():
        if alias == "gnma_orig_date":
            sel.append(pl.col(src).dt.year().cast(pl.Int32).alias("gnma_orig_year"))
            sel.append(pl.col(src).dt.month().cast(pl.Int32).alias("gnma_orig_month"))
            continue
        expr = pl.col(src)
        if alias in _GNMA_FLOAT_COLS:
            expr = expr.cast(pl.Float64, strict=False)
        elif alias in _GNMA_INT_COLS:
            expr = expr.cast(pl.Float64, strict=False).cast(pl.Int64, strict=False)
        else:
            expr = expr.cast(pl.Utf8)
        sel.append(expr.alias(alias))
    # Loan Purpose 1 / 2 only: 3 / 4 are loan modifications and 5 re-performing loans
    # (Ginnie loan-level layout, every version), re-pooled rather than originated.
    frames = [
        pl.scan_parquet(f)
        .select(sel)
        .filter(pl.col("agency").is_in(list(agencies)) & pl.col("gnma_purpose").is_in(["1", "2"]))
        for f in files
    ]
    gnma = (
        pl.concat(frames, how="diagonal_relaxed")
        # one row per DSN: identical copies collapse, a DSN whose rows disagree is excluded
        .unique()
        .unique(subset=["gnma_loan_id"], keep="none")
        .collect(engine="streaming")
    )
    logger.info("GNMA FHA universe: %s", f"{len(gnma):,}")
    return gnma


def load_fha_attributes(min_year: int, max_year: int) -> pl.DataFrame:
    """FHA endorsement attributes: exact amount, rate, and endorsement month index.

    Args:
        min_year: First activity year to include, inclusive.
        max_year: Last activity year to include, inclusive.

    Returns:
        Per-loan FHA amount, rate and endorsement month index.
    """
    return (
        pl.scan_parquet(f"{Config.FHA_SILVER_DIR}/**/*.parquet")
        .filter(pl.col("Year").is_between(min_year - 1, max_year + 1))
        .select(
            "FHA_Index",
            pl.col("Mortgage Amount").cast(pl.Int64).alias("fha_amount"),
            pl.col("Interest Rate").cast(pl.Float64).alias("fha_rate"),
            (pl.col("Year").cast(pl.Int32) * 12 + pl.col("Month").cast(pl.Int32)).alias(
                "endorse_idx"
            ),
        )
        .collect(engine="streaming")
    )


def build_lei_issuer_dyads(
    pairs: pl.DataFrame, hmda: pl.DataFrame, gnma: pl.DataFrame
) -> pl.DataFrame:
    """(LEI, Issuer ID) pair counts observed on accepted matches (the correspondent map).

    Args:
        pairs: Accepted HMDA-GNMA pairs (``HMDAIndex``, ``gnma_loan_id``).
        hmda: The HMDA side's loan universe.
        gnma: The GNMA side's loan universe.

    Returns:
        Observed ``(LEI, Issuer ID)`` pair counts.
    """
    dyads = (
        pairs.select("HMDAIndex", "gnma_loan_id")
        .join(hmda.select("HMDAIndex", "lei"), on="HMDAIndex", how="inner")
        .join(gnma.select("gnma_loan_id", "issuer_id"), on="gnma_loan_id", how="inner")
        .group_by(["lei", "issuer_id"])
        .agg(pl.len().alias("dyad_n"))
        .filter(pl.col("dyad_n") >= Config.DYAD_MIN_COUNT)
    )
    logger.info("LEI-issuer dyads (n >= %d): %s", Config.DYAD_MIN_COUNT, f"{len(dyads):,}")
    return dyads


def _score_candidates(cand: pl.DataFrame, dyads: pl.DataFrame) -> pl.DataFrame:
    """Score block-join candidates on fields outside the block key (max total 15)."""
    cand = cand.join(dyads, on=["lei", "issuer_id"], how="left")
    cltv_ok = pl.col("combined_loan_to_value_ratio").is_between(1, 200) & pl.col(
        "gnma_cltv"
    ).is_between(1, 200)
    mip_dist = pl.min_horizontal(
        (pl.col("gnma_cltv") - pl.col("combined_loan_to_value_ratio")).abs(),
        (pl.col("gnma_cltv") - pl.col("combined_loan_to_value_ratio") * Config.MIP_FACTOR).abs(),
    )
    cand = cand.with_columns(
        pl.when(pl.col("gnma_units") == pl.col("total_units"))
        .then(3)
        .otherwise(0)
        .alias("s_units"),
        pl.when(pl.col("gnma_num_borrowers").clip(upper_bound=2) == pl.col("hmda_num_borrowers"))
        .then(2)
        .otherwise(0)
        .alias("s_borr"),
        pl.when(cltv_ok & (mip_dist <= 0.25))
        .then(4)
        .when(cltv_ok & (mip_dist <= 1.0))
        .then(3)
        .when(cltv_ok & (mip_dist <= 5.0))
        .then(1)
        .otherwise(0)
        .alias("s_cltv"),
        pl.when(pl.col("month_diff").is_between(0, 1)).then(1).otherwise(0).alias("s_month"),
        pl.when(pl.col("dyad_n").is_not_null()).then(2).otherwise(0).alias("s_dyad"),
        pl.when(_dti_one_sided()).then(3).otherwise(0).alias("s_dti"),
        # missing, as opposed to present and disagreeing: the absence admission needs it
        (~cltv_ok).fill_null(value=True).alias("cltv_absent"),
        (pl.col("debt_to_income_ratio").is_null() | pl.col("gnma_dti").is_null()).alias(
            "dti_absent"
        ),
    )
    return cand.with_columns(
        (
            pl.col("s_units")
            + pl.col("s_borr")
            + pl.col("s_cltv")
            + pl.col("s_month")
            + pl.col("s_dyad")
            + pl.col("s_dti")
        ).alias("total_score")
    )


def admitted(floor: int) -> pl.Expr:
    """Whether an accepted pair enters the crosswalk: the score floor, or absent evidence.

    A streamline refinance carries no DTI and often no usable CLTV, so it can reach at most
    8 of 15 points and never the floor, however exact the match. It is admitted when every
    other component scores in full (units, borrowers, month, dyad) and neither CLTV nor DTI
    disagrees -- each is scored or missing. A present, disagreeing value still blocks it.

    Args:
        floor: The ``total_score`` floor.

    Returns:
        A boolean expression over the scored pair columns.
    """
    absent_ok = (
        (pl.col("s_units") == 3)
        & (pl.col("s_borr") == 2)
        & (pl.col("s_month") == 1)
        & (pl.col("s_dyad") == 2)
        & ((pl.col("s_cltv") > 0) | pl.col("cltv_absent"))
        & ((pl.col("s_dti") > 0) | pl.col("dti_absent"))
    )
    return (pl.col("total_score") >= floor) | absent_ok


def _unique_best_1to1(cand: pl.DataFrame) -> pl.DataFrame:
    """Keep pairs that are the strictly-unique best for BOTH sides.

    The former trailing sort-and-fixpoint loop was dead code: the two strict filters
    already leave at most one row per key on each side (pinned by
    ``test_unique_best_fixpoint_loop_is_vestigial``).

    Returns:
        The pairs that are the strictly-unique best for both sides.
    """
    return unique_best(cand, "total_score", "HMDAIndex", "gnma_loan_id", descending=True)


def run_direct_match(
    hmda: pl.DataFrame,
    gnma: pl.DataFrame,
    min_year: int,
    max_year: int,
    dyads: pl.DataFrame,
) -> pl.DataFrame:
    """FHA-enriched direct match of every FHA-matched HMDA loan against GNMA FHA loans.

    Runs the rounds in `ROUNDS_FHA`, each on the loans earlier rounds left unmatched.
    Returns accepted 1:1 matches with score components and absence flags; apply
    `admitted` downstream.

    Args:
        hmda: The HMDA side's loan universe.
        gnma: The GNMA side's loan universe.
        min_year: First activity year to include, inclusive.
        max_year: Last activity year to include, inclusive.
        dyads: ``(lei, issuer_id, dyad_n)`` correspondent map; empty for the first pass.

    Returns:
        Accepted 1:1 matches with their score components.
    """
    hmda_fha = pl.read_parquet(Config.HMDA_FHA_CROSSWALK_PATH).select("FHA_Index", "HMDAIndex")
    targets = (
        hmda.join(hmda_fha, on="HMDAIndex", how="inner")
        .join(load_fha_attributes(min_year, max_year), on="FHA_Index", how="left")
        .with_columns(
            ((pl.col("fha_amount") / 1000).floor() * 1000).cast(pl.Int64).alias("amount_1k"),
            pl.coalesce(
                pl.when(pl.col("interest_rate") > 0).then(pl.col("interest_rate")),
                pl.col("fha_rate"),
            ).alias("rate_use"),
            _hmda_purpose_cat().alias("purpose_cat"),
            pl.when(pl.col("co_applicant_sex").is_in([1, 2, 3, 6]))
            .then(2)
            .otherwise(1)
            .alias("hmda_num_borrowers"),
            pl.col("total_units").cast(pl.Int64),
            pl.col("loan_term").cast(pl.Int64),
        )
        .with_columns(
            (pl.col("rate_use") * 1000).round().cast(pl.Int64).alias("r3"),
            (pl.col("rate_use") / 0.125).round().cast(pl.Int64).alias("r_bucket"),
        )
        .filter(pl.col("amount_1k").is_not_null() & pl.col("r3").is_not_null())
    )
    logger.info("direct-match targets (FHA-matched, usable keys): %s", f"{len(targets):,}")

    cand_pool = gnma.filter(
        pl.col("gnma_orig_year").is_between(min_year - 1, max_year + 1)
    ).with_columns(
        pl.col("gnma_opb").cast(pl.Int64).alias("amount_1k"),
        (pl.col("gnma_rate") * 1000).round().cast(pl.Int64).alias("r3"),
        (pl.col("gnma_rate") / 0.125).round().cast(pl.Int64).alias("r_bucket"),
        _gnma_purpose_cat().alias("purpose_cat"),
        (pl.col("gnma_orig_year") * 12 + pl.col("gnma_orig_month")).alias("orig_idx"),
        pl.col("gnma_term").alias("loan_term"),
    )
    logger.info("candidate pool (GNMA FHA): %s", f"{len(cand_pool):,}")

    tgt_cols = [
        "HMDAIndex",
        "FHA_Index",
        "lei",
        "state_code",
        "activity_year",
        "amount_1k",
        "r3",
        "r_bucket",
        "purpose_cat",
        "loan_term",
        "endorse_idx",
        "hmda_num_borrowers",
        "total_units",
        "combined_loan_to_value_ratio",
        "debt_to_income_ratio",
    ]
    cnd_cols = [
        "gnma_loan_id",
        "issuer_id",
        "gnma_state",
        "amount_1k",
        "r3",
        "r_bucket",
        "purpose_cat",
        "loan_term",
        "orig_idx",
        "gnma_units",
        "gnma_num_borrowers",
        "gnma_cltv",
        "gnma_dti",
        "gnma_orig_year",
    ]

    rounds: list[pl.DataFrame] = []
    matched_h: pl.DataFrame | None = None
    matched_g = pl.DataFrame({"gnma_loan_id": pl.Series([], dtype=pl.Utf8)})

    for rnd, round_ in enumerate(ROUNDS_FHA, start=1):
        t_r = targets.select(tgt_cols)
        c_r = cand_pool.select(cnd_cols).rename({"gnma_state": "state_code"})
        if matched_h is not None:
            t_r = t_r.join(matched_h, on="HMDAIndex", how="anti")
            c_r = c_r.join(matched_g, on="gnma_loan_id", how="anti")
        if round_.require_term:
            t_r = t_r.filter(pl.col("loan_term").is_not_null())
        cand = (
            t_r.join(c_r, on=list(round_.keys), how="inner", suffix="_g")
            .with_columns((pl.col("endorse_idx") - pl.col("orig_idx")).alias("month_diff"))
            .filter(pl.col("month_diff").is_between(*round_.month_window))
        )
        if len(cand) == 0:
            continue
        cand = _score_candidates(cand, dyads).filter(pl.col("total_score") >= round_.min_accept)
        m = _unique_best_1to1(cand).with_columns(pl.lit(rnd, dtype=pl.UInt8).alias("match_round"))
        logger.info("direct round %d: accepted %s", rnd, f"{len(m):,}")
        rounds.append(m)
        new_h = m.select("HMDAIndex")
        matched_h = (
            new_h if matched_h is None else pl.concat([matched_h, new_h], how="diagonal_relaxed")
        )
        matched_g = pl.concat([matched_g, m.select("gnma_loan_id")], how="diagonal_relaxed")

    direct = pl.concat(rounds, how="diagonal_relaxed") if rounds else pl.DataFrame()
    logger.info("direct matches accepted (pre-admission): %s", f"{len(direct):,}")
    return direct


@log_execution_time
def run_matching_fha(
    min_year: int | None = None,
    max_year: int | None = None,
    min_score: int | None = None,
    output_path: Path | None = None,
) -> dict[str, int]:
    """Build the HMDA-GNMA crosswalk for FHA loans (FHA-enriched direct match).

    Two passes: the first runs without the LEI-issuer dyad term, and the dyad map is
    learned from its pairs at the floor; the second runs with it. Pairs enter on
    `admitted` -- the floor, or absent CLTV/DTI evidence -- and ``admission`` records which.

    Args:
        min_year: First HMDA activity year (default ``Config.MIN_YEAR``).
        max_year: Last HMDA activity year (default ``Config.MAX_YEAR``).
        min_score: ``total_score`` floor (default ``Config.FHA_MIN_SCORE``; validated
            tiers: >= 11 good, >= 14 strict).
        output_path: Where to write the crosswalk (default the production location).

    Returns:
        Counts dict: hmda_fha_loans, by_score, by_absence, total.
    """
    lo = Config.MIN_YEAR if min_year is None else min_year
    hi = Config.MAX_YEAR if max_year is None else max_year
    floor = Config.FHA_MIN_SCORE if min_score is None else min_score
    Config.ensure_directories()

    hmda = load_hmda_fha_universe(lo, hi)
    gnma = load_gnma_fha_universe()

    no_dyads = pl.DataFrame(
        schema={"lei": pl.Utf8, "issuer_id": gnma.schema["issuer_id"], "dyad_n": pl.UInt32}
    )
    first = run_direct_match(hmda, gnma, lo, hi, no_dyads)
    dyads = build_lei_issuer_dyads(first.filter(pl.col("total_score") >= floor), hmda, gnma)
    direct = run_direct_match(hmda, gnma, lo, hi, dyads)

    score_cols = ["s_units", "s_borr", "s_cltv", "s_month", "s_dyad", "s_dti"]
    crosswalk = (
        direct.filter(admitted(floor))
        .with_columns(
            pl.when(pl.col("total_score") >= floor)
            .then(pl.lit("score"))
            .otherwise(pl.lit("absent_evidence"))
            .alias("admission")
        )
        .select(
            "HMDAIndex",
            "FHA_Index",
            "gnma_loan_id",
            "match_round",
            "total_score",
            "month_diff",
            *score_cols,
            "cltv_absent",
            "dti_absent",
            "admission",
        )
        # one row per HMDAIndex (checked below), so the sort is total and the file rerun-stable
        .sort("HMDAIndex")
    )
    for col in ("HMDAIndex", "gnma_loan_id"):
        if crosswalk[col].n_unique() != len(crosswalk):
            raise ValueError(f"GNMA crosswalk is not 1:1 in {col}")

    out_path = output_path or Config.get_crosswalk_path("fha")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_output(out_path) as tmp:
        crosswalk.write_parquet(tmp)
    by_absence = crosswalk.filter(pl.col("admission") == "absent_evidence").height
    logger.info(
        "wrote %s (%s rows; %s on absent evidence)",
        out_path,
        f"{len(crosswalk):,}",
        f"{by_absence:,}",
    )

    return {
        "hmda_fha_loans": len(hmda),
        "by_score": len(crosswalk) - by_absence,
        "by_absence": by_absence,
        "total": len(crosswalk),
    }
