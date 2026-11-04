"""Declarative field plans for the combined ``loan_issuance`` master.

The GSE plans come first; the GNMA rows, a single source, have a plan table of their own
(`GNMA_LOAN_ISSUANCE_PLAN`).

Each GSE fuses two loan-level spines, bridged by its strictly 1:1 ``mbs_umbs``
crosswalk:

  * base   = the SF loan-level release (Fannie ``fnma/silver/issuances``, Freddie
             ``fhlmc/silver/origination``)
  * enrich = the UMBS ILLD at-issuance disclosure (``umbs/silver/{GSE}/ILLD``), plus
             the first monthly MLLD/FU snapshot for loans issued before 2019-06

Each plan entry maps one unified output field to its source column(s):

  name         unified output column name
  sf           list of SF source columns (vintage aliases OR-ed via coalesce)
  il           list of ILLD source columns (vintage aliases OR-ed via coalesce)
  disposition  one of:
                 key           handled explicitly by the builder
                 sf_only       pass through from SF
                 umbs_only     pass through from ILLD
                 shared_keep   in both, agree >=99.8% -> coalesce(SF, ILLD)
                 shared_harmon in both, code/encoding differs -> bespoke transform
                 drop          filler or constant at source -> not carried

Source columns that are never populated are not in the plans at all: they are
listed, with their aliases, in `PRUNED_ALWAYS_NULL_FIELDS` at the bottom, which a
data guard re-checks against current silver.
"""

from __future__ import annotations

from typing import Final, TypedDict

from mortgage_data_manager.combined._common import LegField


class FieldSpec(TypedDict):
    """One unified output field and the source column(s) it draws on."""

    name: str
    sf: list[str]
    il: list[str]
    disposition: str


#: The three primary loan dates parsed to ``pl.Date`` (first-of-month).
DATE_FIELDS: Final[frozenset[str]] = frozenset(
    {"first_payment_date", "maturity_date", "origination_date"}
)

#: Fields coalesced ILLD-first because ILLD is the higher-fidelity source.
#: - ``mi_percent``: ILLD is 100%-populated with explicit 0 = "no MI"; SF is null
#:   when there is no MI.
#: - ``seller_name`` / ``servicer_name`` / ``mi_cancellation_ind``: the SF disclosure
#:   VALUE-MASKS small entities as "Other" (only ~50-80 distinct sellers/servicers) while
#:   ILLD discloses the real names (~1,200-1,550) and rescues 100% of the masked rows
#:   (measured 2026-06-26). ILLD-first recovers the true
#:   names for 2019-06+ loans; pre-2019 history falls back to the (masked) SF value.
IL_FIRST_FIELDS: Final[frozenset[str]] = frozenset(
    {"mi_percent", "seller_name", "servicer_name", "mi_cancellation_ind"}
)

#: ILLD-only modification, step-rate and restated-origination fields, identical in both
#: GSE plans (the restated ``orig_*`` fields carry a reissued loan's original terms).
_ILLD_MODIFICATION_FIELDS: Final[list[FieldSpec]] = [
    {
        "name": "umbs_modification_program",
        "sf": [],
        "il": ["Modification Program"],
        "disposition": "umbs_only",
    },
    {
        "name": "umbs_modification_type",
        "sf": [],
        "il": ["Modification Type"],
        "disposition": "umbs_only",
    },
    {
        "name": "umbs_number_of_modifications",
        "sf": [],
        "il": ["Number of Modifications"],
        "disposition": "umbs_only",
    },
    {
        "name": "umbs_total_capitalized_amount",
        "sf": [],
        "il": ["Total Capitalized Amount"],
        "disposition": "umbs_only",
    },
    {
        "name": "umbs_original_deferred_amount",
        "sf": [],
        "il": ["Original Deferred Amount"],
        "disposition": "drop",  # always 0 at issuance
    },
    {
        "name": "umbs_current_deferred_upb",
        "sf": [],
        "il": ["Current Deferred UPB"],
        "disposition": "umbs_only",
    },
    {
        "name": "umbs_loan_age_as_of_mod",
        "sf": [],
        "il": ["Loan Age As Of Modification"],
        "disposition": "umbs_only",
    },
    {
        "name": "step_indicator",
        "sf": [],
        "il": ["Interest Rate Step Indicator"],
        "disposition": "umbs_only",
    },
    {
        "name": "step_initial_fixed_period",
        "sf": [],
        "il": ["Initial Step Fixed-Rate Period"],
        "disposition": "umbs_only",
    },
    {
        "name": "step_total_steps",
        "sf": [],
        "il": ["Total Number of Steps"],
        "disposition": "umbs_only",
    },
    {
        "name": "step_remaining_steps",
        "sf": [],
        "il": ["Number of Remaining Steps"],
        "disposition": "drop",  # always 0 at issuance
    },
    {
        "name": "step_terminal_rate",
        "sf": [],
        "il": ["Terminal Step Rate"],
        "disposition": "umbs_only",
    },
    {
        "name": "step_terminal_date",
        "sf": [],
        "il": ["Terminal Step Date"],
        "disposition": "umbs_only",
    },
    {
        "name": "step_periodic_cap_up",
        "sf": [],
        "il": ["Periodic Step Cap Up Percent"],
        "disposition": "umbs_only",
    },
    {
        "name": "orig_upb_restated",
        "sf": [],
        "il": ["Origination Mortgage Loan Amount"],
        "disposition": "umbs_only",
    },
    {
        "name": "orig_rate_restated",
        "sf": [],
        "il": ["Origination Interest Rate"],
        "disposition": "umbs_only",
    },
    {
        "name": "orig_amort_restated",
        "sf": [],
        "il": ["Origination Amortization Type"],
        "disposition": "umbs_only",
    },
    {
        "name": "orig_io_restated",
        "sf": [],
        "il": ["Origination Interest Only Loan Indicator"],
        "disposition": "umbs_only",
    },
    {
        "name": "orig_fpd_restated",
        "sf": [],
        "il": ["Origination First Payment Date"],
        "disposition": "umbs_only",
    },
    {
        "name": "orig_maturity_restated",
        "sf": [],
        "il": ["Origination Maturity Date"],
        "disposition": "umbs_only",
    },
    {
        "name": "orig_term_restated",
        "sf": [],
        "il": ["Origination Loan Term"],
        "disposition": "umbs_only",
    },
    {
        "name": "orig_ltv_restated",
        "sf": [],
        "il": ["Origination Loan-To-Value (LTV)"],
        "disposition": "umbs_only",
    },
    {
        "name": "orig_cltv_restated",
        "sf": [],
        "il": ["Origination Combined Loan-To-Value (CLTV)"],
        "disposition": "umbs_only",
    },
    {
        "name": "orig_dti_restated",
        "sf": [],
        "il": ["Origination Debt-To-Income Ratio"],
        "disposition": "umbs_only",
    },
    {
        "name": "orig_credit_score_restated",
        "sf": [],
        "il": ["Origination Credit Score"],
        "disposition": "umbs_only",
    },
    {
        "name": "orig_purpose_restated",
        "sf": [],
        "il": ["Origination Loan Purpose"],
        "disposition": "umbs_only",
    },
    {
        "name": "orig_occupancy_restated",
        "sf": [],
        "il": ["Origination Occupancy Status"],
        "disposition": "umbs_only",
    },
    {
        "name": "orig_channel_restated",
        "sf": [],
        "il": ["Origination Channel"],
        "disposition": "umbs_only",
    },
]

#: ARM cap, floor and rounding fields, identical in both GSE plans.
_ARM_CAP_FLOOR_FIELDS: Final[list[FieldSpec]] = [
    {
        "name": "arm_init_cap_down",
        "sf": [],
        "il": ["Initial Interest Rate Cap Down Percent"],
        "disposition": "umbs_only",
    },
    {
        "name": "arm_periodic_cap_down",
        "sf": [],
        "il": ["Periodic Interest Rate Cap Down Percent"],
        "disposition": "umbs_only",
    },
    {
        "name": "arm_lookback",
        "sf": [],
        "il": ["Interest Rate Lookback"],
        "disposition": "umbs_only",
    },
    {
        "name": "arm_rounding_method",
        "sf": [],
        "il": ["Interest Rate Rounding Method"],
        "disposition": "umbs_only",
    },
    {
        "name": "arm_rounding_pct",
        "sf": [],
        "il": ["Interest Rate Rounding Method Percent"],
        "disposition": "umbs_only",
    },
    {
        "name": "arm_convertibility",
        "sf": [],
        "il": ["Convertibility Indicator"],
        "disposition": "umbs_only",
    },
    {
        "name": "arm_life_ceiling",
        "sf": [],
        "il": ["Life Ceiling Interest Rate"],
        "disposition": "umbs_only",
    },
    {
        "name": "arm_life_ceiling_net",
        "sf": [],
        "il": ["Life Ceiling Net Interest Rate"],
        "disposition": "umbs_only",
    },
    {
        "name": "arm_life_floor",
        "sf": [],
        "il": ["Life Floor Interest Rate"],
        "disposition": "umbs_only",
    },
    {
        "name": "arm_life_floor_net",
        "sf": [],
        "il": ["Life Floor Net Interest Rate"],
        "disposition": "umbs_only",
    },
]

FNMA_LOAN_ISSUANCE_PLAN: Final[list[FieldSpec]] = [
    {"name": "loan_key_sf", "sf": ["Loan Identifier"], "il": [], "disposition": "key"},
    {"name": "loan_key_umbs", "sf": [], "il": ["Loan Identifier"], "disposition": "key"},
    {"name": "has_umbs_match", "sf": [], "il": [], "disposition": "key"},
    {
        "name": "umbs_correction_ind",
        "sf": [],
        "il": ["Loan Correction Indicator"],
        "disposition": "umbs_only",
    },
    {"name": "umbs_source_record", "sf": [], "il": ["_record_source"], "disposition": "umbs_only"},
    {
        "name": "sf_reporting_period",
        "sf": ["Monthly Reporting Period"],
        "il": [],
        "disposition": "sf_only",
    },
    {"name": "sf_silver_is_issuance", "sf": ["is_issuance"], "il": [], "disposition": "drop"},
    {"name": "sf_silver_is_termination", "sf": ["is_termination"], "il": [], "disposition": "drop"},
    {"name": "cusip", "sf": [], "il": ["CUSIP"], "disposition": "umbs_only"},
    {"name": "security_id", "sf": [], "il": ["Security Identifier"], "disposition": "umbs_only"},
    {"name": "pool_prefix", "sf": [], "il": ["Prefix"], "disposition": "umbs_only"},
    {
        "name": "loan_participation_pct",
        "sf": [],
        "il": ["Loan Participation Percent"],
        "disposition": "umbs_only",
    },
    {
        "name": "seller_name",
        "sf": ["Seller Name"],
        "il": ["Seller Name"],
        "disposition": "shared_keep",
    },
    {
        "name": "servicer_name",
        "sf": ["Servicer Name"],
        "il": ["Servicer Name"],
        "disposition": "shared_keep",
    },
    {"name": "seller_city", "sf": [], "il": ["Seller City"], "disposition": "umbs_only"},
    {"name": "seller_state", "sf": [], "il": ["Seller State"], "disposition": "umbs_only"},
    {"name": "servicer_city", "sf": [], "il": ["Servicer City"], "disposition": "umbs_only"},
    {"name": "servicer_state", "sf": [], "il": ["Servicer State"], "disposition": "umbs_only"},
    {
        "name": "original_upb",
        "sf": ["Original UPB"],
        "il": ["Mortgage Loan Amount"],
        "disposition": "shared_keep",
    },
    {
        # Was ``shared_both`` (SF full-loan UPB alongside the ILLD participation-weighted
        # UPB), but SF ``UPB at Issuance`` is 100% null on issuance records (2026-07-09
        # audit) so the SF twin ``issuance_full_upb`` was pruned; ILLD is the only source.
        "name": "issuance_investor_upb",
        "sf": [],
        "il": ["Issuance Investor Loan UPB"],
        "disposition": "umbs_only",
    },
    {
        "name": "current_actual_upb",
        "sf": ["Current Actual UPB"],
        "il": [],
        "disposition": "sf_only",
    },
    {
        "name": "current_investor_upb",
        "sf": [],
        "il": ["Current Investor Loan UPB"],
        "disposition": "umbs_only",
    },
    {
        "name": "interest_bearing_upb",
        "sf": ["Interest Bearing UPB"],
        "il": ["Interest Bearing Mortgage Loan Amount"],
        "disposition": "shared_keep",
    },
    {
        "name": "note_rate",
        "sf": ["Original Interest Rate"],
        "il": ["Original Interest Rate"],
        "disposition": "shared_keep",
    },
    {
        "name": "issuance_rate",
        "sf": [],
        "il": ["Issuance Interest Rate"],
        "disposition": "umbs_only",
    },
    {
        "name": "net_rate",
        "sf": [],
        "il": ["Issuance Net Interest Rate"],
        "disposition": "umbs_only",
    },
    {
        "name": "current_rate",
        "sf": ["Current Interest Rate"],
        "il": ["Current Interest Rate"],
        "disposition": "shared_keep",
    },
    {
        "name": "current_net_rate",
        "sf": [],
        "il": ["Current Net Interest Rate"],
        "disposition": "umbs_only",
    },
    {
        "name": "loan_term",
        "sf": ["Original Loan Term"],
        "il": ["Loan Term"],
        "disposition": "shared_keep",
    },
    {"name": "loan_age", "sf": ["Loan Age"], "il": ["Loan Age"], "disposition": "shared_keep"},
    {
        "name": "remaining_months_to_maturity",
        "sf": ["Remaining Months To Maturity"],
        "il": ["Remaining Months to Maturity"],
        "disposition": "shared_keep",
    },
    {
        "name": "remaining_months_legal_maturity",
        "sf": ["Remaining Months to Legal Maturity"],
        "il": [],
        "disposition": "sf_only",
    },
    {"name": "origination_date", "sf": ["Origination Date"], "il": [], "disposition": "sf_only"},
    {
        "name": "first_payment_date",
        "sf": ["First Payment Date"],
        "il": ["First Payment Date"],
        "disposition": "shared_harmon",
    },
    {
        "name": "maturity_date",
        "sf": ["Maturity Date"],
        "il": ["Maturity Date"],
        "disposition": "shared_harmon",
    },
    {
        "name": "next_rate_adjustment_date",
        "sf": ["Next Interest Rate Adjustment Date"],
        "il": ["Next Interest Rate Adjustment Date"],
        "disposition": "shared_keep",
    },
    {
        "name": "next_payment_change_date",
        "sf": ["Next Payment Change Date"],
        "il": [],
        "disposition": "sf_only",
    },
    {
        "name": "months_to_next_rate_adjustment",
        "sf": [],
        "il": ["Months to Next Interest Rate Adjustment Date"],
        "disposition": "umbs_only",
    },
    {
        "name": "ltv",
        "sf": ["Original Loan to Value Ratio (LTV)"],
        "il": ["Loan-To-Value (LTV)"],
        "disposition": "shared_keep",
    },
    {
        "name": "cltv",
        "sf": ["Original Combined Loan to Value Ratio (CLTV)"],
        "il": ["Combined Loan-To-Value (CLTV)"],
        "disposition": "shared_keep",
    },
    {
        "name": "dti",
        "sf": ["Debt-To-Income (DTI)"],
        "il": ["Debt-To-Income (DTI)"],
        "disposition": "shared_keep",
    },
    {
        "name": "estimated_ltv",
        "sf": [],
        "il": ["Estimated Loan-To-Value (ELTV)"],
        "disposition": "umbs_only",
    },
    {
        "name": "high_balance_ind",
        "sf": ["High Balance Loan Indicator "],
        "il": [],
        "disposition": "sf_only",
    },
    {
        "name": "hltv_refi_option_ind",
        "sf": ["High Loan to Value (HLTV) Refinance Option Indicator"],
        "il": [],
        "disposition": "sf_only",
    },
    {
        "name": "borrower_credit_score",
        "sf": ["Borrower Credit Score at Origination", "Origination Classic FICO®"],
        "il": [],
        "disposition": "sf_only",
    },
    {
        "name": "co_borrower_credit_score",
        "sf": ["Co-Borrower Credit Score at Origination"],
        "il": [],
        "disposition": "sf_only",
    },
    {
        "name": "representative_credit_score",
        "sf": [],
        "il": ["Borrower Credit Score", "Classic FICO", "Origination Classic FICO"],
        "disposition": "shared_harmon",
    },
    {
        "name": "credit_score_issuance",
        "sf": ["Borrower Credit Score At Issuance", "Issuance Classic FICO®"],
        "il": [],
        "disposition": "drop",
    },
    {
        "name": "co_borrower_credit_score_issuance",
        "sf": ["Co-Borrower Credit Score At Issuance"],
        "il": [],
        "disposition": "drop",
    },
    {
        "name": "updated_credit_score",
        "sf": [],
        "il": ["Updated Credit Score", "Updated Classic FICO"],
        "disposition": "umbs_only",
    },
    {"name": "vs4_score", "sf": [], "il": ["VS4", "Origination VS4"], "disposition": "umbs_only"},
    {
        "name": "number_of_borrowers",
        "sf": ["Number of Borrowers"],
        "il": ["Number of Borrowers"],
        "disposition": "shared_keep",
    },
    {
        "name": "number_of_units",
        "sf": ["Number of Units"],
        "il": ["Number of Units"],
        "disposition": "shared_keep",
    },
    {
        "name": "first_time_homebuyer",
        "sf": ["First Time Home Buyer Indicator"],
        "il": ["First Time Home Buyer Indicator"],
        "disposition": "shared_keep",
    },
    {
        "name": "loan_purpose",
        "sf": ["Loan Purpose "],
        "il": ["Loan Purpose"],
        "disposition": "shared_harmon",
    },
    {
        "name": "occupancy",
        "sf": ["Occupancy Status"],
        "il": ["Occupancy Status"],
        "disposition": "shared_keep",
    },
    {
        "name": "property_type",
        "sf": ["Property Type"],
        "il": ["Property Type"],
        "disposition": "shared_keep",
    },
    {"name": "channel", "sf": ["Channel"], "il": ["Channel"], "disposition": "shared_keep"},
    {
        "name": "amortization_type",
        "sf": ["Amortization Type"],
        "il": ["Amortization Type"],
        "disposition": "shared_keep",
    },
    {
        "name": "property_state",
        "sf": ["Property State"],
        "il": ["Property State"],
        "disposition": "shared_keep",
    },
    {
        "name": "property_valuation_method",
        "sf": ["Property Valuation Method "],
        "il": ["Property Valuation Method"],
        "disposition": "shared_keep",
    },
    {
        "name": "relocation_mortgage_ind",
        "sf": ["Relocation Mortgage Indicator"],
        "il": [],
        "disposition": "sf_only",
    },
    {
        "name": "negative_amortization_ind",
        "sf": ["Negative Amortization Indicator"],
        "il": [],
        "disposition": "sf_only",
    },
    {
        "name": "non_standard_documentation_ind",
        "sf": ["Non-Standard Documentation Indicator"],
        "il": [],
        "disposition": "sf_only",
    },
    {
        "name": "non_standard_underwriting_ind",
        "sf": ["Non-Standard Underwriting or Eligibility Indicator"],
        "il": [],
        "disposition": "sf_only",
    },
    {
        "name": "msa",
        "sf": [
            "Metropolitan Statistical Area (MSA) or Metropolitan Statistical Division Area (MSDA)",
            "Metropolitan Statistical Area (MSA)",
        ],
        "il": [],
        "disposition": "sf_only",
    },
    {"name": "zip3", "sf": ["Zip Code Short"], "il": [], "disposition": "sf_only"},
    {
        "name": "mi_percent",
        "sf": ["Mortgage Insurance Percentage"],
        "il": ["Mortgage Insurance Percent"],
        "disposition": "shared_harmon",
    },
    {
        "name": "mi_cancellation_ind",
        "sf": ["Mortgage Insurance Cancellation Indicator"],
        "il": ["Mortgage Insurance Cancellation Indicator"],
        "disposition": "shared_keep",
    },
    {"name": "mi_type", "sf": ["Mortgage Insurance Type"], "il": [], "disposition": "sf_only"},
    {
        "name": "government_insured_guarantee",
        "sf": ["Government Insured/Guarantee Indicator"],
        "il": ["Government Insured Guarantee"],
        "disposition": "shared_keep",
    },
    {
        "name": "interest_only_ind",
        "sf": ["Interest Only Loan Indicator"],
        "il": ["Interest Only Loan Indicator"],
        "disposition": "shared_keep",
    },
    {
        "name": "io_first_pi_date",
        "sf": ["Interest Only First Principal And Interest Payment Date"],
        "il": ["Interest Only First Principal and Interest Payment Date"],
        "disposition": "shared_keep",
    },
    {
        "name": "months_to_amortization",
        "sf": ["Months to Amortization"],
        "il": ["Months to Amortization"],
        "disposition": "shared_keep",
    },
    {
        "name": "prepayment_penalty_ind",
        "sf": ["Prepayment Penalty Indicator"],
        "il": ["Prepayment Penalty Indicator"],
        "disposition": "shared_keep",
    },
    {
        "name": "prepayment_penalty_term",
        "sf": [],
        "il": ["Prepayment Penalty Total Term"],
        "disposition": "umbs_only",
    },
    {
        "name": "assumability_ind",
        "sf": [],
        "il": ["Assumability Indicator"],
        "disposition": "umbs_only",
    },
    {
        "name": "special_eligibility_program",
        "sf": ["Special Eligibility Program"],
        "il": ["Special Eligibility Program"],
        "disposition": "shared_keep",
    },
    {
        "name": "servicing_activity_ind",
        "sf": ["Servicing Activity Indicator"],
        "il": [],
        "disposition": "drop",  # always N (false) on the issuance row
    },
    {"name": "arm_index", "sf": ["Index"], "il": ["Index"], "disposition": "shared_keep"},
    {
        "name": "arm_margin",
        "sf": ["Mortgage Margin"],
        "il": ["Mortgage Margin"],
        "disposition": "shared_keep",
    },
    {"name": "arm_pc_margin", "sf": [], "il": ["MBS PC Margin"], "disposition": "umbs_only"},
    {
        "name": "arm_adj_frequency",
        "sf": ["Interest Rate Adjustment Frequency"],
        "il": ["Interest Rate Adjustment Frequency"],
        "disposition": "shared_keep",
    },
    {
        "name": "arm_initial_fixed_period",
        "sf": ["Initial Fixed-Rate Period "],
        "il": ["Initial Fixed Rate Period"],
        "disposition": "shared_keep",
    },
    {
        "name": "arm_init_cap_up",
        "sf": ["Initial Interest Rate Cap Up Percent"],
        "il": ["Initial Interest Rate Cap Up Percent"],
        "disposition": "shared_keep",
    },
    {
        "name": "arm_periodic_cap_up",
        "sf": ["Periodic Interest Rate Cap Up Percent"],
        "il": ["Periodic Interest Rate Cap Up Percent"],
        "disposition": "shared_keep",
    },
    {"name": "arm_product_type", "sf": ["ARM Product Type"], "il": [], "disposition": "sf_only"},
    {
        "name": "arm_initial_fixed_le5yr_ind",
        "sf": ["ARM Initial Fixed-Rate Period  ≤ 5 YR Indicator"],
        "il": [],
        "disposition": "sf_only",
    },
    *_ARM_CAP_FLOOR_FIELDS,
    {
        "name": "modification_flag",
        "sf": ["Modification Flag"],
        "il": [],
        "disposition": "drop",  # always N (false) on the issuance row
    },
    {
        "name": "borrower_assistance_plan",
        "sf": ["Borrower Assistance Plan"],
        "il": ["Borrower Assistance Plan"],
        "disposition": "shared_keep",
    },
    {
        "name": "alternative_delinquency_resolution",
        "sf": ["Alternative Delinquency Resolution"],
        "il": ["Alternative Delinquency Resolution"],
        "disposition": "drop",  # always 7 (not applicable) on the issuance row
    },
    {
        "name": "payment_deferral_mod_event_ind",
        "sf": ["Payment Deferral Modification Event Indicator"],
        "il": [],
        "disposition": "drop",  # always 7 (not applicable) on the issuance row
    },
    *_ILLD_MODIFICATION_FIELDS,
    {
        "name": "delinquency_status",
        "sf": ["Current Loan Delinquency Status"],
        "il": ["Days Delinquent"],
        "disposition": "shared_harmon",
    },
    {
        "name": "loan_payment_history",
        "sf": ["Loan Payment History"],
        "il": ["Loan Performance History"],
        "disposition": "shared_harmon",
    },
    {
        "name": "_filler",
        "sf": [],
        "il": [
            "Filler",
            "Filler_duplicated_0",
            "Filler_duplicated_1",
            "Filler_duplicated_2",
            "Filler_duplicated_3",
            "Filler_duplicated_4",
            "Filler_duplicated_5",
        ],
        "disposition": "drop",
    },
]


FHLMC_LOAN_ISSUANCE_PLAN: Final[list[FieldSpec]] = [
    {"name": "loan_key_sf", "sf": ["Loan Sequence Number"], "il": [], "disposition": "key"},
    {"name": "loan_key_umbs", "sf": [], "il": ["Loan Identifier"], "disposition": "key"},
    {"name": "has_umbs_match", "sf": [], "il": [], "disposition": "key"},
    {
        "name": "umbs_correction_ind",
        "sf": [],
        "il": ["Loan Correction Indicator"],
        "disposition": "umbs_only",
    },
    {"name": "umbs_source_record", "sf": [], "il": ["_record_source"], "disposition": "umbs_only"},
    {"name": "sf_silver_is_issuance", "sf": ["is_issuance"], "il": [], "disposition": "drop"},
    {"name": "sf_silver_is_termination", "sf": ["is_termination"], "il": [], "disposition": "drop"},
    {"name": "cusip", "sf": [], "il": ["CUSIP"], "disposition": "umbs_only"},
    {"name": "security_id", "sf": [], "il": ["Security Identifier"], "disposition": "umbs_only"},
    {"name": "pool_prefix", "sf": [], "il": ["Prefix"], "disposition": "umbs_only"},
    {
        "name": "loan_participation_pct",
        "sf": [],
        "il": ["Loan Participation Percent"],
        "disposition": "umbs_only",
    },
    {
        "name": "seller_name",
        "sf": ["Seller Name"],
        "il": ["Seller Name"],
        "disposition": "shared_keep",
    },
    {
        "name": "servicer_name",
        "sf": ["Servicer Name"],
        "il": ["Servicer Name"],
        "disposition": "shared_keep",
    },
    {"name": "seller_city", "sf": [], "il": ["Seller City"], "disposition": "umbs_only"},
    {"name": "seller_state", "sf": [], "il": ["Seller State"], "disposition": "umbs_only"},
    {"name": "servicer_city", "sf": [], "il": ["Servicer City"], "disposition": "umbs_only"},
    {"name": "servicer_state", "sf": [], "il": ["Servicer State"], "disposition": "umbs_only"},
    {
        "name": "original_upb",
        "sf": ["Original UPB"],
        "il": ["Mortgage Loan Amount"],
        "disposition": "shared_keep",
    },
    {
        "name": "issuance_investor_upb",
        "sf": [],
        "il": ["Issuance Investor Loan UPB"],
        "disposition": "umbs_only",
    },
    {
        "name": "current_investor_upb",
        "sf": [],
        "il": ["Current Investor Loan UPB"],
        "disposition": "umbs_only",
    },
    {
        "name": "interest_bearing_upb",
        "sf": [],
        "il": ["Interest Bearing Mortgage Loan Amount"],
        "disposition": "umbs_only",
    },
    {
        "name": "note_rate",
        "sf": ["Original Interest Rate"],
        "il": ["Original Interest Rate"],
        "disposition": "shared_keep",
    },
    {
        "name": "issuance_rate",
        "sf": [],
        "il": ["Issuance Interest Rate"],
        "disposition": "umbs_only",
    },
    {
        "name": "net_rate",
        "sf": [],
        "il": ["Issuance Net Interest Rate"],
        "disposition": "umbs_only",
    },
    {"name": "current_rate", "sf": [], "il": ["Current Interest Rate"], "disposition": "umbs_only"},
    {
        "name": "current_net_rate",
        "sf": [],
        "il": ["Current Net Interest Rate"],
        "disposition": "umbs_only",
    },
    {
        "name": "loan_term",
        "sf": ["Original Loan Term"],
        "il": ["Loan Term"],
        "disposition": "shared_keep",
    },
    {"name": "loan_age", "sf": [], "il": ["Loan Age"], "disposition": "umbs_only"},
    {
        "name": "remaining_months_to_maturity",
        "sf": [],
        "il": ["Remaining Months to Maturity"],
        "disposition": "umbs_only",
    },
    {
        "name": "first_payment_date",
        "sf": ["First Payment Date"],
        "il": ["First Payment Date"],
        "disposition": "shared_harmon",
    },
    {
        "name": "maturity_date",
        "sf": ["Maturity Date"],
        "il": ["Maturity Date"],
        "disposition": "shared_harmon",
    },
    {
        "name": "next_rate_adjustment_date",
        "sf": [],
        "il": ["Next Interest Rate Adjustment Date"],
        "disposition": "umbs_only",
    },
    {
        "name": "months_to_next_rate_adjustment",
        "sf": [],
        "il": ["Months to Next Interest Rate Adjustment Date"],
        "disposition": "umbs_only",
    },
    {
        "name": "ltv",
        "sf": ["Original Loan-to-Value (LTV)"],
        "il": ["Loan-To-Value (LTV)"],
        "disposition": "shared_keep",
    },
    {
        "name": "cltv",
        "sf": ["Original Combined Loan-to-Value (CLTV)"],
        "il": ["Combined Loan-To-Value (CLTV)"],
        "disposition": "shared_keep",
    },
    {
        "name": "dti",
        "sf": ["Original Debt-to-Income (DTI) Ratio"],
        "il": ["Debt-To-Income (DTI)"],
        "disposition": "shared_keep",
    },
    {
        "name": "estimated_ltv",
        "sf": [],
        "il": ["Estimated Loan-To-Value (ELTV)"],
        "disposition": "umbs_only",
    },
    {
        "name": "representative_credit_score",
        "sf": ["Credit Score"],
        "il": ["Borrower Credit Score", "Classic FICO", "Origination Classic FICO"],
        "disposition": "shared_keep",
    },
    {
        "name": "credit_score_issuance",
        "sf": ["Borrower Credit Score At Issuance", "Issuance Classic FICO®"],
        "il": [],
        "disposition": "drop",
    },
    {
        "name": "co_borrower_credit_score_issuance",
        "sf": ["Co-Borrower Credit Score At Issuance"],
        "il": [],
        "disposition": "drop",
    },
    {
        "name": "updated_credit_score",
        "sf": [],
        "il": ["Updated Credit Score", "Updated Classic FICO"],
        "disposition": "umbs_only",
    },
    {"name": "vs4_score", "sf": [], "il": ["VS4", "Origination VS4"], "disposition": "umbs_only"},
    {
        "name": "number_of_borrowers",
        "sf": ["Number of Borrowers"],
        "il": ["Number of Borrowers"],
        "disposition": "shared_keep",
    },
    {
        "name": "number_of_units",
        "sf": ["Number of Units"],
        "il": ["Number of Units"],
        "disposition": "shared_keep",
    },
    {
        "name": "first_time_homebuyer",
        "sf": ["First Time Homebuyer Flag"],
        "il": ["First Time Home Buyer Indicator"],
        "disposition": "shared_keep",
    },
    {
        "name": "loan_purpose",
        "sf": ["Loan Purpose"],
        "il": ["Loan Purpose"],
        "disposition": "shared_harmon",
    },
    {
        "name": "occupancy",
        "sf": ["Occupancy Status"],
        "il": ["Occupancy Status"],
        "disposition": "shared_keep",
    },
    {
        "name": "property_type",
        "sf": ["Property Type"],
        "il": ["Property Type"],
        "disposition": "shared_keep",
    },
    {"name": "channel", "sf": ["Channel"], "il": ["Channel"], "disposition": "shared_keep"},
    {
        "name": "amortization_type",
        "sf": ["Amortization Type (Formerly Product Type)"],
        "il": ["Amortization Type"],
        "disposition": "shared_keep",
    },
    {
        "name": "property_state",
        "sf": ["Property State"],
        "il": ["Property State"],
        "disposition": "shared_keep",
    },
    {
        "name": "property_valuation_method",
        "sf": ["Property Valuation Method"],
        "il": ["Property Valuation Method"],
        "disposition": "shared_keep",
    },
    {
        "name": "msa",
        "sf": ["Metropolitan Statistical Area (MSA) Or Metropolitan Division"],
        "il": [],
        "disposition": "sf_only",
    },
    {"name": "zip3", "sf": ["Postal Code"], "il": [], "disposition": "sf_only"},
    {
        "name": "mi_percent",
        "sf": ["Mortgage Insurance Percentage (MI %)"],
        "il": ["Mortgage Insurance Percent"],
        "disposition": "shared_harmon",
    },
    {
        "name": "mi_cancellation_ind",
        "sf": ["Mortgage Insurance Cancellation Indicator"],
        "il": ["Mortgage Insurance Cancellation Indicator"],
        "disposition": "shared_keep",
    },
    {
        "name": "government_insured_guarantee",
        "sf": [],
        "il": ["Government Insured Guarantee"],
        "disposition": "umbs_only",
    },
    {
        "name": "interest_only_ind",
        "sf": ["Interest Only (I/O) Indicator"],
        "il": ["Interest Only Loan Indicator"],
        "disposition": "shared_keep",
    },
    {
        "name": "io_first_pi_date",
        "sf": [],
        "il": ["Interest Only First Principal and Interest Payment Date"],
        "disposition": "umbs_only",
    },
    {
        "name": "months_to_amortization",
        "sf": [],
        "il": ["Months to Amortization"],
        "disposition": "umbs_only",
    },
    {
        "name": "prepayment_penalty_ind",
        "sf": ["Prepayment Penalty Mortgage (PPM) Flag"],
        "il": ["Prepayment Penalty Indicator"],
        "disposition": "shared_keep",
    },
    {
        "name": "prepayment_penalty_term",
        "sf": [],
        "il": ["Prepayment Penalty Total Term"],
        "disposition": "umbs_only",
    },
    {
        "name": "assumability_ind",
        "sf": [],
        "il": ["Assumability Indicator"],
        "disposition": "umbs_only",
    },
    {
        "name": "special_eligibility_program",
        "sf": ["Program Indicator"],
        "il": ["Special Eligibility Program"],
        "disposition": "shared_keep",
    },
    {"name": "arm_index", "sf": [], "il": ["Index"], "disposition": "umbs_only"},
    {"name": "arm_margin", "sf": [], "il": ["Mortgage Margin"], "disposition": "umbs_only"},
    {"name": "arm_pc_margin", "sf": [], "il": ["MBS PC Margin"], "disposition": "umbs_only"},
    {
        "name": "arm_adj_frequency",
        "sf": [],
        "il": ["Interest Rate Adjustment Frequency"],
        "disposition": "umbs_only",
    },
    {
        "name": "arm_initial_fixed_period",
        "sf": [],
        "il": ["Initial Fixed Rate Period"],
        "disposition": "umbs_only",
    },
    {
        "name": "arm_init_cap_up",
        "sf": [],
        "il": ["Initial Interest Rate Cap Up Percent"],
        "disposition": "umbs_only",
    },
    {
        "name": "arm_periodic_cap_up",
        "sf": [],
        "il": ["Periodic Interest Rate Cap Up Percent"],
        "disposition": "umbs_only",
    },
    *_ARM_CAP_FLOOR_FIELDS,
    {
        "name": "borrower_assistance_plan",
        "sf": [],
        "il": ["Borrower Assistance Plan"],
        "disposition": "umbs_only",
    },
    {
        "name": "alternative_delinquency_resolution",
        "sf": [],
        "il": ["Alternative Delinquency Resolution"],
        "disposition": "drop",  # always 7 (not applicable) on the issuance row
    },
    *_ILLD_MODIFICATION_FIELDS,
    {"name": "delinquency_status", "sf": [], "il": ["Days Delinquent"], "disposition": "umbs_only"},
    {
        "name": "loan_payment_history",
        "sf": [],
        "il": ["Loan Performance History"],
        "disposition": "umbs_only",
    },
    {
        "name": "_filler",
        "sf": [],
        "il": [
            "Filler",
            "Filler_duplicated_0",
            "Filler_duplicated_1",
            "Filler_duplicated_2",
            "Filler_duplicated_3",
            "Filler_duplicated_4",
            "Filler_duplicated_5",
        ],
        "disposition": "drop",
    },
    {
        "name": "super_conforming_ind",
        "sf": ["Super Conforming Flag"],
        "il": [],
        "disposition": "sf_only",
    },
    {
        "name": "preharp_loan_sequence_number",
        "sf": ["Pre-HARP Loan Sequence Number"],
        "il": [],
        "disposition": "sf_only",
    },
    {"name": "harp_ind", "sf": ["HARP Indicator"], "il": [], "disposition": "sf_only"},
]


#: GNMA rows: one ``dailyllmni`` L file per issuance month, a single source, so the plan
#: is a `LegField` table (see `mortgage_data_manager.combined._common`) rather than a
#: two-spine one. Sources are canonical silver columns; readers are ``_gnma_loan_readers``
#: plus the issuance decodes in `mortgage_data_manager.combined.loan_issuance`. Pool-level
#: ``cusip`` / ``program`` / ``pool_type`` are joined afterward from the P record.
GNMA_LOAN_ISSUANCE_PLAN: Final[list[LegField]] = [
    # loan_key = Disclosure Sequence Number alone: verified globally unique (across
    # Ginnie I/II, no cross-pool/cross-program collisions), stable month-to-month, and
    # identical between dailyllmni issuance and llmon performance (99.96%), so the old
    # Pool-ID prefix was redundant. pool_id is retained as its own column.
    LegField("loan_key", "raw", ("disclosure_sequence_number",)),
    LegField("loan_key_kind", "const", value="gnma_seq"),
    LegField("agency", "const", value="GNMA"),
    LegField("source_dataset", "const", value="gnma_dailyllmni"),
    LegField("issuance_month", "derived"),
    LegField("issuance_year", "derived"),
    LegField("pool_id", "raw", ("pool_id",)),
    LegField("issuer_id", "str", ("issuer_id",)),
    # ``Seller Issuer ID`` (V1.6+) is deliberately NOT emitted: GNMA publishes the slot
    # but it is blank in every dailyllmni month (2026-07-09 audit; the guard test on
    # PRUNED_ALWAYS_NULL_FIELDS alerts if GNMA ever starts populating it).
    LegField("first_payment_date", "date", ("first_payment_date",)),
    LegField("maturity_date", "date", ("maturity_date_of_loan",)),
    LegField("origination_date", "date", ("loan_origination_date",)),
    LegField("orig_quarter", "derived"),
    LegField("original_upb", "gnma_num", ("original_principal_balance",)),
    LegField("issuance_investor_upb", "gnma_num", ("upb_at_issuance",)),
    LegField("loan_term", "int", ("original_loan_term_in_months",)),
    LegField("loan_age", "int", ("loan_age_in_months",)),
    LegField("remaining_months_to_maturity", "int", ("remaining_loan_term_in_months",)),
    LegField("note_rate", "gnma_num", ("loan_interest_rate",)),
    LegField("ltv", "ratio", ("loan_to_value",)),
    LegField("cltv", "ratio", ("combined_ltv",)),
    LegField("dti", "ratio", ("total_debt_expense_ratio_percent",)),
    LegField("representative_credit_score", "fico", ("credit_score",)),
    LegField("number_of_borrowers", "int", ("number_of_borrowers",)),
    LegField("number_of_units", "int", ("property_type",)),  # Ginnie's property type is a count
    LegField("loan_purpose", "derived"),
    LegField("loan_purpose_raw", "str", ("loan_purpose",)),
    LegField("is_modified_reissue", "derived"),
    LegField("refi_type", "str", ("refinance_type",)),
    LegField("channel", "channel", ("third_party_origination_type",)),
    LegField("channel_raw", "str", ("third_party_origination_type",)),
    LegField("amortization_type", "derived"),
    LegField("first_time_homebuyer", "yn", ("first_time_home_buyer",)),
    LegField("property_state", "str", ("state",)),
    LegField("government_insurer", "insurer", ("agency",)),
    LegField("gov_upfront_mip_rate", "gnma_num", ("upfront_mip",)),
    LegField("gov_annual_mip_rate", "gnma_num", ("annual_mip",)),
    LegField("down_payment_assistance", "str", ("down_payment_assistance",)),
    LegField("buydown", "str", ("buy_down_status",)),
    LegField("arm_index", "arm_index", ("index_type",)),
]


class PrunedField(TypedDict):
    """One pruned output field and the never-populated source column(s) behind it."""

    name: str  # unified column formerly emitted by the combined builder
    fnma_sf: list[str]  # FNMA SF issuance-silver source columns (verified all-null)
    il: list[str]  # UMBS ILLD/snapshot source columns (verified all-null)
    gnma: list[str]  # GNMA dailyllmni L source columns (verified all-blank)


#: Output columns PRUNED from the combined ``loan_issuance`` on 2026-07-09 because every
#: source layer behind them is unpopulated (verified across every source layer):
#:
#: * ``fnma_sf`` columns are 100% null across all 51 FNMA SF issuance-silver files (they are
#:   perf-month / termination / CRT fields that only populate on later monthly records — the
#:   SAME source columns populate fine in ``loan_terminations``, so ingestion is sound).
#: * ``il`` columns are 100% null across all ILLD months AND both first-month snapshots (the
#:   step "next adjustment" quartet, ``Updated VS4``, deferral/ADR-count fields — ILLD is an
#:   at-issuance disclosure, so monthly-state fields never populate there). The step
#:   SCHEDULE fields (initial period / total / remaining / terminal / cap) DO populate on
#:   ILLD modified-reissue records and are therefore NOT pruned.
#: * ``gnma`` = ``Seller Issuer ID``: GNMA publishes the slot but it is blank (all spaces) in
#:   every dailyllmni month; the adjacent V1.7+ ``Index Type`` field extracts fine, so the
#:   fixed-width offsets are correct.
#: * ``upb_at_removal`` is the one not-quite-null case: 0.73% of FNMA issuance rows carry a
#:   literal 0.0 (never a real value).
#:
#: ``tests/test_combined/test_pruned_columns_guard.py`` walks this registry against the
#: source silver layers and FAILS if any column ever starts populating — the signal to
#: un-prune it (move its row back into the plan above, aliases as listed here).
PRUNED_ALWAYS_NULL_FIELDS: Final[list[PrunedField]] = [
    {
        "name": "alternative_delinquency_resolution_count",
        "fnma_sf": ["Alternative Delinquency  Resolution Count"],
        "il": ["Number of Alternative Delinquency Resolutions"],
        "gnma": [],
    },
    {"name": "arm_balloon_ind", "fnma_sf": ["ARM Balloon Indicator"], "il": [], "gnma": []},
    {"name": "arm_cap_structure", "fnma_sf": ["ARM Cap Structure"], "il": [], "gnma": []},
    {
        "name": "arm_life_cap_up",
        "fnma_sf": ["Lifetime Interest Rate Cap Up Percent"],
        "il": [],
        "gnma": [],
    },
    {"name": "arm_plan_number", "fnma_sf": ["ARM Plan Number"], "il": [], "gnma": []},
    {"name": "asset_recovery_costs", "fnma_sf": ["Asset Recovery Costs"], "il": [], "gnma": []},
    {
        "name": "co_borrower_credit_score_current",
        "fnma_sf": ["Co-Borrower Credit Score Current"],
        "il": [],
        "gnma": [],
    },
    {
        "name": "credit_enhancement_proceeds",
        "fnma_sf": ["Credit Enhancement Proceeds"],
        "il": [],
        "gnma": [],
    },
    {
        "name": "credit_score_current",
        "fnma_sf": ["Borrower Credit Score Current ", "Current Classic FICO®"],
        "il": [],
        "gnma": [],
    },
    {
        "name": "cumulative_credit_event_gainloss",
        "fnma_sf": ["Cumulative Credit Event Net Gain or Loss"],
        "il": [],
        "gnma": [],
    },
    {
        "name": "cumulative_mod_loss",
        "fnma_sf": ["Cumulative Modification Loss Amount"],
        "il": [],
        "gnma": [],
    },
    {"name": "current_list_price", "fnma_sf": ["Current List Price"], "il": [], "gnma": []},
    {
        "name": "current_list_start_date",
        "fnma_sf": ["Current List Start Date"],
        "il": [],
        "gnma": [],
    },
    {
        "name": "current_period_credit_event_gainloss",
        "fnma_sf": ["Current Period Credit Event Net Gain or Loss"],
        "il": [],
        "gnma": [],
    },
    {
        "name": "current_period_mod_loss",
        "fnma_sf": ["Current Period Modification Loss Amount"],
        "il": [],
        "gnma": [],
    },
    {"name": "deal_name", "fnma_sf": ["Deal Name"], "il": [], "gnma": []},
    {
        "name": "delinquent_accrued_interest",
        "fnma_sf": ["Delinquent Accrued Interest"],
        "il": [],
        "gnma": [],
    },
    {"name": "disposition_date", "fnma_sf": ["Disposition Date"], "il": [], "gnma": []},
    {"name": "foreclosure_costs", "fnma_sf": ["Foreclosure Costs"], "il": [], "gnma": []},
    {"name": "foreclosure_date", "fnma_sf": ["Foreclosure Date"], "il": [], "gnma": []},
    {
        "name": "foreclosure_principal_writeoff",
        "fnma_sf": ["Foreclosure Principal Write-off Amount"],
        "il": [],
        "gnma": [],
    },
    {
        "name": "holding_taxes",
        "fnma_sf": ["Associated Taxes for Holding Property"],
        "il": [],
        "gnma": [],
    },
    {"name": "issuance_full_upb", "fnma_sf": ["UPB at Issuance"], "il": [], "gnma": []},
    {
        "name": "last_paid_installment_date",
        "fnma_sf": ["Last Paid Installment Date"],
        "il": [],
        "gnma": [],
    },
    {
        "name": "loan_holdback_effective_date",
        "fnma_sf": ["Loan Holdback Effective Date"],
        "il": [],
        "gnma": [],
    },
    {"name": "loan_holdback_ind", "fnma_sf": ["Loan Holdback Indicator"], "il": [], "gnma": []},
    {"name": "master_servicer", "fnma_sf": ["Master Servicer"], "il": [], "gnma": []},
    {
        "name": "misc_holding_expenses",
        "fnma_sf": ["Miscellaneous Holding Expenses and Credits"],
        "il": [],
        "gnma": [],
    },
    {
        "name": "mod_noninterest_bearing_upb",
        "fnma_sf": ["Modification-Related Non-Interest Bearing UPB"],
        "il": [],
        "gnma": [],
    },
    {"name": "net_sales_proceeds", "fnma_sf": ["Net Sales Proceeds"], "il": [], "gnma": []},
    {"name": "original_list_price", "fnma_sf": ["Original List Price"], "il": [], "gnma": []},
    {
        "name": "original_list_start_date",
        "fnma_sf": ["Original List Start Date"],
        "il": [],
        "gnma": [],
    },
    {
        "name": "other_foreclosure_proceeds",
        "fnma_sf": ["Other Foreclosure Proceeds"],
        "il": [],
        "gnma": [],
    },
    {
        "name": "principal_forgiveness_amount",
        "fnma_sf": ["Principal Forgiveness Amount"],
        "il": [],
        "gnma": [],
    },
    {
        "name": "property_preservation_costs",
        "fnma_sf": ["Property Preservation and Repair Costs"],
        "il": [],
        "gnma": [],
    },
    {"name": "reference_pool_id", "fnma_sf": ["Reference Pool ID"], "il": [], "gnma": []},
    {"name": "repurchase_date", "fnma_sf": ["Repurchase Date"], "il": [], "gnma": []},
    {
        "name": "repurchase_make_whole_proceeds",
        "fnma_sf": ["Repurchase Make Whole Proceeds"],
        "il": [],
        "gnma": [],
    },
    {
        "name": "repurchase_make_whole_proceeds_flag",
        "fnma_sf": ["Repurchase Make Whole Proceeds Flag"],
        "il": [],
        "gnma": [],
    },
    {
        "name": "scheduled_principal_current",
        "fnma_sf": ["Scheduled Principal Current"],
        "il": [],
        "gnma": [],
    },
    {"name": "seller_issuer_id", "fnma_sf": [], "il": [], "gnma": ["Seller Issuer ID"]},
    {
        "name": "step_adj_frequency",
        "fnma_sf": [],
        "il": ["Step Rate Adjustment Frequency"],
        "gnma": [],
    },
    {
        "name": "step_months_to_next_adj",
        "fnma_sf": [],
        "il": ["Months to Next Step Rate Adjustment Date"],
        "gnma": [],
    },
    {
        "name": "step_next_adj_date",
        "fnma_sf": [],
        "il": ["Next Step Rate Adjustment Date"],
        "gnma": [],
    },
    {"name": "step_next_rate", "fnma_sf": [], "il": ["Next Step Rate"], "gnma": []},
    {
        "name": "total_deferral_amount",
        "fnma_sf": ["Total Deferral Amount "],
        "il": ["Total Deferral Amount"],
        "gnma": [],
    },
    {
        "name": "total_principal_current",
        "fnma_sf": ["Total Principal Current"],
        "il": [],
        "gnma": [],
    },
    {
        "name": "unscheduled_principal_current",
        "fnma_sf": ["Unscheduled Principal Current"],
        "il": [],
        "gnma": [],
    },
    {"name": "upb_at_removal", "fnma_sf": ["UPB at the Time of Removal"], "il": [], "gnma": []},
    {"name": "vs4_updated", "fnma_sf": [], "il": ["Updated VS4"], "gnma": []},
    {"name": "zero_balance_code", "fnma_sf": ["Zero Balance Code"], "il": [], "gnma": []},
    {
        "name": "zero_balance_code_change_date",
        "fnma_sf": ["Zero Balance Code Change Date"],
        "il": [],
        "gnma": [],
    },
    {
        "name": "zero_balance_effective_date",
        "fnma_sf": ["Zero Balance Effective Date"],
        "il": [],
        "gnma": [],
    },
]
