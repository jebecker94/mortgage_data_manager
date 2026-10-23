"""Unit tests for the curated reference lookups/predicates in ``core.reference``.

Cover the GNMA pool-type taxonomy (hand-authored from MBS Guide Appendix III-6) and the two
vintage-bridge predicates whose ``early < CUT <= late`` boundary silently drops cross-year
census-tract joins if the ``<`` / ``<=`` is ever swapped.
"""

from __future__ import annotations

import pytest

from mortgage_data_manager.core.reference.census_tract import needs_vintage_bridge
from mortgage_data_manager.core.reference.ct_planning_region import needs_ct_planreg_bridge
from mortgage_data_manager.core.reference.gnma_pool_types import (
    issue_type_to_program,
    pool_type_family,
    pool_type_is_arm,
)
from mortgage_data_manager.core.reference.states import (
    FIPS_INT_TO_STATE,
    FIPS_TO_STATE,
    STATE_TO_FIPS,
    TERRITORIES,
)


@pytest.mark.parametrize(
    ("code", "family"),
    [
        ("SF", "single_family"),
        ("AR", "arm"),
        ("MH", "manufactured"),
        ("CL", "construction"),
        ("SP", "platinum"),  # Platinum is its own code space, not a retired SF code
        ("ZZ", "other"),
        (None, "other"),
    ],
)
def test_pool_type_family(code, family):
    assert pool_type_family(code) == family


def test_pool_type_is_arm_case_insensitive():
    assert pool_type_is_arm("AR") is True
    assert pool_type_is_arm("rl") is True  # case-insensitive
    assert pool_type_is_arm("SF") is False
    assert pool_type_is_arm(None) is False


def test_issue_type_to_program_ginnie_ii_custom_is_ii():
    assert issue_type_to_program("X") == "ginnie_i"
    assert issue_type_to_program("C") == "ginnie_ii"  # Custom is Ginnie II, not I (classic trap)
    assert issue_type_to_program("m") == "ginnie_ii"  # case-insensitive
    assert issue_type_to_program(None) is None
    assert issue_type_to_program("Z") is None


def test_needs_ct_planreg_bridge_boundary_at_2024():
    assert needs_ct_planreg_bridge(2023, 2024) is True
    assert needs_ct_planreg_bridge(2024, 2024) is False
    assert needs_ct_planreg_bridge(2022, 2023) is False


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
