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

            # Include Volume for Phase 2 distribution-day and FTD computation.
            vol_col = "Volume" if "Volume" in raw.columns else None
            cols = [date_col, "Close"] + ([vol_col] if vol_col else [])
            df = raw[cols].copy()
            new_names = ["trade_date", "close"] + (["volume"] if vol_col else [])
            df.columns = new_names

            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            if "volume" in df.columns:
                df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
            else:
                df["volume"] = 0.0  # fallback — Phase 2 breadth will degrade gracefully

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
    def _enrich(df: pd.DataFrame) -> pd.DataFrame:
        """Compute EMA50, EMA200, slope, and higher-highs for every row in df.

        This is the shared computation used by both single-day detection
        and the full historical backfill.  Requires at least 220 rows for
        EMA 200 to be meaningful; caller is responsible for that check.

        Args:
            df: DataFrame with columns [trade_date, close], sorted ascending.

        Returns:
            df copy with ema_50, ema_200, ema_200_slope_pct, peak_20,
            higher_highs columns appended.
        """
        slope_period = StrategyConfig.EMA_SLOPE_PERIOD

        df = df.copy()
        df["ema_50"]  = df["close"].ewm(span=50,  adjust=False).mean()
        df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()

        prior_ema200 = df["ema_200"].shift(slope_period)
        df["ema_200_slope_pct"] = (
            (df["ema_200"] - prior_ema200)
            / prior_ema200.replace(0, np.nan)
            * 100
        ).fillna(0.0)

        df["peak_20"]     = df["close"].rolling(20).max()
        df["higher_highs"] = df["peak_20"] > df["peak_20"].shift(20)

        return df

    @staticmethod
    def _row_to_result(row: pd.Series) -> RegimeResult:
        """Convert a single enriched Nifty row into a RegimeResult.

        Args:
            row: One row from a DataFrame produced by _enrich().

        Returns:
            RegimeResult with regime_score and market_status populated.
        """
        nifty_close  = float(row["close"])
        ema_50       = float(row["ema_50"])
        ema_200      = float(row["ema_200"])
        ema_slope    = float(row["ema_200_slope_pct"])
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
    def _classify(df: pd.DataFrame) -> Optional[RegimeResult]:
        """Classify regime for the latest bar in df (single-day detection).

        Args:
            df: DataFrame with columns [trade_date, close], sorted ascending.

        Returns:
            RegimeResult for the most recent row, or None if < 220 bars.
        """
        if len(df) < 220:
            logger.warning(
                "Insufficient Nifty history for regime: %d bars (need >= 220)", len(df)
            )
            return None

        enriched = IndexRegime._enrich(df)
        return IndexRegime._row_to_result(enriched.iloc[-1])

    @staticmethod
    def classify_history(df: pd.DataFrame) -> list[RegimeResult]:
        """Classify regime for ALL rows in df — used for historical backfill.

        Requires >= 220 bars total; rows before EMA 200 has warmed up are
        skipped (their ema_200 values are unreliable).  Returns one
        RegimeResult per valid trading day, sorted ascending by trade_date.

        Args:
            df: DataFrame with columns [trade_date, close], sorted ascending.
                Should cover at least 1 year plus a warmup buffer.

        Returns:
            List of RegimeResult, one per row from bar 220 onwards.
            Empty list if df has fewer than 220 rows.
        """
        if len(df) < 220:
            logger.warning(
                "Insufficient Nifty history for backfill: %d bars (need >= 220)", len(df)
            )
            return []

        enriched = IndexRegime._enrich(df)

        results: list[RegimeResult] = []
        for _, row in enriched.iloc[220:].iterrows():
            try:
                results.append(IndexRegime._row_to_result(row))
            except Exception as exc:
                logger.debug("Skipping row %s: %s", row.get("trade_date"), exc)

        logger.info("classify_history complete | %d regime rows computed", len(results))
        return results

    @staticmethod
    def detect() -> Optional[RegimeResult]:
        """Entry point: fetches Nifty 50 data and returns the current regime.

        Phase 2 breadth fields (breadth_positive, distribution_count,
        follow_through_day, vix_risk_off) are computed here and attached
        to the RegimeResult.  Each field falls back gracefully to None if
        data is unavailable — RegimePipeline persists None as SQL NULL.

        Returns:
            RegimeResult for today, or None on data fetch failure.
        """
        df = IndexRegime._fetch_nifty()

        if df.empty:
            return None

        result = IndexRegime._classify(df)

        if result is None:
            return None

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

        # ── Phase 2 breadth indicators ────────────────────────────────────────
        try:
            from app.ml.regime_breadth import RegimeBreadthService
            result.breadth_positive   = RegimeBreadthService.compute_breadth_positive(result.trade_date)
            result.distribution_count = RegimeBreadthService.compute_distribution_count(df)
            result.follow_through_day = RegimeBreadthService.compute_follow_through_day(df)
            result.vix_risk_off       = RegimeBreadthService.compute_vix_risk_off(df)
            logger.info(
                "Phase 2 regime | breadth=%s | dist_days=%s | ftd=%s | vix_off=%s",
                result.breadth_positive,
                result.distribution_count,
                result.follow_through_day,
                result.vix_risk_off,
            )
        except Exception as exc:
            logger.warning("Phase 2 regime computation failed (non-critical): %s", exc)

        return result
