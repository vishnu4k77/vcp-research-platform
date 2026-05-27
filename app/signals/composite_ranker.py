import pandas as pd
import numpy as np

from app.config.strategy_config import (
    StrategyConfig
)


class CompositeRanker:

    REQUIRED_COLUMNS = [

        "trend_signal",
        "stage2_signal",
        "liquidity_signal",
        "quality_signal",
        "vcp_signal",
        "breakout_signal"
    ]

    @staticmethod
    def validate(
        df: pd.DataFrame
    ):

        missing = [

            col
            for col in (
                CompositeRanker
                .REQUIRED_COLUMNS
            )
            if col not in df.columns
        ]

        if missing:

            raise ValueError(
                f"Missing signal columns: {missing}"
            )

    @staticmethod
    def normalize_signal(
        series: pd.Series
    ) -> pd.Series:

        return (

            pd.to_numeric(
                series,
                errors="coerce"
            )

            .fillna(0)

            .clip(
                lower=0,
                upper=1
            )
        )

    @staticmethod
    def calculate(
        df: pd.DataFrame
    ) -> pd.DataFrame:

        if df.empty:

            return df

        CompositeRanker.validate(df)

        df = df.copy()

        weights = (
            StrategyConfig
            .SIGNAL_WEIGHTS
        )

        score = pd.Series(
            0,
            index=df.index,
            dtype=float
        )

        total_weight = sum(
            weights.values()
        )

        for signal, weight in (
            weights.items()
        ):

            normalized_signal = (

                CompositeRanker
                .normalize_signal(
                    df[signal]
                )
            )

            weighted_signal = (

                normalized_signal
                *
                weight
            )

            score += weighted_signal

        df["composite_score"] = (

            (
                score
                /
                total_weight
            )
            * 100
        )

        df["composite_score"] = (
            df["composite_score"]
            .round(2)
        )

        df["composite_rank"] = (

            df["composite_score"]

            .rank(

                ascending=False,

                method="dense",

                pct=True
            )
        )

        df["composite_rank"] = (

            df["composite_rank"]
            * 100
        )

        df["institutional_candidate"] = np.where(

            df["composite_score"]

            >=

            (
                StrategyConfig
                .MIN_COMPOSITE_SCORE
            ),

            1,

            0
        )

        return df