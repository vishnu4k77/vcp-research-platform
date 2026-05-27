import pandas as pd

from sqlalchemy import text

from app.config.db import engine

from app.features.ema_feature import EMAFeature
from app.features.volume_feature import VolumeFeature
from app.features.volatility_feature import VolatilityFeature


class FeaturePipeline:

    REQUIRED_COLUMNS = [

        "symbol",
        "trade_date",

        "open_price",
        "high_price",
        "low_price",
        "close_price",

        "volume"
    ]

    FEATURE_COLUMNS = [

        "symbol",
        "trade_date",

        "close_price",
        "volume",

        "ema_10",
        "ema_21",
        "ema_50",
        "ema_150",
        "ema_200",

        "trend_score",

        "avg_volume_10",
        "avg_volume_20",
        "relative_volume",

        "atr_14",

        "daily_range_pct",
        "volatility_10",
        "volatility_contraction"
    ]

    @staticmethod
    def load_price_data() -> pd.DataFrame:

        query = """

        SELECT

            symbol,
            trade_date,

            open_price,
            high_price,
            low_price,
            close_price,

            volume

        FROM daily_price_data

        ORDER BY
            symbol,
            trade_date ASC

        """

        try:

            df = pd.read_sql(
                query,
                engine
            )

            return df

        except Exception as e:

            print(
                "Failed loading daily_price_data"
            )

            print(str(e))

            return pd.DataFrame()

    @staticmethod
    def validate_price_data(
        df: pd.DataFrame
    ) -> pd.DataFrame:

        if df.empty:

            print(
                "Input dataframe empty"
            )

            return pd.DataFrame()

        df = df.copy()

        # required columns validation
        missing_columns = [

            col
            for col in FeaturePipeline.REQUIRED_COLUMNS
            if col not in df.columns
        ]

        if missing_columns:

            print(
                f"Missing columns: {missing_columns}"
            )

            return pd.DataFrame()

        # remove duplicates
        df.drop_duplicates(

            subset=[
                "symbol",
                "trade_date"
            ],

            inplace=True
        )

        numeric_columns = [

            "open_price",
            "high_price",
            "low_price",
            "close_price",
            "volume"
        ]

        # numeric normalization
        for col in numeric_columns:

            df[col] = pd.to_numeric(

                df[col],
                errors="coerce"
            )

        # datetime normalization
        df["trade_date"] = pd.to_datetime(

            df["trade_date"],
            errors="coerce"
        )

        # remove nulls
        df.dropna(

            subset=FeaturePipeline.REQUIRED_COLUMNS,
            inplace=True
        )

        # invalid prices
        df = df[

            (df["open_price"] > 0)
            &
            (df["high_price"] > 0)
            &
            (df["low_price"] > 0)
            &
            (df["close_price"] > 0)
        ]

        # invalid volume
        df = df[
            df["volume"] >= 0
        ]

        # malformed candles
        df = df[

            (df["high_price"] >= df["low_price"])
            &
            (df["high_price"] >= df["open_price"])
            &
            (df["high_price"] >= df["close_price"])
            &
            (df["low_price"] <= df["open_price"])
            &
            (df["low_price"] <= df["close_price"])
        ]

        # sorting
        df.sort_values(

            by=[
                "symbol",
                "trade_date"
            ],

            inplace=True
        )

        # reset index
        df.reset_index(

            drop=True,
            inplace=True
        )

        return df

    @staticmethod
    def process_symbol(
        symbol_df: pd.DataFrame
    ) -> pd.DataFrame:

        if symbol_df.empty:

            return pd.DataFrame()

        try:

            # EMA FEATURES
            symbol_df = (
                EMAFeature.calculate(
                    symbol_df
                )
            )

            # VOLUME FEATURES
            symbol_df = (
                VolumeFeature.calculate(
                    symbol_df
                )
            )

            # VOLATILITY FEATURES
            symbol_df = (
                VolatilityFeature.calculate(
                    symbol_df
                )
            )

            return symbol_df

        except Exception as e:

            symbol = (
                symbol_df["symbol"]
                .iloc[0]
            )

            print(
                f"Feature processing failed for {symbol}"
            )

            print(str(e))

            return pd.DataFrame()

    @staticmethod
    def save_features(
        df: pd.DataFrame
    ):

        if df.empty:

            print(
                "No features generated"
            )

            return

        final_df = df[
            FeaturePipeline.FEATURE_COLUMNS
        ].copy()

        # remove duplicates
        final_df.drop_duplicates(

            subset=[
                "symbol",
                "trade_date"
            ],

            inplace=True
        )

        try:

            final_df.to_sql(

                "stock_features",
                engine,

                if_exists="append",

                index=False
            )

            print(
                f"Inserted feature rows: {len(final_df)}"
            )

        except Exception as e:

            print(
                "Feature insert failed"
            )

            print(str(e))

    @staticmethod
    def clear_existing_features():

        try:

            with engine.begin() as conn:

                conn.execute(
                    text(
                        "DELETE FROM stock_features"
                    )
                )

            print(
                "Existing feature table cleared"
            )

        except Exception as e:

            print(
                "Failed clearing feature table"
            )

            print(str(e))

    @staticmethod
    def run():

        print(
            "\nStarting feature pipeline"
        )

        raw_df = (
            FeaturePipeline
            .load_price_data()
        )

        print(
            f"Loaded candle rows: {len(raw_df)}"
        )

        validated_df = (
            FeaturePipeline
            .validate_price_data(
                raw_df
            )
        )

        print(
            f"Validated rows: {len(validated_df)}"
        )

        if validated_df.empty:

            print(
                "No valid candle data found"
            )

            return

        FeaturePipeline.clear_existing_features()

        all_feature_frames = []

        unique_symbols = (
            validated_df["symbol"]
            .unique()
        )

        for symbol in unique_symbols:

            print(
                f"\nProcessing features for {symbol}"
            )

            symbol_df = validated_df[
                validated_df["symbol"] == symbol
            ].copy()

            feature_df = (
                FeaturePipeline
                .process_symbol(
                    symbol_df
                )
            )

            if feature_df.empty:

                print(
                    f"No features generated for {symbol}"
                )

                continue

            print(
                f"Generated rows: {len(feature_df)}"
            )

            all_feature_frames.append(
                feature_df
            )

        if not all_feature_frames:

            print(
                "No feature frames generated"
            )

            return

        final_feature_df = pd.concat(

            all_feature_frames,
            ignore_index=True
        )

        print(
            f"\nFinal feature rows: {len(final_feature_df)}"
        )

        FeaturePipeline.save_features(
            final_feature_df
        )

        print(
            "\nFeature pipeline completed"
        )


if __name__ == "__main__":

    FeaturePipeline.run()