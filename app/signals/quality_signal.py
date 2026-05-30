import numpy as np
import pandas as pd

from app.config.logging_config import get_logger
from app.config.strategy_config import StrategyConfig

logger = get_logger(__name__)


class QualitySignal:
    """
    Quality signal — technical proxy for Phase 1.

    Phase 1 (now): purely technical — trend quality + volume adequacy + Stage 2.
    Phase 2 (Screener.in data available): replace with fundamental scorecard
      (ROE ≥ 12%, ROCE ≥ 12%, Debt/Equity ≤ 1.0, etc.) per FUNDAMENTAL_FILTERS.

    Produces two columns:
      quality_signal  — 1 if stock passes technical quality gate, else 0
      rs_signal       — relative-strength vs Nifty 50; 0 until index data available
    """

    # Phase 1 gate: trend_score is 0-4; require ≥ 3 conditions met
    TREND_SCORE_MIN = 3

    @staticmethod
    def calculate(df: pd.DataFrame) -> pd.DataFrame:

        if df.empty:
            return df

        df = df.copy()

        # RS signal: 0 placeholder until Nifty index comparison is built (Phase 2)
        df["rs_signal"] = 0

        for col in ["trend_score", "avg_volume_20"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

        has_stage2 = (
            df["stage2_signal"] == 1
            if "stage2_signal" in df.columns
            else pd.Series(False, index=df.index)
        )

        strong_trend = df["trend_score"] >= QualitySignal.TREND_SCORE_MIN

        adequate_volume = df["avg_volume_20"] >= StrategyConfig.MIN_AVG_VOLUME_20

        df["quality_signal"] = np.where(
            (has_stage2 & strong_trend & adequate_volume),
            1,
            0,
        )

        return df
