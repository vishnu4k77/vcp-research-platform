"""Price Action signal — composite pa_score and binary pa_signal.

Converts PA feature columns (produced by PriceActionFeature) into a 0-100
pa_score and a binary pa_signal.

Scoring (weights sum to 100 — all in PriceActionConfig):
    25 pts  daily trend    — pa_hh_daily AND pa_hl_daily
    25 pts  weekly trend   — pa_hh_weekly AND pa_hl_weekly  (hard gate)
    15 pts  close quality  — close_position >= PA_MIN_CLOSE_POSITION
    15 pts  volume quality — relative_volume in accumulation range
    10 pts  momentum       — roc_10 > roc_20 > 0
    10 pts  extension      — close within EMA50 range

pa_signal = 1  only when:
    pa_score >= PA_MIN_SIGNAL_SCORE (70)
    AND pa_hh_weekly = 1  (weekly structure is a hard gate — not compensable)
    AND pa_hl_weekly = 1
"""

import pandas as pd

from app.config.logging_config import get_logger
from app.config.strategy_config import PriceActionConfig

logger = get_logger(__name__)

# Feature columns consumed by this signal
REQUIRED_FEATURE_COLUMNS: list[str] = [
    "pa_hh_daily",
    "pa_hl_daily",
    "pa_hh_weekly",
    "pa_hl_weekly",
    "pa_close_position",
    "pa_vol_quality",
    "pa_momentum_accel",
    "pa_extension_ok",
]


class PriceActionSignal:
    """Compute pa_score and pa_signal from PA feature columns.

    Designed to be called per-symbol after PriceActionFeature.calculate().
    Returns the DataFrame with pa_score (FLOAT) and pa_signal (BIT) appended.
    """

    @staticmethod
    def calculate(df: pd.DataFrame) -> pd.DataFrame:
        """Compute pa_score and pa_signal for a per-symbol DataFrame.

        pa_score is a 0-100 weighted sum of six independent conditions.
        pa_signal requires pa_score >= threshold AND weekly trend confirmed.
        The weekly gate cannot be compensated — if either weekly flag is 0,
        pa_signal is 0 regardless of all other conditions.

        Args:
            df: Per-symbol DataFrame with PA feature columns appended by
                PriceActionFeature.calculate().

        Returns:
            df copy with pa_score (float) and pa_signal (int 0/1) appended.
            Returns empty DataFrame if required columns are missing.
        """
        if df.empty:
            return pd.DataFrame()

        missing = [c for c in REQUIRED_FEATURE_COLUMNS if c not in df.columns]
        if missing:
            symbol = df["symbol"].iloc[0] if "symbol" in df.columns else "UNKNOWN"
            logger.error("PriceActionSignal missing feature columns for %s: %s", symbol, missing)
            return pd.DataFrame()

        try:
            cfg = PriceActionConfig
            df  = df.copy()

            def _bit(col: str) -> pd.Series:
                return pd.to_numeric(df[col], errors="coerce").fillna(0).clip(0, 1)

            # ── pa_score ─────────────────────────────────────────────────────
            daily_trend   = (_bit("pa_hh_daily")  == 1) & (_bit("pa_hl_daily")  == 1)
            weekly_trend  = (_bit("pa_hh_weekly") == 1) & (_bit("pa_hl_weekly") == 1)
            close_quality = (
                pd.to_numeric(df["pa_close_position"], errors="coerce")
                .fillna(0.0) >= cfg.PA_MIN_CLOSE_POSITION
            )
            vol_quality   = _bit("pa_vol_quality")   == 1
            momentum      = _bit("pa_momentum_accel") == 1
            extension     = _bit("pa_extension_ok")  == 1

            pa_score = (
                  daily_trend.astype(float)   * cfg.PA_SCORE_DAILY_TREND
                + weekly_trend.astype(float)  * cfg.PA_SCORE_WEEKLY_TREND
                + close_quality.astype(float) * cfg.PA_SCORE_CLOSE_QUALITY
                + vol_quality.astype(float)   * cfg.PA_SCORE_VOL_QUALITY
                + momentum.astype(float)      * cfg.PA_SCORE_MOMENTUM
                + extension.astype(float)     * cfg.PA_SCORE_EXTENSION
            )

            df["pa_score"] = pa_score.round(1)

            # ── pa_signal — weekly trend is a hard gate ────────────────────
            df["pa_signal"] = (
                (pa_score >= cfg.PA_MIN_SIGNAL_SCORE)
                & weekly_trend
            ).astype(int)

            return df

        except Exception as exc:
            symbol = df["symbol"].iloc[0] if not df.empty else "UNKNOWN"
            logger.error("PriceActionSignal failed for %s: %s", symbol, exc, exc_info=True)
            return pd.DataFrame()
