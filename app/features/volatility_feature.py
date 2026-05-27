import pandas as pd
import numpy as np


class VolatilityFeature:

    @staticmethod
    def calculate(
        df: pd.DataFrame
    ) -> pd.DataFrame:

        if df.empty:

            return df

        df = df.copy()

        numeric_columns = [
            "high_price",
            "low_price",
            "close_price"
        ]

        for col in numeric_columns:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        df.dropna(
            subset=numeric_columns,
            inplace=True
        )

        # daily range %
        df["daily_range_pct"] = (
            (
                df["high_price"] -
                df["low_price"]
            )
            /
            df["close_price"]
        ) * 100

        # previous close
        previous_close = (
            df["close_price"]
            .shift(1)
        )

        # true range
        tr1 = (
            df["high_price"] -
            df["low_price"]
        )

        tr2 = abs(
            df["high_price"] -
            previous_close
        )

        tr3 = abs(
            df["low_price"] -
            previous_close
        )

        true_range = pd.concat(
            [tr1, tr2, tr3],
            axis=1
        ).max(axis=1)

        # ATR
        df["atr_14"] = (
            true_range
            .rolling(14)
            .mean()
        )

        # rolling volatility
        df["volatility_10"] = (
            df["daily_range_pct"]
            .rolling(10)
            .std()
        )

        # contraction
        df["volatility_contraction"] = (
            df["volatility_10"]
            /
            df["volatility_10"]
            .rolling(10)
            .mean()
        )

        return df