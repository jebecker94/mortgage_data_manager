"""The MBS<->FHFA matching engine, shared by both enterprises.

Matches a prepared enterprise loan-level pool (``inputs.py``) against FHFA's
single-family census-tract file (``sf_c``), which this module loads and prepares
itself, and writes one crosswalk per acquisition year. Which enterprise is being
matched is carried by a `~.spec.GseSpec`; nothing here branches on it.

One pass joins on the exact keys (categoricals plus the pre-binned amount and
term), filters the candidates under the numeric tolerances, scores them, and
resolves them one-to-one by mutual best score. A round (``rounds.py``) runs that
pass, in its term phases, once per leg (an FHFA origination-timing flag and the
origination years offered to it), once or until its leftovers stop yielding
pairs, and gates what it finds. `match_year` runs the enterprise's rounds in
order, each on what the earlier ones left.

Three conventions carry the design:

* **The canonical schema.** Both preparers emit the same columns; the loan key is
  ``loan_id`` and the loan-level origination year ``origination_year``, renamed to
  the enterprise's crosswalk key only in `crosswalk`.
* **Amount binning.** Both sides round the loan amount to the FHFA $10k bin
  midpoint; the loan side carries it as ``loan_amount_rounded`` alongside the
  original amount, and duplicates loans sitting exactly on a boundary into both
  adjacent bins. Keeping the original amount is what lets the amount score term be
  an option rather than a fork.
* **The score.** `deviation_score` sums tolerance-normalised absolute
  deviations over the spec's fields in the spec's order. Field order is
  load-bearing: floating-point addition is not associative, and the sum decides
  ties. A field the spec marks as carrying no pair information
  (``non_evidence_score_fields``) still enters the sum but may not settle an FHFA
  record: `match_pass` flags each pair with whether the evidence-bearing fields
  settled it on their own, and `admitted` drops the rest on the way out.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import atomic_output
from mortgage_data_manager.core.reference.census_tract import tract_geoid
from mortgage_data_manager.core.reference.tract_zip3 import (
    census_year_to_vintage_expr,
    load_dominant_tract_zip3,
)
from mortgage_data_manager.matching.match_fhfa_mbs.config import FHFAMBSMatchingConfig as Config
from mortgage_data_manager.matching.match_fhfa_mbs.inputs import SPECS
from mortgage_data_manager.matching.match_fhfa_mbs.recovery import (
    iterate_to_fixpoint,
    recovery_gate,
    remove_claimed,
    resolve_cross_year,
    settle,
    tag_round,
    without_msa,
)
from mortgage_data_manager.matching.match_fhfa_mbs.rounds import Round
from mortgage_data_manager.matching.match_fhfa_mbs.spec import (
    FHFA_ID,
    LOAN_ID,
    SCORE_FIELDS,
    GseSpec,
    MatchingTolerances,
    MatchOptions,
)
from mortgage_data_manager.matching.resolve import drop_ambiguous, mutual_best

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Shared expressions
# ---------------------------------------------------------------------------


def expand_bin_edges(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Duplicate boundary-amount loans into both adjacent bins.

    An amount at an exact $10k multiple could reasonably have been binned either
    way, so it is offered to the join twice. Non-boundary loans pass through.

    Args:
        lf: The frame to transform.

    Returns:
        The pool with boundary loans duplicated into both adjacent bins.
    """
    edge = pl.col("_is_bin_edge")
    return pl.concat(
        [
            lf.filter(~edge),
            lf.filter(edge).with_columns(
                (pl.col("loan_amount_rounded") - 10000).alias("loan_amount_rounded")
            ),
            lf.filter(edge),
        ],
        how="diagonal_relaxed",
    )


def build_dti_match_expr(
    gse_col: str = "dti",
    fhfa_col: str = "dti_fhfa",
    tolerance: float = 2.0,
) -> pl.Expr:
    """Bin-aware DTI agreement between a loan-level DTI and FHFA's coded DTI.

    FHFA's DTI dictionary: 10 = under 20%, 20 = 20-29%, 30 = 30-35%, the exact
    value from 36 to 49%, 50 = 50-60%, 60 = over 60%, 99 = not available. For the
    binned ranges the loan-level DTI must fall in the bin, or within ``tolerance``
    of one of its edges, in which case the adjacent bin is accepted too. In the
    exact range the two must agree within ``tolerance``. Null on either side and
    FHFA's 99 always pass.

    Args:
        gse_col: Loan-level DTI column.
        fhfa_col: FHFA DTI column.
        tolerance: Slack at bin edges and within the exact range.

    Returns:
        Boolean expression, True where the DTIs agree.
    """
    gse = pl.col(gse_col)
    fhfa = pl.col(fhfa_col)
    null_ok = gse.is_null() | fhfa.is_null() | (fhfa == 99)

    exact_range = (gse >= 36) & (gse <= 49)
    exact_match = ((gse - fhfa).abs() <= tolerance) | ((fhfa == 30) & (gse < 36 + tolerance))

    bin_10 = gse < 20
    match_10 = (fhfa == 10) | ((fhfa == 20) & (gse >= 20 - tolerance))

    bin_20 = (gse >= 20) & (gse < 30)
    match_20 = (
        (fhfa == 20)
        | ((fhfa == 10) & (gse < 20 + tolerance))
        | ((fhfa == 30) & (gse >= 30 - tolerance))
    )

    bin_30 = (gse >= 30) & (gse < 36)
    match_30 = (
        (fhfa == 30)
        | ((fhfa == 20) & (gse < 30 + tolerance))
        | (((gse - fhfa).abs() <= tolerance) & (fhfa >= 36) & (fhfa <= 49))
    )

    bin_50 = (gse >= 50) & (gse <= 60)
    match_50 = (
        (fhfa == 50)
        | (((gse - fhfa).abs() <= tolerance) & (fhfa >= 36) & (fhfa <= 49))
        | ((fhfa == 60) & (gse >= 60 - tolerance))
    )

    bin_60 = gse > 60
    match_60 = (fhfa == 60) | ((fhfa == 50) & (gse < 60 + tolerance))

    return null_ok | (
        (exact_range & exact_match)
        | (bin_10 & match_10)
        | (bin_20 & match_20)
        | (bin_30 & match_30)
        | (bin_50 & match_50)
        | (bin_60 & match_60)
    )


def deviation_score(fields: Sequence[str], tolerances: MatchingTolerances) -> pl.Expr:
    """Sum of tolerance-normalised absolute deviations, lower being a better match.

    Each field contributes ``|gse - fhfa| / tolerance``; fields whose absence is not
    evidence against a pair (LTV, DTI) contribute 0 when either side is null. The
    ZIP3 penalty is added last, as a fixed cost on a disagreement rather than a
    normalised deviation -- it exists to split ties, not to rank.

    Args:
        fields: Score fields in summation order.
        tolerances: Supplies each field's weight and the ZIP3 penalty.

    Returns:
        The score expression.
    """
    score: pl.Expr | None = None
    for name in fields:
        _, null_is_zero = SCORE_FIELDS[name]
        term = (pl.col(name) - pl.col(f"{name}_fhfa")).abs() * tolerances.weight(name)
        if null_is_zero:
            term = term.fill_null(0)
        score = term if score is None else score + term
    if score is None:
        raise ValueError("a match score needs at least one field")
    if tolerances.zip3_penalty > 0:
        disagrees = (
            pl.col("zip3").is_not_null()
            & pl.col("zip3_fhfa").is_not_null()
            & (pl.col("zip3") != pl.col("zip3_fhfa"))
        )
        score = score + disagrees.cast(pl.Float64) * tolerances.zip3_penalty
    return score


def uniquely_best(score: pl.Expr, keys: Sequence[str]) -> pl.Expr:
    """True where ``score`` is the strict minimum within the row's ``keys`` group.

    Ranked ``method="max"``, which gives every row of a tie the highest rank the tie
    spans: a group whose minimum is attained twice has no row at rank 1 at all, so a
    tie decides nothing rather than being broken.

    Args:
        score: The score expression, lower being better.
        keys: Columns identifying the group the row competes in.

    Returns:
        A boolean expression, true only on a group's sole best-scoring row.
    """
    return score.rank(method="max").over(keys) == 1


#: Sidecar column: did the evidence-bearing score fields settle this pair's FHFA
#: record on their own, and settle it the same way the whole score did? Written by
#: `match_pass` for a spec with ``non_evidence_score_fields``, read by `admitted`.
EVIDENCE_DECIDED = "_evidence_decided"

#: Columns `crosswalk` carries past the written schema, for the steps that run between
#: matching and writing: ``fpd_year`` feeds cross-year resolution, `EVIDENCE_DECIDED`
#: the evidence rule. `admitted` cuts both.
SIDECAR_COLUMNS: tuple[str, ...] = ("fpd_year", EVIDENCE_DECIDED)


def admitted(pairs: pl.DataFrame) -> pl.DataFrame:
    """The rows a crosswalk is written from: evidence-decided pairs, sidecar columns cut.

    Where the spec marks a score field as carrying no pair information,
    `match_pass` flags every pair with whether the evidence-bearing fields settled its
    FHFA record on their own. A pair they did not is dropped here rather than written:
    the record goes unmatched, exactly as it would on any other tie, instead of being
    assigned on a quantity that does not distinguish a true pair from a false one.

    The drop is deliberately the last step. Every round and both resolutions run on the
    full pair set, so a dropped pair still claimed its loan and its record all the way
    through, and removing it cannot hand either of them to a different partner: the
    written crosswalk is the crosswalk the unmodified matcher produces, minus these
    rows and nothing else.

    Args:
        pairs: One year's crosswalk columns plus the sidecars, from `crosswalk`.

    Returns:
        The rows to write, in the crosswalk's column order.
    """
    if EVIDENCE_DECIDED in pairs.columns:
        pairs = pairs.filter(pl.col(EVIDENCE_DECIDED))
    return pairs.drop(SIDECAR_COLUMNS, strict=False)


def quality_tier(options: MatchOptions) -> pl.Expr:
    """Match-quality tier from the rate, amount and LTV deviations, 1 being best.

    The ladder is: rate near-exact plus (optionally) the amount on the bin midpoint
    plus LTV exact, then rate near-exact plus LTV exact, then rate near-exact, then
    everything else. Enabling the amount step shifts the rest down by one, which is
    why Fannie's tiers run 1-3 and Freddie's 1-4.

    "Rate near-exact" is convention-aware: a rate within ``tier_rate_epsilon`` OR one
    that FHFA has truncated to hundredths (``_rate_conv_ok``, set by
    `match_pass`). From the 2019 release FHFA reports both enterprises' rates
    truncated to two decimals (4.125 -> 4.12), so without this every eighth-point
    rate -- about half of all loans -- would fall out of tier 1 for a reporting
    change on the FHFA side, not a weaker match.

    Args:
        options: The match options carrying the tier tolerances.

    Returns:
        An expression yielding the match-quality tier, 1 being best.
    """
    rate_ok = (pl.col("_rate_diff") <= options.tier_rate_epsilon) | pl.col("_rate_conv_ok")
    ltv_exact = pl.col("_ltv_diff") == 0
    conditions = (
        [rate_ok & (pl.col("_amount_diff") == 0) & ltv_exact] if options.tier_on_amount else []
    )
    conditions += [rate_ok & ltv_exact, rate_ok]

    # Build the when/then ladder from the bottom rung up so tier 1 is tested first.
    tier = pl.lit(len(conditions) + 1, dtype=pl.Int32)
    for n, condition in reversed(list(enumerate(conditions, start=1))):
        tier = pl.when(condition).then(n).otherwise(tier)
    return tier


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

#: Join keys. Every one is a categorical or pre-binned field, so the join is exact
#: and the tolerance work happens on the (much smaller) candidate frame.
EXACT_KEYS: list[str] = [
    "state_fips",
    "msa",
    "channel",
    "num_units",
    "num_borrowers",
    "fthb",
    "loan_purpose",
    "occupancy_code",
    "property_type",
    "term",
    "loan_amount_rounded",
]


def match_pass(
    gse_lf: pl.LazyFrame,
    fhfa_lf: pl.LazyFrame,
    spec: GseSpec,
    *,
    keys: Sequence[str] = EXACT_KEYS,
    record_key: str | Sequence[str] = FHFA_ID,
) -> pl.LazyFrame:
    """One matching pass: join on the exact keys, filter, score, resolve 1:1.

    Args:
        gse_lf: Prepared loan-level pool.
        fhfa_lf: Prepared FHFA pool.
        spec: The enterprise being matched.
        keys: Join keys; both sides must carry them.
        record_key: What identifies an FHFA record in the resolution. The record id
            alone within one acquisition year; with ``year`` when a pass pools years,
            since FHFA record numbers repeat across years.

    Returns:
        Pairs that are one-to-one on both keys, carrying both sides' columns, a
        ``match_quality_tier``, the ``zip3_consistent`` QA flag, and — for a spec with
        ``non_evidence_score_fields`` — the `EVIDENCE_DECIDED` sidecar.
    """
    tolerances, options = spec.tolerances, spec.options
    record_keys = [record_key] if isinstance(record_key, str) else list(record_key)

    merged = expand_bin_edges(gse_lf).join(
        fhfa_lf.drop("orig_year_flag", strict=False).with_columns(
            pl.col("loan_amount").alias("loan_amount_rounded")
        ),
        on=list(keys),
        how="inner",
        suffix="_fhfa",
    )

    merged = merged.filter(
        (pl.col("interest_rate") - pl.col("interest_rate_fhfa")).abs() <= tolerances.rate_tolerance
    )
    merged = merged.filter(
        pl.col("ltv").is_null()
        | pl.col("ltv_fhfa").is_null()
        | ((pl.col("ltv") - pl.col("ltv_fhfa")).abs() <= tolerances.ltv_tolerance)
    )
    merged = merged.filter(build_dti_match_expr("dti", "dti_fhfa", tolerances.dti_tolerance))

    merged = merged.with_columns(
        deviation_score(options.score_fields, tolerances).alias("_match_score")
    )

    # A score field that carries no pair information may rank candidates but may not
    # settle a record. Both rankings are judged over the whole candidate frame, before
    # any resolution, so the flag says what the record's contest looked like rather
    # than what survived it.
    if options.non_evidence_score_fields:
        evidence = deviation_score(options.evidence_score_fields(), tolerances)
        merged = merged.with_columns(
            (
                uniquely_best(pl.col("_match_score"), record_keys)
                & uniquely_best(evidence, record_keys)
            ).alias(EVIDENCE_DECIDED)
        )

    # Mutual best, then 1:1 -- a score tie leaves both rows ranked first, so the
    # pair is dropped rather than picked. Both rules read values only, never row
    # order, so the result does not depend on how the join happened to order rows.
    pairs = drop_ambiguous(
        mutual_best(merged, "_match_score", LOAN_ID, record_key), LOAN_ID, record_key
    )

    pairs = pairs.with_columns(
        (pl.col("interest_rate") - pl.col("interest_rate_fhfa")).abs().alias("_rate_diff"),
        (pl.col("loan_amount") - pl.col("loan_amount_fhfa")).abs().alias("_amount_diff"),
        (pl.col("ltv") - pl.col("ltv_fhfa")).abs().fill_null(0).alias("_ltv_diff"),
        # sf_c stores the rate as Float32, so compare with slack rather than equality
        (
            ((pl.col("interest_rate") * 100).floor() / 100 - pl.col("interest_rate_fhfa")).abs()
            < 5e-4
        ).alias("_rate_conv_ok"),
    )
    pairs = pairs.with_columns(
        quality_tier(options).alias("match_quality_tier"),
        pl.when(pl.col("zip3").is_not_null() & pl.col("zip3_fhfa").is_not_null())
        .then(pl.col("zip3") == pl.col("zip3_fhfa"))
        .otherwise(None)
        .alias("zip3_consistent"),
    )
    return pairs.drop(
        "_rate_diff",
        "_rate_conv_ok",
        "_amount_diff",
        "_ltv_diff",
        "_match_score",
        "_is_bin_edge",
        "loan_amount_rounded",
    )


def match_terms(gse: pl.DataFrame, fhfa: pl.DataFrame, spec: GseSpec, term: str) -> pl.DataFrame:
    """One pass in a round's term phases (`Round.term`).

    The exact phase joins on the term as reported. The envelope phase re-offers the
    loans the exact phase left whose term the spec's ``term_envelope`` up-bins, at the
    security-product term, against the records it left. Running it on leftovers lets
    exact-term pairings claim records first.

    Args:
        gse: Prepared loan-level pool.
        fhfa: Prepared FHFA pool.
        spec: The enterprise being matched.
        term: ``"exact"``, ``"envelope"`` or ``"both"``.

    Returns:
        The pass's pairs, one-to-one.
    """
    envelope = spec.options.term_envelope
    phases: list[pl.DataFrame] = []
    if term in ("exact", "both"):
        exact = match_pass(gse.lazy(), fhfa.lazy(), spec).collect()
        phases.append(exact)
        gse, fhfa = remove_claimed(gse, fhfa, exact, LOAN_ID)
    if term in ("envelope", "both") and envelope is not None:
        upbinned = gse.filter(pl.col("term").is_in(list(envelope))).with_columns(
            pl.col("term").replace_strict(envelope, default=pl.col("term"))
        )
        phases.append(match_pass(upbinned.lazy(), fhfa.lazy(), spec).collect())
    return pl.concat(phases, how="diagonal_relaxed")


def match_round(
    rnd: Round,
    gse: pl.DataFrame,
    fhfa: pl.DataFrame,
    year: int,
    spec: GseSpec,
    filtered: pl.DataFrame | None,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Run one round on the pools earlier rounds left.

    Each leg offers its origination years to its FHFA flag's records. Every pair the
    round finds leaves both pools; only those passing the round's gate are kept.

    Args:
        rnd: The round.
        gse: Loan-level pool earlier rounds left.
        fhfa: FHFA records earlier rounds left.
        year: The FHFA acquisition year.
        spec: The enterprise being matched.
        filtered: ``loan_id`` of the loans the spec's pool filter keeps, or None where
            no round of the spec filters its pool.

    Returns:
        The accepted pairs and the two leftover pools.

    Raises:
        ValueError: If two legs share an FHFA flag, or the round filters its pool and
            the spec has no filter.
    """
    flags = [leg.fhfa_flag for leg in rnd.legs]
    if len(set(flags)) != len(flags):
        raise ValueError(f"Round {rnd.description!r} offers one FHFA flag twice")
    offered = gse
    if rnd.pool_filter:
        if filtered is None:
            raise ValueError(f"Round {rnd.description!r} filters a pool; {spec.name} has no filter")
        offered = gse.join(filtered, on=LOAN_ID, how="semi")

    def match(loans: pl.DataFrame, records: pl.DataFrame) -> pl.DataFrame:
        return match_terms(loans, records, spec, rnd.term)

    origination = pl.col("origination_year")
    found: list[pl.DataFrame] = []
    for leg in rnd.legs:
        loans = offered.filter(
            origination <= year - leg.min_lag
            if leg.max_lag is None
            else origination.is_between(year - leg.max_lag, year - leg.min_lag)
        )
        records = fhfa.filter(pl.col("orig_year_flag") == leg.fhfa_flag)
        if not rnd.msa:
            loans, records = without_msa(loans), without_msa(records)
        if rnd.occupancy_recode:
            loans = loans.filter(pl.col("occupancy_code") == 1).with_columns(
                pl.lit(3, dtype=pl.Int64).alias("occupancy_code")
            )
            records = records.filter(pl.col("occupancy_code") == 3)
        found.append(
            match(loans, records)
            if rnd.max_passes == 1
            else iterate_to_fixpoint(match, loans, records, LOAN_ID, max_iter=rnd.max_passes)
        )
    pairs = pl.concat(found, how="diagonal_relaxed")
    accepted, gse, fhfa = settle(
        gse, fhfa, pairs, LOAN_ID, recovery_gate(rnd.require_zip3, rnd.max_tier)
    )
    logger.info(
        "  %s: %s pairs (%s rejected by the gate)",
        rnd.description,
        f"{len(accepted):,}",
        f"{len(pairs) - len(accepted):,}",
    )
    return accepted, gse, fhfa


def match_year(year: int, spec: GseSpec, overwrite: bool = False) -> pl.DataFrame:
    """Match one FHFA acquisition year: the enterprise's rounds, in order.

    Args:
        year: The FHFA acquisition year.
        spec: The enterprise being matched.
        overwrite: Rebuild any cache the spec's pool filter keeps.

    Returns:
        Matched pairs with every matched column, ``match_round`` set to each round's
        position in ``spec.rounds``.

    Raises:
        ValueError: If a leg reaches past the origination cohorts the spec loads.
    """
    logger.info("%s-FHFA matching for %d", spec.name.upper(), year)
    tolerances = spec.tolerances
    logger.info(
        "Tolerances: rate +/-%s%%, amount bin-rounded, LTV +/-%s%%, DTI +/-%s%%",
        tolerances.rate_tolerance,
        tolerances.ltv_tolerance,
        tolerances.dti_tolerance,
    )
    depth = spec.options.prior_year_cohorts
    for rnd in spec.rounds:
        if any(leg.max_lag is not None and leg.max_lag > depth for leg in rnd.legs):
            raise ValueError(
                f"Round {rnd.description!r} reaches past the {depth} cohorts {spec.name} loads"
            )

    # The rounds re-filter the pools many times; scan the sources once.
    fhfa = prepare_fhfa(load_fhfa(year, spec), spec).collect()
    gse = spec.prepare(spec.load(year, depth)).collect()
    pool_filter = spec.options.pool_filter
    filtered = (
        pool_filter(gse, year, overwrite).select(LOAN_ID)
        if pool_filter is not None and any(rnd.pool_filter for rnd in spec.rounds)
        else None
    )
    acquired = gse.filter(pl.col("origination_year").is_in([year, year - 1])).height
    fhfa_count = fhfa.height

    rounds: list[pl.DataFrame] = []
    for number, rnd in enumerate(spec.rounds, start=1):
        logger.info("Round %d", number)
        accepted, gse, fhfa = match_round(rnd, gse, fhfa, year, spec, filtered)
        rounds.append(tag_round(accepted, number))
    matches = pl.concat(rounds, how="diagonal_relaxed")

    logger.info(
        "Results: %s records (%d + %d): %s | FHFA records: %s | matches: %s",
        spec.name.upper(),
        year,
        year - 1,
        f"{acquired:,}",
        f"{fhfa_count:,}",
        f"{len(matches):,}",
    )
    for row in (
        matches.group_by("match_quality_tier")
        .agg(pl.len().alias("count"))
        .sort("match_quality_tier")
        .iter_rows(named=True)
    ):
        logger.info("  Tier %s: %s", row["match_quality_tier"], f"{row['count']:,}")
    return matches


def crosswalk(matches: pl.DataFrame, spec: GseSpec) -> pl.DataFrame:
    """Cut the crosswalk columns, plus the sidecars the steps before writing read.

    Args:
        matches: The matched pairs.
        spec: The enterprise spec driving the pass.

    Returns:
        The crosswalk columns plus the `SIDECAR_COLUMNS` present on ``matches``.
    """
    sidecars = [c for c in SIDECAR_COLUMNS if c in matches.columns]
    return matches.select(
        pl.col(LOAN_ID).alias(spec.id_column),
        FHFA_ID,
        pl.col("year").alias("fhfa_year"),
        pl.col("fhfa_enterprise").cast(pl.Int8),
        "match_round",
        pl.col("match_quality_tier").cast(pl.UInt8),
        "zip3_consistent",
        *sidecars,
    ).select(*spec.crosswalk_columns, *sidecars)


def crosswalk_path(output_dir: Path, year: int, spec: GseSpec) -> Path:
    """Where one year's crosswalk is written.

    Args:
        output_dir: Root of the crosswalk output directory.
        year: Acquisition year to match.
        spec: The enterprise spec driving the pass.

    Returns:
        The output path for that year's crosswalk.
    """
    return output_dir / f"fhfa_mbs_crosswalk_{spec.name}_{year}.parquet"


def run_year(
    year: int,
    spec: GseSpec,
    output_dir: Path = Config.FHFA_MBS_CROSSWALK_DIR,
    overwrite: bool = False,
) -> pl.DataFrame:
    """Match one year and write its crosswalk, unresolved across years.

    Each year also offers the prior origination year, so a loan this year claims
    may also be claimed by the adjacent year's run; `run_matching` resolves
    those claims before writing. What is written passes `admitted`.

    Args:
        year: The FHFA acquisition year.
        spec: The enterprise being matched.
        output_dir: Directory the crosswalk is written to.
        overwrite: Rebuild any cache the spec's pool filter keeps.

    Returns:
        Matched pairs with every matched column.
    """
    matches = match_year(year, spec, overwrite)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = crosswalk_path(output_dir, year, spec)
    with atomic_output(path) as tmp:
        admitted(crosswalk(matches, spec)).write_parquet(tmp)
    logger.info("Crosswalk saved to: %s", path)
    return matches


def run_matching(
    agency: Literal["fnma", "fhlmc"],
    min_year: int = 2018,
    max_year: int = 2025,
    output_dir: Path = Config.FHFA_MBS_CROSSWALK_DIR,
    overwrite: bool = False,
) -> None:
    """Match a year range, resolve cross-year claims, then write the crosswalks.

    Years are matched independently and each offers the prior origination year too,
    so a loan can be assigned to FHFA records in two acquisition years; one row per
    loan survives before anything is written. `admitted` then cuts the sidecars and,
    for a spec with ``non_evidence_score_fields``, the pairs those fields decided --
    last, so that removal is a pure deletion.

    Args:
        agency: The enterprise being matched, ``"fnma"`` or ``"fhlmc"`` (its `GseSpec`
            is ``inputs.SPECS[agency]``).
        min_year: First acquisition year (inclusive).
        max_year: Last acquisition year (inclusive).
        output_dir: Directory the crosswalks are written to.
        overwrite: Rebuild any cache the spec's pool filter keeps.
    """
    spec = SPECS[agency]
    crosswalks: dict[int, pl.DataFrame] = {}
    for year in range(min_year, max_year + 1):
        try:
            crosswalks[year] = crosswalk(match_year(year, spec, overwrite), spec)
        except FileNotFoundError as e:
            logger.warning("Skipping year %d: %s", year, e)

    resolved, removed = resolve_cross_year(crosswalks, spec.id_column)
    output_dir.mkdir(parents=True, exist_ok=True)
    undecided = 0
    for year, df in resolved.items():
        written = admitted(df)
        undecided += len(df) - len(written)
        with atomic_output(crosswalk_path(output_dir, year, spec)) as tmp:
            written.write_parquet(tmp)
        logger.info(
            "  %d: %s pairs (%s claimed in two years, %s the evidence did not decide)",
            year,
            f"{len(written):,}",
            f"{len(crosswalks[year]) - len(df):,}",
            f"{len(df) - len(written):,}",
        )
    logger.info("Cross-year resolution removed %s rows", f"{removed:,}")
    if undecided:
        logger.info(
            "The evidence rule dropped %s pairs the %s term settled",
            f"{undecided:,}",
            ", ".join(spec.options.non_evidence_score_fields),
        )


# ---------------------------------------------------------------------------
# The FHFA side
# ---------------------------------------------------------------------------


def load_fhfa(year: int, spec: GseSpec) -> pl.LazyFrame:
    """Scan FHFA sf_c for one year, filtered to the enterprise.

    Args:
        year: Acquisition year to match.
        spec: The enterprise spec driving the pass.

    Returns:
        FHFA sf_c for that year, filtered to the spec's enterprise.
    """
    path = Config.get_fhfa_config().FHFA_SILVER_DIR / "sf_c" / f"sf_c_{year}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"FHFA file not found: {path}")
    return pl.scan_parquet(path).filter(pl.col("enterprise_flag") == spec.enterprise_flag)


def prepare_fhfa(lf: pl.LazyFrame, spec: GseSpec) -> pl.LazyFrame:
    """Map FHFA sf_c onto the canonical matching schema.

    The geography sidecar is the one piece of derivation here: FHFA discloses a
    census tract rather than a ZIP, so the dominant ZIP3 for the tract (at the
    record's own census vintage) is joined back by record id, giving the score a
    sub-MSA signal the loan-level files carry natively.

    Args:
        lf: The frame to transform.
        spec: The enterprise spec driving the pass.

    Returns:
        FHFA sf_c mapped onto the canonical matching schema.
    """
    zip_lookup = (
        lf.select(
            pl.col("record_number").cast(pl.Int64).alias(FHFA_ID),
            tract_geoid("state_code", "county", "census_tract").alias("census_tract"),
            census_year_to_vintage_expr("Census Year"),
        )
        .join(load_dominant_tract_zip3(), on=["census_tract", "vintage"], how="left")
        .select(FHFA_ID, "zip3")
    )

    borrowers = pl.col("number_of_borrowers").cast(pl.Int64)
    if spec.max_borrowers is not None:
        borrowers = borrowers.clip(upper_bound=spec.max_borrowers)

    # sf_c silver stores state/msa as zero-padded strings; cast up front so the
    # msa==99999 non-metro sentinel comparison below stays valid.
    prepared = lf.select(
        pl.col("record_number").cast(pl.Int64).alias(FHFA_ID),
        pl.col("enterprise_flag").cast(pl.Int8).alias("fhfa_enterprise"),
        pl.col("state_code").cast(pl.Int64, strict=False).alias("state_fips"),
        pl.col("msa_code").cast(pl.Int64, strict=False).alias("msa"),
        pl.col("application_channel").cast(pl.Int64).alias("channel"),
        pl.col("interest_rate_at_origination").cast(pl.Float64).alias("interest_rate"),
        pl.col("note_amount").cast(pl.Float64).alias("loan_amount"),
        pl.col("ltv_at_origination").cast(pl.Float64).alias("ltv"),
        pl.col("dti_ratio").cast(pl.Float64).alias("dti"),
        pl.col("loan_term").cast(pl.Int64).alias("term"),
        pl.col("number_of_units").cast(pl.Int64).alias("num_units"),
        borrowers.alias("num_borrowers"),
        pl.col("first_time_home_buyer").cast(pl.Int64).alias("fthb"),
        pl.col("loan_purpose").cast(pl.Int64),
        pl.col("occupancy_code").cast(pl.Int64),
        pl.col("property_type").cast(pl.Int64),
        pl.col("year"),
        pl.col("date_of_mortgage_note").alias("orig_year_flag"),
    ).with_columns(pl.when(pl.col("msa") == 99999).then(0).otherwise(pl.col("msa")).alias("msa"))
    return prepared.join(zip_lookup, on=FHFA_ID, how="left")
