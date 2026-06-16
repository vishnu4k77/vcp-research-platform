"""Multi-Timeframe (MTF) signal — weekly and monthly EMA trend alignment.

Converts the EMA values produced by MTFFeature into binary trend signals and a
0-2 composite score.

Signal logic (identical pattern on each timeframe):
    trend = 1  when  close > ema_fast  AND  ema_fast > ema_slow
    trend = 0  otherwise (including when EMA values are NaN = insufficient history)

Scoring:
    mtf_score = mtf_weekly_trend + mtf_monthly_trend   (range: 0 – 2)
      2 = both timeframes confirm uptrend  → high-conviction macro alignment
      1 = one timeframe aligned            → moderate (short-term or macro stalling)
      0 = neither aligned                  → bearish macro context

The daily trend (trend_signal in stock_signals) is intentionally excluded from
mtf_score — it already appears as a separate column in the scanner.
"""

import pandas as pd
import numpy as np

from app.config.logging_config import get_logger
from app.config.strategy_config import MTFConfig

logger = get_logger(__name__)

# Feature columns this signal consumes (produced by MTFFeature)
REQUIRED_FEATURE_COLUMNS: list[str] = [
    "close_price",
    "mtf_w_ema_fast",
    "mtf_w_ema_slow",
    "mtf_m_ema_fast",
    "mtf_m_ema_slow",
]


class MTFSignal:
    """Compute MTF trend signals and composite score from EMA feature columns.

    Designed to be called per-symbol after MTFFeature.calculate().
    Returns the DataFrame with mtf_weekly_trend, mtf_monthly_trend, and
    mtf_score appended.
    """

    @staticmethod
    def calculate(df: pd.DataFrame) -> pd.DataFrame:
        """Compute MTF signals for a per-symbol DataFrame.

        mtf_weekly_trend  = 1 when close > weekly EMA fast AND fast > slow.
        mtf_monthly_trend = 1 when close > monthly EMA fast AND fast > slow.
        mtf_score         = weekly_trend + monthly_trend  (0 – 2).

        NaN in any EMA column (insufficient history) → signal = 0 (safe fallback).

        Args:
            df: Per-symbol DataFrame with MTF EMA columns appended by
                MTFFeature.calculate(). Must contain close_price.

        Returns:
            df copy with mtf_weekly_trend (int), mtf_monthly_trend (int),
            and mtf_score (int) appended.
            Returns empty DataFrame if required columns are missing.
        """
        if df.empty:
            return pd.DataFrame()

        missing = [c for c in REQUIRED_FEATURE_COLUMNS if c not in df.columns]
        if missing:
            symbol = df["symbol"].iloc[0] if "symbol" in df.columns else "UNKNOWN"
            logger.error("MTFSignal missing feature columns for %s: %s", symbol, missing)
            return pd.DataFrame()

        try:
            df = df.copy()

            def _num(col: str) -> pd.Series:
                """Coerce column to float; NaN propagates (not filled)."""
                return pd.to_numeric(df[col], errors="coerce")

            close      = _num("close_price")
            w_ema_fast = _num("mtf_w_ema_fast")
            w_ema_slow = _num("mtf_w_ema_slow")
            m_ema_fast = _num("mtf_m_ema_fast")
            m_ema_slow = _num("mtf_m_ema_slow")

            # ── Weekly trend ──────────────────────────────────────────────────
            # True only when both EMA values are non-NaN AND conditions hold.
            # NaN comparisons always evaluate to False in pandas — no explicit
            # NaN guard needed; the result is 0 (safe fallback) automatically.
            weekly_trend = (
                (close > w_ema_fast) & (w_ema_fast > w_ema_slow)
            ).fillna(False)

            # ── Monthly trend ─────────────────────────────────────────────────
            monthly_trend = (
                (close > m_ema_fast) & (m_ema_fast > m_ema_slow)
            ).fillna(False)

            df["mtf_weekly_trend"]  = weekly_trend.astype(int)
            df["mtf_monthly_trend"] = monthly_trend.astype(int)
            df["mtf_score"]         = (
                df["mtf_weekly_trend"] + df["mtf_monthly_trend"]
            )

            return df

        except Exception as exc:
            symbol = df["symbol"].iloc[0] if not df.empty else "UNKNOWN"
            logger.error("MTFSignal failed for %s: %s", symbol, exc, exc_info=True)
            return pd.DataFrame()
