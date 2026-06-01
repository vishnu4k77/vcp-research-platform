"""Minervini Trend Template signal — all 8 structural qualification conditions.

Source: 'Trade Like a Stock Market Wizard' (Minervini, 2013).

This signal acts as a STRUCTURAL FILTER, not a breakout trigger.
minervini_signal = 1 means the stock is in a textbook superperformance setup.
It does not require a breakout to happen TODAY — it confirms the stock is in
the correct structural position for a high-probability entry.

All 8 conditions must be met simultaneously:
  1. close > EMA150  AND  close > EMA200
  2. EMA150 > EMA200
  3. EMA200 rising (slope positive for EMA200_RISING_LOOKBACK bars ~ 1 month)
  4. EMA50 > EMA150  AND  EMA50 > EMA200
  5. close > EMA50
  6. close >= 52w_low * (1 + MIN_GAIN_FROM_52W_LOW)   [prior uptrend ≥ 30%]
  7. close <= 52w_high / (1 - MAX_BELOW_52W_HIGH)      [within 25% of 52w high]
  8. rs_signal = 1                                      [outperforms Nifty 50]

PIPELINE ORDER: must run AFTER RSSignal so that rs_signal is already present
in the DataFrame.
"""

import numpy as np
import pandas as pd

from app.config.logging_config import get_logger
from app.config.strategy_config import MinerviniConfig

logger = get_logger(__name__)


class MinerviniSignal:
    """Detects stocks satisfying all 8 Minervini Trend Template conditions.

    minervini_signal = 1 → stock is in a valid superperformance setup structure.
    minervini_signal = 0 → one or more conditions fail.

    Consumes pre-computed feature and signal columns — no raw indicator math here.
    All thresholds are read from MinerviniConfig.
    """

    REQUIRED_COLUMNS: list[str] = [
        "close_price",
        "ema_50",
        "ema_150",
        "ema_200",
        "high_52w",
        "low_52w",
    ]

    @staticmethod
    def validate(df: pd.DataFrame) -> None:
        """Raise ValueError if any required column is absent.

        Args:
            df: Input DataFrame to validate.

        Raises:
            ValueError: If one or more REQUIRED_COLUMNS are missing.
        """
        missing = [c for c in MinerviniSignal.REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"MinerviniSignal missing columns: {missing}")

    @staticmethod
    def calculate(df: pd.DataFrame) -> pd.DataFrame:
        """Compute minervini_signal for every row in a single symbol's history.

        Args:
            df: Feature + signal DataFrame for one symbol, sorted ascending by
                trade_date. Must contain REQUIRED_COLUMNS. rs_signal column is
                used for condition 8 if present; absent = condition 8 skipped.

        Returns:
            df with minervini_signal (int 0/1) column appended.
        """
        if df.empty:
            return df

        MinerviniSignal.validate(df)

        df = df.copy()

        for col in MinerviniSignal.REQUIRED_COLUMNS:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        safe_ema200  = df["ema_200"].replace(0, np.nan)
        safe_52w_low = df["low_52w"].replace(0, np.nan)
        safe_52w_hi  = df["high_52w"].replace(0, np.nan)

        # ── Condition 1: close above EMA150 AND EMA200 ───────────────────
        cond_1 = (
            (df["close_price"] > df["ema_150"])
            & (df["close_price"] > df["ema_200"])
        )

        # ── Condition 2: EMA150 above EMA200 ─────────────────────────────
        cond_2 = df["ema_150"] > df["ema_200"]

        # ── Condition 3: EMA200 is rising (positive slope over ~1 month) ─
        # diff(N) = today's EMA200 - EMA200 N bars ago.
        # Positive = the 200-day MA is trending upward.
        ema200_slope = df["ema_200"].diff(MinerviniConfig.EMA200_RISING_LOOKBACK)
        cond_3 = ema200_slope > 0

        # ── Condition 4: EMA50 above both EMA150 and EMA200 ──────────────
        cond_4 = (
            (df["ema_50"] > df["ema_150"])
            & (df["ema_50"] > df["ema_200"])
        )

        # ── Condition 5: close above EMA50 ───────────────────────────────
        cond_5 = df["close_price"] > df["ema_50"]

        # ── Condition 6: stock is 30%+ above its 52-week low ─────────────
        # Ensures a genuine prior uptrend — not a post-crash bounce.
        prior_uptrend_gain = (df["close_price"] - safe_52w_low) / safe_52w_low
        cond_6 = prior_uptrend_gain >= MinerviniConfig.MIN_GAIN_FROM_52W_LOW

        # ── Condition 7: stock within 25% of its 52-week high ─────────────
        # Stocks near new highs have no overhead supply.
        # Formula: (52w_high - close) / 52w_high <= MAX_BELOW_52W_HIGH
        below_52w_high_pct = (safe_52w_hi - df["close_price"]) / safe_52w_hi
        cond_7 = below_52w_high_pct <= MinerviniConfig.MAX_BELOW_52W_HIGH

        # ── Condition 8: outperforms Nifty 50 (rs_signal = 1) ────────────
        # rs_signal is produced by RSSignal which runs before MinerviniSignal.
        # If REQUIRE_RS_OUTPERFORMANCE is False, skip this condition.
        if "rs_signal" in df.columns and MinerviniConfig.REQUIRE_RS_OUTPERFORMANCE:
            cond_8 = df["rs_signal"] == 1
        else:
            if MinerviniConfig.REQUIRE_RS_OUTPERFORMANCE:
                logger.warning(
                    "MinerviniSignal: rs_signal column absent — condition 8 skipped"
                )
            cond_8 = pd.Series(True, index=df.index)

        # ── All 8 conditions: Minervini Trend Template ────────────────────
        df["minervini_signal"] = np.where(
            cond_1 & cond_2 & cond_3 & cond_4 & cond_5 & cond_6 & cond_7 & cond_8,
            1,
            0,
        ).astype(int)

        return df
