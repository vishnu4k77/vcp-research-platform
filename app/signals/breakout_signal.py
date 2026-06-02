"""Stage 2 resistance-breakout signal — Weinstein / O'Neil methodology."""

import numpy as np
import pandas as pd

from app.config.logging_config import get_logger
from app.config.strategy_config import StrategyConfig

logger = get_logger(__name__)


class BreakoutSignal:
    """Detects confirmed resistance breakouts and breakout-ready setups.

    A valid breakout requires all ten conditions simultaneously:
      1.  EMA stack aligned: EMA50 > EMA150 > EMA200, close above EMA200.
      2.  Close breaks above prior BREAKOUT_LOOKBACK-bar resistance (no lookahead).
      3.  Volume surge on breakout day (relative_volume ≥ BREAKOUT_VOLUME_MULTIPLIER).
      4.  Not overextended: close ≤ EMA50 × (1 + BREAKOUT_MAX_EXTENSION_FROM_50EMA).
      5.  Strong close: close in upper half of day's range (no bearish rejection wicks).
      6.  Not gap-and-fail: open gap ≤ 3% OR close ≥ open (no distribution trap).
      7.  Not earnings day: is_earnings_day = 0 (event-driven volume ≠ accumulation).
      8.  Base quality — tight range: 20-day spread < BREAKOUT_MAX_BASE_RANGE_PCT (15%).
          Eliminates V-shaped recoveries and choppy ranges with no accumulation.
      9.  Base quality — prior uptrend: close ≥ 52w low × (1 + BREAKOUT_PRIOR_UPTREND_MIN).
          O'Neil: institutional money accumulates only after a proven prior run.
      10. Minervini Trend Template conditions 6 + 8:
           a. Close > EMA50 (near-term strength; cond 6).
           b. Close within BREAKOUT_NEAR_52W_HIGH_MAX (25%) of 52-week high (cond 8).
              Near new highs = no overhead supply. Stocks far from highs are in Stage 3/4.

    Uses high_52w from stock_features (pre-computed by VolatilityFeature) to
    avoid duplicate rolling computation.

    Produces four columns:
        breakout_signal           — confirmed breakout (all conditions met)
        breakout_ready_signal     — within BREAKOUT_NEAR_RESISTANCE_THRESHOLD of pivot
        distance_from_pivot_pct   — % distance from resistance pivot (+ = above)
        distance_from_52w_high_pct — % distance from 52-week high
    """

    REQUIRED_COLUMNS: list[str] = [
        "close_price",
        "open_price",
        "high_price",
        "low_price",
        "high_52w",
        "low_52w",
        "ema_50",
        "ema_150",
        "ema_200",
        "relative_volume",
        "avg_volume_10",   # used for base volume dry-up check
        "avg_volume_20",   # denominator for dry-up ratio
        "is_earnings_day",
        "stage2_days",     # computed by SignalPipeline._compute_stage2_age before BreakoutSignal runs
    ]

    @staticmethod
    def validate(df: pd.DataFrame) -> None:
        """Raise ValueError if any required column is absent.

        Args:
            df: Input DataFrame to validate.

        Raises:
            ValueError: If one or more REQUIRED_COLUMNS are missing.
        """
        missing = [c for c in BreakoutSignal.REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"BreakoutSignal missing columns: {missing}")

    @staticmethod
    def calculate(df: pd.DataFrame) -> pd.DataFrame:
        """Compute breakout signals for a single symbol's full price history.

        Args:
            df: Feature DataFrame for a single symbol, sorted ascending by
                trade_date. Must contain REQUIRED_COLUMNS.

        Returns:
            df with breakout_signal, breakout_ready_signal,
            distance_from_pivot_pct, distance_from_52w_high_pct appended.
        """
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

        # ── 52-week high from pre-computed feature (DRY — no duplicate rolling) ──
        safe_52w = df["high_52w"].replace(0, np.nan)

        df["distance_from_52w_high_pct"] = (
            (df["close_price"] - safe_52w) / safe_52w * 100
        ).round(2)

        df["distance_from_pivot_pct"] = (
            (df["close_price"] - safe_resistance) / safe_resistance * 100
        ).round(2)

        # ── Extension guard ───────────────────────────────────────────────
        safe_ema50 = df["ema_50"].replace(0, np.nan)
        extension_from_50ema = (df["close_price"] - safe_ema50) / safe_ema50
        not_extended = (
            extension_from_50ema <= StrategyConfig.BREAKOUT_MAX_EXTENSION_FROM_50EMA
        )

        # ── Candle quality: close must be in upper half of day's range ────
        # Catches bearish rejection candles (big upper wick, weak close) that
        # would otherwise pass the close > resistance check.
        candle_range = (df["high_price"] - df["low_price"]).replace(0, np.nan)
        close_position = (df["close_price"] - df["low_price"]) / candle_range
        strong_close = close_position >= StrategyConfig.BREAKOUT_CLOSE_POSITION_MIN

        # ── Gap-and-fail trap: gap up > threshold AND closed below open ───
        # Detects institutional distribution disguised as a breakout:
        # stock opens far above prior close on excitement, then fades all day.
        prior_close = df["close_price"].shift(1).replace(0, np.nan)
        open_gap_pct = (df["open_price"] - prior_close) / prior_close
        bearish_candle = df["close_price"] < df["open_price"]
        not_gap_trap = ~((open_gap_pct > StrategyConfig.BREAKOUT_MAX_GAP_PCT) & bearish_candle)

        # ── Earnings-day filter ───────────────────────────────────────────
        # is_earnings_day = 1 when EarningsService flagged this row as an
        # earnings release date from Yahoo Finance.  Volume on earnings days
        # is event-driven (surprise reaction), not institutional accumulation.
        # Minervini: wait for a new base to form after an earnings gap; never
        # enter on the earnings day itself.
        not_earnings_day = df["is_earnings_day"] != 1

        # ── Base quality: tight range inside the 20-day window ───────────
        # A valid consolidation base must have a NARROW price range — the stock
        # should be coiling, not chopping.  A wide 20-day range means either:
        #   (a) V-shaped recovery from a gap-down (no real base)
        #   (b) Choppy sideways noise with no institutional accumulation
        #
        # Formula: (rolling_max - rolling_min) / rolling_max < 15%
        # Example good base: pivot=120, 20d low=104 → range=13.3% ✓
        # Example bad (chop): pivot=120, 20d low=95  → range=20.8% ✗
        # Example bad (V):    pivot=120, 20d low=88  → range=26.7% ✗
        base_low = (
            df["close_price"]
            .shift(1)
            .rolling(lookback, min_periods=max(1, lookback // 2))
            .min()
        )
        safe_base_low = base_low.replace(0, np.nan)
        base_range_pct = (safe_resistance - safe_base_low) / safe_resistance
        tight_base = base_range_pct < StrategyConfig.BREAKOUT_MAX_BASE_RANGE_PCT

        # Expose resistance level + base low for TargetCalculator (downstream step).
        # These are the shift(1) rolling values — no lookahead bias.
        df["pivot_price"]    = safe_resistance.round(2)
        df["base_low_price"] = safe_base_low.round(2)

        # ── Prior uptrend: stock must have a meaningful run before the base ─
        # O'Neil / Minervini: institutional accumulation only occurs after a stock
        # has already proven itself with a strong prior uptrend.  A stock sitting
        # near its 52-week low has no such history — any "breakout" is just noise.
        #
        # Formula: (close - 52w_low) / 52w_low >= 30%
        # Example good: 52w low=80, close=108 → uptrend=35% ✓
        # Example bad:  52w low=95, close=102 → uptrend=7.4% ✗  (post-crash bounce)
        safe_52w_low = df["low_52w"].replace(0, np.nan)
        prior_uptrend_gain = (df["close_price"] - safe_52w_low) / safe_52w_low
        has_prior_uptrend = prior_uptrend_gain >= StrategyConfig.BREAKOUT_PRIOR_UPTREND_MIN

        # ── Stage 2 age gate ─────────────────────────────────────────────
        # Late Stage 2 entries (>60 days) are approaching Stage 3 distribution.
        # Only allow breakouts from:
        #   (a) stocks not yet formally in Stage 2 (stage2_days = 0) — they may be
        #       breaking INTO Stage 2 right now, which is the freshest entry.
        #   (b) stocks in early/mid Stage 2 (1–60 days).
        # Reuses BREAKOUT_MAX_STAGE2_AGE_DAYS config (= STAGE2_MID_MAX_DAYS = 60).
        in_stage2 = df["stage2_days"] > 0
        not_late_stage2 = (
            ~in_stage2
            | (df["stage2_days"] <= StrategyConfig.BREAKOUT_MAX_STAGE2_AGE_DAYS)
        )

        # ── Volume dry-up in base ─────────────────────────────────────────
        # Before a valid breakout the stock must have been QUIET — volume
        # contracting as institutions accumulate.  If volume was still elevated
        # during the base, it signals distribution, not accumulation.
        #
        # IMPORTANT: shift(1) excludes today's high-volume breakout day.
        # Without shift, avg_volume_10 includes the current 1.5× surge which
        # inflates the ratio above 0.75 and blocks almost every valid breakout.
        # We want to know: was volume quiet in the DAYS BEFORE the breakout?
        #
        # Formula: avg_volume_10.shift(1) / avg_volume_20 < 0.75
        safe_avg_vol_20 = df["avg_volume_20"].replace(0, np.nan)
        volume_dry_up_base = (
            df["avg_volume_10"].shift(1) / safe_avg_vol_20
            < StrategyConfig.BREAKOUT_BASE_VOLUME_DRY_UP
        )

        # ── Minervini condition 6: close above EMA50 ─────────────────────
        # A stock below its own 50 EMA is in near-term weakness — not the
        # controlled tightness of an accumulation base.  This eliminates
        # entries where price has dipped under the 50 EMA and is bouncing
        # back through a resistance level, rather than riding above it.
        close_above_50ema = df["close_price"] > df["ema_50"]

        # ── Minervini condition 8: within 25% of 52-week high ─────────────
        # The single most important filter from the Trend Template.
        # Stocks near new highs have no overhead supply — institutions have
        # already accumulated and the path of least resistance is upward.
        # Stocks at -40% to -60% from their 52w high are in Stage 3/4 and
        # any "breakout" is merely recovering toward real resistance above.
        # Source: "Trade Like a Stock Market Wizard" (Minervini, 2013)
        near_52w_high = (
            df["distance_from_52w_high_pct"]
            >= -StrategyConfig.BREAKOUT_NEAR_52W_HIGH_MAX * 100
        )

        # ── Uptrend prerequisite ──────────────────────────────────────────
        uptrend = (
            (df["ema_50"]  > df["ema_150"])
            & (df["ema_150"] > df["ema_200"])
            & (df["close_price"] > df["ema_200"])
        )

        # ── Breakout conditions ───────────────────────────────────────────
        price_breakout = df["close_price"] > safe_resistance
        volume_surge = df["relative_volume"] >= StrategyConfig.BREAKOUT_VOLUME_MULTIPLIER

        df["breakout_signal"] = np.where(
            uptrend & price_breakout & volume_surge & not_extended
            & strong_close & not_gap_trap & not_earnings_day
            & tight_base & has_prior_uptrend
            & close_above_50ema & near_52w_high
            & not_late_stage2 & volume_dry_up_base,
            1,
            0,
        )

        # ── Breakout-ready: approaching pivot, not yet broken ─────────────
        near_resistance = (
            safe_resistance.notna()
            & (
                df["close_price"] / safe_resistance
                >= (1.0 - StrategyConfig.BREAKOUT_NEAR_RESISTANCE_THRESHOLD)
            )
            & (~price_breakout)
        )

        df["breakout_ready_signal"] = np.where(
            uptrend & near_resistance,
            1,
            0,
        )

        return df
