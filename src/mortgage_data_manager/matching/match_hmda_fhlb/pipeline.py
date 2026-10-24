"""Pipeline orchestration for HMDA-FHLB matching: input preparation, validation and matching."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.matching.match_hmda_fhlb import (
    analysis,
    inputs,
    matching,
    validation,
)
from mortgage_data_manager.matching.match_hmda_fhlb.config import HMDAFHLBMatchingConfig

logger = get_logger(__name__)

#: Which part of the pipeline `run_matching` runs.
Step = Literal["all", "prepare", "match"]


def run_matching(
    max_year: int = 2025,
    *,
    pre2018: bool = True,
    post2018: bool = True,
    step: Step = "all",
    overwrite: bool = False,
    match_folder: Path | None = None,
) -> None:
    """Run the HMDA-FHLB matcher for either or both eras.

    Each era first prepares its inputs (the FHLB member list from the Avery lender
    panels, the cleaned FHLB acquisitions, and the members' HMDA loans), then matches
    them and logs the match statistics. Post-2018 also validates the prepared inputs
    before matching.

    Args:
        max_year: Last year to process.
        pre2018: Run the pre-2018 era.
        post2018: Run the post-2018 era.
        step: ``"all"``, or only ``"prepare"`` the inputs, or only ``"match"`` the
            already-prepared inputs.
        overwrite: Rebuild prepared inputs that already exist.
        match_folder: Directory for the prepared inputs and match results; defaults to
            the workflow's matching directory.
    """
    folder = (
        HMDAFHLBMatchingConfig.HMDA_FHLB_MATCHING_DIR
        if match_folder is None
        else Path(match_folder)
    )
    folder.mkdir(parents=True, exist_ok=True)

    if pre2018:
        if step in ("all", "prepare"):
            logger.info("--- Preparing pre-2018 inputs ---")
            inputs.get_fhlb_members_pre2018(folder, overwrite=overwrite)
            inputs.prepare_fhlb_pre2018(folder, max_year, overwrite=overwrite)
            inputs.prepare_hmda_pre2018(folder, overwrite=overwrite)
        if step in ("all", "match"):
            logger.info("--- Matching pre-2018 ---")
            matching.run_rounds_pre2018(folder)
            analysis.calculate_match_statistics_pre2018(folder)

    if post2018:
        if step in ("all", "prepare"):
            logger.info("--- Preparing post-2018 inputs ---")
            inputs.get_fhlb_members_post2018(folder, max_year, overwrite=overwrite)
            inputs.prepare_fhlb_post2018(folder, max_year, overwrite=overwrite)
            inputs.prepare_hmda_post2018(folder, max_year, overwrite=overwrite)
        if step in ("all", "match"):
            logger.info("--- Matching post-2018 ---")
            validation.validate_inputs(folder)
            matching.run_rounds_post2018(folder)
            analysis.calculate_match_statistics(folder)
