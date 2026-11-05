"""Data guard: the matchers' GNMA readers reproduce the per-file code they replaced, on real silver.

The hmda_gnma and fhlb_mbs matchers were moved onto `sources.gnma.load` only where the output
is identical. Each test keeps the replaced matcher code inline as the reference and compares it
to the matcher function on a few real silver months (linked into a temporary silver root, so
the run stays small). Skips when the data volume is not mounted.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from mortgage_data_manager.matching.match_fhlb_mbs import matching as fhlb
from mortgage_data_manager.matching.match_hmda_gnma import matching_fha, matching_va_usda
from mortgage_data_manager.matching.match_hmda_gnma import triangulation as tri
from mortgage_data_manager.sources.gnma.config import GNMAConfig

_SILVER = GNMAConfig.GNMA_SILVER_DIR
#: Early (no origination date, no ARM fields), pre/post the 2018 universe floor, and newest.
_MONTHS = ("201309", "201801", "202105", "202607")

pytestmark = pytest.mark.skipif(
    not (_SILVER / "dailyllmni" / "L").exists(), reason="GNMA silver not mounted"
)


@pytest.fixture
def silver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for rt in ("L", "P"):
        (tmp_path / "dailyllmni" / rt).mkdir(parents=True)
        for m in _MONTHS:
            name = f"dailyllmni_{m}_{rt}.parquet"
            (tmp_path / "dailyllmni" / rt / name).symlink_to(_SILVER / "dailyllmni" / rt / name)
    (tmp_path / "issrinfo").symlink_to(_SILVER / "issrinfo")
    monkeypatch.setattr(GNMAConfig, "GNMA_SILVER_DIR", tmp_path)
    return tmp_path


def _l_files(silver: Path, min_month: str = "") -> list[Path]:
    files = sorted((silver / "dailyllmni" / "L").glob("dailyllmni_*_L.parquet"))
    return [f for f in files if f.stem.split("_")[1] >= min_month]


def _sorted(df: pl.DataFrame) -> pl.DataFrame:
    return df.sort(df.columns, nulls_last=True)


@pytest.mark.parametrize("agencies", [("F",), ("V", "R")])
def test_fha_universe_matches_per_file_reader(silver: Path, agencies: tuple[str, ...]) -> None:
    sel: list[pl.Expr] = []
    for alias, src in matching_fha._GNMA_COLS.items():
        if alias == "gnma_orig_date":
            sel.append(pl.col(src).dt.year().cast(pl.Int32).alias("gnma_orig_year"))
            sel.append(pl.col(src).dt.month().cast(pl.Int32).alias("gnma_orig_month"))
            continue
        expr = pl.col(src)
        if alias in matching_fha._GNMA_FLOAT_COLS:
            expr = expr.cast(pl.Float64, strict=False)
        elif alias in matching_fha._GNMA_INT_COLS:
            expr = expr.cast(pl.Float64, strict=False).cast(pl.Int64, strict=False)
        else:
            expr = expr.cast(pl.Utf8)
        sel.append(expr.alias(alias))
    frames = [
        pl.scan_parquet(f)
        .select(sel)
        .filter(pl.col("agency").is_in(list(agencies)) & pl.col("gnma_purpose").is_in(["1", "2"]))
        for f in _l_files(silver, "201801")
    ]
    old = (
        pl.concat(frames, how="diagonal_relaxed")
        .unique()
        .unique(subset=["gnma_loan_id"], keep="none")
        .collect()
    )
    new = matching_fha.load_gnma_fha_universe("201801", agencies)
    assert old.height > 0
    assert_frame_equal(_sorted(new), _sorted(old))


def test_purpose_table_matches_per_file_reader(silver: Path) -> None:
    sel = [pl.col("disclosure_sequence_number").cast(pl.Utf8).alias("gnma_loan_id")]
    sel += [
        pl.col(src).cast(pl.Utf8).alias(alias)
        for alias, src in matching_va_usda._GNMA_PURPOSE_COLS.items()
    ]
    old = (
        pl.concat([pl.scan_parquet(f).select(sel) for f in _l_files(silver, "201801")])
        .unique()
        .unique(subset=["gnma_loan_id"], keep="none")
        .collect()
    )
    assert_frame_equal(_sorted(matching_va_usda.load_gnma_purpose("201801")), _sorted(old))


def test_issuer_names_match_inline_reader(silver: Path) -> None:
    old = (
        pl.scan_parquet(silver / "issrinfo" / "**" / "*.parquet")
        .select(
            pl.col("issuer_id").cast(pl.Utf8).str.strip_chars(),
            pl.col("issuer_name").str.strip_chars(),
            "record_as_of_date",
        )
        .filter(pl.col("issuer_id").is_not_null() & pl.col("issuer_name").is_not_null())
        .filter(pl.col("record_as_of_date") == pl.col("record_as_of_date").max().over("issuer_id"))
        .select("issuer_id", "issuer_name")
        .unique()
        .filter(pl.len().over("issuer_id") == 1)
        .collect()
    )
    assert_frame_equal(_sorted(tri.issuer_names()), _sorted(old))
    va = matching_va_usda._load_issuer_names().rename({"pool_issuer": "issuer_name"})
    assert_frame_equal(_sorted(va), _sorted(old))


def test_attach_issuer_id_matches_glob_reader(silver: Path) -> None:
    pairs = pl.read_parquet(_l_files(silver)[-1], columns=["disclosure_sequence_number"]).select(
        pl.col("disclosure_sequence_number").cast(pl.Utf8).alias("gnma_loan_id")
    )
    old_issuer = (
        pl.scan_parquet(
            silver / "dailyllmni" / "L" / "*.parquet",
            extra_columns="ignore",
            missing_columns="insert",
        )
        .select(
            pl.col("disclosure_sequence_number").cast(pl.Utf8).alias("gnma_loan_id"),
            pl.col("issuer_id").cast(pl.Utf8),
        )
        .join(pairs.lazy(), on="gnma_loan_id", how="semi")
        .unique()
        .filter(pl.len().over("gnma_loan_id") == 1)
        .collect()
    )
    old = pairs.join(old_issuer, on="gnma_loan_id", how="left")
    assert_frame_equal(_sorted(tri.attach_issuer_id(pairs)), _sorted(old))


def test_fhlb_mbs_gnma_side_matches_per_file_reader(silver: Path) -> None:
    raw = pl.concat([pl.read_parquet(f) for f in _l_files(silver)], how="diagonal_relaxed")
    fp_year = pl.col("first_payment_date").dt.year().cast(pl.Int64, strict=False)
    old = raw.filter((fp_year >= 2009) & (fp_year <= 2026))  # every issuer: a bigger sample
    new = fhlb.load_gnma(silver, 2009, 2025, issuer_id=None)
    assert_frame_equal(_sorted(fhlb.prepare_gnma(new)), _sorted(fhlb.prepare_gnma(old)))
