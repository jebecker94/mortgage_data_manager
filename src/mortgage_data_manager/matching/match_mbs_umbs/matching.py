"""The four-round MBS<->UMBS matcher: one engine, one round table, two GSE specs.

Everything that differs between Fannie Mae and Freddie Mac is data on a `GSEMatchSpec`
(`FNMA_SPEC`, `FHLMC_SPEC`): the columns loaded from each side, the credit-score agreement
rule, and whether the UMBS side's loan-purpose notation needs mapping onto the MBS side's.
The rounds are in ``rounds.py``: each is 1:1 on its own, runs on the previous rounds'
leftovers, and is numbered by its position in `ROUNDS`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import atomic_output
from mortgage_data_manager.matching.match_mbs_umbs.config import MBSUMBSMatchingConfig as Config
from mortgage_data_manager.matching.match_mbs_umbs.metrics import (
    compute_run_metrics,
    count_input_rows,
    write_run_metrics,
)
from mortgage_data_manager.matching.match_mbs_umbs.rounds import ROUNDS, UmbsRound
from mortgage_data_manager.matching.match_mbs_umbs.utils import (
    apply_null_tolerance,
    build_rename_maps,
    compute_date_window,
    core_mismatch_count,
    drop_conflicting_duplicates,
    load_standardized_frame,
)
from mortgage_data_manager.matching.resolve import (
    assert_one_to_one,
    drop_ambiguous,
    unique_best,
)

logger = get_logger(__name__)

MBS_ID = "Loan Sequence Number (MBS)"
UMBS_ID = "Loan Sequence Number (UMBS)"

#: The exact join keys every round shares.
JOIN_KEYS: tuple[str, ...] = (
    "Loan Purpose",
    "Property State",
    "Mortgage Loan Amount",
    "Original Interest Rate",
    "First Payment Date",
)


@dataclass(frozen=True)
class GSEMatchSpec:
    """What differs between the Fannie Mae and Freddie Mac legs.

    Attributes:
        gse: ``"fnma"`` or ``"fhlmc"``; labels the metrics sidecar and the 1:1 assertion.
        match_columns: Standardized columns loaded from both sides via the GSE's
            column-mapping CSV.
        credit_score_condition: The credit-score agreement predicate over the joined
            frame, applied in every round that requires credit score.
        purpose_n_to_r: Map UMBS loan purpose ``N`` onto the MBS side's ``R`` after
            duplicate exclusion.
    """

    gse: str
    match_columns: tuple[str, ...]
    credit_score_condition: pl.Expr
    purpose_n_to_r: bool = False


FNMA_SPEC = GSEMatchSpec(
    gse="fnma",
    match_columns=(
        "Loan Sequence Number", "Amortization Type", "Credit Score 1", "Credit Score 2",
        "Channel", "Combined Loan-To-Value", "Debt-To-Income", "First Payment Date",
        "First Time Home Buyer Indicator", "Loan Purpose", "Loan Term", "Loan-To-Value",
        "Mortgage Insurance Percentage", "Mortgage Loan Amount", "Loan Size",
        "Number of Borrowers", "Number of Units", "Occupancy Status",
        "Original Interest Rate", "Property State", "Property Type", "Seller Name",
        "Servicer Name",
    ),
    # Credit Score 1 within 1, or Credit Score 2 (the co-borrower score, MBS-only, so no
    # suffix) exact.
    credit_score_condition=(
        ((pl.col("Credit Score 1 (MBS)") - pl.col("Credit Score 1 (UMBS)")).abs() <= 1)
        | (pl.col("Credit Score 2") == pl.col("Credit Score 1 (UMBS)"))
    ),
    # Fannie's UMBS side codes a no-cash-out refinance N where the MBS side codes R.
    purpose_n_to_r=True,
)  # fmt: skip

FHLMC_SPEC = GSEMatchSpec(
    gse="fhlmc",
    match_columns=(
        "Loan Sequence Number", "Amortization Type", "Credit Score 1", "Channel",
        "Combined Loan-To-Value", "Debt-To-Income", "First Payment Date",
        "First Time Home Buyer Indicator", "Loan Purpose", "Loan Term", "Loan-To-Value",
        "Mortgage Insurance Percentage", "Mortgage Loan Amount", "Number of Borrowers",
        "Number of Units", "Occupancy Status", "Original Interest Rate", "Property State",
        "Property Type",
    ),
    credit_score_condition=(
        (pl.col("Credit Score 1 (MBS)") - pl.col("Credit Score 1 (UMBS)")).abs() == 0
    ),
)  # fmt: skip


def _match_round(
    l_frame: pl.LazyFrame,
    r_frame: pl.LazyFrame,
    spec: GSEMatchSpec,
    rnd: UmbsRound,
    *,
    null_tolerant: bool,
) -> pl.LazyFrame:
    """One round: join on the exact keys, filter on the tolerances, resolve 1:1.

    Returns:
        The round's pairs, ``Loan Sequence Number (MBS)`` and ``(UMBS)``.
    """
    join_keys = list(JOIN_KEYS)
    merged = l_frame.join(r_frame, on=join_keys, how="inner", suffix=" (UMBS)")

    # Rename the LHS copies of shared non-key columns to "(MBS)".
    shared = set(l_frame.collect_schema().names()).intersection(r_frame.collect_schema().names())
    for k in join_keys:
        shared.discard(k)
    merged = merged.rename({name: f"{name} (MBS)" for name in shared})

    conditions = [
        (pl.col("Loan-To-Value (MBS)") - pl.col("Loan-To-Value (UMBS)")).abs() <= 1,
        (pl.col("Combined Loan-To-Value (MBS)") - pl.col("Combined Loan-To-Value (UMBS)")).abs()
        <= 1,
        (pl.col("Debt-To-Income (MBS)") - pl.col("Debt-To-Income (UMBS)")).abs() <= 1,
        (pl.col("Number of Borrowers (MBS)") - pl.col("Number of Borrowers (UMBS)")).abs() == 0,
        (pl.col("Number of Units (MBS)") - pl.col("Number of Units (UMBS)")).abs() == 0,
        (pl.col("Loan Term (MBS)") - pl.col("Loan Term (UMBS)")).abs() <= 6,
        # Exact matches
        pl.col("Channel (MBS)") == pl.col("Channel (UMBS)"),
        pl.col("Occupancy Status (MBS)") == pl.col("Occupancy Status (UMBS)"),
        pl.col("First Time Home Buyer Indicator (MBS)")
        == pl.col("First Time Home Buyer Indicator (UMBS)"),
        pl.col("Property Type (MBS)") == pl.col("Property Type (UMBS)"),
    ]
    if rnd.credit_score:
        conditions.append(spec.credit_score_condition)
    if rnd.mip:
        conditions.append(
            (
                pl.col("Mortgage Insurance Percentage (MBS)")
                - pl.col("Mortgage Insurance Percentage (UMBS)")
            ).abs()
            == 0
        )

    res = merged.filter(pl.all_horizontal(apply_null_tolerance(conditions, enabled=null_tolerant)))

    # Deduplicate matches. `unique_best` keeps only a strictly unique winner and drops
    # every tie outright, so neither branch breaks one.
    if rnd.exact_attribute:
        res = res.with_columns(core_mismatch_count().alias("_core_mismatch"))
        res = unique_best(res, "_core_mismatch", MBS_ID, UMBS_ID).filter(
            pl.col("_core_mismatch") == 0
        )
    else:
        res = drop_ambiguous(res, MBS_ID, UMBS_ID)

    return res.select([MBS_ID, UMBS_ID])


def _unmatched(
    lf1: pl.LazyFrame, lf2: pl.LazyFrame, matched: pl.DataFrame
) -> tuple[pl.LazyFrame, pl.LazyFrame]:
    """Both sides less the loans an earlier round already paired."""
    return (
        lf1.join(matched.lazy(), left_on="Loan Sequence Number", right_on=MBS_ID, how="anti"),
        lf2.join(matched.lazy(), left_on="Loan Sequence Number", right_on=UMBS_ID, how="anti"),
    )


#: Each GSE's spec, keyed by the ``agency`` name `run_matching` takes.
SPECS: dict[str, GSEMatchSpec] = {"fnma": FNMA_SPEC, "fhlmc": FHLMC_SPEC}


def run_matching(
    agency: Literal["fnma", "fhlmc"],
    *,
    snapshot: bool = True,
    harp: bool = True,
    null_tolerant: bool = True,
    exact_attribute_round: bool = True,
    max_year: int = 2099,
    max_month: int = 12,
    crosswalk_file: Path | None = None,
) -> pl.DataFrame:
    """Match one GSE's MBS loans to its UMBS loans and write the crosswalk.

    Every input path comes from the config. The UMBS side is the GSE's ILLD issuance
    disclosure, which begins ``UMBS_ISSUANCE_START`` (2019-06); the first monthly snapshot
    (FNM_MLLD / FU) adds every loan still outstanding at that date that ILLD lacks,
    including the 2019-H2 seam cohort, and opens the MBS window to pre-2019. For Fannie
    the MBS side is likewise two releases: Fannie publishes its HARP/Refi Plus population
    apart from the quarterly files, and without it roughly a million no-cash-out
    refinances that the UMBS side discloses cannot match. Freddie has no such release, so
    ``harp`` has no effect there.

    Both sides are dtype/encoding-harmonized at load (see ``inputs.py``); MIP nulls are
    normalized to 0.0, so the strict-MIP rounds 1, 3 and 4 cover uninsured loans too.

    Args:
        agency: ``"fnma"`` or ``"fhlmc"``.
        snapshot: Add the first monthly UMBS snapshot; False matches the post-2019 ILLD
            population only.
        harp: Add Fannie's HARP/Refi Plus issuances to the MBS side (FNMA only).
        null_tolerant: Let a tolerance/equality check pass when either side's value is
            missing. False is the null-vetoing baseline.
        exact_attribute_round: Run round 4. False is the three-round baseline.
        max_year: Last First Payment Date year to include.
        max_month: Last First Payment Date month of ``max_year`` to include.
        crosswalk_file: Output path; defaults to the production crosswalk. The metrics
            sidecar goes beside it.

    Returns:
        The crosswalk, ``Loan Sequence Number (MBS)`` and ``(UMBS)`` as strings, with
        ``match_round``.
    """
    spec = SPECS[agency]
    mbs_dir = Config.get_mbs_dir(agency)
    umbs_dir = Config.get_umbs_illd_dir(agency)
    snapshot_file = Config.get_umbs_snapshot_file(agency) if snapshot else None
    harp_file = Config.get_mbs_harp_file(agency) if harp else None
    crosswalk_file = crosswalk_file or Config.get_crosswalk_path(agency)
    crosswalk_file.parent.mkdir(parents=True, exist_ok=True)

    mbs_rename_map, umbs_rename_map = build_rename_maps(
        Config.get_column_mapping_file(agency), list(spec.match_columns)
    )
    logger.debug("UMBS rename map: %s", umbs_rename_map)
    logger.debug("MBS rename map: %s", mbs_rename_map)

    issuance_start, mbs_first_date, last_date = compute_date_window(
        max_year, max_month, snapshot_file
    )

    # Staleness guard: raw input-glob row counts, recorded in the metrics sidecar so a later
    # `check` can tell whether the inputs have grown since (mtimes cannot — see metrics.py).
    input_counts = count_input_rows(mbs_dir, umbs_dir, snapshot_file, harp_file)

    # MBS side (glob on directory); load_standardized_frame standardizes columns,
    # harmonizes dtypes/encodings (see inputs.py), and windows First Payment Date.
    lf1 = load_standardized_frame(
        str(mbs_dir / "**/*.parquet"), mbs_rename_map, mbs_first_date, last_date
    )
    # A second MBS release contributes every loan not already in the first. Fannie's HARP
    # and quarterly id sets are disjoint in the data, so the anti-join removes nothing
    # today; it keeps the universe one-row-per-loan if a HARP loan is ever restated into a
    # quarterly file, which would otherwise hand `drop_ambiguous` a self-collision.
    if harp_file is not None:
        lf1_harp = load_standardized_frame(
            str(harp_file), mbs_rename_map, mbs_first_date, last_date
        ).join(lf1.select("Loan Sequence Number"), on="Loan Sequence Number", how="anti")
        lf1 = pl.concat([lf1, lf1_harp], how="diagonal_relaxed")
        logger.info(
            "Combined MBS source: %s + HARP %s (loans not in the quarterly files)",
            mbs_dir.name,
            harp_file.name,
        )

    # UMBS issuance side: ILLD covers [issuance_start, last_date). Loan modifications
    # (Loan Purpose "M") are dropped from each source BEFORE the snapshot anti-join below:
    # a pre-2019 loan later modified re-enters Fannie's ILLD as a purpose-M reissue with a
    # NEW FPD >= 2019-06, and anti-joining against that shadow row would delete the loan's
    # only matchable (snapshot) representation from the universe. Freddie's mod reissues
    # live in a disjoint id namespace; the order is kept for both.
    lf2 = load_standardized_frame(
        str(umbs_dir / "**/*.parquet"), umbs_rename_map, issuance_start, last_date
    ).filter(pl.col("Loan Purpose") != "M")
    # The first monthly snapshot contributes every loan NOT already in ILLD (anti-join,
    # ILLD preferred): the pre-2019 population plus the 2019-H2 seam cohort (FPD >= 2019-06
    # in pools issued before June 2019, which never enter ILLD because FPD lags pool
    # issuance by 1-2 months).
    if snapshot_file is not None:
        lf2_snapshot = (
            load_standardized_frame(str(snapshot_file), umbs_rename_map, mbs_first_date, last_date)
            .filter(pl.col("Loan Purpose") != "M")
            .join(lf2.select("Loan Sequence Number"), on="Loan Sequence Number", how="anti")
        )
        lf2 = pl.concat([lf2, lf2_snapshot], how="diagonal_relaxed")
        logger.info(
            "Combined UMBS source: %s ILLD (>= %s) + snapshot %s (loans not in ILLD, all FPD)",
            spec.gse.upper(),
            issuance_start.date(),
            snapshot_file.name,
        )

    # One UMBS row per loan. Fannie's ILLD discloses some loans in two securities (Freddie's
    # carries no duplicate ids today); copies that agree on every compared column collapse,
    # copies that disagree are excluded.
    lf2, excluded = drop_conflicting_duplicates(lf2, "Loan Sequence Number")
    logger.info(
        "UMBS loans excluded for disagreeing duplicate disclosures: %s",
        f"{excluded.select(pl.len()).collect().item():,}",
    )

    if spec.purpose_n_to_r:
        lf2 = lf2.with_columns(
            pl.when(pl.col("Loan Purpose") == "N")
            .then(pl.lit("R"))
            .otherwise(pl.col("Loan Purpose"))
            .alias("Loan Purpose")
        )

    n_mbs = lf1.select(pl.len()).collect().item()
    n_umbs = lf2.select(pl.len()).collect().item()
    logger.info("Number of MBS observations: %s", f"{n_mbs:,}")
    logger.info("Number of UMBS observations: %s", f"{n_umbs:,}")

    counts: dict[str, int] = {}
    parts: list[pl.DataFrame] = []
    for number, rnd in enumerate(ROUNDS, start=1):
        if rnd.exact_attribute and not exact_attribute_round:
            counts[f"round{number}"] = 0
            logger.info("Round %d (%s) disabled", number, rnd.label)
            continue
        mbs, umbs = (lf1, lf2) if not parts else _unmatched(lf1, lf2, pl.concat(parts))
        pairs = _match_round(mbs, umbs, spec, rnd, null_tolerant=null_tolerant).collect()
        counts[f"round{number}"] = len(pairs)
        logger.info("Round %d matches (%s): %s", number, rnd.label, f"{len(pairs):,}")
        parts.append(pairs.with_columns(pl.lit(number, dtype=pl.UInt8).alias("match_round")))

    df_crosswalk = pl.concat(parts, how="vertical").with_columns(
        pl.col(MBS_ID).cast(pl.String), pl.col(UMBS_ID).cast(pl.String)
    )

    # Each round is 1:1 on its own and runs on the previous rounds' leftovers, so the
    # pooled crosswalk is 1:1 by construction — asserted rather than assumed, because an
    # overlap would mean two rounds claimed the same loan.
    assert_one_to_one(df_crosswalk, MBS_ID, UMBS_ID, context=f"{spec.gse} mbs_umbs crosswalk")
    logger.info("Number of matched observations: %s", f"{len(df_crosswalk):,}")

    with atomic_output(crosswalk_file) as tmp:
        df_crosswalk.write_parquet(tmp)

    # Drift + staleness guards: record input sizes, round composition, and by-vintage match
    # rates alongside the crosswalk. `mortgage-data match mbs-umbs check` compares them to
    # the pinned floors in expected_match_rates.json and re-counts the input globs to catch
    # a crosswalk left behind by inputs that grew without a newer file.
    metrics = compute_run_metrics(
        spec.gse,
        lf1,
        n_umbs,
        df_crosswalk,
        counts,
        input_counts=input_counts,
    )
    write_run_metrics(metrics, crosswalk_file)
    return df_crosswalk
