"""Data guard: FHA HECM servicer/NMLS identifiers stay well-formed in the real silver.

One published HECM workbook delivered ``Current Servicer ID``, ``Previous Servicer ID``
and ``NMLS`` as an Excel linear-trend fill dragged down from its first few genuine rows,
so 10,121 values were a function of row position rather than of the loan. They gave
themselves away by being negative or fractional -- neither of which an identifier can be
-- and the bronze cleaner now nulls exactly those (``_null_fabricated_hecm_identifiers``).

The class cannot be caught by a cast-loss detector: the cells are perfectly valid
numbers, just invented. So the guard is on the property itself. A failure means either a
new vintage ships the same defect (the rule regressed or was bypassed) or a partition was
built by an older bronze -- rebuild the affected bronze snapshot and silver partition.

Width is the third signal: excluding that vintage, every servicer ID is a 5-digit HUD
mortgagee number plus a 5-digit branch, so 10 digits is the ceiling. An 11-digit value is
a filled series escaping again.

Skips when the data volume is not mounted.
"""

from __future__ import annotations

import polars as pl
import pytest

from mortgage_data_manager.sources.fha.config import FHAConfig

_HECM_SILVER = FHAConfig.FHA_SILVER_DIR / "hecm"

#: Silver stores the servicer IDs as strings (leading zeros survive); NMLS stays numeric.
_SERVICER_ID_COLUMNS = ("Current Servicer ID", "Previous Servicer ID")

#: 5-digit HUD mortgagee number + 5-digit branch.
_MAX_SERVICER_ID_DIGITS = 10


def _hecm_silver() -> pl.LazyFrame:
    return pl.scan_parquet(str(_HECM_SILVER / "**" / "*.parquet"), hive_partitioning=True)


@pytest.mark.skipif(not _HECM_SILVER.exists(), reason="FHA HECM silver not mounted")
def test_hecm_servicer_ids_are_well_formed_identifiers():
    lf = _hecm_silver()
    counts = lf.select(
        [
            expr
            for column in _SERVICER_ID_COLUMNS
            for expr in (
                pl.col(column).str.starts_with("-").sum().alias(f"{column}|negative"),
                pl.col(column).str.contains(".", literal=True).sum().alias(f"{column}|fractional"),
                (pl.col(column).str.len_chars() > _MAX_SERVICER_ID_DIGITS)
                .sum()
                .alias(f"{column}|too_wide"),
            )
        ]
    ).collect()

    offenders = {name: n for name, n in counts.row(0, named=True).items() if n}
    assert not offenders, (
        f"HECM silver servicer IDs are malformed: {offenders} — an identifier cannot be "
        f"negative, fractional or wider than {_MAX_SERVICER_ID_DIGITS} digits. Rebuild the "
        f"offending bronze snapshot (the cleaner nulls these) and its silver partition."
    )


@pytest.mark.skipif(not _HECM_SILVER.exists(), reason="FHA HECM silver not mounted")
def test_hecm_nmls_is_non_negative():
    n = _hecm_silver().select((pl.col("NMLS") < 0).sum()).collect().item()
    assert n == 0, (
        f"HECM silver holds {n:,} negative NMLS values — an NMLS ID cannot be negative. "
        f"Rebuild the offending bronze snapshot and its silver partition."
    )
