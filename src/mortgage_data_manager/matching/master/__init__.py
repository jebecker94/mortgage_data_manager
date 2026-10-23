"""Master crosswalk: the HMDA spine across every matching workflow.

One row per post-2018 HMDA loan (``HMDAIndex``) that appears in at least one
matching edge, with nullable downstream ids, ``in_*`` coverage flags, provenance
columns and a ``channel``. Unlike the other ``matching/`` workflows this one
matches nothing -- it *assembles* the crosswalks the others produce, which is
why it carries no ``match_`` prefix.

The conventional edge is the GSE **chain UNION direct**: the ``hmda_gse`` chain
masters (HMDA -> FHFA -> MBS -> UMBS) plus the direct HMDA<->issuance matcher,
which reaches ``combined/loan_issuance`` without the FHFA leg. ``mbs_source``
records which path supplied each row, and because the direct path skips FHFA,
``in_mbs`` is NOT a subset of ``in_fhfa``.

Output: ``crosswalk/master/`` (hive-partitioned by ``activity_year``).

Two things to know before using it:

- **The denominator is the spine, not HMDA.** "% of spine" is not a HMDA-wide
  match rate -- left-join onto HMDA silver by ``HMDAIndex`` for real denominators.
- **Conventional edges stop at ``CONVENTIONAL_MAX_YEAR``** (2025; FHFA PUDB and
  ``combined/loan_issuance`` GSE coverage), so later years are right-censored.

Basis: ``docs/matching/master_crosswalk.md``.

Example:
    >>> from mortgage_data_manager.matching.master import load_master_crosswalk
    >>> spine = load_master_crosswalk(min_year=2021, columns=["in_mbs", "mbs_loan_id"])

Example - rebuild one year:
    >>> from mortgage_data_manager.matching.master import build_master_crosswalk, promote, run_qc
    >>> build_master_crosswalk(min_year=2024, max_year=2024, overwrite=True)
    >>> if run_qc()["passed"]:
    ...     promote(years=[2024])
"""

from __future__ import annotations

from mortgage_data_manager.matching.master.build import (
    build_master_crosswalk,
    load_master_crosswalk,
    promote,
    resolve_ownership_chain,
    resolve_security,
)
from mortgage_data_manager.matching.master.config import MasterCrosswalkConfig
from mortgage_data_manager.matching.master.qc import latest_qc, run_qc

__all__ = [
    "MasterCrosswalkConfig",
    "build_master_crosswalk",
    "latest_qc",
    "load_master_crosswalk",
    "promote",
    "resolve_ownership_chain",
    "resolve_security",
    "run_qc",
]
