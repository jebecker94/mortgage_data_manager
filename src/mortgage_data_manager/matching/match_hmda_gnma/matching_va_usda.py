"""HMDA-GNMA direct matching for VA and USDA loans (government edge, non-FHA programs).

Unlike the FHA edge (``matching_fha.py``), VA/USDA have no administrative bridge, so this
is a pure scored direct match, validated at 94.5% (VA) / 96.3% (USDA) via the
aggregator-issuer name triangulation:

- blocking: state x HMDA $10k amount bin (GNMA's $1,000-floored OPB mapped to its
  containing bin; bin-edge OPBs mapped to both candidate bins) x exact 3-decimal rate
  (round 1; 12.5bp bucket round 2) x term (round 1) x exact origination year;
- score (max 19): units 3, borrowers 2, fee-aware CLTV 4/3/1 (empirical factor set:
  VA ratios are exactly 1.000 for 77% of true pairs, funding-fee modes 1.022/1.023 and
  1.033/1.036 minor; USDA 1.010 guarantee fee), one-sided DTI 3, LEI-issuer dyad 2
  (FHA-master transfer + strict-seed bootstrap), purpose evidence 2 x coarse +/- 1 x
  fine (`_coarse_agree` / `_fine_agree`), and purchaser-type-aware retail identity:
  LEI<->issuer NAME alignment is near-deterministic for retail types (pt=2 ~96%
  aligned, pt=0 80/59%) and absent for sold types, so alignment scores +2 and
  NON-alignment scores -2 (pt=2) / -1 (pt=0) / 0 otherwise;
- strictly-unique-best acceptance on both sides; key cells with more than
  ``GNMA_CELL_CAP`` candidate pairs are dropped up front (IRRRL-wave cells cannot
  resolve 1:1 and blow up the join), then round 3 re-opens exactly those cells
  sub-blocked on purpose x units x borrowers, at its own higher floor.

The rounds are in ``rounds.py`` (`ROUNDS_VA_USDA`). Derivation and validation: ``docs/matching/hmda_gnma_matching.md``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger, log_execution_time
from mortgage_data_manager.core.medallion import atomic_output
from mortgage_data_manager.matching.match_hmda_gnma.config import HMDAGNMAMatchingConfig as Config
from mortgage_data_manager.matching.match_hmda_gnma.matching_fha import (
    _dti_one_sided,
    load_gnma_fha_universe,
    load_hmda_fha_universe,
)
from mortgage_data_manager.matching.match_hmda_gnma.rounds import (
    ROUNDS_VA_USDA,
    STRICT_GNMA_KEYS,
    STRICT_HMDA_KEYS,
    GnmaRound,
)
from mortgage_data_manager.matching.names import jaccard, name_tokens
from mortgage_data_manager.matching.resolve import unique_best
from mortgage_data_manager.matching.terms import round_to_fhfa_bin
from mortgage_data_manager.sources.hmda.load import scan_hmda_years

logger = get_logger(__name__)

# HMDA loan_type -> GNMA Agency code
LOAN_TYPE_TO_AGENCY: dict[int, str] = {3: "V", 4: "R"}

# GNMA CLTV = HMDA base CLTV x factor when the program fee is financed; union of the
# empirically-verified VA funding-fee and USDA guarantee-fee modes (1.0 dominates both).
FEE_FACTORS: list[float] = [1.0, 1.005, 1.01, 1.0215, 1.023, 1.033, 1.036]

#: Purpose fields the shared GNMA universe loader does not carry, read as a separate
#: per-DSN table and attached with a left join. Folding them into ``_GNMA_COLS`` would
#: put them inside that loader's ``keep="none"`` dedup, where a DSN whose rows disagree
#: on the refinance type would leave the matcher altogether instead of merely losing its
#: purpose evidence -- and it would move the FHA edge's universe too.
_GNMA_PURPOSE_COLS: dict[str, str] = {
    "g_purpose": "loan_purpose",
    "g_refi": "refinance_type",
}

#: The crosswalk's columns: the link, its round, and every score component, so a consumer
#: can rebuild a subset that holds out any one piece of evidence.
_CROSSWALK_COLS = [
    "HMDAIndex", "gnma_loan_id", "agency", "issuer_id", "activity_year", "match_round",
    "total_score", "s_identity", "s_units", "s_borr", "s_cltv", "s_dti", "s_dyad",
    "s_purpose",
]  # fmt: skip

#: Blocking keys of rounds 1 and 2, HMDA side then GNMA side, in the same order.

#: The non-key columns each side carries into a round's join, for the score.
_H_SCORE_COLS = [
    "HMDAIndex", "lei", "purchaser_type", "combined_loan_to_value_ratio",
    "debt_to_income_ratio", "total_units", "hmda_num_borrowers", "loan_purpose",
]  # fmt: skip
_G_SCORE_COLS = [
    "gnma_loan_id", "issuer_id", "gnma_cltv", "gnma_dti", "gnma_units",
    "gnma_num_borrowers", "g_purpose", "g_refi",
]  # fmt: skip

_HMDA_GOV_COLS = [
    "HMDAIndex",
    "lei",
    "loan_type",
    "state_code",
    "loan_amount",
    "interest_rate",
    "loan_term",
    "loan_purpose",
    "purchaser_type",
    "combined_loan_to_value_ratio",
    "debt_to_income_ratio",
    "total_units",
    "co_applicant_sex",
]


def load_hmda_gov_universe(min_year: int, max_year: int) -> pl.DataFrame:
    """HMDA VA/USDA originations with match keys, from each year's best available release.

    Args:
        min_year: First activity year to include, inclusive.
        max_year: Last activity year to include, inclusive.

    Returns:
        HMDA VA/USDA originations with match keys, per year's best release.
    """
    hmda = (
        scan_hmda_years("post2018", min_year, max_year, root=Config.HMDA_SILVER_DIR)
        .filter(
            pl.col("loan_type").is_in([3, 4])
            & (pl.col("action_taken") == 1)
            & (pl.col("reverse_mortgage") != 1)
            & (pl.col("total_units").fill_null(1) <= 4)
        )
        .select(*_HMDA_GOV_COLS, pl.col("activity_year").cast(pl.Int32))
        .collect(engine="streaming")
    )
    hmda = hmda.with_columns(
        pl.when(pl.col("interest_rate") > 0).then(pl.col("interest_rate")).alias("rate_use"),
        pl.when(pl.col("co_applicant_sex").is_in([1, 2, 3, 6]))
        .then(2)
        .otherwise(1)
        .alias("hmda_num_borrowers"),
        pl.col("total_units").cast(pl.Int64),
        pl.col("loan_term").cast(pl.Int64),
        pl.col("loan_type")
        .replace_strict(LOAN_TYPE_TO_AGENCY, return_dtype=pl.Utf8)
        .alias("agency"),
    ).with_columns(
        (pl.col("rate_use") * 1000).round().cast(pl.Int64).alias("r3"),
        (pl.col("rate_use") / 0.125).round().cast(pl.Int64).alias("r_bucket"),
    )
    logger.info("HMDA VA+USDA universe: %s", f"{len(hmda):,}")
    return hmda


def load_gnma_purpose(min_pool_yyyymm: str = "201801") -> pl.DataFrame:
    """GNMA Loan Purpose and Refinance Type per loan, for the purpose-evidence term.

    Args:
        min_pool_yyyymm: Earliest pool month to read, as ``YYYYMM``.

    Returns:
        One row per Disclosure Sequence Number; a DSN whose rows disagree on either
        field is excluded, so it left-joins to null and simply scores no purpose.
    """
    files = sorted(Config.GNMA_SILVER_DIR.glob("dailyllmni_*_L.parquet"))
    files = [f for f in files if f.stem.split("_")[1] >= min_pool_yyyymm]
    sel = [pl.col("disclosure_sequence_number").cast(pl.Utf8).alias("gnma_loan_id")]
    sel += [pl.col(src).cast(pl.Utf8).alias(alias) for alias, src in _GNMA_PURPOSE_COLS.items()]
    purpose = (
        pl.concat([pl.scan_parquet(f).select(sel) for f in files], how="diagonal_relaxed")
        # identical copies collapse; a DSN whose rows disagree is excluded
        .unique()
        .unique(subset=["gnma_loan_id"], keep="none")
        .collect(engine="streaming")
    )
    logger.info("GNMA purpose table: %s", f"{len(purpose):,}")
    return purpose


def _h_purpose_block() -> pl.Expr:
    """HMDA purpose as the three-way block: purchase / cash-out refi / rate-term refi."""
    hp = pl.col("loan_purpose")
    return (
        pl.when(hp == 1)
        .then(pl.lit("P"))
        .when(hp == 32)
        .then(pl.lit("RC"))
        .when(hp == 31)
        .then(pl.lit("RT"))
        .otherwise(None)
    )


def _g_purpose_block() -> pl.Expr:
    """GNMA purpose as the same three-way block.

    Ginnie codes Loan Purpose 1 = purchase and 2 = refinance (3/4/5 are modifications
    and re-performing loans, dropped by the universe loader), and Refinance Type
    1 = not streamlined / not cash-out, 2 = cash-out, 3 = streamlined. HMDA 31
    ("refinancing") covers both non-streamlined rate/term and streamlines, so refinance
    types 1 and 3 share a block.

    Returns:
        The GNMA purpose block expression, null where it cannot be determined.
    """
    gp = pl.col("g_purpose")
    gr = pl.col("g_refi")
    return (
        pl.when(gp == "1")
        .then(pl.lit("P"))
        .when((gp == "2") & (gr == "2"))
        .then(pl.lit("RC"))
        .when((gp == "2") & gr.is_in(["1", "3"]))
        .then(pl.lit("RT"))
        .otherwise(None)
    )


#: The extra block keys a round can name in ``sub_keys``: ``name -> (HMDA expression, GNMA
#: expression)``. A row with any of a round's sub-keys null cannot join.
SUB_KEYS: dict[str, tuple[pl.Expr, pl.Expr]] = {
    "purpose_block": (_h_purpose_block(), _g_purpose_block()),
    "k_units": (pl.col("total_units"), pl.col("gnma_units")),
    "k_borr": (pl.col("hmda_num_borrowers"), pl.col("gnma_num_borrowers").clip(upper_bound=2)),
}


def _coarse_agree() -> pl.Expr:
    """Purchase-vs-refinance agreement: +1 agree, -1 disagree, 0 untestable.

    The coarse leg is where the discrimination is. On strictly 1x1 key cells -- where
    the pairing used no score component at all -- it agrees for 99.75% of VA and 99.92%
    of USDA pairs, against 62.9% / 93.0% on an amount-shift placebo.

    Returns:
        The coarse purpose agreement expression.
    """
    hp = pl.col("loan_purpose")
    gp = pl.col("g_purpose")
    h = pl.when(hp == 1).then(pl.lit("P")).when(hp.is_in([31, 32])).then(pl.lit("R"))
    g = pl.when(gp == "1").then(pl.lit("P")).when(gp == "2").then(pl.lit("R"))
    return (
        pl.when(h.is_null() | g.is_null()).then(0).when(h == g).then(1).otherwise(-1).cast(pl.Int32)
    )


def _fine_agree() -> pl.Expr:
    """Cash-out-vs-not agreement inside refi<->refi, VA only: +1 agree, -1 disagree, 0 else.

    VA only on purpose. The fine leg agrees for 98.61% of VA seed pairs against 69.97%
    on the placebo (1.41x), but USDA's lift is 1.01x -- 98% of USDA volume is purchases
    and its ~2,000 testable refinances carry no signal -- so scoring it there would add
    noise and no evidence.

    Returns:
        The fine purpose agreement expression.
    """
    hp = pl.col("loan_purpose")
    gp = pl.col("g_purpose")
    gr = pl.col("g_refi")
    both_refi = (
        (pl.col("agency") == "V") & hp.is_in([31, 32]) & (gp == "2") & gr.is_in(["1", "2", "3"])
    )
    agree = ((hp == 32) & (gr == "2")) | ((hp == 31) & gr.is_in(["1", "3"]))
    return (
        pl.when(~both_refi.fill_null(value=False))
        .then(0)
        .when(agree)
        .then(1)
        .otherwise(-1)
        .cast(pl.Int32)
    )


def _gnma_keys(gnma: pl.DataFrame) -> pl.DataFrame:
    """Blocking keys on the GNMA side; bin-edge OPBs (x9,000) map to both candidate bins."""
    g = gnma.with_columns(
        round_to_fhfa_bin(pl.col("gnma_opb")).cast(pl.Int64).alias("amt_bin"),
        (pl.col("gnma_rate") * 1000).round().cast(pl.Int64).alias("r3"),
        (pl.col("gnma_rate") / 0.125).round().cast(pl.Int64).alias("r_bucket"),
    )
    edge = g.filter(pl.col("gnma_opb") % 10000 >= 9000).with_columns(
        (pl.col("amt_bin") + 10000).alias("amt_bin")
    )
    return pl.concat([g, edge], how="diagonal_relaxed")


def _seed_matches(hmda: pl.DataFrame, g: pl.DataFrame) -> pl.DataFrame:
    """Strict 1x1-cell seed matches (DTI-consistent), used to bootstrap the dyad table.

    A key cell with exactly one row on each side is the joinless equivalent of a
    unique-both-sides match.

    Returns:
        The strict 1x1-cell seed matches.
    """
    hkeys, gkeys = list(STRICT_HMDA_KEYS), list(STRICT_GNMA_KEYS)
    h1 = (
        hmda.filter(pl.col("r3").is_not_null() & pl.col("loan_term").is_not_null())
        .with_columns(pl.len().over(hkeys).alias("_n"))
        .filter(pl.col("_n") == 1)
        .drop("_n")
    )
    g1 = g.with_columns(pl.len().over(gkeys).alias("_n")).filter(pl.col("_n") == 1).drop("_n")
    seeds = h1.join(g1, left_on=hkeys, right_on=gkeys, how="inner").filter(
        _dti_one_sided().fill_null(False)
    )
    logger.info("strict seeds (1x1 cells, DTI-consistent): %s", f"{len(seeds):,}")
    return seeds


def _load_issuer_names() -> pl.DataFrame:
    return (
        pl.scan_parquet(f"{Config.GNMA_ISSRINFO_DIR}/**/*.parquet")
        .select(
            pl.col("issuer_id").cast(pl.Utf8).str.strip_chars().alias("issuer_id"),
            pl.col("issuer_name").str.strip_chars().alias("pool_issuer"),
            pl.col("record_as_of_date").alias("asof"),
        )
        .filter(pl.col("issuer_id").is_not_null() & pl.col("pool_issuer").is_not_null())
        # the name on the latest record; an issuer with two names on that record gets none
        .filter(pl.col("asof") == pl.col("asof").max().over("issuer_id"))
        .select("issuer_id", "pool_issuer")
        .unique()
        .unique(subset="issuer_id", keep="none")
        .collect()
    )


def _load_panel_names() -> pl.DataFrame:
    return (
        scan_hmda_years("post2018", dataset="panel")
        .select("lei", "respondent_name", "activity_year")
        .filter(pl.col("lei").is_not_null() & (pl.col("lei") != ""))
        # the latest panel year's name; an LEI with two names that year gets none
        .filter(pl.col("activity_year") == pl.col("activity_year").max().over("lei"))
        .select("lei", pl.col("respondent_name").alias("lei_name"))
        .unique()
        .unique(subset="lei", keep="none")
        .collect()
    )


def _score(pairs: pl.DataFrame, dyads: pl.DataFrame) -> pl.DataFrame:
    """The validated v2 score (max 19); expects an ``aligned`` boolean on pairs."""
    cltv_ok = pl.col("combined_loan_to_value_ratio").is_between(1, 200) & pl.col(
        "gnma_cltv"
    ).is_between(1, 200)
    fee_dist = pl.min_horizontal(
        *[
            (pl.col("gnma_cltv") - pl.col("combined_loan_to_value_ratio") * f).abs()
            for f in FEE_FACTORS
        ]
    )
    pairs = pairs.join(dyads, on=["lei", "issuer_id"], how="left")
    pairs = pairs.with_columns(
        pl.when(pl.col("aligned"))
        .then(2)
        .when(pl.col("purchaser_type") == 2)
        .then(-2)
        .when(pl.col("purchaser_type") == 0)
        .then(-1)
        .otherwise(0)
        .alias("s_identity"),
        pl.when(pl.col("gnma_units") == pl.col("total_units"))
        .then(3)
        .otherwise(0)
        .alias("s_units"),
        pl.when(pl.col("gnma_num_borrowers").clip(upper_bound=2) == pl.col("hmda_num_borrowers"))
        .then(2)
        .otherwise(0)
        .alias("s_borr"),
        pl.when(cltv_ok & (fee_dist <= 0.25))
        .then(4)
        .when(cltv_ok & (fee_dist <= 1.0))
        .then(3)
        .when(cltv_ok & (fee_dist <= 5.0))
        .then(1)
        .otherwise(0)
        .alias("s_cltv"),
        pl.when(_dti_one_sided().fill_null(False)).then(3).otherwise(0).alias("s_dti"),
        pl.when(pl.col("dyad_n").is_not_null()).then(2).otherwise(0).alias("s_dyad"),
        (_coarse_agree() * 2 + _fine_agree()).alias("s_purpose"),
    )
    return pairs.with_columns(
        (
            pl.col("s_units")
            + pl.col("s_borr")
            + pl.col("s_cltv")
            + pl.col("s_dti")
            + pl.col("s_dyad")
            + pl.col("s_identity")
            + pl.col("s_purpose")
        ).alias("total_score")
    )


def _attach_alignment(
    cand: pl.DataFrame, panel_names: pl.DataFrame, issuer_names: pl.DataFrame
) -> pl.DataFrame:
    """Add ``aligned``: the LEI's panel name and the pool issuer's name token-match."""
    up = (
        cand.select("lei", "issuer_id")
        .unique()
        .join(panel_names, on="lei", how="left")
        .join(issuer_names, on="issuer_id", how="left")
        .with_columns(
            (jaccard(name_tokens("lei_name"), name_tokens("pool_issuer")) >= 0.5)
            .fill_null(False)
            .alias("aligned")
        )
        .select("lei", "issuer_id", "aligned")
    )
    return cand.join(up, on=["lei", "issuer_id"], how="left").with_columns(
        pl.col("aligned").fill_null(False)
    )


def _lei_issuer_dyads(hmda: pl.DataFrame, g: pl.DataFrame, lo: int, hi: int) -> pl.DataFrame:
    """LEI-issuer dyads: transferred from the FHA master, bootstrapped from strict seeds."""
    fha_master = pl.read_parquet(
        Config.get_crosswalk_path("fha"), columns=["HMDAIndex", "gnma_loan_id"]
    )
    fha_lei = load_hmda_fha_universe(lo, hi).select("HMDAIndex", "lei")
    fha_issuer = load_gnma_fha_universe(agencies=("F",)).select("gnma_loan_id", "issuer_id")
    seeds = _seed_matches(hmda, g)
    dyads = (
        pl.concat(
            [
                fha_master.join(fha_lei, on="HMDAIndex", how="inner")
                .join(fha_issuer, on="gnma_loan_id", how="inner")
                .group_by(["lei", "issuer_id"])
                .len(),
                seeds.group_by(["lei", "issuer_id"]).len(),
            ],
            how="diagonal_relaxed",
        )
        .group_by(["lei", "issuer_id"])
        .agg(pl.col("len").sum().alias("dyad_n"))
        .filter(pl.col("dyad_n") >= Config.DYAD_MIN_COUNT)
    )
    logger.info("LEI-issuer dyads (FHA transfer + seed bootstrap): %s", f"{len(dyads):,}")
    return dyads


def _cap_cells(
    t: pl.DataFrame, c: pl.DataFrame, hkeys: list[str], gkeys: list[str]
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Bound the join before it is materialized: keep rows in cells of at most the cap.

    Returns:
        Both sides trimmed to the allowed cells, and the capped cells on the HMDA keys
        with their row counts ``hn`` and ``gn``.
    """
    cells = (
        t.group_by(hkeys)
        .len()
        .rename({"len": "hn"})
        .join(
            c.group_by(gkeys).len().rename({"len": "gn"}),
            left_on=hkeys,
            right_on=gkeys,
            how="inner",
        )
    )
    allowed = cells.filter(pl.col("hn") * pl.col("gn") <= Config.CELL_CAP)
    capped = cells.filter(pl.col("hn") * pl.col("gn") > Config.CELL_CAP)
    t = t.join(allowed.select(hkeys), on=hkeys, how="semi")
    c = c.join(
        allowed.select(hkeys).rename(dict(zip(hkeys, gkeys, strict=True))), on=gkeys, how="semi"
    )
    return t, c, capped


def _run_round(
    number: int,
    rnd: GnmaRound,
    hmda: pl.DataFrame,
    g: pl.DataFrame,
    matched_h: pl.DataFrame,
    matched_g: pl.DataFrame,
    reopened: pl.DataFrame | None,
    score: Callable[[pl.DataFrame], pl.DataFrame],
    min_accept: int,
) -> tuple[pl.DataFrame | None, pl.DataFrame]:
    """One round over the rows earlier rounds left.

    Returns:
        The round's accepted pairs (``None`` when it has no candidates) and the cells it
        dropped at the cap.
    """
    names = list(rnd.sub_keys)
    hkeys, gkeys = [*rnd.hmda_keys, *names], [*rnd.gnma_keys, *names]
    t = hmda.filter(pl.col("r3").is_not_null())
    if rnd.require_term:
        t = t.filter(pl.col("loan_term").is_not_null())
    t = t.join(matched_h, on="HMDAIndex", how="anti")
    c = g.join(matched_g, on="gnma_loan_id", how="anti")
    if reopened is not None:
        base_h, base_g = list(rnd.hmda_keys), list(rnd.gnma_keys)
        t = t.join(reopened.select(base_h), on=base_h, how="semi")
        c = c.join(
            reopened.select(base_h).rename(dict(zip(base_h, base_g, strict=True))),
            on=base_g,
            how="semi",
        )
    if rnd.sub_keys:
        # copies, because the join consumes the right-hand key columns the score reads
        t = t.with_columns(SUB_KEYS[name][0].alias(name) for name in names).drop_nulls(names)
        c = c.with_columns(SUB_KEYS[name][1].alias(name) for name in names).drop_nulls(names)
    t, c, capped = _cap_cells(
        t.select([*hkeys, *_H_SCORE_COLS]), c.select([*gkeys, *_G_SCORE_COLS]), hkeys, gkeys
    )

    pair_cols = [col for col in _CROSSWALK_COLS if col != "match_round"]
    years = sorted(t["activity_year"].unique().to_list()) if rnd.by_year else [None]
    parts: list[pl.DataFrame] = []
    n_cand = 0
    for year in years:
        ty, cy = t, c
        if year is not None:
            ty = t.filter(pl.col("activity_year") == year)
            cy = c.filter(pl.col("gnma_orig_year") == year)
        # every column is a per-HMDAIndex or per-gnma_loan_id attribute, so copies of one
        # pair are byte-identical; a pair whose copies differ stays duplicated and is
        # dropped as ambiguous downstream rather than collapsed onto one copy
        cand = ty.join(cy, left_on=hkeys, right_on=gkeys, how="inner").unique()
        n_cand += len(cand)
        if len(cand) == 0:
            continue
        scored = score(cand).filter(pl.col("total_score") >= min_accept).select(pair_cols)
        parts.append(
            unique_best(scored, "total_score", "HMDAIndex", "gnma_loan_id", descending=True)
        )
    logger.info(
        "round %d: candidates %s (%s HMDA rows in capped cells)",
        number,
        f"{n_cand:,}",
        f"{int(capped['hn'].sum() or 0):,}",
    )
    if not parts:
        return None, capped
    m = pl.concat(parts, how="diagonal_relaxed").with_columns(
        pl.lit(number, dtype=pl.UInt8).alias("match_round")
    )
    logger.info("round %d: accepted %s (score >= %d)", number, f"{len(m):,}", min_accept)
    return m, capped


@log_execution_time
def run_matching_va_usda(
    min_year: int | None = None,
    max_year: int | None = None,
    min_score: int | None = None,
    capped_min_score: int | None = None,
    output_path: Path | None = None,
) -> dict[str, int]:
    """Build the VA/USDA GNMA crosswalk (``hmda_gnma_crosswalk_va_usda.parquet``).

    Args:
        min_year: First HMDA activity year (default ``Config.MIN_YEAR``).
        max_year: Last HMDA activity year (default ``Config.MAX_YEAR``).
        min_score: ``total_score`` floor for rounds 1-2 (default
            ``Config.VA_USDA_MIN_SCORE``; score=12 is a known medium-confidence
            pocket — see the docs).
        capped_min_score: ``total_score`` floor for round 3, the capped-cell round
            (default ``Config.VA_USDA_CAPPED_MIN_SCORE``).
        output_path: Where to write the crosswalk (default the production path).

    Returns:
        Counts dict: hmda_va, hmda_usda, matched_va, matched_usda, total.
    """
    lo = Config.MIN_YEAR if min_year is None else min_year
    hi = Config.MAX_YEAR if max_year is None else max_year
    floor = Config.VA_USDA_MIN_SCORE if min_score is None else min_score
    capped_floor = Config.VA_USDA_CAPPED_MIN_SCORE if capped_min_score is None else capped_min_score
    Config.ensure_directories()

    hmda = load_hmda_gov_universe(lo, hi)
    gnma = load_gnma_fha_universe(agencies=("V", "R"))
    g = _gnma_keys(gnma).join(load_gnma_purpose(), on="gnma_loan_id", how="left")
    dyads = _lei_issuer_dyads(hmda, g, lo, hi)
    panel_names, issuer_names = _load_panel_names(), _load_issuer_names()

    def score(cand: pl.DataFrame) -> pl.DataFrame:
        return _score(_attach_alignment(cand, panel_names, issuer_names), dyads)

    rounds: list[pl.DataFrame] = []
    matched_h = hmda.select("HMDAIndex").clear()
    matched_g = g.select("gnma_loan_id").clear()
    capped: dict[int, pl.DataFrame] = {}
    for number, rnd in enumerate(ROUNDS_VA_USDA, start=1):
        reopened = None if rnd.reopens is None else capped[rnd.reopens]
        min_accept = capped_floor if rnd.min_accept is None else rnd.min_accept
        m, capped[number] = _run_round(
            number, rnd, hmda, g, matched_h, matched_g, reopened, score, min_accept
        )
        if m is not None:
            rounds.append(m)
            matched_h = pl.concat([matched_h, m.select("HMDAIndex")])
            matched_g = pl.concat([matched_g, m.select("gnma_loan_id")])

    out = pl.concat(rounds, how="diagonal_relaxed").filter(pl.col("total_score") >= floor)
    logger.info("accepted at score >= %d: %s", floor, f"{len(out):,}")

    out = out.select(_CROSSWALK_COLS)
    for col in ("HMDAIndex", "gnma_loan_id"):
        if out[col].n_unique() != len(out):
            msg = f"crosswalk not 1:1 in {col}"
            raise ValueError(msg)
    # one row per HMDAIndex (checked above), so the sort is total and the file rerun-stable
    out = out.sort("HMDAIndex")
    out_path = Config.get_crosswalk_path("va_usda") if output_path is None else output_path
    with atomic_output(out_path) as tmp:
        out.write_parquet(tmp)
    logger.info("wrote %s (%s rows)", out_path, f"{len(out):,}")

    n_va = len(hmda.filter(pl.col("agency") == "V"))
    n_rd = len(hmda.filter(pl.col("agency") == "R"))
    m_va = len(out.filter(pl.col("agency") == "V"))
    m_rd = len(out.filter(pl.col("agency") == "R"))
    return {
        "hmda_va": n_va,
        "hmda_usda": n_rd,
        "matched_va": m_va,
        "matched_usda": m_rd,
        "total": len(out),
    }
