"""Servicer-name normalization and parent-entity consolidation.

Servicer/issuer transfers detected by :mod:`analytics.transfers` are keyed on
free-text names (FNMA/FHLMC carry no servicer ID; GNMA names are resolved from
issuer-ID lookups and contain formatting artifacts). The same economic entity
therefore appears under many spellings, and corporate rebrands (Quicken ->
Rocket) surface as spurious "transfers". This module reduces names to a
canonical form and folds spelling variants, rebrands, and affiliate/nominee
shuffles under a single **parent entity**, so that:

- pure renames / punctuation variants / intercompany shuffles can be flagged
  ``intercompany`` and excluded from true-transfer market trends, while
- acquisition-driven integrations (Caliber -> NewRez) are *kept* as true
  transfers (economic control changed).

The normalization heuristic and the curated ``PARENT_MAP`` are ported from the
``msr_transfers`` ``investigation_msr_transfer_trends`` study; the per-row
``map_elements`` lookups there are reimplemented as vectorized expressions
here. ``PARENT_MAP`` covers roughly the top ~120 names by UPB involvement
(~91% of gross flow); unmapped names fall back to a title-cased version of
their normalized form, with a bank/nonbank guess from name keywords.

This is deterministic, closed-form name handling (no model), and is the one
piece of the transfer pipeline that encodes curated judgment — kept separate
from the mechanical detection in :mod:`analytics.transfers` so the curated
parent map can evolve independently.
"""

from __future__ import annotations

import polars as pl

# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------
#: Entity-type suffixes stripped (after punctuation/whitespace removal) so that
#: "Amerihome Mortgage Company, LLC" and "Amerihome Mortgage Company LLC"
#: collapse to the same key. Sorted longest-first so multi-word forms
#: (NATIONALASSOCIATION) match before their abbreviations (NA).
SUFFIXES: list[str] = sorted(
    [
        "LLC", "LP", "INC", "INCORPORATED", "CORP", "CORPORATION", "CO",
        "COMPANY", "LTD", "LIMITED", "NA", "NATIONALASSOCIATION", "FSB",
        "FEDERALSAVINGSBANK", "SSB", "FA", "LLLP", "PLC",
    ],
    key=len,
    reverse=True,
)
#: Matches one or more stacked entity suffixes anchored at the end of a name.
_SUFFIX_REGEX: str = r"(" + "|".join(SUFFIXES) + r")+$"


def normalize_servicer_name(col_name: str = "servicer_name") -> pl.Expr:
    """Normalize a servicer name to a punctuation- and suffix-free upper key.

    Uppercases, strips a leading ``THE``, standardizes ampersands, removes all
    non-alphanumerics, then peels stacked entity suffixes (applied three times
    to catch forms like ``COMPANY LLC``). The result is a join key, not a
    display string.

    Args:
        col_name: Name of the raw servicer-name column.

    Returns:
        A Polars expression producing the normalized key.

    Examples:
        >>> import polars as pl
        >>> df = pl.DataFrame({"servicer_name": ["Amerihome Mortgage Company, LLC"]})
        >>> df.select(normalize_servicer_name("servicer_name")).item()
        'AMERIHOMEMORTGAGE'
    """
    return (
        pl.col(col_name)
        .str.to_uppercase()
        .str.replace(r"^THE\s+", "")
        .str.replace_all(r"&AMP;", "&")
        .str.replace_all(r"&", " AND ")
        .str.replace_all(r"[^A-Z0-9]", "")
        .str.replace(_SUFFIX_REGEX, "")
        .str.replace(_SUFFIX_REGEX, "")
        .str.replace(_SUFFIX_REGEX, "")
    )


# ---------------------------------------------------------------------------
# Parent map: normalized key -> (parent entity, sector)
# Sector: "bank" | "nonbank" | "vehicle" (passive MSR investment vehicle)
# ---------------------------------------------------------------------------
PARENT_MAP: dict[str, tuple[str, str]] = {
    # --- Rocket family (Quicken rebrand chain; Nationstar kept separate) ---
    "QUICKENLOANS": ("Rocket Mortgage", "nonbank"),
    "ROCKETMORTGAGE": ("Rocket Mortgage", "nonbank"),
    # --- Mr. Cooper (Nationstar) ---
    "NATIONSTARMORTGAGE": ("Mr. Cooper (Nationstar)", "nonbank"),
    # --- Rithm family (New Residential renamed Rithm; NewRez is its servicer) ---
    "NEWRESIDENTIALMORTGAGE": ("Rithm (NewRez)", "nonbank"),
    "NEWREZ": ("Rithm (NewRez)", "nonbank"),
    "SHELLPOINTMORTGAGESERVICING": ("Rithm (NewRez)", "nonbank"),
    # --- Two Harbors (Matrix is its nominee; TH MSR Holdings its holdco) ---
    "MATRIXFINANCIALSERVICE": ("Two Harbors (Matrix)", "vehicle"),
    "MATRIXFINANCIALSERVICES": ("Two Harbors (Matrix)", "vehicle"),
    "THMSRHOLDINGS": ("Two Harbors (Matrix)", "vehicle"),
    # --- Onity (Ocwen acquired PHH 2018; renamed Onity 2024) ---
    "PHHMORTGAGE": ("Onity (PHH/Ocwen)", "nonbank"),
    "PHHASSETSERVICES": ("Onity (PHH/Ocwen)", "nonbank"),
    "OCWENLOANSERVICING": ("Onity (PHH/Ocwen)", "nonbank"),
    "ONITYMORTGAGE": ("Onity (PHH/Ocwen)", "nonbank"),
    # --- UWM (United Shore rebrand) ---
    "UNITEDSHOREFINANCIALSERVICES": ("UWM", "nonbank"),
    "UNITEDWHOLESALEMORTGAGE": ("UWM", "nonbank"),
    # --- Freedom (GNMA files use the DBA name) ---
    "FREEDOMMORTGAGE": ("Freedom Mortgage", "nonbank"),
    "DBAFREEDOMHOMEMORTGAGE": ("Freedom Mortgage", "nonbank"),
    # --- Truist (BB&T + SunTrust merger, 2019) ---
    "TRUISTBANK": ("Truist", "bank"),
    "SUNTRUSTBANK": ("Truist", "bank"),
    "SUNTRUSTMORTGAGE": ("Truist", "bank"),
    "BRANCHBANKINGANDTRUST": ("Truist", "bank"),
    # --- Other large operators ---
    "LAKEVIEWLOANSERVICING": ("Lakeview (Bayview)", "nonbank"),
    "PINGORALOANSERVICING": ("Pingora (Bayview)", "vehicle"),
    "AMERIHOMEMORTGAGE": ("AmeriHome (Western Alliance)", "nonbank"),
    "HOMEPOINTFINANCIAL": ("Home Point", "nonbank"),
    "CARRINGTONMORTGAGESERVICES": ("Carrington", "nonbank"),
    "SPECIALIZEDLOANSERVICING": ("SLS (Computershare)", "nonbank"),
    "FLAGSTARBANK": ("Flagstar", "bank"),
    "GUARANTEEDRATE": ("Rate (Guaranteed Rate)", "nonbank"),
    "CALIBERHOMELOANS": ("Caliber", "nonbank"),
    "PROVIDENTFUNDINGASSOCIATES": ("Provident Funding", "nonbank"),
    "PENNYMAC": ("Pennymac", "nonbank"),  # PENNYMAC CORP post-suffix-strip
    "PENNYMACLOANSERVICES": ("Pennymac", "nonbank"),
    "LOANDEPOTCOM": ("loanDepot", "nonbank"),
    "CROSSCOUNTRYMORTGAGE": ("CrossCountry", "nonbank"),
    "MOVEMENTMORTGAGE": ("Movement", "nonbank"),
    "CMGMORTGAGE": ("CMG", "nonbank"),
    "GUILDMORTGAGE": ("Guild", "nonbank"),
    "DITECHFINANCIAL": ("Ditech", "nonbank"),
    "FINANCEOFAMERICAMORTGAGE": ("Finance of America", "nonbank"),
    "AMERISAVEMORTGAGE": ("AmeriSave", "nonbank"),
    "PLAZAHOMEMORTGAGE": ("Plaza Home", "nonbank"),
    "PLANETHOMELENDING": ("Planet Home", "nonbank"),
    "BROKERSOLUTIONS": ("New American Funding", "nonbank"),
    "BROKERSOLUTIONSINCDBANEWAMERICANFUNDING": ("New American Funding", "nonbank"),
    "NEWAMERICANFUNDING": ("New American Funding", "nonbank"),
    "CARDINALFINANCIAL": ("Cardinal Financial", "nonbank"),
    "CARDINALFINANCIALCOMPANYLIMITEDPARTNERSHIP": ("Cardinal Financial", "nonbank"),
    "HOMEBRIDGEFINANCIALSERVICES": ("Homebridge", "nonbank"),
    "UNIONHOMEMORTGAGE": ("Union Home", "nonbank"),
    "SERVISONE": ("BSI Financial", "nonbank"),
    "SERVISONEINCDBABSIFINANCIALSERVICES": ("BSI Financial", "nonbank"),
    "BSIFINANCIALSERVICES": ("BSI Financial", "nonbank"),
    "ARCHOME": ("Arc Home", "nonbank"),
    "AURORAFINANCIALGROUP": ("Aurora Financial", "nonbank"),
    "MONEYSOURCE": ("The Money Source", "nonbank"),
    "MISSIONSERVICINGRESIDENTIAL": ("Mission Servicing", "nonbank"),
    "MORTGAGERESEARCHCENTER": ("Veterans United (MRC)", "nonbank"),
    "AMERICANPACIFICMORTGAGE": ("American Pacific", "nonbank"),
    "STEARNSLENDING": ("Stearns", "nonbank"),
    "FRANKLINAMERICANMORTGAGE": ("Franklin American", "nonbank"),
    "SIERRAPACIFICMORTGAGE": ("Sierra Pacific", "nonbank"),
    "PRIMELENDINGAPLAINSCAPITAL": ("PrimeLending", "nonbank"),
    "FIRSTGUARANTYMORTGAGE": ("First Guaranty", "nonbank"),
    "PULTEMORTGAGE": ("Pulte", "nonbank"),
    "RUSHMORELOANMANAGEMENTSERVICES": ("Rushmore", "nonbank"),
    "ACADEMYMORTGAGE": ("Academy", "nonbank"),
    "MUTUALOFOMAHAMORTGAGE": ("Mutual of Omaha Mortgage", "nonbank"),
    "GMFS": ("GMFS", "nonbank"),
    "EVERETTFINANCIAL": ("Supreme Lending (Everett)", "nonbank"),
    "LOWER": ("Lower", "nonbank"),
    "VILLAGECAPITALANDINVESTMENT": ("Village Capital", "nonbank"),
    "NEWDAYFINANCIAL": ("NewDay USA", "nonbank"),
    "EQUITYPRIMEMORTGAGE": ("Equity Prime", "nonbank"),
    "ROUNDPOINTMORTGAGESERVICING": ("RoundPoint", "nonbank"),
    "SENECAMORTGAGESERVICING": ("Seneca", "nonbank"),
    "CMCFUNDING": ("CMC Funding", "nonbank"),
    # --- Passive MSR investment vehicles ---
    "ONSLOWBAYFINANCIAL": ("Onslow Bay (Annaly)", "vehicle"),
    "MSRASSETVEHICLE": ("MSR Asset Vehicle (MAV)", "vehicle"),
    "NEXUSNOVA": ("Nexus Nova", "vehicle"),
    "PODIUMMORTGAGECAPITAL": ("Podium Mortgage Capital", "vehicle"),
    "BUNGALOWFUNDING": ("Bungalow Funding", "vehicle"),
    "SAILFISHSERVICING": ("Sailfish Servicing", "vehicle"),
    "CYPRESSLOANSERVICING": ("Cypress Loan Servicing", "vehicle"),
    "GRANDERMORTGAGECAPITAL": ("Grander Mortgage Capital", "vehicle"),
    "MARLINMORTGAGECAPITAL": ("Marlin Mortgage Capital", "vehicle"),
    "BASEMSR2": ("Base MSR", "vehicle"),
    "PAMMSRTRUSTI": ("PAM MSR Trust", "vehicle"),
    # --- Banks ---
    "JPMORGANCHASEBANK": ("JPMorgan Chase", "bank"),
    "WELLSFARGOBANK": ("Wells Fargo", "bank"),
    "BANKOFAMERICA": ("Bank of America", "bank"),
    "PNCBANK": ("PNC", "bank"),
    "FIFTHTHIRDBANK": ("Fifth Third", "bank"),
    "FIFTHTHIRDMORTGAGE": ("Fifth Third", "bank"),
    "CITIMORTGAGE": ("Citi", "bank"),
    "USBANK": ("U.S. Bank", "bank"),
    "USAA": ("USAA", "bank"),  # USAA FEDERAL SAVINGS BANK post-suffix-strip
    "COLORADO": ("Colorado Federal Savings Bank", "bank"),  # COLORADO FSB post-suffix-strip
    "BANCORPSOUTH": ("Cadence (BancorpSouth)", "bank"),
    "ARVESTCENTRALMORTGAGE": ("Arvest", "bank"),
    "ARVESTBANK": ("Arvest", "bank"),
    "CENTRALMORTGAGE": ("Arvest", "bank"),
    "MANDTBANK": ("M&T Bank", "bank"),
    "REGIONSBANK": ("Regions", "bank"),
    "HUNTINGTONNATIONALBANK": ("Huntington", "bank"),
    "MUFGUNIONBANK": ("MUFG Union Bank", "bank"),
    "COLONIALSAVINGS": ("Colonial Savings", "bank"),
    "CORNERSTONECAPITALBANK": ("Cornerstone", "bank"),
    "CORNERSTONEHOMELENDING": ("Cornerstone", "bank"),
    "AMERISBANK": ("Ameris", "bank"),
    "NEXBANK": ("NexBank", "bank"),
    "MIDFIRSTBANK": ("MidFirst", "bank"),
    "HOMESTREETBANK": ("HomeStreet", "bank"),
    "GATEWAYFIRSTBANK": ("Gateway First Bank", "bank"),
    "GATEWAYMORTGAGEADIVISIONOFGATEWAYFIRSTBANK": ("Gateway First Bank", "bank"),
    "GATEWAYMORTGAGEGROUP": ("Gateway First Bank", "bank"),
    "BMOHARRISBANK": ("BMO", "bank"),
    "UMPQUABANK": ("Umpqua", "bank"),
    "BOKF": ("BOK Financial", "bank"),
    "TEXASCAPITALBANK": ("Texas Capital", "bank"),
    "BANCORPSOUTHBANK": ("Cadence (BancorpSouth)", "bank"),
    "CADENCEBANK": ("Cadence (BancorpSouth)", "bank"),
    "SANTANDERBANK": ("Santander", "bank"),
    "NBKCBANK": ("NBKC", "bank"),
    "TIAA": ("TIAA Bank", "bank"),
    "BARRINGTONBANKANDTRUST": ("Wintrust (Barrington)", "bank"),
    "FIDELITYBANK": ("Fidelity Bank", "bank"),
    "NORTHPOINTEBANK": ("Northpointe", "bank"),
}

#: Derived flat lookups for vectorized joins (parent name / sector).
_PARENT_NAME: dict[str, str] = {k: v[0] for k, v in PARENT_MAP.items()}
_PARENT_SECTOR: dict[str, str] = {k: v[1] for k, v in PARENT_MAP.items()}

#: Compact (punctuation-free) keyword markers that imply a depository "bank"
#: when an entity is not in ``PARENT_MAP``. Tested against the uppercased,
#: alphanumeric-only raw name (suffixes kept), so spaced forms are dropped.
BANK_KEYWORDS: tuple[str, ...] = (
    "BANK", "BANC", "SAVINGS", "CREDITUNION", "TRUSTCOMPANY",
    "NATIONALASSOCIATION", "FEDERALSAVINGS", "FSB",
)


def classify_sector_expr(raw_col: str) -> pl.Expr:
    """Bank/nonbank guess for names not in ``PARENT_MAP``, from raw-name keywords.

    Operates on the *raw* name (punctuation removed, entity suffixes kept) so
    depository markers like ``FSB`` / ``NATIONAL ASSOCIATION`` survive. Returns
    ``"bank"`` when any :data:`BANK_KEYWORDS` marker is present, else
    ``"nonbank"`` (the safe default for unrecognized originator-servicers).

    Args:
        raw_col: Name of the raw servicer-name column.

    Returns:
        A Polars expression producing ``"bank"`` or ``"nonbank"``.
    """
    compact = pl.col(raw_col).str.to_uppercase().str.replace_all(r"[^A-Z0-9]", "")
    cond = pl.lit(False)
    for kw in BANK_KEYWORDS:
        cond = cond | compact.str.contains(kw, literal=True)
    return pl.when(cond).then(pl.lit("bank")).otherwise(pl.lit("nonbank"))


def _parent_name_expr(norm_col: str) -> pl.Expr:
    """Parent display name from a normalized key, vectorized.

    Mapped keys resolve to their curated parent; unmapped keys fall back to a
    title-cased version of the normalized key (``"(blank)"`` when empty).
    """
    mapped = pl.col(norm_col).replace_strict(_PARENT_NAME, default=None, return_dtype=pl.Utf8)
    fallback = (
        pl.when(pl.col(norm_col) == "")
        .then(pl.lit("(blank)"))
        .otherwise(pl.col(norm_col).str.to_titlecase())
    )
    return mapped.fill_null(fallback)


def _parent_sector_expr(norm_col: str, raw_col: str) -> pl.Expr:
    """Parent sector from a normalized key, falling back to keyword inference."""
    mapped = pl.col(norm_col).replace_strict(_PARENT_SECTOR, default=None, return_dtype=pl.Utf8)
    return mapped.fill_null(classify_sector_expr(raw_col))


def build_servicer_parent_crosswalk(
    df: pl.DataFrame,
    *,
    name_cols: tuple[str, str] = ("servicer_from", "servicer_to"),
) -> pl.DataFrame:
    """Build a raw-name -> (normalized, parent, sector) crosswalk.

    Collects every distinct name appearing in either side of a transfer table,
    normalizes it, and resolves a parent entity and sector. One row per raw
    name; ``manually_mapped`` flags names matched directly by ``PARENT_MAP``.

    Args:
        df: A detected-transfer table.
        name_cols: The (from, to) raw servicer-name columns to pool.

    Returns:
        A DataFrame with ``raw_name``, ``norm``, ``parent``, ``sector``,
        ``manually_mapped``.
    """
    from_col, to_col = name_cols
    names = (
        pl.concat(
            [
                df.select(pl.col(from_col).alias("raw_name")),
                df.select(pl.col(to_col).alias("raw_name")),
            ],
            how="diagonal",
        )
        .unique(subset=["raw_name"])
        .sort("raw_name")
        .with_columns(normalize_servicer_name("raw_name").alias("norm"))
    )
    return names.with_columns(
        _parent_name_expr("norm").alias("parent"),
        _parent_sector_expr("norm", "raw_name").alias("sector"),
        pl.col("norm").is_in(list(PARENT_MAP.keys())).alias("manually_mapped"),
    )


def add_servicer_parents(
    df: pl.DataFrame,
    *,
    from_col: str = "servicer_from",
    to_col: str = "servicer_to",
) -> pl.DataFrame:
    """Attach parent/sector to a transfer table and flag intercompany rows.

    Adds ``parent_from``/``parent_to``, ``sector_from``/``sector_to``, and an
    ``intercompany`` boolean (parent_from == parent_to) marking pure renames,
    punctuation variants, and affiliate/nominee shuffles. Filter on
    ``~intercompany`` to keep only true (control-changing) transfers.

    Args:
        df: A detected-transfer table.
        from_col: Seller (previous) servicer-name column.
        to_col: Buyer (new) servicer-name column.

    Returns:
        ``df`` with the parent/sector/intercompany columns appended.
    """
    return (
        df.with_columns(
            normalize_servicer_name(from_col).alias("_norm_from"),
            normalize_servicer_name(to_col).alias("_norm_to"),
        )
        .with_columns(
            _parent_name_expr("_norm_from").alias("parent_from"),
            _parent_name_expr("_norm_to").alias("parent_to"),
            _parent_sector_expr("_norm_from", from_col).alias("sector_from"),
            _parent_sector_expr("_norm_to", to_col).alias("sector_to"),
        )
        .with_columns((pl.col("parent_from") == pl.col("parent_to")).alias("intercompany"))
        .drop("_norm_from", "_norm_to")
    )
