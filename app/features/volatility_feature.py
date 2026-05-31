"""Volatility and price-context features for the NSE breakout scanner."""

import pandas as pd
import numpy as np

from app.config.strategy_config import StrategyConfig


class VolatilityFeature:
    """Computes volatility indicators and 52-week price context from OHLCV data.

    Computed columns:
        daily_range_pct         — intraday range as % of close
        atr_14                  — 14-period Average True Range
        volatility_10           — 10-period rolling std of daily_range_pct
        volatility_contraction  — current volatility / rolling mean (ratio < 1 = contracting)
        high_52w                — rolling STAGE_LOOKBACK-bar high (52-week high proxy)
        low_52w                 — rolling STAGE_LOOKBACK-bar low  (52-week low proxy)
    """

    REQUIRED_COLUMNS: list[str] = ["high_price", "low_price", "close_price"]

    @staticmethod
    def validate(df: pd.DataFrame) -> None:
        """Raise ValueError if any required column is absent.

        Args:
            df: Input DataFrame to validate.

        Raises:
            ValueError: If one or more REQUIRED_COLUMNS are missing.
        """
        missing = [c for c in VolatilityFeature.REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"VolatilityFeature missing columns: {missing}")

    @staticmethod
    def calculate(df: pd.DataFrame) -> pd.DataFrame:
        """Compute all volatility and price-context features.

        Args:
            df: OHLCV DataFrame for a single symbol, sorted ascending by
                trade_date. Must contain high_price, low_price, close_price.

        Returns:
            df with all volatility and 52-week context columns appended.
        """
        if df.empty:
            return df

        VolatilityFeature.validate(df)

        df = df.copy()

        for col in VolatilityFeature.REQUIRED_COLUMNS:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df.dropna(subset=VolatilityFeature.REQUIRED_COLUMNS, inplace=True)

        # ── Intraday range % ──────────────────────────────────────────────
        df["daily_range_pct"] = (
            (df["high_price"] - df["low_price"]) / df["close_price"] * 100
        )

        # ── True Range + ATR(14) ──────────────────────────────────────────
        prev_close = df["close_price"].shift(1)
        true_range = pd.concat(
            [
                df["high_price"] - df["low_price"],
                (df["high_price"] - prev_close).abs(),
                (df["low_price"]  - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)

        df["atr_14"] = true_range.rolling(
            StrategyConfig.ATR_LOOKBACK, min_periods=1
        ).mean()

        # ── Rolling volatility + contraction ratio ─────────────────────────
        df["volatility_10"] = (
            df["daily_range_pct"]
            .rolling(StrategyConfig.VOLATILITY_LOOKBACK, min_periods=1)
            .std()
        )

        rolling_vol_mean = (
            df["volatility_10"]
            .rolling(StrategyConfig.VOLATILITY_LOOKBACK, min_periods=1)
            .mean()
            .replace(0, np.nan)
        )

        df["volatility_contraction"] = df["volatility_10"] / rolling_vol_mean

        # ── 52-week high / low ────────────────────────────────────────────
        # STAGE_LOOKBACK = 252 trading days ≈ 1 calendar year.
        # min_periods=63 requires at least 1 quarter of data before emitting a value.
        df["high_52w"] = (
            df["close_price"]
            .rolling(StrategyConfig.STAGE_LOOKBACK, min_periods=63)
            .max()
        )
        df["low_52w"] = (
            df["close_price"]
            .rolling(StrategyConfig.STAGE_LOOKBACK, min_periods=63)
            .min()
        )

        return df
