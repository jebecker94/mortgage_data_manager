"""Aggregator ↔ issuer name triangulation, the HMDA-GNMA crosswalks' validation gauge.

A DTI-independent quality gauge for any crosswalk that links a HMDA origination to a
GNMA loan; ``hmda-gnma validate`` scores this workflow's FHA and VA/USDA crosswalks
with it. The pool issuer named in GNMA's ``issrinfo`` file is the lender that
*delivered* the loan. When HMDA's sellers/purchasers crosswalk links the origination
to a purchase record, the purchaser's LEI names the aggregator, and its HMDA panel
name should agree with the pool issuer name far more often than the originator's own
name does. The two rates are reported side by side:

- ``aggregator``: share of testable pairs whose purchaser (``lei_p``) panel name
  token-matches the pool issuer name. High for true pairs.
- ``originator_ctrl``: the same test against the originator (``lei_s``) name. A
  control — it stays low regardless of match quality, so a rise in ``aggregator``
  that is not mirrored here is evidence about the pairs, not about the names.

The gauge uses no field the matchers key on (no rate, amount, tract, DTI), so it
scores a candidate matcher change without circularity. "Testable" pairs are those
whose seller appears in the sellers/purchasers crosswalk; the rest carry no
aggregator identity and are excluded from the denominator.

The sellers/purchasers → LEI pair table is persisted under the workflow's
matching directory (`sp_lei_pairs_path`) so that scoring a crosswalk costs seconds,
not a HMDA scan. ``hmda-gnma validate`` builds it when missing; rebuild it with
``--overwrite`` after the sellers/purchasers crosswalk or the HMDA silver is rebuilt.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger, log_execution_time
from mortgage_data_manager.core.medallion import should_process_file
from mortgage_data_manager.matching.config import MatchingConfig
from mortgage_data_manager.matching.master.config import MasterCrosswalkConfig
from mortgage_data_manager.matching.match_hmda_gnma.config import HMDAGNMAMatchingConfig
from mortgage_data_manager.matching.names import jaccard, name_tokens
from mortgage_data_manager.sources.gnma.load import (
    one_row_per_loan,
    scan_latest_issuer_names,
    scan_new_issuance,
)
from mortgage_data_manager.sources.hmda.load import scan_hmda_years

logger = get_logger(__name__)

#: Pair-table stem: seller ``HMDAIndex_s`` → (``lei_s``, ``lei_p``).
SP_LEI_PAIRS_NAME = "sp_lei_pairs"

#: Token-set Jaccard at or above which two institution names are "the same".
NAME_MATCH_THRESHOLD = 0.5


# ---------------------------------------------------------------------------
# Reference name tables
# ---------------------------------------------------------------------------


def issuer_names() -> pl.DataFrame:
    """Latest ``issuer_name`` per GNMA ``issuer_id`` from the ``issrinfo`` silver.

    An issuer carrying two names on its latest record date has no single latest
    name and is left out.

    Returns:
        One row per GNMA ``issuer_id`` with an unambiguous latest issuer name.
    """
    return scan_latest_issuer_names().collect()


def panel_names(min_year: int = 2018, max_year: int | None = None) -> pl.DataFrame:
    """Latest ``respondent_name`` per ``lei`` from the HMDA post-2018 panel.

    Args:
        min_year: First activity year to read.
        max_year: Last activity year to read, or None for all.

    Returns:
        One row per ``lei`` with an unambiguous latest respondent name; an LEI
        filing two names in its latest year is left out.
    """
    return (
        scan_hmda_years("post2018", min_year, max_year, dataset="panel")
        .select("lei", "respondent_name", "activity_year")
        .filter(pl.col("lei").is_not_null() & (pl.col("lei") != ""))
        .filter(pl.col("activity_year") == pl.col("activity_year").max().over("lei"))
        .select("lei", pl.col("respondent_name").alias("name"))
        .unique()
        .filter(pl.len().over("lei") == 1)
        .collect()
    )


# ---------------------------------------------------------------------------
# Sellers/purchasers → LEI pairs
# ---------------------------------------------------------------------------


@log_execution_time
def build_sp_lei_pairs(min_year: int = 2018, max_year: int | None = None) -> pl.DataFrame:
    """Attach originator and last-buyer LEIs to the sellers/purchasers crosswalk.

    A loan sold more than once has one HMDA purchase record per buyer, all matching
    the origination -- successive owners, not rival candidates. The buyer that
    delivers the loan onward is the one to triangulate against, so each origination
    keeps its LAST HMDA-visible buyer, ordered exactly as the master spine orders
    the chain: a single purchase record is the last buyer; among several, it is the
    one record whose own ``purchaser_type`` reports selling the loan on to an agency.
    An origination with several records and not exactly one such onward sale has no
    identifiable last buyer and is dropped rather than assigned one. The LEI map is
    scanned from the HMDA loan register across both actions, since ``HMDAIndex_p``
    names a purchase record.

    Args:
        min_year: First HMDA activity year to scan for the LEI map.
        max_year: Last activity year; ``None`` takes everything on disk.

    Returns:
        Columns ``HMDAIndex_s``, ``lei_s``, ``lei_p``.
    """
    sp_dir = (
        MatchingConfig.get_crosswalk_type_dir("hmda_sellers_purchasers")
        / "hmda_sellers_purchasers_crosswalk_post2018"
    )
    if not sp_dir.exists():
        raise FileNotFoundError(f"Sellers/purchasers crosswalk not found: {sp_dir}")
    sp = pl.scan_parquet(sp_dir / "**" / "*.parquet").select("HMDAIndex_s", "HMDAIndex_p").unique()
    needed = pl.concat(
        [
            sp.select(pl.col("HMDAIndex_s").alias("HMDAIndex")),
            sp.select(pl.col("HMDAIndex_p").alias("HMDAIndex")),
        ]
    ).unique()
    # HMDAIndex is the register's key; an index that carried two LEIs (or two
    # purchaser types) would name neither.
    register = (
        scan_hmda_years("post2018", min_year, max_year)
        .select("HMDAIndex", "lei", "purchaser_type")
        .join(needed, on="HMDAIndex", how="semi")
        .unique()
        .filter(pl.len().over("HMDAIndex") == 1)
    )
    hops = sp.join(
        register.select(
            pl.col("HMDAIndex").alias("HMDAIndex_p"),
            pl.col("lei").alias("lei_p"),
            pl.col("purchaser_type")
            .is_in(MasterCrosswalkConfig.AGENCY_PURCHASER_TYPES)
            .fill_null(value=False)
            .alias("_onward"),
        ),
        on="HMDAIndex_p",
        how="left",
    ).with_columns(
        pl.len().over("HMDAIndex_s").alias("_n"),
        pl.col("_onward").fill_null(value=False).sum().over("HMDAIndex_s").alias("_n_onward"),
    )
    last = hops.filter(
        (pl.col("_n") == 1)
        | (pl.col("_onward").fill_null(value=False) & (pl.col("_n_onward") == 1))
    )
    return (
        last.join(
            register.select(pl.col("HMDAIndex").alias("HMDAIndex_s"), pl.col("lei").alias("lei_s")),
            on="HMDAIndex_s",
        )
        .select("HMDAIndex_s", "lei_s", "lei_p")
        .collect(engine="streaming")
    )


def sp_lei_pairs_path() -> Path:
    """The persisted pair table, under the HMDA-GNMA workflow's matching directory."""
    return HMDAGNMAMatchingConfig.HMDA_GNMA_MATCHING_DIR / f"{SP_LEI_PAIRS_NAME}.parquet"


def run_build(
    min_year: int = 2018, max_year: int | None = None, overwrite: bool = False
) -> dict[str, int]:
    """Build and write the sellers/purchasers → LEI pair table.

    Args:
        min_year: First activity year to read.
        max_year: Last activity year to read, or None for all.
        overwrite: Rebuild the artifact even when it is already up to date.

    Returns:
        Row count keyed by artifact stem (zero when the build was skipped).
    """
    path = sp_lei_pairs_path()
    if not should_process_file(path, overwrite):
        logger.info("Pair table exists; pass overwrite=True to rebuild: %s", path)
        return {SP_LEI_PAIRS_NAME: 0}
    path.parent.mkdir(parents=True, exist_ok=True)
    pairs = build_sp_lei_pairs(min_year, max_year)
    pairs.write_parquet(path)
    logger.info("Wrote %s (%s rows)", path, f"{len(pairs):,}")
    return {SP_LEI_PAIRS_NAME: len(pairs)}


def load_sp_lei_pairs() -> pl.DataFrame:
    """Load the built pair table.

    Returns:
        The sellers/purchasers to LEI pair table.

    Raises:
        FileNotFoundError: If the pair table has not been built yet.
    """
    path = sp_lei_pairs_path()
    if not path.exists():
        raise FileNotFoundError(
            f"sp_lei_pairs not built: {path}. Run `mortgage-data match hmda-gnma validate`."
        )
    return pl.read_parquet(path)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def attach_issuer_id(pairs: pl.DataFrame) -> pl.DataFrame:
    """Add ``issuer_id`` to pairs that carry only ``gnma_loan_id``.

    The FHA-chain master crosswalk stores no issuer; the VA/USDA master does. Reads
    only the loans present in ``pairs`` from the GNMA new-issuance feed.

    Args:
        pairs: The pair table to annotate.

    Returns:
        The pairs with ``issuer_id`` filled in where it was absent.
    """
    if "issuer_id" in pairs.columns:
        return pairs
    ids = pairs.select("gnma_loan_id").unique().lazy()
    loans = (
        scan_new_issuance()
        .select(
            pl.col("disclosure_sequence_number").cast(pl.Utf8).alias("gnma_loan_id"),
            pl.col("issuer_id").cast(pl.Utf8),
        )
        .join(ids, on="gnma_loan_id", how="semi")
    )
    # A loan id listed under two issuers names neither.
    issuer = one_row_per_loan(loans, "gnma_loan_id").collect(engine="streaming")
    return pairs.join(issuer, on="gnma_loan_id", how="left")


def triangulate(
    pairs: pl.DataFrame,
    sp: pl.DataFrame,
    issuer: pl.DataFrame,
    panel: pl.DataFrame,
    label: str = "all",
    threshold: float = NAME_MATCH_THRESHOLD,
) -> dict[str, object]:
    """Score one set of HMDA ↔ GNMA pairs.

    Args:
        pairs: Must carry ``HMDAIndex`` and ``issuer_id``.
        sp: ``HMDAIndex_s``, ``lei_s``, ``lei_p`` (see `load_sp_lei_pairs`).
        issuer: ``issuer_id``, ``issuer_name`` (see `issuer_names`).
        panel: ``lei``, ``name`` (see `panel_names`).
        label: Name for the row in the returned summary.
        threshold: Jaccard cut-off for a name agreement.

    Returns:
        ``set``, ``n``, ``n_testable``, ``aggregator``, ``originator_ctrl``; the two
        rates are ``None`` when nothing is testable.
    """
    d = (
        pairs.select("HMDAIndex", pl.col("issuer_id").cast(pl.Utf8))
        .join(sp, left_on="HMDAIndex", right_on="HMDAIndex_s", how="inner")
        .join(issuer, on="issuer_id", how="left")
        .join(panel.rename({"lei": "lei_p", "name": "name_p"}), on="lei_p", how="left")
        .join(panel.rename({"lei": "lei_s", "name": "name_s"}), on="lei_s", how="left")
        .with_columns(
            jaccard(name_tokens("issuer_name"), name_tokens("name_p")).fill_null(0.0).alias("jp"),
            jaccard(name_tokens("issuer_name"), name_tokens("name_s")).fill_null(0.0).alias("js"),
        )
    )
    n_testable = len(d)

    def _rate(col: str) -> float | None:
        if n_testable == 0:
            return None
        return round(float(d.select((pl.col(col) >= threshold).mean()).item()), 4)

    return {
        "set": label,
        "n": len(pairs),
        "n_testable": n_testable,
        "aggregator": _rate("jp"),
        "originator_ctrl": _rate("js"),
    }


@log_execution_time
def triangulate_crosswalk(
    path: Path,
    by: str | None = None,
    min_year: int = 2018,
    max_year: int | None = None,
) -> pl.DataFrame:
    """Score a HMDA ↔ GNMA crosswalk file, overall and optionally per group.

    Args:
        path: Crosswalk parquet with ``HMDAIndex`` and ``issuer_id`` or ``gnma_loan_id``.
        by: Column to split the score by (e.g. ``source``, ``match_round``).
        min_year: First HMDA activity year for the panel name table.
        max_year: Last activity year; ``None`` takes everything on disk.

    Returns:
        One summary row per group (plus ``all``), columns as `triangulate`.
    """
    pairs = attach_issuer_id(pl.read_parquet(path))
    sp = load_sp_lei_pairs()
    issuer = issuer_names()
    panel = panel_names(min_year, max_year)
    rows = [triangulate(pairs, sp, issuer, panel, "all")]
    if by is not None:
        for (value,), group in pairs.group_by(by, maintain_order=True):
            rows.append(triangulate(group, sp, issuer, panel, f"{by}={value}"))
    return pl.DataFrame(rows)
