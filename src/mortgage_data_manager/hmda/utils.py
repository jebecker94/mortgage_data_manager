"""Utility Functions for HMDA Data Processing.

Helpers for I/O, cleaning, identity keys, and schema handling.
"""

from __future__ import annotations

import subprocess
import zipfile
from collections.abc import Sequence
from csv import Sniffer
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.hmda.config import HMDA_INDEX_COLUMN

logger = get_logger(__name__)


def append_hmda_index(
    lf: pl.LazyFrame, year: int, file_type_code: str
) -> pl.LazyFrame:
    """Format a row-index column into the canonical HMDAIndex string.

    The HMDAIndex is a stable per-record locator shared across every HMDA era:
    activity year (4 digits) + file-type code (1 char) + zero-padded 9-digit row
    number, e.g. ``2018a_000000001``. The caller must already have added an
    integer row index named :data:`HMDA_INDEX_COLUMN` (e.g. via
    ``scan_csv(row_index_name=...)`` at bronze, or ``with_row_index`` at silver).

    Args:
        lf: Lazy frame carrying an integer row index in ``HMDA_INDEX_COLUMN``.
        year: Activity year, used as the 4-digit prefix.
        file_type_code: Single-character file-type code (a, b, c, d, e).

    Returns:
        Lazy frame with ``HMDA_INDEX_COLUMN`` rewritten as the formatted string.
    """
    prefix = f"{year}{file_type_code}_"
    return (
        lf.cast({HMDA_INDEX_COLUMN: pl.String}, strict=False)
        .with_columns(pl.col(HMDA_INDEX_COLUMN).str.zfill(9).alias(HMDA_INDEX_COLUMN))
        .with_columns((pl.lit(prefix) + pl.col(HMDA_INDEX_COLUMN)).alias(HMDA_INDEX_COLUMN))
    )


# =============================================================================
# I/O Utilities
# =============================================================================


def normalized_file_stem(stem: str) -> str:
    """Remove common suffixes from extracted archive names.

    Args:
        stem: File stem (name without extension)

    Returns:
        Normalized file stem with common suffixes removed
    """
    if stem.endswith("_csv"):
        stem = stem[:-4]
    if stem.endswith("_pipe"):
        stem = stem[:-5]
    return stem


def get_delimiter(file_path: Path | str, bytes: int = 4096) -> str:
    """Determine the delimiter used in a delimited text file."""
    sniffer = Sniffer()
    data = open(file_path, encoding="latin-1").read(bytes)
    return sniffer.sniff(data).delimiter


def replace_csv_column_names(
    csv_file: Path | str, column_name_mapper: dict[str, str] | None = None
) -> None:
    """Replace column headers in a CSV file based on a mapping."""
    if column_name_mapper is None:
        column_name_mapper = {}

    delimiter = get_delimiter(csv_file, bytes=16000)
    with open(csv_file) as f:
        first_line = f.readline().strip()

    first_line_items = first_line.split(delimiter)
    new_first_line_items = []
    for first_line_item in first_line_items:
        for key, item in column_name_mapper.items():
            if first_line_item == key:
                first_line_item = item
        new_first_line_items.append(first_line_item)
    new_first_line = delimiter.join(new_first_line_items)

    with open(csv_file) as f:
        lines = f.readlines()
    lines[0] = new_first_line + "\n"
    with open(csv_file, "w") as f:
        f.writelines(lines)


def unzip_hmda_file(
    zip_file: Path | str, raw_folder: Path | str, overwrite: bool = False
) -> Path:
    """Extract a compressed HMDA archive and return extracted file path."""
    zip_file = Path(zip_file)
    raw_folder = Path(raw_folder)
    if zip_file.suffix.lower() != ".zip":
        zip_file = zip_file.with_suffix(".zip")
        if not zip_file.exists():
            raise ValueError(
                "The file name was not given as a zip file. Failed to find a comparably-named zip file."
            )

    with zipfile.ZipFile(zip_file) as z:
        delimited_files = [
            x for x in z.namelist() if (x.endswith(".txt") or x.endswith(".csv")) and "/" not in x
        ]
        for file in delimited_files:
            raw_file_name = raw_folder / file
            if (not raw_file_name.exists()) or overwrite:
                logger.info("Extracting file: %s", file)
                try:
                    z.extract(file, path=raw_folder)
                except Exception as exc:
                    import shutil

                    sevenz = shutil.which("7z") or shutil.which("7zz")
                    if sevenz is None:
                        raise OSError(
                            f"Failed to extract {file} from {zip_file} "
                            f"({type(exc).__name__}: {exc}). "
                            "Install 7z (`brew install p7zip`) for fallback extraction."
                        ) from exc
                    logger.warning(
                        "zipfile extraction failed for %s (%s). Falling back to 7z.",
                        file,
                        exc,
                    )
                    p = subprocess.Popen(
                        [sevenz, "e", str(zip_file), f"-o{raw_folder}", file, "-y"]
                    )
                    p.wait()

            if "panel" in file:
                column_name_mapper = {
                    "topholder_rssd": "top_holder_rssd",
                    "topholder_name": "top_holder_name",
                    "upper": "lei",
                }
                replace_csv_column_names(raw_file_name, column_name_mapper=column_name_mapper)

    return raw_file_name


# =============================================================================
# Identity / Key Utilities
# =============================================================================


def add_identity_keys(
    df: pl.DataFrame,
    post2018: bool,
    uli_col: str = "universal_loan_identifier",
    lei_col: str = "lei",
    respondent_id_col: str = "respondent_id",
    agency_col: str = "agency_code",
    seq_col: str = "sequence_number",
) -> pl.DataFrame:
    """Construct a stable HMDA record key (Polars)."""
    out = df.clone()
    if post2018:
        for column in (uli_col, lei_col):
            if column not in out.columns:
                out = out.with_columns(pl.lit(None).alias(column))
        out = out.with_columns(
            pl.concat_str(
                [
                    pl.col(lei_col).cast(pl.Utf8).str.strip_chars(),
                    pl.lit("||"),
                    pl.col(uli_col).cast(pl.Utf8).str.strip_chars(),
                ]
            ).alias("hmda_record_key")
        )
    else:
        for column in (respondent_id_col, agency_col, seq_col):
            if column not in out.columns:
                out = out.with_columns(pl.lit(None).alias(column))
        out = out.with_columns(
            pl.concat_str(
                [
                    pl.col(respondent_id_col).cast(pl.Utf8).str.strip_chars(),
                    pl.lit("||"),
                    pl.col(agency_col).cast(pl.Utf8).str.strip_chars(),
                    pl.lit("||"),
                    pl.col(seq_col).cast(pl.Utf8).str.strip_chars(),
                ]
            ).alias("hmda_record_key")
        )
    return out


def deduplicate_records(
    df: pl.DataFrame,
    keep: str = "last",
    subset: Sequence[str] | None = None,
) -> pl.DataFrame:
    """Drop duplicate rows by key or subset (Polars)."""
    if "hmda_record_key" not in df.columns:
        raise ValueError("Run add_identity_keys() before deduplication.")
    dedupe_subset = list(subset) if subset is not None else ["hmda_record_key"]
    before = df.height
    out = df.unique(subset=dedupe_subset, keep=keep, maintain_order=True)
    dropped = before - out.height
    return out.with_columns(pl.lit(dropped).alias("_metadata_dedup_dropped"))


# =============================================================================
# Cleaning Utilities
# =============================================================================


def replace_na_like_values(
    df: pl.DataFrame,
    columns: Sequence[str],
    na_like: Sequence[str] = ("NA", "N/A", "Exempt", "Not Applicable", "NA   ", "nan"),
) -> pl.DataFrame:
    columns_to_update = [column for column in columns if column in df.columns]
    if not columns_to_update:
        return df.clone()
    replacements = list(na_like)
    return df.with_columns(
        [
            pl.col(column).replace(replacements, [None] * len(replacements)).alias(column)
            for column in columns_to_update
        ]
    )


def coerce_numeric_columns(
    df: pl.DataFrame,
    numeric_columns: Sequence[str],
) -> pl.DataFrame:
    columns_to_update = [column for column in numeric_columns if column in df.columns]
    if not columns_to_update:
        return df.clone()
    return df.with_columns(
        [pl.col(column).cast(pl.Float64, strict=False).alias(column) for column in columns_to_update]
    )


def standardize_schema(
    df: pl.DataFrame,
    post2018: bool,
    rename_map_post2018: dict[str, str] | None = None,
    rename_map_pre2018: dict[str, str] | None = None,
) -> pl.DataFrame:
    out = df.clone()
    if post2018:
        default_map = {
            "loan_amount": "loan_amount",
            "applicant_income": "applicant_income",
            "debt_to_income_ratio": "dti",
            "loan_to_value_ratio": "ltv",
            "credit_score": "credit_score",
            "action_taken": "action_taken",
            "lien_status": "lien_status",
            "loan_purpose": "loan_purpose",
            "loan_type": "loan_type",
            "occupancy_type": "occupancy_type",
            "rate_spread": "rate_spread",
            "hoepa_status": "hoepa_status",
            "purchaser_type": "purchaser_type",
        }
        if rename_map_post2018:
            default_map.update(rename_map_post2018)
        out = out.rename(default_map)
    else:
        default_map = {
            "applicant_income_000s": "applicant_income",
            "action_taken_name": "action_taken",
        }
        if rename_map_pre2018:
            default_map.update(rename_map_pre2018)
        out = out.rename(default_map)

    numeric_columns = [
        column
        for column in (
            "loan_amount",
            "applicant_income",
            "dti",
            "ltv",
            "credit_score",
            "rate_spread",
        )
        if column in out.columns
    ]
    return coerce_numeric_columns(out, numeric_columns)


def normalize_missing_and_derived(
    df: pl.DataFrame,
    post2018: bool,
    na_like: Sequence[str] = ("NA", "N/A", "Exempt", "Not Applicable"),
) -> pl.DataFrame:
    out = replace_na_like_values(df, df.columns, na_like)
    if post2018:
        for raw_column, derived_column in (
            ("race", "derived_race"),
            ("ethnicity", "derived_ethnicity"),
            ("sex", "derived_sex"),
        ):
            if derived_column in out.columns:
                out = out.with_columns(pl.col(derived_column).alias(raw_column))
    return out


def harmonize_census_tract(
    df: pl.DataFrame,
    crosswalk: pl.DataFrame | None = None,
    tract_col: str = "census_tract",
    year_col: str = "activity_year",
    to_vintage: int | None = 2020,
    crosswalk_cols: tuple[str, str] = ("tract_src", "tract_2020"),
) -> pl.DataFrame:
    if crosswalk is None or tract_col not in df.columns or year_col not in df.columns:
        return df.clone()
    if not isinstance(crosswalk, pl.DataFrame):
        raise TypeError("crosswalk must be a Polars DataFrame")
    cw = crosswalk.clone()
    src_col, dst_col = crosswalk_cols
    if src_col not in cw.columns or dst_col not in cw.columns:
        return df.clone()
    if "target_year" in cw.columns and to_vintage is not None:
        cw = cw.filter(pl.col("target_year") == to_vintage)
    right_columns = [src_col, dst_col]
    join_left = [tract_col]
    join_right = [src_col]
    if "year" in cw.columns:
        cw = cw.with_columns(pl.col("year"))
        right_columns.append("year")
        join_left.append(year_col)
        join_right.append("year")
    if "target_year" in cw.columns:
        right_columns.append("target_year")
    cw_subset = cw.select(right_columns)
    merged = df.join(cw_subset, left_on=join_left, right_on=join_right, how="left")
    drop_columns = [src_col, dst_col]
    if "year" in right_columns:
        drop_columns.append("year")
    if "target_year" in right_columns:
        drop_columns.append("target_year")
    merged = merged.with_columns(
        pl.coalesce([pl.col(dst_col), pl.col(tract_col)]).alias(tract_col)
    )
    drop_columns = [column for column in drop_columns if column in merged.columns]
    if drop_columns:
        merged = merged.drop(drop_columns)
    return merged


def apply_plausibility_filters(
    df: pl.DataFrame,
    bounds: dict[str, tuple[float | None, float | None]] | None = None,
) -> pl.DataFrame:
    default_bounds: dict[str, tuple[float | None, float | None]] = {
        "ltv": (0, 200),
        "credit_score": (500, 820),
        "dti": (0, 250),
        "applicant_income": (0, 1_000_000),
        "loan_amount": (0, 1_500_000),
    }
    if bounds:
        default_bounds.update(bounds)
    mask_expr = pl.lit(True)
    for column, (lower, upper) in default_bounds.items():
        if column not in df.columns:
            continue
        values = pl.col(column).cast(pl.Float64, strict=False)
        column_mask = values.is_null()
        if lower is not None:
            column_mask = column_mask | (values >= lower)
        if upper is not None:
            column_mask = column_mask | (values <= upper)
        mask_expr = mask_expr & column_mask
    filtered = df.filter(mask_expr)
    dropped = df.height - filtered.height
    return filtered.with_columns(pl.lit(dropped).alias("_metadata_plausibility_dropped"))


def clean_rate_spread(
    df: pl.DataFrame,
    post2018: bool,
    rate_spread_col: str = "rate_spread",
) -> pl.DataFrame:
    if rate_spread_col not in df.columns:
        return df.clone()
    max_abs = 20 if post2018 else 20
    cast_col = pl.col(rate_spread_col).cast(pl.Float64, strict=False)
    cleaned = pl.when(cast_col.abs() > max_abs).then(None).otherwise(cast_col)
    return df.with_columns(cleaned.alias(rate_spread_col))


def flag_outliers_basic(
    df: pl.DataFrame,
    columns: Sequence[str] = ("interest_rate", "rate_spread", "total_loan_costs"),
    z_threshold: float = 6.0,
) -> pl.DataFrame:
    out = df.clone()
    for column in columns:
        if column not in out.columns:
            continue
        stats = out.select(
            pl.col(column).cast(pl.Float64, strict=False).mean().alias("mean"),
            pl.col(column).cast(pl.Float64, strict=False).std().alias("std"),
        )
        mean_val = stats["mean"][0]
        std_val = stats["std"][0]
        if std_val is not None and std_val > 0:
            out = out.with_columns(
                (
                    (pl.col(column).cast(pl.Float64, strict=False) - mean_val).abs()
                    > z_threshold * std_val
                ).alias(f"outlier_{column}")
            )
    return out


def clean_hmda(
    df: pl.DataFrame,
    post2018: bool,
    bounds: dict[str, tuple[float | None, float | None]] | None = None,
    crosswalk: pl.DataFrame | None = None,
) -> pl.DataFrame:
    out = add_identity_keys(df, post2018=post2018)
    out = deduplicate_records(out, keep="last")
    out = standardize_schema(out, post2018=post2018)
    out = normalize_missing_and_derived(out, post2018=post2018)
    out = harmonize_census_tract(out, crosswalk=crosswalk)
    out = apply_plausibility_filters(out, bounds=bounds)
    out = clean_rate_spread(out, post2018=post2018)
    out = flag_outliers_basic(out)
    return out


__all__ = [
    # I/O
    "normalized_file_stem",
    "get_delimiter",
    "replace_csv_column_names",
    "unzip_hmda_file",
    # Identity
    "add_identity_keys",
    "deduplicate_records",
    # Cleaning
    "replace_na_like_values",
    "coerce_numeric_columns",
    "standardize_schema",
    "normalize_missing_and_derived",
    "harmonize_census_tract",
    "apply_plausibility_filters",
    "clean_rate_spread",
    "flag_outliers_basic",
    "clean_hmda",
]
