import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config.db import engine
from app.config.logging_config import get_logger
from app.config.strategy_config import StrategyConfig
from app.config.ticker_loader import load_tickers
from app.data.yahoo_service import YahooFinanceService
from app.services.market_query_service import MarketQueryService

logger = get_logger(__name__)


class RateLimiter:
    """
    Thread-safe minimum-interval rate limiter.

    Guarantees at least `min_interval` seconds between successive acquire()
    calls regardless of how many threads are waiting. This serializes Yahoo
    Finance downloads while allowing DB reads/writes to run concurrently.
    """

    def __init__(self, min_interval: float) -> None:
        self._lock = threading.Lock()
        self._min_interval = min_interval
        self._last_called = 0.0

    def acquire(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_called
            wait = self._min_interval - elapsed
            if wait > 0:
                time.sleep(wait)
            self._last_called = time.monotonic()


class DataIngestionPipeline:
    """
    Pulls incremental OHLCV data from Yahoo Finance for every active ticker
    and persists it to daily_price_data.

    Design rules:
      - Idempotent: safe to re-run; duplicate rows are filtered before insert.
      - Rate-limited: one Yahoo call per YAHOO_RATE_LIMIT_SLEEP_SECONDS across
        all worker threads (RateLimiter serializes API calls).
      - Concurrent: DB reads/writes and data processing run in parallel across
        INGESTION_MAX_WORKERS threads.
      - Per-ticker failures are logged and skipped — one bad ticker never
        aborts the full run.
    """

    INSERT_COLUMNS = [
        "symbol",
        "trade_date",
        "open_price",
        "high_price",
        "low_price",
        "close_price",
        "volume",
    ]

    @staticmethod
    def _validate_latest_db_date(latest_date, ticker: str):
        if latest_date is None:
            return None
        try:
            latest_date = pd.to_datetime(latest_date, errors="coerce").date()
            if pd.isna(latest_date):
                logger.warning("Invalid DB date for %s", ticker)
                return None
            today = pd.Timestamp.today().date()
            if latest_date > today:
                logger.warning("Future DB date for %s: %s", ticker, latest_date)
                return None
            return latest_date
        except Exception as exc:
            logger.error("Date validation failed for %s: %s", ticker, exc)
            return None

    @staticmethod
    def _should_skip_ingestion(latest_date, ticker: str) -> bool:
        if latest_date is None:
            return False
        today = pd.Timestamp.today().date()
        if latest_date >= today:
            logger.debug("%s already up-to-date (latest: %s)", ticker, latest_date)
            return True
        return False

    @staticmethod
    def _validate_insert_dataframe(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame()

        required = ["trade_date", "open_price", "high_price",
                    "low_price", "close_price", "volume"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            logger.error("Insert columns missing for %s: %s", ticker, missing)
            return pd.DataFrame()

        df = df.copy()
        df["symbol"] = ticker
        final = df[DataIngestionPipeline.INSERT_COLUMNS].copy()
        final.drop_duplicates(subset=["symbol", "trade_date"], inplace=True)
        final.sort_values("trade_date", inplace=True)
        final.reset_index(drop=True, inplace=True)
        return final

    @staticmethod
    def _filter_existing_dates(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
        """Remove rows already in daily_price_data — makes every insert idempotent."""
        try:
            with engine.connect() as conn:
                existing = pd.read_sql(
                    text("SELECT trade_date FROM daily_price_data WHERE symbol = :s"),
                    conn,
                    params={"s": ticker},
                )
            if existing.empty:
                return df
            existing_dates = set(existing["trade_date"].tolist())
            before = len(df)
            df = df[~df["trade_date"].isin(existing_dates)].copy()
            skipped = before - len(df)
            if skipped:
                logger.debug("%s: filtered %d already-stored row(s)", ticker, skipped)
            return df
        except Exception as exc:
            logger.warning("Could not pre-filter dates for %s: %s", ticker, exc)
            return df

    @staticmethod
    def _insert_dataframe(df: pd.DataFrame, ticker: str) -> None:
        if df.empty:
            logger.debug("No new rows to insert for %s", ticker)
            return
        try:
            # SQL Server hard limit: 2100 parameters per statement.
            # 7 columns × 250 rows = 1750 — safely under the limit.
            rows = df.to_sql(
                "daily_price_data",
                engine,
                if_exists="append",
                index=False,
                method="multi",
                chunksize=250,
            )
            logger.info("Inserted %s rows for %s", rows, ticker)
        except SQLAlchemyError as exc:
            msg = str(exc)
            if "uq_symbol_date" in msg or "UNIQUE" in msg.upper():
                logger.warning("Duplicate constraint hit for %s (rows skipped)", ticker)
            else:
                logger.error("SQL insert failed for %s: %s", ticker, exc, exc_info=True)

    @staticmethod
    def _process_ticker(ticker: str, rate_limiter: RateLimiter) -> None:
        """
        Full ingestion cycle for one ticker.

        DB reads happen concurrently across all workers.
        Yahoo Finance download is serialized via rate_limiter.acquire().
        DB insert happens concurrently after the download completes.
        """
        latest_db_date = MarketQueryService.get_latest_trade_date(ticker)
        validated_date = DataIngestionPipeline._validate_latest_db_date(
            latest_db_date, ticker
        )

        if DataIngestionPipeline._should_skip_ingestion(validated_date, ticker):
            return

        # Serialize Yahoo API calls — one request per YAHOO_RATE_LIMIT_SLEEP_SECONDS
        rate_limiter.acquire()

        clean_df = YahooFinanceService.fetch_stock_data(
            ticker=ticker,
            latest_date=validated_date,
        )
        if clean_df.empty:
            logger.warning("No clean Yahoo data for %s", ticker)
            return

        final_df = DataIngestionPipeline._validate_insert_dataframe(clean_df, ticker)
        if final_df.empty:
            logger.warning("No valid insert rows for %s", ticker)
            return

        final_df = DataIngestionPipeline._filter_existing_dates(final_df, ticker)
        if final_df.empty:
            logger.debug("%s: all fetched rows already stored", ticker)
            return

        logger.info("%s: %d new rows to insert", ticker, len(final_df))
        DataIngestionPipeline._insert_dataframe(final_df, ticker)

    @staticmethod
    def run() -> None:
        tickers = load_tickers()
        if not tickers:
            logger.warning("No tickers configured — ingestion skipped")
            return

        total = len(tickers)
        max_workers = StrategyConfig.INGESTION_MAX_WORKERS
        logger.info(
            "Starting ingestion | tickers=%d | workers=%d | rate_limit=%.1fs",
            total,
            max_workers,
            StrategyConfig.YAHOO_RATE_LIMIT_SLEEP_SECONDS,
        )

        rate_limiter = RateLimiter(StrategyConfig.YAHOO_RATE_LIMIT_SLEEP_SECONDS)
        completed = 0
        counter_lock = threading.Lock()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    DataIngestionPipeline._process_ticker, ticker, rate_limiter
                ): ticker
                for ticker in tickers
            }

            for future in as_completed(futures):
                ticker = futures[future]
                with counter_lock:
                    completed += 1
                    progress = completed

                try:
                    future.result()
                    logger.info("[%d/%d] Completed %s", progress, total, ticker)
                except Exception as exc:
                    logger.error(
                        "[%d/%d] Failed %s: %s",
                        progress, total, ticker, exc,
                        exc_info=True,
                    )

        logger.info("Data ingestion completed | %d tickers processed", total)


if __name__ == "__main__":
    DataIngestionPipeline.run()
