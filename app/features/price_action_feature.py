"""Price Action feature calculator — daily + weekly multi-timeframe structure.

Reads stock_features columns (close, high, low, ema_50, relative_volume) and
appends PA feature columns.  No EMA recomputation — reuses what FeaturePipeline
already wrote.

Weekly features are derived by resampling per-symbol daily data to weekly bars
(week ending Friday, NSE calendar) then merging back to daily via backward-fill.
This is the primary false-positive reducer: a daily spike creating a "higher high"
rarely survives weekly confirmation.
"""

from typing import Optional

import numpy as np
import pandas as pd

from app.config.logging_config import get_logger
from app.config.strategy_config import PriceActionConfig

logger = get_logger(__name__)

# Columns this module requires from stock_features
REQUIRED_INPUT_COLUMNS: list[str] = [
    "symbol",
    "trade_date",
    "close_price",
    "high_price",
    "low_price",
    "ema_50",
    "relative_volume",
]

# Columns this module produces
OUTPUT_COLUMNS: list[str] = [
    "pa_hh_daily",
    "pa_hl_daily",
    "pa_hh_weekly",
    "pa_hl_weekly",
    "pa_close_position",
    "pa_vol_quality",
    "pa_roc_10",
    "pa_roc_20",
    "pa_momentum_accel",
    "pa_extension_ok",
]


class PriceActionFeature:
    """Compute daily and weekly price-action features for a single symbol.

    Designed to mirror the per-symbol pattern used by FeaturePipeline —
    accepts a sorted ascending DataFrame for one symbol and returns it with
    PA columns appended.  Zero side-effects; never modifies the input.
    """

    @staticmethod
    def _compute_daily(df: pd.DataFrame) -> pd.DataFrame:
        """Compute daily PA features: HH/HL structure, candle quality, ROC, extension.

        Args:
            df: Per-symbol DataFrame sorted ascending by trade_date.
                Must contain close_price, high_price, low_price, ema_50,
                relative_volume columns.

        Returns:
            df copy with daily PA columns appended.
        """
        cfg = PriceActionConfig
        lk  = cfg.PA_DAILY_LOOKBACK
        sh  = cfg.PA_DAILY_SHIFT

        close = df["close_price"]
        high  = df["high_price"]
        low   = df["low_price"]

        # ── Higher High / Higher Low (price structure) ────────────────────────
        # Compare rolling max/min over last LOOKBACK bars vs SHIFT bars ago.
        # shift(1) on rolling max ensures no same-bar lookahead.
        roll_max = close.rolling(lk, min_periods=lk).max().shift(1)
        roll_min = close.rolling(lk, min_periods=lk).min().shift(1)

        prev_max = roll_max.shift(sh)
        prev_min = roll_min.shift(sh)

        df["pa_hh_daily"] = (roll_max > prev_max).astype("Int8").fillna(0)
        df["pa_hl_daily"] = (roll_min > prev_min).astype("Int8").fillna(0)

        # ── Candle quality — close position in day's range ────────────────────
        candle_range = (high - low).replace(0, np.nan)
        df["pa_close_position"] = ((close - low) / candle_range).clip(0.0, 1.0)

        # ── Volume quality — accumulation zone, not climactic ─────────────────
        rv = df["relative_volume"]
        df["pa_vol_quality"] = (
            (rv >= cfg.PA_VOL_MIN) & (rv <= cfg.PA_VOL_MAX)
        ).astype("Int8").fillna(0)

        # ── Rate of change ────────────────────────────────────────────────────
        df["pa_roc_10"] = close.pct_change(10) * 100
        df["pa_roc_20"] = close.pct_change(20) * 100

        # ── Momentum acceleration: roc_10 > roc_20 > 0 ────────────────────────
        df["pa_momentum_accel"] = (
            (df["pa_roc_10"] > df["pa_roc_20"])
            & (df["pa_roc_20"] > cfg.PA_MIN_ROC_20)
        ).astype("Int8").fillna(0)

        # ── Extension from EMA50 ───────────────────────────────────────────────
        ema50 = df["ema_50"].replace(0, np.nan)
        ext   = (close - ema50) / ema50
        df["pa_extension_ok"] = (
            (ext >= cfg.PA_MIN_EXT_EMA50) & (ext <= cfg.PA_MAX_EXT_EMA50)
        ).astype("Int8").fillna(0)

        return df

    @staticmethod
    def _compute_weekly(df: pd.DataFrame) -> pd.DataFrame:
        """Compute weekly HH/HL by resampling daily data and merging back.

        Uses 'W-FRI' resampling (NSE weeks end Friday).  Each daily row receives
        the most recently COMPLETED weekly bar (backward merge) so there is zero
        lookahead — intra-week data never contributes to that week's signal.

        Args:
            df: Per-symbol DataFrame sorted ascending by trade_date.
                Must contain close_price as a column.

        Returns:
            df copy with pa_hh_weekly, pa_hl_weekly columns appended.
        """
        cfg = PriceActionConfig
        lk  = cfg.PA_WEEKLY_LOOKBACK
        sh  = cfg.PA_WEEKLY_SHIFT

        # trade_date must be datetime64 for resample
        dt_dates = pd.to_datetime(df["trade_date"])

        # Build weekly OHLC indexed by datetime
        weekly = (
            pd.DataFrame({
                "dt":    dt_dates.values,
                "close": df["close_price"].values,
            })
            .set_index("dt")
            .resample("W-FRI", label="right", closed="right")
            .agg({"close": "last"})
            .dropna()
        )

        if len(weekly) < lk + sh + 1:
            # Insufficient history — fill with zeros
            df["pa_hh_weekly"] = 0
            df["pa_hl_weekly"] = 0
            return df

        # Weekly HH/HL — shift(1) avoids using the current (possibly incomplete) week
        w_roll_max = weekly["close"].rolling(lk, min_periods=lk).max().shift(1)
        w_roll_min = weekly["close"].rolling(lk, min_periods=lk).min().shift(1)

        weekly["pa_hh_weekly"] = (w_roll_max > w_roll_max.shift(sh)).astype("Int8").fillna(0)
        weekly["pa_hl_weekly"] = (w_roll_min > w_roll_min.shift(sh)).astype("Int8").fillna(0)

        weekly_signals = weekly[["pa_hh_weekly", "pa_hl_weekly"]].reset_index()
        weekly_signals.rename(columns={"dt": "week_end_date"}, inplace=True)
        weekly_signals["week_end_date"] = pd.to_datetime(weekly_signals["week_end_date"])

        # Merge back: each trading day gets the most recent completed weekly bar
        daily_dt = df.copy()
        daily_dt["_dt"] = pd.to_datetime(daily_dt["trade_date"])
        daily_dt = daily_dt.sort_values("_dt")
        weekly_signals = weekly_signals.sort_values("week_end_date")

        merged = pd.merge_asof(
            daily_dt,
            weekly_signals,
            left_on="_dt",
            right_on="week_end_date",
            direction="backward",
        )

        merged["pa_hh_weekly"] = merged["pa_hh_weekly"].fillna(0).astype(int)
        merged["pa_hl_weekly"] = merged["pa_hl_weekly"].fillna(0).astype(int)
        merged.drop(columns=["_dt", "week_end_date"], inplace=True, errors="ignore")

        return merged

    @staticmethod
    def calculate(df: pd.DataFrame) -> pd.DataFrame:
        """Compute all PA features for a single symbol.

        Execution order:
          1. Daily HH/HL, candle quality, ROC, volume quality, extension
          2. Weekly HH/HL (resample + backward merge — no lookahead)

        Args:
            df: Per-symbol DataFrame sorted ascending by trade_date.
                Required columns: symbol, trade_date, close_price, high_price,
                low_price, ema_50, relative_volume.

        Returns:
            df copy with all PA feature columns appended.
            Empty DataFrame on error or if required columns are missing.
        """
        if df.empty:
            return pd.DataFrame()

        missing = [c for c in REQUIRED_INPUT_COLUMNS if c not in df.columns]
        if missing:
            symbol = df["symbol"].iloc[0] if "symbol" in df.columns else "UNKNOWN"
            logger.error("PriceActionFeature missing columns for %s: %s", symbol, missing)
            return pd.DataFrame()

        try:
            df = df.copy().sort_values("trade_date").reset_index(drop=True)
            df = PriceActionFeature._compute_daily(df)
            df = PriceActionFeature._compute_weekly(df)
            return df

        except Exception as exc:
            symbol = df["symbol"].iloc[0] if not df.empty else "UNKNOWN"
            logger.error("PriceActionFeature failed for %s: %s", symbol, exc, exc_info=True)
            return pd.DataFrame()
