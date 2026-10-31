"""GNMA single-family pool-type and issue-type code tables.

Hand-authored from the **Ginnie Mae MBS Guide 5500.3 Rev. 1, Appendix III-6**
(form HUD 11705, instructions 15 & 16) — the authoritative source for the
2-letter pool-type ("program type") codes and the issue-type codes that appear
in GNMA pool disclosure (``nimonSFPS`` PS, ``nissues`` D, ``monthlySFPS`` …).

Two orthogonal axes describe a GNMA pool:

* **Issue type** (instruction 15) — the Ginnie I/II structure, in the ``Pool
  Indicator`` field: ``X`` = Ginnie I; ``C`` = Ginnie II custom (single-issuer);
  ``M`` = Ginnie II multiple-issuer. This is what determines program (I vs II),
  NOT the pool type.
* **Pool type** (instruction 16) — the collateral/product, in the ``Pool Type``
  field (SF, JM, MH, ARM variants, …).

The ARM family is identified in instruction 8a (the codes that carry a "security
margin"): ``AR AQ AT AF AS AX RL QL TL FL FB SL XL``. The fixed single-family
level-payment family is ``SF FS ET RG JM BD``. Program availability (instr. 16):
``PL PN LM LS RX CL CS SN BD`` are Ginnie I only; ``AR AT AF AS AX RG JM AQ ET``
are Ginnie II only.

**Platinum pool types are a separate code space** and are NOT in Appendix III-6.
They are taken from Appendix 1 of the Ginnie Mae Enhanced Platinum Pool Level
Disclosure layout (forward Platinum: ``AP BP FP EP JP SP`` fixed, ``WA``–``WN``
adjustable) and Appendix 1 of the HMBS Platinum Portfolio layout (HECM Platinum:
``PA``–``PF``). All carry ``family="platinum"``; a Platinum security is backed by
other pools, not by loans, so anything summing balances must exclude the family.

Codes observed in our data but NOT defined in the current Appendix III-6
(retired/legacy variants): ``FT`` — mapped to ``other`` until confirmed against
an older MBS Guide vintage. (The Platinum appendix describes ``WC``/``WM`` as
wrapping ``FT`` collateral with a CMT cap structure, which suggests ``FT`` is an
ARM code, but that is an inference from a different layout, not a definition.)
"""

from __future__ import annotations

from typing import Final, NamedTuple


class PoolType(NamedTuple):
    """One GNMA pool-type code.

    Attributes:
        name: Full description from Appendix III-6.
        family: Coarse structure class (the analytic grouping).
        is_arm: True for adjustable-rate pool types (instruction 8a margin codes).
    """

    name: str
    family: str
    is_arm: bool


#: Pool-type code -> (name, family, is_arm). Complete Appendix III-6 set, the
#: Platinum code space (forward + HMBS), and the one observed-but-undefined legacy
#: code.
GNMA_POOL_TYPES: Final[dict[str, PoolType]] = {
    # --- fixed single-family level-payment family ---
    "SF": PoolType("Single-Family Level Payment", "single_family", False),
    "FS": PoolType("Single-Family Level Payment (FHASecure Initiative)", "single_family", False),
    "ET": PoolType("Extended Term", "extended_term", False),
    "RG": PoolType("Reperforming Loans", "reperforming", False),
    "JM": PoolType("High Balance (Jumbo)", "high_balance", False),
    "BD": PoolType("Buydown", "buydown", False),
    # --- ARM family (instruction 8a: carry a security margin) ---
    "AR": PoolType("Adjustable Rate", "arm", True),
    "AQ": PoolType("Adjustable Rate", "arm", True),
    "AT": PoolType("Adjustable Rate", "arm", True),
    "AF": PoolType("Adjustable Rate", "arm", True),
    "AS": PoolType("Adjustable Rate", "arm", True),
    "AX": PoolType("Adjustable Rate", "arm", True),
    "RL": PoolType("Adjustable Rate", "arm", True),
    "QL": PoolType("Adjustable Rate", "arm", True),
    "TL": PoolType("Adjustable Rate", "arm", True),
    "FL": PoolType("Adjustable Rate", "arm", True),
    "FB": PoolType("Adjustable Rate", "arm", True),
    "SL": PoolType("Adjustable Rate", "arm", True),
    "XL": PoolType("Adjustable Rate", "arm", True),
    # --- manufactured housing ---
    "MH": PoolType("Manufactured Home", "manufactured", False),
    # --- graduated payment / growing equity ---
    "GP": PoolType("Graduated Payment", "graduated_payment", False),
    "GT": PoolType("Graduated Payment", "graduated_payment", False),
    "GA": PoolType("Growing Equity", "growing_equity", False),
    "GD": PoolType("Growing Equity", "growing_equity", False),
    # --- serial note ---
    "SN": PoolType("Serial Note", "serial_note", False),
    # --- project loans (Ginnie I) ---
    "PL": PoolType("Project Level Payment Loan", "project", False),
    "PN": PoolType("Project Nonlevel Payment Loan", "project", False),
    "LM": PoolType("Mature Project Loan", "project", False),
    "LS": PoolType("Small Project Loan", "project", False),
    "RX": PoolType("Project Mark-to-Market Loan", "project", False),
    # --- construction loans ---
    "CL": PoolType("Construction Loan", "construction", False),
    "CS": PoolType("Construction Loan - Split Interest Rate", "construction", False),
    # --- Platinum, fixed rate (Enhanced Platinum Disclosure, Appendix 1) ---
    "SP": PoolType("Platinum, 30-Year Single-Family", "platinum", False),
    "JP": PoolType("Platinum, 15-Year Single-Family", "platinum", False),
    "EP": PoolType("Platinum, 40-Year Single-Family", "platinum", False),
    "FP": PoolType("Platinum, 30-Year FHA Secure", "platinum", False),
    "AP": PoolType("Platinum, 30-Year Jumbo and Single-Family", "platinum", False),
    "BP": PoolType("Platinum, 15-Year Jumbo and Single-Family", "platinum", False),
    # --- Platinum, adjustable rate (same appendix; the cap/index variants) ---
    "WA": PoolType("Platinum ARM, 3-Year 1/5 cap CMT", "platinum", True),
    "WB": PoolType("Platinum ARM, 5-Year 1/5 cap CMT", "platinum", True),
    "WC": PoolType("Platinum ARM, 5-Year 2/6 cap CMT", "platinum", True),
    "WD": PoolType("Platinum ARM, 7-Year 2/6 cap CMT", "platinum", True),
    "WE": PoolType("Platinum ARM, 10-Year 2/6 cap CMT", "platinum", True),
    "WF": PoolType("Platinum ARM, 3-Year 1/5 cap LIBOR", "platinum", True),
    "WG": PoolType("Platinum ARM, 5-Year 1/5 cap LIBOR", "platinum", True),
    "WH": PoolType("Platinum ARM, 5-Year 2/6 cap LIBOR", "platinum", True),
    "WI": PoolType("Platinum ARM, 7-Year 2/6 cap LIBOR", "platinum", True),
    "WJ": PoolType("Platinum ARM, 10-Year 2/6 cap LIBOR", "platinum", True),
    "WK": PoolType("Platinum ARM, 1/5 cap CMT", "platinum", True),
    "WL": PoolType("Platinum ARM, 1/5 cap LIBOR", "platinum", True),
    "WM": PoolType("Platinum ARM, 2/6 cap CMT", "platinum", True),
    "WN": PoolType("Platinum ARM, 2/6 cap LIBOR", "platinum", True),
    # --- HMBS (HECM) Platinum (HMBS Platinum Portfolio layout, Appendix 1) ---
    "PA": PoolType("HMBS Platinum, 1-Year CMT HECM", "platinum", True),
    "PB": PoolType("HMBS Platinum, monthly CMT HECM", "platinum", True),
    "PC": PoolType("HMBS Platinum, 1-Year LIBOR HECM", "platinum", True),
    "PD": PoolType("HMBS Platinum, monthly LIBOR HECM", "platinum", True),
    "PE": PoolType("HMBS Platinum, 1-Year SOFR HECM", "platinum", True),
    "PF": PoolType("HMBS Platinum, fixed-rate HECM", "platinum", False),
    # --- observed in legacy nissues but not in current Appendix III-6 ---
    "FT": PoolType("Unknown/retired code", "other", False),
}

#: Issue-type (``Pool Indicator``) code -> human label (Appendix III-6 instr. 15).
GNMA_ISSUE_TYPES: Final[dict[str, str]] = {
    "X": "Ginnie I",
    "C": "Ginnie II Custom (single-issuer)",
    "M": "Ginnie II Multiple-Issuer",
}

#: Issue-type (``Pool Indicator``) -> program (Ginnie I vs II). X=I; C,M=II.
#:
#: The values are the lowercase ``program`` vocabulary the combined security- and
#: loan-grain masters share, so ``mbs_issuance`` and ``mbs_performance`` join on it
#: (``ginnie_i``, ``ginnie_ii``, ``ginnie_platinum``, ``gn_mega``, ``umbs``, ``mbs``).
GNMA_PROGRAM_BY_ISSUE_TYPE: Final[dict[str, str]] = {
    "X": "ginnie_i",
    "C": "ginnie_ii",
    "M": "ginnie_ii",
}
