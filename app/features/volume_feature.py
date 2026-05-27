import pandas as pd


class VolumeFeature:

    @staticmethod
    def calculate(
        df: pd.DataFrame
    ) -> pd.DataFrame:

        if df.empty:

            return df

        df = df.copy()

        df.sort_values(
            by="trade_date",
            inplace=True
        )

        # numeric cleanup
        df["volume"] = pd.to_numeric(
            df["volume"],
            errors="coerce"
        )

        df.dropna(
            subset=["volume"],
            inplace=True
        )

        # remove invalid volume
        df = df[
            df["volume"] >= 0
        ]

        # average volumes
        df["avg_volume_10"] = (
            df["volume"]
            .rolling(10)
            .mean()
        )

        df["avg_volume_20"] = (
            df["volume"]
            .rolling(20)
            .mean()
        )

        # relative volume
        df["relative_volume"] = (
            df["volume"] /
            df["avg_volume_20"]
        )

        return df