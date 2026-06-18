"""MLPipeline — Step 11 of MasterPipeline.

Loads the trained BreakoutPredictor model and scores today's signal
candidates.  Writes ml_win_prob and ml_signal to stock_ml_scores.

Non-blocking: if the model file does not exist (not yet trained) or any
step fails, a warning is logged and the pipeline continues.  The scanner
will show NULL for ml_win_prob until training is done.

Run scripts/train_breakout_model.py once before the first daily run.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd
from sqlalchemy import text

from app.backtesting.backtest_service import BacktestService
from app.config.db import engine
from app.config.logging_config import get_logger
from app.config.strategy_config import MLConfig, StrategyConfig
from app.ml.breakout_predictor import BreakoutPredictor

logger = get_logger(__name__)

# Batch size for TRUNCATE + INSERT (avoids timeout on large universes)
_BATCH: int = MLConfig.ML_BATCH_SIZE


class MLPipeline:
    """Step 11 of MasterPipeline — daily ML score computation.

    Queries today's stock_signals candidates, runs the trained
    BreakoutPredictor model, and writes results to stock_ml_scores.
    """

    @classmethod
    def run(cls) -> None:
        """Score today's scanner candidates with the trained ML model.

        Raises:
            RuntimeError: Propagated only when no model exists — the caller
                (MasterPipeline) wraps this in try/except for non-blocking.
        """
        logger.info("MLPipeline: starting")

        # ── 1. Load model — skip gracefully if not trained yet ────────────────
        predictor = BreakoutPredictor.load_default()
        if predictor is None:
            logger.warning(
                "MLPipeline: model not found — skipping. "
                "Run scripts/train_breakout_model.py to train the model."
            )
            return

        # ── 2. Get latest trade_date ─────────────────────────────────────────
        _, trade_date = BacktestService.get_date_range()
        if trade_date is None:
            logger.warning("MLPipeline: no trade dates found in stock_signals — skipping")
            return

        # ── 3. Fetch today's features ─────────────────────────────────────────
        features = cls._get_today_features(trade_date)
        if features.empty:
            logger.warning("MLPipeline: no feature rows for %s — skipping", trade_date)
            return

        # ── 4. Predict ────────────────────────────────────────────────────────
        try:
            probs = predictor.predict(features)
        except Exception as exc:
            logger.error("MLPipeline: prediction failed: %s", exc)
            return

        features = features.copy()
        features[MLConfig.ML_SCORE_COLUMN]  = probs.values
        features[MLConfig.ML_SIGNAL_COLUMN] = (
            probs.values >= MLConfig.ML_SIGNAL_THRESHOLD
        ).astype(int)

        # ── 5. Write to DB ────────────────────────────────────────────────────
        cls._save(features, trade_date)

        logger.info(
            "MLPipeline: complete | date=%s | scored=%d | ml_signal=1: %d (≥%.0f%%)",
            trade_date,
            len(features),
            int(features[MLConfig.ML_SIGNAL_COLUMN].sum()),
            MLConfig.ML_SIGNAL_THRESHOLD * 100,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _get_today_features(trade_date: date) -> pd.DataFrame:
        """Fetch today's stock_signals rows with all ML feature columns.

        Args:
            trade_date: The date to score (latest trade_date in stock_signals).

        Returns:
            DataFrame with symbol, MLConfig.FEATURE_COLUMNS.
        """
        query = text("""
            SELECT
                ss.symbol,
                ss.composite_score,
                ISNULL(ss.stage2_days,              0)    AS stage2_days,
                CAST(ss.stage2_signal    AS INT)          AS stage2_signal,
                CAST(ss.trend_signal     AS INT)          AS trend_signal,
                CAST(ss.vcp_signal       AS INT)          AS vcp_signal,
                CAST(ss.rs_signal        AS INT)          AS rs_signal,
                CAST(ss.minervini_signal AS INT)          AS minervini_signal,
                CAST(ss.quality_signal   AS INT)          AS quality_signal,
                CAST(ss.liquidity_signal AS INT)          AS liquidity_signal,
                ISNULL(ss.distance_from_pivot_pct, 0.0)  AS distance_from_pivot_pct,
                ISNULL(ss.base_range_pct,          0.0)  AS base_range_pct,
                ISNULL(smtf.mtf_score,             0)    AS mtf_score,
                ISNULL(pas.pa_score,               0.0)  AS pa_score,
                ISNULL(mr.regime_score,            50.0) AS regime_score
            FROM stock_signals ss
            LEFT JOIN stock_mtf_signals smtf
                ON smtf.symbol = ss.symbol AND smtf.trade_date = ss.trade_date
            LEFT JOIN stock_pa_signals pas
                ON pas.symbol = ss.symbol AND pas.trade_date = ss.trade_date
            LEFT JOIN market_regime mr
                ON mr.trade_date = ss.trade_date
            WHERE ss.trade_date = :td
              AND ss.composite_score >= :min_score
            ORDER BY ss.composite_score DESC
        """)
        try:
            from app.config.db import nolock_connect
            with nolock_connect() as conn:
                df = pd.read_sql(query, conn, params={
                    "td":        trade_date,
                    "min_score": MLConfig.LABELER_MIN_COMPOSITE_SCORE,
                })
            logger.debug("MLPipeline._get_today_features: %d rows for %s", len(df), trade_date)
            return df
        except Exception as exc:
            logger.error("MLPipeline._get_today_features failed: %s", exc, exc_info=True)
            return pd.DataFrame()

    @staticmethod
    def _save(df: pd.DataFrame, trade_date: date) -> None:
        """Truncate today's ml_scores and insert new predictions.

        Args:
            df: DataFrame with symbol, ml_win_prob, ml_signal columns.
            trade_date: The date being written.
        """
        model_version = MLConfig.BREAKOUT_MODEL_FILE

        rows = [
            {
                "symbol":          str(row["symbol"]),
                "trade_date":      trade_date,
                "ml_win_prob":     float(row[MLConfig.ML_SCORE_COLUMN]),
                "ml_signal":       int(row[MLConfig.ML_SIGNAL_COLUMN]),
                "ml_model_version": model_version,
            }
            for _, row in df.iterrows()
        ]

        try:
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM stock_ml_scores WHERE trade_date = :td"),
                    {"td": trade_date},
                )
                for i in range(0, len(rows), _BATCH):
                    batch = rows[i: i + _BATCH]
                    conn.execute(
                        text("""
                            INSERT INTO stock_ml_scores
                                (symbol, trade_date, ml_win_prob, ml_signal, ml_model_version)
                            VALUES
                                (:symbol, :trade_date, :ml_win_prob, :ml_signal, :ml_model_version)
                        """),
                        batch,
                    )
            logger.debug("MLPipeline._save: %d rows written for %s", len(rows), trade_date)
        except Exception as exc:
            logger.error("MLPipeline._save failed: %s", exc, exc_info=True)
            raise
