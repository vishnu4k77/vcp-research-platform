"""LightGBM breakout success predictor.

Trains a binary classifier on historical backtest outcomes:
  - Features: composite_score, stage2_days, mtf_score, pa_score,
              regime_score, signal quality flags, base geometry
  - Label: 1 if backtest trade hit the profit target (TARGET), 0 otherwise
  - Regime-aware: regime_score feature lets the model learn that the same
    VCP breakout in a BULL market has higher probability than in BEAR

Usage::

    # Training (one-time, run scripts/train_breakout_model.py)
    predictor = BreakoutPredictor()
    metrics = predictor.train(labeled_df)
    predictor.save(MLConfig.MODEL_DIR / MLConfig.BREAKOUT_MODEL_FILE)

    # Inference (daily, called by MLPipeline)
    predictor = BreakoutPredictor.load_default()
    if predictor:
        probs = predictor.predict(features_df)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from app.config.logging_config import get_logger
from app.config.strategy_config import MLConfig

logger = get_logger(__name__)


class BreakoutPredictor:
    """LightGBM classifier that estimates P(breakout hits profit target).

    Regime-aware: regime_score and market_status are included as features
    so the model learns bull / bear conditional probabilities from your
    own historical trade data.
    """

    def __init__(self) -> None:
        """Initialise with no model loaded — call train() or load()."""
        self._model = None          # lgb.LGBMClassifier, set after train/load
        self._feature_cols: list[str] = MLConfig.FEATURE_COLUMNS
        self._model_version: str = MLConfig.BREAKOUT_MODEL_FILE

    # ── Training ─────────────────────────────────────────────────────────────

    def train(self, df: pd.DataFrame) -> dict:
        """Train the LightGBM model on labeled backtest data.

        Args:
            df: DataFrame with columns = MLConfig.FEATURE_COLUMNS + "label"
                where label=1 means the backtest trade hit the profit target.

        Returns:
            Dict of evaluation metrics: accuracy, auc, precision, recall,
            n_train, n_test, feature_importance (top 5 by gain).

        Raises:
            ImportError: If lightgbm is not installed.
            ValueError: If fewer than MLConfig.MIN_TRAINING_SAMPLES rows are available.
        """
        try:
            import lightgbm as lgb
            from sklearn.metrics import accuracy_score, roc_auc_score, precision_score, recall_score
            from sklearn.model_selection import train_test_split
        except ImportError as exc:
            raise ImportError(
                "lightgbm and scikit-learn are required for ML training. "
                "Run: pip install lightgbm scikit-learn"
            ) from exc

        if len(df) < MLConfig.MIN_TRAINING_SAMPLES:
            raise ValueError(
                f"Only {len(df)} labeled samples — need at least "
                f"{MLConfig.MIN_TRAINING_SAMPLES} to train reliably."
            )

        available = [c for c in self._feature_cols if c in df.columns]
        missing = [c for c in self._feature_cols if c not in df.columns]
        if missing:
            logger.warning("Training: missing feature columns %s — they will be dropped", missing)

        X = df[available].copy()
        y = df["label"].astype(int)

        # Fill NaN with column median (LightGBM handles NaN natively but
        # sklearn metrics need clean arrays).
        X = X.fillna(X.median(numeric_only=True))

        X_train, X_test, y_train, y_test = train_test_split(
            X, y,
            test_size=MLConfig.TEST_SIZE,
            random_state=MLConfig.RANDOM_STATE,
            stratify=y,
        )

        model = lgb.LGBMClassifier(
            n_estimators=MLConfig.N_ESTIMATORS,
            max_depth=MLConfig.MAX_DEPTH,
            learning_rate=MLConfig.LEARNING_RATE,
            min_child_samples=MLConfig.MIN_CHILD_SAMPLES,
            subsample=MLConfig.SUBSAMPLE,
            colsample_bytree=MLConfig.COLSAMPLE_BYTREE,
            n_jobs=MLConfig.N_JOBS,
            random_state=MLConfig.RANDOM_STATE,
            verbosity=-1,
        )
        model.fit(
            X_train, y_train,
            eval_set=[(X_test, y_test)],
            callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(-1)],
        )

        self._model = model
        self._feature_cols = available

        y_pred = model.predict(X_test)
        y_prob = model.predict_proba(X_test)[:, 1]

        fi = pd.Series(
            model.feature_importances_,
            index=available,
        ).sort_values(ascending=False).head(5).to_dict()

        metrics = {
            "n_train":    len(X_train),
            "n_test":     len(X_test),
            "win_rate_train": float(y_train.mean()),
            "win_rate_test":  float(y_test.mean()),
            "accuracy":   float(accuracy_score(y_test, y_pred)),
            "auc":        float(roc_auc_score(y_test, y_prob)),
            "precision":  float(precision_score(y_test, y_pred, zero_division=0)),
            "recall":     float(recall_score(y_test, y_pred, zero_division=0)),
            "top5_features": fi,
        }
        logger.info(
            "BreakoutPredictor trained | n=%d | auc=%.3f | acc=%.1f%% | "
            "precision=%.1f%% | recall=%.1f%%",
            len(df), metrics["auc"],
            metrics["accuracy"] * 100,
            metrics["precision"] * 100,
            metrics["recall"] * 100,
        )
        return metrics

    # ── Inference ─────────────────────────────────────────────────────────────

    def predict(self, df: pd.DataFrame) -> pd.Series:
        """Return win probability (0.0 – 1.0) for each row in df.

        Args:
            df: DataFrame with at least MLConfig.FEATURE_COLUMNS as columns.
                Missing columns are filled with 0 (safe default).

        Returns:
            Series of float probabilities, same index as df.

        Raises:
            RuntimeError: If no model has been trained or loaded yet.
        """
        if self._model is None:
            raise RuntimeError(
                "No model loaded — call train() or load() first."
            )

        X = df.reindex(columns=self._feature_cols, fill_value=0.0).copy()
        X = X.fillna(0.0)

        probs = self._model.predict_proba(X)[:, 1]
        return pd.Series(probs, index=df.index, name=MLConfig.ML_SCORE_COLUMN)

    def feature_importance(self) -> pd.DataFrame:
        """Return feature importances sorted by gain (split count).

        Returns:
            DataFrame with columns [feature, importance] sorted descending.

        Raises:
            RuntimeError: If no model has been trained yet.
        """
        if self._model is None:
            raise RuntimeError("No model loaded.")
        return (
            pd.DataFrame({
                "feature":    self._feature_cols,
                "importance": self._model.feature_importances_,
            })
            .sort_values("importance", ascending=False)
            .reset_index(drop=True)
        )

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self, path: Path) -> None:
        """Serialize model + feature column list to disk via joblib.

        Args:
            path: Full file path for the .joblib file.

        Raises:
            RuntimeError: If no model has been trained yet.
        """
        if self._model is None:
            raise RuntimeError("No model to save — run train() first.")
        import joblib
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"model": self._model, "feature_cols": self._feature_cols}
        joblib.dump(payload, path)
        logger.info("BreakoutPredictor saved → %s", path)

    @classmethod
    def load(cls, path: Path) -> "BreakoutPredictor":
        """Load a previously saved model from disk.

        Args:
            path: Full file path of the .joblib file.

        Returns:
            Loaded BreakoutPredictor instance.

        Raises:
            FileNotFoundError: If the model file does not exist.
        """
        import joblib
        if not path.exists():
            raise FileNotFoundError(f"Model file not found: {path}")
        payload = joblib.load(path)
        instance = cls()
        instance._model = payload["model"]
        instance._feature_cols = payload["feature_cols"]
        logger.info("BreakoutPredictor loaded from %s", path)
        return instance

    @classmethod
    def load_default(cls) -> Optional["BreakoutPredictor"]:
        """Load from MLConfig default path — returns None if not yet trained.

        Designed for non-blocking pipeline use: callers should check for
        None and skip ML scoring gracefully.

        Returns:
            BreakoutPredictor instance or None when model file is absent.
        """
        path = MLConfig.MODEL_DIR / MLConfig.BREAKOUT_MODEL_FILE
        if not path.exists():
            logger.info(
                "ML model not found at %s — run scripts/train_breakout_model.py "
                "to train. MLPipeline will skip scoring until model is available.",
                path,
            )
            return None
        try:
            return cls.load(path)
        except Exception as exc:
            logger.warning("Failed to load ML model: %s", exc)
            return None
