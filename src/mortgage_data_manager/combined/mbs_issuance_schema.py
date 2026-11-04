"""Plan tables for the legs of the combined ``mbs_issuance`` master.

Each leg maps one source feed to the security-at-issuance grain, one `LegField` per
output column in output order: the reader that builds it (see ``_mbs_readers`` in
`mortgage_data_manager.combined.mbs_issuance`) and the source column aliases, the first
present one winning. A column a feed does not carry has no source and so reads as its
typed null. ``derived`` columns need more than one source column or the file's month and
are built by the assembler under the same name.

GNMA canonical silver is snake_case, so its verbose names below resolve through the
canonical slug (parens dropped) when the verbose spelling is absent.
"""

from __future__ import annotations

from typing import Final

from mortgage_data_manager.combined._common import LegField as F

#: GSE ``IS`` / ``RIS`` (Fannie and Freddie share the layout). Dates are ``pl.Date``
#: in silver; amounts and rates are as published.
GSE_MBS_ISSUANCE_PLAN: Final[list[F]] = [
    F("agency", "derived"),
    F("pool_id", "col_str", ("Security Identifier",)),
    F("cusip", "col_str", ("CUSIP",)),
    F("prefix", "raw_str", ("Prefix",)),
    F("program", "derived"),
    F("pool_type_raw", "raw_str", ("Subtype",)),
    F("structure_class", "derived"),
    F("issue_type", "raw_str"),
    F("issue_date", "raw_date", ("Issue Date",)),
    F("maturity_date", "raw_date", ("Maturity Date",)),
    F("as_of_month", "derived"),
    F("loan_count", "int64", ("Loan Count",)),
    F("original_face", "raw_float", ("Issuance Investor Security UPB",)),
    F("current_face", "raw_float", ("Current Investor Security UPB",)),
    F("pool_upb", "raw_float"),
    F("security_factor", "raw_float", ("Security Factor",)),
    F("pass_through_rate", "raw_float", ("WA Net Interest Rate",)),
    F("wac", "raw_float", ("WA Issuance Interest Rate", "WA Current Interest Rate")),
    F("wa_net_rate", "raw_float", ("WA Net Accrual Interest Rate",)),
    F(
        "avg_loan_size",
        "raw_float",
        ("Average Origination Mortgage Loan Amount", "Average Mortgage Loan Amount"),
    ),
    F(
        "wa_loan_size",
        "raw_float",
        ("WA Origination Mortgage Loan Amount", "WA Mortgage Loan Amount"),
    ),
    F("wa_orig_loan_term", "raw_float", ("WA Origination Loan Term", "WA Loan Term")),
    F("wa_rem_term", "raw_float", ("WA Issuance Remaining Months to Maturity",)),
    F("wa_loan_age", "raw_float", ("WA Loan Age",)),
    F(
        "wa_ltv",
        "raw_float",
        ("WA Origination Loan-To-Value (LTV)", "WA Loan-To-Value (LTV)"),
    ),
    F(
        "wa_cltv",
        "raw_float",
        ("WA Origination Combined Loan-To-Value (CLTV)", "WA Combined Loan-To-Value (CLTV)"),
    ),
    F(
        "wa_dti",
        "raw_float",
        ("WA Origination Debt-To-Income (DTI)", "WA Debt-To-Income (DTI)"),
    ),
    F("wa_credit_score", "raw_float", ("WA Origination Classic FICO", "WA Classic FICO")),
    F("wa_vs4", "raw_float", ("WA Origination VS4", "WA VS4")),
    F("wa_margin", "num", ("WA Mortgage Margin",)),
    F("index_type", "str", ("Index",)),
    F("is_arm", "derived"),
    F("is_interest_only", "is_y", ("Interest Only Security Indicator",)),
    F(
        "tpo_upb_pct",
        "raw_float",
        (
            "Origination Third Party Origination UPB Percent",
            "Third Party Origination UPB Percent",
        ),
    ),
    F("issuer_id", "raw_str"),
    F("issuer_name", "raw_str", ("Issuer",)),
    F("seller_name", "raw_str", ("Seller Name",)),
    F("servicer_name", "raw_str", ("Servicer Name",)),
    # Booleanized on both sides: the GSE silver stores these as Boolean and GNMA as
    # Y/N strings, so leaving them to dtype supertyping persists "true"/"false" text
    # beside "Y"/"N".
    F("social_indicator", "yn", ("Social Indicator",)),
    F("green_indicator", "yn", ("Green Indicator",)),
    F("is_resecuritization", "derived"),
    F("record_source", "raw_str", ("_record_source",)),
    F("source_dataset", "derived"),
    F("issue_year", "derived"),
]

#: GNMA ``nimonSFPS`` PS: single-family base pools only, already decoded. Numbers are
#: stored as text, hence the ``ps_*`` readers.
GNMA_MBS_ISSUANCE_PLAN: Final[list[F]] = [
    F("agency", "const", value="GNMA"),
    F("pool_id", "str", ("Pool ID",)),
    F("cusip", "str", ("CUSIP Number",)),
    F("prefix", "raw_str"),
    F("program", "derived"),
    F("pool_type_raw", "str", ("Pool Type",)),
    F("structure_class", "const", value="pass_through"),
    F("issue_type", "derived"),
    F("issue_date", "ymd_text_date", ("Issue Date",)),
    F("maturity_date", "ymd_text_date", ("Maturity Date",)),
    F("as_of_month", "derived"),
    F("loan_count", "ps_int64", ("Number of loans in pool",)),
    F("original_face", "ps_num", ("Original Aggregate Amount",)),  # already USD (not /100)
    F("current_face", "ps_num", ("Remaining Security RPB",)),
    F("pool_upb", "ps_num", ("Pool UPB",)),
    F("security_factor", "ps_num", ("RPB Factor",)),
    F("pass_through_rate", "ps_num", ("Security Interest Rate",)),
    # The "at Issuance" twin is NOT read for the rate. In PS silver it is populated
    # only for 2020-01..2021-02 and there holds the SECURITY rate, not the WAC
    # (equal to `security_interest_rate` on 100% of rows in each of those 14
    # months); from 2021-03 it is 100% null. Coalescing it recorded
    # wac == pass_through_rate on 32,891 rows, a ~44-50bp understatement of the
    # gross note rate, so it is dropped rather than demoted to a fallback — as a
    # fallback it still injected the security rate on the 45 rows where the real
    # column is null. The WALA/WARM/WAOLT twins below are null in every month, so
    # their coalesce order is harmless and is left as published.
    F("wac", "ps_num", ("WA Interest Rate (WAC)",)),
    F("wa_net_rate", "raw_float"),
    F("avg_loan_size", "ps_num", ("Average Original Loan Size (AOLS)",)),
    F("wa_loan_size", "ps_num", ("WA Original Loan Size",)),
    F(
        "wa_orig_loan_term",
        "ps_num",
        ("WA Original Loan Term (WAOLT) at Issuance", "WA Original Loan Term (WAOLT)"),
    ),
    F(
        "wa_rem_term",
        "ps_num",
        (
            "WA Remaining Months to Maturity (WARM) at Issuance",
            "WA Remaining Months to Maturity (WARM)",
        ),
    ),
    F("wa_loan_age", "ps_num", ("WA Loan Age (WALA) at Issuance", "WA Loan Age (WALA)")),
    F("wa_ltv", "ps_num", ("WA Loan to Value (LTV)",)),
    F("wa_cltv", "ps_num", ("WA Combined Loan to Value (CLTV)",)),
    F("wa_dti", "derived"),  # GNMA PS DTI is a fraction
    F("wa_credit_score", "ps_num", ("WA Credit Score",)),
    F("wa_vs4", "raw_float"),
    F("wa_margin", "ps_num", ("WA Gross Margin (WAGM)",)),
    F("index_type", "raw_str"),
    F("is_arm", "derived"),
    F("is_interest_only", "bool"),
    F("tpo_upb_pct", "raw_float"),
    F("issuer_id", "str", ("Issuer Number",)),
    F("issuer_name", "raw_str", ("Issuer Name",)),
    F("seller_name", "raw_str"),
    F("servicer_name", "raw_str"),
    F("social_indicator", "yn", ("Social Indicator",)),
    F("green_indicator", "bool"),
    F("is_resecuritization", "const", value=False),
    F("record_source", "const", value="original"),  # GNMA has no reissue twin
    F("source_dataset", "const", value="nimonSFPS_PS"),
    F("issue_year", "derived"),
]

#: Legacy GNMA ``nissues`` D pool header (2012-02 -> 2021-04), already decoded except
#: the fixed-point 9-fills the ``nissues_*`` readers scrub. The credit score and DTI are
#: joined from the U record afterward (`NISSUES_U_PLAN`).
GNMA_LEGACY_MBS_ISSUANCE_PLAN: Final[list[F]] = [
    F("agency", "const", value="GNMA"),
    F("pool_id", "str", ("pool_number",)),
    F("cusip", "str", ("cusip_number",)),
    F("prefix", "raw_str"),
    F("program", "derived"),
    F("pool_type_raw", "str", ("pool_type",)),
    F("structure_class", "derived"),
    F("issue_type", "derived"),
    F("issue_date", "raw_date", ("pool_issue_date",)),
    F("maturity_date", "raw_date", ("pool_maturity_date",)),
    F("as_of_month", "derived"),
    F("loan_count", "derived"),  # 0 is the "not applicable" fill, and only Platinum has it
    F("original_face", "raw_float", ("original_aggregate_amount",)),
    F("current_face", "raw_float", ("original_aggregate_amount",)),  # factor 1.0 at issuance
    F("pool_upb", "derived"),
    F("security_factor", "const", value=1.0),
    F("pass_through_rate", "nissues_rate", ("security_interest_rate",)),
    F("wac", "nissues_rate", ("wac",)),
    F("wa_net_rate", "raw_float"),
    F("avg_loan_size", "nissues_size", ("average_original_loan_size",)),
    F("wa_loan_size", "nissues_size", ("weighted_average_original_loan_size",)),
    F("wa_orig_loan_term", "nissues_months", ("waolt",)),
    F("wa_rem_term", "nissues_months", ("warm",)),
    F("wa_loan_age", "nissues_months", ("wala",)),
    F("wa_ltv", "nissues_months", ("weighted_average_original_ltv",)),
    F("wa_cltv", "nissues_months", ("weighted_average_original_cltv",)),
    F("wa_vs4", "raw_float"),
    F("wa_margin", "nissues_rate", ("wagm",)),
    F("index_type", "raw_str"),  # lives in the N record (2017-12+)
    F("is_arm", "derived"),
    F("is_interest_only", "bool"),
    F("tpo_upb_pct", "raw_float"),
    F("issuer_id", "str", ("issuer_number",)),
    F("issuer_name", "str", ("issuer_name",)),
    F("seller_name", "raw_str"),
    F("servicer_name", "raw_str"),
    F("social_indicator", "bool"),  # GNMA social starts 2024-09
    F("green_indicator", "bool"),
    F("is_resecuritization", "derived"),
    F("record_source", "const", value="original"),  # GNMA has no reissue twin
    F("source_dataset", "const", value="nissues_D"),
    F("issue_year", "derived"),
]

#: Legacy ``nissues`` U record: the credit score and DTI the D record lacks, by pool.
NISSUES_U_PLAN: Final[list[F]] = [
    F("pool_id", "str", ("pool_number",)),
    F("wa_credit_score", "derived"),
    F("wa_dti", "derived"),  # U stores DTI as a fraction
]

#: GNMA Platinum first observations from ``factorAplat``; the WA block is joined from
#: ``platmonPPS`` afterward (`PLATINUM_WA_ISSUANCE_PLAN`).
GNMA_PLATINUM_MBS_ISSUANCE_PLAN: Final[list[F]] = [
    F("agency", "const", value="GNMA"),
    F("pool_id", "str", ("pool_id",)),
    F("cusip", "str", ("cusip_number",)),
    F("prefix", "raw_str"),
    F("program", "derived"),
    F("pool_type_raw", "str", ("pool_type",)),
    F("structure_class", "const", value="platinum"),
    F("issue_type", "derived"),
    F("issue_date", "raw_date", ("pool_issue_date",)),
    F("maturity_date", "raw_date", ("pool_maturity_date",)),
    F("as_of_month", "derived"),
    F("loan_count", "int64"),  # not disclosed for Platinum
    F("original_face", "gnma_num", ("original_aggregate_amount",)),
    F("current_face", "gnma_num", ("remaining_security_rpb",)),
    # Left as published. The only factors above 1 in the source (863 rows) are HMBS
    # Platinum, which this leg excludes.
    F("security_factor", "num", ("rpb_factor",)),
    F("pass_through_rate", "gnma_num", ("pool_interest_rate",)),
    F("wa_net_rate", "raw_float"),
    F("wa_vs4", "raw_float"),
    F("index_type", "raw_str"),
    F("is_arm", "derived"),
    F("is_interest_only", "bool"),
    F("tpo_upb_pct", "raw_float"),
    F("issuer_id", "str", ("issuer_number",)),
    F("issuer_name", "str", ("issuer_name",)),
    F("seller_name", "raw_str"),
    F("servicer_name", "raw_str"),
    F("social_indicator", "bool"),
    F("green_indicator", "bool"),
    F("is_resecuritization", "const", value=True),
    F("record_source", "const", value="original"),
    F("source_dataset", "const", value="factorAplat"),
    F("issue_year", "derived"),
]

#: ``platmonPPS`` PS: the Platinum WA block, keyed on pool and month. Its "at Issuance"
#: WA columns are unpopulated by design, so the current-month block is read, which is
#: the issuance block for a first observation.
PLATINUM_WA_ISSUANCE_PLAN: Final[list[F]] = [
    F("pool_id", "str", ("pool_id",)),
    F("as_of_month", "derived"),
    F("pool_upb", "num", ("pool_upb",)),
    F("avg_loan_size", "num", ("average_original_loan_size_weighted",)),
    F("wa_loan_size", "num", ("wa_original_loan_size",)),
    F("wac", "num", ("wa_interest_rate",)),
    F("wa_orig_loan_term", "num", ("wa_original_loan_term",)),
    F("wa_rem_term", "num", ("wa_remaining_months_to_maturity",)),
    F("wa_loan_age", "num", ("wa_loan_age",)),
    F("wa_ltv", "num", ("wa_loan_to_value",)),
    F("wa_cltv", "num", ("wa_combined_loan_to_value",)),
    F("wa_credit_score", "num", ("wa_credit_score",)),
    F("wa_dti", "derived"),  # a fraction in the source
    F("wa_margin", "num", ("wa_gross_margin",)),
]
