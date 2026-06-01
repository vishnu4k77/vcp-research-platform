"""Darvas Box breakout signal — near 52-week highs, box consolidation, volume breakout.

Source: 'How I Made $2,000,000 in the Stock Market' (Darvas, 1960).

Nicolas Darvas traded only stocks near NEW 52-week highs — he reasoned that
stocks at new highs have NO OVERHEAD SUPPLY (every shareholder is in profit,
so no one is waiting to sell at break-even).  A tight consolidation 'box'
followed by a breakout on rising volume is the entry trigger.

Difference from BreakoutSignal:
  - BreakoutSignal allows up to 25% below 52w high; DarvasSignal requires 10%.
  - DarvasSignal targets stocks MAKING new highs, not recovering from lows.
  - EMA requirement is softer (close > EMA150 only, not full EMA50>150>200 stack).
  - Box definition reuses the same BREAKOUT_LOOKBACK window but with a tighter
    range threshold (DarvasConfig.BOX_RANGE_MAX_PCT = 12% vs 15%).

darvas_signal = 1 conditions (all must hold simultaneously):
  1. Near 52-week high: (52w_high - close) / 52w_high <= NEAR_HIGH_MAX_PCT
  2. Box is tight:      (box_top - box_bottom) / box_top < BOX_RANGE_MAX_PCT
  3. Box breakout:       close > rolling max of prior BOX_LOOKBACK bars
  4. Volume surge:       relative_volume >= VOLUME_MULTIPLIER
  5. EMA150 condition:   close > EMA150  (if REQUIRE_ABOVE_EMA150 = True)
  6. Not earnings day:   is_earnings_day = 0 (if FILTER_EARNINGS_DAYS = True)
"""

import numpy as np
import pandas as pd

from app.config.logging_config import get_logger
from app.config.strategy_config import DarvasConfig

logger = get_logger(__name__)


class DarvasSignal:
    """Detects Darvas box breakouts near 52-week highs.

    darvas_signal = 1 → stock near new 52w high, broke out of a tight box on volume.
    darvas_signal = 0 → conditions not met.

    Consumes pre-computed feature columns. All thresholds from DarvasConfig.
    is_earnings_day column is optional — absent = earnings filter skipped.
    """

    REQUIRED_COLUMNS: list[str] = [
        "close_price",
        "high_52w",
        "relative_volume",
        "ema_150",
    ]

    @staticmethod
    def validate(df: pd.DataFrame) -> None:
        """Raise ValueError if any required column is absent.

        Args:
            df: Input DataFrame to validate.

        Raises:
            ValueError: If one or more REQUIRED_COLUMNS are missing.
        """
        missing = [c for c in DarvasSignal.REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"DarvasSignal missing columns: {missing}")

    @staticmethod
    def calculate(df: pd.DataFrame) -> pd.DataFrame:
        """Compute darvas_signal for every row in a single symbol's history.

        Args:
            df: Feature DataFrame for one symbol, sorted ascending by trade_date.
                Must contain REQUIRED_COLUMNS.

        Returns:
            df with darvas_signal (int 0/1) column appended.
        """
        if df.empty:
            return df

        DarvasSignal.validate(df)

        df = df.copy()

        for col in DarvasSignal.REQUIRED_COLUMNS:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        lookback     = DarvasConfig.BOX_LOOKBACK
        min_periods  = max(1, lookback // 2)
        safe_52w_hi  = df["high_52w"].replace(0, np.nan)

        # ── Condition 1: near 52-week high (no overhead supply) ───────────
        # (52w_high - close) / 52w_high <= NEAR_HIGH_MAX_PCT
        below_52w_hi_pct = (safe_52w_hi - df["close_price"]) / safe_52w_hi
        near_new_high = below_52w_hi_pct <= DarvasConfig.NEAR_HIGH_MAX_PCT

        # ── Conditions 2 + 3: box definition and breakout ────────────────
        # shift(1) excludes today — we want the box top from PRIOR bars so
        # today's breakout candle does not inflate the box it is breaking out of.
        prior_closes = df["close_price"].shift(1)

        box_top = prior_closes.rolling(lookback, min_periods=min_periods).max()
        box_bot = prior_closes.rolling(lookback, min_periods=min_periods).min()

        safe_box_top = box_top.replace(0, np.nan)
        safe_box_bot = box_bot.replace(0, np.nan)

        # Condition 2: price range inside box < BOX_RANGE_MAX_PCT of box top
        box_range_pct = (safe_box_top - safe_box_bot) / safe_box_top
        tight_box = box_range_pct < DarvasConfig.BOX_RANGE_MAX_PCT

        # Condition 3: today's close exceeds the box top (breakout)
        box_breakout = df["close_price"] > safe_box_top

        # ── Condition 4: volume surge confirms institutional participation ─
        volume_surge = df["relative_volume"] >= DarvasConfig.VOLUME_MULTIPLIER

        # ── Condition 5: close above EMA150 (intermediate uptrend) ────────
        if DarvasConfig.REQUIRE_ABOVE_EMA150:
            above_ema150 = df["close_price"] > df["ema_150"]
        else:
            above_ema150 = pd.Series(True, index=df.index)

        # ── Condition 6: not earnings day ─────────────────────────────────
        if DarvasConfig.FILTER_EARNINGS_DAYS and "is_earnings_day" in df.columns:
            not_earnings = (
                pd.to_numeric(df["is_earnings_day"], errors="coerce").fillna(0) != 1
            )
        else:
            not_earnings = pd.Series(True, index=df.index)

        # ── Composite Darvas signal ───────────────────────────────────────
        df["darvas_signal"] = np.where(
            near_new_high
            & tight_box
            & box_breakout
            & volume_surge
            & above_ema150
            & not_earnings,
            1,
            0,
        ).astype(int)

        return df
