from dataclasses import dataclass
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from app.config.logging_config import get_logger
from app.config.strategy_config import StrategyConfig
from app.services.market_query_service import MarketQueryService

logger = get_logger(__name__)

_LOOKBACK_DAYS = 400   # enough history for EMA 200 + slope warmup


@dataclass
class RegimeResult:
    """
    Snapshot of the market regime for a single trade_date.

    regime_score: 0-100 based on five equally-weighted conditions.
    market_status: state_code from the market_states SQL table.
    Phase-2 fields (breadth_positive, vix_risk_off, distribution_count,
    follow_through_day) are None until those modules are built.
    """

    trade_date: date
    market_status: str
    regime_score: float
    nifty_close: float
    ema_50: float
    ema_200: float
    nifty_above_50ema: bool
    nifty_above_200ema: bool
    ema_50_above_200ema: bool
    ema_200_slope_pct: float
    higher_highs: bool

    # Phase 2 — not yet implemented
    breadth_positive: Optional[bool] = None
    vix_risk_off: Optional[bool] = None
    distribution_count: Optional[int] = None
    follow_through_day: Optional[bool] = None


class IndexRegime:
    """
    Classifies market regime using Nifty 50 (^NSEI) price data from Yahoo Finance.

    Scoring rubric — 20 points each, total 0-100:
      1. Nifty close > EMA 50   (short-term health)
      2. Nifty close > EMA 200  (long-term trend)
      3. EMA 50 > EMA 200       (trend alignment)
      4. EMA 200 slope positive  (trend direction)
      5. Higher highs (20-day)   (momentum)

    Score → market_status mapping is stored in the market_states SQL table
    (state_code, score_min, score_max columns). Call setup_db.py to seed defaults.
    """

    @staticmethod
    def _fetch_nifty() -> pd.DataFrame:
        """Returns a clean DataFrame with columns [trade_date, close]."""

        try:
            raw = yf.download(
                tickers=StrategyConfig.NIFTY_TICKER,
                period=f"{_LOOKBACK_DAYS}d",
                progress=False,
                auto_adjust=True,
            )

            if raw.empty:
                logger.error("Yahoo Finance returned no data for %s", StrategyConfig.NIFTY_TICKER)
                return pd.DataFrame()

            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)

            raw.reset_index(inplace=True)

            date_col = next(
                (c for c in ("Date", "Datetime", "index") if c in raw.columns),
                None,
            )

            if date_col is None or "Close" not in raw.columns:
                logger.error(
                    "Unexpected %s data structure: %s",
                    StrategyConfig.NIFTY_TICKER,
                    raw.columns.tolist(),
                )
                return pd.DataFrame()

            df = raw[[date_col, "Close"]].copy()
            df.columns = ["trade_date", "close"]
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df.dropna(subset=["close"], inplace=True)
            df.sort_values("trade_date", inplace=True)
            df.reset_index(drop=True, inplace=True)

            logger.debug("Fetched %d %s bars", len(df), StrategyConfig.NIFTY_TICKER)
            return df

        except Exception as exc:
            logger.error(
                "%s fetch failed: %s", StrategyConfig.NIFTY_TICKER, exc, exc_info=True
            )
            return pd.DataFrame()

    @staticmethod
    def _classify(df: pd.DataFrame) -> Optional[RegimeResult]:
        """
        Computes regime from the latest bar in df.
        Returns None if not enough history (need >= 220 bars for EMA 200 warmup).
        """

        if len(df) < 220:
            logger.warning(
                "Insufficient Nifty history for regime: %d bars (need >= 220)", len(df)
            )
            return None

        slope_period = StrategyConfig.EMA_SLOPE_PERIOD

        df = df.copy()
        df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
        df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()

        prior_ema200 = df["ema_200"].shift(slope_period)
        df["ema_200_slope_pct"] = (
            (df["ema_200"] - prior_ema200)
            / prior_ema200.replace(0, np.nan)
            * 100
        ).fillna(0.0)

        df["peak_20"] = df["close"].rolling(20).max()
        df["higher_highs"] = df["peak_20"] > df["peak_20"].shift(20)

        row = df.iloc[-1]

        nifty_close = float(row["close"])
        ema_50 = float(row["ema_50"])
        ema_200 = float(row["ema_200"])
        ema_slope = float(row["ema_200_slope_pct"])
        higher_highs = bool(row["higher_highs"])

        score = 0
        if nifty_close > ema_50:   score += 20
        if nifty_close > ema_200:  score += 20
        if ema_50 > ema_200:       score += 20
        if ema_slope > 0:          score += 20
        if higher_highs:           score += 20

        status = MarketQueryService.get_market_state_for_score(score)

        trade_date = row["trade_date"]
        if hasattr(trade_date, "date"):
            trade_date = trade_date.date()

        return RegimeResult(
            trade_date=trade_date,
            market_status=status,
            regime_score=float(score),
            nifty_close=nifty_close,
            ema_50=ema_50,
            ema_200=ema_200,
            nifty_above_50ema=nifty_close > ema_50,
            nifty_above_200ema=nifty_close > ema_200,
            ema_50_above_200ema=ema_50 > ema_200,
            ema_200_slope_pct=ema_slope,
            higher_highs=higher_highs,
        )

    @staticmethod
    def detect() -> Optional[RegimeResult]:
        """
        Entry point: fetches Nifty 50 data and returns the current regime.
        Returns None on data fetch failure or insufficient history.
        """

        df = IndexRegime._fetch_nifty()

        if df.empty:
            return None

        result = IndexRegime._classify(df)

        if result:
            logger.info(
                "Regime detected | %s | score=%.0f | Nifty=%.0f | "
                "EMA50=%.0f | EMA200=%.0f | slope=%.2f%%",
                result.market_status,
                result.regime_score,
                result.nifty_close,
                result.ema_50,
                result.ema_200,
                result.ema_200_slope_pct,
            )

        return result
