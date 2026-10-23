"""FNMA CAS/CIRT reference-pool membership extraction.

Extracts each CRT loan's deal membership (loan -> Reference Pool ID / Deal
Name / program) from Fannie's CAS/CIRT historical downloads.

IMPORTANT — parallel identifier namespaces (verified 2026-07-21): the CRT
files use the 113-column LPPUB *layout*, but their ``Loan Identifier`` is a
CRT-specific sequence, NOT the public LPPUB Loan Identifier. Both are dense
sequential runs in overlapping numeric ranges with zero identity overlap
(111,491 crosswalk ids inside the 2024Q1 LPPUB id block: 0 matches). The
extracted table is therefore CRT-internal — linking Fannie CRT loans to the
SF dataset requires characteristics matching (mirroring ``fhlmc_match``),
not an id join. (Also note: the SF bronze quarterlies on disk mix LPPUB
download vintages — legacy 12-digit ids through 2019Q1, new 9-digit ids
2019Q3+ — so SF-side ids need a uniform bronze rebuild before any
id-keyed Fannie crosswalk work.)

Two sources, tried in order by `run_matching_fnma`:

1. **CAS/CIRT historical downloads** (``CAS_*.zip`` / ``CIRT_*.zip`` in
   fnma raw, from Data Dynamics) — each zip holds one giant pipe-delimited
   CSV in the 113-column LPPUB layout (no header): the full monthly panel of
   every reference-pool loan, with ``Reference Pool ID`` (field 1) and
   ``Deal Name`` (field 104, populated in reporting months from mid-2018
   on). A single streaming pass collects the distinct loan-deal pairs
   without materializing the ~350 GB panel. Ids arrive unpadded and are
   zero-padded to 12 chars for stability.
2. **SF performance bronze** fallback — the same fields in the LPPUB
   quarterlies (entirely null in the current ``Performance_All.zip``
   vintage; this path reports zero rows until a release populates them).
"""

from __future__ import annotations

import zipfile
from io import TextIOWrapper
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger, log_execution_time
from mortgage_data_manager.core.medallion import atomic_output
from mortgage_data_manager.matching.match_crt_mbs.config import CRTMBSMatchingConfig

logger = get_logger(__name__)

_COLS = ["Loan Identifier", "Reference Pool ID", "Deal Name"]

# LPPUB field positions used by the streaming zip extraction (1-based):
# 1 = Reference Pool ID, 2 = Loan Identifier, 104 = Deal Name (of 113).
_N_FIELDS = 113
_DEAL_NAME_POS = 104


@log_execution_time
def extract_fnma_crt_from_zips(
    zip_paths: list[Path],
    crosswalk_file: Path | None = None,
) -> pl.DataFrame:
    """Stream the CAS/CIRT historical zips and extract distinct loan-deal pairs.

    One decompressing pass per zip; a dict keyed on (loan, reference pool)
    accumulates the deal name (first non-empty seen). Ids are zero-padded to
    12 chars and stored as ``CRT Loan Identifier`` — a CRT-namespace id that
    does NOT join to the LPPUB ``Loan Identifier`` (see module docstring).

    Args:
        zip_paths: ``CAS_*.zip`` / ``CIRT_*.zip`` archives to stream.
        crosswalk_file: Output parquet. Defaults to
            ``crosswalk/crt_mbs/crt_mbs_crosswalk_fnma.parquet``.

    Returns:
        The membership DataFrame (CRT Loan Identifier, Reference Pool ID,
        Deal Name, program).
    """
    crosswalk_file = crosswalk_file or CRTMBSMatchingConfig.FNMA_CROSSWALK_FILE
    CRTMBSMatchingConfig.ensure_directories()

    # (loan, pool) -> [deal_name, program]
    mapping: dict[tuple[str, str], list[str | None]] = {}
    # (loan, pool) keys whose rows name two different deals: left unnamed, never picked
    conflicted: set[tuple[str, str]] = set()
    rsplit_n = _N_FIELDS - _DEAL_NAME_POS + 1
    for zp in zip_paths:
        program = zp.name.split("_")[0].upper()
        logger.info("Streaming %s (%s)", zp.name, program)
        n_lines = 0
        with zipfile.ZipFile(zp) as zf:
            for member in zf.namelist():
                if not member.endswith(".csv"):
                    continue
                with zf.open(member) as fh:
                    for line in TextIOWrapper(fh, encoding="utf-8", newline=""):
                        n_lines += 1
                        pool, loan, _ = line.split("|", 2)
                        key = (loan, pool)
                        entry = mapping.get(key)
                        if entry is None:
                            deal = line.rsplit("|", rsplit_n)[1]
                            mapping[key] = [deal or None, program]
                        else:
                            deal = line.rsplit("|", rsplit_n)[1]
                            if not deal or key in conflicted:
                                continue
                            if entry[0] is None:
                                entry[0] = deal
                            elif deal != entry[0]:
                                conflicted.add(key)
                                entry[0] = None
        logger.info(
            "  %s rows -> %s cumulative loan-deal pairs", f"{n_lines:,}", f"{len(mapping):,}"
        )
    if conflicted:
        logger.warning("%s (loan, pool) keys name two deals; left unnamed", f"{len(conflicted):,}")

    if not mapping:
        return pl.DataFrame(
            schema={
                "CRT Loan Identifier": pl.String,
                "Reference Pool ID": pl.String,
                "Deal Name": pl.String,
                "program": pl.String,
            }
        )

    crosswalk = (
        pl.DataFrame(
            {
                "CRT Loan Identifier": [k[0] for k in mapping],
                "Reference Pool ID": [k[1] for k in mapping],
                "Deal Name": [v[0] for v in mapping.values()],
                "program": [v[1] for v in mapping.values()],
            }
        )
        .with_columns(pl.col("CRT Loan Identifier").str.zfill(12))
        .sort("Reference Pool ID", "CRT Loan Identifier")
    )
    with atomic_output(crosswalk_file) as tmp:
        crosswalk.write_parquet(tmp)
    logger.info("Wrote %s loan-deal pairs -> %s", f"{crosswalk.height:,}", crosswalk_file)
    return crosswalk


@log_execution_time
def run_matching_fnma(
    bronze_dir: Path | None = None,
    crosswalk_file: Path | None = None,
    raw_dir: Path | None = None,
) -> pl.DataFrame:
    """Extract Fannie CAS/CIRT deal membership (zips first, bronze fallback).

    When ``CAS_*.zip`` / ``CIRT_*.zip`` historical downloads are present in
    the fnma raw directory, streams them via
    `extract_fnma_crt_from_zips` (the authoritative CRT source).
    Otherwise falls back to scanning the SF performance bronze quarterlies
    for populated ``Reference Pool ID`` / ``Deal Name`` fields (see module
    docstring for the current LPPUB caveat).

    Args:
        bronze_dir: FNMA bronze directory of quarterly parquet files.
            Defaults to the fnma config's bronze dir.
        crosswalk_file: Output parquet. Defaults to
            ``crosswalk/crt_mbs/crt_mbs_crosswalk_fnma.parquet``.
        raw_dir: FNMA raw directory searched for the CRT zips. Defaults to
            the fnma config's raw dir.

    Returns:
        The extracted crosswalk DataFrame (possibly empty).
    """
    fnma = CRTMBSMatchingConfig.get_fnma_config()
    bronze_dir = bronze_dir or fnma.FNMA_BRONZE_DIR
    raw_dir = raw_dir or fnma.FNMA_RAW_DIR
    crosswalk_file = crosswalk_file or CRTMBSMatchingConfig.FNMA_CROSSWALK_FILE
    CRTMBSMatchingConfig.ensure_directories()

    crt_zips = sorted(raw_dir.glob("CAS_*.zip")) + sorted(raw_dir.glob("CIRT_*.zip"))
    if crt_zips:
        logger.info("Using CRT historical zips: %s", [z.name for z in crt_zips])
        return extract_fnma_crt_from_zips(crt_zips, crosswalk_file=crosswalk_file)
    logger.info("No CAS_*/CIRT_*.zip in raw; falling back to SF performance bronze scan")

    pieces: list[pl.DataFrame] = []
    files = sorted(bronze_dir.glob("*.parquet"))
    logger.info("Scanning %s FNMA bronze quarterlies for deal membership", len(files))
    for f in files:
        lf = pl.scan_parquet(f)
        names = lf.collect_schema().names()
        if any(c not in names for c in _COLS):
            logger.warning("  %s: missing deal columns, skipping", f.name)
            continue
        piece = (
            lf.select(_COLS)
            .filter(pl.col("Reference Pool ID").is_not_null() | pl.col("Deal Name").is_not_null())
            .unique()
            .collect()
        )
        if piece.height:
            logger.info("  %s: %s loan-deal rows", f.name, f"{piece.height:,}")
            pieces.append(piece)

    if not pieces:
        logger.warning(
            "No populated Reference Pool ID / Deal Name rows in any bronze quarterly. "
            "The current LPPUB vintage leaves these fields blank — refresh "
            "Performance_All.zip or add the CAS/CIRT loan-level files, then re-run."
        )
        return pl.DataFrame(schema={c: pl.String for c in _COLS})

    crosswalk = (
        pl.concat(pieces, how="diagonal_relaxed").unique().sort("Loan Identifier", "Deal Name")
    )
    with atomic_output(crosswalk_file) as tmp:
        crosswalk.write_parquet(tmp)
    logger.info("Wrote %s loan-deal rows -> %s", f"{crosswalk.height:,}", crosswalk_file)
    return crosswalk
