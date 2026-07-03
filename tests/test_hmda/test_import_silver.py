"""Unit test for pre-2007 geographic standardization (``hmda.import_silver``).

``_standardize_geographic_codes`` rebuilds zero-padded FIPS/GEOID strings from the pre-2007
numeric codes; the ``tract*100 -> round -> zfill(6)`` chain is exactly where a silent
digit-shift would corrupt 11-char census-tract joins (the geo-as-int leading-zero-loss class).
"""

from __future__ import annotations

import polars as pl

from mortgage_data_manager.hmda.import_silver import _standardize_geographic_codes


def test_standardize_geographic_codes_builds_padded_geoids():
    lf = pl.LazyFrame({"state_code": ["1"], "county_code": ["1"], "census_tract": ["9509.02"]})
    row = _standardize_geographic_codes(lf).collect().row(0, named=True)
    assert row["state_code"] == "01"
    assert row["county_code"] == "01001"
    assert row["census_tract"] == "01001950902"


def test_standardize_geographic_codes_passthrough_without_state_code():
    # Panel / transmittal frames carry no loan-level geography -> returned unchanged.
    lf = pl.LazyFrame({"respondent_state": ["CA"]})
    assert _standardize_geographic_codes(lf).collect().columns == ["respondent_state"]
