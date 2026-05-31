"""Relative Strength signal — compares each stock's return to Nifty 50."""

from typing import Optional

import numpy as np
import pandas as pd

from app.config.logging_config import get_logger
from app.config.strategy_config import StrategyConfig

logger = get_logger(__name__)


class RSSignal:
    """Classifies stocks as outperforming or underperforming Nifty 50.

    For each trade_date, computes the stock's RS_LOOKBACK-day return and
    compares it to Nifty 50's return over the same window.

    rs_signal = 1  →  stock outperforms Nifty (relative strength present)
    rs_signal = 0  →  stock underperforms, or Nifty data unavailable

    Consumes:
        close_price   — from stock_features
        trade_date    — from stock_features
        nifty_returns — Series[date → float] provided by signal pipeline
                        (avoids downloading Nifty once per symbol)
    """

    REQUIRED_COLUMNS: list[str] = ["close_price", "trade_date"]

    @staticmethod
    def validate(df: pd.DataFrame) -> None:
        """Raise ValueError if any required column is absent.

        Args:
            df: Input DataFrame to validate.

        Raises:
            ValueError: If one or more REQUIRED_COLUMNS are missing.
        """
        missing = [c for c in RSSignal.REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"RSSignal missing columns: {missing}")

    @staticmethod
    def calculate(
        df: pd.DataFrame,
        nifty_returns: Optional[pd.Series] = None,
    ) -> pd.DataFrame:
        """Compute rs_signal for every row in df.

        Args:
            df: Feature DataFrame for a single symbol, sorted ascending by
                trade_date. Must contain REQUIRED_COLUMNS.
            nifty_returns: Series of Nifty 50 RS_LOOKBACK-day returns indexed
                by datetime.date. When None, rs_signal defaults to 0 for all
                rows and a debug message is logged.

        Returns:
            df with rs_signal (int 0/1) column added.
        """
        if df.empty:
            return df

        RSSignal.validate(df)

        df = df.copy()

        if nifty_returns is None:
            df["rs_signal"] = 0
            logger.debug("Nifty data unavailable — rs_signal defaulted to 0")
            return df

        close = pd.to_numeric(df["close_price"], errors="coerce")
        stock_return = close.pct_change(StrategyConfig.RS_LOOKBACK)

        # Map Nifty return to each row by trade_date
        trade_dates = pd.to_datetime(df["trade_date"]).dt.date
        nifty_mapped = trade_dates.map(nifty_returns)

        valid = stock_return.notna() & nifty_mapped.notna()

        df["rs_signal"] = np.where(
            valid & (stock_return > nifty_mapped),
            1,
            0,
        )

        return df
