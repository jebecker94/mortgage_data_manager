"""Run metrics, drift guard and staleness guard for the MBS-UMBS matcher.

Every matcher run writes a sidecar JSON next to the crosswalk
(``mbs_umbs_crosswalk_{gse}_metrics.json``) recording input sizes, per-round match counts, and
MBS-side match rates by First Payment Date year. ``check_against_baseline`` compares a
sidecar against the pinned floors in ``expected_match_rates.json`` and reports breaches
— the loud tripwire for upstream import-layer changes that silently degrade matching
(encoding drift collapses rates catastrophically, so floors are set well below achieved
rates and only stable clean-window years are pinned).

``check_input_staleness`` answers the other question: not "did the matcher degrade" but
"was it run against today's inputs at all". Matcher outputs live outside the
`core.medallion.should_process_file` guard, and **mtimes cannot answer it** — an input
directory can grow while every file in it stays older than the crosswalk. That is not
hypothetical: ``fnma/silver/issuances`` went from 58.0M rows to 73.5M with no file newer
than the crosswalk built from the smaller universe, leaving it 335,357 pairs behind its
own inputs and passing every mtime check. Row counts catch it; the sidecar records them.

Wire-up: the matchers call ``count_input_rows`` at the start and ``write_run_metrics`` at
the end of every run; ``mortgage-data match mbs-umbs check`` (and ``validate``) call
``check_against_baseline``, and ``check`` / ``info`` call ``check_input_staleness``.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import atomic_output

logger = get_logger(__name__)

BASELINE_FILE = MortgageDataConfig.SCHEMAS_DIR / "umbs" / "expected_match_rates.json"


def count_input_rows(
    mbs_dir: Path,
    umbs_dir: Path,
    snapshot_file: Path | None = None,
    harp_file: Path | None = None,
) -> dict[str, int]:
    """Count the rows behind each of the matcher's input globs.

    Read from parquet metadata, so this costs a directory walk and a footer read per file
    (a few seconds over the whole universe), not a data scan. The counts are **raw**:
    before FPD windowing, the purpose-``M`` filter, the snapshot anti-join and duplicate
    exclusion, so they are a property of the inputs alone and stay comparable across runs
    whose window or filters differ.

    Args:
        mbs_dir: Directory of GSE single-family loan-level parquet (the MBS side).
        umbs_dir: Directory of UMBS issuance-disclosure (ILLD) parquet.
        snapshot_file: First monthly UMBS snapshot, or ``None`` for an ILLD-only run.
        harp_file: Fannie's separate HARP/Refi Plus issuance file, or ``None`` when the
            run does not include it.

    Returns:
        ``{"n_mbs_input_rows": ..., "n_umbs_input_rows": ...}``, each entry summing every
        release that run fed to its side — MBS over the quarterly files and HARP, UMBS
        over ILLD and the snapshot.
    """

    def _rows(pattern: str) -> int:
        return int(pl.scan_parquet(pattern).select(pl.len()).collect().item())

    n_umbs = _rows(str(umbs_dir / "**/*.parquet"))
    if snapshot_file is not None:
        n_umbs += _rows(str(snapshot_file))
    n_mbs = _rows(str(mbs_dir / "**/*.parquet"))
    if harp_file is not None:
        n_mbs += _rows(str(harp_file))
    return {
        "n_mbs_input_rows": n_mbs,
        "n_umbs_input_rows": n_umbs,
    }


def compute_run_metrics(
    gse: str,
    lf_mbs: pl.LazyFrame,
    n_umbs: int,
    crosswalk: pl.DataFrame,
    round_counts: dict[str, int],
    input_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Assemble the run-metrics payload for one matcher run.

    Args:
        gse: ``"fnma"`` or ``"fhlmc"``.
        lf_mbs: The matcher's prepared MBS-side frame (post-harmonization, windowed),
            with ``Loan Sequence Number`` and a ``pl.Date`` ``First Payment Date``.
        n_umbs: Row count of the prepared UMBS-side frame.
        crosswalk: The final crosswalk written to disk.
        round_counts: Matched-pair count per round, e.g. ``{"round1": ..., ...}``.
        input_counts: Raw input-glob row counts from `count_input_rows`, recorded so
            `check_input_staleness` can detect an input that grew after the run.

    Returns:
        The run-metrics payload.
    """
    cw_mbs = (
        crosswalk.lazy()
        .select(pl.col("Loan Sequence Number (MBS)").alias("Loan Sequence Number"))
        .with_columns(pl.lit(True).alias("matched"))
    )
    by_year = (
        lf_mbs.select(["Loan Sequence Number", "First Payment Date"])
        .with_columns(pl.col("First Payment Date").dt.year().alias("fpd_year"))
        .join(cw_mbs, on="Loan Sequence Number", how="left")
        .with_columns(pl.col("matched").fill_null(False))
        .group_by("fpd_year")
        .agg(total=pl.len(), matched=pl.col("matched").sum())
        .with_columns((pl.col("matched") / pl.col("total")).alias("match_rate"))
        .sort("fpd_year")
        .collect()
    )
    total_pairs = int(sum(round_counts.values()))
    return {
        "gse": gse,
        "run_date": datetime.date.today().isoformat(),
        "n_mbs_loans": int(by_year["total"].sum()),
        "n_umbs_loans": int(n_umbs),
        **{k: int(v) for k, v in (input_counts or {}).items()},
        "total_pairs": total_pairs,
        "round_counts": {k: int(v) for k, v in round_counts.items()},
        "round1_share": (round_counts.get("round1", 0) / total_pairs) if total_pairs else 0.0,
        "mbs_match_rate_by_fpd_year": {
            str(int(y)): {"total": int(t), "matched": int(m), "rate": float(r)}
            for y, t, m, r in by_year.iter_rows()
            if y is not None
        },
    }


def write_run_metrics(metrics: dict[str, Any], crosswalk_file: Path) -> Path:
    """Write the metrics sidecar next to the crosswalk; returns the sidecar path.

    Args:
        metrics: The run-metrics payload.
        crosswalk_file: Path of the crosswalk the sidecar belongs to.

    Returns:
        The sidecar path written.
    """
    sidecar = crosswalk_file.with_name(f"{crosswalk_file.stem}_metrics.json")
    with atomic_output(sidecar) as tmp:
        tmp.write_text(json.dumps(metrics, indent=2))
    logger.info("Run metrics written to %s", sidecar)
    return sidecar


def check_input_staleness(
    gse: str,
    crosswalk_dir: Path,
    mbs_dir: Path,
    umbs_dir: Path,
    snapshot_file: Path | None = None,
    harp_file: Path | None = None,
) -> list[str]:
    """Compare the crosswalk's recorded input sizes with a fresh count of the input globs.

    A crosswalk is stale when its inputs have changed since it was built, and **mtimes
    cannot tell you that**: an input directory can grow while every file in it stays older
    than the crosswalk, which is exactly how ``fnma/silver/issuances`` reached 73.5M rows
    against a crosswalk built from 58.0M without a single mtime looking wrong. Row counts
    do tell you, so every run records them and this compares them.

    Deliberately **not fatal** and deliberately not part of the drift guard: a size change
    is not a defect, it is a rebuild the crosswalk has not caught up with. The caller warns
    loudly and carries on.

    A sidecar written before the guard shipped carries no raw input counts. The MBS side is
    still checkable there, one-sidedly: ``n_mbs_loans`` is the *windowed* count and so can
    never exceed the raw input, meaning a fresh count above it proves the input grew. The
    UMBS side has no such bound (its recorded count is post-filter, post-dedup) and is
    skipped until the next run records the raw number.

    Args:
        gse: ``"fnma"`` or ``"fhlmc"``.
        crosswalk_dir: Directory holding the crosswalk and its sidecar.
        mbs_dir: Directory of GSE single-family loan-level parquet (the MBS side).
        umbs_dir: Directory of UMBS issuance-disclosure (ILLD) parquet.
        snapshot_file: First monthly UMBS snapshot, or ``None`` for an ILLD-only run.
        harp_file: Fannie's separate HARP/Refi Plus issuance file, or ``None``.

    Returns:
        Human-readable staleness warnings (empty = the crosswalk matches its inputs).
    """
    sidecar = crosswalk_dir / f"mbs_umbs_crosswalk_{gse}_metrics.json"
    if not sidecar.exists():
        return [f"{gse}: no metrics sidecar at {sidecar} — input sizes cannot be checked"]
    metrics = json.loads(sidecar.read_text())
    fresh = count_input_rows(mbs_dir, umbs_dir, snapshot_file, harp_file)

    warnings: list[str] = []
    if "n_mbs_input_rows" in metrics:
        for side, key in (("MBS", "n_mbs_input_rows"), ("UMBS", "n_umbs_input_rows")):
            was, now = int(metrics.get(key, -1)), fresh[key]
            if was != now:
                warnings.append(
                    f"{gse}: {side} input rows changed since the crosswalk was built — "
                    f"{was:,} at build ({metrics.get('run_date', 'unknown date')}) vs "
                    f"{now:,} now ({now - was:+,}). The crosswalk is STALE; re-run the "
                    "matcher and rebuild its consumers."
                )
    else:
        recorded = int(metrics.get("n_mbs_loans", 0))
        if fresh["n_mbs_input_rows"] > recorded:
            warnings.append(
                f"{gse}: MBS input holds {fresh['n_mbs_input_rows']:,} rows but the "
                f"crosswalk was built over {recorded:,} matchable loans "
                f"({metrics.get('run_date', 'unknown date')}) — the input has grown by at "
                f"least {fresh['n_mbs_input_rows'] - recorded:,} rows. The crosswalk is "
                "STALE; re-run the matcher and rebuild its consumers."
            )
        warnings.append(
            f"{gse}: sidecar predates the input-size guard (no raw input counts recorded), "
            "so the UMBS side could not be checked — the next matcher run records both."
        )
    for w in warnings:
        logger.warning("%s", w)
    return warnings


def check_against_baseline(
    gse: str,
    crosswalk_dir: Path,
    baseline_file: Path = BASELINE_FILE,
) -> list[str]:
    """Compare a run's metrics sidecar against the pinned baseline floors.

    Returns a list of human-readable breach descriptions (empty = healthy). Floors are
    deliberately loose: they exist to catch the catastrophic collapses that encoding
    drift produces (rates falling by tens of points, a round emptying out), not to
    police sub-point fluctuations from data recency.

    Args:
        gse: Which GSE the run covers.
        crosswalk_dir: Directory holding the crosswalk and its sidecar.
        baseline_file: Path of the pinned baseline floors.

    Returns:
        The baseline comparison, naming any floor the run failed.
    """
    sidecar = crosswalk_dir / f"mbs_umbs_crosswalk_{gse}_metrics.json"
    if not sidecar.exists():
        return [f"{gse}: no metrics sidecar at {sidecar} — re-run the matcher to generate one"]
    metrics = json.loads(sidecar.read_text())
    baseline_all = json.loads(baseline_file.read_text())
    baseline = baseline_all.get(gse)
    if baseline is None:
        return [f"{gse}: no baseline entry in {baseline_file}"]

    breaches: list[str] = []
    if metrics["total_pairs"] < baseline["total_pairs_floor"]:
        breaches.append(
            f"{gse}: total pairs {metrics['total_pairs']:,} below floor "
            f"{baseline['total_pairs_floor']:,}"
        )
    if metrics["round1_share"] < baseline["round1_share_floor"]:
        breaches.append(
            f"{gse}: round-1 share {metrics['round1_share']:.1%} below floor "
            f"{baseline['round1_share_floor']:.1%} — MIP or a secondary filter has "
            "likely regressed (round 2 absorbs what round 1 loses, so totals may look fine)"
        )
    by_year = metrics["mbs_match_rate_by_fpd_year"]
    for year, floor in baseline["fpd_year_rate_floors"].items():
        got = by_year.get(year)
        if got is None:
            breaches.append(f"{gse}: FPD year {year} missing from run metrics")
        elif got["rate"] < floor:
            breaches.append(
                f"{gse}: FPD-{year} match rate {got['rate']:.1%} below floor {floor:.1%} "
                f"({got['matched']:,}/{got['total']:,})"
            )
    return breaches
