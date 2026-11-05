"""FHLB-GNMA Matching Logic using Polars.

Matches GNMA loan-level new-issuance data (the dailyllmni / dailyllmnipd silver) with
FHLB AMA (Acquired Member Assets) data to identify loans that were
acquired by Federal Home Loan Banks from GNMA issuers.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import atomic_output
from mortgage_data_manager.core.reference.states import STATE_TO_FIPS
from mortgage_data_manager.matching.match_fhlb_mbs.config import FHLBMBSMatchingConfig as Config
from mortgage_data_manager.matching.match_fhlb_mbs.rounds import ROUNDS, MatchRound, Tolerances
from mortgage_data_manager.matching.resolve import drop_ambiguous, mutual_best
from mortgage_data_manager.sources.gnma.load import scan_new_issuance

logger = get_logger(__name__)


def load_gnma(
    silver_dir: Path | None = None,
    min_year: int = 2009,
    max_year: int = 2025,
    issuer_id: int | None = 3937,
) -> pl.DataFrame:
    """Load GNMA new-issuance loans through `sources.gnma.load.scan_new_issuance`.

    Args:
        silver_dir: GNMA silver root; None for ``GNMAConfig.GNMA_SILVER_DIR``.
        min_year: Minimum year to include (based on First Payment Date)
        max_year: Maximum year to include
        issuer_id: Filter to specific issuer ID (3937 is common for FHLB matching)

    Returns:
        GNMA loan-level rows, one per file row, for the issuer and years.
    """
    lf = scan_new_issuance(silver_dir=silver_dir)
    if issuer_id is not None:
        lf = lf.filter(pl.col("issuer_id").cast(pl.Int64, strict=False) == issuer_id)
    # Filter by first payment date year (present in every file, unlike origination date).
    # First payment is typically 1 month after origination, so this is a good proxy
    fp_year = pl.col("first_payment_date").dt.year().cast(pl.Int64, strict=False)
    df = lf.filter((fp_year >= min_year) & (fp_year <= max_year + 1)).collect(engine="streaming")
    logger.info("GNMA loans (issuer %s, %d-%d): %d", issuer_id, min_year, max_year, len(df))
    return df


def load_fhlb(
    fhlb_dir: Path,
    min_year: int = 2009,
    max_year: int = 2025,
) -> pl.DataFrame:
    """Load FHLB AMA silver data.

    Args:
        fhlb_dir: Directory containing FHLB AMA silver parquet files
        min_year: Minimum year to include
        max_year: Maximum year to include

    Returns:
        Combined FHLB AMA data
    """
    parquet_files = sorted(fhlb_dir.glob("fhlb_ama_*.parquet"))

    if not parquet_files:
        raise FileNotFoundError(f"No parquet files found in {fhlb_dir}")

    # Filter to relevant years
    year_files = [f for f in parquet_files if min_year <= int(f.stem.split("_")[-1]) <= max_year]

    logger.info("Loading %d FHLB AMA files (%d-%d)...", len(year_files), min_year, max_year)

    # Use diagonal_relaxed to handle schema evolution across years
    df = pl.concat([pl.read_parquet(f) for f in year_files], how="diagonal_relaxed")

    logger.info("  Total rows loaded: %d", len(df))

    # Filter to government mortgage types (FHA=1, VA=2, RD=3)
    df = df.filter(pl.col("MortgageType").is_in([1, 2, 3]))
    logger.info("  After mortgage type filter (FHA/VA/RD): %d", len(df))

    return df


def prepare_gnma(df: pl.DataFrame) -> pl.DataFrame:
    """Prepare GNMA data for matching by selecting and transforming columns.

    Args:
        df: Raw GNMA silver data

    Returns:
        Prepared GNMA data with standardized columns
    """
    select_cols = [
        pl.col("disclosure_sequence_number").alias("gnma_loan_id"),
        pl.col("agency"),
        pl.col("loan_purpose"),
        pl.col("refinance_type"),
        pl.col("first_payment_date"),
        pl.col("loan_interest_rate").alias("interest_rate"),
        pl.col("original_principal_balance").alias("original_balance"),
        pl.col("original_loan_term_in_months").alias("original_term"),
        pl.col("loan_to_value").alias("ltv"),
        pl.col("combined_ltv").alias("cltv"),
        pl.col("total_debt_expense_ratio_percent").alias("dti"),
        pl.col("credit_score"),
        pl.col("number_of_borrowers").alias("num_borrowers"),
        pl.col("first_time_home_buyer").alias("first_time_buyer"),
        pl.col("property_type"),
        pl.col("state"),
        pl.col("upfront_mip"),
        pl.col("annual_mip"),
    ]
    # Files before April 2015 carry no origination date.
    if "loan_origination_date" in df.columns:
        select_cols.append(pl.col("loan_origination_date").alias("origination_date"))

    df = df.select(select_cols)

    # Convert to numeric types
    df = df.with_columns(
        [
            # Interest rate: stored as basis points (e.g., 4500 = 4.5%)
            pl.col("interest_rate").cast(pl.Float64, strict=False),
            pl.col("original_balance").cast(pl.Float64, strict=False),
            pl.col("original_term").cast(pl.Int64, strict=False),
            pl.col("ltv").cast(pl.Float64, strict=False),
            pl.col("cltv").cast(pl.Float64, strict=False),
            pl.col("dti").cast(pl.Float64, strict=False),
            pl.col("credit_score").cast(pl.Int64, strict=False),
            pl.col("num_borrowers").cast(pl.Int64, strict=False),
            pl.col("loan_purpose").cast(pl.Int64, strict=False),
        ]
    )

    # New originations only: Ginnie's loan-level layout codes Loan Purpose 3 / 4 as loan
    # modifications and 5 as re-performing, and neither is an FHLB AMA acquisition.
    df = df.filter(pl.col("loan_purpose").is_in([1, 2]))

    # Add State FIPS code
    state_fips_expr = (
        pl.col("state").replace_strict(STATE_TO_FIPS, default=None).alias("state_fips")
    )
    df = df.with_columns(state_fips_expr)

    # Map agency codes: F=FHA=1, V=VA=2, R=RD=3
    df = df.with_columns(
        pl.when(pl.col("agency") == "F")
        .then(1)
        .when(pl.col("agency") == "V")
        .then(2)
        .when(pl.col("agency") == "R")
        .then(3)
        .otherwise(None)
        .alias("agency_code")
    )

    # Derive origination year
    # Use Loan Origination Date when available (exists in files from 2015+)
    # Fall back to first_payment_date year when origination_date is null
    fp_year = pl.col("first_payment_date").dt.year().cast(pl.Int64, strict=False)
    if "origination_date" in df.columns:
        df = df.with_columns(
            pl.coalesce(
                pl.col("origination_date").dt.year().cast(pl.Int64, strict=False),
                fp_year,
            ).alias("origination_year")
        )
    else:
        # No origination date column available, use first payment date
        df = df.with_columns(fp_year.alias("origination_year"))

    # Map property type to unit count (1-4 for single family, etc.)
    df = df.with_columns(
        pl.col("property_type").cast(pl.Int64, strict=False).alias("property_units")
    )

    return df


def prepare_fhlb(df: pl.DataFrame) -> pl.DataFrame:
    """Prepare FHLB AMA data for matching by selecting and transforming columns.

    Args:
        df: Raw FHLB AMA silver data

    Returns:
        Prepared FHLB data with standardized columns
    """
    df = df.select(
        [
            pl.col("LoanCharacteristicsID").alias("fhlb_loan_id"),
            pl.col("FIPSStateNumericCode").alias("state_fips"),
            pl.col("LTVRatioPercent").alias("ltv"),
            pl.col("NoteDate").alias("note_year"),
            pl.col("LoanAcquisitionDate").alias("acquisition_year"),
            pl.col("LoanPurposeType").alias("loan_purpose"),
            pl.col("MortgageType").alias("mortgage_type"),  # 1=FHA, 2=VA, 3=RD
            pl.col("LoanAmortizationMaxTermMonths").alias("original_term"),
            pl.col("BorrowerCount").alias("num_borrowers"),
            pl.col("BorrowerFirstTimeHomebuyer").alias("first_time_buyer"),
            pl.col("PropertyUnitCount").alias("property_units"),
            pl.col("NoteRatePercent").alias("interest_rate"),  # as percent (e.g., 4.5)
            pl.col("NoteAmount").alias("note_amount"),  # in dollars
            pl.col("TotalDebtExpenseRatioPercent").alias("dti"),
            pl.col("Borrower1CreditScoreValue").alias("credit_score_1"),
            pl.col("Borrower2CreditScoreValue").alias("credit_score_2"),
            pl.col("PMICoveragePercent").alias("pmi_coverage"),
            pl.col("Bank").alias("fhlb_bank"),
        ]
    )

    # Filter out certain banks that don't participate in this program
    df = df.filter(~pl.col("fhlb_bank").is_in(["Indianapolis", "Cincinnati"]))

    return df


def _gap(gnma_col: str, fhlb_col: str) -> pl.Expr:
    """Absolute GNMA-FHLB gap on one field."""
    return (pl.col(gnma_col) - pl.col(fhlb_col)).abs()


def _within(tolerances: Tolerances, *, strict_rate: bool = False) -> pl.Expr:
    """Every gap inside ``tolerances`` (LTV and DTI pass when either side is missing)."""
    rate = _gap("interest_rate", "interest_rate_fhlb")
    ltv = _gap("ltv", "ltv_fhlb")
    dti = _gap("dti", "dti_fhlb")
    expr = (
        (rate < tolerances.rate if strict_rate else rate <= tolerances.rate)
        & (_gap("original_balance", "note_amount") <= tolerances.balance)
        & (ltv.is_null() | (ltv <= tolerances.ltv))
        & (dti.is_null() | (dti <= tolerances.dti))
    )
    if tolerances.term is not None:
        expr = expr & (_gap("original_term", "original_term_fhlb") <= tolerances.term)
    return expr


def match_fhlb_gnma(
    gnma_df: pl.DataFrame, fhlb_df: pl.DataFrame, round_: MatchRound
) -> pl.DataFrame:
    """Match GNMA loans to FHLB AMA acquisitions under one round.

    Joins on the round's exact keys, keeps candidates inside its candidate tolerances with a
    compatible loan purpose, takes the mutual best on a score (the sum of the rate, balance,
    LTV and DTI gaps, each scaled by its candidate tolerance; a missing LTV or DTI adds
    nothing), drops every tie, and applies the round's post-match tolerances.

    GNMA canonical silver is decoded to real units at build, so it matches the FHLB scale
    on every field (rate %, balance $, LTV/DTI %) and no conversion is applied.

    Args:
        gnma_df: Prepared GNMA data.
        fhlb_df: Prepared FHLB data.
        round_: The round to run.

    Returns:
        Matched loan pairs with all relevant columns.
    """
    logger.info("Starting match: %d GNMA loans, %d FHLB loans", len(gnma_df), len(fhlb_df))
    gnma_keys = [g for g, _ in round_.keys]
    fhlb_keys = [f for _, f in round_.keys]
    fhlb_df = fhlb_df.with_columns(
        pl.col(f).cast(gnma_df.schema[g])
        for g, f in round_.keys
        if fhlb_df.schema[f] != gnma_df.schema[g]
    )
    merged = gnma_df.join(
        fhlb_df, left_on=gnma_keys, right_on=fhlb_keys, how="inner", suffix="_fhlb"
    )
    logger.info("  After exact-key join: %d candidate pairs", len(merged))

    # GNMA: 1=Purchase, 2=Refinance (3/4/5 modifications / re-performing dropped at prep)
    # FHLB: 1=Purchase, 2=Refi (rate/term), 6=Refi (cash-out)
    purpose_ok = ~((pl.col("loan_purpose") == 1) & pl.col("loan_purpose_fhlb").is_in([2, 6])) & ~(
        (pl.col("loan_purpose") == 2) & (pl.col("loan_purpose_fhlb") == 1)
    )
    merged = merged.filter(_within(round_.candidate) & purpose_ok)
    logger.info("  After tolerances and purpose: %d pairs", len(merged))

    tol = round_.candidate
    merged = merged.with_columns(
        (
            _gap("interest_rate", "interest_rate_fhlb") / tol.rate
            + _gap("original_balance", "note_amount") / tol.balance
            + (_gap("dti", "dti_fhlb") / tol.dti).fill_null(0)
            + (_gap("ltv", "ltv_fhlb") / tol.ltv).fill_null(0)
        ).alias("match_score")
    )
    # Mutual best on the score, then drop every key still tied: a tie at the best score is
    # not evidence for either candidate, so the loan stays unmatched.
    matches = drop_ambiguous(
        mutual_best(merged, "match_score", "gnma_loan_id", "fhlb_loan_id"),
        "gnma_loan_id",
        "fhlb_loan_id",
    )
    logger.info("  Mutual best matches: %d", len(matches))

    if round_.post is not None:
        before = len(matches)
        matches = matches.filter(_within(round_.post, strict_rate=True))
        logger.info(
            "  After post-match filter: %d (dropped %d)", len(matches), before - len(matches)
        )
    return matches


def run_rounds(
    rounds: tuple[MatchRound, ...], gnma_df: pl.DataFrame, fhlb_df: pl.DataFrame
) -> pl.DataFrame:
    """Run ``rounds`` in order, each on the loans earlier rounds left unmatched.

    Args:
        rounds: The rounds to run (`ROUNDS` in production).
        gnma_df: Prepared GNMA data.
        fhlb_df: Prepared FHLB data.

    Returns:
        Matched pairs with all relevant columns and ``match_round``.
    """
    pieces: list[pl.DataFrame] = []
    for number, round_ in enumerate(rounds, start=1):
        logger.info("Round %d: %s", number, round_.description)
        if pieces:
            claimed = pl.concat(pieces, how="diagonal_relaxed")
            gnma_df = gnma_df.join(claimed.select("gnma_loan_id"), on="gnma_loan_id", how="anti")
            fhlb_df = fhlb_df.join(claimed.select("fhlb_loan_id"), on="fhlb_loan_id", how="anti")
        matches = match_fhlb_gnma(gnma_df, fhlb_df, round_)
        pieces.append(matches.with_columns(pl.lit(number, dtype=pl.UInt8).alias("match_round")))
    return pl.concat(pieces, how="diagonal_relaxed").sort("gnma_loan_id", "fhlb_loan_id")


def run_matching(
    min_year: int = 2009,
    max_year: int = 2025,
    issuer_id: int | None = 3937,
) -> pl.DataFrame:
    """Run the FHLB-GNMA matching rounds (`ROUNDS`) and write the crosswalk.

    Args:
        min_year: Minimum year for data.
        max_year: Maximum year for data.
        issuer_id: GNMA issuer ID to filter (3937 is common); None keeps every issuer.

    Returns:
        Matched loan pairs with all relevant columns.
    """
    Config.ensure_directories()
    gnma_dir = Config.get_gnma_silver_dir()
    fhlb_dir = Config.get_fhlb_ama_silver_dir()
    logger.info("GNMA data directory: %s", gnma_dir)
    logger.info("FHLB data directory: %s", fhlb_dir)

    gnma_prepared = prepare_gnma(load_gnma(gnma_dir, min_year, max_year, issuer_id))
    fhlb_prepared = prepare_fhlb(load_fhlb(fhlb_dir, min_year, max_year))
    matches = run_rounds(ROUNDS, gnma_prepared, fhlb_prepared)

    crosswalk = matches.select("gnma_loan_id", "fhlb_loan_id", "match_round")
    crosswalk_file = Config.FHLB_MBS_CROSSWALK_DIR / "fhlb_mbs_crosswalk_gnma.parquet"
    with atomic_output(crosswalk_file) as tmp:
        crosswalk.write_parquet(tmp)
    logger.info("Crosswalk saved to: %s", crosswalk_file)

    # Full match details to intermediate (read by validation)
    details_file = Config.FHLB_MBS_INTERMEDIATE_DIR / "fhlb_mbs_match_details_gnma.parquet"
    matches.write_parquet(details_file)
    logger.info("Match details saved to: %s", details_file)
    return matches
