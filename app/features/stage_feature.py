import numpy as np
import pandas as pd

from app.config.strategy_config import StrategyConfig


class StageFeature:
    """
    Weinstein Stage Analysis — classifies each price bar into Stage 1/2/3/4.

    Stage 1 — Basing:    EMA 200 flat, price oscillating below or at 200 EMA.
                         Accumulation phase; prior downtrend has ended.

    Stage 2 — Advancing: Price above rising 200 EMA; EMA stack 50>150>200.
                         The only stage to hold long positions.

    Stage 3 — Topping:   Price above 200 EMA but stack collapsing; 200 EMA flattening.
                         Distribution phase; reduce exposure.

    Stage 4 — Declining: Price below declining 200 EMA; EMA stack 50<150<200.
                         Never hold long positions here.

    Requires: ema_50, ema_150, ema_200 (computed by EMAFeature upstream).
    Produces: weinstein_stage (integer 1/2/3/4).
    """

    REQUIRED_COLUMNS = ["close_price", "ema_50", "ema_150", "ema_200"]

    @staticmethod
    def validate(df: pd.DataFrame) -> None:

        missing = [
            col for col in StageFeature.REQUIRED_COLUMNS
            if col not in df.columns
        ]

        if missing:
            raise ValueError(f"StageFeature missing columns: {missing}")

    @staticmethod
    def calculate(df: pd.DataFrame) -> pd.DataFrame:

        if df.empty:
            return df

        StageFeature.validate(df)

        df = df.copy()

        for col in StageFeature.REQUIRED_COLUMNS:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        slope_period = StrategyConfig.EMA_SLOPE_PERIOD
        flat_band = StrategyConfig.STAGE_EMA200_SLOPE_NEUTRAL_BAND

        # Normalized EMA 200 slope: % change over slope_period bars
        prior_ema_200 = df["ema_200"].shift(slope_period)
        safe_prior = prior_ema_200.replace(0, np.nan)

        ema_200_pct_slope = (
            (df["ema_200"] - safe_prior) / safe_prior * 100
        ).fillna(0.0)

        # ── Stage 2: the only stage worth owning ─────────────────────────
        stage2 = (
            (df["close_price"] > df["ema_200"])
            & (df["close_price"] > df["ema_150"])
            & (df["ema_50"] > df["ema_150"])
            & (df["ema_150"] > df["ema_200"])
            & (ema_200_pct_slope > 0)
        )

        # ── Stage 4: full downtrend — never long ──────────────────────────
        stage4 = (
            (df["close_price"] < df["ema_200"])
            & (df["ema_50"] < df["ema_150"])
            & (df["ema_150"] < df["ema_200"])
            & (ema_200_pct_slope < -flat_band)
        )

        # ── Stage 1: basing — below EMA 200 but slope not negative enough ─
        stage1 = (
            ~stage2
            & ~stage4
            & (df["close_price"] < df["ema_200"])
            & (ema_200_pct_slope >= -flat_band)
            & (ema_200_pct_slope <= flat_band)
        )

        # ── Stage 3: everything else — topping / distribution ────────────
        df["weinstein_stage"] = np.where(
            stage2, 2,
            np.where(
                stage4, 4,
                np.where(stage1, 1, 3),
            ),
        )

        return df
