"""FHFA-HMDA post-2018 matching workflow.

Config-driven five-round matching strategy optimized for performance:
- Round 1: Census tract + purchaser + demographics (same year, pt 1,3)
- Round 2: Census tract only (same year, pt 1,3)
- Round 3: Cross-year matching for all purchaser types + moderate quality filter
- Round 4: Fuzzy rate matching (floor/ceil bins) for near-rate matches
- Round 5: Cross-vintage tract bridge (HMDA<=2021 ↔ FHFA>=2022) via the
  Census Bureau 2010↔2020 tract relationship file

Key optimizations:
- Anti-joins for exclusions (O(n) vs O(n*m) for is_in())
- Per-year file loading instead of lazy concat
- Lazy evaluation until final uniqueness check
- Quality-based tie-breaking before uniqueness constraint

Architecture:
- FHFAHMDAMatcher: Main class that orchestrates config-driven matching
- MatchResult: Dataclass for match results and statistics
- Helper functions for data loading and filtering
"""

# Standard library imports

from __future__ import annotations

from dataclasses import dataclass, field

# Third-party imports
import polars as pl

# Local application imports
from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.core.reference import (
    load_tract_relationship,
    needs_vintage_bridge,
    to_planning_region,
)
from mortgage_data_manager.fhfa.config import FHFAConfig
from mortgage_data_manager.hmda.config import HMDAConfig

from .preprocessing import clean_hmda_missing_values

# Relative imports
from .round_config import (
    MATCH_ROUNDS,
    MAX_YEAR,
    MIN_YEAR,
    MatchRoundConfig,
    MergeConfig,
    PostMergeFilters,
    PostUniqueFilters,
    PreMergeFilters,
)

logger = get_logger(__name__)

# Data paths
FHFA_SILVER = FHFAConfig.FHFA_SILVER_DIR / "sf_c"
HMDA_SILVER = HMDAConfig.get_dataset_dir("silver", "loans", "post2018")


@dataclass
class MatchResult:
    """Result of the matching workflow.

    Attributes:
        matched_hmda: DataFrame with matched HMDAIndex values.
        matched_fhfa: DataFrame with matched FHFA keys (fhfa_year, enterprise_flag, record_number).
        round_results: List of DataFrames with match results per round.
        stats: Statistics dictionary with round-by-round and summary stats.
    """

    matched_hmda: pl.DataFrame
    matched_fhfa: pl.DataFrame
    round_results: list[pl.DataFrame] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def load_fhfa_year(year: int) -> pl.LazyFrame:
    """Load FHFA data for a single year as LazyFrame.

    Creates derived columns needed for matching:
    - census_string: 11-char FIPS code (state + county + tract)
    - NoteRate125: Interest rate bucketed to 0.125 increments
    - PurchaserType: Maps enterprise_flag to HMDA purchaser_type
    - fhfa_year: Year column for cross-year matching
    """
    fhfa_file = FHFA_SILVER / f"sf_c_{year}.parquet"
    lf = pl.scan_parquet(fhfa_file).with_columns(
        [
            # Create census tract string (state 2 + county 3 + tract 6 = 11 chars)
            (
                pl.col("state_code").cast(pl.Int64).cast(pl.Utf8).str.zfill(2)
                + pl.col("county").cast(pl.Int64).cast(pl.Utf8).str.zfill(3)
                + pl.col("census_tract").cast(pl.Int64).cast(pl.Utf8).str.zfill(6)
            ).alias("census_string"),
            # Bucket interest rate to 0.125 increments
            ((pl.col("interest_rate_at_origination") * 8).round() * 125)
            .cast(pl.Int32)
            .alias("NoteRate125"),
            # Map enterprise_flag to HMDA purchaser_type
            pl.when(pl.col("enterprise_flag") == 1)
            .then(1)  # Fannie Mae
            .when(pl.col("enterprise_flag") == 2)
            .then(3)  # Freddie Mac
            .otherwise(None)
            .alias("PurchaserType"),
            pl.lit(year).cast(pl.Int32).alias("fhfa_year"),
        ]
    )
    # Normalize CT tracts to planning-region convention. FHFA switched CT county
    # coding to planning regions in 2024 while HMDA did so in 2024 too; via the
    # FHFA[Y] <-> HMDA[Y-1] directionality, FHFA 2024 (PR) joins HMDA 2023
    # (county-coded) and the census_string never agrees. Idempotent (PR/non-CT
    # pass through), and orthogonal to the 2010<->2020 vintage bridge (that
    # boundary is 2021/2022, where these CT GEOIDs aren't in the crosswalk).
    return to_planning_region(lf, tract_col="census_string")


def load_hmda_year(year: int, pre_merge: PreMergeFilters) -> tuple[pl.LazyFrame, str]:
    """Load HMDA data for a single year as LazyFrame with pre-merge filters.

    Args:
        year: Activity year to load.
        pre_merge: Configuration for purchaser type filtering.

    Returns:
        LazyFrame and the file_type used (a, b, or c).
    """
    hmda_dir = HMDA_SILVER / f"activity_year={year}"
    lf = pl.scan_parquet(hmda_dir)

    # Select best available file_type: a (three-year) > b (one-year) > c (snapshot)
    file_types = lf.select("file_type").unique().collect()["file_type"].to_list()
    best_type = "a" if "a" in file_types else ("b" if "b" in file_types else "c")

    # Reload and filter
    lf = pl.scan_parquet(hmda_dir)
    lf = lf.filter(pl.col("file_type") == best_type)
    lf = lf.filter(pl.col("action_taken") == 1)  # Originated loans only

    # Filter out 5+ unit properties (FHFA only covers 1-4 unit)
    lf = lf.filter(pl.col("total_units") <= 4)

    # Clean HMDA missing value indicators (e.g., -99999 -> null)
    lf = clean_hmda_missing_values(lf)

    # Build purchaser type filter from pre_merge config
    pt_filters = []
    if pre_merge.include_pt0:
        pt_filters.append(pl.col("purchaser_type") == 0)
    if pre_merge.include_gse:
        pt_filters.append(pl.col("purchaser_type").is_in([1, 3]))
    if pre_merge.include_secondary:
        pt_filters.append(pl.col("purchaser_type") >= 5)

    # Combine filters with OR
    if pt_filters:
        combined_filter = pt_filters[0]
        for f in pt_filters[1:]:
            combined_filter = combined_filter | f
        lf = lf.filter(combined_filter)

    # Add derived columns including fuzzy rate bins for Round 4
    lf = lf.with_columns(
        [
            ((pl.col("interest_rate") * 8).round() * 125).cast(pl.Int32).alias("NoteRate125"),
            ((pl.col("interest_rate") * 8).floor() * 125).cast(pl.Int32).alias("NoteRateFloor"),
            ((pl.col("interest_rate") * 8).ceil() * 125).cast(pl.Int32).alias("NoteRateCeil"),
            pl.lit(year).cast(pl.Int32).alias("activity_year"),
        ]
    )

    # Normalize CT tracts to planning-region convention so cross-year CT joins
    # against FHFA line up across the 2023/2024 coding cut (see load_fhfa_year).
    lf = to_planning_region(lf, tract_col="census_tract")

    return lf, best_type


def apply_post_merge_filters(merged: pl.LazyFrame, config: PostMergeFilters) -> pl.LazyFrame:
    """Apply post-merge filters to merged data.

    Args:
        merged: Merged HMDA-FHFA data.
        config: Filter configuration.

    Returns:
        Filtered merged data.
    """
    # Current year origination filter (date_of_mortgage_note: 1=current year, 2=prior year)
    if config.require_current_year_orig:
        merged = merged.filter(pl.col("date_of_mortgage_note") == 1)

    # Rate and term tolerance filters
    merged = merged.with_columns(
        [
            (pl.col("interest_rate") - pl.col("interest_rate_at_origination"))
            .abs()
            .alias("rate_diff"),
            (pl.col("loan_term") - pl.col("loan_term_right")).abs().alias("term_diff"),
        ]
    )
    merged = merged.filter(
        (pl.col("rate_diff") <= config.rate_tolerance) | pl.col("rate_diff").is_null()
    ).filter((pl.col("term_diff") <= config.term_tolerance) | pl.col("term_diff").is_null())

    # Loan purpose consistency filter
    # Note: Both datasets have loan_purpose; after join FHFA's is loan_purpose_right
    if config.filter_loan_purpose:
        # FHFA Purpose 1 (purchase) should not match HMDA 31/32 (refinance)
        merged = merged.filter(
            ~((pl.col("loan_purpose_right") == 1) & pl.col("loan_purpose").is_in([31, 32]))
        )
        # FHFA Purpose 2/7 (refinance) should not match HMDA 1 (purchase)
        merged = merged.filter(
            ~(pl.col("loan_purpose_right").is_in([2, 7]) & (pl.col("loan_purpose") == 1))
        )

    # Strict loan purpose filter: requires exact category match
    # FHFA: 1=Purchase, 2=Refi, 7=CashOut
    # HMDA: 1=Purchase, 31=Refi, 32=CashOut
    if config.filter_loan_purpose_strict:
        merged = (
            merged.with_columns(
                [
                    pl.when(pl.col("loan_purpose_right") == 1)
                    .then(pl.lit("Purchase"))
                    .when(pl.col("loan_purpose_right") == 2)
                    .then(pl.lit("Refi"))
                    .when(pl.col("loan_purpose_right") == 7)
                    .then(pl.lit("CashOut"))
                    .otherwise(pl.lit("Other"))
                    .alias("_fhfa_purpose"),
                    pl.when(pl.col("loan_purpose") == 1)
                    .then(pl.lit("Purchase"))
                    .when(pl.col("loan_purpose") == 31)
                    .then(pl.lit("Refi"))
                    .when(pl.col("loan_purpose") == 32)
                    .then(pl.lit("CashOut"))
                    .otherwise(pl.lit("Other"))
                    .alias("_hmda_purpose"),
                ]
            )
            .filter(
                (pl.col("_fhfa_purpose") == pl.col("_hmda_purpose"))
                | (pl.col("_fhfa_purpose") == "Other")
                | (pl.col("_hmda_purpose") == "Other")
            )
            .drop(["_fhfa_purpose", "_hmda_purpose"])
        )

    # For cross-year GSE matches: require prior year origination (date_of_mortgage_note=2)
    # Same-year originations (date_of_mortgage_note=1) should match in earlier rounds
    if config.require_prior_year_for_gse:
        # Only apply to GSE purchasers (1=FNMA, 3=FHLMC) in cross-year matches
        is_gse = pl.col("purchaser_type").is_in([1, 3])
        is_cross_year = pl.col("activity_year") != pl.col("fhfa_year")
        is_prior_year_orig = pl.col("date_of_mortgage_note") == 2
        # Keep if: not GSE, or same-year match, or prior-year origination
        merged = merged.filter(~is_gse | ~is_cross_year | is_prior_year_orig)

    # For pt=0 (not sold): require cross-year + prior year origination
    # pt=0 means loan wasn't sold during HMDA reporting year, so can only appear
    # in FHFA data if sold in a subsequent year (cross-year) as a prior-year origination
    if config.require_cross_year_for_pt0:
        is_pt0 = pl.col("purchaser_type") == 0
        is_cross_year = pl.col("activity_year") != pl.col("fhfa_year")
        is_prior_year_orig = pl.col("date_of_mortgage_note") == 2
        # Keep if: not pt=0, or (cross-year AND prior-year origination)
        merged = merged.filter(~is_pt0 | (is_cross_year & is_prior_year_orig))

    if config.require_demographics:
        # Define NA codes for demographics
        # HMDA: 8888=N/A, 9999=N/A; FHFA: 99/999=Not available
        hmda_age_na = [8888, 9999]
        fhfa_age_na = [99, 999]

        # Age 62+ filter - allow if either is null
        merged = merged.filter(
            (pl.col("borrower_age_62_plus") == pl.col("applicant_age_above_62"))
            | pl.col("borrower_age_62_plus").is_null()
            | pl.col("applicant_age_above_62").is_null()
        )

        # Age bucket filter - allow if either is null or NA code
        merged = merged.filter(
            (pl.col("age_borrower") == pl.col("applicant_age"))
            | pl.col("age_borrower").is_null()
            | pl.col("age_borrower").is_in(fhfa_age_na)
            | pl.col("applicant_age").is_null()
            | pl.col("applicant_age").is_in(hmda_age_na)
        )

        # Sex filter - HMDA codes: 1=Male, 2=Female, 3=Both, 4+=NA/special
        # FHFA codes: 1=Male, 2=Female, 3=NA, 4=Both, 9=Not available
        # Only require match for valid codes (1=Male, 2=Female)
        merged = (
            merged.with_columns(
                [
                    # Normalize HMDA sex: keep 1,2, convert others to null
                    pl.when(pl.col("applicant_sex").is_in([1, 2]))
                    .then(pl.col("applicant_sex"))
                    .otherwise(None)
                    .alias("_hmda_sex"),
                    # Normalize FHFA sex: keep 1,2, convert others to null
                    pl.when(pl.col("borrower_sex").is_in([1, 2]))
                    .then(pl.col("borrower_sex"))
                    .otherwise(None)
                    .alias("_fhfa_sex"),
                ]
            )
            .filter(
                (pl.col("_fhfa_sex") == pl.col("_hmda_sex"))
                | pl.col("_fhfa_sex").is_null()
                | pl.col("_hmda_sex").is_null()
            )
            .drop(["_hmda_sex", "_fhfa_sex"])
        )

    # DTI validation filter
    # Encoding is identical: binned codes (10, 20, 30, 50, 60) + numeric (36-49)
    # FHFA: dti_ratio, HMDA: debt_to_income_ratio
    if config.filter_dti:
        # Define missing/NA codes
        dti_missing = [99, -99999]
        dti_binned = [10, 20, 30, 50, 60]

        # Skip filter if either value is missing
        has_both_dti = (
            pl.col("dti_ratio").is_not_null()
            & ~pl.col("dti_ratio").is_in(dti_missing)
            & pl.col("debt_to_income_ratio").is_not_null()
            & ~pl.col("debt_to_income_ratio").is_in(dti_missing)
        )

        # For binned codes: require exact match
        both_binned = pl.col("dti_ratio").is_in(dti_binned) & pl.col("debt_to_income_ratio").is_in(
            dti_binned
        )
        binned_match = pl.col("dti_ratio") == pl.col("debt_to_income_ratio")

        # For numeric 36-49: allow tolerance
        both_numeric = (
            (pl.col("dti_ratio") >= 36)
            & (pl.col("dti_ratio") <= 49)
            & (pl.col("debt_to_income_ratio") >= 36)
            & (pl.col("debt_to_income_ratio") <= 49)
        )
        numeric_match = (
            pl.col("dti_ratio") - pl.col("debt_to_income_ratio")
        ).abs() <= config.dti_tolerance

        # Combined DTI filter:
        # - Skip if missing (pass)
        # - Binned codes must match exactly
        # - Numeric must be within tolerance
        # - Mixed (one binned, one numeric) are allowed at boundaries
        merged = merged.filter(
            ~has_both_dti  # Pass if either is missing
            | (both_binned & binned_match)  # Binned codes match exactly
            | (both_numeric & numeric_match)  # Numeric within tolerance
            | (~both_binned & ~both_numeric)  # Mixed case (boundary): allow
        )

    return merged


def apply_post_unique_filters(df: pl.DataFrame, config: PostUniqueFilters) -> pl.DataFrame:
    """Apply post-unique filters to matched data.

    Args:
        df: Matched data after uniqueness constraint.
        config: Tighter tolerance configuration.

    Returns:
        Filtered matches meeting quality criteria.
    """
    result = df

    # Rate filter (tighter than post-merge)
    result = result.filter(
        (pl.col("rate_diff") <= config.rate_tolerance) | pl.col("rate_diff").is_null()
    )

    # Term filter (tighter than post-merge)
    result = result.filter(
        (pl.col("term_diff") <= config.term_tolerance) | pl.col("term_diff").is_null()
    )

    return result


def apply_uniqueness(merged: pl.LazyFrame) -> pl.LazyFrame:
    """Keep only 1:1 matches (unique on both sides).

    Args:
        merged: Merged data to filter.

    Returns:
        Data with only unique matches on both HMDA and FHFA sides.
    """
    return merged.with_columns(
        [
            pl.len().over(["HMDAIndex"]).alias("CountHMDA"),
            pl.len().over(["fhfa_year", "enterprise_flag", "record_number"]).alias("CountFHFA"),
        ]
    ).filter((pl.col("CountHMDA") == 1) & (pl.col("CountFHFA") == 1))


def apply_quality_selection(merged: pl.LazyFrame) -> pl.LazyFrame:
    """Select best matches when multiple candidates exist.

    Uses multi-metric tie-breaking based on rate difference:
    - Tight match: rate_diff <= 0.01
    - Good match: rate_diff <= 0.05

    For each HMDA/FHFA loan with multiple potential matches, if any match
    is "tight", drop all non-tight matches. This preserves all tight matches
    for the uniqueness filter to resolve.

    Args:
        merged: Merged data with potential duplicate matches.

    Returns:
        Filtered data with lower-quality duplicates removed.
    """
    # Define thresholds
    RATE_TIGHT = 0.01
    RATE_GOOD = 0.05

    # Calculate quality indicators
    result = merged.with_columns(
        [
            (pl.col("rate_diff").abs() <= RATE_TIGHT).fill_null(True).alias("_tight_rate"),
            (pl.col("rate_diff").abs() <= RATE_GOOD).fill_null(True).alias("_good_rate"),
        ]
    )

    # Check if each HMDA/FHFA loan has any tight match
    fhfa_key_cols = ["fhfa_year", "enterprise_flag", "record_number"]
    result = result.with_columns(
        [
            pl.col("_tight_rate").max().over(["HMDAIndex"]).alias("_hmda_has_tight"),
            pl.col("_tight_rate").max().over(fhfa_key_cols).alias("_fhfa_has_tight"),
        ]
    )

    # Filter: if loan has tight match, keep only good or better matches
    # (This drops poor matches when better ones exist)
    result = result.filter((~pl.col("_hmda_has_tight")) | pl.col("_good_rate")).filter(
        (~pl.col("_fhfa_has_tight")) | pl.col("_good_rate")
    )

    # Further tighten: if loan has tight match, keep only tight matches
    result = result.filter((~pl.col("_hmda_has_tight")) | pl.col("_tight_rate")).filter(
        (~pl.col("_fhfa_has_tight")) | pl.col("_tight_rate")
    )

    # Clean up temporary columns
    return result.drop(["_tight_rate", "_good_rate", "_hmda_has_tight", "_fhfa_has_tight"])


INCOME_TIEBREAK_MAX_DIFF = 1000.0


def apply_income_tiebreak(merged: pl.LazyFrame) -> pl.LazyFrame:
    """Break same-fingerprint ties by closest borrower income, only on a near-exact income match.

    When several candidates share the join fingerprint, the rate-based quality selection cannot
    separate them and the 1:1 uniqueness filter would drop them all as non-unique. Borrower income
    (held out of the match keys) resolves the true pair among such ties. Keep, per HMDA loan and per
    FHFA record, the candidate with the smallest |income difference| — but ONLY when that winning
    difference is within ``INCOME_TIEBREAK_MAX_DIFF`` ($1k, one rounding unit since both sources are
    $1k-granular). A weak income match is not trusted to break a tie: a genuinely cross-year loan has
    no true same-year twin, so its closest same-year income is usually far, and letting it win here
    would steal the loan from its correct cross-year round (which runs after this one). Already-unique
    matches are kept regardless of income (there is no tie to break), so recall is never reduced below
    baseline. Ties the gate declines are left for the uniqueness filter to drop, exactly as before, so
    the loan falls through to a later round. Gated to same-year rounds by the caller because FHFA
    income is definitionally inflated for prior-year acquisitions (see
    investigation_fhfa_hmda_income_usage / investigation_fhfa_hmda_dropped_vs_added).
    """
    fhfa_key = ["fhfa_year", "enterprise_flag", "record_number"]
    scored = merged.with_columns(
        pl.when(
            (pl.col("income") > 0)
            & (pl.col("borrower_annual_income") > 0)
            & (pl.col("borrower_annual_income") < 9_999_999)
        )
        .then((pl.col("income") - pl.col("borrower_annual_income")).abs().cast(pl.Float64))
        .otherwise(1e15)
        .alias("_income_diff")
    ).with_columns(
        pl.col("_income_diff").min().over(["HMDAIndex"]).alias("_min_hmda"),
        pl.col("_income_diff").min().over(fhfa_key).alias("_min_fhfa"),
        pl.len().over(["HMDAIndex"]).alias("_n_hmda"),
        pl.len().over(fhfa_key).alias("_n_fhfa"),
    )
    already_unique = (pl.col("_n_hmda") == 1) & (pl.col("_n_fhfa") == 1)
    strong_tiebreak = (
        (pl.col("_income_diff") == pl.col("_min_hmda"))
        & (pl.col("_income_diff") == pl.col("_min_fhfa"))
        & (pl.col("_income_diff") <= INCOME_TIEBREAK_MAX_DIFF)
    )
    return scored.filter(already_unique | strong_tiebreak).drop(
        ["_income_diff", "_min_hmda", "_min_fhfa", "_n_hmda", "_n_fhfa"]
    )


def get_fhfa_counts(years: list[int] | None = None) -> dict[int, int]:
    """Get FHFA loan counts by year."""
    if years is None:
        years = list(range(MIN_YEAR, MAX_YEAR + 1))

    counts = {}
    for year in years:
        fhfa_file = FHFA_SILVER / f"sf_c_{year}.parquet"
        counts[year] = pl.scan_parquet(fhfa_file).select(pl.len()).collect().item()
    return counts


class FHFAHMDAMatcher:
    """Config-driven FHFA-HMDA matching workflow.

    This class orchestrates the matching process using configurable rounds.
    Each round can specify different pre-merge, merge, post-merge, and
    post-unique filter configurations.

    Args:
        rounds: List of round configurations. Defaults to MATCH_ROUNDS (R1-R5).
        years: Years to process. Defaults to MIN_YEAR through MAX_YEAR.

    Examples:
        >>> matcher = FHFAHMDAMatcher()
        >>> result = matcher.run()
        >>> print(f"Matched {len(result.matched_fhfa):,} FHFA loans")

        >>> # Custom rounds
        >>> matcher = FHFAHMDAMatcher(rounds=[ROUND_1, ROUND_2], years=[2020, 2021])
        >>> result = matcher.run()
    """

    def __init__(
        self,
        rounds: list[MatchRoundConfig] | None = None,
        years: list[int] | None = None,
    ):
        self.rounds = rounds or MATCH_ROUNDS
        self.years = years or list(range(MIN_YEAR, MAX_YEAR + 1))
        self._matched_hmda = pl.DataFrame(schema={"HMDAIndex": pl.Utf8})
        self._matched_fhfa = pl.DataFrame(
            schema={
                "fhfa_year": pl.Int32,
                "enterprise_flag": pl.Int64,
                "record_number": pl.Int64,
            }
        )
        self._round_results: list[pl.DataFrame] = []
        self._stats: dict = {"rounds": {}, "summary": {}}

    def run(self) -> MatchResult:
        """Execute all rounds sequentially.

        Returns:
            Dataclass containing matched data and statistics.
        """
        # Get baseline counts
        fhfa_counts = get_fhfa_counts(self.years)
        total_fhfa = sum(fhfa_counts.values())
        self._stats["total_fhfa"] = total_fhfa
        self._stats["fhfa_by_year"] = fhfa_counts

        # Execute each round
        for config in self.rounds:
            self._run_round(config)

        # Calculate summary stats
        total_matched = len(self._matched_fhfa)
        self._stats["summary"] = {
            "total_matched": total_matched,
            "total_fhfa": total_fhfa,
            "fhfa_match_rate": total_matched / total_fhfa * 100 if total_fhfa > 0 else 0,
        }

        self._log_summary()

        return MatchResult(
            matched_hmda=self._matched_hmda,
            matched_fhfa=self._matched_fhfa,
            round_results=self._round_results,
            stats=self._stats,
        )

    def _run_round(self, config: MatchRoundConfig) -> None:
        """Execute a single round using config-driven logic.

        Args:
            config: Configuration for this round.
        """
        round_num = config.round_number
        logger.info(f"Starting Round {round_num}: {config.description}")

        round_matches = []
        round_stats = {
            "round": round_num,
            "description": config.description,
            "by_year": {},
            "total": 0,
            "same_year": 0,
            "cross_year": 0,
        }

        if config.merge.same_year_only:
            # Same-year: process each year independently
            for year in self.years:
                matches = self._match_year_pair(config, hmda_year=year, fhfa_year=year)
                if matches is not None and len(matches) > 0:
                    round_matches.append(matches)
                    round_stats["by_year"][year] = len(matches)
                    round_stats["total"] += len(matches)
                    round_stats["same_year"] += len(matches)
                    logger.info(f"R{round_num} {year}: {len(matches):,} matches")
        else:
            # Cross-year: HMDA year vs all FHFA years >= HMDA year
            for hmda_year in self.years:
                logger.info(f"R{round_num}: HMDA year {hmda_year}")
                for fhfa_year in range(hmda_year, max(self.years) + 1):
                    matches = self._match_year_pair(config, hmda_year, fhfa_year)
                    if matches is not None and len(matches) > 0:
                        round_matches.append(matches)
                        key = f"{hmda_year}->{fhfa_year}"
                        round_stats["by_year"][key] = len(matches)
                        logger.debug(f"  FHFA {fhfa_year}: {len(matches):,} potential")

            # Apply uniqueness across all year combinations
            if round_matches:
                combined = pl.concat(round_matches, how="diagonal")
                logger.info(f"R{round_num}: {len(combined):,} potential matches before uniqueness")

                # Apply quality selection before uniqueness
                combined_lf = combined.lazy()
                combined_lf = apply_quality_selection(combined_lf)

                # Apply uniqueness
                combined_lf = apply_uniqueness(combined_lf)
                unique = combined_lf.collect()

                # Calculate same-year vs cross-year stats
                same_year = unique.filter(pl.col("activity_year") == pl.col("fhfa_year"))
                cross_year = unique.filter(pl.col("activity_year") != pl.col("fhfa_year"))
                round_stats["same_year"] = len(same_year)
                round_stats["cross_year"] = len(cross_year)

                round_matches = [unique]
                round_stats["total"] = len(unique)
                logger.info(
                    f"R{round_num}: {len(unique):,} unique matches "
                    f"({len(same_year):,} same-year, {len(cross_year):,} cross-year)"
                )

        # Apply post_unique filters if configured
        if round_matches and config.post_unique:
            combined = (
                pl.concat(round_matches, how="diagonal")
                if len(round_matches) > 1
                else round_matches[0]
            )
            before_count = len(combined)
            filtered = apply_post_unique_filters(combined, config.post_unique)
            after_count = len(filtered)
            round_stats["post_unique"] = {
                "before": before_count,
                "after": after_count,
                "retained_pct": after_count / before_count * 100 if before_count > 0 else 0,
            }
            round_stats["total"] = after_count
            round_matches = [filtered]
            logger.info(
                f"R{round_num}: Post-unique filter: {before_count:,} -> {after_count:,} "
                f"({round_stats['post_unique']['retained_pct']:.1f}% retained)"
            )

        # Update matched sets
        if round_matches:
            combined = (
                pl.concat(round_matches, how="diagonal")
                if len(round_matches) > 1
                else round_matches[0]
            )
            self._update_matched(combined, config)
            # Standardize columns and add round number
            result_cols = [
                "HMDAIndex",
                "activity_year",
                "fhfa_year",
                "enterprise_flag",
                "record_number",
                "purchaser_type",
            ]
            combined = combined.select(result_cols).with_columns(
                pl.lit(round_num).cast(pl.UInt8).alias("match_round")
            )
            self._round_results.append(combined)

        self._stats["rounds"][round_num] = round_stats

    def _match_year_pair(
        self,
        config: MatchRoundConfig,
        hmda_year: int,
        fhfa_year: int,
    ) -> pl.DataFrame | None:
        """Match single HMDA year against single FHFA year.

        Args:
            config: Round configuration.
            hmda_year: HMDA activity year.
            fhfa_year: FHFA year.

        Returns:
            Matched records or None if no matches.
        """
        # Vintage-bridge rounds only fire on year pairs that cross the
        # 2010↔2020 Census tract vintage boundary (HMDA <=2021, FHFA >=2022).
        # Non-boundary pairs are already covered by other cross-year rounds.
        if config.merge.vintage_bridge and not needs_vintage_bridge(hmda_year, fhfa_year):
            return None

        # 1. Load HMDA with pre_merge filters
        hmda_lf, _ = load_hmda_year(hmda_year, config.pre_merge)

        # 2. Load FHFA
        fhfa_lf = load_fhfa_year(fhfa_year)

        # 2b. If vintage-bridging, translate FHFA's 2020-vintage census_string
        #     to its 2010-vintage candidate GEOIDs (one row per candidate).
        if config.merge.vintage_bridge:
            fhfa_lf = self._apply_vintage_bridge(fhfa_lf)

        # 3. Anti-join to exclude already matched
        hmda_lf = self._exclude_matched_hmda(hmda_lf)
        fhfa_lf = self._exclude_matched_fhfa(fhfa_lf)

        # 4. Merge on configured keys
        merged = self._merge(hmda_lf, fhfa_lf, config.merge)

        # 5. Apply post_merge filters
        filtered = apply_post_merge_filters(merged, config.post_merge)

        # 5.5 Apply quality-based selection to reduce duplicates
        filtered = apply_quality_selection(filtered)

        # 6. Same-year rounds: break same-fingerprint ties by closest borrower income (which the
        #    rate-based quality selection can't separate) so the 1:1 uniqueness filter resolves them
        #    instead of dropping them. Income only agrees same-year (FHFA income is inflated for
        #    prior-year acquisitions), so this is gated with uniqueness to same-year rounds.
        if config.merge.same_year_only:
            filtered = apply_income_tiebreak(filtered)
            filtered = apply_uniqueness(filtered)

        # 7. Select result columns and collect
        result_cols = [
            "HMDAIndex",
            "activity_year",
            "fhfa_year",
            "enterprise_flag",
            "record_number",
            "purchaser_type",
            "rate_diff",
            "term_diff",
        ]
        result = filtered.select(result_cols).collect()

        return result if len(result) > 0 else None

    def _exclude_matched_hmda(self, hmda_lf: pl.LazyFrame) -> pl.LazyFrame:
        """Exclude already matched HMDA records via anti-join."""
        if len(self._matched_hmda) > 0:
            hmda_lf = hmda_lf.join(
                self._matched_hmda.lazy().select("HMDAIndex"), on="HMDAIndex", how="anti"
            )
        return hmda_lf

    def _exclude_matched_fhfa(self, fhfa_lf: pl.LazyFrame) -> pl.LazyFrame:
        """Exclude already matched FHFA records via anti-join."""
        if len(self._matched_fhfa) > 0:
            fhfa_lf = fhfa_lf.join(
                self._matched_fhfa.lazy().select(["fhfa_year", "enterprise_flag", "record_number"]),
                on=["fhfa_year", "enterprise_flag", "record_number"],
                how="anti",
            )
        return fhfa_lf

    def _apply_vintage_bridge(self, fhfa_lf: pl.LazyFrame) -> pl.LazyFrame:
        """Translate FHFA's 2020-vintage census_string to 2010-vintage candidates.

        Inner-joins FHFA against the Census Bureau's 2010↔2020 tract
        relationship file on `census_string == GEOID_TRACT_20`, then replaces
        `census_string` with the corresponding `GEOID_TRACT_10`. Each FHFA
        record is duplicated once per overlapping 2010 tract — 66% of records
        map to one candidate, 34% to multiple.
        """
        rel = load_tract_relationship().select(
            pl.col("GEOID_TRACT_20").alias("census_string"),
            pl.col("GEOID_TRACT_10").alias("census_string_2010"),
        )
        return (
            fhfa_lf.join(rel, on="census_string", how="inner")
            .drop("census_string")
            .rename({"census_string_2010": "census_string"})
        )

    def _merge(
        self,
        hmda_lf: pl.LazyFrame,
        fhfa_lf: pl.LazyFrame,
        config: MergeConfig,
    ) -> pl.LazyFrame:
        """Merge HMDA and FHFA data based on configuration.

        Args:
            hmda_lf: HMDA data.
            fhfa_lf: FHFA data.
            config: Merge configuration.

        Returns:
            Merged data.
        """
        # Base join keys (without rate - rate handling varies by config)
        base_left_keys = ["census_tract", "loan_amount", "loan_type", "occupancy_type"]
        base_right_keys = ["census_string", "note_amount", "federal_guarantee", "occupancy_code"]

        # Add purchaser to join keys if configured
        if config.use_purchaser:
            base_left_keys = ["purchaser_type"] + base_left_keys
            base_right_keys = ["PurchaserType"] + base_right_keys

        if not config.use_rate:
            # No rate in join keys - match on other variables only
            # This catches loans with missing/wrong rate but valid other fields
            return hmda_lf.join(
                fhfa_lf, left_on=base_left_keys, right_on=base_right_keys, how="inner"
            )
        elif config.use_fuzzy_rates:
            # Fuzzy rate matching: match floor OR ceil to FHFA rate
            # Join on floor
            left_floor = base_left_keys + ["NoteRateFloor"]
            right_floor = base_right_keys + ["NoteRate125"]
            merged_floor = hmda_lf.join(
                fhfa_lf, left_on=left_floor, right_on=right_floor, how="inner"
            )

            # Join on ceil
            left_ceil = base_left_keys + ["NoteRateCeil"]
            right_ceil = base_right_keys + ["NoteRate125"]
            merged_ceil = hmda_lf.join(fhfa_lf, left_on=left_ceil, right_on=right_ceil, how="inner")

            # Combine and dedupe (same pair might match both floor and ceil)
            return pl.concat([merged_floor, merged_ceil], how="diagonal").unique()
        else:
            # Standard exact rate matching
            left_keys = base_left_keys + ["NoteRate125"]
            right_keys = base_right_keys + ["NoteRate125"]
            return hmda_lf.join(fhfa_lf, left_on=left_keys, right_on=right_keys, how="inner")

    def _update_matched(self, matches: pl.DataFrame, config: MatchRoundConfig) -> None:
        """Update matched sets with new matches.

        Args:
            matches: New matches to add.
            config: Round configuration (for logging).
        """
        if len(matches) == 0:
            return

        # Update HMDA matched
        new_hmda = matches.select(["HMDAIndex"])
        self._matched_hmda = pl.concat([self._matched_hmda, new_hmda], how="diagonal").unique()

        # Update FHFA matched
        new_fhfa = matches.select(["fhfa_year", "enterprise_flag", "record_number"])
        if len(self._matched_fhfa) > 0:
            self._matched_fhfa = pl.concat([self._matched_fhfa, new_fhfa], how="diagonal").unique()
        else:
            self._matched_fhfa = new_fhfa.unique()

    def _log_summary(self) -> None:
        """Log matching summary."""
        logger.info("MATCHING SUMMARY")

        for round_num, round_stats in self._stats["rounds"].items():
            desc = round_stats["description"]
            total = round_stats["total"]
            logger.info(f"Round {round_num} ({desc}): {total:,}")
            if round_stats.get("same_year", 0) > 0 or round_stats.get("cross_year", 0) > 0:
                logger.info(f"  - Same-year: {round_stats['same_year']:,}")
                logger.info(f"  - Cross-year: {round_stats['cross_year']:,}")
            if "post_unique" in round_stats:
                pu = round_stats["post_unique"]
                logger.info(
                    f"  - Post-unique: {pu['before']:,} -> {pu['after']:,} "
                    f"({pu['retained_pct']:.1f}% retained)"
                )

        summary = self._stats["summary"]
        logger.info(
            f"Total matched: {summary['total_matched']:,} / {summary['total_fhfa']:,} FHFA "
            f"({summary['fhfa_match_rate']:.2f}%)"
        )


def run_matching(
    years: list[int] | None = None,
    save: bool = True,
) -> dict:
    """Run the full multi-round matching workflow.

    Args:
        years: Years to process. Defaults to MIN_YEAR through MAX_YEAR.
        save: Whether to save crosswalk to disk. Defaults to True.

    Returns:
        Statistics for each round and overall summary.
    """
    from .config import FHFAHMDAMatchingConfig

    matcher = FHFAHMDAMatcher(years=years)
    result = matcher.run()

    # Save crosswalk if requested
    if save and result.round_results:
        FHFAHMDAMatchingConfig.ensure_directories()
        crosswalk = pl.concat(result.round_results, how="diagonal")

        # Build filename from year range
        years_list = years or list(range(MIN_YEAR, MAX_YEAR + 1))
        min_yr, max_yr = min(years_list), max(years_list)
        filename = f"fhfa_hmda_crosswalk_{min_yr}_{max_yr}.parquet"
        output_path = FHFAHMDAMatchingConfig.FHFA_HMDA_CROSSWALK_DIR / filename

        crosswalk.write_parquet(output_path)
        logger.info(f"Saved crosswalk ({len(crosswalk):,} rows) to {output_path}")

    return result.stats


if __name__ == "__main__":
    configure_logging(level="INFO")
    run_matching()
