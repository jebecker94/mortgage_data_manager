"""Model-based analytics: estimates and forecasts of future quantities.

Unlike :mod:`analytics.calculated` (closed-form metrics on observed data),
everything here requires a *model*. The implemented estimators target loan-level
**prepayment**, ported from the standalone SMM-estimator study:

- :func:`fit_model` — fit a logistic-regression or XGBoost prepayment model on a
  training panel and return a reusable fitted bundle.
- :func:`forecast_prepayment_probability` — apply a fitted bundle to a feature
  panel, producing the per-loan-month prepayment hazard (predicted SMM).
- :func:`project_prepayment_speeds` — aggregate predicted hazards into a
  cohort-by-age speed/survival curve.
- :func:`run_crossfit` — K-fold cross-fitted XGBoost predictions
  (Chernozhukov et al. 2018): each fold's model is trained on the other folds
  and predicts its held-out loans, yielding unbiased first-stage SMM for every
  loan-month.

:func:`forecast_default_probability` and :func:`estimate_lifetime_loss` remain
scaffolds — the source study modeled prepayment only; a default/loss model needs
the SF credit spine (zero-balance-code waterfall) which the analytics panel does
not yet carry.

ML dependencies (``xgboost``, ``statsmodels``) are in the ``[analytics]`` extra
and lazily imported, so importing this module never requires them.
"""

from __future__ import annotations

import gc
import json
import pickle
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from mortgage_data_manager.analytics.config import AnalyticsConfig
from mortgage_data_manager.analytics.schema import (
    BURNOUT_CUM,
    BURNOUT_X_INCENTIVE,
    COVID,
    DTI,
    DYNAMIC_LTV,
    FICO,
    HPA_4Q,
    INCENTIVE,
    LOAN_AGE,
    LOAN_ID,
    LOG_ORIG_UPB,
    MONTH_OF_YEAR,
    MONTHS_ITM,
    NUM_BORROWERS,
    NUM_UNITS,
    ORIG_DATE,
    ORIG_RATE,
    PERIOD,
    RATE_CHANGE_3M,
    SATO,
    SPLIT,
    UNEMP_RATE,
    VINTAGE_YEAR,
    YIELD_SPREAD,
    Y,
)
from mortgage_data_manager.core.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Feature sets (canonical lists shared by fitting + cross-fitting)
# ---------------------------------------------------------------------------
AGE_SPLINES = [f"age_s{i}" for i in range(1, 4)]
INCENTIVE_SPLINES = [f"incen_s{i}" for i in range(1, 5)]
FICO_SPLINES = [f"fico_s{i}" for i in range(1, 3)]
DLTV_SPLINES = [f"dltv_s{i}" for i in range(1, 3)]

LOAN_PURPOSE_DUMMIES = ["lp_R", "lp_C"]
PROPERTY_TYPE_DUMMIES = ["pt_CO", "pt_CP", "pt_MH", "pt_PU"]
OCCUPANCY_DUMMIES = ["occ_S", "occ_I"]
CHANNEL_DUMMIES = ["ch_B", "ch_C"]
MONTH_DUMMIES = [f"month_{m}" for m in range(2, 13)]
MACRO_FEATURES = [RATE_CHANGE_3M, YIELD_SPREAD, UNEMP_RATE, HPA_4Q]
TIME_FEATURES = [*MONTH_DUMMIES, COVID]

STATIC_FEATURES = [
    FICO,
    DTI,
    LOG_ORIG_UPB,
    NUM_BORROWERS,
    NUM_UNITS,
    *FICO_SPLINES,
    *LOAN_PURPOSE_DUMMIES,
    *PROPERTY_TYPE_DUMMIES,
    *OCCUPANCY_DUMMIES,
    *CHANNEL_DUMMIES,
    "agency_fhlmc",
]

#: The 29-feature set used by the XGBoost benchmark and cross-fitting.
XGB_FEATURES = [
    LOAN_AGE,
    INCENTIVE,
    BURNOUT_CUM,
    MONTHS_ITM,
    SATO,
    FICO,
    DTI,
    LOG_ORIG_UPB,
    NUM_BORROWERS,
    NUM_UNITS,
    DYNAMIC_LTV,
    COVID,
    *MACRO_FEATURES,
    *LOAN_PURPOSE_DUMMIES,
    *PROPERTY_TYPE_DUMMIES,
    *OCCUPANCY_DUMMIES,
    *CHANNEL_DUMMIES,
    "agency_fhlmc",
    MONTH_OF_YEAR,
    VINTAGE_YEAR,
]

#: The logistic baseline (spline incentive + burnout) feature set.
LOGIT_BASELINE_FEATURES = [
    LOAN_AGE,
    *AGE_SPLINES,
    INCENTIVE,
    *INCENTIVE_SPLINES,
    BURNOUT_CUM,
    BURNOUT_X_INCENTIVE,
    SATO,
    *STATIC_FEATURES,
    *MACRO_FEATURES,
    *TIME_FEATURES,
    DYNAMIC_LTV,
    *DLTV_SPLINES,
]

XGB_PARAMS = {
    "objective": "binary:logistic",
    "eval_metric": "auc",
    "max_depth": 6,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 100,
    "tree_method": "hist",
    "seed": 42,
}

# Cross-fitting constants.
K_FOLDS = 3
TRAIN_SUBSAMPLE_FRAC = 0.20
VAL_FRAC = 0.10
PREDICT_CHUNK_SIZE = 2_000_000
SEED = 42
LOGIT_SUBSAMPLE = 5_000_000


# ---------------------------------------------------------------------------
# Fitting helpers
# ---------------------------------------------------------------------------
def _prepare_data(
    df: pl.DataFrame,
    feature_cols: list[str],
    *,
    split: str | None = None,
    subsample: int | None = None,
    seed: int = SEED,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """Materialize (X, y, groups) arrays for a split, dropping absent features."""
    subset = df.filter(pl.col(SPLIT) == split) if split is not None else df
    if subsample and subset.height > subsample:
        subset = subset.sample(n=subsample, seed=seed)
    available = [c for c in feature_cols if c in subset.columns]
    missing = set(feature_cols) - set(available)
    if missing:
        logger.warning(f"Dropping absent features: {sorted(missing)}")
    X = np.nan_to_num(
        subset.select(available).to_numpy().astype(np.float64), nan=0.0, posinf=0.0, neginf=0.0
    )
    y = subset[Y].to_numpy().astype(np.float64)
    groups = subset[LOAN_ID].to_numpy()
    return X, y, groups, available


def _standardize(X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Zero-mean/unit-variance standardize columns; return (X_std, means, stds)."""
    means = np.nanmean(X, axis=0)
    stds = np.nanstd(X, axis=0)
    stds[stds == 0] = 1.0
    return (X - means) / stds, means, stds


def _fit_logistic(
    X: np.ndarray, y: np.ndarray, groups: np.ndarray, feature_names: list[str], name: str
) -> dict[str, Any]:
    """Fit a logistic regression with clustered/robust SEs (standardized features)."""
    import statsmodels.api as sm

    X_std, means, stds = _standardize(X)
    # Prepend the constant explicitly (sm.add_constant skips if it detects one).
    X_const = np.column_stack([np.ones(X_std.shape[0]), X_std])
    names = ["const", *feature_names]
    logger.info(f"Fitting {name}: {X_const.shape[1]} features, {len(y):,} obs")

    result = sm.Logit(y, X_const).fit(disp=0, maxiter=200, method="lbfgs")
    final = result
    try:
        final = result.get_robustcov_results(cov_type="cluster", groups=groups)
    except Exception:  # noqa: BLE001 - degrade to robust, then default SEs
        try:
            final = result.get_robustcov_results(cov_type="HC1")
        except Exception:  # noqa: BLE001
            logger.warning("Robust SEs unavailable; using default SEs")
    return {
        "kind": "logit",
        "name": name,
        "result": final,
        "feature_names": names,
        "feature_list": feature_names,
        "converged": bool(result.mle_retvals["converged"]),
        "_std_means": means,
        "_std_stds": stds,
    }


def _fit_xgboost(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    feature_names: list[str],
    *,
    params: dict[str, Any] | None = None,
    num_boost_round: int = 500,
) -> dict[str, Any]:
    """Fit an XGBoost prepayment model with early stopping on a validation split."""
    import xgboost as xgb

    params = params or XGB_PARAMS
    dtrain = xgb.DMatrix(X_train, label=y_train, feature_names=feature_names)
    dval = xgb.DMatrix(X_val, label=y_val, feature_names=feature_names)
    del X_train, y_train, X_val, y_val
    gc.collect()
    booster = xgb.train(
        params,
        dtrain,
        num_boost_round=num_boost_round,
        evals=[(dtrain, "train"), (dval, "val")],
        early_stopping_rounds=50,
        verbose_eval=False,
    )
    out = {
        "kind": "xgboost",
        "name": "xgboost",
        "model": booster,
        "feature_names": feature_names,
        "feature_list": feature_names,
        "best_iteration": booster.best_iteration,
        "best_score": booster.best_score,
    }
    del dtrain, dval
    gc.collect()
    return out


def fit_model(panel: pl.LazyFrame, *, target: str = Y, spec: dict[str, Any] | None = None) -> Any:
    """Fit a prepayment model on a training panel and return the fitted bundle.

    Args:
        panel: Feature panel (features + realized outcome ``target`` + ``split``).
            A LazyFrame or DataFrame is accepted.
        target: Outcome column name (default ``"y"``, voluntary prepayment).
        spec: Model specification. Recognized keys:
            ``model`` (``"xgboost"`` | ``"logit"``, default ``"xgboost"``),
            ``features`` (feature list; defaults to :data:`XGB_FEATURES` or
            :data:`LOGIT_BASELINE_FEATURES`), ``subsample`` (training row cap),
            ``val_frac`` (XGBoost validation share), ``seed``.

    Returns:
        A fitted bundle dict (``kind``, the fitted object, ``feature_list``,
        and standardization stats for logit) consumable by
        :func:`forecast_prepayment_probability`.

    Raises:
        ValueError: If ``spec['model']`` is not recognized.
    """
    spec = dict(spec or {})
    kind = spec.get("model", "xgboost")
    seed = spec.get("seed", SEED)
    df = panel.collect() if isinstance(panel, pl.LazyFrame) else panel
    if target != Y:
        df = df.rename({target: Y})

    if kind == "logit":
        features = spec.get("features", LOGIT_BASELINE_FEATURES)
        vintage_dummies = [c for c in df.columns if c.startswith("vint_")]
        features = [*features, *vintage_dummies]
        X, y, groups, available = _prepare_data(
            df, features, split="train", subsample=spec.get("subsample", LOGIT_SUBSAMPLE), seed=seed
        )
        return _fit_logistic(X, y, groups, available, spec.get("name", "logit_baseline"))

    if kind == "xgboost":
        features = spec.get("features", XGB_FEATURES)
        X, y, _, available = _prepare_data(
            df, features, split="train", subsample=spec.get("subsample"), seed=seed
        )
        val_frac = spec.get("val_frac", VAL_FRAC)
        rng = np.random.default_rng(seed)
        perm = rng.permutation(len(y))
        n_val = int(len(y) * val_frac)
        val_idx, train_idx = perm[:n_val], perm[n_val:]
        return _fit_xgboost(
            X[train_idx],
            y[train_idx],
            X[val_idx],
            y[val_idx],
            available,
            params=spec.get("params"),
        )

    raise ValueError(f"Unknown model kind {kind!r}; expected 'xgboost' or 'logit'")


def save_model(bundle: dict[str, Any], path: Path) -> Path:
    """Pickle a fitted model bundle to ``path`` (XGBoost is pickle-safe)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(bundle, f)
    logger.info(f"Saved {bundle.get('kind')} model -> {path}")
    return path


def _resolve_bundle(model: Any) -> dict[str, Any]:
    """Coerce a model argument (bundle, path, or str key) into a fitted bundle."""
    if isinstance(model, dict):
        return model
    if isinstance(model, (str, Path)):
        path = Path(model)
        if not path.suffix:
            path = AnalyticsConfig.ANALYTICS_MODELS_DIR / f"{model}.pkl"
        with open(path, "rb") as f:
            return pickle.load(f)
    raise TypeError(f"Cannot resolve model from {type(model)!r}")


def _predict_bundle(bundle: dict[str, Any], df: pl.DataFrame) -> np.ndarray:
    """Predict P(prepay) for every row of ``df`` from a fitted bundle."""
    features = bundle["feature_list"]
    available = [c for c in features if c in df.columns]
    X = np.nan_to_num(
        df.select(available).to_numpy().astype(np.float64), nan=0.0, posinf=0.0, neginf=0.0
    )
    if bundle["kind"] == "logit":
        X_std = (X - bundle["_std_means"]) / bundle["_std_stds"]
        X_const = np.column_stack([np.ones(X_std.shape[0]), X_std])
        return np.asarray(bundle["result"].predict(X_const))
    import xgboost as xgb

    dmat = xgb.DMatrix(X, feature_names=available)
    return np.asarray(bundle["model"].predict(dmat))


# ---------------------------------------------------------------------------
# Forecasting
# ---------------------------------------------------------------------------
def forecast_prepayment_probability(
    panel: pl.LazyFrame,
    *,
    horizon_months: int = 12,
    model: Any = "baseline",
) -> pl.LazyFrame:
    """Estimate the per-loan-month prepayment hazard (predicted SMM).

    Applies a fitted model to a feature panel, producing the one-month
    voluntary-prepayment probability for every loan-month — the building block
    of any multi-month projection (see :func:`project_prepayment_speeds`).

    Args:
        panel: Feature panel (the output of :func:`analytics.features`).
        horizon_months: Accepted for API stability; the baseline returns the
            one-month hazard rather than a per-future-month vector.
        model: A fitted bundle, a pickle path, or a model-key string resolved
            under ``AnalyticsConfig.ANALYTICS_MODELS_DIR``.

    Returns:
        A LazyFrame of ``loan_id``, ``period``, ``predicted_smm``.
    """
    del horizon_months  # one-month hazard; multi-horizon projection is separate
    bundle = _resolve_bundle(model)
    df = panel.collect() if isinstance(panel, pl.LazyFrame) else panel
    preds = _predict_bundle(bundle, df)
    return df.select([LOAN_ID, PERIOD]).with_columns(pl.Series("predicted_smm", preds)).lazy()


def project_prepayment_speeds(
    cohorts: pl.LazyFrame,
    *,
    horizon_months: int = 360,
    model: Any = "baseline",
    cohort_col: str = "orig_quarter",
) -> pl.LazyFrame:
    """Project a cohort-by-age prepayment-speed and survival curve.

    Predicts the per-loan-month hazard, then aggregates balance-unweighted mean
    SMM by ``(cohort, loan_age)`` and accumulates survival as
    ``S(age) = prod(1 - mean_SMM)`` — averaging hazards across loans observed at
    each age (not per-loan survival curves), as cohort composition attrits.

    Args:
        cohorts: Feature panel (must carry ``loan_age`` and the cohort column).
        horizon_months: Maximum loan age retained in the curve.
        model: Fitted bundle / path / key (see
            :func:`forecast_prepayment_probability`).
        cohort_col: Cohort grouping column (default ``orig_quarter``).

    Returns:
        A LazyFrame of ``cohort``, ``loan_age``, ``mean_smm``, ``cpr``,
        ``survival`` sorted within cohort by age.
    """
    bundle = _resolve_bundle(model)
    df = cohorts.collect() if isinstance(cohorts, pl.LazyFrame) else cohorts
    preds = _predict_bundle(bundle, df)
    hazard = (
        df.select([pl.col(cohort_col).alias("cohort"), pl.col(LOAN_AGE).alias("loan_age")])
        .with_columns(pl.Series("predicted_smm", preds))
        .filter(pl.col("loan_age") <= horizon_months)
        .group_by(["cohort", "loan_age"])
        .agg(pl.col("predicted_smm").mean().alias("mean_smm"))
        .sort(["cohort", "loan_age"])
    )
    return hazard.with_columns(
        (1.0 - (1.0 - pl.col("mean_smm")) ** 12).alias("cpr"),
        (1.0 - pl.col("mean_smm")).cum_prod().over("cohort").alias("survival"),
    )


def forecast_default_probability(
    panel: pl.LazyFrame, *, horizon_months: int = 12, model: str = "baseline"
) -> pl.LazyFrame:
    """Estimate forward default (credit-event) probability per loan/pool.

    Scaffold: the ported study modeled prepayment only. A default model needs
    the SF credit spine (zero-balance-code waterfall distinguishing voluntary
    prepay from credit events), which the analytics panel does not yet carry.

    Args:
        panel: Loan/pool feature panel.
        horizon_months: Months ahead to project.
        model: Model spec identifier.

    Returns:
        A LazyFrame of per-entity, per-future-month default probabilities.

    Raises:
        NotImplementedError: Placeholder pending a credit-event panel.
    """
    raise NotImplementedError(
        "forecast_default_probability needs the SF credit-event panel (not yet wired)."
    )


def estimate_lifetime_loss(panel: pl.LazyFrame, *, model: str = "baseline") -> pl.LazyFrame:
    """Estimate lifetime credit loss (PD x LGD x EAD) per loan/pool.

    Scaffold: depends on :func:`forecast_default_probability` plus LGD/EAD
    inputs that the analytics panel does not yet carry.

    Args:
        panel: Loan/pool feature panel.
        model: Model spec identifier.

    Returns:
        A LazyFrame of per-entity lifetime-loss estimates.

    Raises:
        NotImplementedError: Placeholder pending a credit-loss panel.
    """
    raise NotImplementedError(
        "estimate_lifetime_loss depends on a default/LGD model that is not yet built."
    )


# ---------------------------------------------------------------------------
# Cross-fitting (Chernozhukov et al. 2018)
# ---------------------------------------------------------------------------
def assign_folds(
    features_path: Path, output_path: Path, *, k: int = K_FOLDS, seed: int = SEED
) -> pl.DataFrame:
    """Assign each unique loan to a fold, stratified by origination quarter x coupon.

    Args:
        features_path: Feature-matrix parquet.
        output_path: Destination for the fold-assignment parquet.
        k: Number of folds.
        seed: RNG seed.

    Returns:
        The fold-assignment DataFrame (``loan_id``, stratum cols, ``fold``).
    """
    loans = (
        pl.scan_parquet(features_path)
        .select(LOAN_ID, ORIG_DATE, ORIG_RATE)
        .unique(subset=[LOAN_ID])
        .collect()
    )
    loans = loans.with_columns(
        (
            pl.col(ORIG_DATE).dt.year().cast(pl.String)
            + "Q"
            + pl.col(ORIG_DATE).dt.quarter().cast(pl.String)
        ).alias("orig_quarter"),
        (pl.col(ORIG_RATE) / 0.50).floor().cast(pl.Int32).alias("coupon_bucket"),
    )
    loans = loans.with_columns(
        (pl.col("orig_quarter") + "_" + pl.col("coupon_bucket").cast(pl.String)).alias("_stratum")
    ).sort(["_stratum", LOAN_ID])

    rng = np.random.default_rng(seed)
    fold_arrays = [
        rng.permutation(part.height) % k
        for part in loans.partition_by("_stratum", maintain_order=True)
    ]
    loans = loans.with_columns(pl.Series("fold", np.concatenate(fold_arrays).astype(np.int8))).drop(
        "_stratum"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    loans.write_parquet(output_path)
    for fk in range(k):
        logger.info(f"Fold {fk}: {int((loans['fold'] == fk).sum()):,} loans")
    return loans


def _train_fold(
    features_path: Path,
    fold_assignments: pl.DataFrame,
    fold_k: int,
    output_dir: Path,
    *,
    subsample_frac: float,
    seed: int,
) -> None:
    """Train one fold's XGBoost model on loans outside ``fold_k`` (subsampled)."""
    import xgboost as xgb

    output_dir.mkdir(parents=True, exist_ok=True)
    train_loan_ids = fold_assignments.filter(pl.col("fold") != fold_k).get_column(LOAN_ID).to_list()
    rng = np.random.default_rng(seed + fold_k)
    sampled = rng.choice(
        train_loan_ids, size=int(len(train_loan_ids) * subsample_frac), replace=False
    ).tolist()

    rng_split = np.random.default_rng(seed + fold_k + 1000)
    perm = rng_split.permutation(len(sampled))
    n_val = int(len(sampled) * VAL_FRAC)
    val_ids = {sampled[i] for i in perm[:n_val]}
    train_ids = {sampled[i] for i in perm[n_val:]}

    cols_needed = list(dict.fromkeys([LOAN_ID, *XGB_FEATURES, Y]))
    df = (
        pl.scan_parquet(features_path)
        .select(cols_needed)
        .filter(pl.col(LOAN_ID).is_in(set(sampled)))
        .collect()
    )
    feature_cols = [c for c in XGB_FEATURES if c in df.columns]
    df_train = df.filter(pl.col(LOAN_ID).is_in(train_ids))
    df_val = df.filter(pl.col(LOAN_ID).is_in(val_ids))
    del df
    gc.collect()

    def _arrays(d: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        X = np.nan_to_num(
            d.select(feature_cols).to_numpy().astype(np.float32), nan=0.0, posinf=0.0, neginf=0.0
        )
        return X, d.get_column(Y).to_numpy().astype(np.float32)

    X_train, y_train = _arrays(df_train)
    X_val, y_val = _arrays(df_val)
    n_train, n_val_rows = X_train.shape[0], X_val.shape[0]
    del df_train, df_val
    gc.collect()

    dtrain = xgb.DMatrix(X_train, label=y_train, feature_names=feature_cols)
    dval = xgb.DMatrix(X_val, label=y_val, feature_names=feature_cols)
    del X_train, y_train, X_val, y_val
    gc.collect()
    booster = xgb.train(
        XGB_PARAMS,
        dtrain,
        num_boost_round=500,
        evals=[(dtrain, "train"), (dval, "val")],
        early_stopping_rounds=50,
        verbose_eval=False,
    )
    importance = booster.get_score(importance_type="gain")
    booster.save_model(str(output_dir / "model.json"))
    metadata = {
        "fold": fold_k,
        "best_iteration": booster.best_iteration,
        "best_score": booster.best_score,
        "n_train_loans": len(train_ids),
        "n_val_loans": len(val_ids),
        "n_train_rows": n_train,
        "n_val_rows": n_val_rows,
        "subsample_frac": subsample_frac,
        "feature_names": feature_cols,
        "feature_importance_gain": importance,
    }
    with open(output_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)
    logger.info(
        f"Fold {fold_k}: val_auc={booster.best_score:.4f} (best_iter={booster.best_iteration})"
    )
    del dtrain, dval, booster
    gc.collect()


def _predict_fold(
    features_path: Path,
    fold_assignments: pl.DataFrame,
    fold_k: int,
    model_path: Path,
    output_path: Path,
    *,
    chunk_size: int = PREDICT_CHUNK_SIZE,
) -> None:
    """Predict every held-out loan-month for ``fold_k`` and write its parquet."""
    import xgboost as xgb

    booster = xgb.Booster()
    booster.load_model(str(model_path))
    held_out = set(fold_assignments.filter(pl.col("fold") == fold_k).get_column(LOAN_ID).to_list())
    cols_needed = list(dict.fromkeys([LOAN_ID, PERIOD, *XGB_FEATURES]))
    df = (
        pl.scan_parquet(features_path)
        .select(cols_needed)
        .filter(pl.col(LOAN_ID).is_in(held_out))
        .collect()
    )
    n_rows = df.height
    feature_cols = [c for c in XGB_FEATURES if c in df.columns]
    preds = np.empty(n_rows, dtype=np.float32)
    for start in range(0, n_rows, chunk_size):
        end = min(start + chunk_size, n_rows)
        chunk = df.slice(start, end - start)
        X = np.nan_to_num(
            chunk.select(feature_cols).to_numpy().astype(np.float32),
            nan=0.0,
            posinf=0.0,
            neginf=0.0,
        )
        preds[start:end] = booster.predict(xgb.DMatrix(X, feature_names=feature_cols))
        del X, chunk
        gc.collect()
    result = df.select([LOAN_ID, PERIOD]).with_columns(
        pl.Series("predicted_smm", preds), pl.lit(fold_k).cast(pl.Int8).alias("fold")
    )
    tmp = output_path.with_suffix(".tmp")
    result.write_parquet(tmp)
    tmp.rename(output_path)
    logger.info(f"Fold {fold_k}: predicted {n_rows:,} loan-months")
    del df, preds, result
    gc.collect()


def _assemble_predictions(crossfit_dir: Path, k: int = K_FOLDS) -> Path:
    """Concatenate per-fold predictions, asserting no duplicate (loan, period)."""
    parts = [
        pl.read_parquet(crossfit_dir / f"fold_{fk}" / "predictions.parquet") for fk in range(k)
    ]
    combined = pl.concat(parts, how="diagonal_relaxed")
    n_unique = combined.select(pl.struct(LOAN_ID, PERIOD)).n_unique()
    if combined.height != n_unique:
        raise ValueError(
            f"Duplicate (loan_id, period): {combined.height:,} rows, {n_unique:,} unique"
        )
    out = crossfit_dir / "crossfit_predictions.parquet"
    combined.write_parquet(out)
    logger.info(f"Assembled {combined.height:,} cross-fit predictions -> {out}")
    return out


def _build_diagnostics(crossfit_dir: Path, k: int = K_FOLDS) -> None:
    """Aggregate per-fold XGBoost feature importance into a diagnostics parquet."""
    importances = []
    for fk in range(k):
        with open(crossfit_dir / f"fold_{fk}" / "metadata.json") as f:
            importances.append(json.load(f)["feature_importance_gain"])
    all_features = sorted(set().union(*(d.keys() for d in importances)))
    rows = []
    for feat in all_features:
        gains = [d.get(feat, 0.0) for d in importances]
        mean_g = float(np.mean(gains))
        std_g = float(np.std(gains))
        row = {"feature": feat, "mean_gain": mean_g, "std_gain": std_g}
        for fk, g in enumerate(gains):
            row[f"fold_{fk}_gain"] = g
        rows.append(row)
    pl.DataFrame(rows).sort("mean_gain", descending=True).write_parquet(
        crossfit_dir / "diagnostics.parquet"
    )


def run_crossfit(
    *,
    features_path: Path | None = None,
    crossfit_dir: Path | None = None,
    k: int = K_FOLDS,
    subsample_frac: float = TRAIN_SUBSAMPLE_FRAC,
    seed: int = SEED,
) -> Path:
    """Run the resumable K-fold cross-fitting pipeline end to end.

    Steps: assign folds -> (per fold) train + predict held-out -> assemble ->
    diagnostics. Any step whose output already exists is skipped, so a partial
    run resumes cleanly.

    Args:
        features_path: Feature-matrix parquet. Defaults to ``features.parquet``
            under ``AnalyticsConfig.ANALYTICS_PANEL_DIR``.
        crossfit_dir: Output directory. Defaults to
            ``AnalyticsConfig.ANALYTICS_CROSSFIT_DIR``.
        k: Number of folds.
        subsample_frac: Fraction of training loans sampled per fold.
        seed: RNG seed.

    Returns:
        Path to the assembled ``crossfit_predictions.parquet``.

    Raises:
        FileNotFoundError: If the feature matrix does not exist.
    """
    features_path = features_path or (AnalyticsConfig.ANALYTICS_PANEL_DIR / "features.parquet")
    crossfit_dir = crossfit_dir or AnalyticsConfig.ANALYTICS_CROSSFIT_DIR
    if not features_path.exists():
        raise FileNotFoundError(f"{features_path} not found. Build features first.")
    crossfit_dir.mkdir(parents=True, exist_ok=True)

    fold_path = crossfit_dir / "fold_assignments.parquet"
    fold_assignments = (
        pl.read_parquet(fold_path)
        if fold_path.exists()
        else assign_folds(features_path, fold_path, k=k, seed=seed)
    )

    for fk in range(k):
        fold_dir = crossfit_dir / f"fold_{fk}"
        model_path = fold_dir / "model.json"
        pred_path = fold_dir / "predictions.parquet"
        if pred_path.exists():
            continue
        if not model_path.exists():
            _train_fold(
                features_path,
                fold_assignments,
                fk,
                fold_dir,
                subsample_frac=subsample_frac,
                seed=seed,
            )
        _predict_fold(features_path, fold_assignments, fk, model_path, pred_path)

    assembled = crossfit_dir / "crossfit_predictions.parquet"
    if not assembled.exists():
        _assemble_predictions(crossfit_dir, k=k)
    _build_diagnostics(crossfit_dir, k=k)
    logger.info("Cross-fitting complete.")
    return assembled
