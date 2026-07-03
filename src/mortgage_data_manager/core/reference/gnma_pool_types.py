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

Codes observed in our data but NOT defined in the current Appendix III-6
(retired/legacy variants): ``SP``, ``JP``, ``FT`` — mapped to ``other`` until
confirmed against an older MBS Guide vintage.
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


#: Pool-type code -> (name, family, is_arm). Complete Appendix III-6 set plus the
#: three observed-but-undefined legacy codes.
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
    # --- observed in legacy nissues but not in current Appendix III-6 ---
    "SP": PoolType("Unknown/retired code", "other", False),
    "JP": PoolType("Unknown/retired code", "other", False),
    "FT": PoolType("Unknown/retired code", "other", False),
}

#: Issue-type (``Pool Indicator``) code -> human label (Appendix III-6 instr. 15).
GNMA_ISSUE_TYPES: Final[dict[str, str]] = {
    "X": "Ginnie I",
    "C": "Ginnie II Custom (single-issuer)",
    "M": "Ginnie II Multiple-Issuer",
}

#: Issue-type (``Pool Indicator``) -> program (Ginnie I vs II). X=I; C,M=II.
GNMA_PROGRAM_BY_ISSUE_TYPE: Final[dict[str, str]] = {
    "X": "GNMA_I",
    "C": "GNMA_II",
    "M": "GNMA_II",
}


def pool_type_family(code: str | None) -> str:
    """Return the coarse family for a GNMA pool-type code (``other`` if unknown).

    Args:
        code: 2-letter pool-type code (case-insensitive; whitespace stripped).

    Returns:
        The family string, or ``"other"`` for null/unrecognized codes.
    """
    if not code:
        return "other"
    pt = GNMA_POOL_TYPES.get(code.strip().upper())
    return pt.family if pt else "other"


def pool_type_is_arm(code: str | None) -> bool:
    """Return True if the pool-type code is an adjustable-rate (ARM) family code.

    Args:
        code: 2-letter pool-type code (case-insensitive; whitespace stripped).

    Returns:
        True for ARM pool types, else False (including null/unknown).
    """
    if not code:
        return False
    pt = GNMA_POOL_TYPES.get(code.strip().upper())
    return bool(pt and pt.is_arm)


def issue_type_to_program(pool_indicator: str | None) -> str | None:
    """Map a GNMA ``Pool Indicator`` (X/C/M) to program (``GNMA_I``/``GNMA_II``).

    Args:
        pool_indicator: ``X`` (Ginnie I), ``C`` / ``M`` (Ginnie II), case-insensitive.

    Returns:
        ``"GNMA_I"`` / ``"GNMA_II"``, or ``None`` for null/unrecognized values.
    """
    if not pool_indicator:
        return None
    return GNMA_PROGRAM_BY_ISSUE_TYPE.get(pool_indicator.strip().upper())
