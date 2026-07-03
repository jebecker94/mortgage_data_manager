"""Matching utilities and support functions for HMDA seller-purchaser matching.

This module combines all matching support functions and demographic matching
utilities into a single file for simplified imports.
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)

FilterCondition = tuple[str, str, object]


def select_best_file_type(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Filter a single-year HMDA frame to its most complete file_type vintage.

    Prefers the released LAR vintages in order ``a > b > c``, then falls back to
    any other type present -- notably the modified LAR ``e`` for the most recent
    year, before the standard vintages are published. A frame without a
    ``file_type`` column is returned unchanged.
    """
    if "file_type" not in lf.collect_schema().names():
        return lf
    file_types = lf.select("file_type").unique().collect()["file_type"].to_list()
    best_type = next((t for t in _FILE_TYPE_PREFERENCE if t in file_types), file_types[0])
    return lf.filter(pl.col("file_type") == best_type)


_FILE_TYPE_PREFERENCE = ("a", "b", "c", "e")


def scan_best_file_type(year_dir: Path) -> pl.LazyFrame:
    """Scan only the most-complete ``file_type`` partition under one year dir.

    Globbing ``year_dir/**/*.parquet`` scans every ``file_type=`` partition at
    once, which raises when vintages with divergent schemas coexist -- e.g. the
    2025 snapshot (``c``) sitting beside the modified-LAR (``e``), which carries
    extra columns. Selecting the partition before scanning keeps each scan
    schema-uniform while still preferring the most complete vintage available.
    """
    ft_dirs = {p.name.split("=", 1)[1]: p for p in year_dir.glob("file_type=*")}
    best = next((t for t in _FILE_TYPE_PREFERENCE if t in ft_dirs), None)
    target = ft_dirs[best] if best is not None else year_dir
    return pl.scan_parquet(target / "**/*.parquet", hive_partitioning=True)


def _concat_hmda_years(lazy_frames: list[pl.LazyFrame]) -> pl.LazyFrame:
    """Concatenate per-year HMDA frames, tolerating cross-year schema drift.

    Uses a diagonal concat so that columns added, dropped, or renamed in a given
    vintage (e.g. the MLAR ``e`` for the latest year carries a different set of
    non-match columns) union with null-fill instead of raising at scan time.
    """
    return pl.concat(lazy_frames, how="diagonal")


def _load_hmda_with_best_file_type(data_folder: Path) -> pl.LazyFrame:
    """Load HMDA data selecting best file_type per year (a > b > c).

    This avoids window functions that can block query optimization.
    """
    data_folder = Path(data_folder)
    year_dirs = sorted(data_folder.glob("activity_year=*"))
    lazy_frames = [scan_best_file_type(year_dir) for year_dir in year_dirs]
    return _concat_hmda_years(lazy_frames)


def get_match_columns(file: str | Path) -> list[str]:
    """Read the HMDA parquet metadata to identify usable match columns.

    Args:
        file: File to load for columns.

    Returns:
        Column names that should be retained for matching routines.
    """
    # Load File Column Names
    # columns = pq.read_metadata(file).schema.names
    columns = pl.scan_parquet(file).collect_schema().names()

    # Drop Columns Not Used in Match
    drop_columns = [
        "combined_loan_to_value_ratio",
        "prepayment_penalty_term",
        "submission_of_application",
        "initially_payable_to_institution",
        "debt_to_income_ratio",
        "applicant_credit_score_type",
        "co_applicant_credit_score_type",
        "rate_spread",
        "preapproval",
        "aus",
        "aus_1",
        "aus_2",
        "aus_3",
        "aus_4",
        "aus_5",
        "denial_reason",
        "denial_reason_1",
        "denial_reason_2",
        "denial_reason_3",
        "denial_reason_4",
        "tract_population",
        "tract_minority_population_percent",
        "ffiec_msa_md_median_family_income",
        "tract_to_msa_income_percentage",
        "tract_owner_occupied_units",
        "tract_one_to_four_family_homes",
        "tract_median_age_of_housing_units",
        "derived_loan_product_type",
        "derived_dwelling_category",
        "derived_ethnicity",
        "derived_race",
        "derived_sex",
        "file_type",
    ]
    columns = [x for x in columns if x not in drop_columns]

    # Return Columns
    return columns


def load_unmatched_data(
    data_folder: str | Path,
    crosswalk_folder: str | Path,
    filter_gse_sales: bool = True,
    match_round: int | None = None,
    min_year: int | None = None,
    max_year: int | None = None,
) -> pl.LazyFrame:
    """Load HMDA data and exclude records already matched in prior rounds.

    Loads originations (action_taken=1) and purchases (action_taken=6) from
    HMDA data, optionally filters out originations sold directly to GSEs,
    and excludes any records that appear in existing match crosswalks.

    Args:
        data_folder: Directory containing HMDA parquet files to load.
        crosswalk_folder: Directory containing existing match crosswalk parquet files.
        filter_gse_sales: If True, drops originations (action_taken=1) sold directly to GSEs
            (purchaser_type in [1,2,3,4]). Defaults to True.
        match_round: If not None, only load data from the specified match round.
        min_year: If not None, only load data from the specified year.
        max_year: If not None, only load data from the specified year.

    Returns:
        HMDA data with originations and purchases, excluding previously matched records.

    Examples:
        >>> data_folder = Path("data/hmda/post2018")
        >>> crosswalk_folder = Path("data/matches/post2018")
        >>> df = load_unmatched_data(data_folder, crosswalk_folder)
    """
    # Load HMDA data
    data_folder = Path(data_folder)

    # Select best file_type per year (a > b > c) without window functions
    # This pattern avoids materializing all data for a window function
    year_dirs = sorted(data_folder.glob("activity_year=*"))
    lazy_frames = []
    for year_dir in year_dirs:
        year = int(year_dir.name.split("=")[1])

        # Apply year filter early if specified
        if min_year is not None and year < min_year:
            continue
        if max_year is not None and year > max_year:
            continue

        lf = scan_best_file_type(year_dir)

        lazy_frames.append(lf)

    df = _concat_hmda_years(lazy_frames)

    # Filter to originations and purchases
    df = df.filter(pl.col("action_taken").is_in([1, 6]))

    # Note: year filtering already applied above when selecting year directories

    # Filter for originations sold to non-GSEs
    if filter_gse_sales:
        df = df.filter(
            (~pl.col("purchaser_type").is_in([1, 2, 3, 4])) | (pl.col("action_taken") == 6),
        )

    # Load existing match crosswalk
    cw = pl.scan_parquet(
        Path(crosswalk_folder) / "**/*.parquet",
        schema={
            "HMDAIndex_s": pl.Utf8,
            "HMDAIndex_p": pl.Utf8,
            "activity_year_s": pl.Int32,
            "MatchRound": pl.Int32,
        },
    )
    if match_round is not None:
        cw = cw.filter(pl.col("MatchRound") < match_round)

    # Exclude records already matched (as sellers or purchasers)
    df = df.join(cw, how="anti", right_on="HMDAIndex_s", left_on="HMDAIndex")
    df = df.join(cw, how="anti", right_on="HMDAIndex_p", left_on="HMDAIndex")

    return df


def split_originations_purchases(lf: pl.LazyFrame) -> tuple[pl.LazyFrame, pl.LazyFrame]:
    """Split combined HMDA data into originations and purchases using Polars.

    Args:
        lf: Combined HMDA data with both originations (action_taken=1) and purchases (action_taken=6).

    Returns:
        tuple of (originations, purchases)
    """
    originations = lf.filter(pl.col("action_taken") == 1)
    purchases = lf.filter(pl.col("action_taken") == 6)
    return originations, purchases


def merge_sellers_and_purchasers(
    lf_seller: pl.LazyFrame,
    lf_purchaser: pl.LazyFrame,
    match_columns: list[str],
) -> pl.LazyFrame:
    """Merge sellers and purchasers using Polars.

    Args:
        lf_seller: Sellers data.
        lf_purchaser: Purchasers data.
        match_columns: Match columns to use.

    Returns:
        Merged sellers and purchasers data.
    """
    # Rename seller and purchaser columns
    lf_seller = lf_seller.rename({col: f"{col}_s" for col in lf_seller.collect_schema().names()})
    lf_purchaser = lf_purchaser.rename(
        {col: f"{col}_p" for col in lf_purchaser.collect_schema().names()}
    )
    seller_match_columns = [
        f"{col}_s" for col in match_columns if f"{col}_s" in lf_seller.collect_schema().names()
    ]
    purchaser_match_columns = [
        f"{col}_p" for col in match_columns if f"{col}_p" in lf_purchaser.collect_schema().names()
    ]

    # Merge sellers and purchasers
    lf = lf_seller.join(
        lf_purchaser, left_on=seller_match_columns, right_on=purchaser_match_columns, how="inner"
    )

    return lf


def convert_numerics(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Convert key numeric columns to proper numeric types using Polars.

    Args:
        lf: HMDA data with potentially string numeric columns.

    Returns:
        Data with numeric columns properly typed.
    """
    numeric_columns = [
        "loan_amount",
        "income",
        "property_value",
        "interest_rate",
        "loan_term",
        "discount_points",
        "origination_charges",
        "total_loan_costs",
        "total_points_and_fees",
        "lender_credits",
    ]

    col_names = lf.collect_schema().names()

    # Convert columns to float64 if they exist
    conversions = []
    for col in numeric_columns:
        if col in col_names:
            conversions.append(pl.col(col).cast(pl.Float64, strict=False).alias(col))

    if conversions:
        lf = lf.with_columns(conversions)

    return lf


def add_fee_exemption_indicators(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Add fee exemption indicator columns to identify loans exempt from fee reporting.

    Creates two indicator columns:
    - `i_ExemptFromFeesStrict`: True when all fee columns equal 1111
    - `i_ExemptFromFeesWeak`: True when any fee column equals 1111

    Args:
        lf: HMDA data with fee columns.

    Returns:
        Data with fee exemption indicator columns added.
    """
    fee_columns = [
        "total_loan_costs",
        "total_points_and_fees",
        "origination_charges",
        "discount_points",
        "lender_credits",
    ]

    col_names = lf.collect_schema().names()

    # Only add indicators if all fee columns are present
    if all(col in col_names for col in fee_columns):
        # Build expressions for each fee column equaling 1111
        fee_equals_1111 = [pl.col(col) == 1111 for col in fee_columns]

        # Strict: all fee columns equal 1111
        strict_expr = pl.all_horizontal(fee_equals_1111)

        # Weak: any fee column equals 1111
        weak_expr = pl.any_horizontal(fee_equals_1111)

        lf = lf.with_columns(
            [
                strict_expr.cast(pl.Boolean).alias("i_ExemptFromFeesStrict"),
                weak_expr.cast(pl.Boolean).alias("i_ExemptFromFeesWeak"),
            ]
        )

    return lf


def replace_negative_numerics(lf: pl.LazyFrame, columns: list[str] | None = None) -> pl.LazyFrame:
    """Replace negative or zero numeric values with null.

    Some HMDA fields should not have negative or zero values. This function
    replaces such values with null for specified columns.

    Args:
        lf: HMDA data with potentially invalid numeric values.
        columns: List of column names to process. If None, processes a default set
            of columns that should not have negative or zero values.

    Returns:
        Data with negative/zero values replaced by null.
    """
    if columns is None:
        columns = [
            "conforming_loan_limit",
            "construction_method",
            "income",
            "total_units",
            "lien_status",
            "multifamily_affordable_units",
            "total_loan_costs",
            "total_points_and_fees",
            "discount_points",
            "lender_credits",
            "origination_charges",
            "interest_rate",
            "intro_rate_period",
            "loan_term",
            "property_value",
            "balloon_payment",
            "interest_only_payment",
            "negative_amortization",
            "open_end_line_of_credit",
            "other_nonamortizing_features",
            "prepayment_penalty_term",
            "reverse_mortgage",
            "business_or_commercial_purpose",
            "manufactured_home_land_property_",
            "manufactured_home_secured_proper",
        ]

    col_names = lf.collect_schema().names()

    replacements = []
    for col in columns:
        if col in col_names:
            replacements.append(
                pl.when(
                    pl.col(col).cast(pl.Float64, strict=False).is_not_null()
                    & (pl.col(col).cast(pl.Float64, strict=False) <= 0)
                )
                .then(pl.lit(None))
                .otherwise(pl.col(col))
                .alias(col)
            )

    if replacements:
        lf = lf.with_columns(replacements)

    return lf


def fix_intro_rate_period_edge_case(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Fix edge case where intro_rate_period equals loan_term.

    When intro_rate_period equals loan_term, it's likely a data entry error
    or indicates no introductory rate period. This function sets intro_rate_period
    to null in such cases.

    Args:
        lf: HMDA data with intro_rate_period and loan_term columns.

    Returns:
        Data with intro_rate_period set to null when it equals loan_term.
    """
    col_names = lf.collect_schema().names()

    if {"intro_rate_period", "loan_term"}.issubset(set(col_names)):
        lf = lf.with_columns(
            [
                pl.when(pl.col("intro_rate_period") == pl.col("loan_term"))
                .then(pl.lit(None))
                .otherwise(pl.col("intro_rate_period"))
                .alias("intro_rate_period")
            ]
        )

    return lf


def drop_zeros(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Replace zeros with null for specified columns using Polars.

    Replaces zero values with null for columns where zeros are unlikely to represent
    true values (e.g., income, fees, intro_rate_period).

    Args:
        lf: HMDA data potentially containing zero values.

    Returns:
        Data with zeros replaced by null values for specified columns.
    """
    zero_columns = [
        "income",
        "discount_points",
        "lender_credits",
        "origination_charges",
        "intro_rate_period",
        "total_loan_costs",
        "total_points_and_fees",
    ]

    col_names = lf.collect_schema().names()

    # Replace zeros with null
    replacements = []
    for col in zero_columns:
        if col in col_names:
            replacements.append(
                pl.when(pl.col(col) == 0).then(pl.lit(None)).otherwise(pl.col(col)).alias(col)
            )

    if replacements:
        lf = lf.with_columns(replacements)

    return lf


def replace_missing_values(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Replace HMDA missing value codes with null using Polars.

    Args:
        lf: HMDA data with missing value codes.

    Returns:
        Data with missing codes replaced by null values.
    """
    missing_codes = [9999, 8888, 1111, -99999]  # Common HMDA missing codes

    numeric_columns = [
        # 'loan_amount',
        "income",
        "property_value",
        "interest_rate",
        "loan_term",
        "discount_points",
        "origination_charges",
        "total_loan_costs",
        "total_points_and_fees",
        "lender_credits",
        "conforming_loan_limit",
        "construction_method",
        "total_units",
        "lien_status",
        "multifamily_affordable_units",
        "intro_rate_period",
        "balloon_payment",
        "interest_only_payment",
        "negative_amortization",
        "open_end_line_of_credit",
        "other_nonamortizing_features",
        "prepayment_penalty_term",
        "reverse_mortgage",
        "business_or_commercial_purpose",
        "manufactured_home_land_property_",
        "manufactured_home_secured_property",
    ]
    col_names = lf.collect_schema().names()

    # Replace missing codes with null
    replacements = []
    for col in numeric_columns:
        if col in col_names:
            replacements.append(
                pl.when(pl.col(col).is_in(missing_codes))
                .then(pl.lit(None))
                .otherwise(pl.col(col))
                .alias(col)
            )

    if replacements:
        lf = lf.with_columns(replacements)

    return lf


def numeric_matches(
    lf: pl.LazyFrame,
    tolerances: dict[str, float],
) -> pl.LazyFrame:
    """Apply numeric tolerance matching using Polars.

    Args:
        lf: Candidate matches with seller/purchaser pairs.
        tolerances: Dictionary mapping column names to tolerance values.

    Returns:
        Filtered matches where numeric differences are within tolerances.
    """
    col_names = lf.collect_schema().names()

    for column, tolerance in tolerances.items():
        col_s = f"{column}_s"
        col_p = f"{column}_p"

        if col_s in col_names and col_p in col_names:
            log_counts = logger.isEnabledFor(logging.DEBUG)
            start_count = lf.select(pl.count()).collect().item() if log_counts else 0

            # Apply tolerance filter (allow null values through)
            lf = lf.filter(
                ((pl.col(col_s) - pl.col(col_p)).abs() <= tolerance)
                | pl.col(col_s).is_null()
                | pl.col(col_p).is_null()
            )

            if log_counts:
                end_count = lf.select(pl.count()).collect().item()
                logger.debug(f"    {column} tolerance filter: {start_count:,} → {end_count:,}")

    return lf


def weak_numeric_matches(
    lf: pl.LazyFrame,
    tolerances: dict[str, float],
) -> pl.LazyFrame:
    """Apply weak numeric matching (keeps best match per loan) using Polars.

    Args:
        lf: Candidate matches with seller/purchaser pairs.
        tolerances: Dictionary mapping column names to tolerance values.

    Returns:
        Filtered matches keeping only the best matches per seller/purchaser.
    """
    col_names = lf.collect_schema().names()
    for column, tolerance in tolerances.items():
        col_s = f"{column}_s"
        col_p = f"{column}_p"

        if col_s in col_names and col_p in col_names:
            log_counts = logger.isEnabledFor(logging.DEBUG)
            start_count = lf.select(pl.count()).collect().item() if log_counts else 0

            # Calculate absolute differences
            lf = lf.with_columns([(pl.col(col_s) - pl.col(col_p)).abs().alias("abs_diff")])

            # Find minimum difference for each seller and purchaser
            lf = lf.with_columns(
                [
                    pl.col("abs_diff").min().over("HMDAIndex_s").alias("min_diff_s"),
                    pl.col("abs_diff").min().over("HMDAIndex_p").alias("min_diff_p"),
                ]
            )

            # Keep if within tolerance OR if this is the best match for seller/purchaser
            lf = lf.filter(
                (pl.col("abs_diff") <= tolerance)
                | (pl.col("min_diff_s") > 0)
                | (pl.col("min_diff_p") > 0)
                | pl.col("abs_diff").is_null()
            )

            # Clean up temporary columns
            lf = lf.drop(["abs_diff", "min_diff_s", "min_diff_p"])

            if log_counts:
                end_count = lf.select(pl.count()).collect().item()
                logger.debug(f"    {column} weak filter: {start_count:,} → {end_count:,}")

    return lf


def perform_fee_matches(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Apply fee matching filters using Polars.

    Args:
        lf: Candidate matches with seller/purchaser pairs.

    Returns:
        Matches with fee match counts added and filtered.
    """
    # Fee columns to check
    fee_columns = [
        "total_loan_costs",
        "total_points_and_fees",
        "origination_charges",
        "discount_points",
        "lender_credits",
    ]
    col_names = lf.collect_schema().names()

    # Count fee matches and non-missing fees
    fee_match_exprs = []
    nonmissing_s_exprs = []
    nonmissing_p_exprs = []

    for fee_col in fee_columns:
        col_s = f"{fee_col}_s"
        col_p = f"{fee_col}_p"

        if col_s in col_names and col_p in col_names:
            # Count exact matches (excluding null)
            fee_match_exprs.append(
                (pl.col(col_s) == pl.col(col_p))
                & pl.col(col_s).is_not_null()
                & pl.col(col_p).is_not_null()
            )

            # Count non-missing values
            nonmissing_s_exprs.append(pl.col(col_s).is_not_null())
            nonmissing_p_exprs.append(pl.col(col_p).is_not_null())

    # Create generous fee match dummy (if any fee column matches another fee column)
    generous_exprs = [
        ((pl.col(f"{var1}_s") == pl.col(f"{var2}_p")) & pl.col(f"{var1}_s").is_not_null())
        for var1 in fee_columns
        for var2 in fee_columns
    ]

    # Sum up fee matches and non-missing counts
    lf = lf.with_columns(
        [
            pl.sum_horizontal(fee_match_exprs).cast(pl.Int32).alias("NumberFeeMatches"),
            pl.sum_horizontal(nonmissing_s_exprs).cast(pl.Int32).alias("NumberNonmissingFees_s"),
            pl.sum_horizontal(nonmissing_p_exprs).cast(pl.Int32).alias("NumberNonmissingFees_p"),
            pl.any_horizontal(generous_exprs).cast(pl.Int64).alias("i_GenerousFeeMatch"),
        ]
    )

    return lf


def numeric_matches_post_unique(
    lf: pl.LazyFrame,
    tolerances: dict[str, float],
) -> pl.LazyFrame:
    """Apply numeric tolerance matching after uniqueness constraints using Polars.

    This function marks observations for dropping if they exceed tolerances,
    then drops entire sales (HMDAIndex_s) if any observation exceeds tolerances.

    Args:
        lf: Candidate matches with seller/purchaser pairs (after uniqueness filtering).
        tolerances: Dictionary mapping column names to tolerance values.

    Returns:
        Filtered matches where numeric differences are within tolerances.
    """
    log_counts = logger.isEnabledFor(logging.DEBUG)
    start_count = lf.select(pl.count()).collect().item() if log_counts else 0

    col_names = lf.collect_schema().names()

    # Initialize drop observation flag
    lf = lf.with_columns(pl.lit(0).cast(pl.Int8).alias("i_DropObservation"))

    # Check each tolerance
    for column, tolerance in tolerances.items():
        col_s = f"{column}_s"
        col_p = f"{column}_p"

        if col_s in col_names and col_p in col_names:
            # Mark observations that exceed tolerance
            lf = lf.with_columns(
                pl.when(
                    ((pl.col(col_s) - pl.col(col_p)).abs() > tolerance)
                    & pl.col(col_s).is_not_null()
                    & pl.col(col_p).is_not_null()
                )
                .then(pl.lit(1))
                .otherwise(pl.col("i_DropObservation"))
                .alias("i_DropObservation")
            )

    # Mark entire sale for dropping if any observation exceeds tolerance
    lf = lf.with_columns(pl.col("i_DropObservation").max().over("HMDAIndex_s").alias("i_DropSale"))

    # Filter out sales with any bad matches
    lf = lf.filter(pl.col("i_DropSale") == 0)

    # Drop temporary columns
    lf = lf.drop(["i_DropObservation", "i_DropSale"])

    if log_counts:
        end_count = lf.select(pl.count()).collect().item()
        logger.debug(f"    Post-unique numeric filter: {start_count:,} → {end_count:,}")

    return lf


def keep_matches_post_unique(
    lf: pl.LazyFrame,
    one_to_one: bool = True,
) -> pl.LazyFrame:
    """Re-apply uniqueness constraints after other filtering steps using Polars.

    This function re-applies uniqueness constraints after filtering steps have
    been applied (e.g., after numeric_matches_post_unique), ensuring that
    matches remain unique even after tolerances have been relaxed or re-applied.

    Args:
        lf: Candidate matches with seller/purchaser pairs (after numeric filtering).
        one_to_one: Whether to enforce strict one-to-one matching. If False, allows
            one-to-many matches for secondary sales. Default is True.

    Returns:
        Filtered matches with uniqueness constraints re-applied.
    """
    log_counts = logger.isEnabledFor(logging.DEBUG)
    start_count = lf.select(pl.count()).collect().item() if log_counts else 0

    # Count how many matches each seller and purchaser has
    lf = lf.with_columns(
        [
            pl.count().over("HMDAIndex_s").alias("count_seller"),
            pl.count().over("HMDAIndex_p").alias("count_purchaser"),
        ]
    )

    # Keep Purchased Loans w/ Unique Match
    lf = lf.filter(pl.col("count_purchaser") == 1)

    # Keep Uniques
    if one_to_one:
        lf = lf.filter(pl.col("count_seller") == 1)
    else:
        # Keep Loans Where Sale Matches Multiple Purchases if One Known to Be Secondary Sale
        lf = lf.with_columns(
            (pl.col("purchaser_type_p") > 4).cast(pl.Int8).alias("i_SecondarySale")
        )
        lf = lf.with_columns(
            pl.col("i_SecondarySale").max().over("HMDAIndex_s").alias("i_LoanHasSecondarySale")
        )
        lf = lf.filter(
            (pl.col("count_seller") == 1)
            | ((pl.col("count_seller") == 2) & (pl.col("i_LoanHasSecondarySale") == 1))
        )
        lf = lf.drop(["i_SecondarySale", "i_LoanHasSecondarySale"])

    if log_counts:
        end_count = lf.select(pl.count()).collect().item()
        logger.debug(f"    Post-numeric uniqueness filter: {start_count:,} → {end_count:,}")

    # Clean up count columns
    lf = lf.drop(["count_seller", "count_purchaser"])

    return lf


def keep_uniques(lf: pl.LazyFrame, one_to_one: bool = True) -> pl.LazyFrame:
    """Keep only unique matches (one-to-one) using Polars.

    Args:
        lf: Candidate matches with seller/purchaser pairs.
        one_to_one: Whether to keep only one-to-one matches.

    Returns:
        Only unique one-to-one matches.
    """
    # Count how many matches each seller and purchaser has
    lf = lf.with_columns(
        [
            pl.count().over("HMDAIndex_s").alias("count_seller"),
            pl.count().over("HMDAIndex_p").alias("count_purchaser"),
        ]
    )

    # Keep Purchased Loans w/ Unique Match
    lf = lf.filter(pl.col("count_purchaser") == 1)

    # Keep Uniques
    if one_to_one:
        lf = lf.filter(pl.col("count_seller") == 1)

    # Keep Loans Where Sale Matches Multiple Purchases if One Known to Be Secondary Sale
    else:
        lf = lf.with_columns(
            (pl.col("purchaser_type_p") > 4).cast(pl.Int8).alias("i_SecondarySale")
        )
        lf = lf.with_columns(
            pl.col("i_SecondarySale").max().over("HMDAIndex_s").alias("i_LoanHasSecondarySale")
        )
        lf = lf.filter(
            (pl.col("count_seller") == 1)
            | ((pl.col("count_seller") == 2) & (pl.col("i_LoanHasSecondarySale") == 1))
        )
        lf = lf.drop(["i_SecondarySale", "i_LoanHasSecondarySale"])

    # Clean up count columns
    lf = lf.drop(["count_seller", "count_purchaser"])

    return lf


def save_crosswalk(df: pl.LazyFrame, save_folder: str | Path, match_round: int) -> None:
    """Save match results to parquet file using Polars.

    Args:
        df: Final match results to save.
        save_folder: Directory to save the file.
        match_round: Round number for filename.
    """
    df = df.select(["HMDAIndex_s", "HMDAIndex_p", "activity_year_s"])
    df = df.with_columns(pl.lit(match_round).alias("MatchRound"))
    df = df.collect()
    df.write_parquet(
        pl.PartitionByKey(
            base_path=save_folder,
            by=["MatchRound", "activity_year_s"],
        ),
        mkdir=True,
    )


def get_affiliates(
    match_folder: Path,
    data_folder: Path,
    match_round: int,
    strict: bool = False,
) -> pl.DataFrame:
    """Identify affiliate LEI pairs from matched loan outputs using Polars.

    Args:
        match_folder: Directory with matched loan parquet files.
        data_folder: Directory with HMDA data parquet files.
        match_round: Round identifier used when naming matched files.
        strict: If True, require loans to have exactly one matched purchaser. Defaults to False.

    Returns:
        Affiliate LEI combinations with columns ``lei_s`` and ``lei_p``.
    """
    match_folder = Path(match_folder)
    matched_path = match_folder / "**/*.parquet"
    data_folder = Path(data_folder)

    # Load crosswalk
    cw = pl.scan_parquet(matched_path)

    # Filter out later rounds
    cw = cw.filter(pl.col("MatchRound") <= match_round)
    cw = cw.select(["HMDAIndex_s", "HMDAIndex_p"]).unique()

    # Load HMDA data with proper file_type selection
    df = _load_hmda_with_best_file_type(data_folder)
    df_s_loans = df.rename({x: f"{x}_s" for x in df.collect_schema().names()})
    df_p_loans = df.rename({x: f"{x}_p" for x in df.collect_schema().names()})
    df_s_loans = df_s_loans.join(cw, on="HMDAIndex_s", how="inner")
    df_p_loans = df_p_loans.join(cw, on="HMDAIndex_p", how="inner")
    df = df_s_loans.join(df_p_loans, on=["HMDAIndex_s", "HMDAIndex_p"], how="inner")

    # Count sold loans
    df = df.with_columns(pl.count().over("HMDAIndex_s").alias("CountSoldLoan"))

    # Decide how to handle multiple matches
    if strict:
        df = df.filter(pl.col("CountSoldLoan") == 1)
    else:
        df = df.filter(
            (pl.col("CountSoldLoan") == 1) | (~pl.col("purchaser_type_s").is_in([1, 2, 3, 4]))
        )
        df = df.with_columns(pl.count().over("HMDAIndex_s").alias("CountSoldLoan"))
        df = df.filter(pl.col("CountSoldLoan") == 1)

    # Drop loans with unknown purchaser type
    df = df.filter(pl.col("purchaser_type_s") != 0)

    # Count LEI matches
    df = df.with_columns(
        pl.count().over(["lei_s", "lei_p", "activity_year_s"]).alias("CountLEIMatches"),
    )
    df = df.with_columns(
        pl.count()
        .over(["lei_s", "lei_p", "activity_year_s", "purchaser_type_s"])
        .alias("CountLEIPurchaserTypeMatches"),
    )
    df = df.with_columns(
        pl.when(pl.col("CountLEIMatches") > 0)
        .then(pl.col("CountLEIPurchaserTypeMatches") / pl.col("CountLEIMatches"))
        .otherwise(None)
        .alias("share_type_match"),
    )

    # Keep only good matches
    df = df.filter(pl.col("purchaser_type_s") == 8)
    df = df.with_columns(
        pl.col("HMDAIndex_s")
        .rank("dense")
        .over(["lei_s", "lei_p", "activity_year_s"])
        .alias("match_rank")
    )

    # Get unique matches
    df = df.unique(subset=["lei_s", "lei_p", "activity_year_s"])

    # Keep only good matches
    df = df.filter(
        (pl.col("CountLEIPurchaserTypeMatches") >= 10) & (pl.col("share_type_match") >= 0.95)
    )

    # Get unique affiliates
    affiliates = df.select(["lei_s", "lei_p"]).unique().sort(["lei_s", "lei_p"])

    return affiliates


def get_purchaser_type_counts(
    match_folder: Path,
    data_folder: Path,
    match_round: int,
    strict: bool = False,
) -> pl.LazyFrame:
    """Identify affiliate LEI pairs from matched loan outputs using Polars.

    Args:
        match_folder: Directory with matched loan parquet files.
        data_folder: Directory with HMDA data parquet files.
        match_round: Round identifier used when naming matched files.
        strict: If True, require loans to have exactly one matched purchaser. Defaults to False.

    Returns:
        Affiliate LEI combinations with columns ``lei_s`` and ``lei_p``.
    """
    match_folder = Path(match_folder)
    data_folder = Path(data_folder)

    cw = (
        pl.scan_parquet(match_folder / "**/*.parquet")
        .filter(pl.col("MatchRound") <= match_round)
        .select(["HMDAIndex_s", "HMDAIndex_p"])
        .unique()
    )

    # Load HMDA data with proper file_type selection
    df = _load_hmda_with_best_file_type(data_folder)
    df_s = df.rename({c: f"{c}_s" for c in df.collect_schema().names()})
    df_p = df.rename({c: f"{c}_p" for c in df.collect_schema().names()})
    df_s = df_s.join(cw, on="HMDAIndex_s", how="inner")
    df_p = df_p.join(cw, on="HMDAIndex_p", how="inner")
    df = df_s.join(df_p, on=["HMDAIndex_s", "HMDAIndex_p"], how="inner")

    df = df.with_columns(pl.len().over("HMDAIndex_s").alias("CountSoldLoan"))
    if strict:
        df = df.filter(pl.col("CountSoldLoan") == 1)
    else:
        df = df.filter(
            (pl.col("CountSoldLoan") == 1) | (~pl.col("purchaser_type_s").is_in([1, 2, 3, 4]))
        ).with_columns(pl.count().over("HMDAIndex_s").alias("CountSoldLoan"))
        df = df.filter(pl.col("CountSoldLoan") == 1)

    df = df.filter(pl.col("purchaser_type_s") != 0)

    df = df.with_columns(
        pl.count().over(["lei_s", "lei_p", "activity_year_s"]).alias("CountLEIMatches"),
        pl.count()
        .over(["lei_s", "lei_p", "activity_year_s", "purchaser_type_s"])
        .alias("CountLEIPurchaserTypeMatches"),
    ).with_columns(
        pl.when(pl.col("CountLEIMatches") > 0)
        .then(pl.col("CountLEIPurchaserTypeMatches") / pl.col("CountLEIMatches"))
        .otherwise(None)
        .alias("share_type_match")
    )

    df = df.filter(
        (pl.col("CountLEIPurchaserTypeMatches") >= 50) & (pl.col("share_type_match") >= 0.99)
    )

    result = (
        df.select(
            "lei_s",
            "lei_p",
            "purchaser_type_s",
            "activity_year_s",
            "CountLEIMatches",
            "CountLEIPurchaserTypeMatches",
        )
        .unique(subset=["lei_s", "lei_p", "activity_year_s", "purchaser_type_s"])
        .sort(["lei_s", "lei_p", "activity_year_s", "purchaser_type_s"])
    )

    return result


def get_lei_relationships(
    match_folder: Path,
    data_folder: Path,
    match_round: int,
    strict: bool = False,
    min_matches: int = 50,
) -> pl.LazyFrame:
    """Identify affiliate LEI pairs from matched loan outputs using Polars.

    Args:
        match_folder: Directory with matched loan parquet files.
        data_folder: Directory with HMDA data parquet files.
        match_round: Round identifier used when naming matched files.
        strict: If True, require loans to have exactly one matched purchaser. Defaults to False.
        min_matches: Minimum number of matches required to consider a LEI pair. Defaults to 50.

    Returns:
        Affiliate LEI combinations with columns ``lei_s`` and ``lei_p``.
    """
    match_folder = Path(match_folder)
    data_folder = Path(data_folder)

    cw = (
        pl.scan_parquet(match_folder / "**/*.parquet")
        .filter(pl.col("MatchRound") <= match_round)
        .select(["HMDAIndex_s", "HMDAIndex_p"])
        .unique()
    )

    # Load HMDA data with proper file_type selection
    df = _load_hmda_with_best_file_type(data_folder)
    df_s = df.rename({c: f"{c}_s" for c in df.collect_schema().names()})
    df_p = df.rename({c: f"{c}_p" for c in df.collect_schema().names()})
    df_s = df_s.join(cw, on="HMDAIndex_s", how="inner")
    df_p = df_p.join(cw, on="HMDAIndex_p", how="inner")
    df = df_s.join(df_p, on=["HMDAIndex_s", "HMDAIndex_p"], how="inner")

    df = df.with_columns(pl.len().over("HMDAIndex_s").alias("CountSoldLoan"))
    if strict:
        df = df.filter(pl.col("CountSoldLoan") == 1)
    else:
        df = df.filter(
            (pl.col("CountSoldLoan") == 1) | (~pl.col("purchaser_type_s").is_in([1, 2, 3, 4]))
        ).with_columns(pl.count().over("HMDAIndex_s").alias("CountSoldLoan"))
        df = df.filter(pl.col("CountSoldLoan") == 1)

    df = df.with_columns(
        pl.count().over(["lei_s", "lei_p", "activity_year_s"]).alias("CountLEIMatches")
    )

    df = df.filter(pl.col("CountLEIMatches") >= min_matches)

    return df.select(["lei_s", "lei_p", "activity_year_s", "CountLEIMatches"]).unique()


def apply_quality_match_filters(df: pl.LazyFrame) -> pl.LazyFrame:
    """Apply quality-based match filtering using fee, rate, and income indicators.

    Creates indicators for good matches based on fee matches, rate differences,
    and income differences. Then filters to keep only the best matches when
    better alternatives exist.

    Args:
        df: Candidate matches with fee match columns already created via perform_fee_matches().

    Returns:
        Filtered matches keeping only high-quality matches.
    """
    # Create difference columns
    df = df.with_columns(
        [
            (pl.col("NumberFeeMatches") >= 2).cast(pl.Int8).alias("i_GoodMatch"),
            (pl.col("interest_rate_s") - pl.col("interest_rate_p")).alias("RateDifference"),
            (pl.col("income_s") - pl.col("income_p")).alias("IncomeDifference"),
        ]
    )

    # Create rate and income match indicators
    df = df.with_columns(
        [
            pl.when(pl.col("RateDifference").abs() < 0.001)
            .then(1)
            .when(pl.col("interest_rate_s").is_null() | pl.col("interest_rate_p").is_null())
            .then(None)
            .otherwise(0)
            .cast(pl.Int8)
            .alias("i_GoodRateMatch"),
            pl.when(pl.col("income_s") == pl.col("income_p"))
            .then(1)
            .when(pl.col("income_s").is_null() | pl.col("income_p").is_null())
            .then(None)
            .otherwise(0)
            .cast(pl.Int8)
            .alias("i_PerfectIncomeMatch"),
        ]
    )

    # Create window indicators
    df = df.with_columns(
        [
            pl.col("i_GoodMatch").max().over("HMDAIndex_s").alias("i_SaleHasFeeMatch"),
            pl.col("i_GoodMatch").max().over("HMDAIndex_p").alias("i_PurchaseHasFeeMatch"),
            pl.col("i_GoodRateMatch").max().over("HMDAIndex_s").alias("i_SaleHasRateMatch"),
            pl.col("i_GoodRateMatch").max().over("HMDAIndex_p").alias("i_PurchaseHasRateMatch"),
            pl.col("i_PerfectIncomeMatch").max().over("HMDAIndex_s").alias("i_SaleHasIncomeMatch"),
            pl.col("i_PerfectIncomeMatch")
            .max()
            .over("HMDAIndex_p")
            .alias("i_PurchaseHasIncomeMatch"),
        ]
    )

    # Apply filters: keep best matches when better exists
    df = df.filter((pl.col("i_GenerousFeeMatch") == 1) | (pl.col("i_SaleHasFeeMatch") == 0))
    df = df.filter((pl.col("i_GenerousFeeMatch") == 1) | (pl.col("i_PurchaseHasFeeMatch") == 0))
    df = df.filter((pl.col("RateDifference").abs() < 0.001) | (pl.col("i_SaleHasRateMatch") != 1))
    df = df.filter(
        (pl.col("RateDifference").abs() < 0.001) | (pl.col("i_PurchaseHasRateMatch") != 1)
    )
    df = df.filter((pl.col("IncomeDifference").abs() <= 1) | (pl.col("i_SaleHasIncomeMatch") != 1))
    df = df.filter(
        (pl.col("IncomeDifference").abs() <= 1) | (pl.col("i_PurchaseHasIncomeMatch") != 1)
    )

    return df


def apply_drop_observation_cleanup(df: pl.LazyFrame) -> pl.LazyFrame:
    """Drop observations with missing key fields or rate mismatches.

    Marks observations for dropping if they have rate differences >= 0.005
    or are missing income, interest_rate, or property_value. Then drops
    entire sales if any observation is marked.

    Args:
        df: Candidate matches with seller/purchaser pairs.

    Returns:
        Filtered matches with problematic observations removed.
    """
    required_columns = {
        "interest_rate_s",
        "interest_rate_p",
        "income_s",
        "property_value_s",
        "HMDAIndex_s",
    }
    if not required_columns.issubset(df.collect_schema().names()):
        return df

    drop_condition = (
        ((pl.col("interest_rate_s") - pl.col("interest_rate_p")).abs() >= 0.005)
        | pl.col("income_s").is_null()
        | pl.col("interest_rate_s").is_null()
        | pl.col("property_value_s").is_null()
    )
    df = df.with_columns(drop_condition.alias("i_DropObservation"))
    df = df.with_columns(pl.col("i_DropObservation").max().over("HMDAIndex_s").alias("i_DropSale"))
    return df.filter(pl.col("i_DropSale") != 1)


def filter_refi_types(df: pl.LazyFrame) -> pl.LazyFrame:
    """Allow non-matching refi types (codes 31, 32) to match.

    Filters to keep matches where loan purposes match exactly, or where
    either the seller or purchaser has a refi type (31 or 32).

    Args:
        df: Candidate matches with loan_purpose_s and loan_purpose_p columns.

    Returns:
        Filtered matches allowing refi type flexibility.
    """
    return df.filter(
        (pl.col("loan_purpose_s") == pl.col("loan_purpose_p"))
        | pl.col("loan_purpose_s").is_in([31, 32])
        | pl.col("loan_purpose_p").is_in([31, 32])
    )


def filter_generous_fee_match(df: pl.LazyFrame) -> pl.LazyFrame:
    """Keep matches with generous fee match or missing fee data.

    Filters to keep matches where there's a generous fee match (any fee
    column matches any other fee column), or where either side is missing
    fee data entirely.

    Args:
        df: Candidate matches with i_GenerousFeeMatch and NumberNonmissingFees columns.

    Returns:
        Filtered matches with acceptable fee matching.
    """
    return df.filter(
        (pl.col("i_GenerousFeeMatch") == 1)
        | (pl.col("NumberNonmissingFees_s") == 0)
        | (pl.col("NumberNonmissingFees_p") == 0)
    )


def filter_strict_fee_match(df: pl.LazyFrame) -> pl.LazyFrame:
    """Keep matches with at least one fee match or missing fee data.

    Filters to keep matches where at least one fee column matches exactly,
    or where either side is missing fee data entirely.

    Args:
        df: Candidate matches with NumberFeeMatches and NumberNonmissingFees columns.

    Returns:
        Filtered matches with acceptable fee matching.
    """
    return df.filter(
        (pl.col("NumberFeeMatches") >= 1)
        | (pl.col("NumberNonmissingFees_s") == 0)
        | (pl.col("NumberNonmissingFees_p") == 0)
    )


def add_purchase_indicator(df: pl.LazyFrame) -> pl.LazyFrame:
    """Add i_Purchase indicator (1 if loan_purpose == 1, else 0).

    Creates a binary indicator column that is 1 for purchase loans
    (loan_purpose == 1) and 0 for all other loan purposes.

    Args:
        df: Data with loan_purpose column.

    Returns:
        Data with i_Purchase indicator column added.
    """
    return df.with_columns((pl.col("loan_purpose") == 1).cast(pl.Int8).alias("i_Purchase"))


def create_matched_file(
    data_folder: Path,
    match_folder: Path,
    min_year: int = 2018,
    max_year: int = 2024,
    match_round: int = 8,
) -> pl.DataFrame:
    """Build a wide seller/purchaser file for a given round using Polars.

    Args:
        data_folder: Directory containing raw HMDA parquet files (partitioned by year).
        match_folder: Directory with crosswalk outputs written by `save_crosswalk`.
        min_year: Minimum activity year to load. Defaults to 2018.
        max_year: Maximum activity year to load. Defaults to 2024.
        match_round: Match round identifier. Defaults to 8.

    Returns:
        Combined seller/purchaser records for the requested round.
    """
    match_folder = Path(match_folder)
    data_folder = Path(data_folder)

    # Load crosswalk and HMDA data
    crosswalk = pl.scan_parquet(match_folder / "**/*.parquet")
    crosswalk = crosswalk.filter(
        pl.col("activity_year_s").is_between(min_year, max_year),
        pl.col("MatchRound") <= match_round,
    )
    hmda = _load_hmda_with_best_file_type(data_folder).filter(
        pl.col("activity_year").is_between(min_year, max_year),
    )

    # Merge seller data
    logger.info("Merging seller data...")
    df = crosswalk.join(
        hmda.rename(lambda col: f"{col}_s" if col != "HMDAIndex" else "HMDAIndex_s"),
        left_on="HMDAIndex_s",
        right_on="HMDAIndex_s",
        how="left",
    )

    # Merge purchaser data
    logger.info("Merging purchaser data...")
    df = df.join(
        hmda.rename(lambda col: f"{col}_p" if col != "HMDAIndex" else "HMDAIndex_p"),
        left_on="HMDAIndex_p",
        right_on="HMDAIndex_p",
        how="left",
    )

    match_cols = ["HMDAIndex_s", "HMDAIndex_p", "MatchRound"]
    ordered_cols = match_cols + [c for c in df.columns if c not in match_cols]
    df = df.select(ordered_cols)

    return df


# =============================================================================
# Demographic matching functions
# =============================================================================


def apply_demographic_filters(
    lf: pl.LazyFrame,
    strict_age: bool = False,
    strict_ethnicity: bool = False,
    strict_race: bool = False,
    strict_sex: bool = False,
    use_observed: bool = False,
) -> pl.LazyFrame:
    """Apply all demographic matching filters to candidate pairs using Polars.

    Args:
        lf: Candidate seller/purchaser combinations.
        strict_age: Whether to require exact matches on the age field.
        strict_ethnicity: Whether to require exact matches on primary ethnicity field.
        strict_race: Whether to require exact matches on primary race field.
        strict_sex: Whether to require exact matches on the sex field.
        use_observed: Whether to use observed (rather than imputed) demographic
            values when comparing fields.

    Returns:
        Filtered candidate pairs where demographic fields are compatible.
    """
    lf = match_sex(lf, strict=strict_sex, use_observed=use_observed)
    lf = match_age(lf, strict=strict_age, use_observed=use_observed)
    lf = match_race(lf, strict=strict_race, use_observed=use_observed)
    lf = match_ethnicity(lf, strict=strict_ethnicity, use_observed=use_observed)
    return lf


def match_sex(lf: pl.LazyFrame, strict: bool = False, use_observed: bool = False) -> pl.LazyFrame:
    """Drop candidate pairs that conflict on applicant or co-applicant sex."""
    # Create filters for sex matching
    sex_filters = []

    col_names = lf.collect_schema().names()

    for sex_column in ["applicant_sex", "co_applicant_sex"]:
        col_s = f"{sex_column}_s"
        col_p = f"{sex_column}_p"

        # Skip if columns don't exist
        if col_s not in col_names or col_p not in col_names:
            continue

        # Create matching conditions - allow matches or missing values
        sex_filter = (
            # Same values
            (pl.col(col_s) == pl.col(col_p))
            |
            # Missing values
            pl.col(col_s).is_null()
            | pl.col(col_p).is_null()
            |
            # Allow "other" category (4) to match anything
            (pl.col(col_s) == 4)
            | (pl.col(col_p) == 4)
            |
            # Allow "not applicable" (7) to match anything
            (pl.col(col_s) == 7)
            | (pl.col(col_p) == 7)
        )
        sex_filters.append(sex_filter)

    # Apply all sex filters
    for filter_expr in sex_filters:
        lf = lf.filter(filter_expr)

    return lf


def match_age(lf: pl.LazyFrame, strict: bool = False, use_observed: bool = False) -> pl.LazyFrame:
    """Drop candidate pairs that conflict on applicant or co-applicant age."""
    age_filters = []

    col_names = lf.collect_schema().names()

    for age_column in ["applicant_age", "co_applicant_age"]:
        col_s = f"{age_column}_s"
        col_p = f"{age_column}_p"

        if col_s not in col_names or col_p not in col_names:
            continue

        # Age matching: exact match or missing values (8888, 9999)
        age_filter = (
            (pl.col(col_s) == pl.col(col_p))
            | pl.col(col_s).is_in([8888, 9999])
            | pl.col(col_p).is_in([8888, 9999])
            | pl.col(col_s).is_null()
            | pl.col(col_p).is_null()
        )
        age_filters.append(age_filter)

    # Special handling for co-applicant age (9999 means no co-applicant)
    if "co_applicant_age_s" in col_names and "co_applicant_age_p" in col_names:
        co_age_filter = ~(
            (pl.col("co_applicant_age_s") == 9999)
            & ~pl.col("co_applicant_age_p").is_in([8888, 9999])
        ) & ~(
            (pl.col("co_applicant_age_p") == 9999)
            & ~pl.col("co_applicant_age_s").is_in([8888, 9999])
        )
        age_filters.append(co_age_filter)

    # Apply all age filters
    for filter_expr in age_filters:
        lf = lf.filter(filter_expr)

    return lf


def match_race(lf: pl.LazyFrame, strict: bool = False, use_observed: bool = False) -> pl.LazyFrame:
    """Apply race-based consistency checks to candidate matches."""
    # Normalize race subcategories first
    race_mappings = {
        21: 2,
        22: 2,
        23: 2,
        24: 2,
        25: 2,
        26: 2,
        27: 2,  # Asian subcategories -> Asian
        41: 4,
        42: 4,
        43: 4,
        44: 4,  # Pacific Islander subcategories -> Pacific Islander
    }

    col_names = lf.collect_schema().names()

    # Apply race normalization
    for race_column in ["applicant_race", "co_applicant_race"]:
        for race_number in range(1, 6):  # race_1 through race_5
            col_s = f"{race_column}_{race_number}_s"
            col_p = f"{race_column}_{race_number}_p"

            if col_s in col_names:
                for old_val, new_val in race_mappings.items():
                    lf = lf.with_columns(
                        pl.when(pl.col(col_s) == old_val)
                        .then(new_val)
                        .otherwise(pl.col(col_s))
                        .alias(col_s)
                    )

            if col_p in col_names:
                for old_val, new_val in race_mappings.items():
                    lf = lf.with_columns(
                        pl.when(pl.col(col_p) == old_val)
                        .then(new_val)
                        .otherwise(pl.col(col_p))
                        .alias(col_p)
                    )

    # Apply race matching logic
    race_filters = []

    # Applicant race matching
    for race_value in range(1, 7):  # 1-6 race categories
        if "applicant_race_1_s" in col_names and "applicant_race_1_p" in col_names:
            race_filter = (
                # Primary race doesn't equal this value
                (pl.col("applicant_race_1_s") != race_value)
                |
                # OR primary race matches (including missing codes 7,8)
                pl.col("applicant_race_1_p").is_in([race_value, 7, 8])
                |
                # OR appears in any secondary race field
                pl.col("applicant_race_2_p").is_in([race_value])
                | pl.col("applicant_race_3_p").is_in([race_value])
                | pl.col("applicant_race_4_p").is_in([race_value])
                | pl.col("applicant_race_5_p").is_in([race_value])
            )
            race_filters.append(race_filter)

            # Same logic for purchaser primary race
            race_filter_p = (
                (pl.col("applicant_race_1_p") != race_value)
                | pl.col("applicant_race_1_s").is_in([race_value, 7, 8])
                | pl.col("applicant_race_2_s").is_in([race_value])
                | pl.col("applicant_race_3_s").is_in([race_value])
                | pl.col("applicant_race_4_s").is_in([race_value])
                | pl.col("applicant_race_5_s").is_in([race_value])
            )
            race_filters.append(race_filter_p)

    # Co-applicant race matching
    if "co_applicant_race_1_s" in col_names and "co_applicant_race_1_p" in col_names:
        # Handle "no co-applicant" case (8)
        co_race_filter = ~(
            (pl.col("co_applicant_race_1_s") == 8) & ~pl.col("co_applicant_race_1_p").is_in([7, 8])
        ) & ~(
            (pl.col("co_applicant_race_1_p") == 8) & ~pl.col("co_applicant_race_1_s").is_in([7, 8])
        )
        race_filters.append(co_race_filter)

        # Same matching logic as applicant for co-applicant
        for race_value in range(1, 7):
            co_race_filter = (
                (pl.col("co_applicant_race_1_s") != race_value)
                | pl.col("co_applicant_race_1_p").is_in([race_value, 7, 8])
                | pl.col("co_applicant_race_2_p").is_in([race_value])
                | pl.col("co_applicant_race_3_p").is_in([race_value])
                | pl.col("co_applicant_race_4_p").is_in([race_value])
                | pl.col("co_applicant_race_5_p").is_in([race_value])
            )
            race_filters.append(co_race_filter)

    # Strict race matching
    if strict:
        if "applicant_race_1_s" in col_names and "applicant_race_1_p" in col_names:
            strict_filter = (
                (pl.col("applicant_race_1_s") == pl.col("applicant_race_1_p"))
                | pl.col("applicant_race_1_s").is_in([7, 8])
                | pl.col("applicant_race_1_p").is_in([7, 8])
            )
            race_filters.append(strict_filter)

        if "co_applicant_race_1_s" in col_names and "co_applicant_race_1_p" in col_names:
            co_strict_filter = (
                (pl.col("co_applicant_race_1_s") == pl.col("co_applicant_race_1_p"))
                | pl.col("co_applicant_race_1_s").is_in([7, 8])
                | pl.col("co_applicant_race_1_p").is_in([7, 8])
            )
            race_filters.append(co_strict_filter)

    # Apply all race filters
    for filter_expr in race_filters:
        lf = lf.filter(filter_expr)

    return lf


def match_ethnicity(
    lf: pl.LazyFrame, strict: bool = False, use_observed: bool = False
) -> pl.LazyFrame:
    """Apply ethnicity-based consistency checks to candidates."""
    # Normalize ethnicity subcategories
    ethnicity_mappings = {
        11: 1,
        12: 1,
        13: 1,
        14: 1,  # Hispanic subcategories -> Hispanic
    }

    col_names = lf.collect_schema().names()

    # Apply ethnicity normalization
    for ethnicity_column in ["applicant_ethnicity", "co_applicant_ethnicity"]:
        for ethnicity_number in range(1, 6):  # ethnicity_1 through ethnicity_5
            col_s = f"{ethnicity_column}_{ethnicity_number}_s"
            col_p = f"{ethnicity_column}_{ethnicity_number}_p"

            if col_s in col_names:
                for old_val, new_val in ethnicity_mappings.items():
                    lf = lf.with_columns(
                        pl.when(pl.col(col_s) == old_val)
                        .then(new_val)
                        .otherwise(pl.col(col_s))
                        .alias(col_s)
                    )

            if col_p in col_names:
                for old_val, new_val in ethnicity_mappings.items():
                    lf = lf.with_columns(
                        pl.when(pl.col(col_p) == old_val)
                        .then(new_val)
                        .otherwise(pl.col(col_p))
                        .alias(col_p)
                    )

    # Apply ethnicity matching logic
    ethnicity_filters = []

    # Applicant ethnicity matching
    for ethnicity_value in range(1, 3):  # 1-2 ethnicity categories
        if "applicant_ethnicity_1_s" in col_names and "applicant_ethnicity_1_p" in col_names:
            ethnicity_filter = (
                (pl.col("applicant_ethnicity_1_s") != ethnicity_value)
                | pl.col("applicant_ethnicity_1_p").is_in([ethnicity_value, 3, 4])
                | pl.col("applicant_ethnicity_2_p").is_in([ethnicity_value])
                | pl.col("applicant_ethnicity_3_p").is_in([ethnicity_value])
                | pl.col("applicant_ethnicity_4_p").is_in([ethnicity_value])
                | pl.col("applicant_ethnicity_5_p").is_in([ethnicity_value])
            )
            ethnicity_filters.append(ethnicity_filter)

            # Same for purchaser
            ethnicity_filter_p = (
                (pl.col("applicant_ethnicity_1_p") != ethnicity_value)
                | pl.col("applicant_ethnicity_1_s").is_in([ethnicity_value, 3, 4])
                | pl.col("applicant_ethnicity_2_s").is_in([ethnicity_value])
                | pl.col("applicant_ethnicity_3_s").is_in([ethnicity_value])
                | pl.col("applicant_ethnicity_4_s").is_in([ethnicity_value])
                | pl.col("applicant_ethnicity_5_s").is_in([ethnicity_value])
            )
            ethnicity_filters.append(ethnicity_filter_p)

    # Co-applicant ethnicity matching
    if "co_applicant_ethnicity_1_s" in col_names and "co_applicant_ethnicity_1_p" in col_names:
        # Handle "no co-applicant" case (5)
        co_ethnicity_filter = ~(
            (pl.col("co_applicant_ethnicity_1_s") == 5)
            & ~pl.col("co_applicant_ethnicity_1_p").is_in([3, 4, 5])
        ) & ~(
            (pl.col("co_applicant_ethnicity_1_p") == 5)
            & ~pl.col("co_applicant_ethnicity_1_s").is_in([3, 4, 5])
        )
        ethnicity_filters.append(co_ethnicity_filter)

    # Strict ethnicity matching
    if strict:
        if "applicant_ethnicity_1_s" in col_names and "applicant_ethnicity_1_p" in col_names:
            strict_filter = (
                (pl.col("applicant_ethnicity_1_s") == pl.col("applicant_ethnicity_1_p"))
                | pl.col("applicant_ethnicity_1_s").is_in([3, 4])
                | pl.col("applicant_ethnicity_1_p").is_in([3, 4])
            )
            ethnicity_filters.append(strict_filter)

        if "co_applicant_ethnicity_1_s" in col_names and "co_applicant_ethnicity_1_p" in col_names:
            co_strict_filter = (
                (pl.col("co_applicant_ethnicity_1_s") == pl.col("co_applicant_ethnicity_1_p"))
                | pl.col("co_applicant_ethnicity_1_s").is_in([3, 4, 5])
                | pl.col("co_applicant_ethnicity_1_p").is_in([3, 4, 5])
            )
            ethnicity_filters.append(co_strict_filter)

    # Apply all ethnicity filters
    for filter_expr in ethnicity_filters:
        lf = lf.filter(filter_expr)

    return lf


def match_demographics(df: pl.LazyFrame) -> pl.LazyFrame:
    """Apply all demographic matching filters (age, sex, race, ethnicity).

    Convenience function that applies all four demographic matching functions
    in sequence.

    Args:
        df: Candidate matches with demographic columns.

    Returns:
        Filtered matches where demographics are compatible.
    """
    df = match_age(df)
    df = match_sex(df)
    df = match_race(df)
    df = match_ethnicity(df)
    return df


__all__ = [
    # Data loading and preparation
    "get_match_columns",
    "load_unmatched_data",
    "split_originations_purchases",
    "merge_sellers_and_purchasers",
    # Numeric processing
    "convert_numerics",
    "add_fee_exemption_indicators",
    "replace_negative_numerics",
    "fix_intro_rate_period_edge_case",
    "drop_zeros",
    "replace_missing_values",
    # Matching functions
    "numeric_matches",
    "weak_numeric_matches",
    "perform_fee_matches",
    "numeric_matches_post_unique",
    "keep_matches_post_unique",
    "keep_uniques",
    # Quality and filter functions
    "apply_quality_match_filters",
    "apply_drop_observation_cleanup",
    "filter_refi_types",
    "filter_generous_fee_match",
    "filter_strict_fee_match",
    "add_purchase_indicator",
    # Output functions
    "save_crosswalk",
    "create_matched_file",
    # Affiliate and LEI functions
    "get_affiliates",
    "get_purchaser_type_counts",
    "get_lei_relationships",
    # Demographic matching
    "apply_demographic_filters",
    "match_sex",
    "match_age",
    "match_race",
    "match_ethnicity",
    "match_demographics",
]
