"""Data guard: the pruned combined ``loan_issuance`` columns stay unpopulated at source.

The 2026-07-09 schema audit pruned output columns whose every source layer was verified
never-populated (``PRUNED_ALWAYS_NULL_FIELDS`` in ``combined.loan_issuance_schema``). These
tests re-verify that evidence against the CURRENT silver data: if a disclosure vintage ever
starts populating one of those source columns, the matching test FAILS — the signal to
un-prune the field (delete its registry row; its plan entry still documents the wiring) and
rebuild ``loan_issuance``.

A field may be dead in the CURRENT vintages yet populated in an older era -- the audit read
2016-forward silver, and importing FNMA's 2000-2015 history found one such field. Those are
listed in :data:`_LEGACY_ERA_FIELDS` with the year they go dead, and checked from that year
forward only: pruning is about what consumers query, so the load-bearing claim is that the
field stays dead, not that it never lived.

Everything reads parquet FOOTER statistics only (fast, no data), except the GNMA check —
``Seller Issuer ID`` is blank-padded (physically non-null), so footers cannot see it and the
newest few months are actually scanned.

All tests skip when the data volume is not mounted.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import pytest

from mortgage_data_manager.combined.loan_issuance_schema import PRUNED_ALWAYS_NULL_FIELDS
from mortgage_data_manager.core.config import MortgageDataConfig

_FNMA_SF_DIR = MortgageDataConfig.get_subpackage_data_dir("fnma") / "silver" / "issuances"
_UMBS_SILVER = MortgageDataConfig.get_subpackage_data_dir("umbs") / "silver"
_GNMA_L_DIR = MortgageDataConfig.get_subpackage_data_dir("gnma") / "silver" / "dailyllmni" / "L"

#: Pruned fields whose source is populated-with-zeros rather than null (guard: max == 0).
_ALL_ZERO_FIELDS = frozenset({"upb_at_removal"})

#: Pruned fields the FNMA SF *back history* populates, mapped to the first year the field
#: is dead. All four describe one phenomenon, on ONE set of 392 loans spread over
#: 2003Q1-2011Q3: pre-2012 acquisition files carried zero-balance / removal detail for loans
#: already terminated in their first reported month (repurchases), so an *issuance* row --
#: first observation per loan -- could arrive already bearing a zero-balance code, its
#: effective date, the UPB at removal, and the make-whole flag. Fannie stopped after 2011Q3;
#: nothing since. The 2026-07-09 audit read only 2016-forward silver, so it saw
#: never-populated (or, for ``upb_at_removal``, populated-with-zeros) and pruned on that
#: basis. Importing the 2000-2015 history (2026-08-01) exposed the gap.
#:
#: Pruning stays correct -- these are dead across every vintage a consumer queries, and
#: carrying four columns for 392 loans last seen in 2011 is not worth a schema change plus a
#: loan_issuance rebuild. So the guard narrows rather than mutes: it asserts they stay dead
#: from the cutoff FORWARD, which is the claim that actually protects the pruning decision.
#: A new disclosure vintage populating any of them still fails, as intended.
_LEGACY_ERA_FIELDS: dict[str, int] = {
    "repurchase_make_whole_proceeds_flag": 2012,
    "upb_at_removal": 2012,
    "zero_balance_code": 2012,
    "zero_balance_effective_date": 2012,
}


def _footer_stats(files: list[Path], columns: set[str]) -> dict[str, dict[str, object]]:
    """Aggregate footer statistics for ``columns`` across ``files`` (no data read).

    Returns per-column ``{rows, nulls, max}`` — a column absent from every file simply
    does not appear (absent counts as unpopulated).
    """
    acc: dict[str, dict[str, object]] = {}
    for f in files:
        md = pq.ParquetFile(f).metadata
        for rg in range(md.num_row_groups):
            g = md.row_group(rg)
            for ci in range(g.num_columns):
                col = g.column(ci)
                name = col.path_in_schema.split(".")[0]
                if name not in columns:
                    continue
                st = col.statistics
                entry = acc.setdefault(name, {"rows": 0, "nulls": 0, "max": None})
                entry["rows"] += g.num_rows
                if st is not None and st.has_null_count:
                    entry["nulls"] += st.null_count
                if st is not None and st.has_min_max and st.max is not None:
                    entry["max"] = st.max if entry["max"] is None else max(entry["max"], st.max)
    return acc


def _quarters_from(files: list[Path], year: int) -> list[Path]:
    """Quarterly files at or after ``year`` (stems are ``YYYYQ[1-4]``, optionally ``EXCL``)."""
    return [f for f in files if f.stem[:4].isdigit() and int(f.stem[:4]) >= year]


def _assert_unpopulated(
    stats: dict[str, dict[str, object]], aliases: list[str], field: str, since: int | None = None
) -> None:
    """A pruned field's source aliases must be absent, all-null, or (flagged) all-zero.

    Args:
        stats: Footer statistics to check.
        aliases: Source column names for the field.
        field: Pruned field name, for the failure message.
        since: When set, ``stats`` covers only quarters from this year forward -- the
            field is known-populated before it (see :data:`_LEGACY_ERA_FIELDS`), so the
            message says so rather than implying it was never populated at all.
    """
    for alias in aliases:
        if alias not in stats:
            continue  # absent from every file = unpopulated
        entry = stats[alias]
        if entry["nulls"] >= entry["rows"]:
            continue
        if field in _ALL_ZERO_FIELDS and not entry["max"]:
            continue  # populated but every value is 0 — the audited state
        window = f" in {since}-forward quarters" if since else ""
        pytest.fail(
            f"pruned field {field!r}: source column {alias!r} now has "
            f"{entry['rows'] - entry['nulls']:,}/{entry['rows']:,} non-null values"
            f"{window} (max={entry['max']!r}) — un-prune it in PRUNED_ALWAYS_NULL_FIELDS "
            f"and rebuild"
        )


@pytest.mark.skipif(not _FNMA_SF_DIR.exists(), reason="FNMA SF issuance silver not mounted")
def test_fnma_sf_pruned_sources_stay_unpopulated():
    files = sorted(_FNMA_SF_DIR.glob("**/*.parquet"))
    assert files, f"no parquet files under {_FNMA_SF_DIR}"
    wanted = {a for f in PRUNED_ALWAYS_NULL_FIELDS for a in f["fnma_sf"]}
    stats = _footer_stats(files, wanted)
    era_stats: dict[int, dict[str, dict[str, object]]] = {}
    for field in PRUNED_ALWAYS_NULL_FIELDS:
        cutoff = _LEGACY_ERA_FIELDS.get(field["name"])
        if cutoff is None:
            _assert_unpopulated(stats, field["fnma_sf"], field["name"])
            continue
        if cutoff not in era_stats:
            era_stats[cutoff] = _footer_stats(_quarters_from(files, cutoff), wanted)
        _assert_unpopulated(era_stats[cutoff], field["fnma_sf"], field["name"], since=cutoff)


@pytest.mark.skipif(not _UMBS_SILVER.exists(), reason="UMBS silver not mounted")
def test_illd_pruned_sources_stay_unpopulated():
    files = [
        f
        for gse in ("FNMA", "FHLMC")
        for f in sorted((_UMBS_SILVER / gse / "ILLD").rglob("*.parquet"))
    ]
    assert files, f"no ILLD parquet files under {_UMBS_SILVER}"
    # the D-6 first-month snapshots feed the umbs-only pass too — include when available
    from mortgage_data_manager.matching.match_mbs_umbs.config import MBSUMBSMatchingConfig

    for gse in ("fnma", "fhlmc"):
        snap = MBSUMBSMatchingConfig.get_umbs_snapshot_file(gse)
        if snap is not None:
            files.append(snap)
    wanted = {a for f in PRUNED_ALWAYS_NULL_FIELDS for a in f["il"]}
    stats = _footer_stats(files, wanted)
    for field in PRUNED_ALWAYS_NULL_FIELDS:
        _assert_unpopulated(stats, field["il"], field["name"])


@pytest.mark.skipif(not _GNMA_L_DIR.exists(), reason="GNMA dailyllmni silver not mounted")
def test_gnma_pruned_sources_stay_blank():
    # Blank-padded strings are physically non-null, so footers can't see them — scan the
    # newest few months for real (cheap: one column each).
    files = sorted(_GNMA_L_DIR.glob("dailyllmni_*_L.parquet"))[-3:]
    assert files, f"no dailyllmni L files under {_GNMA_L_DIR}"
    gnma_cols = {a for f in PRUNED_ALWAYS_NULL_FIELDS for a in f["gnma"]}
    for f in files:
        lf = pl.scan_parquet(f)
        present = [c for c in gnma_cols if c in lf.collect_schema().names()]
        if not present:
            continue
        counts = lf.select(
            [
                (pl.col(c).cast(pl.String).str.strip_chars().str.len_chars() > 0).sum().alias(c)
                for c in present
            ]
        ).collect()
        for c in present:
            n = counts[c][0]
            assert n == 0, (
                f"pruned GNMA source column {c!r} now has {n:,} non-blank values in "
                f"{f.name} — un-prune it (restore the assembler expression) and rebuild"
            )
