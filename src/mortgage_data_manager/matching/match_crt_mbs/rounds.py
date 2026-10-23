"""The CRT-MBS matching rounds (FHLMC; the FNMA side reads deal membership, no matching).

Each deal runs the rounds in the order listed against its FPD-windowed SF origination pool,
and each round sees only the CRT loans and SF loans earlier rounds left unpinned. A round's
``match_round`` is its position in the tuple (1-based). To add a round, copy an entry,
change its fields and re-run; nothing else needs editing.

Every round joins exactly on its keys (harmonized column names, ``_load_crt_deal`` and
``_load_sf_window`` in ``matching_fhlmc.py``) and keeps the pairs that are 1:1 in both
directions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CrtRound:
    """One FHLMC round.

    Attributes:
        description: Label for the logs and ``info``.
        keys: Harmonized columns joined exactly.
    """

    description: str
    keys: tuple[str, ...]


#: Everything both files carry at origination and that stays stable.
KEYS_ALL: tuple[str, ...] = (
    "fpd",
    "maturity",
    "state",
    "zip3",
    "upb_k",
    "rate_m",
    "fico",
    "ltv",
    "cltv",
    "dti",
    "purpose",
    "prop_type",
    "occ",
    "units",
    "fthb",
)

ROUNDS_FHLMC: tuple[CrtRound, ...] = (
    CrtRound(description="all shared keys", keys=KEYS_ALL),
    # Drops the null-prone keys (DTI, first-time buyer, the purpose recodes) along with
    # property type, occupancy and units.
    CrtRound(
        description="stable core",
        keys=("fpd", "maturity", "state", "zip3", "upb_k", "rate_m", "fico", "ltv", "cltv"),
    ),
    # The sparse core, which still pinned 99.7%+ in the pilot.
    CrtRound(
        description="sparse core",
        keys=("fpd", "state", "zip3", "upb_k", "rate_m", "fico", "ltv"),
    ),
)
