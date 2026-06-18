"""Backtest labeler — generates labeled training data for the ML model.

Joins BacktestEngine trade outcomes with rich feature data to produce the
(X, y) DataFrame required by BreakoutPredictor.train().

Flow:
  1. Run BacktestEngine.run() over the full history → trades with exit_reason
  2. Fetch feature-rich entry data via BacktestService.get_entry_features_for_training()
  3. Inner-join on (symbol, entry_date) to align features ↔ outcomes
  4. Label: 1 if exit_reason == MLConfig.WIN_EXIT_REASON ("TARGET"), 0 otherwise

The labeler uses the same stop% / target% / hold days as defined in MLConfig
so that training labels are consistent with the live backtester.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

from app.backtesting.backtest_engine import BacktestEngine
from app.backtesting.backtest_service import BacktestService
from app.config.logging_config import get_logger
from app.config.strategy_config import BacktestConfig, MLConfig

logger = get_logger(__name__)


class BacktestLabeler:
    """Generates labeled (features, label) training data for BreakoutPredictor.

    Regime-aware: regime_score is included as a feature so the model learns
    that the same VCP setup has different win probabilities in BULL vs BEAR.
    """

    @staticmethod
    def generate(
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        entry_signal: str = MLConfig.LABELER_SIGNAL,
        stop_loss_pct: float = MLConfig.LABELER_STOP_PCT,
        target_pct: float = MLConfig.LABELER_TARGET_PCT,
        max_holding_days: int = MLConfig.LABELER_MAX_HOLDING_DAYS,
        min_composite_score: float = MLConfig.LABELER_MIN_COMPOSITE_SCORE,
    ) -> pd.DataFrame:
        """Build labeled training DataFrame from historical backtest.

        Args:
            start_date: First date to include (defaults to earliest in DB).
            end_date: Last date to include (defaults to latest in DB).
            entry_signal: Signal column to use for entries (e.g. "breakout_signal").
            stop_loss_pct: Stop loss fraction (0.07 = 7 %).
            target_pct: Profit target fraction (0.20 = 20 %).
            max_holding_days: Maximum calendar days per trade.
            min_composite_score: Minimum composite_score gate.

        Returns:
            DataFrame with feature columns + "label" column (1=TARGET, 0=STOP/TIMEOUT).
            Empty DataFrame when no matching data is found.
        """
        # ── 1. Resolve date range from DB if not provided ────────────────────
        db_min, db_max = BacktestService.get_date_range()
        if db_min is None or db_max is None:
            logger.error("BacktestLabeler: no data in stock_signals — cannot generate labels")
            return pd.DataFrame()

        start_date = start_date or db_min
        end_date   = end_date   or db_max

        logger.info(
            "BacktestLabeler: generating labels | signal=%s | %s → %s",
            entry_signal, start_date, end_date,
        )

        # ── 2. Run backtest to get trade outcomes ─────────────────────────────
        result = BacktestEngine.run(
            start_date=start_date,
            end_date=end_date,
            entry_signal=entry_signal,
            stop_loss_pct=stop_loss_pct,
            target_pct=target_pct,
            max_holding_days=max_holding_days,
            min_composite_score=min_composite_score,
        )

        if result.is_empty:
            logger.warning("BacktestLabeler: backtest produced no trades in range")
            return pd.DataFrame()

        trades = result.trades[["symbol", "entry_date", "exit_reason", "return_pct"]].copy()
        trades["entry_date"] = pd.to_datetime(trades["entry_date"]).dt.date
        trades["label"] = (trades["exit_reason"] == MLConfig.WIN_EXIT_REASON).astype(int)

        logger.info(
            "BacktestLabeler: %d trades | win_rate=%.1f%%",
            len(trades), trades["label"].mean() * 100,
        )

        # ── 3. Fetch rich features for those entries ─────────────────────────
        features = BacktestService.get_entry_features_for_training(
            start_date=start_date,
            end_date=end_date,
            entry_signal=entry_signal,
            min_composite_score=min_composite_score,
        )

        if features.empty:
            logger.error("BacktestLabeler: feature query returned no rows")
            return pd.DataFrame()

        features["entry_date"] = pd.to_datetime(features["trade_date"]).dt.date

        # ── 4. Inner join — features ↔ outcomes ──────────────────────────────
        labeled = trades.merge(
            features,
            on=["symbol", "entry_date"],
            how="inner",
            suffixes=("", "_feat"),
        )

        if labeled.empty:
            logger.warning("BacktestLabeler: inner join produced 0 rows (date/symbol mismatch)")
            return pd.DataFrame()

        # Keep only ML feature columns + label
        keep_cols = [c for c in MLConfig.FEATURE_COLUMNS if c in labeled.columns]
        labeled = labeled[keep_cols + ["label"]].copy()
        labeled.reset_index(drop=True, inplace=True)

        logger.info(
            "BacktestLabeler: %d labeled samples | features=%d | "
            "target_rate=%.1f%% | stop_rate=%.1f%%",
            len(labeled),
            len(keep_cols),
            labeled["label"].mean() * 100,
            (1 - labeled["label"].mean()) * 100,
        )
        return labeled
