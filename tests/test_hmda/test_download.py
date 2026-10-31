"""Unit tests for the HMDA download URL table.

The table is pure logic — filename construction and coverage bookkeeping with no
network — and it is the only thing standing between a CLI invocation and the
right bytes, so its edges are worth pinning. In particular, series CFPB has
stopped publishing carry a closed last year: reopening one by accident means
probing a URL that will 404 on every run forever.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mortgage_data_manager.sources.hmda.download import (
    build_url,
    discover_files,
)


@pytest.mark.parametrize(
    ("product", "record_type", "year", "expected"),
    [
        (
            "snapshot",
            "loans",
            2023,
            "https://files.ffiec.cfpb.gov/static-data/snapshot/2023/2023_public_lar_csv.zip",
        ),
        (
            "one_year",
            "transmittal_series",
            2024,
            "https://files.ffiec.cfpb.gov/static-data/one-year/2024/"
            "2024_public_ts_one_year_csv.zip",
        ),
        (
            "three_year",
            "msamd",
            2022,
            "https://files.ffiec.cfpb.gov/static-data/three-year/2022/"
            "2022_public_msamd_three_year_csv.zip",
        ),
        (
            "historic",
            "loans",
            2016,
            "https://files.consumerfinance.gov/hmda-historic-loan-data/"
            "hmda_2016_nationwide_all-records_codes.zip",
        ),
        (
            "historic",
            "transmittal_series",
            2016,
            "https://files.consumerfinance.gov/hmda-historic-institution-data/"
            "hmda_2016_transmittal_sheet.zip",
        ),
        (
            "mlar",
            "loans",
            2024,
            "https://files.ffiec.cfpb.gov/modified-lar/combined-mlar/2024/"
            "2024_combined_mlar_header.zip",
        ),
    ],
)
def test_build_url_matches_the_published_path_shapes(product, record_type, year, expected):
    assert build_url(product, record_type, year) == expected


def test_pipe_variant_is_named_txt_only_for_the_2017_snapshot():
    # CFPB renamed the delimiter token after 2017; every other year and product
    # calls the same file "_pipe".
    assert build_url("snapshot", "loans", 2017, "pipe").endswith("2017_public_lar_txt.zip")
    assert build_url("snapshot", "loans", 2018, "pipe").endswith("2018_public_lar_pipe.zip")
    assert build_url("three_year", "loans", 2017, "pipe").endswith(
        "2017_public_lar_three_year_pipe.zip"
    )


def test_historic_has_no_msamd_series():
    with pytest.raises(ValueError, match="No historic archive"):
        build_url("historic", "msamd", 2016)


@pytest.mark.parametrize(
    ("product", "last_year"),
    [("snapshot", 2023), ("one_year", 2022), ("three_year", 2020)],
)
def test_panel_stops_at_its_discontinued_year(product, last_year):
    # CFPB replaced the reporter panel with the transmittal sheet for later
    # releases. Generating past these years would 404 on every run.
    years = [f.year for f in discover_files([product], ["panel"], max_year=2030)]
    assert max(years) == last_year


def test_open_series_run_through_the_requested_ceiling():
    # An open series has no hardcoded end, so a newly published year is offered
    # up for probing with no code change.
    years = [f.year for f in discover_files(["snapshot"], ["loans"], max_year=2030)]
    assert years == list(range(2017, 2031))


def test_historic_is_closed_at_2017_regardless_of_ceiling():
    years = {f.year for f in discover_files(["historic"], max_year=2030)}
    assert years == set(range(2007, 2018))


def test_min_year_never_widens_a_series_past_its_start():
    # 2010 predates the one-year product entirely; asking for it must not
    # manufacture URLs for years CFPB never published.
    years = [f.year for f in discover_files(["one_year"], ["loans"], min_year=2010, max_year=2021)]
    assert years == [2019, 2020, 2021]


def test_default_products_exclude_mlar():
    products = {f.product for f in discover_files(max_year=2025)}
    assert "mlar" not in products
    assert products == {"snapshot", "one_year", "three_year", "historic"}


def test_destination_nests_under_the_record_type_subfolder():
    (snapshot_lar,) = [
        f for f in discover_files(["snapshot"], ["loans"], min_year=2023, max_year=2023)
    ]
    assert snapshot_lar.destination(Path("/raw")) == Path("/raw/loans/2023_public_lar_csv.zip")


def test_arid_lei_crosswalk_rides_along_with_the_2017_panel():
    files = discover_files(["snapshot"], ["panel"], min_year=2017, max_year=2018)
    extras = [f for f in files if f.subfolder == "misc"]
    assert [f.filename for f in extras] == ["arid2017_to_lei_xref_csv.zip"]
    # ...and not when 2017 is outside the requested range.
    later = discover_files(["snapshot"], ["panel"], min_year=2018, max_year=2020)
    assert [f for f in later if f.subfolder == "misc"] == []


def test_unknown_selectors_are_rejected():
    with pytest.raises(ValueError, match="Unknown HMDA products"):
        discover_files(["snapshit"])
    with pytest.raises(ValueError, match="Unknown HMDA record types"):
        discover_files(None, ["loan"])
