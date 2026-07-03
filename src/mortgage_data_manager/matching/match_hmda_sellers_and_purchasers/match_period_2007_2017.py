"""Pre-2018 HMDA matching workflows (2007-2017).

This module implements seller-purchaser matching for pre-2018 HMDA data,
which has a different column structure and simpler demographic fields
compared to post-2018 data.

Key differences from post-2018:
- Simpler race/ethnicity structure (fewer columns)
- Uses LoanAmountBin for matching instead of exact loan_amount
- Only 2 matching rounds (vs 8 for post-2018)
- Different demographic matching logic
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pyarrow.parquet as pq

from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)


def load_data(
    data_folder: str | Path,
    min_year: int = 2007,
    max_year: int = 2017,
    matched_seller_indices: pl.LazyFrame | None = None,
    matched_purchaser_indices: pl.LazyFrame | None = None,
) -> pl.LazyFrame:
    """Load pre-2018 HMDA originations and purchases into a single frame.

    Args:
        data_folder: Directory containing the annual HMDA parquet deliveries.
        min_year: Earliest activity year to include (inclusive). The default is 2007.
        max_year: Latest activity year to include (inclusive). The default is 2017.
        matched_seller_indices: HMDAIndex values for sellers already matched. If provided, these will
            be excluded from the loaded data.
        matched_purchaser_indices: HMDAIndex values for purchasers already matched. If provided, these
            will be excluded from the loaded data.

    Returns:
        Combined seller and purchaser records for the requested year range.
    """
    data_folder = Path(data_folder)

    # Load data for each year
    dfs = []
    for year in range(min_year, max_year + 1):
        # Find the parquet file for this year
        files = list(data_folder.glob(f"*{year}*.parquet"))
        if not files:
            logger.warning(f"No parquet file found for year {year}")
            continue

        file = files[0]

        # Load with filters: action_taken in [1, 6]
        # Note: Using PyArrow filters for efficiency, then convert to Polars
        table = pq.read_table(file, filters=[('action_taken', 'in', [1, 6])])
        df_year = pl.from_arrow(table).lazy()

        # Additional filter: keep purchases OR originations not sold to GSEs
        df_year = df_year.filter(
            (pl.col("action_taken") == 6) | ~pl.col("purchaser_type").is_in([1, 2, 3, 4])
        )

        dfs.append(df_year)

    # Combine all years
    if not dfs:
        raise ValueError(f"No data found in {data_folder} for years {min_year}-{max_year}")

    df = pl.concat(dfs, how="diagonal_relaxed")

    # Exclude already matched records if provided
    if matched_seller_indices is not None:
        df = df.join(
            matched_seller_indices.select("HMDAIndex_s"),
            left_on="HMDAIndex",
            right_on="HMDAIndex_s",
            how="anti",
        )

    if matched_purchaser_indices is not None:
        df = df.join(
            matched_purchaser_indices.select("HMDAIndex_p"),
            left_on="HMDAIndex",
            right_on="HMDAIndex_p",
            how="anti",
        )

    return df


def keep_uniques(lf: pl.LazyFrame, one_to_one: bool = True) -> pl.LazyFrame:
    """Keep unique matches for pre-2018 data.

    Args:
        lf: Data before unique matches are enforced.
        one_to_one: Whether to only keep unique seller matches. The default is True.

    Returns:
        Data after unique matches are enforced.
    """
    # Identify loan ID columns
    loan_id_columns_s = ['activity_year_s', 'respondent_id_s', 'agency_code_s',
                         'sequence_number_s', 'HMDAIndex_s']
    loan_id_columns_p = ['activity_year_p', 'respondent_id_p', 'agency_code_p',
                         'sequence_number_p', 'HMDAIndex_p']

    # Get available columns
    schema_names = lf.collect_schema().names()
    loan_id_columns_s = [x for x in loan_id_columns_s if x in schema_names]
    loan_id_columns_p = [x for x in loan_id_columns_p if x in schema_names]

    # Count matches per seller and purchaser
    lf = lf.with_columns([
        pl.col("activity_year_s").count().over(loan_id_columns_s).alias("count_index_s"),
        pl.col("activity_year_p").count().over(loan_id_columns_p).alias("count_index_p"),
    ])

    # Log match cardinality
    logger.debug("Match cardinality counts:")
    cardinality = lf.select(["count_index_s", "count_index_p"]).collect()
    logger.debug("\n%s", cardinality.group_by(["count_index_s", "count_index_p"]).agg(pl.len()))

    # Keep purchased loans with unique match
    lf = lf.filter(pl.col("count_index_p") == 1)

    # Optionally enforce one-to-one matches
    if one_to_one:
        lf = lf.filter(pl.col("count_index_s") == 1)
    else:
        # Allow one-to-many for secondary sales
        lf = lf.with_columns([
            (pl.col("purchaser_type_p") > 4).cast(pl.Int8).alias("temp"),
        ])
        lf = lf.with_columns([
            pl.col("temp").max().over(loan_id_columns_s).alias("i_LoanHasSecondarySale"),
        ])
        lf = lf.filter(
            (pl.col("count_index_s") == 1) |
            ((pl.col("count_index_s") == 2) & (pl.col("i_LoanHasSecondarySale") == 1))
        )
        lf = lf.drop(["i_LoanHasSecondarySale", "temp"])

    # Drop count columns
    lf = lf.drop(["count_index_s", "count_index_p"])

    return lf


def match_sex_pre2018(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Drop seller/purchaser pairs with incompatible sex designations (pre-2018).

    Pre-2018 sex codes:
    - 1: Male
    - 2: Female
    - 3: Information not provided
    - 4: Not applicable
    - 5: No co-applicant

    Args:
        lf: Candidate matches with applicant_sex_* and co_applicant_sex_* columns.

    Returns:
        Subset where sex indicators are mutually consistent.
    """
    schema_names = lf.collect_schema().names()

    # Applicant sex filters
    if "applicant_sex_s" in schema_names and "applicant_sex_p" in schema_names:
        lf = lf.filter(
            (pl.col("applicant_sex_s") != 1) | (pl.col("applicant_sex_p") != 2)
        )
        lf = lf.filter(
            (pl.col("applicant_sex_s") != 2) | (pl.col("applicant_sex_p") != 1)
        )

    # Co-applicant sex filters
    if "co_applicant_sex_s" in schema_names and "co_applicant_sex_p" in schema_names:
        lf = lf.filter(
            (pl.col("co_applicant_sex_s") != 1) | (pl.col("co_applicant_sex_p") != 2)
        )
        lf = lf.filter(
            (pl.col("co_applicant_sex_s") != 2) | (pl.col("co_applicant_sex_p") != 1)
        )
        # No co-applicant (5) should not match with male/female
        lf = lf.filter(
            (pl.col("co_applicant_sex_s") != 5) | ~pl.col("co_applicant_sex_p").is_in([1, 2])
        )
        lf = lf.filter(
            ~pl.col("co_applicant_sex_s").is_in([1, 2]) | (pl.col("co_applicant_sex_p") != 5)
        )

    return lf


def match_race_pre2018(lf: pl.LazyFrame, strict: bool = False) -> pl.LazyFrame:
    """Perform race matches for pre-2018 data.

    Pre-2018 race codes:
    - 1-6: Specific race categories
    - 7: Not applicable
    - 8: No co-applicant

    Args:
        lf: Data with unmatched races.
        strict: Whether to enforce strict race matching. The default is False.

    Returns:
        Data with matched races.
    """
    schema_names = lf.collect_schema().names()

    # Applicant race matches (allow multi-race matching)
    if "applicant_race_1_s" in schema_names and "applicant_race_1_p" in schema_names:
        for race_number in range(1, 7):
            # If seller has race_number, purchaser must have it in any race field or be 7/8
            for race_col_p in ['applicant_race_1_p', 'applicant_race_2_p',
                               'applicant_race_3_p', 'applicant_race_4_p', 'applicant_race_5_p']:
                if race_col_p not in schema_names:
                    continue

            # Build filter: if seller is race_number, purchaser must match somewhere
            race_cols_p = [c for c in ['applicant_race_1_p', 'applicant_race_2_p',
                                        'applicant_race_3_p', 'applicant_race_4_p',
                                        'applicant_race_5_p'] if c in schema_names]

            if race_cols_p:
                purchaser_match = pl.col("applicant_race_1_p").is_in([race_number, 7, 8])
                for col in race_cols_p[1:]:
                    purchaser_match = purchaser_match | (pl.col(col) == race_number)

                lf = lf.filter(
                    (pl.col("applicant_race_1_s") != race_number) | purchaser_match
                )

            # Reverse direction
            race_cols_s = [c for c in ['applicant_race_1_s', 'applicant_race_2_s',
                                        'applicant_race_3_s', 'applicant_race_4_s',
                                        'applicant_race_5_s'] if c in schema_names]

            if race_cols_s:
                seller_match = pl.col("applicant_race_1_s").is_in([race_number, 7, 8])
                for col in race_cols_s[1:]:
                    seller_match = seller_match | (pl.col(col) == race_number)

                lf = lf.filter(
                    (pl.col("applicant_race_1_p") != race_number) | seller_match
                )

    # Co-applicant race matches
    if "co_applicant_race_1_s" in schema_names and "co_applicant_race_1_p" in schema_names:
        # No co-applicant matching
        lf = lf.filter(
            (pl.col("co_applicant_race_1_s") != 8) | pl.col("co_applicant_race_1_p").is_in([7, 8])
        )
        lf = lf.filter(
            (pl.col("co_applicant_race_1_p") != 8) | pl.col("co_applicant_race_1_s").is_in([7, 8])
        )

        # Multi-race matching for co-applicant
        for race_number in range(1, 7):
            race_cols_p = [c for c in ['co_applicant_race_1_p', 'co_applicant_race_2_p',
                                        'co_applicant_race_3_p', 'co_applicant_race_4_p',
                                        'co_applicant_race_5_p'] if c in schema_names]

            if race_cols_p:
                purchaser_match = pl.col("co_applicant_race_1_p").is_in([race_number, 7, 8])
                for col in race_cols_p[1:]:
                    purchaser_match = purchaser_match | (pl.col(col) == race_number)

                lf = lf.filter(
                    (pl.col("co_applicant_race_1_s") != race_number) | purchaser_match
                )

            race_cols_s = [c for c in ['co_applicant_race_1_s', 'co_applicant_race_2_s',
                                        'co_applicant_race_3_s', 'co_applicant_race_4_s',
                                        'co_applicant_race_5_s'] if c in schema_names]

            if race_cols_s:
                seller_match = pl.col("co_applicant_race_1_s").is_in([race_number, 7, 8])
                for col in race_cols_s[1:]:
                    seller_match = seller_match | (pl.col(col) == race_number)

                lf = lf.filter(
                    (pl.col("co_applicant_race_1_p") != race_number) | seller_match
                )

    # Strict race matches
    if strict:
        if "applicant_race_1_s" in schema_names and "applicant_race_1_p" in schema_names:
            lf = lf.filter(
                (pl.col("applicant_race_1_s") == pl.col("applicant_race_1_p")) |
                pl.col("applicant_race_1_s").is_in([7, 8]) |
                pl.col("applicant_race_1_p").is_in([7, 8])
            )
        if "co_applicant_race_1_s" in schema_names and "co_applicant_race_1_p" in schema_names:
            lf = lf.filter(
                (pl.col("co_applicant_race_1_s") == pl.col("co_applicant_race_1_p")) |
                pl.col("co_applicant_race_1_s").is_in([7, 8]) |
                pl.col("co_applicant_race_1_p").is_in([7, 8])
            )

    return lf


def match_ethnicity_pre2018(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Perform ethnicity matches for pre-2018 data.

    Pre-2018 ethnicity codes:
    - 1-3: Specific ethnicity categories
    - 4: Not applicable
    - 5: No co-applicant

    Args:
        lf: Data with unmatched ethnicities.

    Returns:
        Data with matched ethnicities.
    """
    schema_names = lf.collect_schema().names()

    # Applicant ethnicity matching
    if "applicant_ethnicity_s" in schema_names and "applicant_ethnicity_p" in schema_names:
        for ethnicity_number in range(1, 4):
            lf = lf.filter(
                (pl.col("applicant_ethnicity_s") != ethnicity_number) |
                pl.col("applicant_ethnicity_p").is_in([ethnicity_number, 4])
            )
            lf = lf.filter(
                (pl.col("applicant_ethnicity_p") != ethnicity_number) |
                pl.col("applicant_ethnicity_s").is_in([ethnicity_number, 4])
            )

    # Co-applicant ethnicity matching
    if "co_applicant_ethnicity_s" in schema_names and "co_applicant_ethnicity_p" in schema_names:
        for ethnicity_number in range(1, 4):
            lf = lf.filter(
                (pl.col("co_applicant_ethnicity_s") != ethnicity_number) |
                pl.col("co_applicant_ethnicity_p").is_in([ethnicity_number, 4])
            )
            lf = lf.filter(
                (pl.col("co_applicant_ethnicity_p") != ethnicity_number) |
                pl.col("co_applicant_ethnicity_s").is_in([ethnicity_number, 4])
            )

        # Co-applicant present
        lf = lf.filter(
            (pl.col("co_applicant_ethnicity_s") != 5) |
            pl.col("co_applicant_ethnicity_p").is_in([4, 5])
        )
        lf = lf.filter(
            (pl.col("co_applicant_ethnicity_p") != 5) |
            pl.col("co_applicant_ethnicity_s").is_in([4, 5])
        )

    return lf


def match_hmda_sellers_purchasers_round1(
    data_folder: str | Path,
    save_folder: str | Path,
    min_year: int = 2007,
    max_year: int = 2017,
) -> None:
    """Generate first-round pre-2018 seller/purchaser matches within a year.

    The routine matches originations and subsequent purchases using a strict
    set of demographic, geography, and loan amount comparisons and writes a
    parquet crosswalk to save_folder.

    Args:
        data_folder: Location of the annual HMDA parquet files to be matched.
        save_folder: Directory where the round-one crosswalk parquet should be written.
        min_year: Inclusive first year to process. Defaults to the start of
            pre-2018 HMDA availability (2007).
        max_year: Inclusive last year to process. Defaults to the end of
            pre-2018 HMDA availability (2017).

    Returns:
        None. Results are written to save_folder; no value is returned.
    """
    data_folder = Path(data_folder)
    save_folder = Path(save_folder)

    all_matches = []

    for year in range(min_year, max_year + 1):
        logger.info('Matching HMDA sellers and purchasers for year: %s', year)

        # Load data for this year
        df = load_data(data_folder, min_year=year, max_year=year)

        # Drop unnecessary columns
        drop_columns = [
            'denial_reason_1', 'denial_reason_2', 'denial_reason_3',
            'edit_status', 'application_date_indicator', 'tract_one_to_four_family_units',
            'derived_loan_product_type', 'derived_dwelling_category',
            'derived_ethnicity', 'derived_race', 'derived_sex', 'lien_status',
            'rate_spread', 'hoepa_status'
        ]
        df = df.drop([c for c in drop_columns if c in df.collect_schema().names()])

        # Create LoanAmountBin for matching
        df = df.with_columns([
            (10 * pl.col("loan_amount").floor_div(10) + 5).alias("LoanAmountBin"),
        ])

        # Define match columns and drop nulls
        match_columns = ['loan_type', 'census_tract', 'occupancy_type', 'loan_purpose', 'LoanAmountBin']
        df = df.drop_nulls(subset=match_columns)
        df = df.filter(pl.col("census_tract") != "")

        # Split into sellers and purchasers
        df_purchaser = df.filter(pl.col("action_taken") == 6)
        df_seller = df.filter(pl.col("action_taken") == 1)

        # Expand LoanAmountBins for sold loans (to catch rounding differences)
        df_temp = df_seller.filter(pl.col("loan_amount") % 10 < 5)
        df_temp = df_temp.with_columns([
            (pl.col("LoanAmountBin") - 10).alias("LoanAmountBin"),
        ])
        df_seller = pl.concat([df_seller, df_temp], how="diagonal_relaxed")

        # Merge sellers and purchasers
        df = df_seller.join(df_purchaser, on=match_columns, suffix="_p")
        df = df.rename({c: f"{c}_s" for c in df.collect_schema().names() if not c.endswith("_p") and c not in match_columns})

        # Income matches (with tolerance)
        df = df.with_columns([
            pl.when(pl.col("income_s").is_in([9999])).then(None).otherwise(pl.col("income_s")).alias("income_s"),
            pl.when(pl.col("income_p").is_in([9999])).then(None).otherwise(pl.col("income_p")).alias("income_p"),
        ])
        df = df.with_columns([
            (pl.col("income_s") - pl.col("income_p")).alias("IncomeDifference"),
        ])
        df = df.filter(
            (pl.col("IncomeDifference").abs() <= 5) | pl.col("IncomeDifference").is_null()
        )

        # Make sure lenders are different
        df = df.filter(
            (pl.col("respondent_id_s") != pl.col("respondent_id_p")) |
            (pl.col("agency_code_s") != pl.col("agency_code_p"))
        )

        # Keep unique loans
        df = keep_uniques(df, one_to_one=True)

        # Keep similar loan amounts and incomes (tighter tolerances)
        df = df.with_columns([
            (pl.col("loan_amount_s") - pl.col("loan_amount_p")).alias("SizeDifference"),
        ])
        df = df.filter(pl.col("SizeDifference").is_in([0, 1]))
        df = df.filter(
            (pl.col("IncomeDifference").abs() <= 0) | pl.col("IncomeDifference").is_null()
        )

        # Drop demographic mismatches
        df = match_sex_pre2018(df)
        df = match_race_pre2018(df)
        df = match_ethnicity_pre2018(df)

        # Drop likely balance sheet loans and secondary sales
        df = df.filter(pl.col("purchaser_type_s") > 0)
        df = df.filter(pl.col("purchaser_type_p") <= 4)

        # Property type match
        if "property_type_s" in df.collect_schema().names() and "property_type_p" in df.collect_schema().names():
            df = df.filter(pl.col("property_type_s") == pl.col("property_type_p"))

        # Collect and log
        df_collected = df.collect()
        logger.info('Adding %d matches from year %s', len(df_collected), year)
        all_matches.append(df_collected)

    # Combine all years
    df_final = pl.concat(all_matches, how="diagonal_relaxed")

    # Keep subset of columns as crosswalk
    loan_id_columns_s = ['activity_year_s', 'respondent_id_s', 'agency_code_s',
                         'sequence_number_s', 'HMDAIndex_s']
    loan_id_columns_p = ['activity_year_p', 'respondent_id_p', 'agency_code_p',
                         'sequence_number_p', 'HMDAIndex_p']

    available_cols_s = [c for c in loan_id_columns_s if c in df_final.columns]
    available_cols_p = [c for c in loan_id_columns_p if c in df_final.columns]

    df_final = df_final.select(available_cols_s + available_cols_p)

    # Add match round
    df_final = df_final.with_columns([
        pl.lit(1).alias("MatchRound"),
    ])

    # Save crosswalk
    save_folder.mkdir(parents=True, exist_ok=True)
    output_file = save_folder / "pre2018_hmda_seller_purchaser_matches_round1.parquet"
    df_final.write_parquet(output_file)
    logger.info("Saved Round 1 crosswalk to %s", output_file)


def match_hmda_sellers_purchasers_round2(
    data_folder: str | Path,
    save_folder: str | Path,
    min_year: int = 2007,
    max_year: int = 2017,
) -> None:
    """Perform cross-year refinement of the pre-2018 seller/purchaser pairs.

    Round two begins with the prior crosswalk, excludes already-matched
    records, and searches for additional matches that meet the relaxed
    temporal and numeric tolerances outlined in the paper's methodology.

    Args:
        data_folder: Location of the annual HMDA parquet files to be matched.
        save_folder: Directory where the round-two crosswalk parquet should be written.
        min_year: Inclusive first activity year to process. Defaults to the
            start of pre-2018 data availability (2007).
        max_year: Inclusive last activity year to process. Defaults to the
            end of pre-2018 data availability (2017).

    Returns:
        None. Results are written to save_folder; no value is returned.
    """
    data_folder = Path(data_folder)
    save_folder = Path(save_folder)

    # Load previous round's crosswalk
    cw = pl.read_parquet(save_folder / "pre2018_hmda_seller_purchaser_matches_round1.parquet")

    all_matches = []

    for year in range(min_year, max_year + 1):
        logger.info('Matching HMDA sellers and purchasers for year: %s', year)

        # Load data for this year
        df = load_data(
            data_folder,
            min_year=year,
            max_year=year,
            matched_seller_indices=cw.lazy(),
            matched_purchaser_indices=cw.lazy(),
        )

        # Drop unnecessary columns
        drop_columns = [
            'denial_reason_1', 'denial_reason_2', 'denial_reason_3',
            'edit_status', 'application_date_indicator', 'tract_one_to_four_family_units',
            'derived_loan_product_type', 'derived_dwelling_category',
            'derived_ethnicity', 'derived_race', 'derived_sex', 'lien_status',
            'rate_spread', 'hoepa_status'
        ]
        df = df.drop([c for c in drop_columns if c in df.collect_schema().names()])

        # Collect for this year
        all_matches.append(df.collect())

    # Combine all years
    df = pl.concat(all_matches, how="diagonal_relaxed").lazy()

    # Drop observations with missing match columns
    match_columns = ['loan_type', 'loan_amount', 'census_tract', 'occupancy_type',
                     'property_type', 'loan_purpose']
    df = df.drop_nulls(subset=match_columns)
    df = df.filter(pl.col("census_tract") != "")

    # Split into sellers and purchasers
    df_purchaser = df.filter(pl.col("action_taken") == 6)
    df_seller = df.filter(pl.col("action_taken") == 1)

    # Merge
    df = df_seller.join(df_purchaser, on=match_columns, suffix="_p")
    df = df.rename({c: f"{c}_s" for c in df.collect_schema().names() if not c.endswith("_p") and c not in match_columns})

    # Year matches (allow cross-year within 1 year)
    df = df.filter(pl.col("activity_year_s") <= pl.col("activity_year_p"))
    df = df.filter((pl.col("activity_year_s") + 1) >= pl.col("activity_year_p"))

    # Income matches
    df = df.with_columns([
        pl.when(pl.col("income_s").is_in([9999])).then(None).otherwise(pl.col("income_s")).alias("income_s"),
        pl.when(pl.col("income_p").is_in([9999])).then(None).otherwise(pl.col("income_p")).alias("income_p"),
    ])
    df = df.with_columns([
        (pl.col("income_s") - pl.col("income_p")).alias("IncomeDifference"),
    ])
    df = df.filter(
        (pl.col("IncomeDifference").abs() <= 5) | pl.col("IncomeDifference").is_null()
    )

    # Make sure lenders are different
    df = df.filter(
        (pl.col("respondent_id_s") != pl.col("respondent_id_p")) |
        (pl.col("agency_code_s") != pl.col("agency_code_p"))
    )

    # Keep unique loans
    df = keep_uniques(df, one_to_one=True)

    # Drop demographic mismatches
    df = match_sex_pre2018(df)
    df = match_race_pre2018(df)
    df = match_ethnicity_pre2018(df)

    # Tight income match
    df = df.filter(
        (pl.col("IncomeDifference").abs() <= 1) | pl.col("IncomeDifference").is_null()
    )

    # Year and purchaser type matches
    df = df.with_columns([
        (pl.col("activity_year_s") == pl.col("activity_year_p")).cast(pl.Int8).alias("i_YearMatch"),
    ])
    df = df.filter(
        (pl.col("i_YearMatch") == 1) | (pl.col("purchaser_type_s") == 0)
    )
    df = df.filter(
        (pl.col("i_YearMatch") != 1) | (pl.col("purchaser_type_s") != 0)
    )

    # Drop secondary sales
    df = df.filter(pl.col("purchaser_type_p") <= 4)

    # Keep ID columns
    loan_id_columns_s = ['activity_year_s', 'respondent_id_s', 'agency_code_s',
                         'sequence_number_s', 'HMDAIndex_s']
    loan_id_columns_p = ['activity_year_p', 'respondent_id_p', 'agency_code_p',
                         'sequence_number_p', 'HMDAIndex_p']

    schema_names = df.collect_schema().names()
    available_cols_s = [c for c in loan_id_columns_s if c in schema_names]
    available_cols_p = [c for c in loan_id_columns_p if c in schema_names]

    df = df.select(available_cols_s + available_cols_p)

    # Collect
    df_collected = df.collect()

    # Add match round and combine with previous round
    df_collected = df_collected.with_columns([
        pl.lit(2).alias("MatchRound"),
    ])
    df_final = pl.concat([cw, df_collected], how="diagonal_relaxed")

    # Save crosswalk
    output_file = save_folder / "pre2018_hmda_seller_purchaser_matches_round2.parquet"
    df_final.write_parquet(output_file)
    logger.info("Saved Round 2 crosswalk to %s", output_file)


__all__ = [
    "match_hmda_sellers_purchasers_round1",
    "match_hmda_sellers_purchasers_round2",
    "load_data",
    "keep_uniques",
    "match_sex_pre2018",
    "match_race_pre2018",
    "match_ethnicity_pre2018",
]
