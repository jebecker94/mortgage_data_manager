"""Integrity checks for the USPS crosswalk bronze layer.

Two checks, both aimed at problems the pipeline cannot see for itself:

- **Coverage** — which published quarters are missing from bronze. This is the
  one that earns its keep: the HUD API's ``query=All`` is only officially
  supported for 2021+, so pre-2021 downloads fail quietly, and new quarters
  appear on HUD's schedule rather than ours.
- **Ratio sums** — each source geography's allocation ratios should sum to 1.0.
  Source geographies with no addresses at all sum to 0 and are skipped; a sum
  that is neither 0 nor ~1 means the quarter arrived truncated.

Formatting lives in the CLI — these functions return data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.sources.usps_crosswalk.config import (
    CROSSWALK_TYPES_BY_NAME,
    RATIO_COLUMNS,
    VALID_CROSSWALK_NAMES,
    USPSCrosswalkConfig,
    latest_expected_quarter,
    quarters_for,
)

logger = get_logger(__name__)

_FILENAME_RE = re.compile(r"^(?P<type>[A-Z_]+)_(?P<year>\d{4})_Q(?P<quarter>[1-4])\.parquet$")


@dataclass
class ValidationResult:
    """Findings for one crosswalk type."""

    crosswalk_type: str
    missing_quarters: list[tuple[int, int]] = field(default_factory=list)
    ratio_issues: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """True when nothing was flagged."""
        return not (self.missing_quarters or self.ratio_issues)


def _source_column(crosswalk_type: str) -> str:
    """Column holding the source geography.

    Derived from the registry's ``source_geo`` rather than parsed out of the
    type name: ``CBSA_DIV_ZIP`` and ``COUNTY_SUB_ZIP`` have source columns
    ``cbsa_div`` / ``county_sub``, which prefix-matching gets wrong.

    Returns:
        The lower-cased source-geography column name.
    """
    return CROSSWALK_TYPES_BY_NAME[crosswalk_type].source_geo.lower()


def validate_file_coverage(
    crosswalk_type: str,
    data_dir: Path | None = None,
    max_year: int | None = None,
    max_quarter: int | None = None,
) -> list[tuple[int, int]]:
    """Quarters that should exist in bronze but don't.

    Args:
        crosswalk_type: Crosswalk type name (e.g. ``"ZIP_TRACT"``).
        data_dir: Bronze root. Defaults to ``USPS_CROSSWALK_BRONZE_DIR``.
        max_year: Last year to expect. Defaults to the latest published quarter.
        max_quarter: Last quarter to expect, paired with ``max_year``.

    Returns:
        Sorted ``(year, quarter)`` pairs with no bronze file.
    """
    data_dir = data_dir or USPSCrosswalkConfig.USPS_CROSSWALK_BRONZE_DIR
    through = (
        (max_year, max_quarter)
        if max_year is not None and max_quarter is not None
        else latest_expected_quarter()
    )

    crosswalk_dir = data_dir / crosswalk_type
    present: set[tuple[int, int]] = set()
    if crosswalk_dir.exists():
        for path in crosswalk_dir.glob("*.parquet"):
            match = _FILENAME_RE.match(path.name)
            if match:
                present.add((int(match["year"]), int(match["quarter"])))

    return sorted(set(quarters_for(crosswalk_type, through)) - present)


def validate_ratios(
    crosswalk_type: str,
    year: int,
    quarter: int,
    data_dir: Path | None = None,
    tolerance: float = 0.01,
) -> list[str]:
    """Check that allocation ratios sum to ~1.0 per source geography.

    Args:
        crosswalk_type: Crosswalk type name.
        year: Year of the quarter to check.
        quarter: Quarter to check.
        data_dir: Bronze root. Defaults to ``USPS_CROSSWALK_BRONZE_DIR``.
        tolerance: Allowed deviation from 1.0.

    Returns:
        One message per ratio column that has offending source geographies.
        Sums at (or near) zero are skipped — those geographies hold no
        addresses of that kind, which is expected, not an error.
    """
    data_dir = data_dir or USPSCrosswalkConfig.USPS_CROSSWALK_BRONZE_DIR
    path = (
        USPSCrosswalkConfig.bronze_path(crosswalk_type, year, quarter)
        if data_dir == USPSCrosswalkConfig.USPS_CROSSWALK_BRONZE_DIR
        else data_dir / crosswalk_type / f"{crosswalk_type}_{year}_Q{quarter}.parquet"
    )
    if not path.exists():
        return [f"File not found: {path}"]

    source_col = _source_column(crosswalk_type)
    lf = pl.scan_parquet(path)
    if source_col not in lf.collect_schema().names():
        return [f"Source column {source_col!r} not found in {path.name}"]

    issues: list[str] = []
    for ratio_col in RATIO_COLUMNS:
        if ratio_col not in lf.collect_schema().names():
            continue
        sums = lf.group_by(source_col).agg(pl.col(ratio_col).sum().alias("s"))
        bad = sums.filter(
            (pl.col("s") > tolerance)
            & ((pl.col("s") < 1.0 - tolerance) | (pl.col("s") > 1.0 + tolerance))
        ).collect()
        if bad.height:
            lo, hi = float(bad["s"].min()), float(bad["s"].max())  # type: ignore[arg-type]
            issues.append(
                f"{ratio_col}: {bad.height} source geo(s) sum outside "
                f"[{1.0 - tolerance:.2f}, {1.0 + tolerance:.2f}] "
                f"(range {lo:.3f}-{hi:.3f})"
            )
    return issues


def validate_crosswalk(
    crosswalk_type: str,
    data_dir: Path | None = None,
    check_ratios: bool = True,
    sample_quarters: int = 4,
) -> ValidationResult:
    """Run coverage and (optionally) ratio checks for one crosswalk type.

    Args:
        crosswalk_type: Crosswalk type name.
        data_dir: Bronze root. Defaults to ``USPS_CROSSWALK_BRONZE_DIR``.
        check_ratios: Whether to run the per-quarter ratio check.
        sample_quarters: How many quarters to ratio-check. Sampled evenly across
            the series (not randomly) so repeated runs agree.

    Returns:
        A `ValidationResult` for this type.
    """
    if crosswalk_type not in VALID_CROSSWALK_NAMES:
        raise ValueError(
            f"Invalid crosswalk type: {crosswalk_type}. Valid types: {VALID_CROSSWALK_NAMES}"
        )

    data_dir = data_dir or USPSCrosswalkConfig.USPS_CROSSWALK_BRONZE_DIR
    result = ValidationResult(
        crosswalk_type=crosswalk_type,
        missing_quarters=validate_file_coverage(crosswalk_type, data_dir),
    )
    if not check_ratios or sample_quarters <= 0:
        return result

    crosswalk_dir = data_dir / crosswalk_type
    if not crosswalk_dir.exists():
        return result

    quarters = sorted(
        (int(m["year"]), int(m["quarter"]))
        for m in (_FILENAME_RE.match(p.name) for p in crosswalk_dir.glob("*.parquet"))
        if m
    )
    if not quarters:
        return result

    # evenly spaced across the series: newest quarter always included, and the
    # selection is stable between runs
    step = max(1, len(quarters) // sample_quarters)
    sampled = quarters[::-1][::step][:sample_quarters]

    for year, quarter in sampled:
        for issue in validate_ratios(crosswalk_type, year, quarter, data_dir):
            result.ratio_issues.append(f"{year} Q{quarter}: {issue}")

    logger.debug(
        "%s: %d missing quarter(s), %d ratio issue(s) over %d sampled quarter(s)",
        crosswalk_type,
        len(result.missing_quarters),
        len(result.ratio_issues),
        len(sampled),
    )
    return result
