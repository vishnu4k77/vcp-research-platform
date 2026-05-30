import numpy as np
import pandas as pd

from app.config.logging_config import get_logger
from app.config.strategy_config import StrategyConfig

logger = get_logger(__name__)


class VCPSignal:
    """
    Volatility Contraction Pattern — Minervini SEPA methodology.

    Detects stocks in Stage 2 uptrend whose ATR, daily range, and volume
    are all contracting simultaneously, indicating an institutional
    accumulation base before a high-probability breakout.

    Produces four columns:
      ema_alignment_signal        — tight EMA stack (close > 10 > 21 > 50)
      volume_contraction_signal   — relative volume below dry-up threshold
      volatility_contraction_signal — rolling vol below its own average
      vcp_signal                  — all VCP conditions met simultaneously
    """

    REQUIRED_COLUMNS = [
        "close_price",
        "ema_10",
        "ema_21",
        "ema_50",
        "relative_volume",
        "atr_14",
        "volatility_contraction",
    ]

    @staticmethod
    def validate(df: pd.DataFrame) -> None:

        missing = [
            col for col in VCPSignal.REQUIRED_COLUMNS
            if col not in df.columns
        ]

        if missing:
            raise ValueError(f"VCPSignal missing columns: {missing}")

    @staticmethod
    def calculate(df: pd.DataFrame) -> pd.DataFrame:

        if df.empty:
            return df

        VCPSignal.validate(df)

        df = df.copy()

        for col in VCPSignal.REQUIRED_COLUMNS:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # ── Tight EMA alignment ──────────────────────────────────────────
        # Price stacked cleanly above 10 > 21 > 50 — institutional quality
        df["ema_alignment_signal"] = np.where(
            (
                (df["close_price"] > df["ema_10"])
                & (df["ema_10"] > df["ema_21"])
                & (df["ema_21"] > df["ema_50"])
            ),
            1,
            0,
        )

        # ── Volume dry-up ────────────────────────────────────────────────
        # Quiet accumulation: professionals buying, retail absent
        df["volume_contraction_signal"] = np.where(
            df["relative_volume"] < StrategyConfig.VCP_VOLUME_CONTRACTION_RATIO,
            1,
            0,
        )

        # ── Volatility contraction ───────────────────────────────────────
        # Current daily-range volatility below its own rolling average
        df["volatility_contraction_signal"] = np.where(
            df["volatility_contraction"] < StrategyConfig.VCP_VOLATILITY_CONTRACTION_RATIO,
            1,
            0,
        )

        # ── ATR contraction ──────────────────────────────────────────────
        # Each contraction tighter than the last — the VCP "coiling spring"
        atr_peak = (
            df["atr_14"]
            .rolling(
                StrategyConfig.VCP_ATR_LOOKBACK,
                min_periods=20,
            )
            .max()
        )

        safe_atr_peak = atr_peak.replace(0, np.nan)
        atr_ratio = df["atr_14"] / safe_atr_peak
        atr_contracting = atr_ratio.fillna(1.0) < StrategyConfig.VCP_ATR_CONTRACTION_RATIO

        # ── Stage 2 prerequisite ─────────────────────────────────────────
        # VCP only valid inside a confirmed Stage 2 uptrend
        has_stage2 = (
            df["stage2_signal"] == 1
            if "stage2_signal" in df.columns
            else pd.Series(False, index=df.index)
        )

        # ── Composite VCP ─────────────────────────────────────────────────
        df["vcp_signal"] = np.where(
            (
                has_stage2
                & (df["ema_alignment_signal"] == 1)
                & (df["volume_contraction_signal"] == 1)
                & (df["volatility_contraction_signal"] == 1)
                & atr_contracting
            ),
            1,
            0,
        )

        return df
