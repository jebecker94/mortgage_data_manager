"""Unit tests for the curated reference lookups/predicates in ``core.reference``.

Cover the state FIPS tables and the vintage-bridge predicate whose ``early < CUT <= late``
boundary silently drops cross-year census-tract joins if the ``<`` / ``<=`` is ever swapped.
"""

from __future__ import annotations

import polars as pl

from mortgage_data_manager.core.reference.census_tract import needs_vintage_bridge, tract_geoid
from mortgage_data_manager.core.reference.states import (
    FIPS_INT_TO_STATE,
    FIPS_TO_STATE,
    STATE_TO_FIPS,
    TERRITORIES,
)
from mortgage_data_manager.core.reference.zip_tract import tract_vintage


def test_needs_vintage_bridge_boundary_at_2022():
    assert needs_vintage_bridge(2021, 2022) is True
    assert needs_vintage_bridge(2022, 2022) is False
    assert needs_vintage_bridge(2020, 2021) is False


def test_state_fips_tables_are_one_table():
    # 50 states + DC + the five inhabited territories, with both derived views round-tripping.
    assert len(STATE_TO_FIPS) == 56
    assert TERRITORIES < set(STATE_TO_FIPS)
    assert {FIPS_TO_STATE[f"{v:02d}"] for v in STATE_TO_FIPS.values()} == set(STATE_TO_FIPS)
    assert FIPS_INT_TO_STATE[11] == "DC" and FIPS_TO_STATE["72"] == "PR"


# ---------------------------------------------------------------------------
# tract_geoid: one helper replacing the matchers' copies. Each case below is pinned to
# the output the replaced copy produced (hmda_fhfa pre/post-2018, fhfa_mbs, hmda_gse).
# ---------------------------------------------------------------------------


def test_tract_geoid_matches_the_replaced_copies():
    df = pl.DataFrame(
        {
            "s": ["06", "01", None, "36"],
            "c": ["037", "001", "001", "061"],
            "t": ["520301", "000100", "000100", None],
        }
    )
    old_post2018 = (
        pl.col("s").cast(pl.Int64).cast(pl.Utf8).str.zfill(2)
        + pl.col("c").cast(pl.Int64).cast(pl.Utf8).str.zfill(3)
        + pl.col("t").cast(pl.Int64).cast(pl.Utf8).str.zfill(6)
    )
    old_pre2018 = (
        pl.col("s").cast(pl.Utf8).str.zfill(2)
        + pl.col("c").cast(pl.Utf8).str.zfill(3)
        + pl.col("t").cast(pl.Utf8).str.zfill(6)
    )
    old_fhfa_mbs = pl.col("s") + pl.col("c") + pl.col("t")
    out = df.select(
        tract_geoid("s", "c", "t").alias("new"),
        old_post2018.alias("post"),
        old_pre2018.alias("pre"),
        old_fhfa_mbs.alias("mbs"),
    )
    assert out["new"].to_list() == ["06037520301", "01001000100", None, None]
    for old in ("post", "pre", "mbs"):
        assert out["new"].equals(out[old]), old


def test_tract_geoid_numeric_parts_and_one_part_form():
    df = pl.DataFrame({"s": [6], "c": [37], "t": [520301.0], "geoid": [6037520301]})
    out = df.select(tract_geoid("s", "c", "t").alias("a"), tract_geoid("geoid").alias("b"))
    assert out.row(0) == ("06037520301", "06037520301")


def test_tract_geoid_keeps_an_overwide_part_whole():
    # A 7-digit tract (eight 2013 FHLB AMA rows) must not borrow a county digit.
    df = pl.DataFrame({"s": [45], "c": [39], "t": [1714000]})
    assert df.select(tract_geoid("s", "c", "t")).item() == "450391714000"


def test_tract_vintage_boundaries():
    assert [tract_vintage(y) for y in (2011, 2012, 2021, 2022)] == [2000, 2010, 2010, 2020]
