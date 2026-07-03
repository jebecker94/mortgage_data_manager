"""Data validation module for HUD crosswalk files.

Provides functions to check data integrity:
- File existence and coverage gaps
- Geographic code format validation
- Ratio consistency checks
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.hud.config import (
    CROSSWALK_TYPES,
    GEO_CODE_LENGTHS,
    RATIO_COLUMNS,
    VALID_CROSSWALK_NAMES,
    HUDConfig,
    is_available,
)

logger = get_logger(__name__)


@dataclass
class ValidationResult:
    """Results from validating a crosswalk type."""

    crosswalk_type: str
    is_valid: bool = True
    missing_files: list[tuple[int, int]] = field(default_factory=list)
    unexpected_files: list[str] = field(default_factory=list)
    geo_code_issues: list[str] = field(default_factory=list)
    ratio_issues: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        lines = [f"Validation for {self.crosswalk_type}:"]
        if self.is_valid:
            lines.append("  Status: PASS")
        else:
            lines.append("  Status: FAIL")

        if self.missing_files:
            lines.append(f"  Missing files ({len(self.missing_files)}):")
            for year, quarter in self.missing_files[:10]:
                lines.append(f"    - {year} Q{quarter}")
            if len(self.missing_files) > 10:
                lines.append(f"    ... and {len(self.missing_files) - 10} more")

        if self.unexpected_files:
            lines.append(f"  Unexpected files ({len(self.unexpected_files)}):")
            for f in self.unexpected_files[:5]:
                lines.append(f"    - {f}")

        if self.geo_code_issues:
            lines.append(f"  Geographic code issues ({len(self.geo_code_issues)}):")
            for issue in self.geo_code_issues[:5]:
                lines.append(f"    - {issue}")
            if len(self.geo_code_issues) > 5:
                lines.append(f"    ... and {len(self.geo_code_issues) - 5} more")

        if self.ratio_issues:
            lines.append(f"  Ratio issues ({len(self.ratio_issues)}):")
            for issue in self.ratio_issues[:5]:
                lines.append(f"    - {issue}")
            if len(self.ratio_issues) > 5:
                lines.append(f"    ... and {len(self.ratio_issues) - 5} more")

        return "\n".join(lines)


def validate_file_coverage(
    crosswalk_type: str,
    data_dir: Path | None = None,
    max_year: int | None = None,
    max_quarter: int | None = None,
) -> ValidationResult:
    """Check that all expected files exist for a crosswalk type.

    Args:
        crosswalk_type: Name of the crosswalk type to validate.
        data_dir: Base directory for raw data.
        max_year: Last year to check (defaults to current year).
        max_quarter: Last quarter to check (defaults to previous quarter).

    Returns:
        ValidationResult with missing/unexpected files.
    """
    if data_dir is None:
        data_dir = HUDConfig.HUD_BRONZE_DIR

    now = datetime.now()
    if max_year is None:
        max_year = now.year
    if max_quarter is None:
        current_quarter = (now.month - 1) // 3 + 1
        if current_quarter == 1:
            max_year = now.year - 1
            max_quarter = 4
        else:
            max_quarter = current_quarter - 1

    result = ValidationResult(crosswalk_type=crosswalk_type)
    crosswalk_dir = data_dir / crosswalk_type

    if not crosswalk_dir.exists():
        result.is_valid = False
        crosswalk = CROSSWALK_TYPES[
            next(k for k, v in CROSSWALK_TYPES.items() if v.name == crosswalk_type)
        ]
        for year in range(crosswalk.start_year, max_year + 1):
            for quarter in range(1, 5):
                if is_available(crosswalk, year, quarter):
                    if year < max_year or quarter <= max_quarter:
                        result.missing_files.append((year, quarter))
        return result

    existing_files = {f.name for f in crosswalk_dir.glob("*.parquet")}
    expected_pattern = re.compile(rf"^{crosswalk_type}_(\d{{4}})_Q([1-4])\.parquet$")

    existing_quarters: set[tuple[int, int]] = set()
    for filename in existing_files:
        match = expected_pattern.match(filename)
        if match:
            existing_quarters.add((int(match.group(1)), int(match.group(2))))
        else:
            result.unexpected_files.append(filename)
            result.is_valid = False

    crosswalk = CROSSWALK_TYPES[
        next(k for k, v in CROSSWALK_TYPES.items() if v.name == crosswalk_type)
    ]
    for year in range(crosswalk.start_year, max_year + 1):
        for quarter in range(1, 5):
            if not is_available(crosswalk, year, quarter):
                continue
            if year == max_year and quarter > max_quarter:
                continue
            if (year, quarter) not in existing_quarters:
                result.missing_files.append((year, quarter))
                result.is_valid = False

    return result


def validate_geo_codes(
    crosswalk_type: str,
    year: int,
    quarter: int,
    data_dir: Path | None = None,
    sample_size: int = 10000,
) -> list[str]:
    """Validate geographic code formats in a crosswalk file.

    Args:
        crosswalk_type: Name of the crosswalk type.
        year: Year of the file to validate.
        quarter: Quarter of the file to validate.
        data_dir: Base directory for raw data.
        sample_size: Number of rows to sample (0 = all rows).

    Returns:
        List of validation issues found.
    """
    if data_dir is None:
        data_dir = HUDConfig.HUD_BRONZE_DIR

    filepath = data_dir / crosswalk_type / f"{crosswalk_type}_{year}_Q{quarter}.parquet"
    if not filepath.exists():
        return [f"File not found: {filepath}"]

    issues: list[str] = []
    df = pl.read_parquet(filepath)
    if sample_size > 0 and len(df) > sample_size:
        df = df.sample(sample_size, seed=42)

    for col, expected_length in GEO_CODE_LENGTHS.items():
        if col not in df.columns:
            continue

        if df[col].dtype != pl.Utf8:
            issues.append(f"{col}: Not string type (is {df[col].dtype})")
            continue

        lengths = df[col].str.len_chars()
        wrong_length = (lengths != expected_length).sum()
        if wrong_length > 0:
            pct = wrong_length / len(df) * 100
            issues.append(
                f"{col}: {wrong_length} rows ({pct:.1f}%) have wrong length "
                f"(expected {expected_length})"
            )

        if col in ("zip", "tract", "county", "cbsa", "cbsadiv", "countysub"):
            non_numeric = df.filter(~pl.col(col).str.contains(r"^\d+$"))
            if len(non_numeric) > 0:
                pct = len(non_numeric) / len(df) * 100
                issues.append(
                    f"{col}: {len(non_numeric)} rows ({pct:.1f}%) contain non-numeric characters"
                )

    return issues


def validate_ratios(
    crosswalk_type: str,
    year: int,
    quarter: int,
    data_dir: Path | None = None,
    tolerance: float = 0.01,
) -> list[str]:
    """Validate that allocation ratios sum to approximately 1.0 per source geography.

    Args:
        crosswalk_type: Name of the crosswalk type.
        year: Year of the file to validate.
        quarter: Quarter of the file to validate.
        data_dir: Base directory for raw data.
        tolerance: Acceptable deviation from 1.0 for ratio sums.

    Returns:
        List of validation issues found.
    """
    if data_dir is None:
        data_dir = HUDConfig.HUD_BRONZE_DIR

    filepath = data_dir / crosswalk_type / f"{crosswalk_type}_{year}_Q{quarter}.parquet"
    if not filepath.exists():
        return [f"File not found: {filepath}"]

    issues: list[str] = []
    df = pl.read_parquet(filepath)

    # Determine the source geography column
    if crosswalk_type.startswith("ZIP_"):
        source_col = "zip"
    elif crosswalk_type.startswith("TRACT_"):
        source_col = "tract"
    elif crosswalk_type.startswith("COUNTY_") and "SUB" not in crosswalk_type:
        source_col = "county"
    elif crosswalk_type.startswith("CBSA_") and "DIV" not in crosswalk_type:
        source_col = "cbsa"
    elif crosswalk_type.startswith("CBSA_DIV"):
        source_col = "cbsadiv"
    elif crosswalk_type.startswith("CD_"):
        source_col = "cd"
    elif crosswalk_type.startswith("COUNTY_SUB"):
        source_col = "countysub"
    else:
        return [f"Unknown source geography for {crosswalk_type}"]

    if source_col not in df.columns:
        return [f"Source column '{source_col}' not found"]

    for ratio_col in RATIO_COLUMNS:
        if ratio_col not in df.columns:
            continue

        sums = df.group_by(source_col).agg(pl.col(ratio_col).sum().alias("ratio_sum"))

        bad_sums = sums.filter(
            (pl.col("ratio_sum") > tolerance)
            & ((pl.col("ratio_sum") < 1.0 - tolerance) | (pl.col("ratio_sum") > 1.0 + tolerance))
        )

        if len(bad_sums) > 0:
            min_sum = bad_sums["ratio_sum"].min()
            max_sum = bad_sums["ratio_sum"].max()
            issues.append(
                f"{ratio_col}: {len(bad_sums)} source geos have ratio sums outside "
                f"[{1.0 - tolerance:.2f}, {1.0 + tolerance:.2f}] (range: {min_sum:.3f} - {max_sum:.3f})"
            )

    return issues


def validate_crosswalk(
    crosswalk_type: str,
    data_dir: Path | None = None,
    check_geo_codes: bool = True,
    check_ratios: bool = True,
    sample_quarters: int = 4,
) -> ValidationResult:
    """Perform comprehensive validation on a crosswalk type.

    Args:
        crosswalk_type: Name of the crosswalk type to validate.
        data_dir: Base directory for raw data.
        check_geo_codes: Whether to validate geographic code formats.
        check_ratios: Whether to validate ratio sums.
        sample_quarters: Number of random quarters to sample for detailed checks.

    Returns:
        Comprehensive validation result.
    """
    if crosswalk_type not in VALID_CROSSWALK_NAMES:
        result = ValidationResult(crosswalk_type=crosswalk_type)
        result.is_valid = False
        result.geo_code_issues.append(f"Invalid crosswalk type: {crosswalk_type}")
        return result

    if data_dir is None:
        data_dir = HUDConfig.HUD_BRONZE_DIR

    result = validate_file_coverage(crosswalk_type, data_dir)

    if not (check_geo_codes or check_ratios):
        return result

    crosswalk_dir = data_dir / crosswalk_type
    if not crosswalk_dir.exists():
        return result

    available_files = list(crosswalk_dir.glob("*.parquet"))
    if not available_files:
        return result

    import random

    sample_files = random.sample(available_files, min(sample_quarters, len(available_files)))

    pattern = re.compile(rf"^{crosswalk_type}_(\d{{4}})_Q([1-4])\.parquet$")

    for filepath in sample_files:
        match = pattern.match(filepath.name)
        if not match:
            continue

        year = int(match.group(1))
        quarter = int(match.group(2))

        if check_geo_codes:
            geo_issues = validate_geo_codes(crosswalk_type, year, quarter, data_dir)
            for issue in geo_issues:
                result.geo_code_issues.append(f"{year} Q{quarter}: {issue}")
                result.is_valid = False

        if check_ratios:
            ratio_issues = validate_ratios(crosswalk_type, year, quarter, data_dir)
            for issue in ratio_issues:
                result.ratio_issues.append(f"{year} Q{quarter}: {issue}")

    return result
