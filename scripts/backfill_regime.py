"""backfill_regime.py — Populate market_regime + market_environment with full Nifty 50 history.

The daily pipeline only classifies TODAY's regime. This script downloads 5 years
of Nifty 50 data and inserts a regime row for every historical trading day so
the backtester can show BULL / BEAR / NEUTRAL splits instead of UNKNOWN.

Usage:
    python scripts/backfill_regime.py            # backfill all missing dates
    python scripts/backfill_regime.py --dry-run  # show counts without writing

Safe to run repeatedly — existing rows are skipped (INSERT WHERE NOT EXISTS).

Exit codes:
    0  success
    1  fatal error
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yfinance as yf
import pandas as pd
from sqlalchemy import text

from app.config.db import engine
from app.config.logging_config import get_logger
from app.config.strategy_config import StrategyConfig
from app.regime.index_regime import IndexRegime
from app.services.market_query_service import MarketQueryService

logger = get_logger("backfill_regime")

# Download period — must be long enough for 220-bar EMA warmup + full backtest window
_DOWNLOAD_PERIOD = "5y"


def _fetch_nifty_full() -> pd.DataFrame:
    """Download 5 years of Nifty 50 daily closes from Yahoo Finance.

    Returns:
        DataFrame with columns [trade_date (date), close (float)],
        sorted ascending.  Empty on failure.
    """
    try:
        raw = yf.download(
            tickers=StrategyConfig.NIFTY_TICKER,
            period=_DOWNLOAD_PERIOD,
            progress=False,
            auto_adjust=True,
        )

        if raw.empty:
            logger.error("Yahoo returned no data for %s", StrategyConfig.NIFTY_TICKER)
            return pd.DataFrame()

        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        raw.reset_index(inplace=True)

        date_col = next(
            (c for c in ("Date", "Datetime", "index") if c in raw.columns), None
        )
        if date_col is None or "Close" not in raw.columns:
            logger.error("Unexpected Nifty column structure: %s", raw.columns.tolist())
            return pd.DataFrame()

        df = raw[[date_col, "Close"]].copy()
        df.columns = ["trade_date", "close"]
        df["close"]      = pd.to_numeric(df["close"], errors="coerce")
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df.dropna(subset=["close"], inplace=True)
        df.sort_values("trade_date", inplace=True)
        df.reset_index(drop=True, inplace=True)

        logger.info(
            "Nifty history downloaded | %d bars | %s to %s",
            len(df),
            df["trade_date"].iloc[0],
            df["trade_date"].iloc[-1],
        )
        return df

    except Exception as exc:
        logger.error("Nifty download failed: %s", exc, exc_info=True)
        return pd.DataFrame()


def _get_existing_dates() -> set:
    """Return the set of trade_dates already in market_regime.

    Returns:
        Set of datetime.date values already stored.
    """
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT trade_date FROM market_regime")
            ).fetchall()
        return {r[0] for r in rows}
    except Exception as exc:
        logger.warning("Could not read existing regime dates: %s", exc)
        return set()


def _insert_regime_row(conn, result, dry_run: bool) -> bool:
    """Insert one RegimeResult row into market_regime (skip if date exists).

    Args:
        conn: Active SQLAlchemy connection.
        result: RegimeResult to persist.
        dry_run: If True, skip the actual INSERT.

    Returns:
        True if the row was (or would be) inserted, False if skipped.
    """
    if dry_run:
        return True

    try:
        conn.execute(
            text("""
                INSERT INTO market_regime (
                    trade_date,
                    nifty_above_50ema,
                    nifty_above_200ema,
                    breadth_positive,
                    vix_risk_off,
                    distribution_count,
                    follow_through_day,
                    regime_score,
                    market_status
                )
                SELECT
                    :trade_date,
                    :nifty_above_50ema,
                    :nifty_above_200ema,
                    NULL, NULL, NULL, NULL,
                    :regime_score,
                    :market_status
                WHERE NOT EXISTS (
                    SELECT 1 FROM market_regime WHERE trade_date = :trade_date
                )
            """),
            {
                "trade_date":        result.trade_date,
                "nifty_above_50ema": 1 if result.nifty_above_50ema  else 0,
                "nifty_above_200ema":1 if result.nifty_above_200ema else 0,
                "regime_score":      result.regime_score,
                "market_status":     result.market_status,
            },
        )
        return True
    except Exception as exc:
        logger.debug("market_regime insert skipped for %s: %s", result.trade_date, exc)
        return False


def _insert_environment_row(conn, result, env: dict, dry_run: bool) -> bool:
    """Insert one environment row into market_environment (skip if date exists).

    Args:
        conn: Active SQLAlchemy connection.
        result: RegimeResult providing trade_date and regime_score.
        env: Trading environment dict from MarketQueryService.
        dry_run: If True, skip the actual INSERT.

    Returns:
        True if inserted (or would be), False if skipped.
    """
    if dry_run:
        return True

    try:
        conn.execute(
            text("""
                INSERT INTO market_environment (
                    trade_date,
                    market_state,
                    regime_score,
                    breadth_score,
                    distribution_days,
                    exposure_level,
                    max_position_size,
                    allow_breakouts,
                    allow_pyramiding,
                    cash_mode
                )
                SELECT
                    :trade_date,
                    :market_state,
                    :regime_score,
                    NULL, NULL,
                    :exposure_level,
                    :max_position_size,
                    :allow_breakouts,
                    :allow_pyramiding,
                    :cash_mode
                WHERE NOT EXISTS (
                    SELECT 1 FROM market_environment WHERE trade_date = :trade_date
                )
            """),
            {
                "trade_date":       result.trade_date,
                "market_state":     result.market_status,
                "regime_score":     result.regime_score,
                "exposure_level":   env["exposure_level"],
                "max_position_size":env["max_position_size"],
                "allow_breakouts":  1 if env["allow_breakouts"]  else 0,
                "allow_pyramiding": 1 if env["allow_pyramiding"] else 0,
                "cash_mode":        1 if env["cash_mode"]        else 0,
            },
        )
        return True
    except Exception as exc:
        logger.debug("market_environment insert skipped for %s: %s", result.trade_date, exc)
        return False


def backfill(dry_run: bool = False) -> int:
    """Download Nifty history and insert a regime row for every missing date.

    Args:
        dry_run: Log what would be inserted without writing to DB.

    Returns:
        Number of rows inserted (or that would be inserted in dry_run mode).
    """
    df = _fetch_nifty_full()
    if df.empty:
        logger.error("No Nifty data — backfill aborted")
        return 0

    results = IndexRegime.classify_history(df)
    if not results:
        logger.error("classify_history returned no rows — backfill aborted")
        return 0

    existing = _get_existing_dates()
    new_results = [r for r in results if r.trade_date not in existing]

    logger.info(
        "Regime backfill | total computed=%d | already in DB=%d | to insert=%d | dry_run=%s",
        len(results),
        len(existing),
        len(new_results),
        dry_run,
    )

    if not new_results:
        logger.info("All regime dates already present — nothing to insert")
        return 0

    inserted = 0

    try:
        with engine.begin() as conn:
            for result in new_results:
                env = MarketQueryService.get_regime_environment(result.market_status)
                r_ok = _insert_regime_row(conn, result, dry_run)
                e_ok = _insert_environment_row(conn, result, env, dry_run)
                if r_ok and e_ok:
                    inserted += 1

    except Exception as exc:
        logger.error("Backfill transaction failed: %s", exc, exc_info=True)
        return inserted

    logger.info("Regime backfill complete | inserted=%d rows", inserted)
    return inserted


def main() -> int:
    """CLI entry point.

    Returns:
        0 on success, 1 on fatal error.
    """
    parser = argparse.ArgumentParser(
        description="Backfill market_regime + market_environment with 5yr Nifty history",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/backfill_regime.py           # full backfill\n"
            "  python scripts/backfill_regime.py --dry-run # preview only\n"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview how many rows would be inserted without writing to DB.",
    )
    args = parser.parse_args()

    inserted = backfill(dry_run=args.dry_run)

    if args.dry_run:
        print(f"\nDry run: {inserted} rows would be inserted into market_regime / market_environment.")
    else:
        print(f"\nBackfill complete: {inserted} rows inserted.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
