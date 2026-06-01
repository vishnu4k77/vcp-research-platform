"""DataGapFiller — standalone daily data-integrity scanner and healer.

PROBLEM STATEMENT
-----------------
Yahoo Finance is a free, unreliable source.  On any given day, 1-5 tickers
silently return empty data.  The main pipeline (run.py) has no way to KNOW
data is missing — it processes whatever is in daily_price_data and moves on.
Over time, undetected gaps corrupt EMA / ATR computations and produce
wrong signals.

Two categories of missing data this module handles:
  A. GAP IN HISTORY   — ticker exists in DB but is missing one or more recent
                        trading days (temporary Yahoo outage, rate-throttle).
  B. NEW TICKER       — ticker was just added to nse_universe and has ZERO rows
                        in daily_price_data; needs full 5-year history download.

DESIGN PRINCIPLE
----------------
This module is COMPLETELY SEPARATE from DataIngestionPipeline.
It runs as its own daily script (scripts/fill_data_gaps.py) AFTER run.py.
Zero changes to app/main.py or any existing pipeline step.

NSE TRADING CALENDAR
--------------------
Derived from market_regime table (one row per NSE trading day after
backfill_regime.py runs).  No external calendar dependency.

DAILY SETUP
-----------
Run AFTER the main pipeline every trading day:

    # 3:45 PM IST — main pipeline
    python run.py

    # 4:15 PM IST — gap detection and fill (separate, standalone)
    python scripts/fill_data_gaps.py

    # 4:30 PM IST — if gaps were filled, recompute signals on complete data
    python run.py --skip-ingestion   (only needed when still_missing > 0 was fixed)

Windows Task Scheduler:
    Task 1: python run.py              → trigger at 15:45 IST daily
    Task 2: python fill_data_gaps.py   → trigger at 16:15 IST daily (30 min buffer)
"""

from datetime import date, timedelta
from typing import TYPE_CHECKING

import pandas as pd
from sqlalchemy import text

from app.config.db import engine
from app.config.logging_config import get_logger
from app.config.strategy_config import StrategyConfig
from app.data.yahoo_service import YahooFinanceService

if TYPE_CHECKING:
    from app.main import RateLimiter

logger = get_logger(__name__)


class DataGapFiller:
    """Detects and fills two types of missing price data.

    Type A — Gaps in existing history (missing recent trading days).
    Type B — New tickers with zero rows (need full 5-year history).

    All methods are static. Call DataGapFiller.run() as the entry point.
    """

    # ── NSE trading calendar ──────────────────────────────────────────────────

    @staticmethod
    def get_trading_days(lookback_days: int) -> list[date]:
        """Return last N NSE trading days from market_regime (ascending order).

        market_regime has exactly one row per NSE trading day — it IS the
        trading calendar.  No external dependency required.

        Args:
            lookback_days: How many recent trading days to return.

        Returns:
            List of date objects sorted ascending. Empty on DB error or if
            market_regime has not been populated yet.
        """
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT TOP (:n) trade_date "
                        "FROM market_regime "
                        "ORDER BY trade_date DESC"
                    ),
                    {"n": lookback_days},
                ).fetchall()
            trading_days = sorted([r[0] for r in rows])
            logger.debug(
                "Trading calendar: %d days (%s → %s)",
                len(trading_days),
                trading_days[0] if trading_days else "—",
                trading_days[-1] if trading_days else "—",
            )
            return trading_days
        except Exception as exc:
            logger.warning("Cannot load trading calendar from market_regime: %s", exc)
            return []

    # ── Type B: new tickers with zero history ─────────────────────────────────

    @staticmethod
    def find_new_tickers(tickers: list[str]) -> list[str]:
        """Return tickers that have ZERO rows in daily_price_data.

        These are newly added to nse_universe and have never been ingested,
        OR their first historical download silently failed (Yahoo empty response).

        Args:
            tickers: Full active ticker list from nse_universe.

        Returns:
            Tickers with no price history at all. Empty list on DB error.
        """
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text("SELECT DISTINCT symbol FROM daily_price_data")
                ).fetchall()
            have_any = {r[0] for r in rows}
            new = [t for t in tickers if t not in have_any]
            if new:
                logger.warning(
                    "New tickers with zero history: %d — %s", len(new), new
                )
            return new
        except Exception as exc:
            logger.warning("Cannot query daily_price_data for new-ticker check: %s", exc)
            return []

    # ── Type A: gaps in existing history ─────────────────────────────────────

    @staticmethod
    def find_gaps(
        tickers: list[str],
        trading_days: list[date],
    ) -> dict[str, date]:
        """Find the earliest missing trading day per ticker.

        Checks only tickers that already have SOME history (new tickers are
        handled separately by find_new_tickers).

        Args:
            tickers:      Active tickers that already have price history.
            trading_days: NSE trading days to audit (ascending, from get_trading_days).

        Returns:
            Dict mapping ticker → earliest missing date. Empty if no gaps.
        """
        if not tickers or not trading_days:
            return {}

        start_date = trading_days[0]
        end_date   = trading_days[-1]

        try:
            with engine.connect() as conn:
                present = pd.read_sql(
                    text(
                        "SELECT symbol, trade_date "
                        "FROM daily_price_data WITH (NOLOCK) "
                        "WHERE trade_date BETWEEN :s AND :e"
                    ),
                    conn,
                    params={"s": start_date, "e": end_date},
                )

            present_set: set[tuple[str, date]] = set(
                zip(present["symbol"], present["trade_date"])
            )

            gaps: dict[str, date] = {}
            for ticker in tickers:
                for day in trading_days:
                    if (ticker, day) not in present_set:
                        if ticker not in gaps or day < gaps[ticker]:
                            gaps[ticker] = day

            if gaps:
                logger.warning(
                    "Gaps found: %d ticker(s) missing data in %s → %s",
                    len(gaps), start_date, end_date,
                )
            else:
                logger.info(
                    "No gaps: all %d tickers complete for %s → %s",
                    len(tickers), start_date, end_date,
                )
            return gaps

        except Exception as exc:
            logger.error("Gap query failed: %s", exc, exc_info=True)
            return {}

    # ── Fill helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _fill_history(
        ticker: str,
        rate_limiter: "RateLimiter",
        latest_date: date | None = None,
    ) -> bool:
        """Download and insert price data for one ticker.

        Args:
            ticker:       Yahoo Finance symbol.
            rate_limiter: Shared rate limiter.
            latest_date:  If None → full 5-year historical download (new ticker).
                          If set  → incremental from (latest_date + 1) onward.

        Returns:
            True if data was successfully written (or already present).
        """
        from app.main import DataIngestionPipeline

        rate_limiter.acquire()

        try:
            clean_df = YahooFinanceService.fetch_stock_data(
                ticker=ticker,
                latest_date=latest_date,
            )

            if clean_df.empty:
                logger.warning("Fill: no data returned from Yahoo for %s", ticker)
                return False

            final_df = DataIngestionPipeline._validate_insert_dataframe(clean_df, ticker)
            if final_df.empty:
                return False

            # Filter rows already in DB — makes every call idempotent
            final_df = DataIngestionPipeline._filter_existing_dates(final_df, ticker)
            if final_df.empty:
                logger.debug("Fill: all rows already present for %s", ticker)
                return True

            DataIngestionPipeline._insert_dataframe(final_df, ticker)
            logger.info(
                "Fill: inserted %d row(s) for %s (latest_date=%s)",
                len(final_df), ticker, latest_date or "full history",
            )
            return True

        except Exception as exc:
            logger.error("Fill failed for %s: %s", ticker, exc, exc_info=True)
            return False

    # ── Main entry point ──────────────────────────────────────────────────────

    @staticmethod
    def run(
        tickers: list[str],
        rate_limiter: "RateLimiter",
        lookback_days: int = StrategyConfig.INGESTION_GAP_CHECK_DAYS,
    ) -> dict[str, int]:
        """Detect and fix all missing price data.

        Handles both types:
          Type B first — new tickers (full 5-year historical download).
          Type A second — recent gaps in existing history.

        Args:
            tickers:      Full active ticker list from nse_universe.
            rate_limiter: Rate limiter (matches Yahoo's free-tier limits).
            lookback_days: Number of recent trading days to audit for gaps.

        Returns:
            Summary dict:
              new_tickers_found, new_tickers_filled, new_tickers_failed,
              gaps_found, gaps_filled, gaps_failed, still_missing.
        """
        summary = {
            "new_tickers_found":  0,
            "new_tickers_filled": 0,
            "new_tickers_failed": 0,
            "gaps_found":         0,
            "gaps_filled":        0,
            "gaps_failed":        0,
            "still_missing":      0,
        }

        # ── Type B: new tickers (zero history) ───────────────────────────────
        new_tickers = DataGapFiller.find_new_tickers(tickers)
        summary["new_tickers_found"] = len(new_tickers)

        for ticker in new_tickers:
            ok = DataGapFiller._fill_history(ticker, rate_limiter, latest_date=None)
            if ok:
                summary["new_tickers_filled"] += 1
            else:
                summary["new_tickers_failed"] += 1
                summary["still_missing"] += 1

        # ── Type A: gaps in existing history ─────────────────────────────────
        trading_days = DataGapFiller.get_trading_days(lookback_days)

        if not trading_days:
            logger.warning(
                "market_regime is empty — run backfill_regime.py to enable gap detection."
            )
            return summary

        # Exclude new tickers from gap check (they were just handled above)
        existing_tickers = [t for t in tickers if t not in new_tickers]
        gaps = DataGapFiller.find_gaps(existing_tickers, trading_days)
        summary["gaps_found"] = len(gaps)

        for ticker, earliest_gap in gaps.items():
            # Start download from day before the gap so the incremental
            # fetch window includes the missing date(s).
            effective_latest = earliest_gap - timedelta(days=1)
            ok = DataGapFiller._fill_history(ticker, rate_limiter, latest_date=effective_latest)
            if ok:
                summary["gaps_filled"] += 1
            else:
                summary["gaps_failed"] += 1
                summary["still_missing"] += 1
                logger.error(
                    "STILL MISSING after retry: %s (gap from %s). "
                    "Verify ticker on Yahoo Finance. If delisted, run: "
                    "UPDATE nse_universe SET is_active=0 WHERE symbol='%s'",
                    ticker, earliest_gap, ticker.replace(".NS", ""),
                )

        logger.info(
            "Gap filler summary | "
            "new=%d(filled=%d,failed=%d) | "
            "gaps=%d(filled=%d,failed=%d) | "
            "still_missing=%d",
            summary["new_tickers_found"], summary["new_tickers_filled"], summary["new_tickers_failed"],
            summary["gaps_found"], summary["gaps_filled"], summary["gaps_failed"],
            summary["still_missing"],
        )
        return summary
