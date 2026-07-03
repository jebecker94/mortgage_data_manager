"""Derived analytics over the mortgage/MBS datasets.

A top-level subpackage (sibling to ``matching`` and ``combined``) for metrics
computed on top of the silver/combined layers. Split by epistemic kind:

- :mod:`analytics.calculated` - deterministic, closed-form metrics on observed
  data (CPR/SMM, RPB aggregations, weighted averages, realized transition
  rates). Exact given the inputs.
- :mod:`analytics.estimated` - model-based estimates/forecasts of not-yet-
  observed quantities (prepayment/default probability, projected speeds,
  lifetime loss). Carry a model spec and uncertainty.

The deterministic metrics in :mod:`analytics.calculated`, the prepayment model
+ cross-fitting in :mod:`analytics.estimated`, and the loan-month panel/feature
builders (:mod:`analytics.panel`, :mod:`analytics.features`) productionize the
analyses previously explored ad hoc in ``investigations/`` (the SMM estimator
and the agency-MBS prepayment study). The macro inputs they consume live in
:mod:`core.reference.macro`.
"""

from mortgage_data_manager.analytics import (
    calculated,
    estimated,
    features,
    panel,
    servicer_names,
    servicing,
    transfers,
)
from mortgage_data_manager.analytics.config import AnalyticsConfig
from mortgage_data_manager.analytics.features import engineer_features
from mortgage_data_manager.analytics.panel import build_panel
from mortgage_data_manager.analytics.servicer_names import (
    add_servicer_parents,
    build_servicer_parent_crosswalk,
    normalize_servicer_name,
)
from mortgage_data_manager.analytics.servicing import (
    expected_servicing_pv,
    realized_servicing_pv,
)
from mortgage_data_manager.analytics.transfers import detect_servicer_transfers

__all__ = [
    "AnalyticsConfig",
    "calculated",
    "estimated",
    "features",
    "panel",
    "servicer_names",
    "servicing",
    "transfers",
    "build_panel",
    "engineer_features",
    "detect_servicer_transfers",
    "add_servicer_parents",
    "build_servicer_parent_crosswalk",
    "normalize_servicer_name",
    "expected_servicing_pv",
    "realized_servicing_pv",
]
