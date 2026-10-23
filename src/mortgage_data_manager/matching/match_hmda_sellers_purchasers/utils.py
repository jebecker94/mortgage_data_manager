"""Matching utilities and support functions for HMDA seller-purchaser matching.

This module combines all matching support functions and demographic matching
utilities into a single file for simplified imports.
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import write_hive_partitioned
from mortgage_data_manager.core.types import null_sentinels
from mortgage_data_manager.matching.resolve import drop_ambiguous
from mortgage_data_manager.sources.hmda.load import scan_hmda_years

logger = get_logger(__name__)


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

    Example:
        >>> data_folder = Path("data/hmda/post2018")
        >>> crosswalk_folder = Path("data/matches/post2018")
        >>> df = load_unmatched_data(data_folder, crosswalk_folder)
    """
    df = scan_hmda_years("post2018", min_year, max_year, root=Path(data_folder))

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
            "match_round": pl.UInt8,
        },
        # Crosswalk files store activity_year_s as Int16; widen instead of raising
        cast_options=pl.ScanCastOptions(integer_cast="upcast"),
    )
    if match_round is not None:
        cw = cw.filter(pl.col("match_round") < match_round)

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
    replacements = [
        null_sentinels(pl.col(col), missing_codes).alias(col)
        for col in numeric_columns
        if col in col_names
    ]
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
            start_count = lf.select(pl.len()).collect().item() if log_counts else 0

            # Apply tolerance filter (allow null values through)
            lf = lf.filter(
                ((pl.col(col_s) - pl.col(col_p)).abs() <= tolerance)
                | pl.col(col_s).is_null()
                | pl.col(col_p).is_null()
            )

            if log_counts:
                end_count = lf.select(pl.len()).collect().item()
                logger.debug(
                    "    %s tolerance filter: %s → %s", column, f"{start_count:,}", f"{end_count:,}"
                )

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
            start_count = lf.select(pl.len()).collect().item() if log_counts else 0

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
                end_count = lf.select(pl.len()).collect().item()
                logger.debug(
                    "    %s weak filter: %s → %s", column, f"{start_count:,}", f"{end_count:,}"
                )

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
    start_count = lf.select(pl.len()).collect().item() if log_counts else 0

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
        end_count = lf.select(pl.len()).collect().item()
        logger.debug(
            "    Post-unique numeric filter: %s → %s", f"{start_count:,}", f"{end_count:,}"
        )

    return lf


def keep_uniques(lf: pl.LazyFrame, one_to_one: bool = True) -> pl.LazyFrame:
    """Keep only unique matches (one-to-one) using Polars.

    Args:
        lf: Candidate matches with seller/purchaser pairs.
        one_to_one: Whether to keep only one-to-one matches. When False, a sale may
            carry two purchases provided one of them is a known secondary sale.

    Returns:
        Only unique one-to-one matches.
    """
    if one_to_one:
        return drop_ambiguous(lf, "HMDAIndex_s", "HMDAIndex_p")

    # One-to-many. Kept in long form deliberately: `i_LoanHasSecondarySale` is a window
    # over the purchaser-filtered frame, not the full candidate set, so the step order
    # here is load-bearing and does not decompose into drop_ambiguous.
    lf = lf.with_columns(
        [
            pl.len().over("HMDAIndex_s").alias("count_seller"),
            pl.len().over("HMDAIndex_p").alias("count_purchaser"),
        ]
    )

    # Keep Purchased Loans w/ Unique Match
    lf = lf.filter(pl.col("count_purchaser") == 1)

    # Keep Loans Where Sale Matches Multiple Purchases if One Known to Be Secondary Sale
    lf = lf.with_columns((pl.col("purchaser_type_p") > 4).cast(pl.Int8).alias("i_SecondarySale"))
    lf = lf.with_columns(
        pl.col("i_SecondarySale").max().over("HMDAIndex_s").alias("i_LoanHasSecondarySale")
    )
    lf = lf.filter(
        (pl.col("count_seller") == 1)
        | ((pl.col("count_seller") == 2) & (pl.col("i_LoanHasSecondarySale") == 1))
    )
    lf = lf.drop(["i_SecondarySale", "i_LoanHasSecondarySale"])

    # Clean up count columns
    return lf.drop(["count_seller", "count_purchaser"])


def save_crosswalk(df: pl.LazyFrame, save_folder: str | Path, match_round: int) -> None:
    """Save match results to parquet file using Polars.

    Args:
        df: Final match results to save.
        save_folder: Directory to save the file.
        match_round: Round number for filename.
    """
    # Sorted so a rerun writes the same row order: the joins upstream do not preserve
    # order. Seller and purchaser ids order every row; rows tied on both are identical.
    df = df.select(["HMDAIndex_s", "HMDAIndex_p", "activity_year_s"]).sort(
        ["HMDAIndex_s", "HMDAIndex_p"]
    )
    df = df.with_columns(pl.lit(match_round, dtype=pl.UInt8).alias("match_round"))
    write_hive_partitioned(
        df,
        Path(save_folder),
        partition_cols=["match_round", "activity_year_s"],
        include_key=True,
    )


def get_purchaser_type_counts(
    match_folder: Path,
    data_folder: Path,
    match_round: int,
    strict: bool = False,
) -> pl.LazyFrame:
    """Find seller->purchaser LEI pairs whose matched loans carry one dominant purchaser_type.

    Keeps a (seller LEI, purchaser LEI, activity year, purchaser_type) cell when at
    least 50 matched loans fall in it and they are at least 99% of that pair-year's
    matches. Loans with ``purchaser_type == 0`` are dropped first.

    Args:
        match_folder: Directory with matched loan parquet files.
        data_folder: HMDA post-2018 silver root.
        match_round: Keep matches from rounds up to and including this one.
        strict: If True, require loans to have exactly one matched purchaser.

    Returns:
        One row per (``lei_s``, ``lei_p``, ``activity_year_s``, ``purchaser_type_s``)
        with ``CountLEIMatches`` and ``CountLEIPurchaserTypeMatches``.
    """
    match_folder = Path(match_folder)
    data_folder = Path(data_folder)

    cw = (
        pl.scan_parquet(match_folder / "**/*.parquet")
        .filter(pl.col("match_round") <= match_round)
        .select(["HMDAIndex_s", "HMDAIndex_p"])
        .unique()
    )

    # Load HMDA data with proper file_type selection
    df = scan_hmda_years("post2018", root=data_folder)
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
        ).with_columns(pl.len().over("HMDAIndex_s").alias("CountSoldLoan"))
        df = df.filter(pl.col("CountSoldLoan") == 1)

    df = df.filter(pl.col("purchaser_type_s") != 0)

    df = df.with_columns(
        pl.len().over(["lei_s", "lei_p", "activity_year_s"]).alias("CountLEIMatches"),
        pl.len()
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
        # Every selected column is constant within the cell, so this collapses
        # identical rows -- no row is chosen.
        .unique()
        .sort(["lei_s", "lei_p", "activity_year_s", "purchaser_type_s"])
    )

    return result


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


# =============================================================================
# Demographic matching functions
# =============================================================================


def match_sex(lf: pl.LazyFrame, strict: bool = False, use_observed: bool = False) -> pl.LazyFrame:
    """Drop candidate pairs that conflict on applicant or co-applicant sex.

    Args:
        lf: The candidate pairs.
        strict: Require agreement rather than only dropping conflicts.
        use_observed: Use the observed-status fields rather than the reported ones.

    Returns:
        The candidates with sex-conflicting pairs dropped.
    """
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
    """Drop candidate pairs that conflict on applicant or co-applicant age.

    Args:
        lf: The candidate pairs.
        strict: Require agreement rather than only dropping conflicts.
        use_observed: Use the observed-status fields rather than the reported ones.

    Returns:
        The candidates with age-conflicting pairs dropped.
    """
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
    """Apply race-based consistency checks to candidate matches.

    Args:
        lf: The candidate pairs.
        strict: Require agreement rather than only dropping conflicts.
        use_observed: Use the observed-status fields rather than the reported ones.

    Returns:
        The candidates with race-conflicting pairs dropped.
    """
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
    """Apply ethnicity-based consistency checks to candidates.

    Args:
        lf: The candidate pairs.
        strict: Require agreement rather than only dropping conflicts.
        use_observed: Use the observed-status fields rather than the reported ones.

    Returns:
        The candidates with ethnicity-conflicting pairs dropped.
    """
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
    "load_unmatched_data",
    "split_originations_purchases",
    "merge_sellers_and_purchasers",
    # Numeric processing
    "drop_zeros",
    "replace_missing_values",
    # Matching functions
    "numeric_matches",
    "weak_numeric_matches",
    "perform_fee_matches",
    "numeric_matches_post_unique",
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
    # Affiliate and LEI functions
    "get_purchaser_type_counts",
    # Demographic matching
    "match_sex",
    "match_age",
    "match_race",
    "match_ethnicity",
    "match_demographics",
]
