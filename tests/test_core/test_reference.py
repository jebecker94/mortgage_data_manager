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


@pytest.mark.parametrize(
    ("code", "family"),
    [
        ("SF", "single_family"),
        ("AR", "arm"),
        ("MH", "manufactured"),
        ("CL", "construction"),
        ("ZZ", "other"),
        (None, "other"),
    ],
)
def test_pool_type_family(code, family):
    assert pool_type_family(code) == family


def test_pool_type_is_arm_case_insensitive():
    assert pool_type_is_arm("AR") is True
    assert pool_type_is_arm("rl") is True   # case-insensitive
    assert pool_type_is_arm("SF") is False
    assert pool_type_is_arm(None) is False


def test_issue_type_to_program_ginnie_ii_custom_is_ii():
    assert issue_type_to_program("X") == "GNMA_I"
    assert issue_type_to_program("C") == "GNMA_II"  # Custom is Ginnie II, not I (the classic trap)
    assert issue_type_to_program("m") == "GNMA_II"  # case-insensitive
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
