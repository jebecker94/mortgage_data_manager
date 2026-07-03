#!/usr/bin/env python3
"""GNMA Schema - Analysis.

Functions for analyzing schema coverage and formats.

Functions:
    - analyze_temporal_coverage: Analyze date coverage and gaps
    - analyze_schema_formats: Analyze schema format types (delimited vs fixed-width)

"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)


def analyze_temporal_coverage(
    combined_dir: str | Path,
    output_dir: str | Path | None = None,
    save_analysis: bool = True,
    verbose: bool = False
) -> pd.DataFrame:
    """Analyze temporal coverage across all combined schema files.

    Identifies monthly gaps in schema coverage for each prefix by analyzing
    Min_Date and Max_Date columns. Generates expected monthly ranges and
    identifies gaps where schema files are missing.

    Args:
        combined_dir: Directory containing combined schema files
        output_dir: Optional output directory for analysis results
        save_analysis: Whether to save analysis results
        verbose: Whether to print detailed progress

    Returns:
        DataFrame with temporal coverage analysis including:
        - Prefix name
        - Files count
        - Coverage start/end dates
        - Total/covered/missing months
        - Coverage percentage
        - Gap periods
    """
    combined_dir = Path(combined_dir)

    if not combined_dir.exists():
        logger.warning(f"Combined schemas directory not found: {combined_dir}")
        return pd.DataFrame()

    # Find all combined schema files
    schema_files = list(combined_dir.glob('*_combined_schema.csv'))

    if not schema_files:
        logger.warning(f"No combined schema files found in {combined_dir}")
        return pd.DataFrame()

    coverage_results = []

    logger.info(f"Analyzing temporal coverage for {len(schema_files)} prefixes...")

    for schema_file in schema_files:
        prefix = schema_file.stem.replace('_combined_schema', '')

        try:
            # Load the schema file
            df = pd.read_csv(schema_file)

            if 'Min_Date' not in df.columns or 'Max_Date' not in df.columns:
                logger.warning(f"{prefix}: Missing date columns")
                continue

            # Convert date columns to datetime
            df['Min_Date'] = pd.to_datetime(df['Min_Date'])
            df['Max_Date'] = pd.to_datetime(df['Max_Date'])

            # Replace far future dates (2099) with current date for analysis
            current_date = pd.Timestamp.now().normalize()
            df['Max_Date_Analysis'] = df['Max_Date'].where(
                df['Max_Date'] < pd.Timestamp('2099-01-01'),
                current_date
            )

            # Get unique date ranges from files
            date_ranges = df[['Min_Date', 'Max_Date_Analysis']].drop_duplicates().sort_values('Min_Date')

            if len(date_ranges) == 0:
                continue

            # Calculate overall coverage
            overall_min = date_ranges['Min_Date'].min()
            overall_max = date_ranges['Max_Date_Analysis'].max()

            # Generate expected monthly range
            expected_months = pd.date_range(
                start=overall_min.replace(day=1),
                end=overall_max.replace(day=1),
                freq='MS'  # Month start
            )

            # Generate covered months from all date ranges
            covered_months = set()
            for _, row in date_ranges.iterrows():
                range_months = pd.date_range(
                    start=row['Min_Date'].replace(day=1),
                    end=row['Max_Date_Analysis'].replace(day=1),
                    freq='MS'
                )
                covered_months.update(range_months)

            # Find gaps
            missing_months = []
            for month in expected_months:
                if month not in covered_months:
                    missing_months.append(month)

            # Calculate gap details
            gap_periods = []
            if missing_months:
                missing_months.sort()
                gap_start = missing_months[0]
                gap_end = missing_months[0]

                for i in range(1, len(missing_months)):
                    # Check if this month is consecutive to the previous
                    if missing_months[i] == missing_months[i-1] + pd.DateOffset(months=1):
                        gap_end = missing_months[i]
                    else:
                        # End current gap and start new one
                        gap_periods.append((gap_start, gap_end))
                        gap_start = missing_months[i]
                        gap_end = missing_months[i]

                # Add the last gap
                gap_periods.append((gap_start, gap_end))

            # Create summary
            total_months_expected = len(expected_months)
            total_months_covered = len(covered_months)
            total_months_missing = len(missing_months)
            coverage_pct = (total_months_covered / total_months_expected) * 100 if total_months_expected > 0 else 0

            # Format gap periods for display
            gap_summary = []
            for gap_start, gap_end in gap_periods:
                if gap_start == gap_end:
                    gap_summary.append(gap_start.strftime('%Y-%m'))
                else:
                    gap_summary.append(f"{gap_start.strftime('%Y-%m')} to {gap_end.strftime('%Y-%m')}")

            coverage_results.append({
                'Prefix': prefix,
                'Files_Count': len(date_ranges),
                'Coverage_Start': overall_min.strftime('%Y-%m-%d'),
                'Coverage_End': overall_max.strftime('%Y-%m-%d'),
                'Total_Months_Expected': total_months_expected,
                'Total_Months_Covered': total_months_covered,
                'Total_Months_Missing': total_months_missing,
                'Coverage_Percentage': round(coverage_pct, 1),
                'Gap_Periods': '; '.join(gap_summary) if gap_summary else 'None',
                'Has_Gaps': len(missing_months) > 0
            })

            if verbose:
                logger.info(f"{prefix}:")
                logger.info(f"   Coverage: {overall_min.strftime('%Y-%m')} to {overall_max.strftime('%Y-%m')}")
                logger.info(f"   Files: {len(date_ranges)}")
                logger.info(f"   Coverage: {total_months_covered}/{total_months_expected} months ({coverage_pct:.1f}%)")
                if gap_summary:
                    logger.info(f"   Gaps: {'; '.join(gap_summary)}")
                else:
                    logger.info("   Complete coverage")

        except Exception as e:
            logger.warning(f"Error analyzing {prefix}: {str(e)}")
            continue

    # Convert to DataFrame and sort by coverage percentage
    results_df = pd.DataFrame(coverage_results)
    if not results_df.empty:
        results_df = results_df.sort_values(['Has_Gaps', 'Coverage_Percentage'], ascending=[True, False])

        # Save analysis
        if save_analysis:
            if output_dir is None:
                output_dir = combined_dir.parent / 'analysis'
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / 'temporal_coverage_analysis.csv'
            results_df.to_csv(output_path, index=False)
            logger.info(f"Saved temporal coverage analysis to: {output_path}")

    return results_df


def analyze_schema_formats(
    combined_dir: str | Path,
    output_dir: str | Path | None = None,
    save_analysis: bool = True,
    verbose: bool = False
) -> pd.DataFrame:
    """Analyze schema files to identify delimited vs fixed-width formats.

    Uses column structure patterns to predict whether data files are:
    - DELIMITED (pipe, comma, or other delimiter-separated)
    - FIXED-WIDTH (positional, no delimiters)
    - UNCERTAIN (cannot determine confidently)

    Args:
        combined_dir: Directory containing combined schema files
        output_dir: Optional output directory for analysis results
        save_analysis: Whether to save analysis results
        verbose: Whether to print detailed progress

    Returns:
        DataFrame with format analysis including:
        - Predicted format type
        - Confidence score
        - Format indicators
        - Column structure details
    """
    combined_dir = Path(combined_dir)

    if not combined_dir.exists():
        logger.warning(f"Combined schemas directory not found: {combined_dir}")
        return pd.DataFrame()

    # Find all combined schema files
    schema_files = list(combined_dir.glob('*_combined_schema.csv'))

    if not schema_files:
        logger.warning(f"No combined schema files found in {combined_dir}")
        return pd.DataFrame()

    format_results = []

    logger.info(f"Analyzing schema formats for {len(schema_files)} prefixes...")

    for schema_file in schema_files:
        prefix = schema_file.stem.replace('_combined_schema', '')

        try:
            # Read just the header to analyze column structure
            df_header = pd.read_csv(schema_file, nrows=0)  # Just get column names
            columns = list(df_header.columns)

            # Analyze column patterns
            has_max_length = 'Max Length' in columns
            has_length = 'Length' in columns
            has_begin_end = 'Begin' in columns and 'End' in columns
            has_format = 'Format' in columns
            has_description = 'Description' in columns
            has_definition = 'Definition' in columns
            has_remarks = 'Remarks' in columns
            has_data_element = 'Data Element' in columns
            has_data_item = 'Data Item' in columns

            # Read a few rows to check for additional patterns
            df_sample = pd.read_csv(schema_file, nrows=10)

            # Count total columns
            total_columns = len(columns)

            # Predict file format based on patterns
            format_indicators = []
            confidence_score = 0

            if has_max_length:
                format_indicators.append("Max Length column")
                confidence_score += 3

            if has_begin_end:
                format_indicators.append("Begin/End columns")
                confidence_score -= 3  # This suggests fixed-width

            if has_format:
                format_indicators.append("Format column")
                confidence_score += 2

            if has_description and has_definition:
                format_indicators.append("Description/Definition columns")
                confidence_score += 1

            if has_remarks and not (has_description or has_definition):
                format_indicators.append("Remarks column only")
                confidence_score -= 1  # Suggests older/simpler fixed-width format

            if has_data_element:
                format_indicators.append("Data Element naming")
                confidence_score += 1
            elif has_data_item:
                format_indicators.append("Data Item naming")
                confidence_score -= 1

            # Make prediction
            if confidence_score >= 3:
                predicted_format = "DELIMITED"
            elif confidence_score <= -2:
                predicted_format = "FIXED-WIDTH"
            else:
                predicted_format = "UNCERTAIN"

            # Check for any explicit delimiter indicators in the data
            delimiter_hints = []
            if not df_sample.empty:
                for col in ['Format', 'Description', 'Definition', 'Remarks']:
                    if col in df_sample.columns:
                        sample_text = ' '.join(df_sample[col].dropna().astype(str).head(10))
                        if 'delimit' in sample_text.lower():
                            delimiter_hints.append(f"'{col}' mentions delimiter")
                        if 'fixed' in sample_text.lower() and 'width' in sample_text.lower():
                            delimiter_hints.append(f"'{col}' mentions fixed width")
                        if 'pipe' in sample_text.lower() or '|' in sample_text:
                            delimiter_hints.append(f"'{col}' mentions pipe delimiter")
                        if 'comma' in sample_text.lower() and 'separat' in sample_text.lower():
                            delimiter_hints.append(f"'{col}' mentions comma separator")

            format_results.append({
                'Prefix': prefix,
                'Total_Columns': total_columns,
                'Has_Max_Length': has_max_length,
                'Has_Length': has_length,
                'Has_Begin_End': has_begin_end,
                'Has_Format': has_format,
                'Has_Description': has_description,
                'Has_Definition': has_definition,
                'Has_Remarks': has_remarks,
                'Has_Data_Element': has_data_element,
                'Has_Data_Item': has_data_item,
                'Confidence_Score': confidence_score,
                'Predicted_Format': predicted_format,
                'Format_Indicators': '; '.join(format_indicators),
                'Delimiter_Hints': '; '.join(delimiter_hints) if delimiter_hints else 'None',
                'Column_Structure': ', '.join(columns[:6])  # First 6 columns for reference
            })

            if verbose:
                logger.debug(
                    f"{prefix}: predicted={predicted_format} (score={confidence_score}); "
                    f"indicators={'; '.join(format_indicators)}; "
                    f"hints={'; '.join(delimiter_hints) if delimiter_hints else 'None'}"
                )

        except Exception as e:
            logger.warning(f"Error analyzing {prefix}: {str(e)}")
            continue

    # Convert to DataFrame and sort by prediction confidence
    results_df = pd.DataFrame(format_results)
    if not results_df.empty:
        results_df = results_df.sort_values(['Predicted_Format', 'Confidence_Score'],
                                            ascending=[True, False])

        # Save analysis
        if save_analysis:
            if output_dir is None:
                output_dir = combined_dir.parent / 'analysis'
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / 'schema_format_analysis.csv'
            results_df.to_csv(output_path, index=False)
            logger.info(f"Saved schema format analysis to: {output_path}")

    return results_df

