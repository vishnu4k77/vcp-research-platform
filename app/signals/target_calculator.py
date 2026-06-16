"""Price target and upside-probability computation — Phase 2B.

Runs as the final step in SignalPipeline.process_symbol(), after:
  - BreakoutSignal  → pivot_price, base_low_price exposed as columns
  - CompositeRanker → composite_score computed

Produces 6 new columns written to stock_signals every daily run:
    pivot_price      already written by BreakoutSignal; included in OUTPUT_COLUMNS
    base_range_pct   base width as % of pivot  (tight < 15%, wide > 15%)
    target_1_pct     % upside from today's close to T1
    target_2_pct     % upside from today's close to T2  (full measured move)
    risk_reward_t2   T2_pct / STOP_LOSS_PCT  (e.g. 15 / 7 = 2.14)
    upside_prob_pct  formula-based probability — updates daily as inputs change

upside_prob_pct is NOT a backtest-calibrated stat.  It is an honest estimate
labelled "Est. Prob%" in the dashboard.  Recalibrate via BacktestConfig after
enough signal_outcomes history accumulates.
"""

import numpy as np
import pandas as pd

from app.config.logging_config import get_logger
from app.config.strategy_config import TargetConfig

logger = get_logger(__name__)


class TargetCalculator:
    """Append price targets and upside probability to per-symbol signal rows.

    Inputs consumed from upstream steps:
        pivot_price      — BreakoutSignal: 20-day prior resistance in ₹
        base_low_price   — BreakoutSignal: 20-day base bottom in ₹
        close_price      — stock_features: today's close
        composite_score  — CompositeRanker
        breakout_signal, minervini_signal, rs_new_high, darvas_signal, stage2_days

    All inputs are optional via defensive column checks so this step never
    aborts the pipeline even if an upstream step failed.
    """

    @staticmethod
    def calculate(df: pd.DataFrame) -> pd.DataFrame:
        """Compute target columns for every row in a symbol's history.

        Args:
            df: Full signal DataFrame for one symbol, sorted ascending by
                trade_date.  Must include pivot_price and base_low_price.

        Returns:
            df with base_range_pct, target_1_pct, target_2_pct,
            risk_reward_t2, upside_prob_pct appended.
        """
        if df.empty:
            return df

        df = df.copy()

        # ── Retrieve inputs safely ─────────────────────────────────────────
        def _col(name: str) -> pd.Series:
            return pd.to_numeric(
                df[name] if name in df.columns else pd.Series(np.nan, index=df.index),
                errors="coerce",
            )

        close    = _col("close_price")
        pivot    = _col("pivot_price")
        base_low = _col("base_low_price")

        safe_close = close.replace(0, np.nan)
        safe_pivot = pivot.replace(0, np.nan)

        # ── Base range ────────────────────────────────────────────────────
        # Width of the 20-day consolidation in ₹.  clip(lower=0) prevents
        # negative values when base_low somehow exceeds pivot (stale data).
        base_range = (safe_pivot - base_low).clip(lower=0)
        df["base_range_pct"] = (base_range / safe_pivot * 100).round(2)

        # ── Target prices ─────────────────────────────────────────────────
        t1_price = safe_pivot + base_range * TargetConfig.T1_MULTIPLIER
        t2_price = safe_pivot + base_range * TargetConfig.T2_MULTIPLIER

        # Actual target prices in ₹ — for setting limit orders directly
        df["target_1_price"] = np.where(safe_pivot.notna(), t1_price.round(2), np.nan)
        df["target_2_price"] = np.where(safe_pivot.notna(), t2_price.round(2), np.nan)

        # % upside from TODAY's close so the user sees total remaining potential.
        # Positive = still upside remaining; negative = already exceeded the target.
        df["target_1_pct"] = ((t1_price / safe_close - 1) * 100).round(2)
        df["target_2_pct"] = ((t2_price / safe_close - 1) * 100).round(2)

        # ── T3 — conservative early-gain target (25% of measured move) ──────
        t3_price = safe_pivot + base_range * TargetConfig.T3_MULTIPLIER
        df["target_3_price"] = np.where(safe_pivot.notna(), t3_price.round(2), np.nan)
        df["target_3_pct"]   = ((t3_price / safe_close - 1) * 100).round(2)

        # Confidence columns are placeholders here — filled per-stock after
        # ConfidenceCalibrator builds the empirical hit-rate model.
        if "confidence_t1" not in df.columns:
            df["confidence_t1"] = np.nan
        if "confidence_t2" not in df.columns:
            df["confidence_t2"] = np.nan
        if "confidence_t3" not in df.columns:
            df["confidence_t3"] = np.nan

        # ── Position stage + dynamic trailing stop ───────────────────────
        # Tells the user WHERE their position is relative to targets so they
        # know whether to hold, trail the stop, or take profits.
        #
        # Stage logic (Minervini / O'Neil framework):
        #   IN_BASE     price still below pivot  → no signal yet, watch only
        #   ACTIVE      pivot ≤ price < T1×0.97  → hold, stop at pivot×0.93
        #   NEAR_T1     within 3% of T1          → partial exit, trail to pivot
        #   PAST_T1     T1 < price ≤ T2          → move stop to T1, aim for T2
        #   PAST_T2     price > T2               → consider full exit, stop at T2
        #
        # Trailing stop uses the higher of ATR-based support and level-based stop:
        #   ATR support  = close − (1.5 × ATR14)  — respects current volatility
        #   Level stop   = stage-specific price level
        # The MAX of the two prevents the stop being set BELOW current support.

        ema21   = _col("ema_21")
        atr14   = _col("atr_14")

        # ATR trailing support: close - 1.5 × ATR (adapts to volatility expansion)
        atr_support = (safe_close - 1.5 * atr14.fillna(0)).clip(lower=0)

        # Stage classification — vectorised, no row-by-row loop
        in_base    = safe_close < safe_pivot
        past_t2    = safe_close > t2_price
        past_t1    = (safe_close > t1_price) & ~past_t2
        near_t1    = (safe_close >= t1_price * 0.97) & (safe_close <= t1_price)
        active     = (safe_close >= safe_pivot) & (safe_close < t1_price * 0.97)

        stage = pd.Series("IN_BASE", index=df.index, dtype=object)
        stage = stage.where(~active,  "ACTIVE")
        stage = stage.where(~near_t1, "NEAR_T1")
        stage = stage.where(~past_t1, "PAST_T1")
        stage = stage.where(~past_t2, "PAST_T2")
        df["position_stage"] = np.where(safe_pivot.notna(), stage, None)

        # Trailing stop — rises as position moves through stages
        stop_at_pivot   = (safe_pivot * (1 - TargetConfig.STOP_LOSS_PCT)).round(2)
        stop_at_t1      = (t1_price   * 0.98).round(2)   # 2% below T1 (wiggle room)
        stop_at_t2      = (t2_price   * 0.98).round(2)   # 2% below T2

        trailing = pd.Series(np.nan, index=df.index)
        # IN_BASE / ACTIVE: stop below pivot, raised further by ATR support
        trailing = trailing.where(~in_base,  np.maximum(stop_at_pivot, atr_support))
        trailing = trailing.where(~active,   np.maximum(stop_at_pivot, atr_support))
        # NEAR_T1: move stop to near breakeven (pivot level), rising with ATR
        trailing = trailing.where(~near_t1,  np.maximum(stop_at_pivot, atr_support))
        # PAST_T1: stop at T1 level — locks in first-target profit
        trailing = trailing.where(~past_t1,  np.maximum(stop_at_t1, atr_support))
        # PAST_T2: stop at T2 — locks in full-measured-move profit
        trailing = trailing.where(~past_t2,  np.maximum(stop_at_t2, atr_support))

        df["trailing_stop_price"] = np.where(
            safe_pivot.notna(), trailing.round(2), np.nan
        )

        # ── Risk : Reward to T2 ───────────────────────────────────────────
        stop_pct = TargetConfig.STOP_LOSS_PCT * 100   # 7.0%
        df["risk_reward_t2"] = (df["target_2_pct"] / stop_pct).round(2)

        # ── Upside probability (formula-driven, daily update) ─────────────
        # Starts from the baseline win rate and adds per-signal adjustments.
        # Only shown when pivot_price is available (valid base computed).
        prob = pd.Series(TargetConfig.BASE_PROB_PCT, index=df.index, dtype=float)

        for col, adj in [
            ("breakout_signal",  TargetConfig.PROB_ADJ_BREAKOUT_SIGNAL),
            ("minervini_signal", TargetConfig.PROB_ADJ_MINERVINI_SIGNAL),
            ("rs_new_high",      TargetConfig.PROB_ADJ_RS_NEW_HIGH),
            ("darvas_signal",    TargetConfig.PROB_ADJ_DARVAS_SIGNAL),
        ]:
            if col in df.columns:
                prob = prob + (
                    pd.to_numeric(df[col], errors="coerce").fillna(0).clip(0, 1) * adj
                )

        if "composite_score" in df.columns:
            high_score = (
                pd.to_numeric(df["composite_score"], errors="coerce")
                >= TargetConfig.HIGH_SCORE_THRESHOLD
            )
            prob = prob + high_score.fillna(False).astype(float) * TargetConfig.PROB_ADJ_HIGH_SCORE

        if "stage2_days" in df.columns:
            s2d      = pd.to_numeric(df["stage2_days"], errors="coerce").fillna(0)
            fresh_s2 = (s2d > 0) & (s2d <= TargetConfig.FRESH_STAGE2_DAYS)
            late_s2  = s2d > TargetConfig.LATE_STAGE2_DAYS
            prob = prob + fresh_s2.astype(float) * TargetConfig.PROB_ADJ_FRESH_STAGE2
            prob = prob + late_s2.astype(float)  * TargetConfig.PROB_ADJ_LATE_STAGE2

        clipped = prob.clip(TargetConfig.MIN_PROB_PCT, TargetConfig.MAX_PROB_PCT).round(1)

        # NaN when pivot unavailable — keeps NULL in the DB instead of a meaningless float
        df["upside_prob_pct"] = np.where(safe_pivot.notna(), clipped, np.nan)

        # ── Expected Value score — primary scanner rank driver ────────────
        # EV% = (P × T2%) − (1−P) × stop%
        # This is the statistically correct measure of profit-per-rupee-risked.
        # Positive EV = edge exists. Higher EV = better trade, accounting for
        # both upside magnitude AND probability simultaneously.
        # Example: P=0.56, T2=24.4%, stop=7% → EV = 13.7 − 3.1 = +10.6%
        # NaN propagates when T2 is NaN (no valid base / pivot).
        p     = clipped / 100
        ev    = (p * df["target_2_pct"]) - ((1 - p) * stop_pct)
        df["ev_score"] = np.where(
            df["target_2_pct"].notna(),
            ev.round(2),
            np.nan,
        )

        return df
