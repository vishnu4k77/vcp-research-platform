"""
Ticker loader — provides the NSE symbol list for Yahoo Finance ingestion.

Priority order:
  1. nse_universe table (is_active = 1)
     The production source. Maintained by scripts/fetch_nse_tickers.py.
     Run it once after fresh install, then monthly after NSE rebalances.

  2. daily_price_data (DISTINCT symbol)
     Fallback used when nse_universe is empty (fetch script not yet run).
     Returns all symbols already in the DB so in-flight pipelines keep working.
"""
from typing import List

from sqlalchemy import text

from app.config.db import engine
from app.config.logging_config import get_logger

logger = get_logger(__name__)

_NSE_SUFFIX = ".NS"


def load_tickers() -> List[str]:
    """
    Returns the full list of NSE tickers for the ingestion pipeline.
    All returned values carry the '.NS' suffix required by yfinance.
    """

    # ── Source 1: nse_universe table ─────────────────────────────────────────
    tickers = _from_nse_universe()
    if tickers:
        return tickers

    # ── Source 2: daily_price_data (symbols already in DB) ───────────────────
    logger.warning(
        "nse_universe is empty — falling back to daily_price_data. "
        "Run: python scripts/fetch_nse_tickers.py"
    )
    return _from_price_data()


def _from_nse_universe() -> List[str]:
    """Active stocks from the nse_universe master table."""

    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT symbol "
                    "FROM nse_universe "
                    "WHERE is_active = 1 "
                    "ORDER BY symbol ASC"
                )
            ).fetchall()

        tickers = [_ensure_ns(row[0]) for row in rows if row[0]]
        logger.info("Loaded %d active tickers from nse_universe", len(tickers))
        return tickers

    except Exception as exc:
        logger.error("nse_universe query failed: %s", exc, exc_info=True)
        return []


def _from_price_data() -> List[str]:
    """All distinct symbols already stored in daily_price_data."""

    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT DISTINCT symbol "
                    "FROM daily_price_data "
                    "ORDER BY symbol ASC"
                )
            ).fetchall()

        tickers = [_ensure_ns(row[0]) for row in rows if row[0]]
        logger.info("Loaded %d tickers from daily_price_data", len(tickers))
        return tickers

    except Exception as exc:
        logger.error("daily_price_data query failed: %s", exc, exc_info=True)
        return []


def _ensure_ns(symbol: str) -> str:
    """Appends .NS suffix if not already present (Yahoo Finance NSE format)."""

    return symbol if symbol.upper().endswith(_NSE_SUFFIX) else f"{symbol}{_NSE_SUFFIX}"
