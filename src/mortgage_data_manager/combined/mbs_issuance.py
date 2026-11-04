"""The ``mbs_issuance`` master: one row per security or pool at issuance."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import polars as pl

from mortgage_data_manager.combined._common import (
    Readers,
    _build_dataset,
    _date_to_yyyymmdd,
    _gnma_num,
    _plan_exprs,
    _union_schema,
    _write_partitioned,
)
from mortgage_data_manager.combined.mbs_issuance_schema import (
    GNMA_LEGACY_MBS_ISSUANCE_PLAN,
    GNMA_MBS_ISSUANCE_PLAN,
    GNMA_PLATINUM_MBS_ISSUANCE_PLAN,
    GSE_MBS_ISSUANCE_PLAN,
    NISSUES_U_PLAN,
    PLATINUM_WA_ISSUANCE_PLAN,
)
from mortgage_data_manager.combined.mbs_performance_schema import (
    GNMA_PLATINUM_HMBS_INDICATOR,
    GSE_MIRROR_SELLER_PREFIX,
    GSE_RESEC_SELLER_PREFIX,
    UMBS_ERA_START,
)
from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.dates import yyyymmdd_to_date
from mortgage_data_manager.core.harmonized import flag_to_bool
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.reference.gnma_pool_types import (
    GNMA_POOL_TYPES,
    GNMA_PROGRAM_BY_ISSUE_TYPE,
)

logger = get_logger(__name__)


# ===================== mbs_issuance (security / pool grain) =================
# One row per security/pool at issuance. GSE spine = umbs/silver/{FNMA,FHLMC}/IS
# (new-issuance-only; FNMA & FHLMC share an identical 99-col schema, as-published
# dollars/percent — no scaling). GNMA spine = gnma/silver/nimonSFPS PS record
# (already decoded; only DTI needs x100, a fraction), extended back to 2012-02 by
# the legacy nissues D pool header (single-family scope + the pre-2017 Platinum
# pool-of-pools rows) and joined by Platinum first observations from factorAplat.
# CUSIP is the universal key. The clean tri-agency window is 2020-01+ (GSE alone
# reaches 2019-06; GNMA alone reaches 2012-02) — filter `source_dataset` to pick a
# homogeneous window. Both GSEs' Issue Date is MMDDYYYY, and GNMA PS Original
# Aggregate Amount is already USD.

#: GNMA Pool Type codes that denote an ARM (security carries a margin).
_GNMA_ARM_POOL_TYPES: frozenset[str] = frozenset(
    c for c, pt in GNMA_POOL_TYPES.items() if pt.is_arm
)


#: GNMA Pool Type codes outside the single-family scope of the modern nimonSFPS PS
#: feed. The legacy nissues D record covers the whole Ginnie universe, so the
#: multifamily project and construction families are dropped to keep one scope
#: across the seam (they are 20,131 rows / $225.1B, and a multifamily issuance
#: master is separate work). Derived from the family axis rather than a literal set
#: so a new project code is picked up automatically.
_GNMA_PROJECT_POOL_TYPES: frozenset[str] = frozenset(
    c for c, pt in GNMA_POOL_TYPES.items() if pt.family in ("project", "construction")
)


#: GNMA Platinum (pool-of-pools) codes — a code space of their own, disjoint from
#: the base forward-MBS pool types. Rows carrying one are resecuritizations.
_GNMA_PLATINUM_POOL_TYPES: frozenset[str] = frozenset(
    c for c, pt in GNMA_POOL_TYPES.items() if pt.family == "platinum"
)


#: GNMA Pool Indicator -> issue_type enum (Appendix III-6 instr. 15).
_GNMA_ISSUE_TYPE_ENUM: dict[str, str] = {"X": "single_issuer", "C": "custom", "M": "multi_issuer"}


#: ``program`` value for the Platinum layer (the lowercase cross-master vocabulary).
_GNMA_PLATINUM_PROGRAM: str = "ginnie_platinum"


#: Fixed-point 9-fills that survive canonical decoding of the legacy nissues D
#: record and must be scrubbed to null by the assembler. The AOLS family has two
#: forms because D has two fixed-width eras (an 8-character field through 2015-03 and
#: a 10-character one from 2015-04), plus a 9-digit fill on 18 Platinum rows in
#: 2015-05..2016-01.
_NISSUES_RATE_SENTINEL: float = 99.999  # 5-char x1000 rates: security rate, WAC, WAGM


_NISSUES_SIZE_SENTINELS: tuple[float, ...] = (999999.99, 9999999.99, 99999999.99)  # AOLS


_NISSUES_MONTHS_SENTINEL: int = 999  # 3-char ints: WARM, WALA, WAOLT, LTV, CLTV


_NISSUES_SCORE_SENTINEL: int = 999  # 3-char int: U-record credit score


#: Plausible WA DTI percent. Outside it the U record holds junk (its 9.999 fill, 4,428
#: pools below 1% and 562 above 100%), while every other feed stays within 0-65.
_WA_DTI_RANGE: tuple[float, float] = (1.0, 100.0)


def _canon_slug(name: str) -> str:
    """Canonical snake_case id for a verbose GNMA column (drop parens; matches the schema id)."""
    s = re.sub(r"\([^)]*\)", " ", name)
    s = re.sub(r"[^0-9a-zA-Z]+", "_", s.strip().lower()).strip("_")
    return re.sub(r"_+", "_", s)


def _pick(names: list[str], available: set[str], dtype: pl.DataType | type[pl.DataType]) -> pl.Expr:
    """Coalesce the present source columns, or a typed null if none exist (vintage-robust).

    Falls back to the canonical snake_case ids (parens dropped) ONLY when none of the verbose
    names are present — so legacy verbose silver (GSE/factorA) is unaffected, while canonical
    GNMA silver (nimonSFPS/monthlySFPS, snake_case) resolves via the slug.

    Returns:
        A coalescing expression, or a typed null when no source column is present.
    """
    present = [n for n in names if n in available]
    if not present:
        present = [s for n in names if (s := _canon_slug(n)) in available]
    if not present:
        return pl.lit(None, dtype=dtype)
    return pl.coalesce([pl.col(n) for n in present]) if len(present) > 1 else pl.col(present[0])


def _pick_str(names: list[str], av: set[str]) -> pl.Expr:
    """`_pick` as a whitespace-trimmed String."""
    return _pick(names, av, pl.String).cast(pl.String).str.strip_chars()


def _pick_num(names: list[str], av: set[str]) -> pl.Expr:
    """`_pick` as Float64; a value that does not parse becomes null."""
    return _pick(names, av, pl.Float64).cast(pl.Float64, strict=False)


def _mbs_readers(av: set[str]) -> Readers:
    """The reader table the MBS leg plans name, over one source frame's columns.

    Args:
        av: Column names available on the source frame.

    Returns:
        Reader name -> builder from source aliases to an expression.
    """

    def pick(dtype: pl.DataType | type[pl.DataType]) -> Callable[[tuple[str, ...]], pl.Expr]:
        return lambda src: _pick(list(src), av, dtype)

    def text_num(src: tuple[str, ...]) -> pl.Expr:
        return _pick(list(src), av, pl.String).cast(pl.Float64, strict=False)

    def nissues(sentinel: float) -> Callable[[tuple[str, ...]], pl.Expr]:
        def read(src: tuple[str, ...]) -> pl.Expr:
            v = _pick(list(src), av, pl.Float64).cast(pl.Float64, strict=False)
            return pl.when(v != sentinel).then(v)

        return read

    def nissues_size(src: tuple[str, ...]) -> pl.Expr:
        v = _pick(list(src), av, pl.Float64).cast(pl.Float64, strict=False)
        return pl.when(~v.is_in(list(_NISSUES_SIZE_SENTINELS))).then(v)

    return {
        # a column every file of the feed carries, read without a fallback
        "col_str": lambda src: pl.col(src[0]).cast(pl.String),
        "col_str_strip": lambda src: pl.col(src[0]).cast(pl.String).str.strip_chars(),
        "str": lambda src: _pick_str(list(src), av),
        "str_cast": lambda src: _pick(list(src), av, pl.String).cast(pl.String),
        "raw_str": pick(pl.String),
        "raw_float": pick(pl.Float64),
        "raw_date": pick(pl.Date),
        "bool": pick(pl.Boolean),
        "num": lambda src: _pick_num(list(src), av),
        "int64": lambda src: _pick(list(src), av, pl.Int64).cast(pl.Int64, strict=False),
        "int32": lambda src: _pick(list(src), av, pl.Int64).cast(pl.Int32, strict=False),
        "ymd": lambda src: _date_to_yyyymmdd(_pick(list(src), av, pl.Date)),
        "ymd_text_date": lambda src: yyyymmdd_to_date(_pick(list(src), av, pl.String)),
        "yn": lambda src: flag_to_bool(_pick(list(src), av, pl.String)),
        "is_y": lambda src: _pick(list(src), av, pl.String) == "Y",
        "gnma_num": lambda src: _gnma_num(_pick(list(src), av, pl.Float64)),
        "gnma_num_keep0": lambda src: _gnma_num(_pick(list(src), av, pl.Float64), keep_zero=True),
        # numbers stored as text (GNMA PS)
        "ps_num": text_num,
        "ps_int64": lambda src: text_num(src).cast(pl.Int64, strict=False),
        "ps_int32": lambda src: text_num(src).cast(pl.Int32, strict=False),
        # legacy nissues fixed-point 9-fills
        "nissues_rate": nissues(_NISSUES_RATE_SENTINEL),
        "nissues_months": nissues(_NISSUES_MONTHS_SENTINEL),
        "nissues_size": nissues_size,
    }


def _gse_program(issue_date_ymd: pl.Expr) -> pl.Expr:
    """GSE ``program`` from the security's issue date: UMBS from 2019-06, else MBS.

    The Single Security Initiative went live 2019-06-03, so a GSE security issued on
    or after it is a UMBS and everything earlier (Fannie MBS, Freddie Gold PCs) is
    not. One rule for both security-grain masters, so they agree row for row.

    Returns:
        A String expression yielding ``UMBS`` or ``MBS``.
    """
    return (
        pl.when(issue_date_ymd >= UMBS_ERA_START)
        .then(pl.lit("umbs"))
        .otherwise(pl.lit("mbs"))
        .alias("program")
    )


def _gse_structure_flags(
    av: set[str], *, agency: str, issue_date_ymd: pl.Expr
) -> tuple[pl.Expr, pl.Expr]:
    """Classify a GSE security from the seller/servicer resecuritization marker.

    Both GSE feeds put a code rather than a firm in ``Seller Name`` **and**
    ``Servicer Name`` when a security is backed by other securities — see
    ``GSE_RESEC_SELLER_PREFIX`` for the evidence and the double-count warning it
    carries. Shared by ``mbs_issuance`` and ``mbs_performance`` so one rule produces
    one vocabulary on both grains.

    The marker sits in either name field (occasionally only the servicer side), so
    they are OR-ed. A row that blanks the whole descriptive block — a paid-off or
    cancelled security — yields NULL rather than a false ``pass_through``; the
    performance builder fills those from the pool's other months. At the issuance
    grain such a row is a placeholder with no face or loan count, published a month
    before the populated row of the same CUSIP or never followed by one, and
    ``mbs_issuance`` drops it.

    Args:
        av: Column names available on the source frame.
        agency: ``FNMA`` or ``FHLMC`` (selects the legacy resec class name).
        issue_date_ymd: The security's issue date as ``Int32`` ``YYYYMMDD``.

    Returns:
        ``(structure_class, is_resecuritization)`` expressions, unaliased.
    """
    names = [_pick_str([col], av).str.to_uppercase() for col in ("Seller Name", "Servicer Name")]
    block_present = pl.any_horizontal([n.is_not_null() & (n != "") for n in names])
    is_resec = pl.any_horizontal(
        [n.str.starts_with(GSE_RESEC_SELLER_PREFIX) for n in names]
    ).fill_null(value=False)
    is_mirror = pl.any_horizontal(
        [n.str.starts_with(GSE_MIRROR_SELLER_PREFIX) for n in names]
    ).fill_null(value=False)
    legacy_resec_class = "mega" if agency == "FNMA" else "giant"
    structure_class = (
        # Mirrors carry the underlying Gold PC's issue date (mostly 2018), so they
        # are classified by the marker itself, ahead of the era split.
        pl.when(~block_present)
        .then(pl.lit(None, dtype=pl.String))
        .when(is_mirror)
        .then(pl.lit("mirror"))
        .when(is_resec & (issue_date_ymd >= UMBS_ERA_START))
        .then(pl.lit("super"))
        .when(is_resec)
        .then(pl.lit(legacy_resec_class))
        .otherwise(pl.lit("pass_through"))
    )
    return structure_class, pl.when(block_present).then(is_resec)


def _assemble_gse_mbs_issuance(lf: pl.LazyFrame, *, agency: str, month: str) -> pl.LazyFrame:
    """Map one GSE IS file (FNMA or FHLMC — identical schema) to the unified grain.

    GSE IS date columns are already ``pl.Date`` in the umbs silver layer (parsed
    bronze->silver via ``core.dates``), so they are read straight through.

    ``source_dataset`` distinguishes the two physical feeds folded into this silver:
    the ``IS`` new-issuance file and its ``RIS`` republication, which the silver marks
    on ``_record_source``. ``record_source`` keeps its {original, correction} meaning.

    Returns:
        The file mapped to the unified ``mbs_issuance`` grain.
    """
    av = set(lf.collect_schema().names())
    read = _mbs_readers(av)
    issue_date = _pick(["Issue Date"], av, pl.Date)
    issue_date_ymd = _date_to_yyyymmdd(issue_date)
    structure_class, is_resec = _gse_structure_flags(
        av, agency=agency, issue_date_ymd=issue_date_ymd
    )
    margin = _pick_num(["WA Mortgage Margin"], av)
    index_present = _pick_str(["Index"], av)
    derived = {
        "agency": pl.lit(agency),
        "program": _gse_program(issue_date_ymd),
        "structure_class": structure_class,
        "as_of_month": pl.lit(int(month), dtype=pl.Int64),
        "is_arm": ((index_present.str.len_chars() > 0) | (margin > 0)).fill_null(value=False),
        "is_resecuritization": is_resec,
        "source_dataset": pl.when(_pick(["_record_source"], av, pl.String) == "correction")
        .then(pl.lit("RIS"))
        .otherwise(pl.lit("IS")),
        "issue_year": issue_date.dt.year().fill_null(int(month[:4])).cast(pl.Int64),
    }
    return lf.select(_plan_exprs(GSE_MBS_ISSUANCE_PLAN, read, derived)).filter(
        pl.col("structure_class").is_not_null()  # blank placeholder rows
    )


def _assemble_gnma_mbs_issuance(lf: pl.LazyFrame, *, month: str) -> pl.LazyFrame:
    """Map one GNMA nimonSFPS PS file to the unified mbs_issuance grain.

    Single-family, base pools only: the feed carries no multifamily and no Platinum,
    so every row is ``pass_through`` / ``is_resecuritization = False``. The pre-2020
    history comes from the legacy ``nissues`` D record instead.

    Returns:
        The file mapped to the unified ``mbs_issuance`` grain.
    """
    av = set(lf.collect_schema().names())
    read = _mbs_readers(av)
    pind = _pick_str(["Pool Indicator"], av)
    ptype = _pick_str(["Pool Type"], av)
    wagm = read["ps_num"](("WA Gross Margin (WAGM)",))
    derived = {
        "program": pind.replace_strict(GNMA_PROGRAM_BY_ISSUE_TYPE, default=None),
        "issue_type": pind.replace_strict(_GNMA_ISSUE_TYPE_ENUM, default=None),
        "as_of_month": pl.lit(int(month), dtype=pl.Int64),
        "wa_dti": read["ps_num"](("WA Debt to Income",)) * 100.0,
        "is_arm": (ptype.is_in(list(_GNMA_ARM_POOL_TYPES)) | (wagm > 0)).fill_null(value=False),
        "issue_year": read["ymd_text_date"](("Issue Date",))
        .dt.year()
        .fill_null(int(month[:4]))
        .cast(pl.Int64),
    }
    return lf.select(_plan_exprs(GNMA_MBS_ISSUANCE_PLAN, read, derived))


def _nissues_u_enrichment(u_file: Path) -> pl.LazyFrame:
    """Scan one ``nissues`` U file to the credit-score / DTI enrichment keyed on pool.

    The D (Pool Details) record carries no credit score and no DTI; the U record
    carries both for the same pool and month. Scored on the 16-month overlap with the
    modern feed: the credit score agrees with ``nimonSFPS`` PS **exactly** (max
    absolute difference 0.0 over 36,646 pairs), so it is carried without reservation.
    The DTI agrees to 0.05pp on 89.2% of pairs and to 5pp on 99.2% — close but not
    identical, because U reports the issuance-month weighted average and PS the
    current-month one, so it is carried as approximate rather than exact.

    Args:
        u_file: Path to one ``nissues_YYYYMM_U`` file.

    Returns:
        A LazyFrame keyed on ``pool_id`` with ``wa_credit_score`` / ``wa_dti``.
    """
    scan = pl.scan_parquet(str(u_file))
    av = set(scan.collect_schema().names())
    score = _pick_num(["weighted_average_credit_score"], av)
    dti = _pick_num(["weighted_average_debt_income_ratio"], av)
    derived = {
        "wa_credit_score": pl.when(score != _NISSUES_SCORE_SENTINEL).then(score),
        # U stores DTI as a fraction; x100 gives the percent the PS assembler emits.
        "wa_dti": pl.when((dti * 100.0).is_between(*_WA_DTI_RANGE)).then(dti * 100.0),
    }
    return (
        scan.select(_plan_exprs(NISSUES_U_PLAN, _mbs_readers(av), derived))
        .unique()
        .unique(subset=["pool_id"], keep="none")
    )


def _assemble_gnma_legacy_mbs_issuance(
    lf: pl.LazyFrame, *, month: str, u_file: Path | None = None
) -> pl.LazyFrame:
    """Map one legacy GNMA ``nissues`` D file to the unified mbs_issuance grain.

    ``nissues`` D is the pool-at-issuance header GNMA published before ``nimonSFPS``
    PS (2012-02 -> 2021-04, one row per newly-issued pool, no cross-month
    re-reporting). Its canonical silver is already decoded — real USD, percent and
    ``pl.Date`` — so the work here is sentinel scrubbing, scope and vocabulary.

    Scope: D covers the whole Ginnie universe, PS only single-family, so the
    multifamily project and construction families are dropped
    (`_GNMA_PROJECT_POOL_TYPES`). The Platinum pool-of-pools rows (``SP``/``JP``
    ``78xxxx`` pools, issuer 9999, zero loan count and pool UPB, gone from the feed
    after 2017-07) are KEPT, flagged as resecuritizations — they are the pre-2019
    Platinum issuance history that ``factorAplat`` cannot reach.

    Field agreement against PS over the 16-month overlap: ``original_face``,
    ``loan_count``, ``pass_through_rate``, ``wa_ltv``, ``wa_cltv``, ``avg_loan_size``,
    ``wa_loan_size``, ``issue_date``, ``maturity_date``, ``pool_type_raw``,
    ``issue_type``, ``program``, ``cusip`` and ``issuer_id`` are exact on 100.000% of
    37,023 pairs; ``pool_upb`` is within $1 on 100%; the term/WARM/WALA trio is within
    one month on >=99.99%.

    Args:
        lf: A scan over one ``nissues_YYYYMM_D`` file.
        month: The as-of ``YYYYMM`` (from the filename).
        u_file: Optional sibling ``nissues_YYYYMM_U`` file supplying credit score and
            DTI, which the D record does not carry.

    Returns:
        A LazyFrame at the canonical mbs_issuance grain.
    """
    av = set(lf.collect_schema().names())
    read = _mbs_readers(av)
    ptype = _pick_str(["pool_type"], av)
    pind = _pick_str(["pool_indicator"], av)
    is_platinum = ptype.is_in(list(_GNMA_PLATINUM_POOL_TYPES))
    wagm = read["nissues_rate"](("wagm",))
    loans = read["int64"](("number_of_loans_in_pool",))
    pool_upb = _pick(["pool_upb"], av, pl.Float64)
    derived = {
        "program": pl.when(is_platinum)
        .then(pl.lit(_GNMA_PLATINUM_PROGRAM))
        .otherwise(pind.replace_strict(GNMA_PROGRAM_BY_ISSUE_TYPE, default=None)),
        "structure_class": pl.when(is_platinum)
        .then(pl.lit("platinum"))
        .otherwise(pl.lit("pass_through")),
        "issue_type": pind.replace_strict(_GNMA_ISSUE_TYPE_ENUM, default=None),
        "as_of_month": pl.lit(int(month), dtype=pl.Int64),
        "loan_count": pl.when(loans > 0).then(loans),
        "pool_upb": pl.when(pool_upb > 0).then(pool_upb),
        "is_arm": (ptype.is_in(list(_GNMA_ARM_POOL_TYPES)) | (wagm > 0)).fill_null(value=False),
        "is_resecuritization": is_platinum,
        "issue_year": _pick(["pool_issue_date"], av, pl.Date)
        .dt.year()
        .fill_null(int(month[:4]))
        .cast(pl.Int64),
    }
    mapped = (
        lf.filter(~ptype.is_in(list(_GNMA_PROJECT_POOL_TYPES)))
        .select(_plan_exprs(GNMA_LEGACY_MBS_ISSUANCE_PLAN, read, derived))
        # 2015-11 carries one byte-identical duplicate row (pool 784062).
        .unique()
        .unique(subset=["pool_id", "as_of_month"], keep="none")
    )
    if u_file is not None and u_file.exists():
        return mapped.join(_nissues_u_enrichment(u_file), on="pool_id", how="left")
    return mapped.with_columns(
        pl.lit(None, dtype=pl.Float64).alias("wa_credit_score"),
        pl.lit(None, dtype=pl.Float64).alias("wa_dti"),
    )


def _gnma_platinum_wa_issuance(pps_file: Path, month: str) -> pl.LazyFrame:
    """Scan one ``platmonPPS`` PS file to the Platinum WA block, keyed on pool x month.

    ``platmonPPS`` PS is the Platinum analog of ``monthlySFPS`` PS and is already
    decoded. Its four "at Issuance" WA columns are unpopulated by design (null in
    every month bar a 2021-01/02 artifact that just repeats the security coupon), so
    the current-month block is what is read — which IS the issuance block here,
    because a Platinum's first ``factorAplat`` observation is its issue month.

    Args:
        pps_file: Path to one ``platmonPPS_YYYYMM_PS`` file.
        month: The as-of ``YYYYMM`` (the join key ``as_of_month``).

    Returns:
        A LazyFrame keyed on ``(pool_id, as_of_month)`` with the WA columns.
    """
    scan = pl.scan_parquet(str(pps_file))
    av = set(scan.collect_schema().names())
    derived = {
        "as_of_month": pl.lit(int(month), dtype=pl.Int64),
        "wa_dti": _pick_num(["wa_debt_to_income"], av) * 100.0,
    }
    return (
        scan.select(_plan_exprs(PLATINUM_WA_ISSUANCE_PLAN, _mbs_readers(av), derived))
        .unique()
        .unique(subset=["pool_id", "as_of_month"], keep="none")
    )


def _assemble_gnma_platinum_mbs_issuance(lf: pl.LazyFrame, *, month: str) -> pl.LazyFrame:
    """Map one ``factorAplat`` file to the Platinum leg of the mbs_issuance grain.

    GNMA publishes no Platinum new-issuance file, so a Platinum issuance record is a
    FIRST-OBSERVATION record — which the data licenses: for all 3,700 Platinum
    securities issued inside the ``factorAplat`` window the lag between the pool's
    issue month and the first month it appears is 0 months, 100.0% of the time, and
    the static fields (issue/maturity date, original face, coupon, pool type) do not
    move month to month. The caller reduces the concatenated months to the first
    observation per pool; this assembler only maps one month.

    HMBS Platinum is excluded by ``Pool Indicator`` rather than pool type
    (``GNMA_PLATINUM_HMBS_INDICATOR``), because Platinum pool types are a separate
    code space from the base-panel HMBS set. The WA block is left-joined from
    ``platmonPPS`` by the caller and so is not emitted here.

    Args:
        lf: A scan over one ``factorAplat_YYYYMM_ALL`` file.
        month: The as-of ``YYYYMM`` (from the filename).

    Returns:
        A LazyFrame at the canonical grain, minus the joined WA columns.
    """
    av = set(lf.collect_schema().names())
    cusip = _pick_str(["cusip_number"], av)
    ptype = _pick_str(["pool_type"], av)
    pind = _pick_str(["pool_indicator"], av)
    derived = {
        "program": pl.lit(_GNMA_PLATINUM_PROGRAM),
        "issue_type": pind.replace_strict(_GNMA_ISSUE_TYPE_ENUM, default=None),
        "as_of_month": pl.lit(int(month), dtype=pl.Int64),
        "is_arm": ptype.is_in(list(_GNMA_ARM_POOL_TYPES)).fill_null(value=False),
        "issue_year": _pick(["pool_issue_date"], av, pl.Date)
        .dt.year()
        .fill_null(int(month[:4]))
        .cast(pl.Int64),
    }
    return lf.filter((cusip != "") & (pind != GNMA_PLATINUM_HMBS_INDICATOR)).select(
        _plan_exprs(GNMA_PLATINUM_MBS_ISSUANCE_PLAN, _mbs_readers(av), derived)
    )


def _build_gnma_platinum_mbs_issuance(
    plat_dir: Path, pps_dir: Path, exclude_pools: set[str]
) -> pl.DataFrame:
    """Reduce the ``factorAplat`` panel to one issuance row per Platinum security.

    ``factorAplat`` is a portfolio file, not a new-issuance file, so the whole panel
    is read and collapsed to each security's first observed month, then enriched with
    that month's ``platmonPPS`` WA block.

    Securities whose first observation is NOT their issue month are pre-window
    (``factorAplat`` starts 2019-06): their static fields are still exact, but their
    balance, factor and WA block would be a mid-life snapshot — median factor 0.062 —
    so those point-in-time fields are nulled rather than persisted into an issuance
    master.

    Args:
        plat_dir: ``factorAplat/ALL`` silver directory.
        pps_dir: ``platmonPPS/PS`` silver directory.
        exclude_pools: Pool ids already carried by the legacy ``nissues`` D leg, which
            holds the true at-issuance record for the Platinum securities it covers.

    Returns:
        One row per Platinum security, at the canonical grain.
    """
    frames = [
        _assemble_gnma_platinum_mbs_issuance(pl.scan_parquet(str(f)), month=f.stem.split("_")[-2])
        for f in sorted(plat_dir.glob("*.parquet"))
    ]
    if not frames:
        return pl.DataFrame()
    first = (
        pl.concat(frames, how="diagonal_relaxed")
        .filter(~pl.col("pool_id").is_in(list(exclude_pools)))
        # each pool's first observation; a pool with two differing rows that month gets none
        .filter(pl.col("as_of_month") == pl.col("as_of_month").min().over("pool_id"))
        .unique()
        .unique(subset=["pool_id"], keep="none")
        .collect(engine="streaming")
    )
    wa_files = sorted(pps_dir.glob("*.parquet"))
    wa = (
        pl.concat(
            [_gnma_platinum_wa_issuance(f, f.stem.split("_")[-2]) for f in wa_files],
            how="diagonal_relaxed",
        ).collect(engine="streaming")
        if wa_files
        else None
    )
    if wa is not None:
        first = first.join(wa, on=["pool_id", "as_of_month"], how="left")
    issue_month = pl.col("issue_date").dt.year().cast(pl.Int64) * 100 + pl.col(
        "issue_date"
    ).dt.month().cast(pl.Int64)
    at_issuance = pl.col("as_of_month") == issue_month
    point_in_time = [
        c for c in ("current_face", "security_factor", "pool_upb") if c in first.columns
    ]
    return first.with_columns(
        [pl.when(at_issuance).then(pl.col(c)).alias(c) for c in point_in_time]
    )


def _log_gnma_issuance_seam(nissues_dir: Path, ps_dir: Path, ps_first: int) -> None:
    """Log whether the modern GNMA feed still covers everything the legacy one reports.

    The two feeds are joined by a month cutoff, not a row-level dedup, which is only
    sound while ``nimonSFPS`` PS is a superset of ``nissues`` D's single-family scope
    over their overlap — it was, on 37,023 of 37,023 pools by both keys. This logs the
    containment on every build so a coverage regression surfaces as a warning instead
    of silently duplicating pools if the cutoff ever moves.
    """
    overlap = [
        f for f in sorted(nissues_dir.glob("*.parquet")) if int(f.stem.split("_")[1]) >= ps_first
    ]
    if not overlap:
        return
    out_of_scope = list(_GNMA_PROJECT_POOL_TYPES | _GNMA_PLATINUM_POOL_TYPES)
    legacy = (
        pl.scan_parquet([str(f) for f in overlap])
        .filter(~pl.col("pool_type").str.strip_chars().is_in(out_of_scope))
        .select(pl.col("pool_number").str.strip_chars().alias("pool_id"))
        .unique()
    )
    months = {f.stem.split("_")[1] for f in overlap}
    ps_files = [f for f in sorted(ps_dir.glob("*.parquet")) if f.stem.split("_")[-2] in months]
    if not ps_files:
        return
    modern = (
        pl.scan_parquet([str(f) for f in ps_files])
        .select(pl.col("pool_id").cast(pl.String).str.strip_chars())
        .unique()
    )
    missing = (
        legacy.join(modern, on="pool_id", how="anti").select(pl.len()).collect(engine="streaming")
    ).item()
    if missing:
        logger.warning(
            "  GNMA seam: %d single-family pools in legacy nissues D over the %d-month overlap "
            "are absent from nimonSFPS PS; the month cutoff now loses them.",
            missing,
            len(months),
        )
    else:
        logger.info(
            "  GNMA seam: nimonSFPS PS covers every legacy single-family pool over the "
            "%d-month overlap (month cutoff is sufficient).",
            len(months),
        )


def build_mbs_issuance(*, overwrite: bool = False) -> pl.LazyFrame:
    """Build the tri-agency master MBS/pool issuance dataset (GNMA+FNMA+FHLMC).

    Spine = the per-agency single-class issuance records: GSE ``umbs/silver/
    {FNMA,FHLMC}/IS`` (new-issuance-only; identical 99-col schema) and GNMA
    ``gnma/silver/nimonSFPS`` PS pool headers (already decoded). One row per
    security/pool at issuance, keyed on CUSIP. Output is hive-partitioned by
    ``agency`` and ``issue_year`` under ``data/combined/mbs_issuance``.

    Two GNMA legs extend that spine, both tagged on ``source_dataset`` so a
    homogeneous window can be selected:

      * **``nissues`` D** — the legacy pool-at-issuance header, 2012-02 -> the month
        before ``nimonSFPS`` PS begins. Single-family scope plus the pre-2017-08
        Platinum pool-of-pools rows. The cutoff is computed from the PS file list
        rather than written as a literal, and PS is a strict superset of D's
        single-family scope across their 16-month overlap (37,023 of 37,023 pools,
        both keys), so no dedup branch is needed — see the assertion logged below.
      * **``factorAplat``** — one first-observation row per GNMA Platinum security,
        which for in-window issues IS the issuance month. Pools already carried by
        the ``nissues`` leg are skipped there, since that leg holds the true
        at-issuance record for them.

    **This master is a union of overlapping security layers.** Platinum securities and
    GSE Supers/Giants/Megas/Mirrors wrap collateral that is itself in the master, so
    any aggregate over balances or counts must filter ``is_resecuritization = False``
    unless the question is about the wrapper.

    Args:
        overwrite: Rebuild if the output already exists.

    Returns:
        A LazyFrame scanning the persisted combined output.
    """
    return _build_dataset("mbs_issuance", _write_mbs_issuance, overwrite=overwrite)


def _write_mbs_issuance(out_dir: Path) -> None:
    """Write the ``mbs_issuance`` tree into ``out_dir`` (see `build_mbs_issuance`)."""
    umbs = MortgageDataConfig.get_subpackage_data_dir("umbs") / "silver"
    gnma = MortgageDataConfig.get_subpackage_data_dir("gnma") / "silver"
    gnma_ps = gnma / "nimonSFPS" / "PS"
    gnma_nissues = gnma / "nissues" / "D"
    gnma_nissues_u = gnma / "nissues" / "U"
    gnma_plat = gnma / "factorAplat" / "ALL"
    gnma_plat_ps = gnma / "platmonPPS" / "PS"
    gse_dirs = {"FNMA": umbs / "FNMA" / "IS", "FHLMC": umbs / "FHLMC" / "IS"}

    # Canonical schema = union of every assembler's output (the GSE and modern GNMA
    # legs emit the same columns; the legacy and Platinum legs differ, and the union
    # both reconciles them and guards any future divergence).
    gse_sample = sorted(gse_dirs["FNMA"].glob("*.parquet"))
    gnma_sample = sorted(gnma_ps.glob("*.parquet"))
    legacy_sample = sorted(gnma_nissues.glob("*.parquet"))
    plat_sample = sorted(gnma_plat.glob("*.parquet"))
    samples = [
        _assemble_gse_mbs_issuance(
            pl.scan_parquet(str(gse_sample[-1])), agency="FNMA", month="209912"
        ),
        _assemble_gnma_mbs_issuance(pl.scan_parquet(str(gnma_sample[-1])), month="209912"),
    ]
    if legacy_sample:
        samples.append(
            _assemble_gnma_legacy_mbs_issuance(
                pl.scan_parquet(str(legacy_sample[-1])), month="209912"
            )
        )
    if plat_sample:
        samples.append(
            _assemble_gnma_platinum_mbs_issuance(
                pl.scan_parquet(str(plat_sample[-1])), month="209912"
            )
        )
    canon = _union_schema([s.collect_schema() for s in samples])

    logger.info("Building combined mbs_issuance (GNMA+FNMA+FHLMC) -> %s", out_dir)
    total = 0
    for agency, sdir in gse_dirs.items():
        files = sorted(sdir.glob("*.parquet"))
        logger.info("  %s: %d IS files", agency, len(files))
        for f in files:
            month = f.stem.split("_")[-1]  # IS_YYYYMM
            chunk = _assemble_gse_mbs_issuance(
                pl.scan_parquet(str(f)), agency=agency, month=month
            ).collect(engine="streaming")
            total += _write_partitioned(
                chunk, canon, out_dir, "issue_year", agency, f"{agency.lower()}_{month}"
            )
    gnma_files = sorted(gnma_ps.glob("*.parquet"))
    # The legacy feed stops where the modern one starts. Read the boundary from the
    # PS file list rather than writing it as a literal, so a re-download that extends
    # PS backwards moves the seam instead of duplicating months.
    ps_first = min((int(f.stem.split("_")[-2]) for f in gnma_files), default=0)
    legacy_files = [
        f for f in sorted(gnma_nissues.glob("*.parquet")) if int(f.stem.split("_")[1]) < ps_first
    ]
    logger.info(
        "  GNMA: %d nimonSFPS PS files (from %d) + %d legacy nissues D files",
        len(gnma_files),
        ps_first,
        len(legacy_files),
    )
    for f in gnma_files:
        month = f.stem.split("_")[-2]  # nimonSFPS_YYYYMM_PS
        chunk = _assemble_gnma_mbs_issuance(pl.scan_parquet(str(f)), month=month).collect(
            engine="streaming"
        )
        total += _write_partitioned(chunk, canon, out_dir, "issue_year", "GNMA", f"gnma_{month}")
    _log_gnma_issuance_seam(gnma_nissues, gnma_ps, ps_first)
    legacy_platinum_pools: set[str] = set()
    for f in legacy_files:
        month = f.stem.split("_")[1]  # nissues_YYYYMM_D
        chunk = _assemble_gnma_legacy_mbs_issuance(
            pl.scan_parquet(str(f)),
            month=month,
            u_file=gnma_nissues_u / f"nissues_{month}_U.parquet",
        ).collect(engine="streaming")
        legacy_platinum_pools.update(
            chunk.filter(pl.col("is_resecuritization"))["pool_id"].to_list()
        )
        total += _write_partitioned(
            chunk, canon, out_dir, "issue_year", "GNMA", f"gnma_nissues_{month}"
        )
    if plat_sample:
        plat = _build_gnma_platinum_mbs_issuance(gnma_plat, gnma_plat_ps, legacy_platinum_pools)
        logger.info(
            "  GNMA Platinum: %d securities from factorAplat (%d already in the legacy leg)",
            plat.height,
            len(legacy_platinum_pools),
        )
        total += _write_partitioned(plat, canon, out_dir, "issue_year", "GNMA", "gnma_platinum")

    logger.info("mbs_issuance built: %d rows (GNMA+FNMA+FHLMC) -> %s", total, out_dir)
