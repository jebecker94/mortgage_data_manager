"""Post-2018 seller-purchaser matching workflow.

This module provides entry points for the post-2018 HMDA seller-purchaser
matching workflow using the configuration-driven matching engine.

Usage:
    from mortgage_data_manager.matching.match_hmda_sellers_and_purchasers.match_post2018 import (
        run_all_rounds,
        run_round,
    )

    # Run all 8 rounds
    run_all_rounds(data_folder, save_folder, min_year=2018, max_year=2024)

    # Run specific rounds
    run_all_rounds(data_folder, save_folder, rounds=[1, 2, 5])

    # Run a single round
    run_round(1, data_folder, save_folder, min_year=2018, max_year=2024)
"""

from __future__ import annotations

from pathlib import Path

from mortgage_data_manager.matching.match_hmda_sellers_and_purchasers.config import (
    MAX_YEAR_POST2018,
    MIN_YEAR_POST2018,
)
from mortgage_data_manager.matching.match_hmda_sellers_and_purchasers.config.post2018_rounds import (
    POST2018_ROUNDS,
)
from mortgage_data_manager.matching.match_hmda_sellers_and_purchasers.matching_engine import (
    run_all_rounds,
    run_matching_round,
)

# Build a lookup dict for round configs by number
_ROUND_CONFIGS = {config.round_number: config for config in POST2018_ROUNDS}


def run_round(
    round_num: int,
    data_folder: str | Path,
    save_folder: str | Path,
    min_year: int = MIN_YEAR_POST2018,
    max_year: int = MAX_YEAR_POST2018,
) -> None:
    """Run a single matching round by number.

    Args:
        round_num: Round number to run (1-8).
        data_folder: Directory containing HMDA parquet files.
        save_folder: Directory to save match crosswalk outputs.
        min_year: Minimum activity year to process. Defaults to ``MIN_YEAR_POST2018``.
        max_year: Maximum activity year to process. Defaults to ``MAX_YEAR_POST2018``.

    Raises:
        ValueError: If round_num is not a valid round number.
    """
    if round_num not in _ROUND_CONFIGS:
        valid_rounds = sorted(_ROUND_CONFIGS.keys())
        raise ValueError(f"Invalid round {round_num}. Valid rounds: {valid_rounds}")

    config = _ROUND_CONFIGS[round_num]
    run_matching_round(config, Path(data_folder), Path(save_folder), min_year, max_year)


__all__ = [
    "run_all_rounds",
    "run_matching_round",
    "run_round",
    "POST2018_ROUNDS",
]
