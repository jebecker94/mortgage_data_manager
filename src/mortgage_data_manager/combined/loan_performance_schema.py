"""Declarative field plans for the GSE ``loan_performance`` + ``loan_terminations`` masters.

The column allow-lists below are the ones the field-volatility investigation
(``investigations/reports/investigation_perf_field_volatility_2026-06-26.md``)
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
the canonical source when a field is on both spines.
"""

from __future__ import annotations

from typing import Final, TypedDict


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
#: forbearance/deferral fields to null, while UMBS discloses them (verified in
#: ``investigation_sf_umbs_source_fidelity_2026-06-26``: UMBS rescues 100% of SF-masked
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
    "96": "removal",
}
#: ZBC codes that are credit/default-driven terminal events (vs voluntary prepay).
ZBC_CREDIT_EVENT_CODES: Final[frozenset[str]] = frozenset({"02", "03", "09"})
