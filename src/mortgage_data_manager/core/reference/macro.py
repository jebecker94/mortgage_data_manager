"""Macroeconomic reference series for prepayment/credit analytics.

A small set of national/state macro time series that the analytics layer (and
any matching workflow) needs to contextualize loan behavior:

- ``pmms``: PMMS 30-year fixed mortgage rate (FRED ``MORTGAGE30US``), the
  market-rate benchmark for refinance incentive — weekly, averaged to monthly.
- ``hpi``: FHFA All-Transactions House Price Index by state (with a 4-quarter
  percent change) for dynamic-LTV derivation — quarterly, dated to quarter
  start.
- ``unemployment``: BLS LAUS state-level unemployment rate — monthly.
- ``yield_curve``: 10Y-2Y Treasury spread (FRED ``T10Y2Y``) — daily, averaged
  to monthly.

Each series is cached as parquet under ``MortgageDataConfig.DATA_DIR /
"reference" / "macro"`` following the :mod:`core.reference` convention. Unlike
the Census tables in this package, these series are *refreshed regularly* by
their publishers — call :func:`refresh_macro_series` (or pass
``overwrite=True``) to re-pull, otherwise the cached parquet is reused.

FRED access falls back to the public CSV endpoint (no key required); set
``FRED_API_KEY`` in ``.env`` to use the ``fredapi`` package instead.
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from typing import Literal

import polars as pl
import requests
from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)

#: Earliest observation kept across all series (analytics panels start 2018).
MACRO_START: date = date(2017, 1, 1)

MacroSeries = Literal["pmms", "hpi", "unemployment", "yield_curve"]

# FIPS code -> 2-letter state abbreviation (50 states + DC), used to key the
# BLS LAUS series to the same ``state`` abbreviation the GSE silver uses.
FIPS_TO_STATE: dict[str, str] = {
    "01": "AL",
    "02": "AK",
    "04": "AZ",
    "05": "AR",
    "06": "CA",
    "08": "CO",
    "09": "CT",
    "10": "DE",
    "11": "DC",
    "12": "FL",
    "13": "GA",
    "15": "HI",
    "16": "ID",
    "17": "IL",
    "18": "IN",
    "19": "IA",
    "20": "KS",
    "21": "KY",
    "22": "LA",
    "23": "ME",
    "24": "MD",
    "25": "MA",
    "26": "MI",
    "27": "MN",
    "28": "MS",
    "29": "MO",
    "30": "MT",
    "31": "NE",
    "32": "NV",
    "33": "NH",
    "34": "NJ",
    "35": "NM",
    "36": "NY",
    "37": "NC",
    "38": "ND",
    "39": "OH",
    "40": "OK",
    "41": "OR",
    "42": "PA",
    "44": "RI",
    "45": "SC",
    "46": "SD",
    "47": "TN",
    "48": "TX",
    "49": "UT",
    "50": "VT",
    "51": "VA",
    "53": "WA",
    "54": "WV",
    "55": "WI",
    "56": "WY",
}


def _cache_dir() -> Path:
    return MortgageDataConfig.DATA_DIR / "reference" / "macro"


def _parquet_path(series: MacroSeries) -> Path:
    return _cache_dir() / f"{series}.parquet"


def _user_agent() -> str:
    """Honest research User-Agent for BLS/FHFA (kept out of the bot-default)."""
    return config("MACRO_USER_AGENT", default="mortgage-data-manager/0.1 (research)")


def _fetch_fred_csv(series_id: str) -> pl.DataFrame:
    """Download a FRED series as a two-column (``date``, ``value``) frame.

    Tries the ``fredapi`` package when ``FRED_API_KEY`` is set, then falls back
    to the public ``fredgraph.csv`` endpoint (no key needed).

    Args:
        series_id: FRED series identifier (e.g. ``MORTGAGE30US``).

    Returns:
        A DataFrame with ``date`` (Date) and ``value`` (Float64), null
        observations (FRED ``.`` sentinel) dropped.
    """
    api_key = config("FRED_API_KEY", default="")

    if api_key:
        try:
            from fredapi import Fred  # type: ignore[import-untyped]

            s = Fred(api_key=api_key).get_series(series_id)
            return pl.DataFrame(
                {"date": s.index.date.tolist(), "value": s.values.tolist()}
            ).with_columns(
                pl.col("date").cast(pl.Date),
                pl.col("value").cast(pl.Float64),
            )
        except Exception as exc:  # noqa: BLE001 - fall back to the CSV endpoint
            logger.warning(f"fredapi failed for {series_id} ({exc}); using CSV endpoint")

    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    logger.info(f"Downloading FRED series {series_id} from {url}")
    resp = requests.get(url, timeout=60, headers={"User-Agent": _user_agent()})
    resp.raise_for_status()

    df = pl.read_csv(io.BytesIO(resp.content), infer_schema_length=0)
    date_col = next(c for c in df.columns if "date" in c.lower())
    value_col = next(c for c in df.columns if c != date_col)
    return (
        df.rename({date_col: "date", value_col: "value"})
        .with_columns(
            pl.col("date").str.to_date("%Y-%m-%d"),
            pl.when(pl.col("value").str.strip_chars() == ".")
            .then(None)
            .otherwise(pl.col("value"))
            .cast(pl.Float64)
            .alias("value"),
        )
        .drop_nulls("value")
    )


def _build_pmms() -> pl.DataFrame:
    """PMMS 30-year fixed rate, weekly averaged to monthly (first of month)."""
    df = _fetch_fred_csv("MORTGAGE30US")
    return (
        df.with_columns(pl.col("date").dt.truncate("1mo").alias("date"))
        .group_by("date")
        .agg(pl.col("value").mean().alias("mortgage_rate"))
        .filter(pl.col("date") >= MACRO_START)
        .sort("date")
        .with_columns(pl.col("date").cast(pl.Date), pl.col("mortgage_rate").cast(pl.Float64))
    )


def _build_yield_curve() -> pl.DataFrame:
    """10Y-2Y Treasury spread, daily averaged to monthly (first of month)."""
    df = _fetch_fred_csv("T10Y2Y")
    return (
        df.with_columns(pl.col("date").dt.truncate("1mo").alias("date"))
        .group_by("date")
        .agg(pl.col("value").mean().alias("yield_spread"))
        .filter(pl.col("date") >= MACRO_START)
        .sort("date")
        .with_columns(pl.col("date").cast(pl.Date), pl.col("yield_spread").cast(pl.Float64))
    )


def _build_hpi_from_fhfa() -> pl.DataFrame:
    """Download FHFA All-Transactions state HPI from the bulk CSV (raises on failure)."""
    urls = [
        "https://www.fhfa.gov/hpi/download/monthly/hpi_at_state.csv",
        "https://www.fhfa.gov/sites/default/files/2025-02/HPI_AT_state.csv",
        "https://www.fhfa.gov/sites/default/files/2024-11/HPI_AT_state.csv",
    ]
    qtr_month_map = {1: 1, 2: 4, 3: 7, 4: 10}
    for url in urls:
        try:
            logger.info(f"Trying FHFA state HPI bulk CSV: {url}")
            resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                continue
            raw = pl.read_csv(io.BytesIO(resp.content))
            raw = raw.rename({c: c.strip().lower() for c in raw.columns})
            col_map: dict[str, str] = {}
            for c in raw.columns:
                if c in ("state", "region", "area"):
                    col_map[c] = "state"
                elif c in ("yr", "year"):
                    col_map[c] = "yr"
                elif c in ("qtr", "quarter"):
                    col_map[c] = "qtr"
                elif "index" in c:
                    col_map[c] = "index_nsa"
            raw = raw.rename(col_map)
            df = (
                raw.with_columns(
                    pl.col("yr").cast(pl.Int32),
                    pl.col("qtr").cast(pl.Int32),
                    pl.col("index_nsa").cast(pl.Float64),
                )
                .with_columns(
                    pl.col("qtr")
                    .replace_strict(qtr_month_map, return_dtype=pl.Int32)
                    .alias("month")
                )
                .with_columns(pl.date(pl.col("yr"), pl.col("month"), 1).alias("date"))
                .select("state", "date", pl.col("index_nsa").alias("hpi"))
                .filter(pl.col("state").str.len_chars() == 2)
                .sort("state", "date")
            )
            logger.info(f"FHFA state HPI: {len(df):,} rows")
            return df
        except Exception as exc:  # noqa: BLE001 - try the next mirror
            logger.warning(f"FHFA HPI URL failed ({exc})")
    raise RuntimeError("All FHFA state-HPI URLs failed.")


def _build_hpi_from_fred() -> pl.DataFrame:
    """Fallback: per-state FRED ``{ST}STHPI`` series, concatenated."""
    logger.info("Falling back to FRED per-state HPI ({ST}STHPI)")
    frames: list[pl.DataFrame] = []
    for st in sorted(set(FIPS_TO_STATE.values())):
        try:
            df = _fetch_fred_csv(f"{st}STHPI").rename({"value": "hpi"})
            frames.append(df.with_columns(pl.lit(st).alias("state")).select("state", "date", "hpi"))
        except Exception as exc:  # noqa: BLE001 - skip a missing state series
            logger.warning(f"Failed to download {st}STHPI: {exc}")
    if not frames:
        raise RuntimeError("Failed to download any state HPI series from FRED.")
    return pl.concat(frames, how="diagonal").sort("state", "date")


def _build_hpi() -> pl.DataFrame:
    """FHFA state HPI with a 4-quarter percent change (``hpa_4q``)."""
    try:
        df = _build_hpi_from_fhfa()
    except RuntimeError:
        df = _build_hpi_from_fred()
    return (
        df.with_columns(
            (pl.col("hpi") / pl.col("hpi").shift(4).over("state") - 1.0).alias("hpa_4q")
        )
        .filter(pl.col("date") >= MACRO_START)
        .with_columns(
            pl.col("state").cast(pl.String),
            pl.col("date").cast(pl.Date),
            pl.col("hpi").cast(pl.Float64),
            pl.col("hpa_4q").cast(pl.Float64),
        )
    )


def _build_unemployment() -> pl.DataFrame:
    """BLS LAUS state-level unemployment rate, monthly."""
    url = "https://download.bls.gov/pub/time.series/la/la.data.3.AllStatesS"
    logger.info(f"Downloading BLS LAUS state unemployment: {url}")
    resp = requests.get(url, timeout=120, headers={"User-Agent": _user_agent()})
    resp.raise_for_status()

    raw = pl.read_csv(io.BytesIO(resp.content), separator="\t", infer_schema_length=0)
    raw = raw.rename({c: c.strip() for c in raw.columns})
    for c in raw.columns:
        raw = raw.with_columns(pl.col(c).str.strip_chars())

    # Unemployment-rate series: LASST{2-digit FIPS}...03; drop annual avg (M13).
    raw = raw.filter(
        pl.col("series_id").str.starts_with("LASST")
        & pl.col("series_id").str.ends_with("03")
        & (pl.col("period") != "M13")
    ).with_columns(pl.col("series_id").str.slice(5, 2).alias("fips"))

    fips_df = pl.DataFrame(
        {"fips": list(FIPS_TO_STATE.keys()), "state": list(FIPS_TO_STATE.values())}
    )
    raw = raw.join(fips_df, on="fips", how="inner")
    return (
        raw.with_columns(
            pl.col("year").cast(pl.Int32),
            pl.col("period").str.slice(1, 2).cast(pl.Int32).alias("month"),
            pl.when(pl.col("value") == "-")
            .then(None)
            .otherwise(pl.col("value"))
            .cast(pl.Float64)
            .alias("unemp_rate"),
        )
        .drop_nulls("unemp_rate")
        .with_columns(pl.date(pl.col("year"), pl.col("month"), 1).alias("date"))
        .select("state", "date", "unemp_rate")
        .filter(pl.col("date") >= MACRO_START)
        .sort("state", "date")
        .with_columns(
            pl.col("state").cast(pl.String),
            pl.col("date").cast(pl.Date),
            pl.col("unemp_rate").cast(pl.Float64),
        )
    )


_BUILDERS: dict[MacroSeries, object] = {
    "pmms": _build_pmms,
    "hpi": _build_hpi,
    "unemployment": _build_unemployment,
    "yield_curve": _build_yield_curve,
}


def load_macro_series(series: MacroSeries, *, overwrite: bool = False) -> pl.DataFrame:
    """Load one macro series, building (and caching) its parquet if needed.

    Args:
        series: One of ``"pmms"``, ``"hpi"``, ``"unemployment"``,
            ``"yield_curve"``.
        overwrite: Re-download and rebuild the cached parquet even if present.

    Returns:
        The series as a Polars DataFrame (see module docstring for columns).

    Raises:
        ValueError: If ``series`` is not a recognized macro series.
    """
    if series not in _BUILDERS:
        raise ValueError(f"Unknown macro series {series!r}; expected one of {list(_BUILDERS)}")
    path = _parquet_path(series)
    if path.exists() and not overwrite:
        return pl.read_parquet(path)
    df: pl.DataFrame = _BUILDERS[series]()  # type: ignore[operator]
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)
    logger.info(f"Cached macro series {series}: {len(df):,} rows -> {path}")
    return df


def load_pmms(*, overwrite: bool = False) -> pl.DataFrame:
    """PMMS 30-year fixed mortgage rate (``date``, ``mortgage_rate``)."""
    return load_macro_series("pmms", overwrite=overwrite)


def load_hpi(*, overwrite: bool = False) -> pl.DataFrame:
    """FHFA state HPI (``state``, ``date``, ``hpi``, ``hpa_4q``)."""
    return load_macro_series("hpi", overwrite=overwrite)


def load_unemployment(*, overwrite: bool = False) -> pl.DataFrame:
    """BLS LAUS state unemployment rate (``state``, ``date``, ``unemp_rate``)."""
    return load_macro_series("unemployment", overwrite=overwrite)


def load_yield_curve(*, overwrite: bool = False) -> pl.DataFrame:
    """10Y-2Y Treasury spread (``date``, ``yield_spread``)."""
    return load_macro_series("yield_curve", overwrite=overwrite)


def refresh_macro_series() -> dict[str, int]:
    """Re-download and re-cache every macro series.

    Returns:
        A mapping of series name to row count after refresh.
    """
    return {name: len(load_macro_series(name, overwrite=True)) for name in _BUILDERS}  # type: ignore[arg-type]
