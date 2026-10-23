"""Regression tests for the MBS-UMBS harmonization layer and drift guard.

Each test pins a drift class that actually broke (or silently degraded) the production
matcher when an upstream import layer was rebuilt — see ``inputs.py``'s module
docstring for the incident log. If one of these fails after a silver/bronze rebuild,
the rebuild changed an encoding the matcher depends on.
"""

from __future__ import annotations

import datetime
import json

import polars as pl
import pytest

from mortgage_data_manager.matching.match_mbs_umbs.inputs import (
    FTHB,
    MIP,
    harmonize_match_frame,
    parse_packed_date,
)
from mortgage_data_manager.matching.match_mbs_umbs.metrics import (
    check_against_baseline,
    check_input_staleness,
    count_input_rows,
)
from mortgage_data_manager.matching.match_mbs_umbs.utils import (
    CORE_EXACT_COLUMNS,
    apply_null_tolerance,
    core_mismatch_count,
)


def _schema(lf: pl.LazyFrame) -> dict[str, pl.DataType]:
    return dict(lf.collect_schema())


# ---------------------------------------------------------------------------
# harmonize_match_frame
# ---------------------------------------------------------------------------


def test_enum_join_keys_cast_to_string() -> None:
    # 2026-06-27 fnma silver rebuild: join keys became Enum, raising SchemaError on the
    # 5-key join against UMBS String columns. Harmonization must land both sides on String.
    enum_side = pl.LazyFrame(
        {"Loan Purpose": pl.Series(["P", "R"], dtype=pl.Enum(["C", "P", "R"]))}
    )
    string_side = pl.LazyFrame({"Loan Purpose": ["P", "C"]})

    left = harmonize_match_frame(enum_side)
    right = harmonize_match_frame(string_side)

    assert _schema(left)["Loan Purpose"] == pl.String
    joined = left.join(right, on="Loan Purpose", how="inner").collect()
    assert joined["Loan Purpose"].to_list() == ["P"]


def test_boolean_fthb_maps_to_umbs_yn_encoding() -> None:
    # Same rebuild: FTHB became Boolean (true/false) vs UMBS "Y"/"N" — the equality
    # filter could never pass again. Boolean must map to Y/N; string stays untouched.
    boolean_side = harmonize_match_frame(pl.LazyFrame({FTHB: [True, False]}))
    string_side = harmonize_match_frame(pl.LazyFrame({FTHB: ["Y", "N"]}))

    assert boolean_side.collect()[FTHB].to_list() == ["Y", "N"]
    assert string_side.collect()[FTHB].to_list() == ["Y", "N"]


def test_mip_null_and_zero_encodings_agree_under_strict_filter() -> None:
    # FNMA silver stores uninsured MIP as null; FNM_ILLD stores 0.0. The strict
    # (a - b) == 0 condition is null-poisoned, which silently made the strict-MIP
    # rounds insured-only. After harmonization the uninsured pair must pass.
    mbs = harmonize_match_frame(pl.LazyFrame({MIP: [None, 25.0]})).collect()
    umbs = harmonize_match_frame(pl.LazyFrame({MIP: [0.0, 25.0]})).collect()

    strict = ((mbs[MIP] - umbs[MIP]).abs() == 0).to_list()
    assert strict == [True, True]


def test_rate_join_key_representation_noise_is_rounded_away() -> None:
    # Float64 representation noise (~1e-7) on Original Interest Rate broke the exact
    # join for ~1.1k loans. Rounding to the 3dp disclosure grid must align both sides
    # without moving on-grid values.
    noisy = pl.LazyFrame({"Original Interest Rate": [6.124999990353675, 3.875]})
    clean = pl.LazyFrame({"Original Interest Rate": [6.125, 3.875]})

    left = harmonize_match_frame(noisy).collect()
    right = harmonize_match_frame(clean).collect()

    assert left["Original Interest Rate"].to_list() == right["Original Interest Rate"].to_list()


def test_amount_key_int_and_float_sides_join() -> None:
    # ILLD stores Mortgage Loan Amount as i64 while the MLLD/FU snapshots store f64;
    # exact joins need one dtype. Both sides must land on Float64 with equal values.
    int_side = harmonize_match_frame(
        pl.LazyFrame({"Mortgage Loan Amount": pl.Series([219000], dtype=pl.Int64)})
    )
    float_side = harmonize_match_frame(
        pl.LazyFrame({"Mortgage Loan Amount": pl.Series([219000.0], dtype=pl.Float64)})
    )

    assert _schema(int_side)["Mortgage Loan Amount"] == pl.Float64
    joined = int_side.join(float_side, on="Mortgage Loan Amount", how="inner").collect()
    assert joined.height == 1


def test_numeric_string_drift_casts_non_strict() -> None:
    # UMBS bronze schema inference can present numeric columns as strings ("0.125"),
    # which raises "arithmetic on string and numeric" in the difference filters.
    lf = harmonize_match_frame(
        pl.LazyFrame({"Debt-To-Income": ["42", "junk"], "Loan Term": ["360", None]})
    ).collect()

    assert lf["Debt-To-Income"].to_list() == [42.0, None]
    assert lf["Loan Term"].to_list() == [360.0, None]


def test_untouched_columns_pass_through() -> None:
    # Harmonization only touches match columns — anything else must survive unchanged.
    lf = harmonize_match_frame(pl.LazyFrame({"Seller Name": ["A"], "custom": [1]})).collect()
    assert lf["Seller Name"].to_list() == ["A"]
    assert lf["custom"].to_list() == [1]


# ---------------------------------------------------------------------------
# parse_packed_date
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "dtype"),
    [
        ([12019, 122019], pl.Int64),  # packed MYYYY / MMYYYY ints (bronze)
        (["12019", "122019"], pl.String),  # packed strings
    ],
)
def test_parse_packed_date_variants(values: list, dtype: pl.DataType) -> None:
    lf = pl.LazyFrame({"First Payment Date": pl.Series(values, dtype=dtype)})
    out = parse_packed_date(lf, "First Payment Date").collect()
    assert out["First Payment Date"].to_list() == [
        datetime.date(2019, 1, 1),
        datetime.date(2019, 12, 1),
    ]


def test_parse_packed_date_passthrough_for_date_and_absent_column() -> None:
    # Current silver already parses packed encodings to pl.Date — must pass through
    # untouched (the pre-fix .str.zfill path crashed on Date input).
    dated = pl.LazyFrame({"First Payment Date": [datetime.date(2025, 1, 1)]})
    out = parse_packed_date(dated, "First Payment Date").collect()
    assert out["First Payment Date"].to_list() == [datetime.date(2025, 1, 1)]

    absent = pl.LazyFrame({"other": [1]})
    assert parse_packed_date(absent, "First Payment Date").collect().columns == ["other"]


# ---------------------------------------------------------------------------
# check_against_baseline (drift guard)
# ---------------------------------------------------------------------------


def _write_guard_fixtures(tmp_path, metrics: dict, baseline: dict) -> tuple:
    crosswalk_dir = tmp_path / "crosswalk"
    crosswalk_dir.mkdir()
    (crosswalk_dir / "mbs_umbs_crosswalk_fnma_metrics.json").write_text(json.dumps(metrics))
    baseline_file = tmp_path / "expected_match_rates.json"
    baseline_file.write_text(json.dumps({"fnma": baseline}))
    return crosswalk_dir, baseline_file


HEALTHY_METRICS = {
    "total_pairs": 1000,
    "round1_share": 0.8,
    "mbs_match_rate_by_fpd_year": {"2020": {"total": 100, "matched": 99, "rate": 0.99}},
}
BASELINE = {
    "total_pairs_floor": 900,
    "round1_share_floor": 0.5,
    "fpd_year_rate_floors": {"2020": 0.97},
}


def test_guard_healthy_run_has_no_breaches(tmp_path) -> None:
    crosswalk_dir, baseline_file = _write_guard_fixtures(tmp_path, HEALTHY_METRICS, BASELINE)
    assert check_against_baseline("fnma", crosswalk_dir, baseline_file) == []


@pytest.mark.parametrize(
    ("override", "expect_fragment"),
    [
        ({"total_pairs": 500}, "total pairs"),
        ({"round1_share": 0.2}, "round-1 share"),
        (
            {"mbs_match_rate_by_fpd_year": {"2020": {"total": 100, "matched": 50, "rate": 0.5}}},
            "FPD-2020 match rate",
        ),
        ({"mbs_match_rate_by_fpd_year": {}}, "missing from run metrics"),
    ],
)
def test_guard_flags_each_breach_class(tmp_path, override: dict, expect_fragment: str) -> None:
    metrics = {**HEALTHY_METRICS, **override}
    crosswalk_dir, baseline_file = _write_guard_fixtures(tmp_path, metrics, BASELINE)
    breaches = check_against_baseline("fnma", crosswalk_dir, baseline_file)
    assert len(breaches) == 1
    assert expect_fragment in breaches[0]


def test_guard_missing_sidecar_is_itself_a_breach(tmp_path) -> None:
    crosswalk_dir = tmp_path / "crosswalk"
    crosswalk_dir.mkdir()
    baseline_file = tmp_path / "expected_match_rates.json"
    baseline_file.write_text(json.dumps({"fnma": BASELINE}))
    breaches = check_against_baseline("fnma", crosswalk_dir, baseline_file)
    assert len(breaches) == 1
    assert "no metrics sidecar" in breaches[0]


# --------------------------------------------------------------------------- #
# apply_null_tolerance — missing-value semantics of the tolerance filters       #
# --------------------------------------------------------------------------- #
def test_null_tolerance_admits_one_sided_missing_and_baseline_vetoes() -> None:
    # A polars comparison against null is null, and `filter` drops nulls — so BOTH the
    # positive (`diff <= tol`) and negated (`~(diff > tol)`) forms veto a candidate over a
    # single missing attribute. Only an explicit fill_null(True) admits it. The four rows
    # are (both present & equal), (MBS missing), (UMBS missing), (both missing).
    df = pl.DataFrame({"a": [40, None, 40, None], "b": [40, 40, None, None]})
    cond = [(pl.col("a") - pl.col("b")).abs() <= 1]

    tolerant = df.filter(pl.all_horizontal(apply_null_tolerance(cond, enabled=True)))
    baseline = df.filter(pl.all_horizontal(apply_null_tolerance(cond, enabled=False)))

    assert tolerant.height == 4  # missing on either side no longer vetoes
    assert baseline.height == 1  # null-vetoing behavior preserved when disabled
    assert baseline.rows() == [(40, 40)]


def test_null_tolerance_still_rejects_a_real_disagreement() -> None:
    # Permissiveness is scoped to MISSING values only: two present-but-different values
    # must still fail, or the tolerance stops meaning anything.
    df = pl.DataFrame({"a": [40, 40], "b": [40, 55]})
    cond = [(pl.col("a") - pl.col("b")).abs() <= 1]

    out = df.filter(pl.all_horizontal(apply_null_tolerance(cond, enabled=True)))

    assert out.rows() == [(40, 40)]


# --------------------------------------------------------------------------- #
# core_mismatch_count — the round-4 score                                      #
# --------------------------------------------------------------------------- #
def _core_frame(rows: list[tuple]) -> pl.DataFrame:
    cols = [f"{c} ({side})" for c in CORE_EXACT_COLUMNS for side in ("MBS", "UMBS")]
    return pl.DataFrame(rows, schema=cols, orient="row")


def test_core_mismatch_counts_only_present_disagreements() -> None:
    # (LTV, CLTV, DTI, Term) x (MBS, UMBS). Row 1 is exact everywhere; row 2 disagrees on
    # LTV and DTI; row 3 is one-sided-null on LTV and exact elsewhere, which is NOT a
    # mismatch (an absent value is no evidence, as for the filters themselves); row 4 is
    # within the +-1 / +-6 tolerance bands but not exact, which IS a mismatch.
    df = _core_frame(
        [
            (80.0, 80.0, 80.0, 80.0, 35.0, 35.0, 360.0, 360.0),
            (80.0, 75.0, 80.0, 80.0, 35.0, 41.0, 360.0, 360.0),
            (None, 80.0, 80.0, 80.0, 35.0, 35.0, 360.0, 360.0),
            (80.0, 81.0, 80.0, 80.0, 35.0, 35.0, 360.0, 354.0),
        ]
    )

    got = df.select(core_mismatch_count().alias("mm"))["mm"].to_list()

    assert got == [0, 2, 0, 2]


def test_core_exact_columns_excludes_credit_score() -> None:
    # Credit Score is a tolerance filter too, but FNMA true pairs agree exactly only ~78%
    # of the time (the VantageScore-4 / Classic-FICO transition), so a score gap is not
    # evidence and must never enter the round-4 criterion.
    assert "Credit Score 1" not in CORE_EXACT_COLUMNS
    assert set(CORE_EXACT_COLUMNS) == {
        "Loan-To-Value",
        "Combined Loan-To-Value",
        "Debt-To-Income",
        "Loan Term",
    }


# --------------------------------------------------------------------------- #
# check_input_staleness — the structural staleness guard                       #
# --------------------------------------------------------------------------- #
def _input_dirs(tmp_path, mbs_rows: int, umbs_rows: int) -> tuple:
    mbs_dir, umbs_dir = tmp_path / "mbs", tmp_path / "umbs"
    mbs_dir.mkdir()
    umbs_dir.mkdir()
    pl.DataFrame({"x": range(mbs_rows)}).write_parquet(mbs_dir / "a.parquet")
    pl.DataFrame({"x": range(umbs_rows)}).write_parquet(umbs_dir / "a.parquet")
    return mbs_dir, umbs_dir


def _sidecar(tmp_path, metrics: dict):
    crosswalk_dir = tmp_path / "crosswalk"
    crosswalk_dir.mkdir()
    (crosswalk_dir / "mbs_umbs_crosswalk_fnma_metrics.json").write_text(json.dumps(metrics))
    return crosswalk_dir


def test_staleness_guard_is_quiet_when_input_sizes_match(tmp_path) -> None:
    mbs_dir, umbs_dir = _input_dirs(tmp_path, 10, 4)
    crosswalk_dir = _sidecar(tmp_path, {"n_mbs_input_rows": 10, "n_umbs_input_rows": 4})

    assert check_input_staleness("fnma", crosswalk_dir, mbs_dir, umbs_dir) == []


def test_staleness_guard_flags_an_input_that_grew(tmp_path) -> None:
    # The incident this guard exists for: the input grows, every file stays older than the
    # crosswalk, and the mtime heuristic sees nothing wrong.
    mbs_dir, umbs_dir = _input_dirs(tmp_path, 25, 4)
    crosswalk_dir = _sidecar(tmp_path, {"n_mbs_input_rows": 10, "n_umbs_input_rows": 4})

    warnings = check_input_staleness("fnma", crosswalk_dir, mbs_dir, umbs_dir)

    assert len(warnings) == 1
    assert "MBS input rows changed" in warnings[0]
    assert "+15" in warnings[0]


def test_staleness_guard_counts_the_snapshot_on_the_umbs_side(tmp_path) -> None:
    mbs_dir, umbs_dir = _input_dirs(tmp_path, 10, 4)
    snapshot = tmp_path / "snapshot.parquet"
    pl.DataFrame({"x": range(3)}).write_parquet(snapshot)
    crosswalk_dir = _sidecar(tmp_path, {"n_mbs_input_rows": 10, "n_umbs_input_rows": 7})

    assert check_input_staleness("fnma", crosswalk_dir, mbs_dir, umbs_dir, snapshot) == []
    assert count_input_rows(mbs_dir, umbs_dir, snapshot)["n_umbs_input_rows"] == 7


def test_staleness_guard_falls_back_for_a_sidecar_without_raw_counts(tmp_path) -> None:
    # A sidecar written before the guard shipped records only the windowed MBS count, which
    # can never exceed the raw input — so a fresh count above it still proves growth, while
    # the UMBS side (post-filter, post-dedup) has no such bound and is reported unchecked.
    mbs_dir, umbs_dir = _input_dirs(tmp_path, 25, 4)
    crosswalk_dir = _sidecar(tmp_path, {"n_mbs_loans": 10, "n_umbs_loans": 4})

    warnings = check_input_staleness("fnma", crosswalk_dir, mbs_dir, umbs_dir)

    assert len(warnings) == 2
    assert "grown by at least 15" in warnings[0]
    assert "predates the input-size guard" in warnings[1]


def test_staleness_guard_missing_sidecar_is_reported_not_raised(tmp_path) -> None:
    mbs_dir, umbs_dir = _input_dirs(tmp_path, 10, 4)
    crosswalk_dir = tmp_path / "crosswalk"
    crosswalk_dir.mkdir()

    warnings = check_input_staleness("fnma", crosswalk_dir, mbs_dir, umbs_dir)

    assert len(warnings) == 1
    assert "no metrics sidecar" in warnings[0]
