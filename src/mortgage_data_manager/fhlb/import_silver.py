"""FHLB silver layer data imports.

This module handles transforming bronze layer data to silver layer:
- AMA (Acquired Member Assets) loan-level data with data quality transformations
"""

from __future__ import annotations

import datetime
from pathlib import Path

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.core.medallion import should_process_file

logger = get_logger(__name__)


def import_ama_silver(
    years: list[int],
    bronze_dir: Path,
    output_dir: Path,
    overwrite: bool = False
) -> dict[str, int]:
    """Transform FHLB AMA data from bronze to silver layer with data quality transformations.

    Applies standardized data cleaning and normalization:
    - Normalizes percentage values across schema versions
    - Fixes income amount units (monthly vs yearly)
    - Standardizes indicator variable encoding (1/2 -> 0/1)
    - Cleans census tract identifiers
    - Converts property type codes
    - Caps extreme debt-to-income ratios
    - Replaces sentinel missing value codes with nulls
    - Parses date fields
    - Removes redundant columns

    Args:
        years: Years to process (e.g., [2009, 2010, 2011])
        bronze_dir: Directory containing bronze parquet files
        output_dir: Output directory for silver parquet files
        overwrite: Whether to overwrite existing files

    Returns:
        Summary with 'loaded' and 'skipped' counts
    """
    import polars as pl

    loaded = 0
    skipped = 0

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    for year in years:
        input_file = bronze_dir / f"fhlb_ama_{year}.parquet"

        if not input_file.exists():
            logger.warning(f"Bronze file not found for year {year}")
            skipped += 1
            continue

        output_file = output_dir / f"fhlb_ama_{year}.parquet"

        if not should_process_file(output_file, overwrite):
            logger.info(f"Skipping {year} - file exists (use --overwrite to rebuild)")
            skipped += 1
            continue

        logger.info(f"Processing FHLB AMA data for {year} to silver layer")

        try:
            df = pl.read_parquet(input_file)

            # 1. Normalize percentage values (pre-2019 stored as decimals, post-2019 as percentages)
            pct_cols = [
                'LTVRatioPercent',
                'NoteRatePercent',
                'HousingExpenseRatioPercent',
                'TotalDebtExpenseRatioPercent',
                'PMICoveragePercent',
            ]
            for col in pct_cols:
                if col in df.columns:
                    df = df.with_columns(
                        pl.when(pl.col('Year') < 2019)
                        .then(pl.col(col) * 100)
                        .otherwise(pl.col(col))
                        .alias(col)
                    )

            # 2. Fix income amounts (pre-2019 monthly, 2019+ yearly/12)
            if 'TotalMonthlyIncomeAmount' in df.columns:
                df = df.with_columns(
                    pl.when(pl.col('Year') >= 2019)
                    .then(pl.col('TotalMonthlyIncomeAmount') * 12)
                    .otherwise(pl.col('TotalMonthlyIncomeAmount'))
                    .alias('TotalMonthlyIncomeAmount')
                )

            # 3. Fix indicator variables (pre-2019 used 1/2, post-2019 uses 0/1)
            if 'BorrowerFirstTimeHomebuyer' in df.columns:
                df = df.with_columns(
                    pl.when((pl.col('Year') < 2019) & (pl.col('BorrowerFirstTimeHomebuyer') == 2))
                    .then(0)
                    .otherwise(pl.col('BorrowerFirstTimeHomebuyer'))
                    .alias('BorrowerFirstTimeHomebuyer')
                )

            if 'EmploymentBorrowerSelfEmployed' in df.columns:
                df = df.with_columns(
                    pl.when((pl.col('Year') < 2019) & (pl.col('EmploymentBorrowerSelfEmployed') == 2))
                    .then(0)
                    .otherwise(pl.col('EmploymentBorrowerSelfEmployed'))
                    .alias('EmploymentBorrowerSelfEmployed')
                )

            # 4. Fix census tract (multiply by 100 and round to avoid floating point errors)
            if 'CensusTractIdentifier' in df.columns:
                df = df.with_columns(
                    (pl.col('CensusTractIdentifier') * 100).round(0).alias('CensusTractIdentifier')
                )

            # 5. Property type to numeric (remove "PT" prefix)
            if 'PropertyType' in df.columns:
                df = df.with_columns(
                    pl.col('PropertyType').str.replace('PT', '').cast(pl.Int64, strict=False).alias('PropertyType')
                )

            # 6. Cap extreme DTI values (likely data errors)
            if 'TotalDebtExpenseRatioPercent' in df.columns:
                df = df.with_columns(
                    pl.when(pl.col('TotalDebtExpenseRatioPercent') >= 1000)
                    .then(None)
                    .otherwise(pl.col('TotalDebtExpenseRatioPercent'))
                    .alias('TotalDebtExpenseRatioPercent')
                )

            if 'HousingExpenseRatioPercent' in df.columns:
                df = df.with_columns(
                    pl.when(pl.col('HousingExpenseRatioPercent') >= 1000)
                    .then(None)
                    .otherwise(pl.col('HousingExpenseRatioPercent'))
                    .alias('HousingExpenseRatioPercent')
                )

            # 7. Replace sentinel missing value codes with nulls
            missing_value_replacements = {
                'Bed1': [98],
                'Bed2': [98],
                'Bed3': [98],
                'Bed4': [98],
                'IndexSourceType': [99],
                'Borrower1AgeAtApplicationYears': [99, 999],
                'Borrower2AgeAtApplicationYears': [98, 99, 998, 999],
                'HousingExpenseRatioPercent': [999.0, 999.99],
                'TotalDebtExpenseRatioPercent': [999.0, 999.99],
                'CoreBasedStatisticalAreaCode': [99999],
                'MarginRatePercent': [9999, 99999],
                'FeatureID': [9999999999],
                'Rent1': [9999999999],
                'Rent2': [9999999999],
                'Rent3': [9999999999],
                'Rent4': [9999999999],
                'RentUt1': [9999999999],
                'RentUt2': [9999999999],
                'RentUt3': [9999999999],
                'RentUt4': [9999999999],
            }

            for col, missing_vals in missing_value_replacements.items():
                if col in df.columns:
                    # Cast missing values to same type as column for comparison
                    col_dtype = df[col].dtype
                    if col_dtype in [pl.Float32, pl.Float64]:
                        missing_vals_typed = [float(v) for v in missing_vals]
                    else:
                        missing_vals_typed = [int(v) if isinstance(v, float) else v for v in missing_vals]

                    df = df.with_columns(
                        pl.when(pl.col(col).is_in(missing_vals_typed))
                        .then(None)
                        .otherwise(pl.col(col))
                        .alias(col)
                    )

            # 8. Convert prepayment penalty expiration dates
            if 'PrepaymentPenaltyExpirationDate' in df.columns:
                col_dtype = df['PrepaymentPenaltyExpirationDate'].dtype

                # Check if already a date type (from bronze layer auto-parsing)
                if col_dtype == pl.Date:
                    # Already parsed, just replace sentinel date with null
                    sentinel_date = datetime.date(9999, 12, 31)
                    df = df.with_columns(
                        pl.when(pl.col('PrepaymentPenaltyExpirationDate') == sentinel_date)
                        .then(None)
                        .otherwise(pl.col('PrepaymentPenaltyExpirationDate'))
                        .alias('PrepaymentPenaltyExpirationDate')
                    )
                elif col_dtype == pl.String or col_dtype == pl.Utf8:
                    # String type, need to parse based on year
                    if year < 2019:
                        # Pre-2019: MM/DD/YYYY format with '12/31/9999' as missing
                        df = df.with_columns(
                            pl.when(pl.col('PrepaymentPenaltyExpirationDate') == '12/31/9999')
                            .then(None)
                            .otherwise(pl.col('PrepaymentPenaltyExpirationDate').str.to_date('%m/%d/%Y', strict=False))
                            .alias('PrepaymentPenaltyExpirationDate')
                        )
                    else:
                        # 2019+: YYYY-MM-DD format with '9999-12-31' as missing
                        df = df.with_columns(
                            pl.when(pl.col('PrepaymentPenaltyExpirationDate') == '9999-12-31')
                            .then(None)
                            .otherwise(pl.col('PrepaymentPenaltyExpirationDate').str.to_date('%Y-%m-%d', strict=False))
                            .alias('PrepaymentPenaltyExpirationDate')
                        )

            # 9. Drop redundant Year column (same as LoanAcquisitionDate)
            if 'Year' in df.columns:
                df = df.drop('Year')

            # Write to parquet
            df.write_parquet(output_file)

            logger.info(f"Saved {len(df):,} records to {output_file.name}")
            loaded += 1

        except Exception as e:
            logger.error(f"Failed to process {year}: {e}")
            skipped += 1

    return {"loaded": loaded, "skipped": skipped}
