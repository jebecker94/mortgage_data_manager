"""On-disk cache for geocoded addresses.

Global cache at ``data/reference/geocoder_cache/geocoder_cache.parquet``,
keyed on a sha1 hash of the normalized address. Shared across subpackages so
the same address is never geocoded twice across the project.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import polars as pl

from mortgage_data_manager.core.config import MortgageDataConfig
from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)


CACHE_SCHEMA = {
    "address_hash": pl.Utf8,
    "street_norm": pl.Utf8,
    "city_norm": pl.Utf8,
    "state_norm": pl.Utf8,
    "zip5": pl.Utf8,
    "geocode_lat": pl.Float64,
    "geocode_lon": pl.Float64,
    "geocode_matched": pl.Boolean,
    "geocode_match_reason": pl.Utf8,
    "geocode_match_type": pl.Utf8,
    "geocode_matched_address": pl.Utf8,
    "geocode_tigerline_id": pl.Utf8,
    "geocode_provider": pl.Utf8,
    "geocode_confidence": pl.Utf8,
    "geocode_cached_at": pl.Date,
}


def cache_dir() -> Path:
    return MortgageDataConfig.DATA_DIR / "reference" / "geocoder_cache"


def cache_path() -> Path:
    return cache_dir() / "geocoder_cache.parquet"


def _lock_path() -> Path:
    return cache_dir() / "geocoder_cache.lock"


def normalize_address(street: str | None, city: str | None, state: str | None, zip_: str | None) -> tuple[str, str, str, str]:
    """Canonicalize an address tuple for hashing + cache lookup.

    Returns:
        (street_norm, city_norm, state_norm, zip5).
    """
    s = (street or "").strip().upper()
    c = (city or "").strip().upper()
    st = (state or "").strip().upper()
    z = (zip_ or "").strip()[:5]
    return s, c, st, z


def address_hash(street: str | None, city: str | None, state: str | None, zip_: str | None) -> str:
    s, c, st, z = normalize_address(street, city, state, zip_)
    return hashlib.sha1(f"{s}|{c}|{st}|{z}".encode()).hexdigest()


def attach_hash(df: pl.DataFrame, street: str = "street", city: str = "city", state: str = "state", zip_: str = "zip") -> pl.DataFrame:
    """Append `_street_norm`, `_city_norm`, `_state_norm`, `_zip5`, `address_hash`."""
    return df.with_columns(
        pl.col(street).fill_null("").str.strip_chars().str.to_uppercase().alias("_street_norm"),
        pl.col(city).fill_null("").str.strip_chars().str.to_uppercase().alias("_city_norm"),
        pl.col(state).fill_null("").str.strip_chars().str.to_uppercase().alias("_state_norm"),
        pl.col(zip_).fill_null("").str.strip_chars().str.slice(0, 5).alias("_zip5"),
    ).with_columns(
        pl.concat_str(["_street_norm", "_city_norm", "_state_norm", "_zip5"], separator="|").alias("_addr_key")
    ).with_columns(
        pl.col("_addr_key").map_elements(
            lambda s: hashlib.sha1(s.encode("utf-8")).hexdigest(),
            return_dtype=pl.Utf8,
        ).alias("address_hash"),
    ).drop("_addr_key")


def load_cache() -> pl.DataFrame:
    """Read the cache parquet. Returns an empty DataFrame with the right schema if absent."""
    p = cache_path()
    if not p.exists():
        return pl.DataFrame(schema=CACHE_SCHEMA)
    return pl.read_parquet(p)


def _acquire_lock(timeout_seconds: float = 30.0) -> None:
    """Best-effort file lock for concurrent writes to the cache parquet."""
    lock = _lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return
        except FileExistsError:
            if time.monotonic() - start > timeout_seconds:
                # Stale lock — overwrite.
                logger.warning(f"Geocoder cache lock held >{timeout_seconds}s; forcing release")
                try:
                    lock.unlink()
                except FileNotFoundError:
                    pass
                continue
            time.sleep(0.1)


def _release_lock() -> None:
    try:
        _lock_path().unlink()
    except FileNotFoundError:
        pass


def append_cache(new_rows: pl.DataFrame) -> int:
    """Append rows to the cache parquet (atomic write via temp file + rename).

    Dedupes on address_hash, keeping the latest geocode_cached_at when an
    address is seen more than once.

    Returns:
        Number of rows written to the cache after dedupe.
    """
    if new_rows.is_empty():
        return 0
    # Coerce schema strictly
    new_rows = new_rows.select([pl.col(k).cast(v) for k, v in CACHE_SCHEMA.items()])
    p = cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    _acquire_lock()
    try:
        existing = load_cache()
        combined = pl.concat([existing, new_rows], how="diagonal_relaxed")
        deduped = (
            combined.sort("geocode_cached_at", descending=True)
            .unique(subset=["address_hash"], keep="first", maintain_order=True)
            .sort("address_hash")
        )
        # Atomic write
        tmp_fd, tmp_name = tempfile.mkstemp(dir=p.parent, suffix=".tmp.parquet")
        os.close(tmp_fd)
        deduped.write_parquet(tmp_name)
        os.replace(tmp_name, p)
        return len(deduped)
    finally:
        _release_lock()


def purge_cache(
    *,
    older_than: date | None = None,
    providers: Sequence[str] | None = None,
    match_reason: Sequence[str] | None = None,
) -> int:
    """Remove rows from the cache matching the given filters. Returns rows removed."""
    p = cache_path()
    if not p.exists():
        return 0
    _acquire_lock()
    try:
        cache = pl.read_parquet(p)
        n_before = len(cache)
        if older_than is not None:
            cache = cache.filter(pl.col("geocode_cached_at") >= older_than)
        if providers is not None:
            cache = cache.filter(~pl.col("geocode_provider").is_in(list(providers)))
        if match_reason is not None:
            cache = cache.filter(~pl.col("geocode_match_reason").is_in(list(match_reason)))
        tmp_fd, tmp_name = tempfile.mkstemp(dir=p.parent, suffix=".tmp.parquet")
        os.close(tmp_fd)
        cache.write_parquet(tmp_name)
        os.replace(tmp_name, p)
        return n_before - len(cache)
    finally:
        _release_lock()
