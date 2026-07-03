"""Configuration for FHA-GNMA matching workflow.

This module provides configuration management for the FHA-GNMA matching subpackage,
inheriting from the base MortgageDataConfig and MatchingConfig.

Paths, tolerances, and mappings for matching FHA endorsement records
with GNMA loan-level disclosure data (dailyllmni).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.matching.config import MatchingConfig


class FHAGNMAMatchingConfig(MatchingConfig):
    """FHA-GNMA matching specific configuration."""

    # Input data directories (from other subpackages)
    FHA_SILVER_DIR: Path = Path(
        config(
            "FHA_SILVER_DIR",
            default=str(
                MortgageDataConfig.get_subpackage_data_dir("fha")
                / "silver"
                / "single_family"
            ),
        )
    )
    GNMA_SILVER_DIR: Path = Path(
        config(
            "GNMA_SILVER_DIR",
            default=str(
                MortgageDataConfig.get_subpackage_data_dir("gnma")
                / "silver"
                / "dailyllmni"
                / "L"
            ),
        )
    )

    # FHA-GNMA matching output directories (data/matching/fha_gnma/)
    FHA_GNMA_MATCHING_DIR: Path = MatchingConfig.get_matching_type_dir("fha_gnma")
    FHA_GNMA_OUTPUT_DIR: Path = Path(
        config(
            "FHA_GNMA_OUTPUT_DIR",
            default=str(FHA_GNMA_MATCHING_DIR / "output"),
        )
    )
    FHA_GNMA_INTERMEDIATE_DIR: Path = FHA_GNMA_MATCHING_DIR / "intermediate"

    # Crosswalk output directory (crosswalk/fha_gnma/)
    FHA_GNMA_CROSSWALK_OUTPUT_DIR: Path = MatchingConfig.get_crosswalk_type_dir("fha_gnma")

    # Year range for matching
    # GNMA Loan Origination Date available from April 2015+
    MIN_YEAR: int = int(config("FHA_GNMA_MIN_YEAR", default="2015"))
    MAX_YEAR: int = int(config("FHA_GNMA_MAX_YEAR", default="2025"))

    # Initial filter for DC-only pilot (fast iteration)
    # Set to None to run all states
    PILOT_STATE: str | None = "DC"

    @classmethod
    def ensure_directories(cls) -> None:
        """Create necessary FHA-GNMA matching directories if they don't exist."""
        dirs = [
            cls.FHA_GNMA_MATCHING_DIR,
            cls.FHA_GNMA_OUTPUT_DIR,
            cls.FHA_GNMA_INTERMEDIATE_DIR,
            cls.FHA_GNMA_CROSSWALK_OUTPUT_DIR,
        ]
        for dir_path in dirs:
            dir_path.mkdir(parents=True, exist_ok=True)


# =============================================================================
# Matching Tolerances
# =============================================================================


@dataclass
class MatchingTolerances:
    """Configuration for FHA-GNMA matching tolerances.

    Two-stage tolerance system (following match_mbs_fhlb/gnma_match.py pattern):
    1. Candidate tolerances: Used during initial matching to find potential pairs
    2. Post-match tolerances: Stricter filters applied after mutual best-match selection

    Month tolerance: FHA endorsement typically occurs 0-2 months after GNMA origination.
    Based on analysis: 74% within 0-1 months, 85% within 0-3 months.

    Attributes:
        amount_tolerance: Loan amount tolerance in dollars for candidate matching
        rate_tolerance: Interest rate tolerance in percentage points for candidate matching
        month_tolerance_min: Minimum month difference (FHA month - GNMA month). Typically 0.
        month_tolerance_max: Maximum month difference (FHA month - GNMA month). Typically 1-2.
        post_amount_tolerance: Stricter loan amount tolerance for post-match filtering
        post_rate_tolerance: Stricter interest rate tolerance for post-match filtering
        apply_post_match_filter: Whether to apply post-match filtering (default: True)
    """

    # Candidate-finding tolerances (looser)
    amount_tolerance: float = 2500.0  # dollars
    rate_tolerance: float = 0.25  # percentage points

    # Month tolerance: FHA month - GNMA month should be in [min, max]
    # FHA endorsement typically 0-2 months after GNMA origination
    month_tolerance_min: int = 0
    month_tolerance_max: int = 2

    # Post-match tolerances (stricter)
    post_amount_tolerance: float = 1000.0  # dollars
    post_rate_tolerance: float = 0.125  # percentage points

    # Behavior
    apply_post_match_filter: bool = True

    def __post_init__(self):
        """Validate tolerances."""
        if self.post_amount_tolerance > self.amount_tolerance:
            raise ValueError("post_amount_tolerance must be <= amount_tolerance")
        if self.post_rate_tolerance > self.rate_tolerance:
            raise ValueError("post_rate_tolerance must be <= rate_tolerance")


# Round configurations
# GNMA TRUNCATES loan amounts to thousands (per documentation), NOT rounds.
# So: GNMA = floor(FHA / 1000) * 1000
# This means: diff = FHA - GNMA = FHA mod 1000, always in range [0, 999]
# We use asymmetric tolerance: diff must be >= 0 and < 1000
AMOUNT_TOLERANCE_MIN = 0  # GNMA can't be higher than FHA (truncation)
AMOUNT_TOLERANCE_MAX = 999  # Max diff is 999 (e.g., FHA=123999, GNMA=123000)

# Round 1: Strict tolerances for high-confidence matches
ROUND1_TOLERANCES = MatchingTolerances(
    amount_tolerance=999.0,  # Will be used asymmetrically in matching.py
    rate_tolerance=0.0625,
    month_tolerance_min=0,
    month_tolerance_max=1,
    post_amount_tolerance=999.0,
    post_rate_tolerance=0.01,
)

# Round 2: Slightly relaxed month tolerance
ROUND2_TOLERANCES = MatchingTolerances(
    amount_tolerance=999.0,
    rate_tolerance=0.125,
    month_tolerance_min=-1,
    month_tolerance_max=3,
    post_amount_tolerance=999.0,
    post_rate_tolerance=0.0625,
)


# =============================================================================
# FHA to GNMA Mappings
# =============================================================================

# FHA Loan Purpose to GNMA Loan Purpose mapping
# GNMA: 1=Purchase, 2=Refi (rate/term), 3=Refi (cash-out), 4=Construction
# FHA: "Purchase", "Refi_FHA", "Refi_Conv_Curr"
FHA_TO_GNMA_PURPOSE = {
    "Purchase": [1],  # Purchase maps to GNMA 1
    "Refi_FHA": [2, 3],  # FHA refi can be rate/term or cash-out
    "Refi_Conv_Curr": [2, 3],  # Conv refi can be rate/term or cash-out
}

# Reverse mapping for matching: which FHA purposes are compatible with GNMA purpose
GNMA_PURPOSE_COMPATIBLE = {
    1: ["Purchase"],  # GNMA Purchase only matches FHA Purchase
    2: ["Refi_FHA", "Refi_Conv_Curr"],  # GNMA Rate/Term matches FHA refis
    3: ["Refi_FHA", "Refi_Conv_Curr"],  # GNMA Cash-out matches FHA refis
    4: [],  # Construction - no FHA equivalent in our data
}

# FHA Product Type to ARM indicator
# "Fixed Rate" -> 0, "Adjustable Rate" -> 1
FHA_ARM_MAPPING = {
    "Fixed Rate": 0,
    "Adjustable Rate": 1,
}


# =============================================================================
# State FIPS Code Mapping (for reference)
# =============================================================================

STATE_FIPS = {
    "AL": 1, "AK": 2, "AZ": 4, "AR": 5, "CA": 6, "CO": 8, "CT": 9, "DE": 10,
    "DC": 11, "FL": 12, "GA": 13, "HI": 15, "ID": 16, "IL": 17, "IN": 18,
    "IA": 19, "KS": 20, "KY": 21, "LA": 22, "ME": 23, "MD": 24, "MA": 25,
    "MI": 26, "MN": 27, "MS": 28, "MO": 29, "MT": 30, "NE": 31, "NV": 32,
    "NH": 33, "NJ": 34, "NM": 35, "NY": 36, "NC": 37, "ND": 38, "OH": 39,
    "OK": 40, "OR": 41, "PA": 42, "RI": 44, "SC": 45, "SD": 46, "TN": 47,
    "TX": 48, "UT": 49, "VT": 50, "VA": 51, "WA": 53, "WV": 54, "WI": 55,
    "WY": 56, "PR": 72, "VI": 78, "GU": 66, "AS": 60, "MP": 69,
}


# =============================================================================
# Module-level exports for backward compatibility
# =============================================================================

PROJECT_DIR: Path = FHAGNMAMatchingConfig.PROJECT_DIR
DATA_DIR: Path = FHAGNMAMatchingConfig.DATA_DIR
FHA_SILVER_DIR: Path = FHAGNMAMatchingConfig.FHA_SILVER_DIR
GNMA_SILVER_DIR: Path = FHAGNMAMatchingConfig.GNMA_SILVER_DIR
MATCHING_DIR: Path = FHAGNMAMatchingConfig.FHA_GNMA_MATCHING_DIR
OUTPUT_DIR: Path = FHAGNMAMatchingConfig.FHA_GNMA_OUTPUT_DIR
INTERMEDIATE_DIR: Path = FHAGNMAMatchingConfig.FHA_GNMA_INTERMEDIATE_DIR
CROSSWALK_OUTPUT_DIR: Path = FHAGNMAMatchingConfig.FHA_GNMA_CROSSWALK_OUTPUT_DIR
MIN_YEAR: int = FHAGNMAMatchingConfig.MIN_YEAR
MAX_YEAR: int = FHAGNMAMatchingConfig.MAX_YEAR
PILOT_STATE: str | None = FHAGNMAMatchingConfig.PILOT_STATE


def ensure_directories() -> None:
    """Create output directories if they don't exist."""
    FHAGNMAMatchingConfig.ensure_directories()
