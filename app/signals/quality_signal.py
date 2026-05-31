"""Quality signal — fundamental scorecard with technical proxy fallback.

Phase 1 (no fundamentals data):
    quality_signal = 1 if stock passes a technical quality gate
    (Stage 2 + strong trend + adequate volume).

Phase 2 (stock_fundamentals populated by fetch_fundamentals.py):
    quality_signal = 1 if the stock's quality_score ≥ QUALITY_PASS_SCORE,
    where quality_score is the count of fundamental criteria passed
    (ROE, ROCE, D/E, growth rates, promoter holding, etc.).

The switch between phases is automatic: when stock_fundamentals has data for
a symbol, the fundamental path is taken; otherwise the technical proxy is used.
This means you can run fetch_fundamentals.py incrementally — symbols with data
get the real quality signal; unscraped symbols fall back gracefully.
"""

import pandas as pd
import numpy as np
from sqlalchemy import text

from app.config.db import engine
from app.config.logging_config import get_logger
from app.config.strategy_config import FundamentalsConfig, StrategyConfig

logger = get_logger(__name__)

# Phase 1 threshold: trend_score is 0-4; require ≥ 3 conditions for the proxy
_TREND_SCORE_MIN_PROXY = 3


def _load_quality_pass_score() -> int:
    """Load quality_pass_score from fundamental_filter_config, fall back to Python config.

    Reads the 'quality_pass_score' row from SQL so the threshold can be changed
    in the database without redeploying code.  Falls back to
    FundamentalsConfig.QUALITY_PASS_SCORE on any DB error.

    Returns:
        Minimum quality_score an instrument must have for quality_signal = 1.
    """
    query = text(
        "SELECT threshold FROM fundamental_filter_config "
        "WHERE filter_name = 'quality_pass_score' AND is_active = 1"
    )
    try:
        with engine.connect() as conn:
            row = conn.execute(query).fetchone()
        if row is not None:
            return int(row[0])
    except Exception as exc:
        logger.debug("Could not load quality_pass_score from DB: %s", exc)
    return FundamentalsConfig.QUALITY_PASS_SCORE


def _load_latest_quality_scores() -> dict[str, int]:
    """Fetch the most recent quality_score per symbol from stock_fundamentals.

    Returns the latest row per symbol (by trade_date desc) so stale data is
    used when fresher data isn't available rather than dropping to proxy.

    Returns:
        Dict mapping bare NSE symbol → quality_score (int 0-10).
        Empty dict when stock_fundamentals is empty or query fails.
    """
    query = text("""
        SELECT symbol, quality_score
        FROM (
            SELECT
                symbol,
                quality_score,
                ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) AS rn
            FROM stock_fundamentals
            WHERE quality_score IS NOT NULL
        ) ranked
        WHERE rn = 1
    """)
    try:
        with engine.connect() as conn:
            rows = conn.execute(query).fetchall()
        scores = {r[0]: int(r[1]) for r in rows if r[1] is not None}
        logger.debug("Loaded fundamental quality scores for %d symbols", len(scores))
        return scores
    except Exception as exc:
        logger.warning(
            "Could not load stock_fundamentals — falling back to technical proxy: %s", exc
        )
        return {}


class QualitySignal:
    """Compute quality_signal using fundamentals when available, technical proxy otherwise.

    The signal logic is:
      Fundamental path (preferred):
        quality_signal = 1  if quality_score >= FundamentalsConfig.QUALITY_PASS_SCORE
        quality_signal = 0  otherwise

      Technical proxy path (Phase 1 fallback):
        quality_signal = 1  if Stage 2 AND trend_score >= 3 AND volume adequate
        quality_signal = 0  otherwise

    The path is chosen per-symbol based on whether fundamental data exists.
    """

    @staticmethod
    def calculate(
        df: pd.DataFrame,
        fundamental_scores: dict | None = None,
        pass_score: int | None = None,
    ) -> pd.DataFrame:
        """Apply quality signal logic to a symbol's feature DataFrame.

        Args:
            df: Feature rows for one symbol, with at least these columns:
                stage2_signal, trend_score, avg_volume_20.
                Symbol is taken from df["symbol"].iloc[0].
            fundamental_scores: Pre-loaded dict of bare symbol → quality_score.
                When None, loaded from DB (use the pre-loaded form in batch runs
                to avoid one DB round-trip per symbol).
            pass_score: Minimum quality_score for quality_signal = 1.
                When None, loaded from DB.

        Returns:
            df with quality_signal column (BIT: 0 or 1) added.
        """
        if df.empty:
            return df

        df = df.copy()

        if fundamental_scores is None:
            fundamental_scores = _load_latest_quality_scores()

        # Normalise numeric columns used by the technical proxy path
        for col in ("trend_score", "avg_volume_20"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

        # Determine which symbols in this df have fundamental data.
        # df may contain one symbol or multiple if called on a batch.
        symbol_col = df["symbol"] if "symbol" in df.columns else pd.Series([""] * len(df))

        # Strip Yahoo suffix to match bare symbol in stock_fundamentals
        bare_symbols = symbol_col.str.replace(
            StrategyConfig.YAHOO_NSE_SUFFIX, "", regex=False
        )

        has_fundamentals = bare_symbols.map(lambda s: s in fundamental_scores)

        # ── Fundamental path ──────────────────────────────────────────────────
        if pass_score is None:
            pass_score = _load_quality_pass_score()
        fundamental_quality = bare_symbols.map(
            lambda s: fundamental_scores.get(s, 0) >= pass_score
        )

        # ── Technical proxy path ──────────────────────────────────────────────
        has_stage2 = (
            df["stage2_signal"] == 1
            if "stage2_signal" in df.columns
            else pd.Series(False, index=df.index)
        )
        strong_trend  = df["trend_score"] >= _TREND_SCORE_MIN_PROXY
        adequate_vol  = df["avg_volume_20"] >= StrategyConfig.MIN_AVG_VOLUME_20
        proxy_quality = has_stage2 & strong_trend & adequate_vol

        # Merge: use fundamental result where data exists, proxy otherwise
        df["quality_signal"] = np.where(
            has_fundamentals,
            fundamental_quality.astype(int),
            proxy_quality.astype(int),
        )

        fundamental_count = int(has_fundamentals.sum())
        proxy_count       = len(df) - fundamental_count
        logger.debug(
            "quality_signal | fundamental_path=%d proxy_path=%d",
            fundamental_count, proxy_count,
        )

        return df
