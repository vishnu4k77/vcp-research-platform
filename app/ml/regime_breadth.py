"""Phase 2 market regime breadth indicators.

Computes the four fields that are always NULL in the existing RegimeResult:
  - breadth_positive    : > 50 % of NSE stocks above their 50-day EMA (DB query)
  - distribution_count  : O'Neil distribution days — Nifty down > 0.2 % on
                          volume higher than prior session, last 25 sessions
  - follow_through_day  : O'Neil FTD — Nifty up ≥ 1.7 % on higher volume on
                          day 4+ of a rally attempt after a correction
  - vix_risk_off        : India VIX above MLConfig threshold (fetched live)

Called by RegimePipeline._compute_phase2() after the existing 5-condition
primary regime detection.  All methods are read-only with graceful fallback.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from app.config.db import nolock_connect
from app.config.logging_config import get_logger
from app.config.strategy_config import StrategyConfig

logger = get_logger(__name__)

# ── Constants (no magic numbers) ─────────────────────────────────────────────
_BREADTH_MIN_STOCKS: int = 20          # need at least this many stocks for breadth signal
_BREADTH_BULL_THRESHOLD: float = 0.50  # > 50 % above 50 EMA → breadth positive
_DISTRIB_LOOKBACK: int = 25            # O'Neil distribution day window
_DISTRIB_DOWN_MIN_PCT: float = 0.002   # Nifty must fall ≥ 0.2 % to count as a down day
_DISTRIB_MAX_COUNT: int = 5            # ≥ 5 distribution days → bearish pressure
_FTD_MIN_UP_PCT: float = 0.017         # Nifty must rise ≥ 1.7 % for a follow-through day
_FTD_MIN_DAY: int = 4                  # follow-through only valid on day 4+ of rally attempt
_FTD_CORRECTION_MIN_PCT: float = 0.05  # prior decline must be ≥ 5 % to call a correction
_VIX_RISK_OFF_THRESHOLD: float = 20.0  # India VIX above this → risk-off
_VIX_TICKER: str = "^INDIAVIX"


class RegimeBreadthService:
    """Computes Phase 2 regime breadth indicators from DB + live Nifty data.

    All methods return None on failure so the caller can fall back gracefully.
    """

    @staticmethod
    def compute_breadth_positive(trade_date: date) -> Optional[bool]:
        """Return True when > 50 % of NSE stocks are above their 50-day EMA.

        Queries stock_features for the given trade_date — populated daily by
        FeaturePipeline.  Returns None when fewer than _BREADTH_MIN_STOCKS rows
        are available (avoid a misleading signal on sparse data days).

        Args:
            trade_date: The date to evaluate breadth for.

        Returns:
            True / False / None (on error or insufficient data).
        """
        query = """
            SELECT
                COUNT(*)                                          AS total,
                SUM(CASE WHEN close_price > ema_50 THEN 1 ELSE 0 END) AS above_ema50
            FROM stock_features
            WHERE trade_date = :td
              AND close_price IS NOT NULL
              AND ema_50      IS NOT NULL
        """
        try:
            with nolock_connect() as conn:
                row = conn.execute(
                    __import__("sqlalchemy").text(query),
                    {"td": trade_date},
                ).fetchone()

            if row is None or row[0] < _BREADTH_MIN_STOCKS:
                logger.debug(
                    "breadth_positive: insufficient data (%s stocks) for %s",
                    row[0] if row else 0, trade_date,
                )
                return None

            total, above = int(row[0]), int(row[1])
            ratio = above / total
            result = ratio > _BREADTH_BULL_THRESHOLD
            logger.debug(
                "breadth_positive: %.1f%% above 50 EMA → %s | date=%s",
                ratio * 100, result, trade_date,
            )
            return result

        except Exception as exc:
            logger.warning("breadth_positive failed for %s: %s", trade_date, exc)
            return None

    @staticmethod
    def compute_distribution_count(nifty_df: pd.DataFrame) -> Optional[int]:
        """Count O'Neil distribution days in the last _DISTRIB_LOOKBACK sessions.

        A distribution day = Nifty closes down ≥ 0.2 % on volume higher than
        the prior session. Stalling days (up slightly but volume surge) count
        as half but this simplified version counts only clear down-volume days.

        Args:
            nifty_df: DataFrame with columns [trade_date, close, volume]
                      sorted ascending — the same data fetched by IndexRegime.

        Returns:
            Count of distribution days (0-25), or None on data error.
        """
        if nifty_df.empty or "close" not in nifty_df.columns:
            return None
        try:
            df = nifty_df.sort_values("trade_date").tail(_DISTRIB_LOOKBACK + 1).copy()
            df["pct_chg"] = df["close"].pct_change()
            df["vol_up"] = df["volume"] > df["volume"].shift(1)
            # Distribution: down day on higher volume
            dist_mask = (df["pct_chg"] <= -_DISTRIB_DOWN_MIN_PCT) & df["vol_up"]
            count = int(dist_mask.sum())
            logger.debug("distribution_count=%d | lookback=%d", count, _DISTRIB_LOOKBACK)
            return count
        except Exception as exc:
            logger.warning("distribution_count failed: %s", exc)
            return None

    @staticmethod
    def compute_follow_through_day(nifty_df: pd.DataFrame) -> Optional[bool]:
        """Detect O'Neil follow-through day (FTD) in the most recent rally attempt.

        Algorithm:
          1. Find the most recent correction (close drops ≥ 5 % from recent peak).
          2. Find the first day of the rally attempt (first up-close after the low).
          3. Check if, on day 4+ of the rally attempt, Nifty closes up ≥ 1.7 %
             on higher volume than the prior session.

        Args:
            nifty_df: DataFrame with [trade_date, close, volume] sorted ascending.

        Returns:
            True if a valid FTD exists in the data, False if not, None on error.
        """
        if nifty_df.empty:
            return None
        try:
            df = nifty_df.sort_values("trade_date").reset_index(drop=True).copy()
            closes = df["close"].values.astype(float)
            volumes = df["volume"].values.astype(float)

            # Rolling 20-day peak
            rolling_peak = pd.Series(closes).rolling(20, min_periods=5).max().values

            # Detect the most recent correction start (where close fell ≥ 5 % from peak)
            correction_start_idx: Optional[int] = None
            for i in range(len(closes) - 1, 4, -1):
                if rolling_peak[i] > 0 and (closes[i] / rolling_peak[i] - 1) <= -_FTD_CORRECTION_MIN_PCT:
                    correction_start_idx = i
                    break

            if correction_start_idx is None:
                return False  # No correction found — no FTD possible

            # Find local low after correction start
            low_idx = correction_start_idx + int(
                np.argmin(closes[correction_start_idx:])
            )

            # Rally attempt starts the first up-close day after the low
            rally_start_idx: Optional[int] = None
            for i in range(low_idx + 1, len(closes)):
                if i > 0 and closes[i] > closes[i - 1]:
                    rally_start_idx = i
                    break

            if rally_start_idx is None:
                return False

            # Check days from FTD_MIN_DAY onward (day 1 = rally_start_idx)
            for j in range(_FTD_MIN_DAY - 1, len(closes) - rally_start_idx):
                idx = rally_start_idx + j
                if idx >= len(closes) or idx == 0:
                    break
                pct_up = (closes[idx] / closes[idx - 1] - 1.0)
                vol_higher = volumes[idx] > volumes[idx - 1]
                if pct_up >= _FTD_MIN_UP_PCT and vol_higher:
                    logger.debug(
                        "FTD detected at idx=%d | up=%.2f%% | vol_higher=%s",
                        idx, pct_up * 100, vol_higher,
                    )
                    return True

            return False

        except Exception as exc:
            logger.warning("compute_follow_through_day failed: %s", exc)
            return None

    @staticmethod
    def compute_vix_risk_off(nifty_df: pd.DataFrame) -> Optional[bool]:
        """Return True when India VIX is above _VIX_RISK_OFF_THRESHOLD.

        Attempts to fetch ^INDIAVIX via yfinance.  Returns None (not False)
        when the fetch fails so the caller can skip the field rather than
        recording a misleading False.

        Args:
            nifty_df: Unused — signature kept symmetric with other methods.
                      VIX data is fetched separately (different ticker).

        Returns:
            True if VIX is elevated, False if calm, None if fetch failed.
        """
        try:
            import yfinance as yf
            raw = yf.download(
                tickers=_VIX_TICKER,
                period="5d",
                progress=False,
                auto_adjust=True,
            )
            if raw.empty:
                logger.warning("VIX fetch returned no data for %s", _VIX_TICKER)
                return None

            if isinstance(raw.columns, __import__("pandas").MultiIndex):
                raw.columns = raw.columns.get_level_values(0)

            latest_vix = float(raw["Close"].dropna().iloc[-1])
            result = latest_vix > _VIX_RISK_OFF_THRESHOLD
            logger.debug(
                "vix_risk_off: VIX=%.2f threshold=%.1f → %s",
                latest_vix, _VIX_RISK_OFF_THRESHOLD, result,
            )
            return result

        except Exception as exc:
            logger.warning("compute_vix_risk_off failed: %s", exc)
            return None
