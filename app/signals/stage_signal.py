import pandas as pd
import numpy as np


class StageSignal:

    @staticmethod
    def validate(df):

        required_columns = [

            "close_price",
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

        StageSignal.validate(df)

        df = df.copy()

        rolling_high = (
            df["close_price"]
            .rolling(252)
            .max()
        )

        rolling_low = (
            df["close_price"]
            .rolling(252)
            .min()
        )

        range_position = (

            (
                df["close_price"]
                -
                rolling_low
            )

            /

            (
                rolling_high
                -
                rolling_low
            )
        )

        ema_200_slope = (
            df["ema_200"]
            .diff(20)
        )

        conditions = (

            (df["close_price"] > df["ema_150"])

            &

            (df["close_price"] > df["ema_200"])

            &

            (ema_200_slope > 0)

            &

            (range_position >= 0.75)
        )

        df["stage2_signal"] = np.where(
            conditions,
            1,
            0
        )

        return df