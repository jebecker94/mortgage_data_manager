"""Unit test for ``hmda.utils.append_hmda_index`` (the canonical HMDAIndex format).

Every downstream HMDA joiner keys on this native string index, so the ``{year}{file_type}_``
prefix plus 9-digit zero-pad is a stable-contract regression seam: a width or format change
silently breaks all crosswalk joins.
"""

from __future__ import annotations

import polars as pl

from mortgage_data_manager.sources.hmda.config import HMDA_INDEX_COLUMN
from mortgage_data_manager.sources.hmda.utils import append_hmda_index


def test_append_hmda_index_formats_prefix_and_zero_pads():
    lf = pl.LazyFrame({HMDA_INDEX_COLUMN: [1, 42]})
    out = append_hmda_index(lf, 2018, "a").collect()[HMDA_INDEX_COLUMN].to_list()
    assert out == ["2018a_000000001", "2018a_000000042"]


def test_respondent_key_normalizes_both_vintage_spellings():
    from mortgage_data_manager.sources.hmda.utils import respondent_key

    ids = ["0000013303", "13303", "63-1005978", "631005978", "0000000000", None]
    out = pl.DataFrame({"respondent_id": ids}).select(respondent_key())
    assert out["respondent_key"].to_list() == [
        "13303",
        "13303",
        "631005978",
        "631005978",
        None,
        None,
    ]


def test_number_of_borrowers_reads_co_applicant_sex_per_era():
    from mortgage_data_manager.sources.hmda.utils import number_of_borrowers

    sex = pl.DataFrame({"co_applicant_sex": [1, 2, 3, 4, 5, 6, None]})
    post = sex.select(number_of_borrowers("post2018"))["number_of_borrowers"].to_list()
    pre = sex.select(number_of_borrowers("period_2007_2017"))["number_of_borrowers"].to_list()
    assert post == [2, 2, 2, None, 1, 2, None]
    assert pre == [2, 2, 2, None, 1, None, None]  # 6 is not a pre-2018 code


def test_purchaser_type_label_tables_cover_each_era():
    from mortgage_data_manager.sources.hmda.utils import purchaser_type_label, purchaser_type_labels

    assert sorted(purchaser_type_labels("period_2007_2017")) == list(range(10))
    assert sorted(purchaser_type_labels("post2018")) == [0, 1, 2, 3, 4, 5, 6, 8, 9, 71, 72]
    codes = pl.DataFrame({"purchaser_type": [1, 71, 7, None]})
    labels = codes.select(purchaser_type_label("post2018"))["purchaser_type_label"].to_list()
    assert labels == [
        "Fannie Mae",
        "Credit union, mortgage company, or finance company",
        None,
        None,
    ]
