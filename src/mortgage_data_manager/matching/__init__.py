"""Matching workflows for linking records across mortgage datasets.

One subpackage per pairwise edge, named HMDA first and the MBS/UMBS side last
(``match_hmda_fha``, ``match_hmda_fhfa``, ``match_hmda_fhlb``, ``match_hmda_gse``,
``match_hmda_gnma``, ``match_hmda_sellers_purchasers``, ``match_fhfa_mbs``,
``match_fhlb_mbs``, ``match_crt_mbs``, ``match_mbs_umbs``), plus ``master``, which
assembles them into the HMDA spine. Each has ``config.py``, its matching modules
(``matching*.py``), ``validation.py`` where it validates, and a flat ``cli.py``.
"""

from __future__ import annotations

from mortgage_data_manager.matching.config import MatchingConfig

__all__ = ["MatchingConfig"]
