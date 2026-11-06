"""Per-source data pipelines: one subpackage per public data source.

Each subpackage takes its source through the medallion layers (raw → bronze →
silver) with the same modules every time: ``config``, ``download``,
``import_bronze``, ``import_silver``, ``pipeline`` and ``cli``. Import from the
subpackage itself, e.g. ``from mortgage_data_manager.sources.hmda import HMDAConfig``;
nothing is imported here, so loading one source never loads the others.
"""

from __future__ import annotations

__all__ = [
    "fha",
    "fhfa",
    "fhlb",
    "fhlmc",
    "fnma",
    "gnma",
    "hmda",
    "umbs",
    "usps_crosswalk",
]
