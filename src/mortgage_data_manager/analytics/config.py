"""Configuration for the analytics subpackage.

Analytics consumes silver/combined datasets and produces derived metrics. Two
kinds, split across modules:

- :mod:`analytics.calculated` - deterministic, closed-form metrics computed
  directly from observed data (CPR/SMM, RPB, factors, WALA/WARM, realized
  delinquency-transition rates). No model, no forecast — given the inputs, the
  output is exact.
- :mod:`analytics.estimated` - forward-looking quantities that require a model
  to estimate/forecast (prepayment probability, default/loss projections,
  speed forecasts). These carry parameter/specification choices and
  uncertainty.

Outputs (when persisted) land under ``data/analytics/``.
"""

from __future__ import annotations

from pathlib import Path

from decouple import config

from mortgage_data_manager.core.config import MortgageDataConfig


class AnalyticsConfig(MortgageDataConfig):
    """Configuration for derived analytics outputs."""

    SUBPACKAGE: str = "analytics"

    ANALYTICS_DATA_DIR: Path = Path(
        config(
            "ANALYTICS_DATA_DIR",
            default=str(MortgageDataConfig.get_subpackage_data_dir("analytics")),
        )
    )
    #: Deterministic metric outputs.
    ANALYTICS_CALCULATED_DIR: Path = Path(
        config("ANALYTICS_CALCULATED_DIR", default=str(ANALYTICS_DATA_DIR / "calculated"))
    )
    #: Model-based estimate/forecast outputs.
    ANALYTICS_ESTIMATED_DIR: Path = Path(
        config("ANALYTICS_ESTIMATED_DIR", default=str(ANALYTICS_DATA_DIR / "estimated"))
    )
    #: Detected MSR servicer/issuer transfer event tables (deterministic, so
    #: nested under the calculated outputs).
    ANALYTICS_TRANSFERS_DIR: Path = Path(
        config("ANALYTICS_TRANSFERS_DIR", default=str(ANALYTICS_CALCULATED_DIR / "transfers"))
    )
    #: Harmonized loan-month panel + engineered feature matrix.
    ANALYTICS_PANEL_DIR: Path = Path(
        config("ANALYTICS_PANEL_DIR", default=str(ANALYTICS_DATA_DIR / "panel"))
    )
    #: Fitted-model artifacts (logit/XGBoost pickles, native XGBoost JSON).
    ANALYTICS_MODELS_DIR: Path = Path(
        config("ANALYTICS_MODELS_DIR", default=str(ANALYTICS_DATA_DIR / "models"))
    )
    #: Cross-fitted first-stage prediction outputs.
    ANALYTICS_CROSSFIT_DIR: Path = Path(
        config("ANALYTICS_CROSSFIT_DIR", default=str(ANALYTICS_ESTIMATED_DIR / "crossfit"))
    )

    #: Default fraction of unique loans sampled when building the panel.
    PANEL_SAMPLE_FRAC: float = float(config("ANALYTICS_PANEL_SAMPLE_FRAC", default="0.10"))
    #: Default temporal train/test cutoff (period <= cutoff is train).
    PANEL_TRAIN_CUTOFF: str = config("ANALYTICS_PANEL_TRAIN_CUTOFF", default="2022-12-31")

    @classmethod
    def ensure_directories(cls) -> None:
        """Create analytics output directories if they don't exist."""
        for d in (
            cls.ANALYTICS_CALCULATED_DIR,
            cls.ANALYTICS_ESTIMATED_DIR,
            cls.ANALYTICS_TRANSFERS_DIR,
            cls.ANALYTICS_PANEL_DIR,
            cls.ANALYTICS_MODELS_DIR,
        ):
            d.mkdir(parents=True, exist_ok=True)
