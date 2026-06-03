"""Price Action pipeline — standalone, reads stock_features, writes stock_pa_signals.

Completely independent of the main pipeline.  Run after the main pipeline
has populated stock_features (EMA50 and relative_volume must exist).

Run command:
    python scripts/run_pa_pipeline.py

Flow:
    1. Load stock_features (close, high, low, ema_50, relative_volume)
    2. Per-symbol: PriceActionFeature.calculate()  → daily + weekly features
    3. Per-symbol: PriceActionSignal.calculate()   → pa_score, pa_signal
    4. Truncate stock_pa_signals
    5. Bulk-insert results

Zero changes to stock_features, stock_signals, or any existing pipeline step.
"""

import sys

import pandas as pd
from sqlalchemy import text
from tqdm import tqdm

from app.config.db import engine
from app.config.logging_config import get_logger
from app.config.strategy_config import PriceActionConfig, StrategyConfig
from app.features.price_action_feature import PriceActionFeature
from app.signals.price_action_signal import PriceActionSignal

logger = get_logger(__name__)

# Columns loaded from stock_features — only what PA computation needs.
# Reuses EMA50 and relative_volume already computed by FeaturePipeline.
_LOAD_COLUMNS: list[str] = [
    "symbol",
    "trade_date",
    "close_price",
    "high_price",
    "low_price",
    "ema_50",
    "relative_volume",
]

# Output columns written to stock_pa_signals — must match table DDL
_OUTPUT_COLUMNS: list[str] = [
    "symbol",
    "trade_date",
    "pa_hh_daily",
    "pa_hl_daily",
    "pa_hh_weekly",
    "pa_hl_weekly",
    "pa_close_position",
    "pa_vol_quality",
    "pa_momentum_accel",
    "pa_extension_ok",
    "pa_score",
    "pa_signal",
]


class PAPipeline:
    """Orchestrate PA feature + signal computation and persistence.

    Reads from stock_features (read-only), writes to stock_pa_signals.
    Main pipeline (stock_features, stock_signals) is never touched.
    """

    @staticmethod
    def _load_features() -> pd.DataFrame:
        """Load the PA-relevant columns from stock_features.

        Returns:
            DataFrame sorted by symbol, trade_date ascending.
            Empty DataFrame on error.
        """
        cols = ", ".join(_LOAD_COLUMNS)
        query = text(f"""
            SELECT {cols}
            FROM stock_features
            ORDER BY symbol, trade_date ASC
        """)
        try:
            with engine.connect() as conn:
                df = pd.read_sql(query, conn)
            logger.info("Loaded %d rows from stock_features for PA pipeline", len(df))
            return df
        except Exception as exc:
            logger.error("PA feature load failed: %s", exc, exc_info=True)
            return pd.DataFrame()

    @staticmethod
    def _validate(df: pd.DataFrame) -> pd.DataFrame:
        """Drop rows with missing PA-critical columns.

        Args:
            df: Raw DataFrame from _load_features().

        Returns:
            Cleaned DataFrame, or empty on failure.
        """
        if df.empty:
            return df

        required = ["symbol", "trade_date", "close_price", "ema_50", "relative_volume"]
        for col in ["close_price", "ema_50", "relative_volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        before = len(df)
        df = df.dropna(subset=required).copy()
        dropped = before - len(df)
        if dropped:
            logger.warning("PA validation: dropped %d rows with nulls", dropped)

        df.sort_values(["symbol", "trade_date"], inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    @staticmethod
    def _clear_pa_table() -> None:
        """Truncate stock_pa_signals — preserves schema and constraints."""
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    "IF OBJECT_ID('dbo.stock_pa_signals','U') IS NOT NULL "
                    "TRUNCATE TABLE stock_pa_signals"
                ))
            logger.info("stock_pa_signals cleared")
        except Exception as exc:
            logger.error("Failed to clear stock_pa_signals: %s", exc, exc_info=True)

    @staticmethod
    def _save(df: pd.DataFrame) -> None:
        """Bulk-insert PA signal rows to stock_pa_signals.

        Args:
            df: Final DataFrame with _OUTPUT_COLUMNS present.
        """
        if df.empty:
            logger.warning("No PA signals to save")
            return

        available = [c for c in _OUTPUT_COLUMNS if c in df.columns]
        missing   = [c for c in _OUTPUT_COLUMNS if c not in df.columns]
        if missing:
            logger.warning("PA output missing columns (skipped): %s", missing)

        final = df[available].copy()
        final.drop_duplicates(subset=["symbol", "trade_date"], inplace=True)

        # Coerce BIT columns to plain int so SQL Server accepts them
        bit_cols = [
            "pa_hh_daily", "pa_hl_daily", "pa_hh_weekly", "pa_hl_weekly",
            "pa_vol_quality", "pa_momentum_accel", "pa_extension_ok", "pa_signal",
        ]
        for col in bit_cols:
            if col in final.columns:
                final[col] = pd.to_numeric(final[col], errors="coerce").fillna(0).astype(int)

        try:
            final.to_sql(
                "stock_pa_signals",
                engine,
                if_exists="append",
                index=False,
                chunksize=PriceActionConfig.PA_SQL_BATCH_SIZE,
            )
            logger.info("Saved %d PA signal rows to stock_pa_signals", len(final))
        except Exception as exc:
            logger.error("PA signal insert failed: %s", exc, exc_info=True)

    @staticmethod
    def run() -> None:
        """Execute the full PA pipeline.

        Prerequisite: stock_features must be populated (run main pipeline first).
        """
        logger.info("PA pipeline started")

        raw_df = PAPipeline._load_features()
        if raw_df.empty:
            logger.warning("No stock_features data — PA pipeline aborted")
            return

        df = PAPipeline._validate(raw_df)
        if df.empty:
            logger.warning("No valid rows after validation — aborting")
            return

        n_symbols = df["symbol"].nunique()
        logger.info("Computing PA signals for %d symbols", n_symbols)

        all_frames: list[pd.DataFrame] = []

        for symbol, sym_df in tqdm(
            df.groupby("symbol", sort=False),
            desc="PA signals",
            unit="sym",
            ncols=90,
            file=sys.stderr,
            leave=True,
            total=n_symbols,
        ):
            sym_df = sym_df.copy()

            pa_df = PriceActionFeature.calculate(sym_df)
            if pa_df.empty:
                logger.debug("PA features empty for %s — skipping", symbol)
                continue

            pa_df = PriceActionSignal.calculate(pa_df)
            if pa_df.empty:
                logger.debug("PA signal empty for %s — skipping", symbol)
                continue

            all_frames.append(pa_df)

        if not all_frames:
            logger.warning("No PA signal frames generated")
            return

        final_df = pd.concat(all_frames, ignore_index=True)
        logger.info(
            "PA pipeline complete | symbols=%d | rows=%d | pa_signal=1 count=%d",
            n_symbols,
            len(final_df),
            int(final_df["pa_signal"].sum()) if "pa_signal" in final_df.columns else 0,
        )

        PAPipeline._clear_pa_table()
        PAPipeline._save(final_df)
        logger.info("PA pipeline finished")
