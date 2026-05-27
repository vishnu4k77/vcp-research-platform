import pandas as pd
import numpy as np


class TrendSignal:

    EMA_COLUMNS = [

        "ema_50",
        "ema_150",
        "ema_200"
    ]

    @staticmethod
    def validate(df: pd.DataFrame):

        required_columns = [

            "close_price",

            "ema_50",
            "ema_150",
            "ema_200"
        ]

        missing = [

            col
            for col in required_columns
            if col not in df.columns
        ]

        if missing:

            raise ValueError(
                f"Missing columns: {missing}"
            )

    @staticmethod
    def calculate(
        df: pd.DataFrame
    ) -> pd.DataFrame:

        if df.empty:

            return df

        TrendSignal.validate(df)

        df = df.copy()

        for col in TrendSignal.EMA_COLUMNS:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        df["ema_200_slope"] = (
            df["ema_200"]
            .diff(20)
        )

        df["ema_50_slope"] = (
            df["ema_50"]
            .diff(10)
        )

        conditions = (

            (df["close_price"] > df["ema_50"])

            &

            (df["ema_50"] > df["ema_150"])

            &

            (df["ema_150"] > df["ema_200"])

            &

            (df["ema_200_slope"] > 0)

            &

            (df["ema_50_slope"] > 0)
        )

        df["trend_signal"] = np.where(
            conditions,
            1,
            0
        )

        return df