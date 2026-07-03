"""Unit test for ``hmda.utils.append_hmda_index`` (the canonical HMDAIndex format).

Every downstream HMDA joiner keys on this native string index, so the ``{year}{file_type}_``
prefix plus 9-digit zero-pad is a stable-contract regression seam: a width or format change
silently breaks all crosswalk joins.
"""

from __future__ import annotations

import polars as pl

from mortgage_data_manager.hmda.config import HMDA_INDEX_COLUMN
from mortgage_data_manager.hmda.utils import append_hmda_index


def test_append_hmda_index_formats_prefix_and_zero_pads():
    lf = pl.LazyFrame({HMDA_INDEX_COLUMN: [1, 42]})
    out = append_hmda_index(lf, 2018, "a").collect()[HMDA_INDEX_COLUMN].to_list()
    assert out == ["2018a_000000001", "2018a_000000042"]
