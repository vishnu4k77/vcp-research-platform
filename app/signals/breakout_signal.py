import numpy as np
import pandas as pd

from app.config.logging_config import get_logger
from app.config.strategy_config import StrategyConfig

logger = get_logger(__name__)


class BreakoutSignal:
    """
    Stage 2 Resistance Breakout — Weinstein / O'Neil methodology.

    A valid breakout requires:
      1. EMA stack aligned (50 > 150 > 200), price above 200 EMA.
      2. Close breaks above prior N-bar resistance (no lookahead).
      3. Volume surge on breakout day (relative_volume ≥ threshold).
      4. Not overextended (close ≤ EMA50 * (1 + extension_limit)).

    Produces four columns:
      breakout_signal          — confirmed breakout (all conditions met)
      breakout_ready_signal    — price within 3% of resistance, not broken yet
      distance_from_pivot_pct  — % distance from resistance (+ = above)
      distance_from_52w_high_pct — % distance from 52-week high
    """

    REQUIRED_COLUMNS = [
        "close_price",
        "ema_50",
        "ema_150",
        "ema_200",
        "relative_volume",
    ]

    @staticmethod
    def validate(df: pd.DataFrame) -> None:

        missing = [
            col for col in BreakoutSignal.REQUIRED_COLUMNS
            if col not in df.columns
        ]

        if missing:
            raise ValueError(f"BreakoutSignal missing columns: {missing}")

    @staticmethod
    def calculate(df: pd.DataFrame) -> pd.DataFrame:

        if df.empty:
            return df

        BreakoutSignal.validate(df)

        df = df.copy()

        for col in BreakoutSignal.REQUIRED_COLUMNS:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        lookback = StrategyConfig.BREAKOUT_LOOKBACK

        # ── Prior resistance (shift 1 → zero lookahead bias) ─────────────
        resistance = (
            df["close_price"]
            .shift(1)
            .rolling(lookback, min_periods=max(1, lookback // 2))
            .max()
        )
        safe_resistance = resistance.replace(0, np.nan)

        # ── 52-week context ───────────────────────────────────────────────
        high_52w = (
            df["close_price"]
            .rolling(StrategyConfig.STAGE_LOOKBACK, min_periods=63)
            .max()
        )
        safe_52w = high_52w.replace(0, np.nan)

        df["distance_from_52w_high_pct"] = (
            (df["close_price"] - safe_52w) / safe_52w * 100
        ).round(2)

        df["distance_from_pivot_pct"] = (
            (df["close_price"] - safe_resistance) / safe_resistance * 100
        ).round(2)

        # ── Extension guard ───────────────────────────────────────────────
        # Avoid chasing stocks already >10% above EMA 50
        safe_ema50 = df["ema_50"].replace(0, np.nan)
        extension_from_50ema = (df["close_price"] - safe_ema50) / safe_ema50
        not_extended = (
            extension_from_50ema <= StrategyConfig.BREAKOUT_MAX_EXTENSION_FROM_50EMA
        )

        # ── Uptrend prerequisite ─────────────────────────────────────────
        uptrend = (
            (df["ema_50"] > df["ema_150"])
            & (df["ema_150"] > df["ema_200"])
            & (df["close_price"] > df["ema_200"])
        )

        # ── Breakout conditions ───────────────────────────────────────────
        price_breakout = df["close_price"] > safe_resistance
        volume_surge = (
            df["relative_volume"] >= StrategyConfig.BREAKOUT_VOLUME_MULTIPLIER
        )

        df["breakout_signal"] = np.where(
            (uptrend & price_breakout & volume_surge & not_extended),
            1,
            0,
        )

        # ── Breakout-ready: approaching resistance, not yet broken ────────
        near_resistance = (
            safe_resistance.notna()
            & (
                df["close_price"] / safe_resistance
                >= (1.0 - StrategyConfig.BREAKOUT_NEAR_RESISTANCE_THRESHOLD)
            )
            & (~price_breakout)
        )

        df["breakout_ready_signal"] = np.where(
            (uptrend & near_resistance),
            1,
            0,
        )

        return df
