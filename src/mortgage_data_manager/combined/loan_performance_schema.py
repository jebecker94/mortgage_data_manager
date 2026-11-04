"""Declarative field plans for the ``loan_performance`` + ``loan_terminations`` masters.

The column allow-lists below are the ones the field-volatility analysis
proved are GENUINELY TIME-VARYING within a loan id — everything static lives once
on ``loan_issuance`` and joins by ``loan_key``. Two outputs:

  * ``loan_performance`` (loan x reporting-month) -- the dynamic core, fused from
    the SF Loan-Performance panel (base) and the UMBS MLLD/FU monthly factor
    (enrichment + the recency tail), bridged by the strictly-1:1 mbs_umbs
    crosswalk and joined on ``reporting_period``. A FULL-OUTER month join keeps a
    matched loan's UMBS-only recent months (SF release lags ~9mo) and its SF-only
    early months (UMBS disclosure lags issuance ~2-3mo); ``month_source`` tags each
    row both / sf_only / umbs_only.
  * ``loan_terminations`` (one row per loan terminal event) -- the sparse
    zero-balance / loss waterfall, SF-sourced (SF carries the explicit ZBC; UMBS
    corroborates by disappearance). Kept OUT of the dense monthly panel because it
    is populated for ~1 month in ~42 (pure per-month waste).

Per-GSE source aliases are coalesced (vintage/GSE renames OR-ed); ``resolve`` picks
the canonical source when a field is on both spines. The GNMA rows, a single source,
have a plan table of their own (`GNMA_LOAN_PERFORMANCE_PLAN`).
"""

from __future__ import annotations

from typing import Final, TypedDict

from mortgage_data_manager.combined._common import LegField


class PerfField(TypedDict):
    """One unified performance-panel column and the source column(s) it draws on."""

    name: str  # unified output column
    sf: list[str]  # SF Loan-Performance source aliases (coalesced)
    umbs: list[str]  # MLLD/FU source aliases (coalesced)
    resolve: str  # sf | umbs | sf_first (coalesce SF,UMBS) | umbs_first (coalesce UMBS,SF)


class TermField(TypedDict):
    """One unified terminations/event column (SF-sourced; per-GSE aliases coalesced)."""

    name: str
    sf: list[str]


#: The genuinely time-varying core (loan x month). ``current_upb`` is the FULL-loan
#: balance (aligns with GNMA v1 ``current_upb``); ``current_investor_upb`` is the
#: UMBS participation-weighted balance -- both kept (the investor-vs-actual spread).
#: Delinquency and event fields are handled bespoke in the builder (parse to int /
#: derive the taxonomy) and are NOT listed here.
#:
#: Shared fields resolve **UMBS-first by default**: UMBS is both fresher (the SF panel
#: is right-censored at the release frontier ~9mo) AND more complete -- the SF
#: disclosure VALUE-MASKS small servicers/sellers as "Other" and suppresses
#: forbearance/deferral fields to null, while UMBS discloses them (verified 2026-06-26
#: on real data: UMBS rescues 100% of SF-masked
#: servicer/seller rows; ``total_deferral_amount`` SF 96.5% null vs UMBS 0%). SF fills
#: where UMBS is null (pre-2019-06 history). EXCEPTIONS resolve ``sf_first`` -- the
#: fields where SF is the substantive source and UMBS (esp. FHLMC FU) is ~100% null:
#: ``interest_bearing_upb`` and ``estimated_ltv``.
GSE_PERF_PLAN: Final[list[PerfField]] = [
    # --- seasoning (mechanical monthly tick; ~99% change) ---
    {"name": "loan_age", "sf": ["Loan Age"], "umbs": ["Loan Age"], "resolve": "umbs_first"},
    {
        "name": "remaining_months_to_maturity",
        "sf": ["Remaining Months to Legal Maturity", "Remaining Months To Maturity"],
        "umbs": ["Remaining Months to Maturity"],
        "resolve": "umbs_first",
    },
    # --- balances (KEEP BOTH UPB notions) ---
    {"name": "current_upb", "sf": ["Current Actual UPB"], "umbs": [], "resolve": "sf"},
    {
        "name": "current_investor_upb",
        "sf": [],
        "umbs": ["Current Investor Loan UPB"],
        "resolve": "umbs",
    },
    {
        "name": "interest_bearing_upb",
        "sf": ["Interest Bearing UPB"],
        "umbs": ["Interest Bearing Mortgage Loan Amount"],
        "resolve": "sf_first",  # SF is the substantive source; UMBS (FU) is ~100% null
    },
    {
        "name": "current_deferred_upb",
        "sf": ["Current Deferred UPB"],
        "umbs": ["Current Deferred UPB"],
        "resolve": "umbs_first",
    },
    # --- rate (constant for FRM; moves for ARMs/mods; SF==UMBS 99.9999%) ---
    {
        "name": "current_interest_rate",
        "sf": ["Current Interest Rate"],
        "umbs": ["Current Interest Rate"],
        "resolve": "umbs_first",
    },
    {
        "name": "current_net_interest_rate",
        "sf": [],
        "umbs": ["Current Net Interest Rate"],
        "resolve": "umbs",
    },
    # --- servicing (MSR transfers, ~30-40% of loans) ---
    {
        "name": "servicer_name",
        "sf": ["Servicer Name"],
        "umbs": ["Servicer Name"],
        "resolve": "umbs_first",
    },
    # --- workout / forbearance (~10%) ---
    {
        "name": "borrower_assistance_plan",
        "sf": ["Borrower Assistance Plan", "Borrower Assistance Status Code"],
        "umbs": ["Borrower Assistance Plan"],
        "resolve": "umbs_first",
    },
    {
        "name": "total_deferral_amount",
        "sf": ["Total Deferral Amount "],
        "umbs": ["Total Deferral Amount"],
        "resolve": "umbs_first",
    },
    {"name": "modification_flag", "sf": ["Modification Flag"], "umbs": [], "resolve": "sf"},
    {
        "name": "number_of_modifications",
        "sf": [],
        "umbs": ["Number of Modifications"],
        "resolve": "umbs",
    },
    # --- MI (dynamic on UMBS, ~5% = cancellation) ---
    {
        "name": "mortgage_insurance_percent",
        "sf": [],
        "umbs": ["Mortgage Insurance Percent"],
        "resolve": "umbs",
    },
    {
        "name": "mi_cancellation_indicator",
        "sf": ["Mortgage Insurance Cancellation Indicator"],
        "umbs": ["Mortgage Insurance Cancellation Indicator"],
        "resolve": "umbs_first",
    },
    # --- refreshed marks ---
    {
        "name": "estimated_ltv",
        "sf": ["Estimated Loan-to-Value (ELTV)"],
        "umbs": ["Estimated Loan-To-Value (ELTV)"],
        "resolve": "sf_first",  # FHLMC SF ELTV is the rich monthly AVM; UMBS (FU) ~100% null
    },
    {
        "name": "updated_credit_score",
        "sf": [],
        "umbs": ["Updated Credit Score", "Updated Classic FICO"],
        "resolve": "umbs",
    },
    # --- pool identity (FNMA RE-POOLS loans -> on the monthly row / loanxpool bridge) ---
    {"name": "cusip", "sf": [], "umbs": ["CUSIP"], "resolve": "umbs"},
    {"name": "security_identifier", "sf": [], "umbs": ["Security Identifier"], "resolve": "umbs"},
    {"name": "prefix", "sf": [], "umbs": ["Prefix"], "resolve": "umbs"},
]

#: SF delinquency status -> integer months delinquent (00/0 = current). Bespoke in
#: the builder; the raw string is kept as ``delinquency_raw``. UMBS ``Days Delinquent``
#: (a mislabeled 0-6 CYCLE count, null ~17% of current months) is kept as
#: ``days_delinquent_umbs``.
SF_DELINQUENCY_COL: Final[str] = "Current Loan Delinquency Status"
UMBS_DAYS_DELINQUENT_COL: Final[str] = "Days Delinquent"

#: Sparse termination / loss-waterfall fields (one row per loan terminal event).
#: SF-sourced; per-GSE aliases coalesced. Several FNMA CRT loss fields are absent in
#: the STANDARD SF Loan-Performance product (empty here) -- carried for schema
#: completeness, populated where the source provides them.
GSE_TERM_PLAN: Final[list[TermField]] = [
    {"name": "upb_at_removal", "sf": ["UPB at the Time of Removal", "Zero Balance Removal UPB"]},
    {
        "name": "last_paid_installment_date",
        "sf": ["Last Paid Installment Date", "Due Date of Last Paid Installment (DDLPI)"],
    },
    {"name": "foreclosure_date", "sf": ["Foreclosure Date"]},
    {"name": "disposition_date", "sf": ["Disposition Date"]},
    {"name": "defect_settlement_date", "sf": ["Defect Settlement Date"]},
    {"name": "net_sales_proceeds", "sf": ["Net Sales Proceeds"]},
    {"name": "credit_enhancement_proceeds", "sf": ["Credit Enhancement Proceeds"]},
    {"name": "mi_recoveries", "sf": ["MI Recoveries"]},
    {"name": "non_mi_recoveries", "sf": ["Non MI Recoveries"]},
    {"name": "foreclosure_costs", "sf": ["Foreclosure Costs"]},
    {"name": "legal_costs", "sf": ["Legal Costs"]},
    {
        "name": "maintenance_preservation_costs",
        "sf": ["Property Preservation and Repair Costs", "Maintenance and Preservation Costs"],
    },
    {"name": "taxes", "sf": ["Associated Taxes for Holding Property", "Taxes and Insurance"]},
    {
        "name": "misc_expenses",
        "sf": ["Miscellaneous Holding Expenses and Credits", "Miscellaneous Expenses"],
    },
    {"name": "asset_recovery_costs", "sf": ["Asset Recovery Costs"]},
    {"name": "expenses", "sf": ["Expenses"]},
    {"name": "actual_loss", "sf": ["Actual Loss Calculation"]},
    {
        "name": "modification_cost",
        "sf": ["Cumulative Modification Loss Amount", "Modification Cost"],
    },
    {"name": "delinquent_accrued_interest", "sf": ["Delinquent Accrued Interest"]},
    {"name": "principal_forgiveness", "sf": ["Principal Forgiveness Amount"]},
    {"name": "principal_writeoff", "sf": ["Foreclosure Principal Write-off Amount"]},
    {"name": "repurchase_make_whole_proceeds", "sf": ["Repurchase Make Whole Proceeds"]},
    {"name": "other_foreclosure_proceeds", "sf": ["Other Foreclosure Proceeds"]},
]

#: Zero-Balance-Code -> unified event taxonomy. FNMA discloses 2-char strings, FHLMC
#: integers; both are normalized to a zero-padded 2-char key first. Codes line up by
#: meaning across the GSEs (Fannie/Freddie file layouts, "Zero Balance Code").
ZBC_EVENT_TYPE: Final[dict[str, str]] = {
    "01": "prepaid_or_matured",
    "02": "third_party_sale",
    "03": "short_sale_or_chargeoff",
    "06": "repurchase",
    "09": "reo_disposition",
    "15": "note_sale",
    "16": "reperforming_or_other_sale",
    # Freddie 96 = a confirmed underwriting or servicing defect. The remedy may be a
    # repurchase, an alternative remedy or a waiver, and some loans keep reporting
    # afterwards, so it is not filed under repurchase.
    "96": "confirmed_defect",
}
#: ZBC codes that are credit/default-driven terminal events (vs voluntary prepay).
ZBC_CREDIT_EVENT_CODES: Final[frozenset[str]] = frozenset({"02", "03", "09"})

#: Detailed per-agency ``event_type`` -> coarse TRI-AGENCY ``event_class``. The detailed
#: vocabularies are disjoint by construction (GSE = ZBC-derived, GNMA = removal-reason-
#: derived) and carry agency-specific nuance worth keeping; ``event_class`` is the level at
#: which cross-agency pooling is safe. Consistent with ``is_credit_event``:
#: ``event_class == 'credit_event'`` ⟺ ``is_credit_event`` (GSE ZBC 02/03/09; GNMA
#: removal 2/3 — GNMA 4 loss-mitigation stays a separate ``workout_removal`` because it is
#: excluded from the credit-event flag).
EVENT_CLASS: Final[dict[str, str]] = {
    # GSE (ZBC-derived event_type)
    "prepaid_or_matured": "voluntary_payoff",
    "third_party_sale": "credit_event",
    "short_sale_or_chargeoff": "credit_event",
    "reo_disposition": "credit_event",
    "repurchase": "repurchase",
    "note_sale": "loan_sale",
    "reperforming_or_other_sale": "loan_sale",
    "confirmed_defect": "other_removal",
    # GNMA (removal-reason-derived event_type)
    "voluntary_prepay": "voluntary_payoff",
    "dq_buyout": "credit_event",
    "foreclosure_reo": "credit_event",
    "loss_mit_workout": "workout_removal",
    "substitution": "other_removal",
    "other_removal": "other_removal",
}

#: "Not available / not applicable" codes that the GSE disclosures mix into otherwise
#: Y/N/plan-letter code spaces ('7' is the MODAL value of the MI-cancellation and
#: borrower-assistance columns, ~70-76% of rows). Normalized to null in the combined
#: outputs. Pure yes/no
#: indicators are normalized to Boolean at emit time (``core.harmonized.flag_to_bool``, which also
#: nulls 7/9); this scrub list covers the remaining letter-coded columns.
GSE_NA_CODES: Final[frozenset[str]] = frozenset({"7", "9"})
#: Perf-panel columns carrying those N/A codes inside a richer letter code space.
PERF_NA_CODE_COLUMNS: Final[tuple[str, ...]] = ("borrower_assistance_plan",)


#: GNMA rows: the llmon L record (llmon1 = Ginnie I, llmon2 = Ginnie II; llmon silver
#: shares the dailyllmni layout), time-varying fields only. Readers are
#: ``_gnma_loan_readers`` plus ``rmm``; ``cusip`` / ``pool_type`` are joined afterward
#: from the P record. ``loan_terminations`` reads its Ginnie columns through this plan.
GNMA_LOAN_PERFORMANCE_PLAN: Final[list[LegField]] = [
    # loan_key = Disclosure Sequence Number alone, matching the issuance loan_key 1:1.
    LegField("loan_key", "raw", ("disclosure_sequence_number",)),
    LegField("loan_key_kind", "const", value="gnma_seq"),
    LegField("agency", "const", value="GNMA"),
    LegField("source_universe", "const", value="gnma_disclosure"),
    LegField("program", "derived"),
    LegField("reporting_period", "derived"),
    LegField("reporting_year", "derived"),
    LegField("pool_id", "raw", ("pool_id",)),
    LegField("issuer_id", "str", ("issuer_id",)),
    LegField("current_upb", "gnma_num", ("unpaid_principal_balance",)),
    LegField("current_interest_rate", "gnma_num", ("loan_interest_rate",)),
    LegField("loan_age", "int", ("loan_age_in_months",)),
    # RMM junk reaches 967 months against a 480-month product ceiling -> window scrub.
    LegField("remaining_months_to_maturity", "rmm", ("remaining_loan_term_in_months",)),
    # Already the 0-6 capped cycle count (6 = 6+).
    LegField("delinquency_months", "int", ("months_delinquent",)),
    LegField("delinquency_raw", "str", ("months_delinquent",)),
    LegField("months_prepaid", "int", ("months_pre_paid",)),
    LegField("event_flag", "flag_y", ("current_month_liquidation_flag",)),
    LegField("event_type", "derived"),
    LegField("event_class", "derived"),
    LegField("event_raw_code", "str", ("removal_reason",)),
    LegField("is_credit_event", "derived"),
    LegField("property_state", "str", ("state",)),
]
