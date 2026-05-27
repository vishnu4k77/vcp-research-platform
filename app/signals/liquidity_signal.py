import pandas as pd
import numpy as np


class LiquiditySignal:

    @staticmethod
    def calculate(
        df: pd.DataFrame
    ) -> pd.DataFrame:

        if df.empty:

            return df

        df = df.copy()

        traded_value = (

            df["close_price"]

            *

            df["avg_volume_20"]
        )

        conditions = (

            (df["avg_volume_20"] >= 500000)

            &

            (traded_value >= 50000000)
        )

        df["liquidity_signal"] = np.where(
            conditions,
            1,
            0
        )

        return df