import pandas as pd


class EMAFeature:

    EMA_PERIODS = [
        10,
        21,
        50,
        150,
        200
    ]

    @staticmethod
    def calculate(
        df: pd.DataFrame
    ) -> pd.DataFrame:

        if df.empty:

            return df

        required_columns = [
            "close_price"
        ]

        missing_columns = [
            col
            for col in required_columns
            if col not in df.columns
        ]

        if missing_columns:

            raise ValueError(
                f"Missing required columns: {missing_columns}"
            )

        df = df.copy()

        # ensure sorting
        df.sort_values(
            by="trade_date",
            inplace=True
        )

        # numeric conversion
        df["close_price"] = pd.to_numeric(
            df["close_price"],
            errors="coerce"
        )

        # remove invalid rows
        df.dropna(
            subset=["close_price"],
            inplace=True
        )

        # calculate EMAs
        for period in EMAFeature.EMA_PERIODS:

            df[f"ema_{period}"] = (
                df["close_price"]
                .ewm(
                    span=period,
                    adjust=False
                )
                .mean()
            )

        # trend scoring
        df["trend_score"] = 0

        df.loc[
            df["ema_21"] > df["ema_50"],
            "trend_score"
        ] += 1

        df.loc[
            df["ema_50"] > df["ema_150"],
            "trend_score"
        ] += 1

        df.loc[
            df["ema_150"] > df["ema_200"],
            "trend_score"
        ] += 1

        df.loc[
            df["close_price"] > df["ema_21"],
            "trend_score"
        ] += 1

        return df