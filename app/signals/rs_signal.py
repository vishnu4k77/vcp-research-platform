"""Relative Strength signal — stock return vs Nifty 50 over RS_LOOKBACK bars.

Produces three columns per row:
  rs_signal   BIT   — 1 if stock outperforms Nifty over RS_LOOKBACK period
  rs_value    FLOAT — excess return (stock_return - nifty_return) × 100
                      positive = outperforming, negative = underperforming
  rs_new_high BIT   — 1 if today's rs_value exceeds its own RS_NEW_HIGH_LOOKBACK
                      rolling max (RS line making a new high)
"""

from typing import Optional

import numpy as np
import pandas as pd

from app.config.logging_config import get_logger
from app.config.strategy_config import MinerviniConfig, StrategyConfig

logger = get_logger(__name__)


class RSSignal:
    """Classifies stocks as outperforming or underperforming Nifty 50.

    For each trade_date, computes the stock's RS_LOOKBACK-day return and
    compares it to Nifty 50's return over the same window.

    rs_signal   = 1  → stock outperforms Nifty (relative strength present)
    rs_signal   = 0  → underperforms, or Nifty data unavailable
    rs_value        → excess return percentage (stock - Nifty), e.g. +12.4
    rs_new_high = 1  → rs_value is at its highest level in the past
                        MinerviniConfig.RS_NEW_HIGH_LOOKBACK bars

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
        """Compute rs_signal, rs_value, and rs_new_high for every row in df.

        Algorithm:
          1. stock_return_63d  = close_price.pct_change(RS_LOOKBACK)
          2. nifty_return_63d  = nifty_returns mapped by trade_date
          3. rs_value          = (stock_return - nifty_return) * 100   [% excess]
          4. rs_signal         = 1 where both returns are valid AND stock > nifty
          5. rs_new_high       = 1 where rs_value > rolling max of prior
                                  RS_NEW_HIGH_LOOKBACK bars (shift 1 avoids
                                  today competing against itself)

        Args:
            df: Feature DataFrame for a single symbol, sorted ascending by
                trade_date. Must contain REQUIRED_COLUMNS.
            nifty_returns: Series of Nifty 50 RS_LOOKBACK-day returns indexed
                by datetime.date. When None, all three outputs default to 0/NaN.

        Returns:
            df with rs_signal (int 0/1), rs_value (float), rs_new_high (int 0/1)
            columns added.
        """
        if df.empty:
            return df

        RSSignal.validate(df)

        df = df.copy()

        if nifty_returns is None:
            df["rs_signal"]   = 0
            df["rs_value"]    = np.nan
            df["rs_new_high"] = 0
            logger.debug("Nifty data unavailable — rs outputs defaulted to 0/NaN")
            return df

        close        = pd.to_numeric(df["close_price"], errors="coerce")
        stock_return = close.pct_change(StrategyConfig.RS_LOOKBACK)

        trade_dates  = pd.to_datetime(df["trade_date"]).dt.date
        nifty_mapped = trade_dates.map(nifty_returns)

        valid = stock_return.notna() & nifty_mapped.notna()

        # ── rs_signal: binary outperformance flag ─────────────────────────
        df["rs_signal"] = np.where(
            valid & (stock_return > nifty_mapped),
            1,
            0,
        ).astype(int)

        # ── rs_value: excess return expressed as a percentage ─────────────
        # NaN where Nifty data or stock return is unavailable (first RS_LOOKBACK rows).
        rs_excess = (stock_return - nifty_mapped) * 100
        df["rs_value"] = rs_excess.where(valid).round(2)

        # ── rs_new_high: rs_value at its own rolling RS_NEW_HIGH_LOOKBACK max ──
        # shift(1) ensures today's value does not inflate the rolling max it
        # is compared against (avoids a row always being its own "new high").
        # min_periods = lookback // 2 allows partial-history stocks to qualify
        # once they have at least 6 months of RS data.
        lookback = MinerviniConfig.RS_NEW_HIGH_LOOKBACK
        rs_rolling_max = (
            df["rs_value"]
            .shift(1)
            .rolling(lookback, min_periods=lookback // 2)
            .max()
        )
        df["rs_new_high"] = np.where(
            df["rs_value"].notna()
            & rs_rolling_max.notna()
            & (df["rs_value"] > rs_rolling_max),
            1,
            0,
        ).astype(int)

        return df
