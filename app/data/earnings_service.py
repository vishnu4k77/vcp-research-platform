"""Earnings dates fetch and caching service — Yahoo Finance → stock_earnings_dates.

Responsibilities (single module, three concerns kept cleanly separated):
  1. Fetch  — yf.Ticker(symbol).earnings_dates via Yahoo Finance
  2. Store  — insert new dates into stock_earnings_dates (skip existing via UNIQUE constraint)
  3. Load   — return {symbol: set(date)} map consumed by FeaturePipeline

All thresholds come from EarningsConfig. No magic numbers in this file.
Rate limiting and retry follow the same patterns as YahooFinanceService.
"""

import time
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf
from tenacity import (
    retry,
    retry_if_exception_type,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)
from sqlalchemy import text

from app.config.db import engine
from app.config.logging_config import get_logger
from app.config.strategy_config import EarningsConfig, StrategyConfig

logger = get_logger(__name__)


class EarningsNetworkError(Exception):
    """Raised when a Yahoo Finance earnings fetch fails due to a network-level error.

    Distinguished from data-format errors (KeyError/TypeError/ValueError) which
    are non-retryable.  refresh_all() tracks consecutive EarningsNetworkError
    raises and aborts early when MAX_CONSECUTIVE_NETWORK_FAILURES is reached.
    """


# ── Retry wrapper for Yahoo Ticker calls ─────────────────────────────────────
# Mirrors the pattern in yahoo_service.py: retry on any exception with
# exponential backoff, parameters from config.

@retry(
    # Do NOT retry on structural/dependency errors — they won't resolve on the next attempt.
    #   ImportError / ModuleNotFoundError — missing package (install required)
    #   KeyError / TypeError / ValueError  — Yahoo returned unexpected data shape for this symbol
    #   EarningsNetworkError               — re-raised after retries exhausted; caller handles it
    retry=retry_if_not_exception_type(
        (ImportError, ModuleNotFoundError, KeyError, TypeError, ValueError, EarningsNetworkError)
    ),
    stop=stop_after_attempt(EarningsConfig.RETRY_ATTEMPTS),
    wait=wait_exponential(
        multiplier=2,
        min=EarningsConfig.RETRY_MIN_WAIT_SECONDS,
        max=EarningsConfig.RETRY_MAX_WAIT_SECONDS,
    ),
    reraise=True,
    before_sleep=before_sleep_log(logger, 20),
)
def _yf_earnings_dates(symbol: str) -> Optional[pd.DataFrame]:
    """Single yf.Ticker(symbol).earnings_dates attempt — wrapped externally with retry.

    Args:
        symbol: Yahoo Finance ticker (e.g. 'NYKAA.NS').

    Returns:
        DataFrame with DatetimeIndex of earnings dates, or None if unavailable.

    Raises:
        EarningsNetworkError: On DNS failure, connection refused, or timeout — after
            all retry attempts are exhausted. Caller (refresh_all) tracks consecutive
            EarningsNetworkError and aborts early if Yahoo is broadly unreachable.
    """
    try:
        return yf.Ticker(symbol).earnings_dates
    except (KeyError, TypeError, ValueError):
        raise   # structural — re-raise so tenacity does NOT retry
    except Exception as exc:
        # Network-level failure (DNS, timeout, connection refused).
        # Wrap in EarningsNetworkError so refresh_all() can distinguish it
        # from a data-format error and abort early when Yahoo is unreachable.
        raise EarningsNetworkError(str(exc)) from exc


class EarningsService:
    """Fetches quarterly earnings dates from Yahoo Finance and caches them in SQL.

    Stale-check logic (EarningsConfig.STALE_AFTER_DAYS) prevents unnecessary
    re-fetches on every daily pipeline run.  Only dates within
    EarningsConfig.MAX_FUTURE_DAYS are stored to avoid polluting the table with
    Yahoo's speculative far-future estimates.

    Public interface:
        EarningsService.refresh_all(symbols)   — called by MasterPipeline pre-features
        EarningsService.load_earnings_map()    — called by FeaturePipeline per-symbol loop
    """

    # ── Fetch ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _fetch_from_yahoo(symbol: str) -> list[date]:
        """Fetch earnings dates for one symbol from Yahoo Finance.

        Strips timezone info and filters to dates within MAX_FUTURE_DAYS.
        Returns an empty list on any error so the caller can continue safely.

        Args:
            symbol: Yahoo Finance ticker (e.g. 'NYKAA.NS').

        Returns:
            List of earnings dates as datetime.date objects.
        """
        try:
            df = _yf_earnings_dates(symbol)
        except (KeyError, TypeError, ValueError) as exc:
            # Yahoo returned an unexpected data shape — not a network error, not retryable.
            logger.debug("Yahoo earnings: unexpected structure for %s: %s", symbol, exc)
            return []
        except EarningsNetworkError:
            # Network failure after retries exhausted — re-raise so refresh_all()
            # can track consecutive failures and abort early if Yahoo is unreachable.
            raise
        except Exception as exc:
            logger.warning("Yahoo earnings fetch failed for %s: %s", symbol, exc)
            return []

        if df is None or df.empty:
            logger.debug("No earnings dates returned for %s", symbol)
            return []

        cutoff = date.today() + timedelta(days=EarningsConfig.MAX_FUTURE_DAYS)
        dates: list[date] = []

        for ts in df.index:
            try:
                # DatetimeIndex entries may be tz-aware — normalize to date
                d = pd.Timestamp(ts).tz_localize(None).date() if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts).date()
                if d <= cutoff:
                    dates.append(d)
            except Exception:
                continue

        logger.debug("Yahoo returned %d earnings dates for %s", len(dates), symbol)
        return dates

    # ── Staleness check ───────────────────────────────────────────────────────

    @staticmethod
    def _get_last_fetch_date(symbol: str) -> Optional[date]:
        """Return the most recent fetched_at date for a symbol in stock_earnings_dates.

        Args:
            symbol: Yahoo Finance ticker (e.g. 'NYKAA.NS').

        Returns:
            Most recent fetched_at as datetime.date, or None if no data exists.
        """
        query = text("""
            SELECT TOP 1 CAST(fetched_at AS DATE) AS last_fetch
            FROM stock_earnings_dates
            WHERE symbol = :symbol
            ORDER BY fetched_at DESC
        """)
        try:
            with engine.connect() as conn:
                row = conn.execute(query, {"symbol": symbol}).fetchone()
            return row[0] if row else None
        except Exception as exc:
            logger.warning("Staleness check failed for %s: %s", symbol, exc)
            return None

    @staticmethod
    def _is_stale(symbol: str) -> bool:
        """Return True if earnings data for symbol needs refreshing.

        A symbol is stale if it has no data or if its last fetch was more than
        EarningsConfig.STALE_AFTER_DAYS ago.

        Args:
            symbol: Yahoo Finance ticker (e.g. 'NYKAA.NS').

        Returns:
            True if a fresh fetch is required.
        """
        last = EarningsService._get_last_fetch_date(symbol)
        if last is None:
            return True
        return (date.today() - last).days > EarningsConfig.STALE_AFTER_DAYS

    # ── Storage ───────────────────────────────────────────────────────────────

    @staticmethod
    def _upsert_dates(symbol: str, dates: list[date]) -> int:
        """Insert earnings dates for a symbol, skipping any that already exist.

        Uses WHERE NOT EXISTS to honour the UNIQUE(symbol, earnings_date)
        constraint without raising exceptions for duplicates.

        Args:
            symbol: Yahoo Finance ticker (e.g. 'NYKAA.NS').
            dates:  List of earnings dates to persist.

        Returns:
            Number of new rows inserted.
        """
        if not dates:
            return 0

        inserted = 0
        query = text("""
            INSERT INTO stock_earnings_dates (symbol, earnings_date)
            SELECT :symbol, :earnings_date
            WHERE NOT EXISTS (
                SELECT 1 FROM stock_earnings_dates
                WHERE symbol = :symbol AND earnings_date = :earnings_date
            )
        """)

        try:
            with engine.begin() as conn:
                for batch_start in range(0, len(dates), EarningsConfig.SQL_BATCH_SIZE):
                    batch = dates[batch_start : batch_start + EarningsConfig.SQL_BATCH_SIZE]
                    for d in batch:
                        result = conn.execute(query, {"symbol": symbol, "earnings_date": d})
                        inserted += result.rowcount
        except Exception as exc:
            logger.error("DB insert failed for %s earnings dates: %s", symbol, exc, exc_info=True)

        return inserted

    # ── Per-symbol refresh ────────────────────────────────────────────────────

    @staticmethod
    def refresh_symbol(symbol: str) -> bool:
        """Fetch and store earnings dates for one symbol, skipping if still fresh.

        Returns True when a Yahoo network call was attempted (rate limiting required
        by the caller regardless of how many dates were inserted).  Returns False
        when the symbol was skipped because its data is still within STALE_AFTER_DAYS
        (no network call made, no rate limiting needed).

        Args:
            symbol: Yahoo Finance ticker (e.g. 'NYKAA.NS').

        Returns:
            True  — Yahoo was called (caller must apply rate limit delay).
            False — skipped (fresh data, no Yahoo call made).
        """
        if not EarningsService._is_stale(symbol):
            logger.debug("%s earnings dates are fresh — skipping", symbol)
            return False

        # Yahoo network call made — caller is responsible for rate limiting.
        dates = EarningsService._fetch_from_yahoo(symbol)
        inserted = EarningsService._upsert_dates(symbol, dates)
        logger.debug("%s: inserted %d new earnings date(s)", symbol, inserted)
        return True

    # ── Batch refresh ─────────────────────────────────────────────────────────

    @staticmethod
    def refresh_all(symbols: list[str]) -> None:
        """Refresh earnings dates for all symbols with rate limiting.

        Symbols whose data is still fresh (within STALE_AFTER_DAYS) are skipped
        automatically.  Rate limiting follows EarningsConfig.RATE_LIMIT_SECONDS
        to avoid hammering Yahoo Finance.

        Args:
            symbols: List of Yahoo Finance tickers (e.g. ['NYKAA.NS', 'RELIANCE.NS']).
        """
        if not symbols:
            logger.warning("refresh_all called with empty symbols list")
            return

        fetched = 0
        skipped = 0
        failed = 0
        consecutive_network_failures = 0
        max_consec = EarningsConfig.MAX_CONSECUTIVE_NETWORK_FAILURES

        logger.info("Earnings refresh starting | %d symbols", len(symbols))

        for idx, symbol in enumerate(symbols, start=1):
            try:
                made_call = EarningsService.refresh_symbol(symbol)
                if made_call:
                    fetched += 1
                    consecutive_network_failures = 0  # reset on any success
                    # Rate limit ONLY after a real Yahoo network call.
                    # Fresh (skipped) symbols never hit Yahoo so no delay needed.
                    if idx < len(symbols):
                        time.sleep(EarningsConfig.RATE_LIMIT_SECONDS)
                else:
                    skipped += 1

            except EarningsNetworkError as exc:
                logger.warning("Earnings network error for %s: %s", symbol, exc)
                failed += 1
                consecutive_network_failures += 1
                if consecutive_network_failures >= max_consec:
                    logger.warning(
                        "Earnings refresh aborted: %d consecutive network failures "
                        "(Yahoo Finance unreachable) — fetched=%d skipped=%d failed=%d",
                        consecutive_network_failures, fetched, skipped, failed,
                    )
                    return

            except Exception as exc:
                logger.warning("Earnings refresh failed for %s: %s", symbol, exc)
                failed += 1

        logger.info(
            "Earnings refresh complete | fetched=%d | skipped=%d | failed=%d",
            fetched, skipped, failed,
        )

    # ── Load for feature pipeline ─────────────────────────────────────────────

    @staticmethod
    def load_earnings_map() -> dict[str, set[date]]:
        """Load all earnings dates from DB as a symbol → date-set mapping.

        Called once by FeaturePipeline.run() before the per-symbol loop.
        Each symbol's set is used to vectorise the is_earnings_day flag
        computation inside process_symbol() without any per-row DB access.

        Returns:
            Dict mapping Yahoo Finance ticker → frozenset of earnings dates.
            Returns empty dict on DB error (pipeline continues with flag = 0).
        """
        query = text("SELECT symbol, earnings_date FROM stock_earnings_dates")
        try:
            with engine.connect() as conn:
                rows = conn.execute(query).fetchall()
        except Exception as exc:
            logger.error("load_earnings_map failed: %s", exc, exc_info=True)
            return {}

        earnings_map: dict[str, set[date]] = {}
        for symbol, earnings_date in rows:
            if symbol not in earnings_map:
                earnings_map[symbol] = set()
            earnings_map[symbol].add(earnings_date)

        logger.info(
            "Loaded earnings map | %d symbols | %d total dates",
            len(earnings_map),
            sum(len(v) for v in earnings_map.values()),
        )
        return earnings_map
