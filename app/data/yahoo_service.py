from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from app.config.logging_config import get_logger
from app.config.strategy_config import StrategyConfig

logger = get_logger(__name__)


def _nse_market_is_closed() -> bool:
    """Return True if NSE has closed for today (after 15:30 IST = 10:00 UTC).

    Uses UTC so no tz library needed. Config-driven via NSE_MARKET_CLOSE_UTC_HOUR/MINUTE.
    This guards against storing partial intraday candles when the pipeline runs
    during market hours — today's data is only safe to fetch after this returns True.
    """
    now_utc = datetime.now(timezone.utc)
    close_utc = now_utc.replace(
        hour=StrategyConfig.NSE_MARKET_CLOSE_UTC_HOUR,
        minute=StrategyConfig.NSE_MARKET_CLOSE_UTC_MINUTE,
        second=0,
        microsecond=0,
    )
    return now_utc >= close_utc


# ── Module-level retry wrapper ────────────────────────────────────────────────
# Applied once at import time; parameters read from StrategyConfig so they
# are configurable without touching this file.

@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(StrategyConfig.YAHOO_RETRY_ATTEMPTS),
    wait=wait_exponential(
        multiplier=2,
        min=StrategyConfig.YAHOO_RETRY_MIN_WAIT_SECONDS,
        max=StrategyConfig.YAHOO_RETRY_MAX_WAIT_SECONDS,
    ),
    reraise=True,
    before_sleep=before_sleep_log(logger, 20),  # 20 = logging.WARNING
)
def _yf_download(ticker: str, **kwargs) -> pd.DataFrame:
    """Single yf.download attempt — wrapped externally with retry.

    yfinance returns an empty DataFrame (no exception) when data is unavailable,
    e.g. temporary Yahoo outage or "possibly delisted" false positive.
    Raising ValueError here converts that silent empty-return into a retryable
    event so all YAHOO_RETRY_ATTEMPTS are used before giving up.
    """
    result = yf.download(tickers=ticker, **kwargs)
    if result.empty:
        raise ValueError(
            f"yf.download returned empty DataFrame for {ticker} — may be temporary"
        )
    return result


class YahooFinanceService:
    """
    Fetches and validates OHLCV data from Yahoo Finance for a single ticker.

    All quality thresholds are read from StrategyConfig — nothing hardcoded here.
    The public entry point is fetch_stock_data().
    """

    REQUIRED_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]

    @staticmethod
    def _validate_latest_date(latest_date):

        if latest_date is None:
            return None

        try:
            latest_date = pd.to_datetime(latest_date, errors="coerce").date()

            if pd.isna(latest_date):
                return None

            today = pd.Timestamp.today().date()

            if latest_date > today:
                logger.warning("Future DB date detected: %s — treating as None", latest_date)
                return None

            return latest_date

        except Exception:
            return None

    @staticmethod
    def _determine_download_mode(latest_date) -> dict:
        """Determine whether to skip, do a full historical download, or incremental.

        Excludes today's date from the download window when NSE market is still open
        (before 15:30 IST / 10:00 UTC) — prevents storing partial intraday candles.
        Today's EOD candle is only safe to fetch after _nse_market_is_closed() = True.
        """
        today = pd.Timestamp.today().date()
        yesterday = today - timedelta(days=1)

        # Safe upper bound: today only after market has closed, yesterday otherwise.
        fetch_up_to = today if _nse_market_is_closed() else yesterday

        if latest_date is None:
            return {"mode": "historical", "start_date": None, "end_date": fetch_up_to}

        if latest_date >= fetch_up_to:
            return {"mode": "skip", "start_date": None, "end_date": None}

        next_day = latest_date + timedelta(days=1)

        if next_day > fetch_up_to:
            return {"mode": "skip", "start_date": None, "end_date": None}

        return {"mode": "incremental", "start_date": next_day, "end_date": fetch_up_to}

    @staticmethod
    def _download_data(ticker: str, download_mode: dict) -> pd.DataFrame:

        if download_mode["mode"] == "skip":
            logger.debug("%s already up-to-date", ticker)
            return pd.DataFrame()

        # yf.download end= is exclusive: to include fetch_up_to, pass fetch_up_to + 1 day.
        end_date = download_mode.get("end_date")
        end_str = (end_date + timedelta(days=1)).strftime("%Y-%m-%d") if end_date else None

        try:
            if download_mode["mode"] == "historical":
                logger.debug(
                    "Historical download for %s (period=%s)",
                    ticker, StrategyConfig.YAHOO_HISTORICAL_PERIOD,
                )
                # Historical uses period= (no start/end); partial intraday rows are
                # stripped later in fetch_stock_data() using end_date from download_mode.
                return _yf_download(
                    ticker,
                    period=StrategyConfig.YAHOO_HISTORICAL_PERIOD,
                    progress=False,
                    auto_adjust=False,
                )

            start_date = download_mode["start_date"]
            logger.debug(
                "Incremental download for %s from %s to %s",
                ticker, start_date, end_str or "today",
            )
            kwargs: dict = {
                "start": start_date.strftime("%Y-%m-%d"),
                "progress": False,
                "auto_adjust": False,
            }
            if end_str:
                kwargs["end"] = end_str
            return _yf_download(ticker, **kwargs)

        except Exception as exc:
            logger.error("Yahoo download failed for %s: %s", ticker, exc, exc_info=True)
            return pd.DataFrame()

    @staticmethod
    def _normalize_columns(data: pd.DataFrame) -> pd.DataFrame:

        if data.empty:
            return pd.DataFrame()

        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        data.reset_index(inplace=True)

        date_col = next(
            (c for c in ("Date", "Datetime", "index") if c in data.columns),
            None,
        )

        if not date_col:
            logger.error("Date column not found in Yahoo response")
            return pd.DataFrame()

        missing = [c for c in YahooFinanceService.REQUIRED_COLUMNS if c not in data.columns]

        if missing:
            logger.error("Yahoo response missing expected columns: %s", missing)
            return pd.DataFrame()

        normalized = data[[date_col, "Open", "High", "Low", "Close", "Volume"]].copy()

        normalized.columns = [
            "trade_date", "open_price", "high_price",
            "low_price", "close_price", "volume",
        ]

        return normalized

    @staticmethod
    def _normalize_datatypes(df: pd.DataFrame) -> pd.DataFrame:

        if df.empty:
            return pd.DataFrame()

        df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.date

        for col in ("open_price", "high_price", "low_price", "close_price", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

        return df

    @staticmethod
    def _apply_quality_filters(df: pd.DataFrame) -> pd.DataFrame:

        if df.empty:
            return pd.DataFrame()

        df.dropna(
            subset=["trade_date", "open_price", "high_price",
                    "low_price", "close_price", "volume"],
            inplace=True,
        )

        df.drop_duplicates(subset=["trade_date"], inplace=True)

        # Remove physically impossible candles
        df = df[
            (df["high_price"] >= df["low_price"])
            & (df["open_price"] > 0)
            & (df["close_price"] > 0)
            & (df["volume"] >= 0)
        ]

        # Filter split-detection anomalies (e.g. 5× single-day move)
        safe_open = df["open_price"].replace(0, float("nan"))
        df = df[
            (df["close_price"] / safe_open).abs()
            < StrategyConfig.DATA_QUALITY_MAX_DAILY_MOVE_RATIO
        ]

        # Filter abnormal volume spikes
        volume_median = df["volume"].median()

        if volume_median > 0:
            df = df[
                df["volume"]
                < volume_median * StrategyConfig.DATA_QUALITY_MAX_VOLUME_MULTIPLIER
            ]

        df.sort_values(by="trade_date", inplace=True)
        df.reset_index(drop=True, inplace=True)

        return df

    @staticmethod
    def fetch_stock_data(
        ticker: str,
        latest_date=None,
    ) -> pd.DataFrame:

        logger.debug("Fetching Yahoo data for %s", ticker)

        validated_date = YahooFinanceService._validate_latest_date(latest_date)

        download_mode = YahooFinanceService._determine_download_mode(validated_date)

        raw_data = YahooFinanceService._download_data(ticker, download_mode)

        if raw_data.empty:
            logger.debug("No Yahoo data returned for %s", ticker)
            return pd.DataFrame()

        normalized = YahooFinanceService._normalize_columns(raw_data)

        if normalized.empty:
            return pd.DataFrame()

        normalized = YahooFinanceService._normalize_datatypes(normalized)

        # Strip rows beyond the safe fetch boundary — guards against partial intraday
        # candles returned by Yahoo Finance when historical mode fetches via period=.
        # Incremental mode already passes end= to yf.download, but we filter here too
        # for defence-in-depth.
        end_date = download_mode.get("end_date")
        if end_date is not None and "trade_date" in normalized.columns:
            before = len(normalized)
            normalized = normalized[normalized["trade_date"] <= end_date]
            dropped = before - len(normalized)
            if dropped:
                logger.info(
                    "%s: dropped %d row(s) beyond safe fetch boundary %s "
                    "(market still open — partial intraday candle excluded)",
                    ticker, dropped, end_date,
                )

        clean = YahooFinanceService._apply_quality_filters(normalized)

        if clean.empty:
            logger.warning("No valid rows after quality filters for %s", ticker)
            return pd.DataFrame()

        logger.debug("%s: %d validated rows", ticker, len(clean))
        return clean
