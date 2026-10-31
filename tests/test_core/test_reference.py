"""Unit tests for the curated reference lookups/predicates in ``core.reference``.

Cover the state FIPS tables and the vintage-bridge predicate whose ``early < CUT <= late``
boundary silently drops cross-year census-tract joins if the ``<`` / ``<=`` is ever swapped.
"""

from __future__ import annotations

from mortgage_data_manager.core.reference.census_tract import needs_vintage_bridge
from mortgage_data_manager.core.reference.states import (
    FIPS_INT_TO_STATE,
    FIPS_TO_STATE,
    STATE_TO_FIPS,
    TERRITORIES,
)


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
