"""Multi-Timeframe (MTF) feature calculator — weekly and monthly EMA structure.

Resamples per-symbol daily close prices from stock_features into weekly and
monthly bars, computes EMA pairs on each timeframe, then merges the values
back to each daily row via backward-fill (no lookahead bias).

The merged EMA values are consumed by MTFSignal to produce BIT trend signals.

Design mirrors PriceActionFeature:
  - Accepts a single-symbol DataFrame sorted ascending by trade_date.
  - Never modifies the input; returns a copy with new columns appended.
  - Zero side-effects; safe to call in parallel across symbols.

Weekly benchmark  : Weinstein Stage 2 — 30-week MA is the stage boundary.
Monthly benchmark : Minervini/O'Neil   — 10-month EMA is the primary trend line.
"""

from packaging.version import Version as _Version

import numpy as np
import pandas as pd

from app.config.logging_config import get_logger
from app.config.strategy_config import MTFConfig

logger = get_logger(__name__)

# ── Pandas version compatibility ──────────────────────────────────────────────
# pandas 2.2 introduced "ME", "QE", "YE" as replacements for the deprecated
# "M", "Q", "A" aliases.  On pandas < 2.2 the new aliases raise ValueError, so
# we resolve once at import time and use the correct form throughout.
# Config stores the modern "ME" alias; this map downgrades it for older installs.
_PD_VERSION = _Version(pd.__version__)
_FREQ_COMPAT: dict[str, str] = (
    {}                                          # pandas ≥ 2.2 — use as-is
    if _PD_VERSION >= _Version("2.2.0")
    else {"ME": "M", "QE": "Q", "YE": "A"}    # pandas < 2.2 legacy aliases
)


def _compat_freq(freq: str) -> str:
    """Return the resample frequency alias compatible with the installed pandas.

    Config stores the modern pandas 2.2+ aliases (e.g. 'ME').  On older installs
    this function maps them to the legacy form ('M') to avoid both ValueError and
    FutureWarning.  When pandas is upgraded the mapping is empty and the function
    is a no-op.

    Args:
        freq: Frequency string from MTFConfig (e.g. 'ME', 'W-FRI').

    Returns:
        Compatible alias for the installed pandas version.
    """
    return _FREQ_COMPAT.get(freq, freq)

# Columns required from stock_features
REQUIRED_INPUT_COLUMNS: list[str] = [
    "symbol",
    "trade_date",
    "close_price",
]

# Columns produced by this module (values in ₹, assigned per daily row)
OUTPUT_COLUMNS: list[str] = [
    "mtf_w_ema_fast",   # weekly EMA (fast period) in ₹
    "mtf_w_ema_slow",   # weekly EMA (slow period) in ₹
    "mtf_m_ema_fast",   # monthly EMA (fast period) in ₹
    "mtf_m_ema_slow",   # monthly EMA (slow period) in ₹
]


class MTFFeature:
    """Compute weekly and monthly EMA features for a single symbol.

    Mirrors the per-symbol pattern used by PriceActionFeature:
    accepts a sorted-ascending DataFrame for one symbol and returns it with
    MTF EMA columns appended.  Zero side-effects; never modifies the input.
    """

    @staticmethod
    def _resample_ema(
        daily_df: pd.DataFrame,
        freq: str,
        fast_period: int,
        slow_period: int,
        min_bars: int,
        col_fast: str,
        col_slow: str,
    ) -> pd.DataFrame:
        """Resample daily close to a lower frequency, compute EMA pair, merge back.

        Each daily row receives the EMA values from the most recently COMPLETED
        bar on the target frequency (backward merge_asof — no lookahead).
        When the symbol has fewer than ``min_bars`` completed bars the columns
        are filled with NaN so the signal layer emits 0 (safe fallback).

        Args:
            daily_df:    Per-symbol DataFrame sorted ascending by trade_date.
                         Must contain close_price as a numeric column.
            freq:        Pandas resample frequency string (e.g. 'W-FRI', 'ME').
            fast_period: EMA span for the fast line (e.g. 10 for 10-week EMA).
            slow_period: EMA span for the slow line (e.g. 30 for 30-week EMA).
            min_bars:    Minimum completed bars before emitting valid EMA values.
            col_fast:    Column name to write the fast EMA into (e.g. 'mtf_w_ema_fast').
            col_slow:    Column name to write the slow EMA into (e.g. 'mtf_w_ema_slow').

        Returns:
            daily_df copy with col_fast and col_slow appended (NaN when
            insufficient history).
        """
        cfg = MTFConfig

        dt_index = pd.to_datetime(daily_df["trade_date"])

        # Build a datetime-indexed Series for resampling
        close_series = pd.Series(
            daily_df["close_price"].values,
            index=dt_index.values,
            name="close",
            dtype=float,
        )

        # Resample to the target frequency (last close of each period).
        # _compat_freq() maps modern aliases (ME) to legacy ones (M) on
        # pandas < 2.2 so neither ValueError nor FutureWarning is raised.
        resampled = (
            close_series
            .resample(_compat_freq(freq), label="right", closed="right")
            .last()
            .dropna()
        )

        if len(resampled) < min_bars:
            # Insufficient history — fill both columns with NaN
            daily_df[col_fast] = np.nan
            daily_df[col_slow] = np.nan
            return daily_df

        # EMA on resampled close
        ema_fast = resampled.ewm(span=fast_period, adjust=False).mean()
        ema_slow = resampled.ewm(span=slow_period, adjust=False).mean()

        # Build a DataFrame with the completed-bar EMA values.
        # shift(1) ensures each bar's EMA is only available from the NEXT bar
        # (i.e. only once the bar is fully closed) — eliminates same-bar lookahead.
        ema_df = pd.DataFrame(
            {
                "bar_end_date": resampled.index,
                col_fast:       ema_fast.shift(1).values,
                col_slow:       ema_slow.shift(1).values,
            }
        )
        ema_df["bar_end_date"] = pd.to_datetime(ema_df["bar_end_date"])
        ema_df.sort_values("bar_end_date", inplace=True)

        # Merge back to daily resolution — each trading day gets the most
        # recently COMPLETED bar's EMA (backward merge = no lookahead).
        daily_dt = daily_df.copy()
        daily_dt["_dt"] = pd.to_datetime(daily_dt["trade_date"])
        daily_dt.sort_values("_dt", inplace=True)

        merged = pd.merge_asof(
            daily_dt,
            ema_df,
            left_on="_dt",
            right_on="bar_end_date",
            direction="backward",
        )

        merged.drop(columns=["_dt", "bar_end_date"], inplace=True, errors="ignore")
        return merged

    @staticmethod
    def calculate(df: pd.DataFrame) -> pd.DataFrame:
        """Compute weekly and monthly EMA features for a single symbol.

        Execution order:
          1. Resample daily → weekly (W-FRI); compute EMA fast + slow; merge back.
          2. Resample daily → monthly (ME);   compute EMA fast + slow; merge back.

        Args:
            df: Per-symbol DataFrame sorted ascending by trade_date.
                Required columns: symbol, trade_date, close_price.

        Returns:
            df copy with four MTF EMA columns appended:
                mtf_w_ema_fast, mtf_w_ema_slow (weekly)
                mtf_m_ema_fast, mtf_m_ema_slow (monthly)
            Returns empty DataFrame on error or missing required columns.
        """
        if df.empty:
            return pd.DataFrame()

        missing = [c for c in REQUIRED_INPUT_COLUMNS if c not in df.columns]
        if missing:
            symbol = df["symbol"].iloc[0] if "symbol" in df.columns else "UNKNOWN"
            logger.error("MTFFeature missing columns for %s: %s", symbol, missing)
            return pd.DataFrame()

        try:
            df = df.copy().sort_values("trade_date").reset_index(drop=True)
            df["close_price"] = pd.to_numeric(df["close_price"], errors="coerce")

            cfg = MTFConfig

            # ── Weekly EMAs ───────────────────────────────────────────────────
            df = MTFFeature._resample_ema(
                daily_df=df,
                freq=cfg.MTF_WEEKLY_FREQ,
                fast_period=cfg.MTF_WEEKLY_EMA_FAST,
                slow_period=cfg.MTF_WEEKLY_EMA_SLOW,
                min_bars=cfg.MTF_MIN_WEEKLY_BARS,
                col_fast="mtf_w_ema_fast",
                col_slow="mtf_w_ema_slow",
            )

            # ── Monthly EMAs ──────────────────────────────────────────────────
            df = MTFFeature._resample_ema(
                daily_df=df,
                freq=cfg.MTF_MONTHLY_FREQ,
                fast_period=cfg.MTF_MONTHLY_EMA_FAST,
                slow_period=cfg.MTF_MONTHLY_EMA_SLOW,
                min_bars=cfg.MTF_MIN_MONTHLY_BARS,
                col_fast="mtf_m_ema_fast",
                col_slow="mtf_m_ema_slow",
            )

            return df

        except Exception as exc:
            symbol = df["symbol"].iloc[0] if not df.empty else "UNKNOWN"
            logger.error("MTFFeature failed for %s: %s", symbol, exc, exc_info=True)
            return pd.DataFrame()
