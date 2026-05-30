import pandas as pd
import numpy as np

from app.config.strategy_config import StrategyConfig
from app.config.logging_config import get_logger

logger = get_logger(__name__)


class LiquiditySignal:

    REQUIRED_COLUMNS = ["close_price", "avg_volume_20"]

    @staticmethod
    def validate(df: pd.DataFrame) -> None:

        missing = [
            col for col in LiquiditySignal.REQUIRED_COLUMNS
            if col not in df.columns
        ]

        if missing:
            raise ValueError(f"LiquiditySignal missing columns: {missing}")

    @staticmethod
    def calculate(
        df: pd.DataFrame,
    ) -> pd.DataFrame:

        if df.empty:
            return df

        LiquiditySignal.validate(df)

        df = df.copy()

        for col in LiquiditySignal.REQUIRED_COLUMNS:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        traded_value = df["close_price"] * df["avg_volume_20"]

        conditions = (
            (df["avg_volume_20"] >= StrategyConfig.MIN_AVG_VOLUME_20)
            & (traded_value >= StrategyConfig.MIN_TRADED_VALUE)
        )

        df["liquidity_signal"] = np.where(conditions, 1, 0)

        return df