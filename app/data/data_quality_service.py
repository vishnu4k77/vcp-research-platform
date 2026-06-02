"""Daily data quality checks and auto-remediation.

Runs as Step 2 in MasterPipeline — AFTER ingestion, BEFORE features.
Must run before features so that stale rows do not contaminate EMA/ATR
calculations. stock_features and stock_signals are DROP+rebuild each run,
so they self-heal automatically once daily_price_data is clean.

Three checks in order:
  1. StaleData    — consecutive zero-volume same-price rows (Yahoo bad data)
                    → auto-DELETE confirmed stale runs (≥ 2 consecutive days)
                    → single zero-vol days left intact (NSE circuit breakers)
  2. Gaps         — date gaps > MAX_GAP_DAYS in last GAP_LOOKBACK_DAYS
                    → LOG only (pipeline re-downloads on next run)
  3. ThinHistory  — stocks with < MIN_HISTORY_DAYS trading rows
                    → LOG only (builds naturally over time)

All findings are written to data_quality_log for audit and trending.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import text

from app.config.db import engine
from app.config.logging_config import get_logger

logger = get_logger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
MIN_HISTORY_DAYS  = 200   # below this = EMA200 unreliable → WARNING
WARN_HISTORY_DAYS = 252   # below this = 52w high/low incomplete → CAUTION
MAX_GAP_DAYS      = 5     # calendar days before a gap is flagged (NSE ≤ 4 days for long weekend)
GAP_LOOKBACK_DAYS = 60    # only scan recent data for gaps (performance)
PRICE_TOLERANCE   = 0.01  # ₹ tolerance for "same price" comparison (float safety)


# ── SQL statements ────────────────────────────────────────────────────────────

# Detect stale rows: part of a consecutive run of ≥ 2 days with volume=0
# and price unchanged from the prior day.
# Single zero-volume days (NSE circuit breakers) are NOT matched
# because their prev_vol is > 0.
_STALE_DETECT_SQL = text("""
    WITH lagged AS (
        SELECT
            id,
            symbol,
            trade_date,
            close_price,
            volume,
            LAG(close_price) OVER (PARTITION BY symbol ORDER BY trade_date) AS prev_close,
            LAG(volume)      OVER (PARTITION BY symbol ORDER BY trade_date) AS prev_vol
        FROM daily_price_data
    )
    SELECT id, symbol, trade_date, close_price, volume
    FROM lagged
    WHERE volume   = 0
      AND prev_vol = 0
      AND ABS(close_price - prev_close) < :tol
""")

_STALE_DELETE_SQL = text("""
    DELETE FROM daily_price_data
    WHERE id IN (
        SELECT l.id
        FROM (
            SELECT
                id,
                volume,
                close_price,
                LAG(close_price) OVER (PARTITION BY symbol ORDER BY trade_date) AS prev_close,
                LAG(volume)      OVER (PARTITION BY symbol ORDER BY trade_date) AS prev_vol
            FROM daily_price_data
        ) l
        WHERE l.volume   = 0
          AND l.prev_vol = 0
          AND ABS(l.close_price - l.prev_close) < :tol
    )
""")

_GAP_DETECT_SQL = text("""
    WITH recent AS (
        SELECT symbol, trade_date,
               LAG(trade_date) OVER (PARTITION BY symbol ORDER BY trade_date) AS prev_date
        FROM daily_price_data
        WHERE trade_date >= DATEADD(day, -:lookback, CAST(GETDATE() AS DATE))
    )
    SELECT symbol,
           prev_date                              AS gap_start,
           trade_date                             AS gap_end,
           DATEDIFF(day, prev_date, trade_date)   AS gap_days
    FROM recent
    WHERE DATEDIFF(day, prev_date, trade_date) > :max_gap
    ORDER BY gap_days DESC
""")

_THIN_HISTORY_SQL = text("""
    SELECT symbol, COUNT(*) AS days_count, MIN(trade_date) AS earliest_date
    FROM daily_price_data
    GROUP BY symbol
    HAVING COUNT(*) < :warn_days
""")

_LOG_INSERT_SQL = text("""
    INSERT INTO data_quality_log (run_date, check_type, symbol, description, rows_affected)
    VALUES (:run_date, :check_type, :symbol, :description, :rows_affected)
""")

_LOG_SUMMARY_SQL = text("""
    SELECT check_type, COUNT(*) AS issues, SUM(rows_affected) AS rows
    FROM data_quality_log
    WHERE run_date = :run_date
    GROUP BY check_type
    ORDER BY check_type
""")


# ── Service ───────────────────────────────────────────────────────────────────

class DataQualityService:
    """Detects and fixes data quality issues in daily_price_data."""

    # ── Stale data ────────────────────────────────────────────────────────────

    @staticmethod
    def fix_stale_data(run_date: date | None = None) -> int:
        """Delete Yahoo stale rows (consecutive zero-vol, same price).

        Safe to run daily — idempotent (no stale rows = 0 rows deleted).
        Only removes rows that are part of a ≥ 2-day consecutive stale run.
        Single zero-volume days (NSE circuit breakers) are untouched.

        Returns:
            Number of rows deleted.
        """
        run_date = run_date or date.today()

        try:
            with engine.connect() as conn:
                stale_df = pd.read_sql(
                    _STALE_DETECT_SQL,
                    conn,
                    params={"tol": PRICE_TOLERANCE},
                )

            if stale_df.empty:
                logger.info("DataQuality | stale_data | no stale rows found")
                return 0

            affected_symbols = stale_df["symbol"].unique().tolist()
            total_rows = len(stale_df)

            logger.warning(
                "DataQuality | stale_data | %d stale rows in %d symbols — deleting",
                total_rows,
                len(affected_symbols),
            )

            with engine.begin() as conn:
                result = conn.execute(_STALE_DELETE_SQL, {"tol": PRICE_TOLERANCE})
                deleted = result.rowcount

            # Log each affected symbol
            with engine.begin() as conn:
                for sym, grp in stale_df.groupby("symbol"):
                    conn.execute(_LOG_INSERT_SQL, {
                        "run_date":     run_date,
                        "check_type":   "stale_delete",
                        "symbol":       sym,
                        "description":  (
                            f"{len(grp)} stale rows deleted | "
                            f"{grp['trade_date'].min()} → {grp['trade_date'].max()} | "
                            f"price={grp['close_price'].iloc[0]:.2f}"
                        ),
                        "rows_affected": len(grp),
                    })

            logger.info("DataQuality | stale_data | deleted %d rows across %d symbols",
                        deleted, len(affected_symbols))
            return deleted

        except Exception as exc:
            logger.error("DataQuality | fix_stale_data failed: %s", exc, exc_info=True)
            return 0

    # ── Gap detection ─────────────────────────────────────────────────────────

    @staticmethod
    def detect_gaps(run_date: date | None = None) -> int:
        """Find and log date gaps > MAX_GAP_DAYS in recent price history.

        Does NOT delete — the pipeline's gap filler re-downloads missing days
        on the next run.py call.

        Returns:
            Number of gaps found.
        """
        run_date = run_date or date.today()

        try:
            with engine.connect() as conn:
                gap_df = pd.read_sql(
                    _GAP_DETECT_SQL,
                    conn,
                    params={"lookback": GAP_LOOKBACK_DAYS, "max_gap": MAX_GAP_DAYS},
                )

            if gap_df.empty:
                logger.info("DataQuality | gaps | none found in last %d days", GAP_LOOKBACK_DAYS)
                return 0

            logger.warning(
                "DataQuality | gaps | %d gaps found — pipeline will back-fill on next run",
                len(gap_df),
            )

            with engine.begin() as conn:
                for _, row in gap_df.iterrows():
                    conn.execute(_LOG_INSERT_SQL, {
                        "run_date":      run_date,
                        "check_type":    "gap_detected",
                        "symbol":        row["symbol"],
                        "description":   (
                            f"{row['gap_days']} calendar days missing | "
                            f"{row['gap_start']} → {row['gap_end']}"
                        ),
                        "rows_affected": 0,
                    })

            return len(gap_df)

        except Exception as exc:
            logger.error("DataQuality | detect_gaps failed: %s", exc, exc_info=True)
            return 0

    # ── Thin history ──────────────────────────────────────────────────────────

    @staticmethod
    def detect_thin_history(run_date: date | None = None) -> int:
        """Log stocks with insufficient price history.

        Does NOT remove from universe — new listings build history naturally.
        Logged so users can track progress and identify bad Yahoo tickers.

        Returns:
            Number of stocks flagged.
        """
        run_date = run_date or date.today()

        try:
            with engine.connect() as conn:
                thin_df = pd.read_sql(
                    _THIN_HISTORY_SQL,
                    conn,
                    params={"warn_days": WARN_HISTORY_DAYS},
                )

            if thin_df.empty:
                logger.info("DataQuality | thin_history | all stocks have >= %d days",
                            WARN_HISTORY_DAYS)
                return 0

            critical = (thin_df["days_count"] < 63).sum()
            warning  = ((thin_df["days_count"] >= 63) & (thin_df["days_count"] < MIN_HISTORY_DAYS)).sum()
            caution  = ((thin_df["days_count"] >= MIN_HISTORY_DAYS) & (thin_df["days_count"] < WARN_HISTORY_DAYS)).sum()

            logger.info(
                "DataQuality | thin_history | critical=%d warning=%d caution=%d",
                critical, warning, caution,
            )

            with engine.begin() as conn:
                for _, row in thin_df.iterrows():
                    days = int(row["days_count"])
                    if days < 63:
                        severity = "CRITICAL"
                    elif days < MIN_HISTORY_DAYS:
                        severity = "WARNING"
                    else:
                        severity = "CAUTION"

                    conn.execute(_LOG_INSERT_SQL, {
                        "run_date":      run_date,
                        "check_type":    "thin_history",
                        "symbol":        row["symbol"],
                        "description":   (
                            f"{severity} | {days} days | "
                            f"from {row['earliest_date']}"
                        ),
                        "rows_affected": 0,
                    })

            return len(thin_df)

        except Exception as exc:
            logger.error("DataQuality | detect_thin_history failed: %s", exc, exc_info=True)
            return 0

    # ── Entry point ───────────────────────────────────────────────────────────

    @staticmethod
    def run() -> None:
        """Run all three checks in sequence and print a summary.

        Called by MasterPipeline after ingestion and before features.
        Non-blocking — all exceptions are caught internally.
        """
        today = date.today()

        logger.info("DataQuality | starting daily checks | %s", today)

        stale_deleted = DataQualityService.fix_stale_data(today)
        gaps_found    = DataQualityService.detect_gaps(today)
        thin_found    = DataQualityService.detect_thin_history(today)

        logger.info(
            "DataQuality | complete | stale_deleted=%d gaps=%d thin_history=%d",
            stale_deleted, gaps_found, thin_found,
        )

        # Print summary to data_quality_log for the day
        try:
            with engine.connect() as conn:
                rows = conn.execute(_LOG_SUMMARY_SQL, {"run_date": today}).fetchall()
            if rows:
                logger.info("DataQuality | today's log summary:")
                for r in rows:
                    logger.info("  %-20s  issues=%-4d  rows_affected=%d", r[0], r[1], r[2] or 0)
        except Exception:
            pass
