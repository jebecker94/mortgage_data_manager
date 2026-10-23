"""QC suite for the master crosswalk -- the gate on promoting a build.

Every check here is either a structural guarantee of the assembly (grain,
target exclusivity) or a cross-matcher consistency check that would catch a
regression in an upstream crosswalk. ``run_qc`` returns a structured result and
``passed`` decides whether `~.build.promote` may run.

Note on ``mbs_without_fhfa``: ``in_mbs`` is not a subset of ``in_fhfa``, because the
conventional edge includes a direct path that skips FHFA and its ``direct_no_link``
cell legitimately sits outside FHFA. The check is instead that ``mbs_without_fhfa``
equals the ``direct_no_link`` count EXACTLY, because every other conventional row
either chained (so its FHFA leg exists by construction) or was FHFA-corroborated. A
mismatch means an FHFA-corroborated row lost its FHFA link, or a no_link row
acquired one.
"""

from __future__ import annotations

import json
from typing import Any

import polars as pl

from mortgage_data_manager.core.logging import get_logger, log_execution_time
from mortgage_data_manager.matching.master.build import load_cross_era_fha, load_master_crosswalk
from mortgage_data_manager.matching.master.config import MasterCrosswalkConfig as Config

logger = get_logger(__name__)

#: Checks that must hold for a build to be promotable. Each maps to a key in
#: the QC result's ``invariants`` block and an expected value of 0 / True.
_HARD_ZERO = (
    "umbs_without_mbs",
    "agency_enterprise_mismatch",
    "gnma_master_overlap",
    "mbs_target_collisions",
    "umbs_target_collisions",
    "corroborated_without_fhfa",
    # under the strict policy no ASSERTED link may be one another method contradicts
    "contested_with_asserted_mbs",
    "gse_gnma_overlap",
    "gnma_disagree_asserted",
    "fha_cross_era_asserted",
    "orphan_rows",
    # ownership-chain integrity
    "ordered_chain_missing_first_hop",
    "unordered_chain_asserts_first_hop",
    "ordered_chain_ambiguous_first_hop",
    "sp_chain_length_mismatch",
    "sp_chain_duplicate_hop",
)
_HARD_TRUE = ("grain_ok", "mbs_without_fhfa_reconciles")


@log_execution_time
def run_qc(*, staging: bool = True, stamp: str | None = None) -> dict[str, Any]:
    """Run the invariant suite over a built artifact.

    Args:
        staging: QC the staging tree (the default, since this gates promotion).
            Set False to audit the promoted crosswalk.
        stamp: Run label for the persisted JSON; defaults to today.

    Returns:
        The QC result. ``result["passed"]`` is the promotion gate.
    """
    import datetime as dt

    stamp = stamp or dt.date.today().isoformat()
    lf = load_master_crosswalk(staging=staging)
    flags = Config.flag_columns()

    n, n_uniq = (
        lf.select(pl.len().alias("n"), pl.col("HMDAIndex").n_unique().alias("u")).collect().row(0)
    )
    coverage = lf.select([pl.col(c).sum().alias(c) for c in flags]).collect().to_dicts()[0]

    conventional = lf.filter(pl.col("in_mbs"))
    source_counts = (
        conventional.group_by("mbs_source")
        .agg(pl.len().alias("n"))
        .sort("mbs_source")
        .collect()
        .to_dicts()
    )
    by_source = {r["mbs_source"]: int(r["n"]) for r in source_counts}
    no_link = by_source.get("direct_no_link", 0)

    inv = (
        lf.select(
            (pl.col("in_mbs") & ~pl.col("in_fhfa")).sum().alias("mbs_without_fhfa"),
            (pl.col("in_umbs") & ~pl.col("in_mbs")).sum().alias("umbs_without_mbs"),
            (pl.col("in_mbs") & ~pl.col("in_umbs")).sum().alias("mbs_without_umbs"),
            (
                pl.col("agency").is_not_null()
                & pl.col("fhfa_enterprise").is_not_null()
                & (
                    ((pl.col("agency") == "fnma") & (pl.col("fhfa_enterprise") != 1))
                    | ((pl.col("agency") == "fhlmc") & (pl.col("fhfa_enterprise") != 2))
                )
            )
            .sum()
            .alias("agency_enterprise_mismatch"),
            (pl.col("agency").is_not_null() & pl.col("fhfa_enterprise").is_not_null())
            .sum()
            .alias("agency_enterprise_checked"),
            ((pl.col("in_fhfa") | pl.col("in_mbs")) & pl.col("in_gnma"))
            .sum()
            .alias("gse_gnma_overlap"),
            # not a conflict: the enterprises acquire FHA/VA/RHS loans (informational)
            ((pl.col("in_fhfa") | pl.col("in_mbs")) & pl.col("in_fha"))
            .sum()
            .alias("gse_acquired_fha"),
            # a contested loan must have NO conventional link asserted
            (pl.col("mbs_contested") & pl.col("in_mbs")).sum().alias("contested_with_asserted_mbs"),
            # the GNMA bridge and the masters must not both be asserted while disagreeing
            (
                pl.col("in_gnma")
                & pl.col("gnma_loan_id_fhlb").is_not_null()
                & pl.coalesce("gnma_loan_id_fha", "gnma_loan_id_va_usda").is_not_null()
                & (
                    pl.col("gnma_loan_id_fhlb").cast(pl.Utf8)
                    != pl.coalesce(
                        pl.col("gnma_loan_id_fha").cast(pl.Utf8),
                        pl.col("gnma_loan_id_va_usda").cast(pl.Utf8),
                    )
                )
            )
            .sum()
            .alias("gnma_disagree_asserted"),
            # ownership-chain integrity: the first-hop field must be populated
            # exactly when the chain's order is known, never arbitrarily
            (pl.col("sp_chain_ordered") & pl.col("purchaser_hmdaindex").is_null())
            .sum()
            .alias("ordered_chain_missing_first_hop"),
            (
                ~pl.col("sp_chain_ordered").fill_null(value=True)
                & pl.col("purchaser_hmdaindex").is_not_null()
            )
            .sum()
            .alias("unordered_chain_asserts_first_hop"),
            # the first hop is only determined for one or two hops (see _edge_seller_purchaser)
            (pl.col("sp_chain_ordered") & (pl.col("sp_chain_n") > 2))
            .sum()
            .alias("ordered_chain_ambiguous_first_hop"),
            (
                pl.col("in_seller_purchaser")
                & (pl.col("sp_chain").list.len() != pl.col("sp_chain_n"))
            )
            .sum()
            .alias("sp_chain_length_mismatch"),
            (
                pl.col("in_seller_purchaser")
                & (pl.col("sp_chain").list.n_unique() != pl.col("sp_chain_n"))
            )
            .sum()
            .alias("sp_chain_duplicate_hop"),
            pl.col("conflict_flags").is_not_null().sum().alias("conflict_records"),
            # no orphan rows: every row is either in an edge or carries a conflict record
            (~pl.any_horizontal([pl.col(f) for f in flags]) & pl.col("conflict_flags").is_null())
            .sum()
            .alias("orphan_rows"),
            # loans present only as a withheld-everything record
            (
                ~pl.any_horizontal([pl.col(f) for f in flags])
                & pl.col("conflict_flags").is_not_null()
            )
            .sum()
            .alias("all_links_withheld"),
            (
                pl.col("gnma_loan_id_fha").is_not_null()
                & pl.col("gnma_loan_id_va_usda").is_not_null()
            )
            .sum()
            .alias("gnma_master_overlap"),
            pl.col("mbs_contested").sum().alias("mbs_contested"),
            # `corroborated` means the FHFA pin named the same enterprise, so the
            # FHFA link must exist. Falsifiable, unlike a structural tautology.
            ((pl.col("mbs_source") == "direct_corroborated") & ~pl.col("in_fhfa"))
            .sum()
            .alias("corroborated_without_fhfa"),
        )
        .collect()
        .to_dicts()[0]
    )
    inv = {k: int(v) for k, v in inv.items()}

    # no FHA link may be asserted on an endorsement the pre-2018 crosswalk also claims
    inv["fha_cross_era_asserted"] = int(
        lf.filter(pl.col("in_fha"))
        .join(load_cross_era_fha().lazy(), on="fha_index", how="semi")
        .select(pl.len())
        .collect()
        .item()
    )

    # target exclusivity, pooled across every partition (the upstream matcher
    # only guarantees this within an activity year)
    excl = (
        conventional.select(
            pl.col("mbs_loan_id").len().alias("mbs_rows"),
            pl.col("mbs_loan_id").n_unique().alias("mbs_unique"),
            pl.col("umbs_loan_id").drop_nulls().len().alias("umbs_rows"),
            pl.col("umbs_loan_id").drop_nulls().n_unique().alias("umbs_unique"),
        )
        .collect()
        .to_dicts()[0]
    )
    inv["mbs_target_collisions"] = int(excl["mbs_rows"]) - int(excl["mbs_unique"])
    inv["umbs_target_collisions"] = int(excl["umbs_rows"]) - int(excl["umbs_unique"])

    channel = {
        r["channel"]: int(r["n"])
        for r in lf.group_by("channel")
        .agg(pl.len().alias("n"))
        .sort("channel")
        .collect()
        .to_dicts()
    }
    gnma_source = {
        r["gnma_source"]: int(r["n"])
        for r in lf.filter(pl.col("in_gnma"))
        .group_by("gnma_source")
        .agg(pl.len().alias("n"))
        .sort("gnma_source")
        .collect()
        .to_dicts()
    }
    per_year = (
        lf.group_by(Config.PARTITION_COL)
        .agg(pl.len().alias("spine"), *[pl.col(c).sum() for c in flags])
        .sort(Config.PARTITION_COL)
        .collect()
    )
    # ownership chain: how deep, and how often the order is recoverable
    sp = lf.filter(pl.col("in_seller_purchaser"))
    chain_stats: dict[str, Any] = {
        "loans_with_chain": int(sp.select(pl.len()).collect().item()),
        "by_hops": {
            str(r["sp_chain_n"]): int(r["n"])
            for r in sp.group_by("sp_chain_n")
            .agg(pl.len().alias("n"))
            .sort("sp_chain_n")
            .collect()
            .to_dicts()
        },
        "multi_hop": int(sp.filter(pl.col("sp_chain_n") > 1).select(pl.len()).collect().item()),
        "multi_hop_ordered": int(
            sp.filter((pl.col("sp_chain_n") > 1) & pl.col("sp_chain_ordered"))
            .select(pl.len())
            .collect()
            .item()
        ),
    }
    multi = chain_stats["multi_hop"]
    chain_stats["multi_hop_ordered_pct"] = (
        round(chain_stats["multi_hop_ordered"] / multi * 100, 2) if multi else None
    )
    # Independent check on the ordering rule: the last HMDA-visible hop reports the
    # agency it sold to, and the spine's own conventional/GNMA edge should name the
    # same one. Informational, not a gate -- it depends on the agency edge's own
    # coverage -- but a drop here means the ordering inference has regressed.
    terminal = (
        sp.filter((pl.col("sp_chain_n") > 1) & pl.col("sp_chain_ordered"))
        .select(
            pl.when(pl.col("agency") == "fnma")
            .then(pl.lit("fnma"))
            .when(pl.col("agency") == "fhlmc")
            .then(pl.lit("fhlmc"))
            .when(pl.col("in_gnma"))
            .then(pl.lit("gnma"))
            .otherwise(None)
            .alias("spine_agency")
        )
        .group_by("spine_agency")
        .agg(pl.len().alias("n"))
        .collect()
        .to_dicts()
    )
    chain_stats["ordered_terminal_agency_on_spine"] = {
        str(r["spine_agency"]): int(r["n"]) for r in terminal
    }

    # what the strict policy withheld, by reason (a loan can carry more than one)
    withheld = {
        reason: int(
            lf.filter(pl.col("conflict_flags").str.contains(reason))
            .select(pl.len())
            .collect()
            .item()
        )
        for reason in ("mbs_contested", "gse_gnma", "gnma_disagree", "fha_cross_era")
    }

    result: dict[str, Any] = {
        "stamp": stamp,
        "source": "staging" if staging else "crosswalk",
        "total_rows": int(n),
        "unique_hmdaindex": int(n_uniq),
        "grain_ok": bool(n == n_uniq),
        "coverage": {k: int(v) for k, v in coverage.items()},
        "mbs_source": by_source,
        "invariants": inv,
        "mbs_without_fhfa_reconciles": bool(inv["mbs_without_fhfa"] == no_link),
        "channel": channel,
        "gnma_source": gnma_source,
        "conflicts_withheld": withheld,
        "ownership_chain": chain_stats,
        "per_year": per_year.to_dicts(),
    }

    failures = [k for k in _HARD_ZERO if inv.get(k, 0) != 0]
    failures += [k for k in _HARD_TRUE if not result.get(k, False)]
    result["failures"] = failures
    result["passed"] = not failures

    Config.MASTER_QC_DIR.mkdir(parents=True, exist_ok=True)
    Config.get_qc_path(stamp).write_text(json.dumps(result, indent=2, default=str))

    logger.info(
        "QC %s: %d rows, grain %s, mbs_without_fhfa %d vs direct_no_link %d (%s)",
        "PASS" if result["passed"] else "FAIL",
        result["total_rows"],
        "OK" if result["grain_ok"] else "FAIL",
        inv["mbs_without_fhfa"],
        no_link,
        "reconciles" if result["mbs_without_fhfa_reconciles"] else "MISMATCH",
    )
    if failures:
        logger.error("QC failures: %s", failures)
    return result


def latest_qc(*, staging: bool = True) -> dict[str, Any] | None:
    """Load the most recent QC result for the given source, if any.

    Args:
        staging: Match results recorded against staging rather than the crosswalk.

    Returns:
        The newest matching QC result, or None.
    """
    if not Config.MASTER_QC_DIR.exists():
        return None
    want = "staging" if staging else "crosswalk"
    for path in sorted(Config.MASTER_QC_DIR.glob("master_qc_*.json"), reverse=True):
        try:
            payload: dict[str, Any] = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("source") == want:
            return payload
    return None
