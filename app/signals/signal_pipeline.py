import pandas as pd

from sqlalchemy import text

from app.config.db import engine

from app.signals.trend_signal import TrendSignal
from app.signals.stage_signal import StageSignal
from app.signals.vcp_signal import VCPSignal
from app.signals.liquidity_signal import LiquiditySignal
from app.signals.quality_signal import QualitySignal
from app.signals.breakout_signal import BreakoutSignal
from app.signals.composite_ranker import CompositeRanker


class SignalPipeline:

    REQUIRED_COLUMNS = [

        "symbol",
        "trade_date",

        "close_price",

        "ema_10",
        "ema_21",
        "ema_50",
        "ema_150",
        "ema_200",

        "avg_volume_20",
        "relative_volume",

        "atr_14",

        "volatility_10",
        "volatility_contraction",

        "trend_score"
    ]

    @staticmethod
    def load_feature_data():

        query = """

        SELECT *

        FROM stock_features

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
                "Feature load failed"
            )

            print(str(e))

            return pd.DataFrame()

    @staticmethod
    def validate_feature_data(
        df: pd.DataFrame
    ) -> pd.DataFrame:

        if df.empty:

            return pd.DataFrame()

        df = df.copy()

        missing_columns = [

            col
            for col in SignalPipeline.REQUIRED_COLUMNS
            if col not in df.columns
        ]

        if missing_columns:

            print(
                f"Missing columns: {missing_columns}"
            )

            return pd.DataFrame()

        df.drop_duplicates(

            subset=[
                "symbol",
                "trade_date"
            ],

            inplace=True
        )

        numeric_columns = [

            "close_price",

            "ema_10",
            "ema_21",
            "ema_50",
            "ema_150",
            "ema_200",

            "avg_volume_20",
            "relative_volume",

            "atr_14",

            "volatility_10",
            "volatility_contraction",

            "trend_score"
        ]

        for col in numeric_columns:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        df.dropna(

            subset=SignalPipeline.REQUIRED_COLUMNS,
            inplace=True
        )

        df.sort_values(

            by=[
                "symbol",
                "trade_date"
            ],

            inplace=True
        )

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

            symbol_df = (
                TrendSignal.calculate(
                    symbol_df
                )
            )

            symbol_df = (
                StageSignal.calculate(
                    symbol_df
                )
            )

            symbol_df = (
                VCPSignal.calculate(
                    symbol_df
                )
            )

            symbol_df = (
                LiquiditySignal.calculate(
                    symbol_df
                )
            )

            symbol_df = (
                QualitySignal.calculate(
                    symbol_df
                )
            )

            symbol_df = (
                BreakoutSignal.calculate(
                    symbol_df
                )
            )

            symbol_df = (
                CompositeRanker.calculate(
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
                f"Signal generation failed for {symbol}"
            )

            print(str(e))

            return pd.DataFrame()

    @staticmethod
    def clear_existing_signals():

        try:

            with engine.begin() as conn:

                conn.execute(
                    text(
                        "DELETE FROM stock_signals"
                    )
                )

            print(
                "Existing signals cleared"
            )

        except Exception as e:

            print(
                "Failed clearing signals"
            )

            print(str(e))

    @staticmethod
    def save_signals(
        df: pd.DataFrame
    ):

        if df.empty:

            print(
                "No signals generated"
            )

            return

        final_columns = [

            "symbol",
            "trade_date",

            "trend_signal",
            "stage2_signal",

            "ema_alignment_signal",

            "volume_contraction_signal",
            "volatility_contraction_signal",

            "breakout_ready_signal",

            "liquidity_signal",

            "quality_signal",

            "rs_signal",

            "vcp_signal",

            "composite_score"
        ]

        final_df = df[
            final_columns
        ].copy()

        final_df.drop_duplicates(

            subset=[
                "symbol",
                "trade_date"
            ],

            inplace=True
        )

        try:

            final_df.to_sql(

                "stock_signals",
                engine,

                if_exists="append",

                index=False
            )

            print(
                f"Inserted signal rows: {len(final_df)}"
            )

        except Exception as e:

            print(
                "Signal insert failed"
            )

            print(str(e))

    @staticmethod
    def run():

        print(
            "\nStarting signal pipeline"
        )

        raw_df = (
            SignalPipeline
            .load_feature_data()
        )

        print(
            f"Loaded feature rows: {len(raw_df)}"
        )

        validated_df = (
            SignalPipeline
            .validate_feature_data(
                raw_df
            )
        )

        print(
            f"Validated rows: {len(validated_df)}"
        )

        if validated_df.empty:

            print(
                "No valid feature data"
            )

            return

        SignalPipeline.clear_existing_signals()

        all_signal_frames = []

        unique_symbols = (
            validated_df["symbol"]
            .unique()
        )

        for symbol in unique_symbols:

            print(
                f"\nGenerating signals for {symbol}"
            )

            symbol_df = validated_df[
                validated_df["symbol"] == symbol
            ].copy()

            signal_df = (
                SignalPipeline
                .process_symbol(
                    symbol_df
                )
            )

            if signal_df.empty:

                continue

            all_signal_frames.append(
                signal_df
            )

        if not all_signal_frames:

            print(
                "No signals generated"
            )

            return

        final_signal_df = pd.concat(

            all_signal_frames,
            ignore_index=True
        )

        print(
            f"\nFinal signal rows: {len(final_signal_df)}"
        )

        SignalPipeline.save_signals(
            final_signal_df
        )

        print(
            "\nSignal pipeline completed"
        )


if __name__ == "__main__":

    SignalPipeline.run()