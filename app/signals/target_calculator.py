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

        # % upside from TODAY's close so the user sees total remaining potential.
        # Positive = still upside remaining; negative = already exceeded the target.
        df["target_1_pct"] = ((t1_price / safe_close - 1) * 100).round(2)
        df["target_2_pct"] = ((t2_price / safe_close - 1) * 100).round(2)

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

        return df
