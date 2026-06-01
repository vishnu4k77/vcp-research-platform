"""Backtest read layer — SQL queries that supply entry signals and price data.

All SQL lives here. BacktestEngine never touches the database directly.
Follows the same architectural contract as ScannerService: read-only, no
business logic, parameterised queries only (no string interpolation of values).

Signal columns queried by name are validated against a whitelist defined in
BacktestConfig.ENTRY_SIGNAL_OPTIONS to prevent SQL injection via UI inputs.
"""

from datetime import date, timedelta
from typing import Optional

import pandas as pd
from sqlalchemy import text

from app.config.db import nolock_connect
from app.config.logging_config import get_logger
from app.config.strategy_config import BacktestConfig, StrategyConfig

logger = get_logger(__name__)

# Whitelist of valid entry signal columns — prevents injection via UI inputs.
_VALID_ENTRY_SIGNALS: frozenset[str] = frozenset(BacktestConfig.ENTRY_SIGNAL_OPTIONS)


class BacktestService:
    """Read-only SQL queries for the backtesting engine.

    All methods return DataFrames or plain Python types. No writes.
    """

    @staticmethod
    def get_date_range() -> tuple[Optional[date], Optional[date]]:
        """Return the earliest and latest trade_date available in stock_signals.

        Returns:
            Tuple (min_date, max_date). Both are None if stock_signals is empty.
        """
        query = text(
            "SELECT MIN(trade_date), MAX(trade_date) FROM stock_signals"
        )
        try:
            with nolock_connect() as conn:
                row = conn.execute(query).fetchone()
            if row and row[0] and row[1]:
                return row[0], row[1]
        except Exception as exc:
            logger.error("get_date_range failed: %s", exc, exc_info=True)
        return None, None

    @staticmethod
    def get_signal_entries(
        start_date: date,
        end_date: date,
        entry_signal: str,
        min_composite_score: float,
        min_stage2_days: Optional[int] = None,
        max_stage2_days: Optional[int] = None,
    ) -> pd.DataFrame:
        """Return all entry-signal rows in [start_date, end_date] with their entry prices.

        The entry_signal column name is validated against _VALID_ENTRY_SIGNALS before
        being interpolated into the SQL string (column names cannot be parameterised
        in SQL Server — the whitelist is the safety guarantee).

        Stage 2 age filtering (min/max_stage2_days) applies the same Early/Mid/Advanced
        classification used in the scanner.  Pass None for either bound to skip that check.
        Requires stock_signals.stage2_days to be populated (run the signal pipeline first).

        Args:
            start_date: First date to include.
            end_date: Last date to include.
            entry_signal: Column name in stock_signals (e.g. "breakout_signal").
            min_composite_score: Only include rows where composite_score >= this.
            min_stage2_days: Minimum days in current Stage 2 run (None = no lower bound).
            max_stage2_days: Maximum days in current Stage 2 run (None = no upper bound).

        Returns:
            DataFrame with columns:
              entry_date, symbol, composite_score, stage2_days, entry_price, regime.
            Empty DataFrame on error or no matches.

        Raises:
            ValueError: If entry_signal is not in BacktestConfig.ENTRY_SIGNAL_OPTIONS.
        """
        if entry_signal not in _VALID_ENTRY_SIGNALS:
            raise ValueError(
                f"Invalid entry_signal '{entry_signal}'. "
                f"Must be one of: {sorted(_VALID_ENTRY_SIGNALS)}"
            )

        # stage2_days filter: SQL Server supports parameterised NULL in IS NULL checks.
        # Python None → SQL NULL → the OR branch short-circuits the filter for that bound.
        query = text(f"""
            SELECT
                ss.trade_date                               AS entry_date,
                ss.symbol,
                ss.composite_score,
                ss.stage2_days,
                dpd.close_price                             AS entry_price,
                ISNULL(mr.market_status, 'UNKNOWN')         AS regime
            FROM stock_signals ss
            JOIN daily_price_data dpd
                ON  dpd.symbol     = ss.symbol
                AND dpd.trade_date = ss.trade_date
            LEFT JOIN market_regime mr
                ON  mr.trade_date  = ss.trade_date
            WHERE ss.trade_date        BETWEEN :start_date AND :end_date
              AND ss.{entry_signal}    = 1
              AND ss.composite_score   >= :min_score
              AND (:min_s2d IS NULL OR ss.stage2_days >= :min_s2d)
              AND (:max_s2d IS NULL OR ss.stage2_days <= :max_s2d)
            ORDER BY ss.trade_date ASC, ss.symbol ASC
        """)

        try:
            with nolock_connect() as conn:
                df = pd.read_sql(
                    query,
                    conn,
                    params={
                        "start_date": start_date,
                        "end_date":   end_date,
                        "min_score":  min_composite_score,
                        "min_s2d":    min_stage2_days,
                        "max_s2d":    max_stage2_days,
                    },
                )
            logger.info(
                "Signal entries loaded | signal=%s | %s→%s | s2d=[%s,%s] | rows=%d",
                entry_signal, start_date, end_date,
                min_stage2_days or "—", max_stage2_days or "—", len(df),
            )
            return df
        except Exception as exc:
            logger.error("get_signal_entries failed: %s", exc, exc_info=True)
            return pd.DataFrame()

    @staticmethod
    def get_bulk_price_data(
        symbols: list[str],
        from_date: date,
        to_date: date,
    ) -> pd.DataFrame:
        """Return OHLC daily price data for a set of symbols over a date range.

        Loaded in one bulk query then handed to BacktestEngine for in-memory
        forward-scan simulation. Chunked internally to avoid SQL Server's
        IN-clause parameter limit.

        Args:
            symbols: List of tickers (Yahoo Finance format, e.g. 'RELIANCE.NS').
            from_date: Start of price window (inclusive).
            to_date: End of price window (inclusive).

        Returns:
            DataFrame with columns: symbol, trade_date, high_price, low_price, close_price.
            Sorted by symbol, trade_date ascending. Empty on error.
        """
        if not symbols:
            return pd.DataFrame()

        # Profiling confirmed pd.read_sql() = 95% of backtest time for 320k rows.
        # Root cause: pd.read_sql() does per-row type inference + column mapping.
        # Fix: fetchall() returns raw tuples; pd.DataFrame() constructs in one pass.
        # This is 5-10x faster for large result sets.
        #
        # PRICE_LOAD_BATCH_SIZE=2000 means all ~447 typical symbols load in ONE
        # query/connection instead of 3 separate round-trips.
        # ORDER BY removed — server-side sort of 320k rows costs ~5s; the engine
        # groups by symbol via dict, which does not require sorted input.
        chunk_size = BacktestConfig.PRICE_LOAD_BATCH_SIZE
        all_frames: list[pd.DataFrame] = []

        _COLUMNS = ["symbol", "trade_date", "high_price", "low_price", "close_price"]

        for i in range(0, len(symbols), chunk_size):
            batch = symbols[i : i + chunk_size]
            placeholders = ", ".join(f":s{j}" for j in range(len(batch)))
            sym_params   = {f"s{j}": s for j, s in enumerate(batch)}

            query = text(f"""
                SELECT symbol, trade_date, high_price, low_price, close_price
                FROM daily_price_data
                WHERE symbol    IN ({placeholders})
                  AND trade_date BETWEEN :from_date AND :to_date
            """)

            try:
                with nolock_connect() as conn:
                    result = conn.execute(
                        query,
                        {"from_date": from_date, "to_date": to_date, **sym_params},
                    )
                    rows = result.fetchall()

                if rows:
                    all_frames.append(pd.DataFrame(rows, columns=_COLUMNS))

            except Exception as exc:
                logger.error(
                    "get_bulk_price_data failed (batch %d): %s", i // chunk_size, exc,
                    exc_info=True,
                )

        if not all_frames:
            return pd.DataFrame()

        result_df = pd.concat(all_frames, ignore_index=True) if len(all_frames) > 1 else all_frames[0]
        logger.info(
            "Bulk price data loaded | symbols=%d | %s→%s | rows=%d",
            len(symbols), from_date, to_date, len(result_df),
        )
        return result_df
