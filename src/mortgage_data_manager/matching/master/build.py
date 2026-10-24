"""Assemble the master crosswalk: one row per post-2018 HMDA loan.

The spine is the union of every loan-side ``HMDAIndex`` across the workflows'
crosswalks. Each edge is collapsed to ONE best row per ``HMDAIndex`` (lowest
match round) and then left-joined, so the result is exactly one row per loan
with nullable downstream ids, ``in_*`` coverage flags, and a ``channel``. A tie at
the best round is never broken: `_best` stops the build instead of picking a row.

Deliberately skinny: ids, rounds and provenance only. Join to the underlying
silver for loan characteristics -- this artifact is a linkage index, not a
feature table.

The conventional edge is **chain UNION direct**: the GSE chain masters
(HMDA -> FHFA -> MBS -> UMBS) plus the direct HMDA<->issuance matcher, which
reaches ``combined/loan_issuance`` without the FHFA leg. ``mbs_source`` records
which path supplied each row. Because the direct path skips FHFA, ``in_mbs`` is
NOT a subset of ``in_fhfa`` -- see `~.qc` for the invariant checked instead.

Deliberately EXCLUDED: an FNMA-FHLB edge (AMA misses FNMA-routed / MPF-Xtra
loans, so it finds essentially no pairs) and an FHLMC-FHLB edge (not built).
FHLB reaches the spine through ``hmda_fhlb`` only.

Basis: ``docs/matching/master_crosswalk.md``.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, Literal

import polars as pl

from mortgage_data_manager.core.logging import get_logger, log_execution_time
from mortgage_data_manager.core.medallion import clear_partitions, write_hive_partitioned
from mortgage_data_manager.matching.master.config import MasterCrosswalkConfig as Config
from mortgage_data_manager.matching.match_hmda_gse.matching_direct import admitted_row
from mortgage_data_manager.sources.hmda.load import scan_hmda_years
from mortgage_data_manager.sources.hmda.utils import hmda_index_year

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger(__name__)

#: How to treat a link two methods disagree about. ``strict`` (the default)
#: withholds it and records the reason; ``flag`` keeps the priority winner and
#: records the reason only.
ConflictPolicy = Literal["strict", "flag"]


# ======================================================================================
# Helpers
# ======================================================================================


def _best(
    lf: pl.LazyFrame, key: str, order: list[pl.Expr], keep: list[str], *, edge: str
) -> pl.LazyFrame:
    """Collapse an edge to one row per ``key``: its lowest-``order`` row, never a pick.

    Rows are narrowed to each key's minimum of every ``order`` expression in turn (most
    significant first; a null ranks last). Byte-identical survivors collapse; a key whose
    survivors still differ on ``keep`` is a tie no evidence separates, and the build
    stops rather than choose one. With an empty ``order`` any key carrying two different
    rows stops the build -- for edges with no ranking signal.

    Args:
        lf: The edge's rows.
        key: Column to collapse on.
        order: Ranking expressions, most significant first; lower is better.
        keep: Columns carried from the surviving row.
        edge: Edge name, for the error.

    Returns:
        One row per ``key``: ``key`` plus ``keep``.

    Raises:
        ValueError: If any key ties at its best rank with different ``keep`` values.
    """
    best = lf
    for expr in order:
        lowest = expr.min().over(key)
        best = best.filter((expr == lowest) | lowest.is_null())
    best = best.select(key, *keep).unique()
    tied = best.group_by(key).len().filter(pl.col("len") > 1).select(pl.len()).collect().item()
    if tied:
        msg = (
            f"{edge} edge: {tied:,} {key} value(s) tie at their best rank with different "
            "links; refusing to pick one"
        )
        raise ValueError(msg)
    return best


def _missing_inputs(min_year: int, max_year: int) -> tuple[list[str], list[str]]:
    """Split absent inputs into fatal (required edges) and skippable (optional).

    Args:
        min_year: First activity year requested.
        max_year: Last activity year requested.

    Returns:
        ``(fatal, optional)`` lists of human-readable descriptions.
    """
    required: list[tuple[str, Path]] = [
        ("hmda_fha", Config.HMDA_FHA_CROSSWALK),
        ("hmda_fhfa", Config.HMDA_FHFA_CROSSWALK),
        ("hmda_gnma_fha", Config.HMDA_GNMA_FHA_CROSSWALK),
        ("hmda_gnma_va_usda", Config.HMDA_GNMA_VA_USDA_CROSSWALK),
        ("hmda_sellers_purchasers", Config.HMDA_SELLERS_PURCHASERS_CROSSWALK),
    ]
    required += [(f"hmda_gse_chain_{g}", Config.get_chain_crosswalk_path(g)) for g in Config.GSES]  # type: ignore[arg-type]
    required += [(f"mbs_umbs_{g}", Config.get_mbs_umbs_path(g)) for g in Config.GSES]  # type: ignore[arg-type]

    fatal = [f"{name} -> {path}" for name, path in required if not path.exists()]

    optional: list[str] = []
    for name, path in (
        ("hmda_fhlb", Config.HMDA_FHLB_CROSSWALK),
        ("gnma_fhlb", Config.FHLB_MBS_CROSSWALK),
        ("hmda_fha_pre2018", Config.HMDA_FHA_PRE2018_CROSSWALK),
    ):
        if not path.exists():
            optional.append(f"{name} -> {path}")

    direct_years = [
        y
        for y in range(min_year, min(max_year, Config.CONVENTIONAL_MAX_YEAR) + 1)
        if not Config.get_direct_crosswalk_path(y).exists()
    ]
    if direct_years:
        optional.append(
            f"hmda_gse direct years {direct_years} (run `match hmda-gse run --route direct`)"
        )
    return fatal, optional


def load_cross_era_fha() -> pl.DataFrame:
    """FHA endorsements the pre-2018 HMDA-FHA crosswalk also pairs with a HMDA loan.

    Each era's crosswalk is 1:1 within itself, but both eras' matchers can reach
    late-2017 / January-2018 endorsements, so one endorsement can be paired with a
    2017 loan in the pre-2018 file and a later loan in the spine's FHA edge. Both
    cannot be right and the spine does not adjudicate between the eras, so such a
    link is withheld (``fha_cross_era``; see `_apply_conflict_policy`).

    Returns:
        A single-column ``fha_index`` frame; empty when the pre-2018 crosswalk is
        absent (`_missing_inputs` warns about that).
    """
    if not Config.HMDA_FHA_PRE2018_CROSSWALK.exists():
        return pl.DataFrame(schema={"fha_index": pl.String})
    return (
        pl.read_parquet(Config.HMDA_FHA_PRE2018_CROSSWALK, columns=["FHA_Index"])
        .rename({"FHA_Index": "fha_index"})
        .unique()
    )


# ======================================================================================
# The direct HMDA<->issuance edge
# ======================================================================================


@log_execution_time
def load_direct_edge() -> tuple[pl.DataFrame, pl.DataFrame]:
    """Load the direct edge's increment rows and its contested-target set.

    The direct crosswalk labels every row rather than dropping any, so the
    admission rule (``match_hmda_gse.matching_direct.admitted_row``) is applied here:

    - ``agrees`` -- same target as the chain, so a no-op for the spine.
    - ``increment`` (not FHFA-contradicted) -- the coverage this edge adds.
    - ``contested_target`` -- the two matchers name DIFFERENT targets. Not
      reliably adjudicable (80% are twins on every held-out field, and the two
      usable estimators disagree in direction), so the chain keeps priority as
      the conservative status quo and the row is FLAGGED via ``mbs_contested``.
    - ``dropped_double_claim`` / FHFA-``contradicted`` -- excluded as unsafe.

    The ``increment`` / ``contested_target`` labels describe the chain AS IT WAS
    when the direct crosswalk was built. The chain masters are rebuilt more often
    (the fhfa_mbs recovery rounds alone re-claimed thousands of refi-boom loans),
    so every increment row is re-checked against the CURRENT chain here: a row
    whose HMDAIndex the chain now links to the same target is a stale ``agrees``
    and is dropped as a no-op; one the chain now links to a DIFFERENT target is a
    stale ``contested_target`` and joins the contested set, so the conventional
    link is withheld for it exactly as for the upstream-labelled cases. Without
    this the spine carried one chain row and one direct row per such loan and
    failed the grain check.

    Record-exclusivity is then re-derived GLOBALLY. The upstream ``admit()``
    builds its claimed-target set from a single activity year, so a target the
    chain assigned to an ADJACENT year's HMDA loan is invisible to it -- and the
    matcher deliberately admits prior-year Q4 originations. Pooled 2018-2024
    that leaks ~14.9k chain collisions plus ~1.9k twice-claimed targets, 100% of
    them cross-year. Dropping them keeps the pooled spine one-target-one-loan.

    **Takes no year range on purpose.** The exclusivity universe must span EVERY
    built year, not the years being rebuilt, or a single-year rebuild reproduces
    the very bug this screen exists to fix: with only 2024 loaded, a target also
    claimed by a 2021 increment loan has no visible partner and survives. That
    made a one-year rebuild disagree with the full build by 30 rows. Callers
    slice the result by year themselves.

    Returns:
        ``(increment, contested)``. ``increment`` has one row per HMDAIndex the
        chain never matched, with ``loan_key``, ``agency``, ``mbs_match_round``,
        ``fhfa_verdict`` and ``umbs_loan_id``. ``contested`` is a single-column
        frame of contested ``HMDAIndex``. Both are empty when no year is built.
    """
    paths = [
        p
        for y in range(Config.MIN_YEAR, Config.CONVENTIONAL_MAX_YEAR + 1)
        if (p := Config.get_direct_crosswalk_path(y)).exists()
    ]
    empty_inc = pl.DataFrame(
        schema={
            "HMDAIndex": pl.String,
            "loan_key": pl.String,
            "activity_year": pl.Int32,
            "agency": pl.String,
            "mbs_match_round": pl.UInt8,
            "fhfa_verdict": pl.String,
            "umbs_loan_id": pl.String,
        }
    )
    if not paths:
        logger.warning("No direct-issuance crosswalk found; conventional edge is chain-only")
        return empty_inc, pl.DataFrame(schema={"HMDAIndex": pl.String})

    direct = pl.concat([pl.scan_parquet(p) for p in paths], how="diagonal_relaxed")
    increment = direct.filter(admitted_row() & (pl.col("disposition") == "increment")).select(
        "HMDAIndex",
        "loan_key",
        "activity_year",
        pl.col("agency").str.to_lowercase().alias("agency"),
        pl.col("match_round").cast(pl.UInt8).alias("mbs_match_round"),
        "fhfa_verdict",
    )

    chain_links = pl.concat(
        [
            pl.scan_parquet(Config.get_chain_crosswalk_path(g)).select(  # type: ignore[arg-type]
                "HMDAIndex", pl.col("mbs_loan_id").alias("_chain_key")
            )
            for g in Config.GSES
        ],
        how="diagonal_relaxed",
    ).unique()
    chain_claimed = chain_links.select(pl.col("_chain_key").alias("loan_key")).unique()

    n_admitted = increment.select(pl.len()).collect().item()

    # re-check every increment row against the chain as it stands today
    overlap = (
        increment.join(chain_links, on="HMDAIndex", how="inner")
        .group_by("HMDAIndex")
        .agg((pl.col("loan_key") == pl.col("_chain_key")).any().alias("_agrees"))
        .collect(engine="streaming")
    )
    stale_contested = overlap.filter(~pl.col("_agrees")).select("HMDAIndex")
    n_stale_agree = overlap.height - stale_contested.height
    increment = increment.join(overlap.lazy().select("HMDAIndex"), on="HMDAIndex", how="anti")

    multi_claimed = (
        increment.group_by("loan_key")
        .agg(pl.len().alias("_n"))
        .filter(pl.col("_n") > 1)
        .select("loan_key")
    )
    excluded = pl.concat([chain_claimed, multi_claimed], how="diagonal_relaxed").unique()
    increment = increment.join(excluded, on="loan_key", how="anti")

    # UMBS hop: a left join, so a miss costs the UMBS id and not the match.
    umbs = pl.concat(
        [
            pl.scan_parquet(Config.get_mbs_umbs_path(g)).select(  # type: ignore[arg-type]
                pl.col("Loan Sequence Number (MBS)").alias("loan_key"),
                pl.col("Loan Sequence Number (UMBS)").alias("umbs_loan_id"),
            )
            for g in Config.GSES
        ],
        how="diagonal_relaxed",
    )
    # mbs_umbs is 1:1 by construction; an MBS id with two different UMBS ids gets neither
    umbs = umbs.unique().unique(subset="loan_key", keep="none")
    inc = increment.join(umbs, on="loan_key", how="left").collect(engine="streaming")

    upstream_contested = (
        direct.filter(pl.col("disposition") == "contested_target")
        .select("HMDAIndex")
        .collect(engine="streaming")
    )
    contested = pl.concat([upstream_contested, stale_contested]).unique()
    n_umbs = int(inc["umbs_loan_id"].is_not_null().sum())
    logger.info(
        "direct edge: %d admitted increment -> %d after re-check against the current chain "
        "(-%d now agree, -%d now contested) and global exclusivity; "
        "%d contested (%d upstream + %d stale); UMBS attached %d (%.2f%%)",
        n_admitted,
        inc.height,
        n_stale_agree,
        stale_contested.height,
        contested.height,
        upstream_contested.height,
        stale_contested.height,
        n_umbs,
        (n_umbs / inc.height * 100) if inc.height else 0.0,
    )
    return inc, contested


# ======================================================================================
# Per-edge loaders
# ======================================================================================


def _edge_fha(year_filter: pl.Expr) -> pl.LazyFrame:
    """HMDA <-> FHA endorsement edge."""
    return _best(
        pl.scan_parquet(Config.HMDA_FHA_CROSSWALK).filter(year_filter),
        "HMDAIndex",
        [pl.col("match_round")],
        ["FHA_Index", "match_round"],
        edge="fha",
    ).rename({"FHA_Index": "fha_index", "match_round": "fha_match_round"})


def _edge_fhfa(year_filter: pl.Expr) -> pl.LazyFrame:
    """HMDA <-> FHFA PUDB edge (the chain's first leg)."""
    return _best(
        pl.scan_parquet(Config.HMDA_FHFA_CROSSWALK).filter(year_filter),
        "HMDAIndex",
        [pl.col("match_round")],
        ["record_number", "fhfa_year", "enterprise_flag", "match_round"],
        edge="fhfa",
    ).rename(
        {
            "record_number": "fhfa_record_number",
            "enterprise_flag": "fhfa_enterprise",
            "match_round": "fhfa_match_round",
        }
    )


def _edge_fhlb(year_filter: pl.Expr) -> pl.LazyFrame:
    """HMDA <-> FHLB AMA edge (empty frame when the crosswalk is absent)."""
    if not Config.HMDA_FHLB_CROSSWALK.exists():
        return pl.LazyFrame(
            schema={"HMDAIndex": pl.String, "fhlb_id": pl.Int64, "fhlb_match_round": pl.UInt8}
        )
    return _best(
        pl.scan_parquet(Config.HMDA_FHLB_CROSSWALK).filter(year_filter),
        "HMDAIndex",
        [pl.col("match_round")],
        ["LoanCharacteristicsID", "match_round"],
        edge="fhlb",
    ).rename({"LoanCharacteristicsID": "fhlb_id", "match_round": "fhlb_match_round"})


def _edge_conventional(
    year: int, year_filter: pl.Expr, increment: pl.DataFrame
) -> tuple[pl.LazyFrame, int]:
    """Conventional edge: the GSE chain masters UNION the direct increment.

    The two are disjoint on ``HMDAIndex`` because `load_direct_edge`
    re-checks every increment row against the current chain and removes any the
    chain now claims (agreeing ones as no-ops, disagreeing ones into the
    contested set), so the union needs no priority rule beyond the concat, and
    `~.qc` asserts the disjointness.

    Args:
        year: Activity year being built.
        year_filter: HMDAIndex-prefix year filter.
        increment: Output of `load_direct_edge`.

    Returns:
        ``(edge, n_direct)`` -- the unioned edge and how many rows came from
        the direct path.
    """
    chain = pl.concat(
        [
            pl.scan_parquet(Config.get_chain_crosswalk_path(g))  # type: ignore[arg-type]
            .filter(year_filter)
            .select(
                "HMDAIndex",
                "mbs_loan_id",
                "umbs_loan_id",
                pl.col("match_round").alias("mbs_match_round"),
                pl.lit(g).alias("agency"),
                pl.lit("chain").alias("mbs_source"),
            )
            for g in Config.GSES
        ],
        how="diagonal_relaxed",
    )
    # A HMDA loan belongs to one GSE and each chain master is 1:1 on HMDAIndex, so a loan
    # in both masters is two mutually exclusive claims: no ranking applies, stop the build.
    chain = _best(
        chain,
        "HMDAIndex",
        [],
        ["mbs_loan_id", "umbs_loan_id", "mbs_match_round", "agency", "mbs_source"],
        edge="conventional chain",
    )

    direct_year = increment.filter(pl.col("activity_year") == year)
    direct = direct_year.lazy().select(
        "HMDAIndex",
        pl.col("loan_key").alias("mbs_loan_id"),
        "umbs_loan_id",
        "mbs_match_round",
        "agency",
        pl.when(pl.col("fhfa_verdict") == "corroborated")
        .then(pl.lit("direct_corroborated"))
        .otherwise(pl.lit("direct_no_link"))
        .alias("mbs_source"),
    )
    return pl.concat([chain, direct], how="diagonal_relaxed"), direct_year.height


def _edge_gnma(year_filter: pl.Expr) -> tuple[pl.LazyFrame, pl.LazyFrame]:
    """GNMA edges: the FHA-program master and the VA/USDA master.

    Both are already 1:1 on HMDAIndex and verified HMDAIndex-disjoint. The FHA
    master is a direct match keyed on each loan's FHA endorsement (through
    ``hmda_fha``), so every FHA-program link is ``fha_direct``.

    Returns:
        The FHA-program edge and the VA/USDA edge.
    """
    fha_program = (
        pl.scan_parquet(Config.HMDA_GNMA_FHA_CROSSWALK)
        .filter(year_filter)
        .select(
            "HMDAIndex",
            pl.col("gnma_loan_id").alias("gnma_loan_id_fha"),
            pl.lit("fha_direct").alias("gnma_source_fha"),
            pl.col("match_round").cast(pl.UInt8).alias("gnma_fha_match_round"),
        )
    )
    va_usda = (
        pl.scan_parquet(Config.HMDA_GNMA_VA_USDA_CROSSWALK)
        .filter(year_filter)
        .select(
            "HMDAIndex",
            pl.col("gnma_loan_id").alias("gnma_loan_id_va_usda"),
            pl.when(pl.col("agency") == "V")
            .then(pl.lit("va"))
            .otherwise(pl.lit("usda"))
            .alias("gnma_source_va_usda"),
            pl.col("match_round").cast(pl.UInt8).alias("gnma_va_usda_match_round"),
        )
    )
    return fha_program, va_usda


def _edge_gnma_via_fhlb(fhlb: pl.LazyFrame) -> pl.LazyFrame:
    """GNMA reached through the FHLB AMA bridge -- fallback only.

    Contributes only loans neither GNMA master reaches. Retained because it is
    an independent matcher: where a loan carries both this id and a master id
    the two agree ~99.8% of the time, which is mutual corroboration for two
    completely different match designs.

    Returns:
        The GNMA edge reached through the FHLB AMA bridge.
    """
    empty = pl.LazyFrame(schema={"HMDAIndex": pl.String, "gnma_loan_id_fhlb": pl.String})
    if not Config.FHLB_MBS_CROSSWALK.exists():
        return empty
    bridge = _best(
        pl.scan_parquet(Config.FHLB_MBS_CROSSWALK),
        "fhlb_loan_id",
        [],  # no match metadata on this edge: an FHLB loan with two GNMA ids stops the build
        ["gnma_loan_id"],
        edge="gnma_fhlb bridge",
    ).rename({"fhlb_loan_id": "fhlb_id", "gnma_loan_id": "gnma_loan_id_fhlb"})
    return (
        fhlb.select("HMDAIndex", "fhlb_id")
        .join(bridge, on="fhlb_id", how="inner")
        .select("HMDAIndex", "gnma_loan_id_fhlb")
    )


_PURCHASE_PT: pl.DataFrame | None = None


def _purchase_record_purchaser_types() -> pl.DataFrame:
    """Each HMDA purchased-loan record's OWN ``purchaser_type``.

    A purchase record reports who *that* buyer sold to next, which is what orders
    successive owners. Loaded across every year because a 2018 origination's
    second buyer may file in a later year.

    Returns:
        One row per purchased-loan HMDAIndex with its ``purchaser_type``.

    Raises:
        ValueError: If a purchase record carries two different ``purchaser_type`` values.
    """
    global _PURCHASE_PT
    if _PURCHASE_PT is not None:
        return _PURCHASE_PT
    pt = (
        scan_hmda_years(
            "post2018", Config.MIN_YEAR, Config.MAX_YEAR, root=Config.HMDA_SILVER_LOANS_DIR
        )
        .filter(pl.col("action_taken") == Config.PURCHASED_LOAN_ACTION)
        .select("HMDAIndex", "purchaser_type")
        .unique()
        .collect(engine="streaming")
    )
    # HMDAIndex is unique in silver; a record with two purchaser_types would order its
    # chain arbitrarily, so stop rather than pick one
    n_conflict = pt.height - pt["HMDAIndex"].n_unique()
    if n_conflict:
        msg = f"{n_conflict:,} purchase records carry two different purchaser_type values"
        raise ValueError(msg)
    _PURCHASE_PT = pt
    logger.info("purchase-record purchaser_type: %d records", _PURCHASE_PT.height)
    return _PURCHASE_PT


def _edge_seller_purchaser(year: int) -> pl.LazyFrame:
    """HMDA-internal ownership chain: origination -> successive purchase records.

    This edge is one-to-many because a loan sold more than once has one HMDA
    purchase record PER BUYER, and every one of them describes the same loan, so
    every one matches the origination. They are successive owners, not competing
    candidates -- which is why the spine stores the whole ordered chain rather
    than picking one.

    **The order is inferred, not traversed.** No purchase record is itself a
    seller in the crosswalk (verified: 0), so there is no linked list to walk. The
    ordering signal is each record's own ``purchaser_type``: a record reporting an
    agency buyer sold the loan ON to an agency, so it is the LAST HMDA-visible
    hop, and its counterpart is the earlier one. Exactly one of the two reports an
    onward agency sale for 60.0% of multi-hop loans (107,475 of 179,075), and the
    inferred terminal agency is confirmed by the spine's independent
    conventional/GNMA edge 99.8% (FHLMC) / 98.2% (FNMA) of the time.

    **The chain deliberately stops before the agency.** Fannie, Freddie and Ginnie
    are not HMDA reporters and file no purchase records -- the largest
    purchased-loan filers are all aggregators -- so the terminal agency hop is read
    off ``agency`` / ``fhfa_enterprise`` / ``in_gnma``, not from here. That is also
    why the upstream matcher excludes originations with ``purchaser_type`` in
    {1,2,3,4}: a sale to an agency can never have a matching HMDA purchase record.

    Args:
        year: HMDA activity year.

    Returns:
        One row per seller HMDAIndex with ``sp_chain`` (ordered where known),
        ``sp_chain_n``, ``sp_chain_ordered``, ``purchaser_hmdaindex`` (the first
        hop, NULL when a multi-hop chain could not be ordered) and
        ``sp_match_round``.
    """
    raw = pl.scan_parquet(Config.HMDA_SELLERS_PURCHASERS_CROSSWALK, hive_partitioning=True).filter(
        pl.col("activity_year_s") == year
    )
    hops = raw.join(
        _purchase_record_purchaser_types()
        .lazy()
        .select(pl.col("HMDAIndex").alias("HMDAIndex_p"), "purchaser_type"),
        on="HMDAIndex_p",
        how="left",
    ).with_columns(
        pl.col("purchaser_type")
        .is_in(Config.AGENCY_PURCHASER_TYPES)
        .fill_null(value=False)
        .alias("_onward")
    )
    agg = hops.group_by("HMDAIndex_s").agg(
        # ascending on _onward puts the earlier hop first and the agency-selling hop
        # last; HMDAIndex_p only fixes the list's presentation order and never decides
        # the first hop (see sp_chain_ordered)
        pl.col("HMDAIndex_p").sort_by("_onward", "HMDAIndex_p").alias("sp_chain"),
        pl.col("HMDAIndex_p").n_unique().alias("sp_chain_n"),
        pl.col("match_round").min().alias("sp_match_round"),
        pl.col("_onward").sum().alias("_n_onward"),
    )
    return (
        agg.with_columns(
            # a single hop is trivially ordered; a pair is ordered when exactly one
            # side reports the onward agency sale. Three or more hops never are: the
            # onward sale fixes only the LAST hop, so the first stays undetermined.
            (
                (pl.col("sp_chain_n") == 1)
                | ((pl.col("sp_chain_n") == 2) & (pl.col("_n_onward") == 1))
            ).alias("sp_chain_ordered")
        )
        .with_columns(
            # never expose an arbitrary "the purchaser": null it when unordered
            pl.when(pl.col("sp_chain_ordered"))
            # tie-safe: taken only when sp_chain_ordered, i.e. the first hop is determined
            .then(pl.col("sp_chain").list.first())
            .otherwise(None)
            .alias("purchaser_hmdaindex")
        )
        .drop("_n_onward")
        .rename({"HMDAIndex_s": "HMDAIndex"})
    )


# ======================================================================================
# Conflict policy
# ======================================================================================

#: Link groups a conflict can withhold, and the columns each group owns.
_LINK_COLUMNS: dict[str, tuple[str, ...]] = {
    "conventional": ("mbs_loan_id", "umbs_loan_id", "mbs_match_round", "agency", "mbs_source"),
    "fhfa": ("fhfa_record_number", "fhfa_year", "fhfa_enterprise", "fhfa_match_round"),
    "fha": ("fha_index", "fha_match_round"),
    "gnma": (
        "gnma_loan_id",
        "gnma_source",
        "gnma_match_round",
        "gnma_loan_id_fha",
        "gnma_fha_match_round",
        "gnma_loan_id_va_usda",
        "gnma_va_usda_match_round",
        "gnma_loan_id_fhlb",
    ),
    "seller_purchaser": (
        "purchaser_hmdaindex",
        "sp_chain",
        "sp_chain_n",
        "sp_chain_ordered",
        "sp_match_round",
    ),
}

#: Flags recomputed after links are withheld. ``in_seller_purchaser`` keys on
#: ``sp_chain`` rather than ``purchaser_hmdaindex``, because an unordered
#: multi-hop chain stores its owners but deliberately leaves the "first hop"
#: field null -- the link exists, the sequence is what is unknown.
_FLAG_SOURCE: dict[str, str] = {
    "in_fha": "fha_index",
    "in_fhfa": "fhfa_record_number",
    "in_mbs": "mbs_loan_id",
    "in_umbs": "umbs_loan_id",
    "in_gnma": "gnma_loan_id",
    "in_fhlb": "fhlb_id",
    "in_seller_purchaser": "sp_chain",
}


def _apply_conflict_policy(lf: pl.LazyFrame, policy: ConflictPolicy) -> pl.LazyFrame:
    """Record every cross-matcher conflict and, under ``strict``, withhold the link.

    A conflict is two methods disagreeing about the same fact. Where that happens
    ``flag`` stores one side by priority; ``strict`` stores NEITHER,
    because a link that another method contradicts is exactly the link a
    reliability-first consumer must not trust. The reason is always recorded in
    ``conflict_flags``, so nothing disappears without a trace -- "we withheld this
    and why" beats a silent null.

    The four classes:

    - ``mbs_contested`` -- the chain and the direct matcher name DIFFERENT
      conventional targets. Not adjudicable: ~86% agree on the enterprise and
      differ only on which loan inside it (twins on every held-out field), and
      for the ~14% that differ on the enterprise the FHFA pin cannot break the
      tie because the chain routes THROUGH FHFA, so its agreement is tautological.
      Withholds the conventional link.
    - ``gse_gnma`` -- the loan carries both a GSE link (FHFA acquisition or GSE
      MBS/UMBS loan) and a GNMA loan. A loan is securitized by one guarantor, so
      at most one side is right and nothing cheap says which. Withholds the GSE
      links and the GNMA link. An FHA endorsement is NOT a conflict with a GSE
      link: the enterprises acquire FHA/VA/RHS-insured loans and disclose them
      with ``federal_guarantee`` set, and both FHFA matchers key on that field,
      so the FHA link and the GSE link on one loan are two views of one fact.
    - ``gnma_disagree`` -- the FHLB->GNMA bridge and the GNMA masters name
      different GNMA loans (two independent matchers, ~99.8% agreement). Withholds
      the GNMA link.
    - ``fha_cross_era`` -- the loan's FHA endorsement is also paired, in the
      pre-2018 crosswalk, with a 2017 HMDA loan (see `load_cross_era_fha`). Both
      pairs cannot be right and the eras are not adjudicated. Withholds the FHA
      link, and the FHA-program GNMA link too (``gnma_source == "fha_direct"``):
      that match is keyed on the endorsement's amount and month. A VA/USDA or
      FHLB-bridge GNMA link does not depend on it and stays.

    Multi-purchaser seller/purchaser loans are NOT a conflict class. They were
    briefly treated as one, on the reading that a second purchase record competed
    with the first. It does not: successive buyers each file a record for the same
    loan, so both are true and the sequence is the only open question. They are
    stored as an ordered ``sp_chain`` with ``sp_chain_ordered`` marking whether the
    order is known -- see `_edge_seller_purchaser`.

    Args:
        lf: The joined spine, with flags computed and gnma coalesced.
        policy: ``strict`` withholds; ``flag`` records only.

    Returns:
        ``lf`` plus ``conflict_flags`` (semicolon-joined reasons, null when clean),
        with flags recomputed if links were withheld.
    """
    conditions: dict[str, pl.Expr] = {
        "mbs_contested": pl.col("mbs_contested"),
        "gse_gnma": (pl.col("in_fhfa") | pl.col("in_mbs")) & pl.col("in_gnma"),
        "gnma_disagree": pl.col("gnma_loan_id_fhlb").is_not_null()
        & pl.coalesce("gnma_loan_id_fha", "gnma_loan_id_va_usda").is_not_null()
        & (
            pl.col("gnma_loan_id_fhlb").cast(pl.Utf8)
            != pl.coalesce(
                pl.col("gnma_loan_id_fha").cast(pl.Utf8),
                pl.col("gnma_loan_id_va_usda").cast(pl.Utf8),
            )
        ),
        "fha_cross_era": pl.col("_fha_cross_era"),
    }

    lf = lf.with_columns(
        [cond.alias(f"_conf_{name}") for name, cond in conditions.items()]
    ).with_columns(
        pl.concat_str(
            [
                pl.when(pl.col(f"_conf_{name}")).then(pl.lit(name)).otherwise(None)
                for name in conditions
            ],
            separator=";",
            ignore_nulls=True,
        )
        .replace("", None)
        .alias("conflict_flags")
    )

    if policy == "flag":
        return lf.drop([f"_conf_{n}" for n in conditions])

    # which link groups each conflict withholds
    withhold: dict[str, tuple[str, ...]] = {
        "mbs_contested": ("conventional",),
        "gse_gnma": ("conventional", "fhfa", "gnma"),
        "gnma_disagree": ("gnma",),
        "fha_cross_era": ("fha", "gnma"),
    }
    # where a conflict withholds a group only on some of its rows
    only_where: dict[tuple[str, str], pl.Expr] = {
        # a GNMA link reached THROUGH the withheld endorsement goes with it
        ("fha_cross_era", "gnma"): pl.col("gnma_source") == "fha_direct",
    }
    blank: dict[str, pl.Expr] = {}
    for name in conditions:
        for group in withhold[name]:
            hit = pl.col(f"_conf_{name}") & only_where.get((name, group), pl.lit(value=True))
            for col in _LINK_COLUMNS[group]:
                prior = blank.get(col, pl.col(col))
                blank[col] = pl.when(hit).then(None).otherwise(prior)
    lf = lf.with_columns([expr.alias(col) for col, expr in blank.items()])

    # recompute the flags so no withheld link is still advertised
    return lf.with_columns(
        [pl.col(src).is_not_null().alias(flag) for flag, src in _FLAG_SOURCE.items()]
    ).drop([f"_conf_{n}" for n in conditions])


# ======================================================================================
# Assembly
# ======================================================================================


def build_year(
    year: int,
    increment: pl.DataFrame,
    contested: pl.DataFrame,
    cross_era_fha: pl.DataFrame,
    *,
    policy: ConflictPolicy = "strict",
) -> pl.DataFrame:
    """Assemble the spine for one activity year.

    Args:
        year: HMDA activity year.
        increment: Direct-edge increment rows (all years; filtered here).
        contested: Contested-target HMDAIndex set from the direct edge.
        cross_era_fha: FHA endorsements the pre-2018 crosswalk also claims
            (`load_cross_era_fha`).
        policy: ``strict`` withholds every contradicted link (see
            `_apply_conflict_policy`); ``flag`` keeps them and records the
            reason only.

    Returns:
        One row per HMDAIndex present in at least one edge for ``year``.
    """
    yf = hmda_index_year() == year

    fha = _edge_fha(yf)
    fhfa = _edge_fhfa(yf)
    fhlb = _edge_fhlb(yf)
    mbs, n_direct = _edge_conventional(year, yf, increment)
    gnma_fha, gnma_va_usda = _edge_gnma(yf)
    gnma_via_fhlb = _edge_gnma_via_fhlb(fhlb)
    sp = _edge_seller_purchaser(year)

    contested_lf = contested.lazy().with_columns(pl.lit(value=True).alias("mbs_contested"))
    cross_era_lf = cross_era_fha.lazy().with_columns(pl.lit(value=True).alias("_fha_cross_era"))

    spine = (
        pl.concat(
            [
                fha.select("HMDAIndex"),
                fhfa.select("HMDAIndex"),
                fhlb.select("HMDAIndex"),
                mbs.select("HMDAIndex"),
                gnma_fha.select("HMDAIndex"),
                gnma_va_usda.select("HMDAIndex"),
                sp.select("HMDAIndex"),
            ],
            how="diagonal_relaxed",
        )
        .unique()
        .with_columns(pl.lit(year).cast(pl.Int32).alias(Config.PARTITION_COL))
    )

    out = (
        spine.join(fha, on="HMDAIndex", how="left")
        .join(fhfa, on="HMDAIndex", how="left")
        .join(mbs, on="HMDAIndex", how="left")
        .join(fhlb, on="HMDAIndex", how="left")
        .join(gnma_fha, on="HMDAIndex", how="left")
        .join(gnma_va_usda, on="HMDAIndex", how="left")
        .join(gnma_via_fhlb, on="HMDAIndex", how="left")
        .join(sp, on="HMDAIndex", how="left")
        .join(contested_lf, on="HMDAIndex", how="left")
        .join(cross_era_lf, on="fha_index", how="left")
    )

    out = (
        out.with_columns(
            pl.coalesce("gnma_loan_id_fha", "gnma_loan_id_va_usda", "gnma_loan_id_fhlb").alias(
                "gnma_loan_id"
            ),
            pl.coalesce(
                "gnma_source_fha",
                "gnma_source_va_usda",
                pl.when(pl.col("gnma_loan_id_fhlb").is_not_null())
                .then(pl.lit("fhlb"))
                .otherwise(None),
            ).alias("gnma_source"),
            pl.coalesce("gnma_fha_match_round", "gnma_va_usda_match_round").alias(
                "gnma_match_round"
            ),
            # contested only means anything where the chain actually assigned a target
            (
                pl.col("mbs_contested").fill_null(value=False) & (pl.col("mbs_source") == "chain")
            ).alias("mbs_contested"),
            pl.col("fha_index").is_not_null().alias("in_fha"),
            pl.col("fhfa_record_number").is_not_null().alias("in_fhfa"),
            pl.col("mbs_loan_id").is_not_null().alias("in_mbs"),
            pl.col("umbs_loan_id").is_not_null().alias("in_umbs"),
            pl.col("fhlb_id").is_not_null().alias("in_fhlb"),
            pl.col("purchaser_hmdaindex").is_not_null().alias("in_seller_purchaser"),
            pl.col("_fha_cross_era").fill_null(value=False),
        )
        .with_columns(pl.col("gnma_loan_id").is_not_null().alias("in_gnma"))
        .drop("gnma_source_fha", "gnma_source_va_usda")
    )

    out = _apply_conflict_policy(out, policy).drop("_fha_cross_era")

    # channel is derived AFTER the policy, so a withheld link cannot still route a loan
    out = out.with_columns(
        pl.when(pl.col("in_fhfa") | pl.col("in_mbs"))
        .then(pl.lit("conventional"))
        .when(pl.col("in_fha") | pl.col("in_gnma"))
        .then(pl.lit("government"))
        .otherwise(pl.lit("other"))
        .alias("channel")
    )
    # A loan whose every link was withheld is kept, with all links null and its
    # conflict_flags set -- otherwise "a matcher reached this loan but no link is
    # assertable" would silently become "we never heard of this loan", and the
    # conflict record would vanish for exactly the loans the policy hit hardest.
    # Rows like this are all-flags-false with conflict_flags non-null, and keeping
    # them makes the spine total invariant to the policy.
    flags = Config.flag_columns()
    any_edge = pl.col(flags[0])
    for f in flags[1:]:
        any_edge = any_edge | pl.col(f)
    out = out.filter(any_edge | pl.col("conflict_flags").is_not_null())

    df = out.collect(engine="streaming")
    n_conf = int(df["conflict_flags"].is_not_null().sum())
    logger.info(
        "  %d: %d loans (%d conventional from the direct edge; %d carry a conflict record)",
        year,
        df.height,
        n_direct,
        n_conf,
    )
    return df


@log_execution_time
def build_master_crosswalk(
    min_year: int | None = None,
    max_year: int | None = None,
    *,
    overwrite: bool = False,
    policy: ConflictPolicy = "strict",
) -> dict[int, int]:
    """Build the master crosswalk into the private staging tree.

    Writing to staging rather than straight to ``crosswalk/master/`` is what
    lets `~.qc.run_qc` gate promotion: a build that violates an invariant
    never replaces a good artifact. Call `promote` once QC passes.

    Args:
        min_year: First activity year (defaults to ``Config.MIN_YEAR``).
        max_year: Last activity year (defaults to ``Config.MAX_YEAR``).
        overwrite: Rebuild years already present in staging.
        policy: ``strict`` (default) withholds any link another method
            contradicts; ``flag`` keeps the priority winner and only records the
            reason. See `_apply_conflict_policy`.

    Returns:
        Rows written per year (absent for years skipped or empty).

    Raises:
        FileNotFoundError: If a required input crosswalk is missing.
    """
    min_year = Config.MIN_YEAR if min_year is None else min_year
    max_year = Config.MAX_YEAR if max_year is None else max_year
    Config.ensure_directories()

    fatal, optional = _missing_inputs(min_year, max_year)
    if fatal:
        raise FileNotFoundError("Required input crosswalk(s) missing:\n  " + "\n  ".join(fatal))
    for note in optional:
        logger.warning("Optional input absent, edge will be empty: %s", note)

    # Always over every built year -- never the requested slice (see load_direct_edge).
    increment, contested = load_direct_edge()
    cross_era_fha = load_cross_era_fha()
    logger.info("Pre-2018 crosswalk claims %d FHA endorsements", cross_era_fha.height)

    written: dict[int, int] = {}
    for year in range(min_year, max_year + 1):
        part = Config.get_partition_dir(year, staging=True)
        if part.exists() and not overwrite:
            logger.info("%d: staging partition exists; skipping (use overwrite=True)", year)
            continue
        df = build_year(year, increment, contested, cross_era_fha, policy=policy)
        if df.is_empty():
            logger.info("  %d: no rows in any edge", year)
            continue
        clear_partitions(part.parent, [part])
        # include_key=True keeps activity_year INSIDE each file as declared Int32.
        # Without it the column exists only in the directory name, so it is
        # invisible to readers scanning with hive_partitioning=False and comes
        # back as path-inferred Int64 for everyone else.
        write_hive_partitioned(
            df.lazy(),
            Config.MASTER_STAGING_DIR,
            partition_cols=[Config.PARTITION_COL],
            include_key=True,
        )
        written[year] = df.height

    logger.info(
        "Staged %d loans across %d year(s) -> %s",
        sum(written.values()),
        len(written),
        Config.MASTER_STAGING_DIR,
    )
    return written


def promote(years: list[int] | None = None) -> list[int]:
    """Move staged partitions into ``crosswalk/master/``, partition by partition.

    Partition-scoped by design: only the year subtrees being promoted are
    replaced, so a single-year rebuild can never take out the whole artifact.

    Args:
        years: Years to promote (defaults to every year present in staging).

    Returns:
        The years promoted.

    Raises:
        FileNotFoundError: If staging holds nothing to promote.
    """
    Config.ensure_directories()
    staged = sorted(
        int(p.name.split("=")[1])
        for p in Config.MASTER_STAGING_DIR.glob(f"{Config.PARTITION_COL}=*")
        if p.is_dir()
    )
    if not staged:
        raise FileNotFoundError(f"Nothing staged under {Config.MASTER_STAGING_DIR}")
    targets = staged if years is None else [y for y in staged if y in years]

    # Staging and the crosswalk tree can sit on different filesystems, which turns a move
    # into a copy. Copy into a hidden sibling of the crosswalk root first, then swap it in
    # by rename, so neither a reader nor a sync client ever sees a half-copied partition
    # and the old partition survives until its replacement is complete.
    root = Config.MASTER_CROSSWALK_DIR
    for year in targets:
        src = Config.get_partition_dir(year, staging=True)
        dst = Config.get_partition_dir(year)
        tmp = root.parent / f".{root.name}_{dst.name}.tmp"
        old = root.parent / f".{root.name}_{dst.name}.old"
        for stale in (tmp, old):
            if stale.exists():
                shutil.rmtree(stale)
        shutil.copytree(src, tmp)
        replacing = dst.exists()
        if replacing:
            dst.rename(old)
        tmp.rename(dst)
        if replacing:
            shutil.rmtree(old)
        shutil.rmtree(src)
        logger.info("promoted %s=%d", Config.PARTITION_COL, year)
    logger.info("Promoted %d partition(s) -> %s", len(targets), Config.MASTER_CROSSWALK_DIR)
    return targets


def load_master_crosswalk(
    min_year: int | None = None,
    max_year: int | None = None,
    *,
    columns: list[str] | None = None,
    staging: bool = False,
) -> pl.LazyFrame:
    """Scan the master crosswalk.

    Args:
        min_year: Optional first activity year to keep.
        max_year: Optional last activity year to keep.
        columns: Optional column subset (``HMDAIndex`` is always included).
        staging: Read the staging tree instead of the promoted crosswalk.

    Returns:
        LazyFrame over the hive-partitioned artifact.

    Raises:
        FileNotFoundError: If the artifact has not been built.
    """
    base = Config.MASTER_STAGING_DIR if staging else Config.MASTER_CROSSWALK_DIR
    if not base.exists() or not any(base.glob(f"{Config.PARTITION_COL}=*")):
        raise FileNotFoundError(
            f"No master crosswalk at {base}. Run `mortgage-data match master build`."
        )
    lf = pl.scan_parquet(base, hive_partitioning=True)
    if min_year is not None:
        lf = lf.filter(pl.col(Config.PARTITION_COL) >= min_year)
    if max_year is not None:
        lf = lf.filter(pl.col(Config.PARTITION_COL) <= max_year)
    if columns is not None:
        keep = ["HMDAIndex", *[c for c in columns if c != "HMDAIndex"]]
        lf = lf.select(keep)
    return lf


def resolve_ownership_chain(*, ordered_only: bool = False) -> pl.LazyFrame:
    """Full ownership sequence per loan: originator -> each HMDA buyer -> the agency.

    Assembles the chain from the two edges that hold its parts. The HMDA-visible
    hops come from the seller/purchaser edge (``sp_chain``); the terminal agency
    comes from the conventional / GNMA edges, because Fannie, Freddie and Ginnie
    are not HMDA reporters and file no purchase records of their own.

    ``owners`` is the ordered list: the originating loan's own ``HMDAIndex``,
    then each purchase record, then a terminal agency token where one is known.
    ``chain_ordered`` says whether the intermediate hops' SEQUENCE is established
    (a single hop is trivially ordered); when false the owners are still all
    correct, only their order is open.

    Args:
        ordered_only: Keep only loans whose hop order is established.

    Returns:
        LazyFrame of HMDAIndex, activity_year, owners, n_owners, chain_ordered,
        terminal_agency.
    """
    lf = load_master_crosswalk().filter(pl.col("in_seller_purchaser"))
    if ordered_only:
        lf = lf.filter(pl.col("sp_chain_ordered"))
    terminal = (
        pl.when(pl.col("agency") == "fnma")
        .then(pl.lit("FNMA"))
        .when(pl.col("agency") == "fhlmc")
        .then(pl.lit("FHLMC"))
        .when(pl.col("in_gnma"))
        .then(pl.lit("GNMA"))
        .when(pl.col("fhfa_enterprise") == 1)
        .then(pl.lit("FNMA"))
        .when(pl.col("fhfa_enterprise") == 2)
        .then(pl.lit("FHLMC"))
        .otherwise(None)
        .alias("terminal_agency")
    )
    return (
        lf.with_columns(terminal)
        .with_columns(
            pl.concat_list(
                pl.col("HMDAIndex"),
                pl.col("sp_chain"),
                pl.col("terminal_agency").fill_null(""),
            )
            .list.eval(pl.element().filter(pl.element() != ""))
            .alias("owners")
        )
        .select(
            "HMDAIndex",
            Config.PARTITION_COL,
            "owners",
            pl.col("owners").list.len().alias("n_owners"),
            pl.col("sp_chain_ordered").alias("chain_ordered"),
            "terminal_agency",
        )
    )


def resolve_security(
    gse: Literal["fnma", "fhlmc", "either"] = "either",
    *,
    chain_only: bool = False,
) -> pl.LazyFrame:
    """Convenience view: HMDA loan -> its GSE security ids.

    Under the strict conflict policy every asserted ``in_mbs`` link is already
    uncontradicted, so this needs no contested filter. It remains useful
    for the one caveat that survives: ``in_mbs`` does not imply the chain, so
    chain-specific analysis must filter ``mbs_source``.

    Args:
        gse: Restrict to one enterprise, or ``either``.
        chain_only: Keep only chain-sourced rows (for chain-specific analysis).

    Returns:
        LazyFrame of HMDAIndex, activity_year, agency, mbs/umbs ids, source.
    """
    lf = load_master_crosswalk().filter(pl.col("in_mbs"))
    if gse != "either":
        lf = lf.filter(pl.col("agency") == gse)
    if chain_only:
        lf = lf.filter(pl.col("mbs_source") == "chain")
    return lf.select(
        "HMDAIndex",
        Config.PARTITION_COL,
        "agency",
        "mbs_loan_id",
        "umbs_loan_id",
        "mbs_source",
        "mbs_match_round",
    )
