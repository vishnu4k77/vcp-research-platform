from typing import Dict

import pandas as pd
from sqlalchemy import text

from app.config.db import engine
from app.config.logging_config import get_logger

logger = get_logger(__name__)

# Conservative defaults — applied when the DB is unavailable or the state_code is unknown.
_SAFE_ENV: Dict = {
    "exposure_level": 0.0,
    "max_position_size": 0.0,
    "allow_breakouts": False,
    "allow_pyramiding": False,
    "cash_mode": True,
}


class MarketQueryService:
    """
    Thin DB read layer for the ingestion pipeline.

    Architectural rule: this class only reads raw data from SQL.
    Indicator calculations belong in app/features/, never here.
    """

    @staticmethod
    def get_latest_trade_date(symbol: str):
        """
        Returns the most recent trade_date stored for a symbol, or None.
        Used by DataIngestionPipeline to decide the incremental fetch window.
        """

        query = text("""
            SELECT MAX(trade_date) AS latest_date
            FROM daily_price_data
            WHERE symbol = :symbol
        """)

        try:
            with engine.connect() as conn:
                row = conn.execute(query, {"symbol": symbol}).fetchone()

            return row[0] if row and row[0] is not None else None

        except Exception as exc:
            logger.error(
                "get_latest_trade_date failed for %s: %s", symbol, exc, exc_info=True
            )
            return None

    @staticmethod
    def get_market_state_for_score(score: int) -> str:
        """
        Returns the state_code whose score range contains the given score.
        Queries the market_states table (is_active=1, score bounds not NULL).
        Falls back to 'BEAR' if the DB is unavailable or no row matches.
        """
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT state_code, score_min "
                        "FROM market_states "
                        "WHERE is_active = 1 "
                        "  AND score_min IS NOT NULL "
                        "  AND score_max IS NOT NULL "
                        "  AND :score BETWEEN score_min AND score_max "
                        "ORDER BY score_min DESC"
                    ),
                    {"score": score},
                ).fetchall()

            if rows:
                return rows[0][0]

            logger.warning("No active market_state matched score=%d — defaulting to BEAR", score)
            return "BEAR"

        except Exception as exc:
            logger.error("get_market_state_for_score failed: %s", exc, exc_info=True)
            return "BEAR"

    @staticmethod
    def get_regime_environment(state_code: str) -> Dict:
        """
        Returns trading-environment parameters for the given state_code.
        Falls back to a conservative cash-mode dict if the DB is unavailable.
        """
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT exposure_level, max_position_size, "
                        "       allow_breakouts, allow_pyramiding, cash_mode "
                        "FROM market_states "
                        "WHERE state_code = :sc"
                    ),
                    {"sc": state_code},
                ).fetchone()

            if row:
                return {
                    "exposure_level":    row[0],
                    "max_position_size": row[1],
                    "allow_breakouts":   bool(row[2]),
                    "allow_pyramiding":  bool(row[3]),
                    "cash_mode":         bool(row[4]),
                }

            logger.warning("state_code '%s' not found in market_states — using safe defaults", state_code)
            return _SAFE_ENV.copy()

        except Exception as exc:
            logger.error("get_regime_environment failed for '%s': %s", state_code, exc, exc_info=True)
            return _SAFE_ENV.copy()

    @staticmethod
    def get_raw_price_data(symbol: str) -> pd.DataFrame:
        """
        Returns raw OHLCV history for a symbol, sorted ascending by date.
        No indicator calculations — callers must use FeaturePipeline for that.
        """

        query = text("""
            SELECT
                symbol,
                trade_date,
                open_price,
                high_price,
                low_price,
                close_price,
                volume
            FROM daily_price_data
            WHERE symbol = :symbol
            ORDER BY trade_date ASC
        """)

        try:
            with engine.connect() as conn:
                df = pd.read_sql(query, conn, params={"symbol": symbol})

            if df.empty:
                logger.debug("No price data found for %s", symbol)

            return df

        except Exception as exc:
            logger.error(
                "get_raw_price_data failed for %s: %s", symbol, exc, exc_info=True
            )
            return pd.DataFrame()
