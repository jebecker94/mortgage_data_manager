"""Direct HMDA <-> combined loan-issuance matching (no FHFA leg, no chain input).

Links HMDA originations straight to the tri-agency ``combined/loan_issuance``
spine, bypassing the FHFA hop the chain routes through. The target is the fused
MBS ∪ UMBS record, so it carries the strengths of both disclosures at once: ZIP3
and MSA from the SF legacy side, uncensored seller names from ILLD, and an
origination quarter on every GSE spine (Freddie's decoded from its loan key).

The matcher reads nothing the chain produced, so it is a second, independent route
to the same targets -- and the chain-free direct baseline the chain's uplift is
measured against. It runs in three steps:

1. A **name bridge** maps each LEI to the GSE seller names it is known by: a seller
   name equal, exactly or after legal-suffix stripping, to a name HMDA's transmittal
   series, the HMDA panel or the Avery lender file gives that LEI
   (`build_name_bridge`).
2. A **bridge pass** (``rounds.BRIDGE_PASS``) matches on characteristics alone over the
   full pool. Its pairs on loans HMDA reports as sold to a GSE teach the bridge the
   names lenders actually deliver under -- rebrands and DBAs (`learn_bridge`) -- and are
   otherwise discarded (`build_bridge`).
3. The **rounds** (``rounds.ROUNDS``) run in order, each on what earlier rounds left
   unclaimed (`match_round`, `run_rounds`), and a target claimed in two activity years
   goes to no one (`pooled_unique`).

Every blocking key here was calibrated against 7.93M chain-verified true pairs
(activity year 2021) before being adopted; the agreement rates are quoted at
each key below. Two fields are deliberately kept out of every key and score:

- ``purchaser_type`` -- held out as the destination validator. It is the only
  validator that works on matches the chain never made (there is no chain pair
  to concord against). It only selects which nameless pairs teach the bridge
  (step 2); it never decides whether a pair is accepted.
- ``number_of_borrowers`` -- held out as an independent quality gate (99.66%
  agreement on true pairs vs ~50% shuffled). Production artifacts get rebuilt
  repeatedly and each rebuild needs a gate that is not one of its own inputs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import polars as pl

from mortgage_data_manager.core.harmonized import GSE_OCCUPANCY, HMDA_OCCUPANCY, invert, recode
from mortgage_data_manager.core.logging import get_logger, log_execution_time
from mortgage_data_manager.core.medallion import atomic_output, should_process_file
from mortgage_data_manager.core.reference import load_tract_zip3
from mortgage_data_manager.matching.match_hmda_gse.config import HMDAGSEMatchingConfig as Config
from mortgage_data_manager.matching.match_hmda_gse.lender_names import (
    MASKED_SELLER_NAMES,
    best_tier,
    build_name_universe,
    strip_legal,
)
from mortgage_data_manager.matching.match_hmda_gse.rounds import BRIDGE_PASS, ROUNDS, Round
from mortgage_data_manager.matching.names import normalize_name
from mortgage_data_manager.matching.terms import is_bin_edge, round_to_fhfa_bin
from mortgage_data_manager.sources.hmda.load import scan_hmda

logger = get_logger(__name__)

#: HMDA bins ``loan_amount`` to the midpoint of its $10k interval as ``(low, high]``
#: while ``floor(upb/10000)*10000+5000`` bins as ``[low, high)``. The two disagree
#: for exactly the UPBs that land on a $10k multiple -- 231,530 of 7.93M true pairs
#: (2.92%), every one of them off by exactly one bin low, none ever high. Emitting
#: the extra low-side candidate for those takes bin coverage 97.08% -> 100.00%.
BIN_WIDTH = 10_000

#: Origination timing, quarter-aware. On true pairs the origination year equals the
#: activity year for 99.33%; the 0.67% that sit a year earlier are 99.67% Q4 --
#: year-end straddle between HMDA's action date and the GSE's origination date
#: (2020Q4 52,940 vs Q1/Q2/Q3 6/38/129). Allowing the whole prior YEAR instead of
#: just its Q4 admitted 34,581 matches in quarters where genuine pairs essentially
#: do not exist, at a cost of 173 true pairs in 7.93M (0.002%) to exclude them.
#: This constraint is only expressible because the target carries an origination
#: quarter -- a first-payment-date proxy cannot separate these.
PRIOR_YEAR_QUARTER = "Q4"

#: Tract->ZIP3 vintages: 2010-census tracts through activity 2021, 2020-census from
#: 2022 (the vintage keys are 2019/2023, NOT 2022).
ZIP3_VINTAGE_SPLIT = 2022
ZIP3_MIN_SHARE = 0.15

#: Bridge-learning gate, the same one the chain-derived bridge used: at least
#: ``LEARN_MIN_PAIRS`` bridge-pass pairs for the lender-year, the name carrying at least
#: ``LEARN_MIN_SHARE`` of them, and the name aligned to one the LEI is known by.
LEARN_MIN_PAIRS = 25
LEARN_MIN_SHARE = 0.15
LEARN_TIERS = ("T1_exact", "T2_stripped", "T3_contains", "T4_firstword")

#: Characteristic block keys shared by every round (a name-pinned round adds seller name).
_CHAR_KEYS = ["zip3n", "bin_mid", "rate3", "term", "purpose", "occ"]

#: A round's accepted pairs, in the crosswalk's column order.
_PAIR_COLS = [
    "HMDAIndex", "loan_key", "agency", "score", "lei", "seller_norm", "match_round", "geo",
    "activity_year",
]  # fmt: skip

_HMDA_COLS = [
    "HMDAIndex", "lei", "state_code", "census_tract", "loan_amount", "interest_rate",
    "loan_term", "loan_purpose", "occupancy_type", "total_units",
    "combined_loan_to_value_ratio", "debt_to_income_ratio",
]  # fmt: skip

_TARGET_COLS = [
    "loan_key", "agency", "orig_quarter", "zip3", "original_upb", "note_rate",
    "loan_term", "loan_purpose_raw", "occupancy", "property_state", "cltv", "dti",
    "number_of_units", "seller_name",
]  # fmt: skip


def _norm_name(col: str) -> pl.Expr:
    """Uppercase, normalize punctuation, collapse whitespace (shared with the bridge)."""
    return normalize_name(pl.col(col))


def _purpose_coarse(expr: pl.Expr, *, hmda: bool) -> pl.Expr:
    """Collapse loan purpose to purchase / not-purchase.

    Only the purchase split survives calibration. The three-way P/R/C map leaks
    badly in both refinance directions -- HMDA 31 (refi) lands on the target's
    cash-out code for 71,506 true pairs and HMDA 2 (home improvement) for another
    39,587 -- while the purchase side is essentially clean (377 non-purchase HMDA
    codes on a target ``P`` out of 2.49M).

    Returns:
        A boolean-collapsed purpose expression.
    """
    return pl.when(expr == (1 if hmda else pl.lit("P"))).then(pl.lit("P")).otherwise(pl.lit("NP"))


def _occupancy_unified(expr: pl.Expr, *, hmda: bool) -> pl.Expr:
    """Map occupancy to P/S/I.

    Exact on true pairs: HMDA 1/2/3 map to target P/S/I with zero off-diagonal
    mass, so this is a free hard key.

    Returns:
        An expression on the unified P/S/I occupancy vocabulary.
    """
    if hmda:
        return recode(expr, HMDA_OCCUPANCY, invert(GSE_OCCUPANCY))
    return expr.cast(pl.Utf8)


def _dti_one_sided(hmda_dti: pl.Expr, target_dti: pl.Expr) -> pl.Expr:
    """One-sided DTI consistency, rounding-aware against an integer target.

    HMDA reports exact integers 36-49 as FLOORS (so the true value lies in
    ``[h, h+1)``) and everything else as a bucket code; -99999 is the Exempt
    sentinel. Target DTI >= 990 is the not-available sentinel and never passes.

    ``combined/loan_issuance.dti`` passes through the GSE-published DTI, which is
    an integer they publish ROUNDED. Against a rounded integer a ``[h, h+1)``
    window means ``t == h``, so a true 44.6 -- HMDA 44, target 45 -- reads as a
    DISAGREEMENT. On chain-true pairs the delta splits near 50/50 between 0 and
    +1 with under 0.8% at -1, every year 2018-2024: the signature of
    floor-vs-round on a uniform fractional part. The window is therefore
    ``t in {h, h+1}``, with the same +1 slack at each bucket's edge (on 2022 true
    pairs bucket 10 goes 0.8994 -> 0.9842, bucket 20 0.9266 -> 0.9891, bucket 30
    0.8992 -> 0.9862). Hit rate on true pairs 0.68-0.74 -> 0.976 while the
    amount-shift placebo barely moves (0.130 -> 0.162), so this is a
    specification fix, not a relaxation.

    The slack is TARGET-SPECIFIC and must not be copied to a matcher whose target
    carries a real fractional DTI, where the half-open window is correct.

    Returns:
        A boolean expression, true when the two DTIs are consistent.
    """
    t = pl.when(target_dti >= 990).then(None).otherwise(target_dti)
    return (
        pl.when(hmda_dti.is_between(36, 49))
        .then((t >= hmda_dti) & (t <= hmda_dti + 1))
        .when(hmda_dti == 10)
        .then(t <= 20)
        .when(hmda_dti == 20)
        .then((t >= 20) & (t <= 30))
        .when(hmda_dti == 30)
        .then((t >= 30) & (t <= 36))
        .when(hmda_dti == 50)
        .then((t >= 50) & (t <= 60))
        .when(hmda_dti == 60)
        .then(t >= 59)
        .otherwise(None)
        .fill_null(value=False)
    )


def load_hmda(year: int) -> pl.LazyFrame:
    """Load one year's conventional first-lien HMDA originations, keyed for matching.

    Args:
        year: HMDA activity year.

    Returns:
        LazyFrame with the block keys and score inputs. ``purchaser_type`` and
        borrower count are deliberately absent -- both are held-out validators.
    """
    lf = (
        scan_hmda("post2018", year, root=Config.HMDA_SILVER_DIR)
        .filter(
            (pl.col("action_taken") == 1)
            & (pl.col("loan_type") == 1)
            & (pl.col("lien_status") == 1)
        )
        .select(_HMDA_COLS)
        .with_columns(
            pl.lit(year, dtype=pl.Int32).alias("activity_year"),
            pl.col("interest_rate").cast(pl.Float64).round(3).alias("rate3"),
            pl.col("loan_amount").cast(pl.Float64).alias("bin_mid"),
            pl.col("loan_term").cast(pl.Int32).alias("term"),
            _purpose_coarse(pl.col("loan_purpose"), hmda=True).alias("purpose"),
            _occupancy_unified(pl.col("occupancy_type"), hmda=True).alias("occ"),
            pl.col("census_tract").cast(pl.Utf8).str.zfill(11).alias("tract"),
        )
        .filter(pl.col("rate3").is_not_null() & pl.col("term").is_not_null())
    )
    return lf


def load_target(min_year: int, max_year: int) -> pl.LazyFrame:
    """Load the GSE side of ``combined/loan_issuance``, keyed for matching.

    Emits one row per loan, plus a second candidate row for UPBs on an exact $10k
    multiple (see `BIN_WIDTH`).

    Args:
        min_year: First HMDA activity year being matched.
        max_year: Last HMDA activity year being matched.

    Returns:
        LazyFrame of candidate target rows.
    """
    lo, hi = min_year - 1, max_year
    base = (
        pl.scan_parquet(str(Config.COMBINED_LOAN_ISSUANCE_DIR), hive_partitioning=True)
        .filter(
            pl.col("agency").is_in(["FNMA", "FHLMC"])
            & pl.col("orig_quarter").is_not_null()
            & pl.col("seller_name").is_not_null()
            # Loans with no ILLD record fall back to the SF disclosure's value-masked
            # seller ("Other" / "Other sellers"), which no bridge entry can ever name.
            # They are unmatchable by construction, so they never reach the join.
            & ~_norm_name("seller_name").is_in(MASKED_SELLER_NAMES)
        )
        .select(_TARGET_COLS)
        .with_columns(pl.col("orig_quarter").str.slice(0, 4).cast(pl.Int32).alias("orig_year"))
        .filter(pl.col("orig_year").is_between(lo, hi))
        .with_columns(
            pl.col("note_rate").cast(pl.Float64).round(3).alias("rate3"),
            pl.col("loan_term").cast(pl.Int32).alias("term"),
            _purpose_coarse(pl.col("loan_purpose_raw"), hmda=False).alias("purpose"),
            _occupancy_unified(pl.col("occupancy"), hmda=False).alias("occ"),
            pl.col("property_state").cast(pl.Utf8).alias("state"),
            pl.col("zip3").cast(pl.Utf8).str.zfill(3).alias("zip3n"),
            _norm_name("seller_name").alias("seller_norm"),
            round_to_fhfa_bin(pl.col("original_upb").cast(pl.Float64)).alias("bin_mid"),
            is_bin_edge(pl.col("original_upb").cast(pl.Float64)).alias("on_edge"),
        )
        .filter(pl.col("rate3").is_not_null() & pl.col("term").is_not_null())
    )
    edge = base.filter(pl.col("on_edge")).with_columns(pl.col("bin_mid") - BIN_WIDTH)
    return pl.concat([base, edge], how="diagonal_relaxed").drop("on_edge")


def attach_zip3(hmda: pl.LazyFrame, year: int) -> pl.LazyFrame:
    """Attach ZIP3 candidates to HMDA rows via the tract->ZIP3 reference.

    Keeps every candidate at or above `ZIP3_MIN_SHARE`, which reaches the
    target's ZIP3 on 99.16% of true pairs (the dominant candidate alone reaches
    only 97.52%) -- boundary tracts get duplicate candidate rows, the same device
    the amount bins use.

    Args:
        hmda: Read the HMDA code space rather than the target's.
        year: Activity year.

    Returns:
        The HMDA rows fanned to their candidate ZIP3s.
    """
    vintage = 2019 if year < ZIP3_VINTAGE_SPLIT else 2023
    tz = (
        load_tract_zip3()
        .filter((pl.col("vintage") == vintage) & (pl.col("share") >= ZIP3_MIN_SHARE))
        .select(pl.col("census_tract").alias("tract"), pl.col("zip3").alias("zip3n"))
    )
    return hmda.join(tz, on="tract", how="left")


def _score() -> pl.Expr:
    """Characteristic agreement score (max 7, borrowers excluded by design).

    CLTV tiers are calibrated on true pairs: 98.56% agree within 1pp but only
    56.38% within 0.25pp, so the 3-point tier carries most of the signal and the
    4-point tier is a genuine tie-splitter rather than the main term.

    DTI is the second-strongest term and worth its 2 points: on the corrected
    window (`_dti_one_sided`) it agrees on 97.6-97.7% of true pairs against
    13.7-16.2% on an amount-shift placebo, next to CLTV within 1pp at
    98.9-99.7% vs 8-12%. ``units`` is nearly free evidence in the other
    direction -- 99.97% true against 98.5% placebo -- so it separates almost
    nothing and is kept only because it is harmless.

    Returns:
        The characteristic agreement score expression, max 7.
    """
    d = (
        pl.col("combined_loan_to_value_ratio").cast(pl.Float64) - pl.col("cltv").cast(pl.Float64)
    ).abs()
    cltv = (
        pl.when(d <= 0.25).then(4).when(d <= 1).then(3).when(d <= 5).then(1).otherwise(0)
    ).fill_null(0)
    dti = (
        _dti_one_sided(
            pl.col("debt_to_income_ratio").cast(pl.Float64), pl.col("dti").cast(pl.Float64)
        ).cast(pl.Int32)
        * 2
    )
    units = (
        (pl.col("total_units").cast(pl.Int32) == pl.col("number_of_units").cast(pl.Int32))
        .cast(pl.Int32)
        .fill_null(0)
    )
    return (cltv + dti + units).alias("score")


def _best_per(scored: pl.LazyFrame, key: str, prefix: str) -> pl.LazyFrame:
    """Per-key best score and how many candidates tie at it.

    A ``group_by`` aggregation rather than ``.over()`` windows: polars runs window
    functions single-threaded, which dominates the runtime on a join this wide.

    Returns:
        One row per key, with its best score and how many candidates tie at it.
    """
    return scored.group_by(key).agg(
        pl.col("score").max().alias(f"{prefix}_max"),
        (pl.col("score") == pl.col("score").max()).sum().alias(f"{prefix}_n"),
    )


def _accept_unique_best(pairs: pl.LazyFrame, floor: int) -> pl.LazyFrame:
    """Keep pairs that are the strictly-unique maximum score on BOTH sides.

    Ties are dropped rather than broken. Dropping ambiguity is what makes the 1:1
    guarantee meaningful -- a tie means the evidence genuinely does not separate
    two candidates, and picking one would manufacture precision.

    Returns:
        The pairs that are the strictly-unique maximum on both sides.
    """
    scored = pairs.filter(pl.col("score") >= floor)
    return (
        scored.join(_best_per(scored, "HMDAIndex", "_h"), on="HMDAIndex", how="inner")
        .join(_best_per(scored, "loan_key", "_t"), on="loan_key", how="inner")
        .filter(
            (pl.col("score") == pl.col("_h_max"))
            & (pl.col("_h_n") == 1)
            & (pl.col("score") == pl.col("_t_max"))
            & (pl.col("_t_n") == 1)
        )
        .drop("_h_max", "_h_n", "_t_max", "_t_n")
    )


def _window() -> pl.Expr:
    """Origination timing: the activity year, or the prior year's `PRIOR_YEAR_QUARTER`."""
    return (pl.col("orig_year") == pl.col("activity_year")) | (
        (pl.col("orig_year") == pl.col("activity_year") - 1)
        & pl.col("orig_quarter").str.ends_with(PRIOR_YEAR_QUARTER)
    )


@log_execution_time
def match_round(
    rnd: Round, year: int, bridge: pl.DataFrame | None, claimed: pl.DataFrame
) -> pl.DataFrame:
    """Run one round for one activity year over what earlier rounds left unclaimed.

    Blocks on the characteristic keys (ZIP3, $10k amount bin, 3-decimal rate, term,
    coarse purpose, occupancy), plus the seller name through the bridge when the round
    is name-pinned, filters on the origination window, and accepts strictly-unique best
    pairs at or above the round's floor. A capped round decides only the cells that are
    sparse enough on the full pool, before the claimed loans and targets are removed:
    without a name in the key, a dense cell's unique best is as likely to be a
    coincidence as a match. Denser cells are skipped, never sampled -- a sample would
    make the match depend on which candidates happened to be drawn.

    Args:
        rnd: The round.
        year: HMDA activity year.
        bridge: ``lei`` -> ``seller_norm`` entries with a ``year`` (null binds to every
            year), from `learn_bridge`. Required by a name-pinned round.
        claimed: ``HMDAIndex``, ``loan_key`` of every pair earlier rounds accepted, in
            any activity year.

    Returns:
        Accepted pairs in the crosswalk's column layout, ``match_round`` null (the
        caller numbers the round); ``seller_norm`` is null unless the round is
        name-pinned.

    Raises:
        ValueError: A name-pinned round was given no bridge.
    """
    hmda = load_hmda(year)
    target = load_target(year, year)
    keys = list(_CHAR_KEYS)
    if rnd.name_pinned:
        if bridge is None:
            raise ValueError(f"round {rnd.description!r} is name-pinned and needs a bridge")
        # lei -> seller name(s); year-keyed entries bind to their year, year-free to all
        names = bridge.lazy().select("lei", "seller_norm", pl.col("year").alias("bridge_year"))
        hmda = (
            hmda.join(names, on="lei", how="inner")
            .filter(pl.col("bridge_year").is_null() | (pl.col("bridge_year") == year))
            .drop("bridge_year")
            .unique()  # one row per (HMDA row, seller name), whichever bridge year reached it
        )
        keys = ["seller_norm", *keys]
    else:
        target = target.drop("seller_norm")
    hmda = attach_zip3(hmda, year).filter(pl.col("zip3n").is_not_null())
    target = target.filter(pl.col("zip3n").is_not_null())

    if rnd.cell_cap is not None:
        # cell density on loans, not rows: the ZIP3 and amount-bin fan-out is collapsed first
        n_hmda = hmda.select("HMDAIndex", *keys).unique().group_by(keys).len("_nh")
        n_target = target.select("loan_key", *keys).unique().group_by(keys).len("_nt")
        decidable = (
            n_hmda.join(n_target, on=keys)
            .filter(pl.col("_nh").cast(pl.Int64) * pl.col("_nt").cast(pl.Int64) <= rnd.cell_cap)
            .select(keys)
        )
        hmda = hmda.join(decidable, on=keys, how="semi")
    hmda = hmda.join(claimed.lazy().select("HMDAIndex"), on="HMDAIndex", how="anti")
    target = target.join(claimed.lazy().select("loan_key"), on="loan_key", how="anti")

    name = ["seller_norm"] if rnd.name_pinned else []
    pairs = (
        hmda.join(target, on=keys, how="inner", suffix="_t")
        .filter(_window())
        .with_columns(_score())
        # One HMDA loan can reach the same target twice (two ZIP3 candidates, or the
        # amount-bin edge row). Keep only the per-loan columns acceptance reads -- the
        # score is per-loan too -- and collapse the now byte-identical copies BEFORE the
        # uniqueness test; otherwise a self-duplicate reads as a tie and the pair is lost.
        .select("HMDAIndex", "loan_key", "agency", "score", "lei", *name)
        .unique()
    )
    accepted = (
        _accept_unique_best(pairs, rnd.score_floor)
        .with_columns(
            *([] if rnd.name_pinned else [pl.lit(None, dtype=pl.String).alias("seller_norm")]),
            pl.lit(None, dtype=pl.UInt8).alias("match_round"),
            pl.lit("zip3").alias("geo"),
            pl.lit(year, dtype=pl.Int32).alias("activity_year"),
        )
        .select(_PAIR_COLS)
        .collect(engine="streaming")
    )
    logger.info("  %s %s: %s accepted", rnd.description, year, f"{len(accepted):,}")
    return accepted


def _gse_seller_names() -> pl.LazyFrame:
    """``loan_key`` -> normalized seller name for every GSE target with a real name."""
    return (
        pl.scan_parquet(str(Config.COMBINED_LOAN_ISSUANCE_DIR), hive_partitioning=True)
        .filter(pl.col("agency").is_in(["FNMA", "FHLMC"]))
        .select("loan_key", _norm_name("seller_name").alias("seller_norm"))
        .filter(
            pl.col("seller_norm").is_not_null() & ~pl.col("seller_norm").is_in(MASKED_SELLER_NAMES)
        )
    )


@log_execution_time
def build_name_bridge(universe: pl.DataFrame) -> pl.DataFrame:
    """Map LEIs to the GSE seller names they are known by, from HMDA-side names alone.

    A seller name joins an LEI when it equals one of the LEI's names exactly, or after
    a leading ``THE`` and trailing legal-form tokens are stripped from both (stripped
    names under three characters never join). A seller name may map to several LEIs
    and an LEI to several names: the pinned round blocks on the pair, so a shared name
    only widens the candidate set and the unique-best test still decides.

    Args:
        universe: ``lei``, ``name`` -- every name an LEI is known by
            (`build_name_universe`).

    Returns:
        ``lei``, ``seller_norm`` pairs.
    """
    sellers = (
        _gse_seller_names()
        .select("seller_norm")
        .unique()
        .collect(engine="streaming")
        .with_columns(strip_legal("seller_norm").alias("_s_strip"))
    )
    names = universe.select("lei", "name").with_columns(strip_legal("name").alias("_u_strip"))
    exact = sellers.join(names, left_on="seller_norm", right_on="name").select("lei", "seller_norm")
    stripped = (
        sellers.filter(pl.col("_s_strip").str.len_chars() >= 3)
        .join(names, left_on="_s_strip", right_on="_u_strip")
        .select("lei", "seller_norm")
    )
    bridge = pl.concat([exact, stripped]).unique()
    logger.info(
        "name bridge: %s entries, %s seller names, %s LEIs",
        f"{len(bridge):,}",
        f"{bridge['seller_norm'].n_unique():,}",
        f"{bridge['lei'].n_unique():,}",
    )
    return bridge


@log_execution_time
def learn_bridge(
    taught: pl.DataFrame, name_bridge: pl.DataFrame, universe: pl.DataFrame
) -> pl.DataFrame:
    """Add the seller names lenders actually deliver under, learned from nameless pairs.

    On loans HMDA reports as sold to Fannie Mae or Freddie Mac, the originator is the
    delivering seller, so a pair matched without a name reveals the name that lender
    delivers under.
    For each lender-year, a seller name becomes an entry when the lender has at least
    `LEARN_MIN_PAIRS` such pairs, the name carries at least `LEARN_MIN_SHARE` of them,
    and it aligns (`LEARN_TIERS`) with a name the LEI is known by. Every qualifying
    name is kept, so a mid-year rebrand keeps both names. Learned entries bind to their
    activity year; name-bridge entries bind to every year.

    Args:
        taught: The bridge pass's accepted pairs for every year in the window.
        name_bridge: ``lei``, ``seller_norm`` from `build_name_bridge`.
        universe: ``lei``, ``name`` from `build_name_universe`.

    Returns:
        ``lei``, ``seller_norm``, ``year`` (Int32; null for name-bridge entries).
    """
    years = sorted(taught["activity_year"].unique().to_list())
    sold = pl.concat(
        [
            scan_hmda("post2018", y, root=Config.HMDA_SILVER_DIR).select(
                "HMDAIndex", "lei", "purchaser_type"
            )
            for y in years
        ]
    ).filter(pl.col("purchaser_type").is_in([1, 3]))
    keys = taught.select("loan_key").unique().lazy()
    pairs = (
        taught.lazy()
        .select("HMDAIndex", "loan_key", "activity_year")
        .join(sold.select("HMDAIndex", "lei"), on="HMDAIndex", how="inner")
        .join(_gse_seller_names().join(keys, on="loan_key", how="semi"), on="loan_key")
        .collect(engine="streaming")
    )
    candidates = (
        pairs.group_by("lei", "activity_year", "seller_norm")
        .agg(pl.len().alias("_np"))
        .with_columns(pl.col("_np").sum().over("lei", "activity_year").alias("_n"))
        .filter(
            (pl.col("_n") >= LEARN_MIN_PAIRS) & (pl.col("_np") / pl.col("_n") >= LEARN_MIN_SHARE)
        )
    )
    learned = (
        candidates.join(universe.select("lei", "name"), on="lei", how="inner")
        .filter(best_tier("seller_norm", "name").is_in(LEARN_TIERS))
        .select("lei", "seller_norm", pl.col("activity_year").cast(pl.Int32).alias("year"))
        .unique()
    )
    bridge = pl.concat(
        [name_bridge.with_columns(pl.lit(None, dtype=pl.Int32).alias("year")), learned]
    ).unique()
    new = learned.join(name_bridge, on=["lei", "seller_norm"], how="anti")
    logger.info(
        "learned bridge: %s bridge-pass pairs on GSE-sold loans; %s learned entries, %s new "
        "to the name bridge (%s LEIs)",
        f"{len(pairs):,}",
        f"{len(learned):,}",
        f"{len(new):,}",
        f"{new['lei'].n_unique():,}",
    )
    return bridge


def pooled_unique(pairs: pl.DataFrame) -> pl.DataFrame:
    """Drop every pair whose target is claimed in two activity years.

    The origination window admits prior-year Q4 loans, so adjacent years compete for a
    target; each year's pair is its own year's unique best and nothing separates them.
    A later round never sees a target an earlier round claimed in any year, so the
    competing claims are always the same round's.

    Args:
        pairs: Every round's accepted pairs for every year in the window.

    Returns:
        ``pairs`` without any target claimed in more than one activity year.
    """
    multi = (
        pairs.group_by("loan_key")
        .agg(pl.col("activity_year").n_unique().alias("_ny"))
        .filter(pl.col("_ny") > 1)
    )
    logger.info("pooled exclusivity: %s targets claimed in two years", f"{len(multi):,}")
    return pairs.join(multi.select("loan_key"), on="loan_key", how="anti")


# ======================================================================================
# Admission: folding direct matches into the chain
# ======================================================================================

#: FHFA ``enterprise_flag`` -> agency (1 = Fannie, 2 = Freddie).
ENTERPRISE_AGENCY: dict[int, str] = {1: "FNMA", 2: "FHLMC"}


def load_chain(year: int | None = None) -> pl.LazyFrame:
    """Load the chain's HMDA -> target assignment.

    Args:
        year: Restrict to one activity year. ``None`` spans every year both chain
            masters hold, which is the window a claimed-target screen needs -- a
            target the chain gave an ADJACENT year's HMDA loan is just as taken.

    Returns:
        The chain's HMDA-to-target assignment.
    """
    xw = Config.HMDA_GSE_CROSSWALK_DIR
    frames: list[pl.LazyFrame] = []
    for agency in ("fnma", "fhlmc"):
        lf = pl.scan_parquet(xw / f"hmda_gse_crosswalk_chain_{agency}.parquet")
        if year is not None:
            lf = lf.filter(pl.col("activity_year") == year)
        frames.append(lf.select("HMDAIndex", pl.col("mbs_loan_id").alias("chain_key")))
    return pl.concat(frames, how="diagonal_relaxed")


def load_fhfa_pin(year: int) -> pl.LazyFrame:
    """Load the chain's HMDA -> FHFA enterprise pin for one activity year.

    This is the chain's *first* leg only, so it survives on loans whose downstream
    legs failed -- which is what makes it available to corroborate or contradict a
    direct match the chain never completed.

    Args:
        year: Activity year.

    Returns:
        The chain's HMDA-to-enterprise pin for that year.
    """
    return (
        pl.scan_parquet(Config.HMDA_FHFA_CROSSWALK)
        .filter(pl.col("activity_year") == year)
        .select(
            "HMDAIndex",
            pl.col("enterprise_flag")
            .replace_strict(ENTERPRISE_AGENCY, default=None)
            .alias("fhfa_agency"),
        )
        # hmda_fhfa is 1:1 on HMDAIndex; a loan pinned to two enterprises gets no pin
        .unique()
        .unique(subset="HMDAIndex", keep="none")
    )


@log_execution_time
def admit(
    direct: pl.DataFrame,
    year: int,
    *,
    record_exclusive: bool = True,
    exclusivity: Literal["global", "year"] = "global",
) -> pl.DataFrame:
    """Resolve a year's direct matches against the chain and label every row.

    Two conflicts arise, and they are not symmetric:

    - **Type 1** -- the chain and the direct matcher assign the same HMDA loan to
      DIFFERENT targets (0.87% of the overlap, 2018-2024). This is coverage-neutral:
      the loan is matched either way and only the target identity is contested. It
      is also not reliably adjudicable -- 80% of contested pairs are twins on every
      held-out field (they matched the same rate, term, amount bin and geography, so
      the separating evidence was already spent as keys), and the two usable
      estimators disagree in direction (held-out borrowers favours the chain 75/25
      but is biased toward it, since ``hmda_fhfa`` matches on demographics; held-out
      property value favours direct 59/41 but is noisy, landing within 2% on only
      75.6% of known-agreed pairs). So the chain keeps priority as the conservative
      status quo and the row is FLAGGED, never silently resolved.
    - **Type 2** -- a direct match claims a target the chain assigned to a different
      HMDA loan (9.18% of the increment). This one does cost coverage. One of the two
      assignments is certainly wrong and nothing separates them, so under
      ``record_exclusive`` the direct claim is dropped: 0.43pp of known-sold coverage
      to keep the spine one-target-one-loan.

    The type-2 screen runs over the WHOLE chain by default, not ``year``'s slice of
    it. Its window is not the work's window: the matcher deliberately admits
    prior-year Q4 originations, so adjacent years genuinely compete for a target,
    and a target the chain gave an adjacent year's HMDA loan is just as taken.
    Scoped to one year (``exclusivity="year"``, the pre-fix behaviour, kept so a
    rebuild can be compared against an artifact built under it) the pooled
    crosswalk leaks 14,896 such rows, 100% of them cross-year.

    The other half of pooled exclusivity -- two increment rows in DIFFERENT years
    claiming one target -- is invisible from inside a single year by construction,
    so the master applies it over every built year when it reads the edge.

    Args:
        direct: Accepted direct matches for ``year``.
        year: HMDA activity year.
        record_exclusive: Drop increment rows whose target the chain already claims.
        exclusivity: Window for the claimed-target screen -- ``global`` spans every
            year both chain masters hold, ``year`` only ``year`` (pre-fix).

    Returns:
        ``direct`` with ``disposition`` in {``agrees``, ``contested_target``,
        ``increment``, ``dropped_double_claim``} and the FHFA pin attached.
    """
    chain = load_chain(year).collect(engine="streaming")
    claimed = (
        load_chain(None if exclusivity == "global" else year)
        .select(pl.col("chain_key").alias("loan_key"))
        .drop_nulls()
        .unique()
        .collect(engine="streaming")
    )
    fhfa = load_fhfa_pin(year).collect(engine="streaming")

    labelled = (
        direct.join(chain, on="HMDAIndex", how="left")
        .join(claimed.with_columns(pl.lit(value=True).alias("_claimed")), on="loan_key", how="left")
        .join(fhfa, on="HMDAIndex", how="left")
        .with_columns(
            pl.when(pl.col("chain_key") == pl.col("loan_key"))
            .then(pl.lit("agrees"))
            .when(pl.col("chain_key").is_not_null())
            .then(pl.lit("contested_target"))
            .when(pl.col("_claimed").fill_null(value=False))
            .then(pl.lit("dropped_double_claim" if record_exclusive else "increment_double_claim"))
            .otherwise(pl.lit("increment"))
            .alias("disposition")
        )
        .drop("_claimed")
    )
    counts = labelled.group_by("disposition").agg(pl.len().alias("n")).sort("n", descending=True)
    for row in counts.iter_rows(named=True):
        logger.info("  %s %s: %s", year, row["disposition"], f"{row['n']:,}")
    return labelled


def fhfa_screen(labelled: pl.DataFrame) -> pl.DataFrame:
    """Split admitted increment rows by whether the FHFA pin corroborates them.

    The pin is built from different keys, on a different dataset, by a different
    matcher, so agreement is genuine corroboration rather than shared error. The
    admission rule (`admitted_row`) accepts a name-pinned increment unless the pin
    contradicts it, a nameless increment only when the pin corroborates it, and drops
    FHFA-contradicted matches outright (~99% wrong there).

    Args:
        labelled: Output of `admit`.

    Returns:
        ``labelled`` with a ``fhfa_verdict`` of ``corroborated`` / ``contradicted``
        / ``no_link``.
    """
    return labelled.with_columns(
        pl.when(pl.col("fhfa_agency").is_null())
        .then(pl.lit("no_link"))
        .when(pl.col("fhfa_agency") == pl.col("agency"))
        .then(pl.lit("corroborated"))
        .otherwise(pl.lit("contradicted"))
        .alias("fhfa_verdict")
    )


# ======================================================================================
# Orchestration + validation
# ======================================================================================

#: Held-out validators. Neither is available to the matcher, so both stay usable as
#: quality gates on every rebuild. ``purchaser_type`` is the only one that works on
#: increment rows (there is no chain pair to concord against).
_VALIDATOR_COLS = ["HMDAIndex", "purchaser_type", "co_applicant_sex"]


def _work_path(name: str) -> Path:
    """An intermediate artifact of the direct matcher, under the workflow's data directory."""
    return Config.HMDA_GSE_OUTPUT_DIR / "direct" / f"{name}.parquet"


def _write(df: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_output(path) as tmp:
        df.write_parquet(tmp)


@log_execution_time
def build_bridge(*, overwrite: bool = False) -> pl.DataFrame:
    """Build the lender bridge: the name bridge plus names learned by the bridge pass.

    Runs ``rounds.BRIDGE_PASS`` over the full pool of every year in
    ``Config.MIN_YEAR``-``Config.MAX_YEAR`` (caching each year's pairs), then learns
    from the pairs (`learn_bridge`). The pass's pairs are not matches: the rounds run
    afterwards, on the finished bridge.

    Args:
        overwrite: Rerun the bridge pass rather than reuse its cached pairs.

    Returns:
        ``lei``, ``seller_norm``, ``year`` (null for name-bridge entries), sorted.

    Raises:
        ValueError: ``BRIDGE_PASS`` is name-pinned, which needs the bridge it builds.
    """
    if BRIDGE_PASS.name_pinned:
        raise ValueError("the bridge pass cannot be name-pinned: it builds the bridge")
    years = list(range(Config.MIN_YEAR, Config.MAX_YEAR + 1))
    no_claims = pl.DataFrame(schema={"HMDAIndex": pl.String, "loan_key": pl.String})
    for year in years:
        path = _work_path(f"bridge_pass_{year}")
        if should_process_file(path, overwrite):
            _write(match_round(BRIDGE_PASS, year, None, no_claims), path)
    taught = pl.concat([pl.read_parquet(_work_path(f"bridge_pass_{y}")) for y in years])
    universe = build_name_universe(Config.MIN_YEAR, Config.MAX_YEAR)
    bridge = learn_bridge(taught, build_name_bridge(universe), universe)
    return bridge.sort("lei", "seller_norm", "year", nulls_last=False)


@log_execution_time
def run_rounds(bridge: pl.DataFrame) -> pl.DataFrame:
    """Run ``rounds.ROUNDS`` in order over every year, each on what earlier rounds left.

    Always spans ``Config.MIN_YEAR``-``Config.MAX_YEAR``, whatever years a caller then
    writes: a target an earlier round claimed in any year is gone for every later round,
    and pooled exclusivity (`pooled_unique`) has to see every year that could claim one.

    Args:
        bridge: The lender bridge (`build_bridge`).

    Returns:
        Every round's accepted pairs, ``match_round`` set to the round's position.
    """
    years = list(range(Config.MIN_YEAR, Config.MAX_YEAR + 1))
    claimed = pl.DataFrame(schema={"HMDAIndex": pl.String, "loan_key": pl.String})
    found: list[pl.DataFrame] = []
    for position, rnd in enumerate(ROUNDS, start=1):
        pairs = pl.concat([match_round(rnd, year, bridge, claimed) for year in years])
        pairs = pairs.with_columns(pl.lit(position, dtype=pl.UInt8).alias("match_round"))
        logger.info("round %s (%s): %s pairs", position, rnd.description, f"{len(pairs):,}")
        found.append(pairs)
        claimed = pl.concat([claimed, pairs.select("HMDAIndex", "loan_key")])
    return pl.concat(found)


@log_execution_time
def run_matching_direct(
    min_year: int | None = None,
    max_year: int | None = None,
    *,
    overwrite: bool = False,
    record_exclusive: bool = True,
    saved_bridge: bool = False,
) -> dict[int, int]:
    """Build the direct HMDA<->issuance crosswalk for a span of activity years.

    Builds the lender bridge (`build_bridge`) and saves it, or reads the saved one; runs
    the rounds over the whole window (`run_rounds`) and applies pooled exclusivity; then
    per year resolves the pairs against the chain, attaches the FHFA verdict, and writes
    one crosswalk file. Rows are never silently discarded -- every row carries its
    ``disposition`` and ``fhfa_verdict``, so a consumer picks its own admission rule and
    a future adjudication of the flagged set does not need a rerun.

    Args:
        min_year: First activity year (defaults to ``Config.MIN_YEAR``).
        max_year: Last activity year (defaults to ``Config.MAX_YEAR``).
        overwrite: Rerun the bridge pass and rebuild the years whose crosswalk already
            exists.
        record_exclusive: Label increment rows that claim a chain-assigned target
            as ``dropped_double_claim`` rather than admitting them.
        saved_bridge: Read the bridge saved at ``Config.get_bridge_path()`` instead of
            building it.

    Returns:
        Rows written per year (absent for years skipped as already built).

    Raises:
        FileNotFoundError: ``saved_bridge`` and no bridge is saved.
    """
    min_year = Config.MIN_YEAR if min_year is None else min_year
    max_year = Config.MAX_YEAR if max_year is None else max_year
    Config.ensure_directories()

    todo = []
    for year in range(min_year, max_year + 1):
        out = Config.get_direct_crosswalk_path(year)
        if should_process_file(out, overwrite):
            todo.append(year)
        else:
            logger.info("%s: %s exists; skipping", year, out.name)
    if not todo:
        return {}

    bridge_path = Config.get_bridge_path()
    if saved_bridge:
        if not bridge_path.exists():
            raise FileNotFoundError(f"no saved bridge at {bridge_path}; build one first")
        bridge = pl.read_parquet(bridge_path)
        logger.info("bridge: %s entries read from %s", f"{len(bridge):,}", bridge_path)
    else:
        bridge = build_bridge(overwrite=overwrite)
        _write(bridge, bridge_path)
        logger.info("bridge: %s entries saved to %s", f"{len(bridge):,}", bridge_path)

    pooled = attach_target_umbs(pooled_unique(run_rounds(bridge)))

    written: dict[int, int] = {}
    for year in todo:
        out = Config.get_direct_crosswalk_path(year)
        matched = pooled.filter(pl.col("activity_year") == year)
        if matched.is_empty():
            logger.warning("%s: no matches; nothing written", year)
            continue
        labelled = fhfa_screen(admit(matched, year, record_exclusive=record_exclusive))
        with atomic_output(out) as tmp:
            labelled.write_parquet(tmp)
        written[year] = len(labelled)
        logger.info("%s: wrote %s rows -> %s", year, f"{len(labelled):,}", out)
    return written


def attach_target_umbs(pairs: pl.DataFrame) -> pl.DataFrame:
    """Add ``target_umbs``: whether the matched target carries a UMBS identity.

    Args:
        pairs: Pairs with a ``loan_key``.

    Returns:
        ``pairs`` with ``target_umbs`` (false where the target has none, or is unknown).
    """
    umbs = (
        pl.scan_parquet(str(Config.COMBINED_LOAN_ISSUANCE_DIR), hive_partitioning=True)
        .filter(pl.col("loan_key_umbs").is_not_null())
        .select("loan_key")
        .join(pairs.select("loan_key").unique().lazy(), on="loan_key", how="semi")
        .unique()
        .collect(engine="streaming")
    )
    return pairs.with_columns(
        pl.col("loan_key").is_in(umbs["loan_key"].implode()).alias("target_umbs")
    )


def admitted_row() -> pl.Expr:
    """Whether a crosswalk row is admitted to the master's conventional edge.

    Chain-agreeing rows always are. An increment row (a loan the chain left unlinked)
    is admitted unless the chain's FHFA enterprise pin contradicts it. An increment
    matched without a seller name (``seller_norm`` null) needs more: the pin must
    positively corroborate it -- with no name in the key, the pin is the only
    lender-side evidence it has -- and its target must carry a UMBS identity.
    Corroborated nameless increments on targets without one agree with HMDA's borrower
    count only 25.0% of the time (800 rows, 2018-2025), against 99.1% on targets with
    one.

    Returns:
        A boolean expression over ``disposition``, ``fhfa_verdict``, ``seller_norm`` and
        ``target_umbs``.
    """
    corroborated_if_nameless = pl.col("seller_norm").is_not_null() | (
        (pl.col("fhfa_verdict") == "corroborated") & pl.col("target_umbs")
    )
    return (pl.col("disposition") == "agrees") | (
        (pl.col("disposition") == "increment")
        & (pl.col("fhfa_verdict") != "contradicted")
        & corroborated_if_nameless
    )


def _validators(year: int) -> pl.LazyFrame:
    """Load the held-out HMDA fields used for validation."""
    return scan_hmda("post2018", year, root=Config.HMDA_SILVER_DIR).select(_VALIDATOR_COLS)


@log_execution_time
def run_validation_direct(year: int) -> pl.DataFrame:
    """Score one year's crosswalk on held-out fields, by round, disposition and FHFA verdict.

    Three measures, none of them a matcher input:

    - ``borrowers``: HMDA's implied borrower count vs the target's, against a
      within-cell shuffled floor (~0.50). Chain-confirmed rows benchmark at ~0.998.
    - ``destination``: ``purchaser_type`` vs the matched agency. The only measure
      that works on increment rows.
    - ``concordance``: agreement with the chain, defined only where the chain also
      matched the loan.

    Args:
        year: HMDA activity year.

    Returns:
        One row per (match_round, disposition, fhfa_verdict) cell.
    """
    matches = pl.scan_parquet(Config.get_direct_crosswalk_path(year))
    target = pl.scan_parquet(str(Config.COMBINED_LOAN_ISSUANCE_DIR), hive_partitioning=True).select(
        "loan_key", "number_of_borrowers"
    )
    frame = (
        matches.join(_validators(year), on="HMDAIndex", how="left")
        .join(target, on="loan_key", how="left")
        .with_columns(
            pl.when(pl.col("co_applicant_sex").is_in([1, 2, 3, 6]))
            .then(2)
            .otherwise(1)
            .alias("_h_nb"),
            pl.col("number_of_borrowers").cast(pl.Int32).clip(upper_bound=2).alias("_t_nb"),
            (
                ((pl.col("purchaser_type") == 1) & (pl.col("agency") == "FNMA"))
                | ((pl.col("purchaser_type") == 3) & (pl.col("agency") == "FHLMC"))
            ).alias("_dest_ok"),
        )
        .collect(engine="streaming")
    )
    shuffled = frame.with_columns(pl.col("_t_nb").shuffle(seed=7))
    return (
        frame.group_by("match_round", "disposition", "fhfa_verdict")
        .agg(
            pl.len().alias("n"),
            (pl.col("_h_nb") == pl.col("_t_nb")).mean().alias("borrowers"),
            (pl.col("_dest_ok").sum() / pl.col("purchaser_type").is_in([1, 3]).sum()).alias(
                "destination"
            ),
            (pl.col("chain_key") == pl.col("loan_key")).mean().alias("concordance"),
        )
        .with_columns(
            pl.lit(year, dtype=pl.Int32).alias("activity_year"),
            pl.lit(shuffled.select((pl.col("_h_nb") == pl.col("_t_nb")).mean()).item()).alias(
                "borrowers_shuffled"
            ),
        )
        .sort("n", descending=True)
    )
