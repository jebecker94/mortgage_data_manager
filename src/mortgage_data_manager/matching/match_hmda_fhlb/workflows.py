"""FHLB Matching Pipeline Workflows.

This module provides the main pipeline orchestration functions for HMDA-FHLB matching.
It coordinates cleaning, validation, and matching processes.
"""

from __future__ import annotations

from pathlib import Path

from mortgage_data_manager.core.logging import get_logger

from . import analysis, cleaning, matching, validation
from .config import DATA_DIR

logger = get_logger(__name__)


def run_pipeline(
    match_folder: Path | None = None,
    max_year: int = 2024,
    run_pre2018: bool = True,
    run_post2018: bool = True,
    skip_cleaning: bool = False,
    overwrite: bool = False,
) -> None:
    """Run the full HMDA-FHLB matching pipeline.

    Args:
        match_folder: Output directory for match results. If None, uses default DATA_DIR.
        max_year: Maximum year to process (default: 2024)
        run_pre2018: Whether to run pre-2018 matching (default: True)
        run_post2018: Whether to run post-2018 matching (default: True)
        skip_cleaning: If True, skip data cleaning steps entirely (default: False)
        overwrite: If True, reprocess and overwrite existing cleaned files (default: False)
    """
    if match_folder is None:
        match_folder = DATA_DIR
    else:
        match_folder = Path(match_folder)

    # Ensure match folder exists
    match_folder.mkdir(parents=True, exist_ok=True)

    if run_pre2018:
        run_pre2018_pipeline(
            match_folder=match_folder,
            max_year=max_year,
            skip_cleaning=skip_cleaning,
            overwrite=overwrite,
        )

    if run_post2018:
        run_post2018_pipeline(
            match_folder=match_folder,
            max_year=max_year,
            skip_cleaning=skip_cleaning,
            overwrite=overwrite,
        )


def run_pre2018_pipeline(
    match_folder: Path | None = None,
    max_year: int = 2017,
    skip_cleaning: bool = False,
    overwrite: bool = False,
) -> None:
    """Run the pre-2018 HMDA-FHLB matching pipeline.

    Args:
        match_folder: Output directory for match results. If None, uses default DATA_DIR.
        max_year: Maximum year to process (default: 2017)
        skip_cleaning: If True, skip data cleaning steps entirely
        overwrite: If True, reprocess and overwrite existing cleaned files
    """
    if match_folder is None:
        match_folder = DATA_DIR
    else:
        match_folder = Path(match_folder)

    match_folder.mkdir(parents=True, exist_ok=True)

    logger.info("--- Starting Pre-2018 Pipeline ---")

    if not skip_cleaning:
        cleaning.get_fhlb_members_pre2018(match_folder, overwrite=overwrite)
        cleaning.clean_fhlb_data_pre2018(match_folder, max_year, overwrite=overwrite)
        cleaning.clean_hmda_data_pre2018(match_folder, overwrite=overwrite)

    matching.match_pre2018(match_folder)

    # Calculate statistics
    analysis.calculate_match_statistics_pre2018(match_folder)

    logger.info("--- Completed Pre-2018 Pipeline ---")


def run_post2018_pipeline(
    match_folder: Path | None = None,
    max_year: int = 2024,
    skip_cleaning: bool = False,
    overwrite: bool = False,
) -> None:
    """Run the post-2018 HMDA-FHLB matching pipeline.

    Args:
        match_folder: Output directory for match results. If None, uses default DATA_DIR.
        max_year: Maximum year to process (default: 2024)
        skip_cleaning: If True, skip data cleaning steps entirely
        overwrite: If True, reprocess and overwrite existing cleaned files
    """
    if match_folder is None:
        match_folder = DATA_DIR
    else:
        match_folder = Path(match_folder)

    match_folder.mkdir(parents=True, exist_ok=True)

    logger.info("--- Starting Post-2018 Pipeline ---")

    if not skip_cleaning:
        cleaning.get_fhlb_members_post2018(match_folder, max_year, overwrite=overwrite)
        cleaning.clean_fhlb_data_post2018(match_folder, max_year, overwrite=overwrite)
        cleaning.clean_hmda_data_post2018(match_folder, max_year, overwrite=overwrite)

    # Validate data before matching
    validation.validate_datasets(match_folder)

    df_matches = matching.match_post2018(match_folder)
    df_matches.write_parquet(match_folder / "fhlb_hmda_matches_post2018.parquet")
    df_matches.write_csv(match_folder / "fhlb_hmda_matches_post2018.csv")

    # Calculate and print match statistics
    analysis.calculate_match_statistics(match_folder)

    logger.info("--- Completed Post-2018 Pipeline ---")


def run_cleaning_only(
    match_folder: Path | None = None,
    max_year: int = 2024,
    run_pre2018: bool = True,
    run_post2018: bool = True,
    overwrite: bool = False,
) -> None:
    """Run only the data cleaning steps without matching.

    Args:
        match_folder: Output directory for cleaned data. If None, uses default DATA_DIR.
        max_year: Maximum year to process
        run_pre2018: Whether to clean pre-2018 data
        run_post2018: Whether to clean post-2018 data
        overwrite: If True, reprocess and overwrite existing cleaned files
    """
    if match_folder is None:
        match_folder = DATA_DIR
    else:
        match_folder = Path(match_folder)

    match_folder.mkdir(parents=True, exist_ok=True)

    if run_pre2018:
        logger.info("--- Cleaning Pre-2018 Data ---")
        cleaning.get_fhlb_members_pre2018(match_folder, overwrite=overwrite)
        cleaning.clean_fhlb_data_pre2018(match_folder, max_year, overwrite=overwrite)
        cleaning.clean_hmda_data_pre2018(match_folder, overwrite=overwrite)
        logger.info("--- Pre-2018 Cleaning Complete ---")

    if run_post2018:
        logger.info("--- Cleaning Post-2018 Data ---")
        cleaning.get_fhlb_members_post2018(match_folder, max_year, overwrite=overwrite)
        cleaning.clean_fhlb_data_post2018(match_folder, max_year, overwrite=overwrite)
        cleaning.clean_hmda_data_post2018(match_folder, max_year, overwrite=overwrite)
        logger.info("--- Post-2018 Cleaning Complete ---")


def run_matching_only(
    match_folder: Path | None = None, run_pre2018: bool = True, run_post2018: bool = True
) -> None:
    """Run only the matching steps (assumes cleaned data exists).

    Args:
        match_folder: Directory containing cleaned data. If None, uses default DATA_DIR.
        run_pre2018: Whether to run pre-2018 matching
        run_post2018: Whether to run post-2018 matching
    """
    if match_folder is None:
        match_folder = DATA_DIR
    else:
        match_folder = Path(match_folder)

    if run_pre2018:
        logger.info("--- Matching Pre-2018 Data ---")
        matching.match_pre2018(match_folder)
        analysis.calculate_match_statistics_pre2018(match_folder)
        logger.info("--- Pre-2018 Matching Complete ---")

    if run_post2018:
        logger.info("--- Matching Post-2018 Data ---")
        validation.validate_datasets(match_folder)
        df_matches = matching.match_post2018(match_folder)
        df_matches.write_parquet(match_folder / "fhlb_hmda_matches_post2018.parquet")
        df_matches.write_csv(match_folder / "fhlb_hmda_matches_post2018.csv")
        analysis.calculate_match_statistics(match_folder)
        logger.info("--- Post-2018 Matching Complete ---")
