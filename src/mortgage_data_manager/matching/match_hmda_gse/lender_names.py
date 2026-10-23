"""Every name a lender is known by on the HMDA side, and how to compare them to seller names.

The GSE loan-level files identify a loan's lender only by its ``Seller Name``; HMDA identifies
it by ``lei``. This module gathers, for each LEI, every name an authoritative HMDA-side source
gives it -- the HMDA transmittal series, the HMDA panel, and the Avery lender file's six name
columns -- and supplies the normalization and alignment-tier helpers that compare a seller
name to those names. It reads no crosswalk, so a matcher that bridges LEIs to seller names
with it stays independent of every other matching route.

Consumed by the direct HMDA <-> loan-issuance matcher
(`~mortgage_data_manager.matching.match_hmda_gse.matching_direct`).
"""

from __future__ import annotations

import polars as pl

from mortgage_data_manager.core.logging import get_logger, log_execution_time
from mortgage_data_manager.core.reference.avery import avery_file_path
from mortgage_data_manager.matching.names import normalize_name
from mortgage_data_manager.sources.hmda.config import HMDAConfig
from mortgage_data_manager.sources.hmda.load import scan_hmda_years

logger = get_logger(__name__)

# Both GSEs value-mask the seller on some records, with per-GSE strings: Fannie
# writes "Other", Freddie "Other sellers" (the servicer field masks separately, as
# "Other servicers"). ILLD and the monthly snapshots disclose real names and carry
# none of these; masking reaches a consumer through the SF fallback in
# ``combined/loan_issuance``, the direct matcher's target. Kept normalized, to compare
# after `_norm_name`.
MASKED_SELLER_NAMES = ["OTHER", "OTHER SELLERS", "OTHER SELLERS/SERVICERS"]

# Legal-form tokens stripped before comparing a seller name to an HMDA-side name.
LEGAL_SUFFIXES = (
    "LLC", "INC", "INCORPORATED", "CORP", "CORPORATION", "COMPANY", "CO", "NA",
    "NATIONAL ASSOCIATION", "FSB", "SSB", "SB", "LP", "LLP", "LTD", "LIMITED",
    "PARTNERSHIP", "A DIVISION OF", "DBA",
)  # fmt: skip


def _norm_name(col: str) -> pl.Expr:
    """Uppercase, normalize punctuation, collapse whitespace, by column name."""
    return normalize_name(pl.col(col))


def strip_legal(col: str) -> pl.Expr:
    """Strip a leading ``THE`` and trailing legal-form tokens."""
    expr = pl.col(col).str.replace(r"^THE ", "")
    for suffix in LEGAL_SUFFIXES:
        # Twice: names like "X MORTGAGE COMPANY LLC" carry two stacked forms.
        expr = expr.str.replace(f" {suffix}$", "").str.replace(f" {suffix}$", "")
    return expr.str.strip_chars()


#: Avery lender-file name columns. Six distinct name concepts per LEI-year --
#: transmittal, respondent, parent, high holder, entity, organization. The
#: parent/high-holder names matter here: a subsidiary that delivers under its
#: parent's name is invisible to any single-name source.
AVERY_NAME_COLS = ("NAMET", "NAMER", "NAMEP", "NAMEHH", "NAME", "NAMEOR")


def _ts_names(min_year: int, max_year: int) -> pl.LazyFrame:
    """Scan HMDA transmittal-sheet respondent names.

    The transmittal series is the filer's own self-reported name, filed with its
    LAR, so it is current and covers every filer -- exactly one row per LEI-year,
    and it extends past the panel's last year. It is free text, though: filers
    enter abbreviations, DBAs, and occasionally filing artifacts ("CBNA YEAR TO
    DATE"), which is why it supplements the panel rather than replacing it.

    Args:
        min_year: First activity year to scan.
        max_year: Last activity year to scan.

    Returns:
        One row per LEI-year, carrying the filer's self-reported name.
    """
    ts_dir = HMDAConfig.get_dataset_dir("silver", "transmittal_series", "post2018")
    if not ts_dir.exists():
        logger.warning("HMDA transmittal series missing: %s", ts_dir)
        return pl.LazyFrame({"lei": [], "name": []}, schema={"lei": pl.Utf8, "name": pl.Utf8})
    return (
        scan_hmda_years("post2018", min_year, max_year, dataset="transmittal_series")
        # Every name the filer reported, not one per year: the universe is a set.
        .select("lei", _norm_name("respondent_name").alias("name"))
        .unique()
    )


def _avery_names() -> pl.LazyFrame:
    """Scan the Avery lender file for every name it associates with an LEI.

    Fed-hosted and maintained on a different cadence than the CFPB panel, so it
    covers years the panel does not, and its parent/high-holder columns are names
    no HMDA-side source carries.

    Returns:
        Every ``(lei, name)`` association the Avery file carries.
    """
    avery_path = avery_file_path("post2018")
    if not avery_path.exists():
        logger.warning("Avery lender file missing; skipping: %s", avery_path)
        return pl.LazyFrame({"lei": [], "name": []}, schema={"lei": pl.Utf8, "name": pl.Utf8})
    avery = pl.read_excel(avery_path, infer_schema_length=None)
    present = [c for c in AVERY_NAME_COLS if c in avery.columns]
    frames = [
        avery.select(
            pl.col("LEI").cast(pl.Utf8).alias("lei"),
            normalize_name(pl.col(col).cast(pl.Utf8)).alias("name"),
        )
        for col in present
    ]
    return pl.concat(frames, how="diagonal_relaxed").lazy()


def _panel_names(min_year: int, max_year: int) -> pl.LazyFrame:
    """Scan the HMDA panel for each year's ``lei`` → ``respondent_name``."""
    return (
        scan_hmda_years("post2018", min_year, max_year, dataset="panel")
        .select(
            "lei",
            _norm_name("respondent_name").alias("panel_name"),
            pl.col("activity_year").cast(pl.Int32),
        )
        .filter(pl.col("lei").is_not_null())
        # Every name the panel lists, not one per LEI-year: the universe is a set.
        .unique()
    )


@log_execution_time
def build_name_universe(min_year: int = 2018, max_year: int = 2025) -> pl.DataFrame:
    """Every name any authoritative source associates with an LEI.

    Unions the HMDA transmittal series, the HMDA panel, and all six Avery name
    columns, then drops the year: a name a lender ever went by is a name it might
    have delivered under. Corroboration is deliberately year-agnostic because the
    sources adopt renames at different times and in different directions -- the
    panel led the UWM rename by a year in 2020 and lagged it in 2021, while the
    GSE seller field switched in between.

    Args:
        min_year: First activity year to read from the HMDA-side sources.
        max_year: Last activity year to read from the HMDA-side sources.

    Returns:
        One row per ``(lei, name)`` with the sources that supplied it.
    """
    parts = [
        _ts_names(min_year, max_year).with_columns(pl.lit("ts").alias("source")),
        _panel_names(min_year, max_year)
        .select("lei", pl.col("panel_name").alias("name"))
        .with_columns(pl.lit("panel").alias("source")),
        _avery_names().with_columns(pl.lit("avery").alias("source")),
    ]
    universe = (
        pl.concat(parts, how="diagonal_relaxed")
        .filter(pl.col("name").is_not_null() & (pl.col("name").str.len_chars() >= 3))
        .collect(engine="streaming")
        .group_by("lei", "name")
        .agg(pl.col("source").unique().sort().str.join("+").alias("sources"))
    )
    logger.info(
        "  name universe: %s (lei, name) pairs over %s LEIs",
        f"{len(universe):,}",
        f"{universe['lei'].n_unique():,}",
    )
    for row in (
        universe.group_by("sources").agg(pl.len().alias("n")).sort("n", descending=True).head(8)
    ).iter_rows(named=True):
        logger.info("    %s: %s", row["sources"], f"{row['n']:,}")
    return universe


def best_tier(candidate: str, universe: str) -> pl.Expr:
    """Best alignment tier between a candidate seller name and a universe name."""
    c_s, u_s = strip_legal(candidate), strip_legal(universe)
    return (
        pl.when(pl.col(candidate) == pl.col(universe))
        .then(pl.lit("T1_exact"))
        .when(c_s == u_s)
        .then(pl.lit("T2_stripped"))
        .when(
            (c_s.str.len_chars() >= 5)
            & (u_s.str.len_chars() >= 5)
            & (c_s.str.contains(u_s, literal=True) | u_s.str.contains(c_s, literal=True))
        )
        .then(pl.lit("T3_contains"))
        # tie-safe: list.first() is each name's first word, a positional token, not a row pick
        .when(c_s.str.split(" ").list.first() == u_s.str.split(" ").list.first())
        .then(pl.lit("T4_firstword"))
        .otherwise(pl.lit("T5_unaligned"))
    )
