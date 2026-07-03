"""Direct HMDA-UMBS matching for unmatched GSE-sold loans.

This module implements direct matching between HMDA and UMBS ILLD data for loans that:
1. Have purchaser_type = 1 (FNMA) or 3 (FHLMC) in HMDA (reported as sold to GSEs)
2. Are NOT matched in the existing HMDA-FHFA-MBS-UMBS chain crosswalk
3. Pass quality filters (first lien, 1-4 units, purchase/refi, conforming)

Key Challenge:
UMBS ILLD has no census tract - only state-level geography. This eliminates the strongest
match key used in FHFA-HMDA matching. We compensate with:
- Stricter blocking on loan amount + interest rate
- Lender-seller crosswalk to constrain match candidates
- Processing by state to manage memory

Three-phase matching approach:
- Phase 1: All channels with lender-seller constraint
- Phase 2: Correspondent loans without constraint
- Phase 3: Correspondent chains using purchaser LEI
"""

from __future__ import annotations

# Standard library imports
from typing import Literal

# Third-party imports
import numpy as np
import polars as pl

# Local application imports
from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.matching.match_hmda_mbs.config import HMDAMBSMatchingConfig as Config

logger = get_logger(__name__)


def _get_best_file_type_for_year(year: int) -> str:
    """Determine the most mature file_type available for a given year."""
    year_dir = Config.HMDA_SILVER_DIR / f"activity_year={year}"
    if not year_dir.exists():
        return "c"

    lf = pl.scan_parquet(str(year_dir / "**/*.parquet"))
    available_types = lf.select("file_type").unique().collect()["file_type"].to_list()

    for ft in ["a", "b", "c"]:
        if ft in available_types:
            return ft
    return "c"


def _load_lender_seller_crosswalk() -> pl.DataFrame:
    """Load lender-seller crosswalk mapping LEI to valid seller names."""
    logger.info("Loading lender-seller crosswalk...")

    path = Config.HMDA_MBS_OUTPUT_DIR / "lender_seller_good_matches.parquet"
    df = pl.read_parquet(path)

    crosswalk = df.select(["lei", "seller_name"]).unique()

    logger.info(f"  Loaded {len(crosswalk):,} LEI-seller pairs")
    return crosswalk


def _load_seller_purchaser_crosswalk(min_year: int, max_year: int) -> pl.DataFrame:
    """Load seller-purchaser crosswalk linking originations to purchase records."""
    logger.info("Loading seller-purchaser crosswalk...")

    crosswalk = pl.scan_parquet(str(Config.SELLER_PURCHASER_CROSSWALK_DIR / "**/*.parquet"))

    crosswalk = crosswalk.filter(
        (pl.col("activity_year_s") >= min_year) & (pl.col("activity_year_s") <= max_year)
    )

    df = crosswalk.sort("MatchRound").group_by("HMDAIndex_s").first().collect()

    logger.info(f"  Loaded {len(df):,} origination-purchase pairs")
    return df


def _load_phase3_candidates(
    min_year: int,
    max_year: int,
    agency: Literal["fnma", "fhlmc"],
    sp_xwalk: pl.DataFrame,
    already_matched: pl.DataFrame,
) -> pl.DataFrame:
    """Load Phase 3 candidate loans: originations where correspondent sold to GSE.

    These are loans where:
    1. Originator sold to correspondent (purchaser_type 6, 71, etc.)
    2. Correspondent later sold to GSE (purchaser_type 1 or 3)
    3. We have a seller/purchaser crosswalk link between them
    4. The origination is not already matched
    """
    logger.info("Loading Phase 3 candidates (correspondent -> GSE chains)...")

    purchaser_type = 1 if agency == "fnma" else 3

    frames = []
    for year in range(min_year, max_year + 1):
        year_dir = Config.HMDA_SILVER_DIR / f"activity_year={year}"
        if not year_dir.exists():
            continue

        best_ft = _get_best_file_type_for_year(year)
        limit = Config.CONFORMING_LIMITS.get(year, 1_000_000)

        # Load purchase records that sold to this agency's GSE
        hmda_p = pl.scan_parquet(str(year_dir / "**/*.parquet"))
        hmda_p = (
            hmda_p.filter(
                (pl.col("file_type") == best_ft)
                & (pl.col("action_taken") == 6)
                & (pl.col("purchaser_type") == purchaser_type)
            )
            .select(["HMDAIndex", "lei"])
            .collect()
        )

        # Join to crosswalk to get origination IDs
        xwalk_year = sp_xwalk.filter(pl.col("activity_year_s") == year)
        gse_pairs = xwalk_year.join(
            hmda_p.select(
                [pl.col("HMDAIndex").alias("HMDAIndex_p"), pl.col("lei").alias("lei_purchaser")]
            ),
            on="HMDAIndex_p",
            how="inner",
        )

        if len(gse_pairs) == 0:
            continue

        # Load originations that match our criteria
        hmda_s = pl.scan_parquet(str(year_dir / "**/*.parquet"))
        hmda_s = (
            hmda_s.filter(
                (pl.col("file_type") == best_ft)
                & (pl.col("action_taken") == 1)
                & (pl.col("loan_type") == 1)
                & (pl.col("lien_status") == 1)
                & (pl.col("total_units") <= 4)
                & (pl.col("loan_purpose").is_in([1, 31, 32]))
                & (pl.col("loan_amount") <= limit)
                & (pl.col("initially_payable_to_institution") == 2)
            )
            .select(
                [
                    "HMDAIndex",
                    "activity_year",
                    "lei",
                    "state_code",
                    "loan_amount",
                    "interest_rate",
                    "loan_term",
                    "loan_purpose",
                    "occupancy_type",
                    "total_units",
                    "rate_spread",
                    "initially_payable_to_institution",
                ]
            )
            .collect()
        )

        # Join to get originations where correspondent sold to GSE
        candidates = hmda_s.join(
            gse_pairs.select([pl.col("HMDAIndex_s").alias("HMDAIndex"), "lei_purchaser"]),
            on="HMDAIndex",
            how="inner",
        )

        if len(candidates) > 0:
            frames.append(candidates)

    if not frames:
        logger.info("  No Phase 3 candidates found")
        return pl.DataFrame()

    combined = pl.concat(frames, how="diagonal_relaxed")

    # Remove already matched
    unmatched = combined.join(already_matched, on="HMDAIndex", how="anti")

    logger.info(f"  Total candidates: {len(combined):,}, unmatched: {len(unmatched):,}")
    return unmatched


def load_unmatched_hmda(
    agency: Literal["fnma", "fhlmc"],
    min_year: int = 2020,
    max_year: int = 2024,
) -> pl.DataFrame:
    """Load HMDA loans sold to GSE but not in chain crosswalk.

    Args:
        agency: Which GSE to load for ("fnma" or "fhlmc")
        min_year: Minimum activity year to include
        max_year: Maximum activity year to include

    Returns:
        DataFrame of unmatched HMDA loans with columns needed for matching
    """
    purchaser_type = Config.AGENCY_CONFIG[agency]["purchaser_type"]
    crosswalk_path = Config.get_output_path(agency, enriched=False)

    logger.info(f"Loading unmatched HMDA loans sold to {agency.upper()}...")

    # Load master crosswalk HMDAIndex values
    crosswalk_ids = (
        pl.scan_parquet(str(crosswalk_path))
        .filter((pl.col("activity_year") >= min_year) & (pl.col("activity_year") <= max_year))
        .select("HMDAIndex")
        .unique()
        .collect()
    )

    frames = []
    for year in range(min_year, max_year + 1):
        year_dir = Config.HMDA_SILVER_DIR / f"activity_year={year}"
        if not year_dir.exists():
            logger.info(f"  Year {year} not found")
            continue

        best_ft = _get_best_file_type_for_year(year)
        logger.info(f"  {year}: using file_type '{best_ft}'")

        limit = Config.CONFORMING_LIMITS.get(year, 1_000_000)

        lf = pl.scan_parquet(str(year_dir / "**/*.parquet"))

        # Apply filters
        lf = lf.filter(
            (pl.col("action_taken") == 1)
            & (pl.col("loan_type") == 1)
            & (pl.col("lien_status") == 1)
            & (pl.col("total_units") <= 4)
            & (pl.col("loan_purpose").is_in([1, 31, 32]))
            & (pl.col("loan_amount") <= limit)
            & (pl.col("purchaser_type") == purchaser_type)
            & (pl.col("file_type") == best_ft)
        )

        # Select needed columns
        lf = lf.select(
            [
                "HMDAIndex",
                "activity_year",
                "lei",
                "state_code",
                "loan_amount",
                "interest_rate",
                "loan_term",
                "loan_purpose",
                "occupancy_type",
                "total_units",
                "rate_spread",
                "initially_payable_to_institution",
            ]
        )

        frames.append(lf.collect())

    if not frames:
        return pl.DataFrame()

    combined = pl.concat(frames, how="diagonal_relaxed")

    # Anti-join against crosswalk
    unmatched = combined.join(crosswalk_ids, on="HMDAIndex", how="anti")

    logger.info(f"  Total unmatched: {len(unmatched):,}")
    return unmatched


def load_unmatched_umbs(
    agency: Literal["fnma", "fhlmc"],
    min_year: int = 2020,
    max_year: int = 2024,
) -> pl.DataFrame:
    """Load UMBS ILLD loans not in chain crosswalk.

    Args:
        agency: Which GSE to load for ("fnma" or "fhlmc")
        min_year: Minimum first payment year to include
        max_year: Maximum first payment year to include

    Returns:
        DataFrame of unmatched UMBS loans with columns needed for matching
    """
    illd_dir = Config.get_umbs_illd_dir(agency)
    crosswalk_path = Config.get_output_path(agency, enriched=False)

    logger.info(f"Loading unmatched UMBS {agency.upper()} ILLD...")

    # Load master crosswalk umbs_loan_id values
    crosswalk_ids = (
        pl.scan_parquet(str(crosswalk_path))
        .filter((pl.col("activity_year") >= min_year) & (pl.col("activity_year") <= max_year))
        .select("umbs_loan_id")
        .unique()
        .collect()
    )

    files = list(illd_dir.glob("*.parquet"))
    if not files:
        logger.info(f"  No ILLD files found in {illd_dir}")
        return pl.DataFrame()

    logger.info(f"  Found {len(files)} ILLD monthly files")

    lf = pl.scan_parquet(
        [str(f) for f in files],
        missing_columns="insert",
        extra_columns="ignore",
    )

    # Extract first payment year from First Payment Date (MMYYYY format)
    lf = lf.with_columns([(pl.col("First Payment Date") % 10000).alias("first_payment_year")])

    # Filter to relevant first payment years
    lf = lf.filter(
        (pl.col("first_payment_year") >= min_year) & (pl.col("first_payment_year") <= max_year + 1)
    )

    # Select and rename columns
    lf = lf.select(
        [
            pl.col("Loan Identifier").cast(pl.Utf8).alias("umbs_loan_id"),
            pl.col("Mortgage Loan Amount").alias("umbs_loan_amount"),
            pl.col("Original Interest Rate").alias("umbs_interest_rate"),
            "first_payment_year",
            pl.col("Property State").alias("umbs_state"),
            pl.col("Loan Term").alias("umbs_loan_term"),
            pl.col("Number of Units").alias("umbs_units"),
            pl.col("Occupancy Status").alias("umbs_occupancy"),
            pl.col("Seller Name").alias("umbs_seller_name"),
            pl.col("Borrower Credit Score").alias("umbs_credit_score"),
            pl.col("Channel").alias("umbs_channel"),
        ]
    )

    # Keep unique loans
    lf = lf.unique(subset=["umbs_loan_id"])

    # Collect
    df = lf.collect()

    # Anti-join against crosswalk
    unmatched = df.join(crosswalk_ids, on="umbs_loan_id", how="anti")

    logger.info(f"  Total unmatched: {len(unmatched):,}")
    return unmatched


def _prepare_data_for_matching(
    hmda: pl.DataFrame,
    umbs: pl.DataFrame,
    lender_seller: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Prepare HMDA and UMBS data with derived fields for matching.

    Adds blocking keys and standardizes fields needed for join operations:
    - HMDA: occupancy mapping, rate bucket (quarter-point bins)
    - UMBS: rounded loan amount ($10k bins), rate bucket, uppercase seller name
    - Lender-seller: uppercase seller name for matching

    Args:
        hmda: HMDA DataFrame with columns: occupancy_type, interest_rate
        umbs: UMBS DataFrame with columns: umbs_loan_amount, umbs_interest_rate, umbs_seller_name
        lender_seller: Lender-seller crosswalk with columns: lei, seller_name

    Returns:
        Tuple of (prepared HMDA DataFrame, prepared UMBS DataFrame, prepared lender_seller DataFrame)
        with derived fields added for matching
    """
    # HMDA: Map occupancy
    hmda = hmda.with_columns(
        [
            pl.when(pl.col("occupancy_type") == 1)
            .then(pl.lit("O"))
            .when(pl.col("occupancy_type") == 2)
            .then(pl.lit("S"))
            .when(pl.col("occupancy_type") == 3)
            .then(pl.lit("I"))
            .otherwise(pl.lit(None))
            .alias("hmda_occupancy_mapped")
        ]
    )

    # HMDA: Add rate bucket for blocking (quarter-point buckets)
    hmda = hmda.with_columns(
        [((pl.col("interest_rate") / 0.25).round() * 0.25).alias("rate_bucket")]
    )

    # UMBS: Round loan amount to HMDA's $10k bins
    umbs = umbs.with_columns(
        [
            ((pl.col("umbs_loan_amount") // 10000) * 10000 + 5000)
            .cast(pl.Int64)
            .alias("umbs_loan_amount_rounded")
        ]
    )

    # UMBS: Add rate bucket for blocking
    umbs = umbs.with_columns(
        [((pl.col("umbs_interest_rate") / 0.25).round() * 0.25).alias("umbs_rate_bucket")]
    )

    # UMBS: Uppercase seller name
    umbs = umbs.with_columns(
        [pl.col("umbs_seller_name").str.to_uppercase().alias("umbs_seller_name_upper")]
    )

    # Prepare lender_seller for deferred matching
    lender_seller = lender_seller.with_columns(
        [pl.col("seller_name").str.to_uppercase().alias("seller_name_upper")]
    )

    return hmda, umbs, lender_seller


def _match_state_by_amount_buckets(
    hmda_state: pl.DataFrame,
    umbs_state: pl.DataFrame,
    valid_lei_seller: pl.DataFrame,
    round_num: int,
    rate_tol: float,
    term_tol: int,
    apply_lender_constraint: bool = True,
) -> pl.DataFrame:
    """Match a state using amount-based bucketing for large states.

    Memory-efficient approach that divides loans into $50k amount buckets, matches
    within each bucket, then combines and deduplicates across all buckets. For round 2,
    also checks adjacent rate buckets (+/- 0.25%).

    Args:
        hmda_state: HMDA loans for a single state with columns: loan_amount, rate_bucket,
            interest_rate, activity_year, loan_term, total_units, hmda_occupancy_mapped, lei
        umbs_state: UMBS loans for a single state with columns: umbs_loan_amount_rounded,
            umbs_rate_bucket, umbs_interest_rate, first_payment_year, umbs_loan_term,
            umbs_units, umbs_occupancy, umbs_seller_name_upper
        valid_lei_seller: Lender-seller crosswalk with columns: lei, seller_name_upper
        round_num: Matching round (1 or 2). Round 1 uses stricter filters (units, occupancy).
        rate_tol: Maximum allowed interest rate difference in percentage points (e.g., 0.125)
        term_tol: Maximum allowed loan term difference in months (e.g., 6)
        apply_lender_constraint: Whether to require lei-seller_name match via crosswalk

    Returns:
        DataFrame of unique matches with columns: HMDAIndex, activity_year, umbs_loan_id,
        state_code, loan_amount, interest_rate, match_round
    """
    empty_schema = {
        "HMDAIndex": pl.Utf8,
        "activity_year": pl.Int64,
        "umbs_loan_id": pl.Utf8,
        "state_code": pl.Utf8,
        "loan_amount": pl.Int64,
        "interest_rate": pl.Float64,
        "match_round": pl.UInt8,
    }

    # Add amount bucket ($50k ranges)
    hmda_state = hmda_state.with_columns([(pl.col("loan_amount") // 50000).alias("amount_bucket")])
    umbs_state = umbs_state.with_columns(
        [(pl.col("umbs_loan_amount_rounded") // 50000).alias("amount_bucket")]
    )

    # Get unique amount buckets
    hmda_buckets = set(hmda_state["amount_bucket"].unique().to_list())
    umbs_buckets = set(umbs_state["amount_bucket"].unique().to_list())
    common_buckets = sorted(hmda_buckets & umbs_buckets)

    bucket_matches = []

    for bucket in common_buckets:
        hmda_bucket = hmda_state.filter(pl.col("amount_bucket") == bucket)
        umbs_bucket = umbs_state.filter(pl.col("amount_bucket") == bucket)

        if len(hmda_bucket) == 0 or len(umbs_bucket) == 0:
            continue

        # Join on (amount, rate_bucket)
        joined = hmda_bucket.join(
            umbs_bucket,
            left_on=["loan_amount", "rate_bucket"],
            right_on=["umbs_loan_amount_rounded", "umbs_rate_bucket"],
            how="inner",
        )

        # For round 2, also check adjacent rate buckets
        if round_num == 2:
            umbs_adj_up = umbs_bucket.with_columns(
                [(pl.col("umbs_rate_bucket") + 0.25).alias("umbs_rate_bucket")]
            )
            joined_up = hmda_bucket.join(
                umbs_adj_up,
                left_on=["loan_amount", "rate_bucket"],
                right_on=["umbs_loan_amount_rounded", "umbs_rate_bucket"],
                how="inner",
            )

            umbs_adj_down = umbs_bucket.with_columns(
                [(pl.col("umbs_rate_bucket") - 0.25).alias("umbs_rate_bucket")]
            )
            joined_down = hmda_bucket.join(
                umbs_adj_down,
                left_on=["loan_amount", "rate_bucket"],
                right_on=["umbs_loan_amount_rounded", "umbs_rate_bucket"],
                how="inner",
            )

            joined = pl.concat([joined, joined_up, joined_down], how="diagonal_relaxed")

        if len(joined) == 0:
            continue

        # Apply filters
        joined = joined.filter(
            ((pl.col("interest_rate") - pl.col("umbs_interest_rate")).abs() <= rate_tol)
            & (
                (pl.col("activity_year") == pl.col("first_payment_year"))
                | (pl.col("activity_year") == pl.col("first_payment_year") - 1)
            )
            & ((pl.col("loan_term") - pl.col("umbs_loan_term")).abs() <= term_tol)
        )

        if len(joined) == 0:
            continue

        # Apply lender-seller constraint
        if apply_lender_constraint:
            joined = joined.join(
                valid_lei_seller,
                left_on=["lei", "umbs_seller_name_upper"],
                right_on=["lei", "seller_name_upper"],
                how="semi",
            )

            if len(joined) == 0:
                continue

        # Units and occupancy filter for round 1 only
        if round_num == 1:
            joined = joined.filter(
                (pl.col("total_units") == pl.col("umbs_units"))
                & (pl.col("hmda_occupancy_mapped") == pl.col("umbs_occupancy"))
            )

        if len(joined) == 0:
            continue

        bucket_matches.append(joined)

    if not bucket_matches:
        return pl.DataFrame(schema=empty_schema)

    # Combine all bucket matches
    all_joined = pl.concat(bucket_matches, how="diagonal_relaxed")

    # Score and deduplicate across all buckets
    all_joined = all_joined.with_columns(
        [
            (
                (pl.col("interest_rate") - pl.col("umbs_interest_rate")).abs() * 100
                + (pl.col("loan_term") - pl.col("umbs_loan_term")).abs()
            ).alias("match_score")
        ]
    )

    hmda_best = all_joined.sort("match_score").group_by("HMDAIndex").first()
    umbs_best = all_joined.sort("match_score").group_by("umbs_loan_id").first()

    unique_matches = hmda_best.join(
        umbs_best.select(["HMDAIndex", "umbs_loan_id"]),
        on=["HMDAIndex", "umbs_loan_id"],
        how="semi",
    )

    if len(unique_matches) == 0:
        return pl.DataFrame(schema=empty_schema)

    return unique_matches.select(
        [
            "HMDAIndex",
            "activity_year",
            "umbs_loan_id",
            "state_code",
            "loan_amount",
            "interest_rate",
            pl.lit(round_num).cast(pl.UInt8).alias("match_round"),
        ]
    )


def _run_matching_global(
    hmda: pl.DataFrame,
    umbs: pl.DataFrame,
    lender_seller: pl.DataFrame,
    phase3_candidates: pl.DataFrame | None = None,
) -> tuple[pl.DataFrame, dict]:
    """Run matching in three phases by channel type.

    Phase 1: All channels with lender-seller constraint (originator LEI -> Seller Name)
    Phase 2: Correspondent HMDA loans against UMBS channel C without lender constraint
    Phase 3: Correspondent loans where correspondent sold to GSE, using purchaser's LEI
    """
    logger.info("Running three-phase matching by channel")

    empty_schema = {
        "HMDAIndex": pl.Utf8,
        "activity_year": pl.Int64,
        "umbs_loan_id": pl.Utf8,
        "state_code": pl.Utf8,
        "loan_amount": pl.Int64,
        "interest_rate": pl.Float64,
        "match_round": pl.UInt8,
    }

    stats = {
        "phase_1_round_1": 0,
        "phase_1_round_2": 0,
        "phase_2_round_1": 0,
        "phase_2_round_2": 0,
        "phase_3_round_1": 0,
        "phase_3_round_2": 0,
        "round_1": 0,
        "round_2": 0,
    }

    # Track matched IDs
    matched_hmda = pl.DataFrame({"HMDAIndex": []}, schema={"HMDAIndex": pl.Utf8})
    matched_umbs = pl.DataFrame({"umbs_loan_id": []}, schema={"umbs_loan_id": pl.Utf8})

    all_matches = []

    # Prepare lender-seller lookup
    valid_lei_seller = lender_seller.select(["lei", "seller_name_upper"]).unique()

    hmda_years = sorted(hmda["activity_year"].unique().to_list())
    logger.info(f"  Processing years: {hmda_years}")

    # Threshold for using amount-bucketed matching
    large_state_threshold = 50_000_000

    # Two main phases
    for phase in [1, 2]:
        if phase == 1:
            logger.info("Phase 1: All Channels with Lender Constraint")
            umbs_phase = umbs
            hmda_phase = hmda
            apply_lender_constraint = True
        else:
            logger.info("Phase 2: Correspondent Loans (UMBS C) without Lender Constraint")
            umbs_phase = umbs.filter(pl.col("umbs_channel") == "C")
            umbs_phase = umbs_phase.join(matched_umbs, on="umbs_loan_id", how="anti")
            hmda_phase = hmda.filter(pl.col("initially_payable_to_institution") == 2)
            hmda_phase = hmda_phase.join(matched_hmda, on="HMDAIndex", how="anti")
            apply_lender_constraint = False

            if len(hmda_phase) == 0 or len(umbs_phase) == 0:
                logger.info("  No unmatched correspondent loans to process")
                continue

        logger.info(f"  HMDA candidates: {len(hmda_phase):,}, UMBS candidates: {len(umbs_phase):,}")

        for round_num in [1, 2]:
            logger.info(f"Phase {phase} Round {round_num}")

            if round_num == 1:
                rate_tol = 0.125
                term_tol = 6
            else:
                rate_tol = 0.375
                term_tol = 12

            round_matches = []

            for year in hmda_years:
                hmda_year = hmda_phase.filter(pl.col("activity_year") == year)
                hmda_year = hmda_year.join(matched_hmda, on="HMDAIndex", how="anti")

                if len(hmda_year) == 0:
                    continue

                umbs_year = umbs_phase.filter(
                    (pl.col("first_payment_year") == year)
                    | (pl.col("first_payment_year") == year + 1)
                )
                umbs_year = umbs_year.join(matched_umbs, on="umbs_loan_id", how="anti")

                if len(umbs_year) == 0:
                    continue

                logger.info(f"    Year {year}: HMDA={len(hmda_year):,}, UMBS={len(umbs_year):,}")

                states = sorted(
                    set(hmda_year["state_code"].unique().to_list())
                    & set(umbs_year["umbs_state"].unique().to_list())
                )

                year_matches = 0
                large_states = 0
                for state in states:
                    hmda_state = hmda_year.filter(pl.col("state_code") == state)
                    umbs_state = umbs_year.filter(pl.col("umbs_state") == state)

                    if len(hmda_state) == 0 or len(umbs_state) == 0:
                        continue

                    state_size = len(hmda_state) * len(umbs_state)
                    is_large = state_size > large_state_threshold

                    if is_large:
                        large_states += 1
                        matches = _match_state_by_amount_buckets(
                            hmda_state,
                            umbs_state,
                            valid_lei_seller,
                            round_num,
                            rate_tol,
                            term_tol,
                            apply_lender_constraint,
                        )
                    else:
                        # Direct matching for small states
                        joined = hmda_state.join(
                            umbs_state,
                            left_on=["loan_amount", "rate_bucket"],
                            right_on=["umbs_loan_amount_rounded", "umbs_rate_bucket"],
                            how="inner",
                        )

                        if round_num == 2:
                            umbs_adj_up = umbs_state.with_columns(
                                [(pl.col("umbs_rate_bucket") + 0.25).alias("umbs_rate_bucket")]
                            )
                            joined_up = hmda_state.join(
                                umbs_adj_up,
                                left_on=["loan_amount", "rate_bucket"],
                                right_on=["umbs_loan_amount_rounded", "umbs_rate_bucket"],
                                how="inner",
                            )

                            umbs_adj_down = umbs_state.with_columns(
                                [(pl.col("umbs_rate_bucket") - 0.25).alias("umbs_rate_bucket")]
                            )
                            joined_down = hmda_state.join(
                                umbs_adj_down,
                                left_on=["loan_amount", "rate_bucket"],
                                right_on=["umbs_loan_amount_rounded", "umbs_rate_bucket"],
                                how="inner",
                            )

                            joined = pl.concat(
                                [joined, joined_up, joined_down], how="diagonal_relaxed"
                            )

                        if len(joined) == 0:
                            continue

                        joined = joined.filter(
                            (
                                (pl.col("interest_rate") - pl.col("umbs_interest_rate")).abs()
                                <= rate_tol
                            )
                            & (
                                (pl.col("activity_year") == pl.col("first_payment_year"))
                                | (pl.col("activity_year") == pl.col("first_payment_year") - 1)
                            )
                            & ((pl.col("loan_term") - pl.col("umbs_loan_term")).abs() <= term_tol)
                        )

                        if len(joined) == 0:
                            continue

                        if apply_lender_constraint:
                            joined = joined.join(
                                valid_lei_seller,
                                left_on=["lei", "umbs_seller_name_upper"],
                                right_on=["lei", "seller_name_upper"],
                                how="semi",
                            )

                            if len(joined) == 0:
                                continue

                        if round_num == 1:
                            joined = joined.filter(
                                (pl.col("total_units") == pl.col("umbs_units"))
                                & (pl.col("hmda_occupancy_mapped") == pl.col("umbs_occupancy"))
                            )

                        if len(joined) == 0:
                            continue

                        joined = joined.with_columns(
                            [
                                (
                                    (pl.col("interest_rate") - pl.col("umbs_interest_rate")).abs()
                                    * 100
                                    + (pl.col("loan_term") - pl.col("umbs_loan_term")).abs()
                                ).alias("match_score")
                            ]
                        )

                        hmda_best = joined.sort("match_score").group_by("HMDAIndex").first()
                        umbs_best = joined.sort("match_score").group_by("umbs_loan_id").first()

                        unique_matches = hmda_best.join(
                            umbs_best.select(["HMDAIndex", "umbs_loan_id"]),
                            on=["HMDAIndex", "umbs_loan_id"],
                            how="semi",
                        )

                        if len(unique_matches) == 0:
                            continue

                        matches = unique_matches.select(
                            [
                                "HMDAIndex",
                                "activity_year",
                                "umbs_loan_id",
                                "state_code",
                                "loan_amount",
                                "interest_rate",
                                pl.lit(round_num).cast(pl.UInt8).alias("match_round"),
                            ]
                        )

                    if len(matches) > 0:
                        round_matches.append(matches)
                        year_matches += len(matches)

                        matched_hmda = pl.concat(
                            [matched_hmda, matches.select("HMDAIndex")], how="diagonal"
                        )
                        matched_umbs = pl.concat(
                            [matched_umbs, matches.select("umbs_loan_id")], how="diagonal"
                        )

                if year_matches > 0:
                    logger.info(f"      Matches: {year_matches:,} ({large_states} large states)")

            if round_matches:
                round_df = pl.concat(round_matches, how="diagonal_relaxed")
                all_matches.append(round_df)
                stats[f"phase_{phase}_round_{round_num}"] = len(round_df)
                stats[f"round_{round_num}"] = stats.get(f"round_{round_num}", 0) + len(round_df)
                logger.info(f"  Phase {phase} Round {round_num} total: {len(round_df):,}")
            else:
                logger.info(f"  Phase {phase} Round {round_num} total: 0")

    # Phase 3: Correspondent chains
    if phase3_candidates is not None and len(phase3_candidates) > 0:
        logger.info("Phase 3: Correspondent Chains (using Purchaser LEI)")

        hmda_with_purchaser_lei = phase3_candidates.join(matched_hmda, on="HMDAIndex", how="anti")

        hmda_with_purchaser_lei = hmda_with_purchaser_lei.with_columns(
            [
                pl.when(pl.col("occupancy_type") == 1)
                .then(pl.lit("O"))
                .when(pl.col("occupancy_type") == 2)
                .then(pl.lit("S"))
                .when(pl.col("occupancy_type") == 3)
                .then(pl.lit("I"))
                .otherwise(pl.lit(None))
                .alias("hmda_occupancy_mapped"),
                ((pl.col("interest_rate") / 0.25).round() * 0.25).alias("rate_bucket"),
            ]
        )

        umbs_corr = umbs.filter(pl.col("umbs_channel") == "C")
        umbs_corr = umbs_corr.join(matched_umbs, on="umbs_loan_id", how="anti")

        logger.info(f"  HMDA correspondent with purchaser LEI: {len(hmda_with_purchaser_lei):,}")
        logger.info(f"  UMBS Channel C unmatched: {len(umbs_corr):,}")

        if len(hmda_with_purchaser_lei) > 0 and len(umbs_corr) > 0:
            valid_purchaser_seller = lender_seller.select(["lei", "seller_name_upper"]).unique()

            for round_num in [1, 2]:
                logger.info(f"Phase 3 Round {round_num}")

                if round_num == 1:
                    rate_tol = 0.125
                    term_tol = 6
                else:
                    rate_tol = 0.375
                    term_tol = 12

                round_matches = []

                for year in hmda_years:
                    hmda_year = hmda_with_purchaser_lei.filter(pl.col("activity_year") == year)
                    hmda_year = hmda_year.join(matched_hmda, on="HMDAIndex", how="anti")

                    if len(hmda_year) == 0:
                        continue

                    umbs_year = umbs_corr.filter(
                        (pl.col("first_payment_year") == year)
                        | (pl.col("first_payment_year") == year + 1)
                    )
                    umbs_year = umbs_year.join(matched_umbs, on="umbs_loan_id", how="anti")

                    if len(umbs_year) == 0:
                        continue

                    logger.info(
                        f"    Year {year}: HMDA={len(hmda_year):,}, UMBS={len(umbs_year):,}",
                    )

                    states = sorted(
                        set(hmda_year["state_code"].unique().to_list())
                        & set(umbs_year["umbs_state"].unique().to_list())
                    )

                    year_matches = 0
                    for state in states:
                        hmda_state = hmda_year.filter(pl.col("state_code") == state)
                        umbs_state = umbs_year.filter(pl.col("umbs_state") == state)

                        if len(hmda_state) == 0 or len(umbs_state) == 0:
                            continue

                        # Use amount-bucketed approach for Phase 3
                        hmda_state_bucketed = hmda_state.with_columns(
                            [(pl.col("loan_amount") // 50000).alias("amount_bucket")]
                        )
                        umbs_state_bucketed = umbs_state.with_columns(
                            [(pl.col("umbs_loan_amount_rounded") // 50000).alias("amount_bucket")]
                        )

                        hmda_buckets = set(hmda_state_bucketed["amount_bucket"].unique().to_list())
                        umbs_buckets = set(umbs_state_bucketed["amount_bucket"].unique().to_list())
                        common_buckets = sorted(hmda_buckets & umbs_buckets)

                        bucket_matches = []
                        for bucket in common_buckets:
                            hmda_bucket = hmda_state_bucketed.filter(
                                pl.col("amount_bucket") == bucket
                            )
                            umbs_bucket = umbs_state_bucketed.filter(
                                pl.col("amount_bucket") == bucket
                            )

                            if len(hmda_bucket) == 0 or len(umbs_bucket) == 0:
                                continue

                            joined = hmda_bucket.join(
                                umbs_bucket,
                                left_on=["loan_amount", "rate_bucket"],
                                right_on=["umbs_loan_amount_rounded", "umbs_rate_bucket"],
                                how="inner",
                            )

                            if round_num == 2:
                                umbs_adj_up = umbs_bucket.with_columns(
                                    [(pl.col("umbs_rate_bucket") + 0.25).alias("umbs_rate_bucket")]
                                )
                                joined_up = hmda_bucket.join(
                                    umbs_adj_up,
                                    left_on=["loan_amount", "rate_bucket"],
                                    right_on=["umbs_loan_amount_rounded", "umbs_rate_bucket"],
                                    how="inner",
                                )

                                umbs_adj_down = umbs_bucket.with_columns(
                                    [(pl.col("umbs_rate_bucket") - 0.25).alias("umbs_rate_bucket")]
                                )
                                joined_down = hmda_bucket.join(
                                    umbs_adj_down,
                                    left_on=["loan_amount", "rate_bucket"],
                                    right_on=["umbs_loan_amount_rounded", "umbs_rate_bucket"],
                                    how="inner",
                                )

                                joined = pl.concat(
                                    [joined, joined_up, joined_down], how="diagonal_relaxed"
                                )

                            if len(joined) == 0:
                                continue

                            joined = joined.filter(
                                (
                                    (pl.col("interest_rate") - pl.col("umbs_interest_rate")).abs()
                                    <= rate_tol
                                )
                                & (
                                    (pl.col("activity_year") == pl.col("first_payment_year"))
                                    | (pl.col("activity_year") == pl.col("first_payment_year") - 1)
                                )
                                & (
                                    (pl.col("loan_term") - pl.col("umbs_loan_term")).abs()
                                    <= term_tol
                                )
                            )

                            if len(joined) == 0:
                                continue

                            # Apply lender-seller constraint using PURCHASER's LEI
                            joined = joined.join(
                                valid_purchaser_seller,
                                left_on=["lei_purchaser", "umbs_seller_name_upper"],
                                right_on=["lei", "seller_name_upper"],
                                how="semi",
                            )

                            if len(joined) == 0:
                                continue

                            if round_num == 1:
                                joined = joined.filter(
                                    (pl.col("total_units") == pl.col("umbs_units"))
                                    & (pl.col("hmda_occupancy_mapped") == pl.col("umbs_occupancy"))
                                )

                            if len(joined) > 0:
                                bucket_matches.append(joined)

                        if not bucket_matches:
                            continue

                        all_joined = pl.concat(bucket_matches, how="diagonal_relaxed")

                        all_joined = all_joined.with_columns(
                            [
                                (
                                    (pl.col("interest_rate") - pl.col("umbs_interest_rate")).abs()
                                    * 100
                                    + (pl.col("loan_term") - pl.col("umbs_loan_term")).abs()
                                ).alias("match_score")
                            ]
                        )

                        hmda_best = all_joined.sort("match_score").group_by("HMDAIndex").first()
                        umbs_best = all_joined.sort("match_score").group_by("umbs_loan_id").first()

                        unique_matches = hmda_best.join(
                            umbs_best.select(["HMDAIndex", "umbs_loan_id"]),
                            on=["HMDAIndex", "umbs_loan_id"],
                            how="semi",
                        )

                        if len(unique_matches) > 0:
                            matches = unique_matches.select(
                                [
                                    "HMDAIndex",
                                    "activity_year",
                                    "umbs_loan_id",
                                    "state_code",
                                    "loan_amount",
                                    "interest_rate",
                                    pl.lit(round_num).cast(pl.UInt8).alias("match_round"),
                                ]
                            )

                            round_matches.append(matches)
                            year_matches += len(matches)

                            matched_hmda = pl.concat(
                                [matched_hmda, matches.select("HMDAIndex")], how="diagonal"
                            )
                            matched_umbs = pl.concat(
                                [matched_umbs, matches.select("umbs_loan_id")], how="diagonal"
                            )

                    if year_matches > 0:
                        logger.info(f"      Matches: {year_matches:,}")

                if round_matches:
                    round_df = pl.concat(round_matches, how="diagonal_relaxed")
                    all_matches.append(round_df)
                    stats[f"phase_3_round_{round_num}"] = len(round_df)
                    stats[f"round_{round_num}"] = stats.get(f"round_{round_num}", 0) + len(round_df)
                    logger.info(f"  Phase 3 Round {round_num} total: {len(round_df):,}")
                else:
                    logger.info(f"  Phase 3 Round {round_num} total: 0")
        else:
            logger.info("  No correspondent loans with purchaser LEI to match")
    else:
        logger.info("Phase 3: Skipped (no Phase 3 candidates)")

    if all_matches:
        final_matches = pl.concat(all_matches, how="diagonal_relaxed")
    else:
        final_matches = pl.DataFrame(schema=empty_schema)

    return final_matches, stats


def validate_direct_matches(
    matches: pl.DataFrame,
    hmda: pl.DataFrame,
    umbs: pl.DataFrame,
) -> dict:
    """Validate match quality via rate_spread vs credit_score correlation.

    Args:
        matches: DataFrame of matched pairs with HMDAIndex and umbs_loan_id
        hmda: HMDA DataFrame with rate_spread column
        umbs: UMBS DataFrame with umbs_credit_score column

    Returns:
        Dictionary with validation statistics including correlation coefficient
    """
    result = {
        "records_with_both": 0,
        "correlation": None,
        "valid": False,
    }

    if len(matches) == 0:
        return result

    # Join matches with rate_spread and credit_score
    validated = matches.join(
        hmda.select(["HMDAIndex", "rate_spread"]), on="HMDAIndex", how="left"
    ).join(umbs.select(["umbs_loan_id", "umbs_credit_score"]), on="umbs_loan_id", how="left")

    # Filter to records with valid values
    with_both = validated.filter(
        pl.col("rate_spread").is_not_null()
        & (pl.col("rate_spread") > 0)
        & pl.col("umbs_credit_score").is_not_null()
        & (pl.col("umbs_credit_score") >= 300)
        & (pl.col("umbs_credit_score") <= 850)
    )

    result["records_with_both"] = len(with_both)

    if len(with_both) < 100:
        return result

    # Compute correlation
    rate_spread = with_both["rate_spread"].to_numpy()
    credit_score = with_both["umbs_credit_score"].to_numpy()

    correlation = float(np.corrcoef(rate_spread, credit_score)[0, 1])
    result["correlation"] = correlation
    result["valid"] = correlation < -0.05

    return result


def run_direct_matching(
    agency: Literal["fnma", "fhlmc"],
    min_year: int = 2020,
    max_year: int = 2024,
) -> pl.DataFrame:
    """Run direct HMDA-UMBS matching for unmatched GSE-sold loans.

    Three-phase matching:
    - Phase 1: All channels with lender-seller constraint
    - Phase 2: Correspondent loans without constraint
    - Phase 3: Correspondent chains using purchaser LEI

    Args:
        agency: Which GSE to process ("fnma" or "fhlmc")
        min_year: Minimum activity year to process
        max_year: Maximum activity year to process

    Returns:
        DataFrame of matched pairs with columns:
        - HMDAIndex: HMDA loan identifier
        - activity_year: Year of origination
        - umbs_loan_id: UMBS loan identifier
        - state_code: State of property
        - loan_amount: Loan amount
        - interest_rate: Interest rate
        - match_round: Which matching round produced the match
    """
    logger.info(f"Direct HMDA-UMBS Matching: {agency.upper()}")
    logger.info(f"Years: {min_year}-{max_year}")

    # Load lender-seller crosswalk
    lender_seller = _load_lender_seller_crosswalk()

    # Load seller-purchaser crosswalk for Phase 3
    seller_purchaser_xwalk = _load_seller_purchaser_crosswalk(min_year, max_year)

    # Load unmatched HMDA and UMBS
    hmda = load_unmatched_hmda(agency, min_year, max_year)
    umbs = load_unmatched_umbs(agency, min_year, max_year)

    if len(hmda) == 0 or len(umbs) == 0:
        logger.info("No data to match")
        return pl.DataFrame(
            schema={
                "HMDAIndex": pl.Utf8,
                "activity_year": pl.Int64,
                "umbs_loan_id": pl.Utf8,
                "state_code": pl.Utf8,
                "loan_amount": pl.Int64,
                "interest_rate": pl.Float64,
                "match_round": pl.UInt8,
            }
        )

    initial_hmda = len(hmda)
    initial_umbs = len(umbs)

    logger.info(f"HMDA unmatched GSE-sold: {initial_hmda:,}")
    logger.info(f"UMBS unmatched: {initial_umbs:,}")

    # Load Phase 3 candidates
    crosswalk_path = Config.get_output_path(agency, enriched=False)
    crosswalk_ids = (
        pl.scan_parquet(str(crosswalk_path))
        .filter((pl.col("activity_year") >= min_year) & (pl.col("activity_year") <= max_year))
        .select("HMDAIndex")
        .unique()
        .collect()
    )
    phase3_candidates = _load_phase3_candidates(
        min_year, max_year, agency, seller_purchaser_xwalk, crosswalk_ids
    )

    # Prepare data
    logger.info("Preparing data for matching...")
    hmda, umbs, lender_seller = _prepare_data_for_matching(hmda, umbs, lender_seller)

    # Run matching with all three phases
    final_matches, stats = _run_matching_global(hmda, umbs, lender_seller, phase3_candidates)

    # Log summary
    total_matches = len(final_matches)
    match_rate = 100 * total_matches / initial_hmda if initial_hmda > 0 else 0
    logger.info(f"Total matched: {total_matches:,} ({match_rate:.1f}% of unmatched HMDA)")
    logger.info(f"  Round 1: {stats['round_1']:,}")
    logger.info(f"  Round 2: {stats['round_2']:,}")

    # Validation
    validation = validate_direct_matches(final_matches, hmda, umbs)
    if validation["correlation"] is not None:
        logger.info(
            f"Validation: correlation={validation['correlation']:.3f} "
            f"(n={validation['records_with_both']:,})",
        )

    return final_matches
