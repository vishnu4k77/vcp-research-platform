"""Empirical confidence calibrator for T1 / T2 / T3 price targets.

Algorithm
---------
For every historical breakout or BO-ready signal that has target prices stored
in stock_signals, we look N days forward in daily_price_data and check whether
the high_price ever crossed each target level.  Hit rates are grouped by
(score_bucket, market_regime) and stored in target_confidence_model.

On each pipeline run the model is rebuilt when the cached version is older
than REBUILD_DAYS, then the current-date stock_signals rows get their
confidence_t1 / confidence_t2 / confidence_t3 columns updated.

T3 is the conservative near-term target (pivot + base_range × T3_MULTIPLIER).
Its confidence is expected to be ≥ 90 % for quality setups — the UI highlights
it green when it crosses CONF_T3_GREEN_THRESHOLD.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import text

from app.config.logging_config import get_logger
from app.config.strategy_config import TargetConfig
from app.config.db import engine

logger = get_logger(__name__)

# Rebuild the model at most once every N days to avoid heavy recomputation
REBUILD_DAYS = 7


def _score_bucket(score: float) -> int:
    """Map composite_score to its bucket: 70 / 50 / 30 / 0."""
    if score >= 70:
        return 70
    if score >= 50:
        return 50
    if score >= 30:
        return 30
    return 0


def _model_is_fresh() -> bool:
    """Return True if target_confidence_model was built within REBUILD_DAYS."""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT MAX(computed_at) FROM target_confidence_model")
        ).fetchone()
    if not row or row[0] is None:
        return False
    age = dt.datetime.utcnow() - row[0]
    return age.days < REBUILD_DAYS


def build_model() -> pd.DataFrame:
    """Compute empirical T1/T2/T3 hit rates and persist to target_confidence_model.

    Uses SQL CROSS APPLY to find the max forward high price for each
    historical breakout signal within the configured hold-day windows.

    Returns the model as a DataFrame (also written to the DB table).
    """
    logger.info("ConfidenceCalibrator: building hit-rate model …")

    sql = f"""
        SELECT
            s.score_bucket,
            ISNULL(me.market_state, 'UNKNOWN')          AS market_regime,
            COUNT(*)                                      AS n,
            AVG(CAST(t1.hit AS FLOAT)) * 100             AS hit_rate_t1,
            AVG(CAST(t2.hit AS FLOAT)) * 100             AS hit_rate_t2,
            AVG(CAST(t3.hit AS FLOAT)) * 100             AS hit_rate_t3
        FROM (
            SELECT
                symbol, trade_date,
                target_1_price, target_2_price,
                pivot_price,
                ISNULL(
                    target_3_price,
                    pivot_price + (pivot_price - pivot_price * (1 - base_range_pct / 100.0))
                    * {TargetConfig.T3_MULTIPLIER}
                ) AS t3_price,
                CASE WHEN composite_score >= 70 THEN 70
                     WHEN composite_score >= 50 THEN 50
                     WHEN composite_score >= 30 THEN 30
                     ELSE 0 END AS score_bucket
            FROM stock_signals
            WHERE (breakout_signal = 1 OR breakout_ready_signal = 1)
              AND target_1_price IS NOT NULL
              AND target_2_price IS NOT NULL
              AND pivot_price    IS NOT NULL
              AND trade_date <= DATEADD(day, -{TargetConfig.CONF_HOLD_DAYS_T2}, GETDATE())
        ) s
        LEFT JOIN market_environment me ON me.trade_date = s.trade_date
        CROSS APPLY (
            SELECT CASE WHEN MAX(p.high_price) >= s.target_1_price THEN 1 ELSE 0 END AS hit
            FROM daily_price_data p
            WHERE p.symbol     = s.symbol
              AND p.trade_date > s.trade_date
              AND p.trade_date <= DATEADD(day, {TargetConfig.CONF_HOLD_DAYS_T1}, s.trade_date)
        ) t1
        CROSS APPLY (
            SELECT CASE WHEN MAX(p.high_price) >= s.target_2_price THEN 1 ELSE 0 END AS hit
            FROM daily_price_data p
            WHERE p.symbol     = s.symbol
              AND p.trade_date > s.trade_date
              AND p.trade_date <= DATEADD(day, {TargetConfig.CONF_HOLD_DAYS_T2}, s.trade_date)
        ) t2
        CROSS APPLY (
            SELECT CASE WHEN MAX(p.high_price) >= s.t3_price THEN 1 ELSE 0 END AS hit
            FROM daily_price_data p
            WHERE p.symbol     = s.symbol
              AND p.trade_date > s.trade_date
              AND p.trade_date <= DATEADD(day, {TargetConfig.CONF_HOLD_DAYS_T3}, s.trade_date)
        ) t3
        GROUP BY s.score_bucket, ISNULL(me.market_state, 'UNKNOWN')
        HAVING COUNT(*) >= {TargetConfig.CONF_MIN_SAMPLES}
    """

    model_df = pd.read_sql(sql, engine)

    if model_df.empty:
        logger.warning("ConfidenceCalibrator: no rows returned — model not updated")
        return model_df

    now = dt.datetime.utcnow()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM target_confidence_model"))
        conn.execute(
            text("""
                INSERT INTO target_confidence_model
                  (score_bucket, market_regime, hit_rate_t1, hit_rate_t2, hit_rate_t3,
                   sample_count, hold_days_t1, hold_days_t2, hold_days_t3, computed_at)
                VALUES
                  (:bucket, :regime, :t1, :t2, :t3, :n, :h1, :h2, :h3, :ts)
            """),
            [
                {
                    "bucket": int(row["score_bucket"]),
                    "regime": str(row["market_regime"]),
                    "t1":     round(float(row["hit_rate_t1"]), 1),
                    "t2":     round(float(row["hit_rate_t2"]), 1),
                    "t3":     round(float(row["hit_rate_t3"]), 1),
                    "n":      int(row["n"]),
                    "h1":     TargetConfig.CONF_HOLD_DAYS_T1,
                    "h2":     TargetConfig.CONF_HOLD_DAYS_T2,
                    "h3":     TargetConfig.CONF_HOLD_DAYS_T3,
                    "ts":     now,
                }
                for _, row in model_df.iterrows()
            ],
        )

    logger.info(
        "ConfidenceCalibrator: model built — %d bucket×regime combinations",
        len(model_df),
    )
    return model_df


def load_model() -> dict[tuple[int, str], dict[str, float]]:
    """Load the persisted confidence model into a fast in-memory lookup dict.

    Returns:
        {(score_bucket, regime): {"t1": float, "t2": float, "t3": float}}
    """
    df = pd.read_sql(
        "SELECT score_bucket, market_regime, hit_rate_t1, hit_rate_t2, hit_rate_t3 "
        "FROM target_confidence_model",
        engine,
    )
    return {
        (int(r.score_bucket), str(r.market_regime)): {
            "t1": float(r.hit_rate_t1),
            "t2": float(r.hit_rate_t2),
            "t3": float(r.hit_rate_t3),
        }
        for _, r in df.iterrows()
    }


def update_today_signals(trade_date: Optional[dt.date] = None) -> int:
    """Populate confidence_t1/t2/t3 for all stock_signals rows on trade_date.

    Looks up each row's (score_bucket, regime) in the confidence model.
    Falls back to the formula-based upside_prob_pct when the model bucket
    has no data.

    Args:
        trade_date: Date to update; defaults to today.

    Returns:
        Number of rows updated.
    """
    if trade_date is None:
        trade_date = dt.date.today()

    model = load_model()
    if not model:
        logger.warning("ConfidenceCalibrator: model empty — skipping confidence update")
        return 0

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT market_state FROM market_environment WHERE trade_date = :d"),
            {"d": trade_date},
        ).fetchone()
    regime = row[0] if row else "UNKNOWN"

    signals_df = pd.read_sql(
        text(
            "SELECT id, composite_score, upside_prob_pct "
            "FROM stock_signals "
            "WHERE trade_date = :d AND target_1_price IS NOT NULL"
        ),
        engine,
        params={"d": trade_date},
    )

    if signals_df.empty:
        return 0

    rows: list[dict] = []
    for _, sig in signals_df.iterrows():
        bucket = _score_bucket(float(sig["composite_score"]))
        conf   = model.get((bucket, regime)) or model.get((bucket, "UNKNOWN"))

        if conf:
            c1 = round(conf["t1"], 1)
            c2 = round(conf["t2"], 1)
            c3 = round(conf["t3"], 1)
        else:
            prob = float(sig["upside_prob_pct"]) if pd.notna(sig["upside_prob_pct"]) else 40.0
            c2   = round(prob, 1)
            c1   = round(min(prob * 1.5, 95.0), 1)
            c3   = round(min(prob * 2.0, 99.0), 1)

        rows.append({"c1": c1, "c2": c2, "c3": c3, "sid": int(sig["id"])})

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE stock_signals "
                "SET confidence_t1 = :c1, confidence_t2 = :c2, confidence_t3 = :c3 "
                "WHERE id = :sid"
            ),
            rows,
        )

    logger.info(
        "ConfidenceCalibrator: updated %d rows for %s (regime=%s)",
        len(rows), trade_date, regime,
    )
    return len(rows)


def run(trade_date: Optional[dt.date] = None) -> None:
    """Entry point called from MasterPipeline."""
    if not _model_is_fresh():
        build_model()
    else:
        logger.info("ConfidenceCalibrator: model is fresh — skipping rebuild")

    update_today_signals(trade_date)
