"""Tests for core.geocoding.

HTTP is mocked end-to-end so these run offline. The cache uses a tmp dir
override via MortgageDataConfig.DATA_DIR monkeypatch.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from mortgage_data_manager.core.geocoding import api
from mortgage_data_manager.core.geocoding import cache as cachemod
from mortgage_data_manager.core.geocoding.providers import (
    geocode_oneline,
)


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    """Point the cache at a fresh temp dir."""
    monkeypatch.setattr(
        "mortgage_data_manager.core.geocoding.cache.MortgageDataConfig.DATA_DIR",
        tmp_path,
    )
    return tmp_path


def _make_session(response_map):
    """Build a mock requests.Session whose GET returns canned JSON keyed by address."""
    session = MagicMock()

    def _get(url, params=None, timeout=None):
        addr = (params or {}).get("address", "")
        resp = MagicMock()
        body = response_map.get(addr)
        if body is None:
            resp.status_code = 200
            resp.json.return_value = {"result": {"addressMatches": []}}
        else:
            resp.status_code = 200
            resp.json.return_value = body
        resp.raise_for_status = MagicMock()
        return resp

    session.get.side_effect = _get
    return session


def test_address_hash_is_stable_and_case_insensitive():
    h1 = cachemod.address_hash("123 Main St", "Springfield", "IL", "62701")
    h2 = cachemod.address_hash("  123 main st  ", "springfield", "il", "62701-1234")
    assert h1 == h2


def test_attach_hash_round_trips():
    df = pl.DataFrame(
        {
            "address_id": ["a", "b"],
            "street": ["123 Main St", "456 Oak Ave"],
            "city": ["Springfield", "Centralia"],
            "state": ["IL", "MO"],
            "zip": ["62701", "65240"],
        }
    )
    out = cachemod.attach_hash(df)
    assert "address_hash" in out.columns
    assert out["address_hash"].n_unique() == 2


def test_oneline_no_match_returns_empty(isolated_cache):
    session = _make_session({})
    results = geocode_oneline(
        [{"address_id": "1", "street": "Nowhere Pl", "city": "X", "state": "ZZ", "zip": "99999"}],
        provider="census_current",
        session=session,
        max_workers=1,
        progress=False,
    )
    assert results[0]["matched"] is False
    assert results[0]["match_reason"] == "no_match"


def test_oneline_match_returns_coords(isolated_cache):
    one = "1600 Pennsylvania Ave, Washington, DC 20500"
    body = {
        "result": {
            "addressMatches": [
                {
                    "coordinates": {"x": -77.036, "y": 38.898},
                    "matchedAddress": "1600 PENNSYLVANIA AVE, WASHINGTON, DC, 20500",
                    "tigerLine": {"side": "R", "tigerLineId": "1234567"},
                }
            ]
        }
    }
    session = _make_session({one: body})
    results = geocode_oneline(
        [{"address_id": "1", "street": "1600 Pennsylvania Ave", "city": "Washington", "state": "DC", "zip": "20500"}],
        provider="census_current",
        session=session,
        max_workers=1,
        progress=False,
    )
    assert results[0]["matched"] is True
    assert results[0]["lat"] == pytest.approx(38.898)
    assert results[0]["lon"] == pytest.approx(-77.036)
    assert results[0]["confidence"] == "medium"


def test_geocode_addresses_writes_and_reads_cache(isolated_cache):
    df = pl.DataFrame(
        {
            "address_id": ["a"],
            "street": ["1600 Pennsylvania Ave"],
            "city": ["Washington"],
            "state": ["DC"],
            "zip": ["20500"],
        }
    )
    body = {
        "result": {
            "addressMatches": [
                {
                    "coordinates": {"x": -77.036, "y": 38.898},
                    "matchedAddress": "1600 PENNSYLVANIA AVE, WASHINGTON, DC, 20500",
                    "tigerLine": {"side": "R", "tigerLineId": "1234567"},
                }
            ]
        }
    }
    one = "1600 Pennsylvania Ave, Washington, DC 20500"

    with patch(
        "mortgage_data_manager.core.geocoding.providers.requests.Session"
    ) as MockSession:
        MockSession.return_value = _make_session({one: body})
        out = api.geocode_addresses(df, max_workers=1, progress=False)

    assert out["geocode_matched"].to_list() == [True]
    assert cachemod.cache_path().exists()
    cache_df = cachemod.load_cache()
    assert len(cache_df) == 1
    assert cache_df["geocode_provider"].to_list() == ["census_current"]

    # Second call must NOT hit the network — patch with a session that would raise on use.
    with patch(
        "mortgage_data_manager.core.geocoding.providers.requests.Session"
    ) as MockSession2:
        MockSession2.return_value.get.side_effect = AssertionError("should not be called")
        out2 = api.geocode_addresses(df, max_workers=1, progress=False)
    assert out2["geocode_matched"].to_list() == [True]


def test_geocode_addresses_caches_misses(isolated_cache):
    df = pl.DataFrame(
        {
            "address_id": ["z"],
            "street": ["Nowhere Pl"],
            "city": ["Nowhere"],
            "state": ["ZZ"],
            "zip": ["99999"],
        }
    )
    with patch("mortgage_data_manager.core.geocoding.providers.requests.Session") as MockSession:
        MockSession.return_value = _make_session({})
        out = api.geocode_addresses(df, max_workers=1, progress=False)
    assert out["geocode_matched"].to_list() == [False]
    assert cachemod.load_cache().height == 1
    # And it doesn't re-call on retry.
    with patch("mortgage_data_manager.core.geocoding.providers.requests.Session") as MockSession2:
        MockSession2.return_value.get.side_effect = AssertionError("should not be called")
        out2 = api.geocode_addresses(df, max_workers=1, progress=False)
    assert out2["geocode_matched"].to_list() == [False]


def test_purge_cache(isolated_cache):
    df = pl.DataFrame(
        {
            "address_id": ["a"],
            "street": ["Nowhere Pl"],
            "city": ["X"],
            "state": ["ZZ"],
            "zip": ["99999"],
        }
    )
    with patch("mortgage_data_manager.core.geocoding.providers.requests.Session") as MockSession:
        MockSession.return_value = _make_session({})
        api.geocode_addresses(df, max_workers=1, progress=False)
    assert cachemod.load_cache().height == 1
    removed = cachemod.purge_cache(match_reason=["no_match"])
    assert removed == 1
    assert cachemod.load_cache().height == 0
