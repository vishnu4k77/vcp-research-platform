"""Multi-Timeframe (MTF) pipeline — standalone, reads stock_features, writes stock_mtf_signals.

Completely independent of the main pipeline.  Run after the main pipeline
has populated stock_features (close_price must exist for all symbols).

Run command:
    python scripts/run_mtf_pipeline.py

Flow:
    1. Load close_price history from stock_features (all symbols, all dates)
    2. Per-symbol: MTFFeature.calculate()  → weekly + monthly EMA columns
    3. Per-symbol: MTFSignal.calculate()   → mtf_weekly_trend, mtf_monthly_trend, mtf_score
    4. Truncate stock_mtf_signals
    5. Bulk-insert results

Zero changes to stock_features, stock_signals, or any existing pipeline step.
Mirrors the PAPipeline architecture exactly.
"""

import sys

import pandas as pd
from sqlalchemy import text
from tqdm import tqdm

from app.config.db import engine
from app.config.logging_config import get_logger
from app.config.strategy_config import MTFConfig
from app.features.mtf_feature import MTFFeature
from app.signals.mtf_signal import MTFSignal

logger = get_logger(__name__)

# Columns loaded from stock_features — only what MTF computation needs.
_LOAD_COLUMNS: list[str] = [
    "symbol",
    "trade_date",
    "close_price",
]

# Output columns written to stock_mtf_signals — must match table DDL
_OUTPUT_COLUMNS: list[str] = [
    "symbol",
    "trade_date",
    "mtf_w_ema_fast",
    "mtf_w_ema_slow",
    "mtf_m_ema_fast",
    "mtf_m_ema_slow",
    "mtf_weekly_trend",
    "mtf_monthly_trend",
    "mtf_score",
]


class MTFPipeline:
    """Orchestrate MTF feature + signal computation and persistence.

    Reads from stock_features (read-only), writes to stock_mtf_signals.
    Main pipeline (stock_features, stock_signals) is never touched.
    """

    @staticmethod
    def _load_features() -> pd.DataFrame:
        """Load close_price history from stock_features for all symbols.

        Returns:
            DataFrame sorted by symbol, trade_date ascending.
            Empty DataFrame on error.
        """
        cols  = ", ".join(_LOAD_COLUMNS)
        query = text(f"""
            SELECT {cols}
            FROM stock_features
            ORDER BY symbol ASC, trade_date ASC
        """)
        try:
            with engine.connect() as conn:
                df = pd.read_sql(query, conn)
            logger.info("Loaded %d rows from stock_features for MTF pipeline", len(df))
            return df
        except Exception as exc:
            logger.error("MTF feature load failed: %s", exc, exc_info=True)
            return pd.DataFrame()

    @staticmethod
    def _validate(df: pd.DataFrame) -> pd.DataFrame:
        """Drop rows with missing MTF-critical columns and coerce types.

        Args:
            df: Raw DataFrame from _load_features().

        Returns:
            Cleaned DataFrame, or empty on failure.
        """
        if df.empty:
            return df

        df = df.copy()
        df["close_price"] = pd.to_numeric(df["close_price"], errors="coerce")

        required = ["symbol", "trade_date", "close_price"]
        before   = len(df)
        df       = df.dropna(subset=required)
        dropped  = before - len(df)
        if dropped:
            logger.warning("MTF validation: dropped %d rows with nulls in required columns", dropped)

        df.sort_values(["symbol", "trade_date"], inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    @staticmethod
    def _clear_mtf_table() -> None:
        """Truncate stock_mtf_signals — preserves schema and constraints."""
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    "IF OBJECT_ID('dbo.stock_mtf_signals','U') IS NOT NULL "
                    "TRUNCATE TABLE stock_mtf_signals"
                ))
            logger.info("stock_mtf_signals cleared")
        except Exception as exc:
            logger.error("Failed to clear stock_mtf_signals: %s", exc, exc_info=True)

    @staticmethod
    def _save(df: pd.DataFrame) -> None:
        """Bulk-insert MTF signal rows to stock_mtf_signals.

        Args:
            df: Final DataFrame with _OUTPUT_COLUMNS present.
        """
        if df.empty:
            logger.warning("No MTF signals to save")
            return

        available = [c for c in _OUTPUT_COLUMNS if c in df.columns]
        missing   = [c for c in _OUTPUT_COLUMNS if c not in df.columns]
        if missing:
            logger.warning("MTF output missing columns (skipped): %s", missing)

        final = df[available].copy()
        final.drop_duplicates(subset=["symbol", "trade_date"], inplace=True)

        # Coerce BIT columns to plain int so SQL Server accepts them
        bit_cols = ["mtf_weekly_trend", "mtf_monthly_trend"]
        for col in bit_cols:
            if col in final.columns:
                final[col] = pd.to_numeric(final[col], errors="coerce").fillna(0).astype(int)

        # mtf_score is INT (0-2)
        if "mtf_score" in final.columns:
            final["mtf_score"] = pd.to_numeric(final["mtf_score"], errors="coerce").fillna(0).astype(int)

        try:
            final.to_sql(
                "stock_mtf_signals",
                engine,
                if_exists="append",
                index=False,
                chunksize=MTFConfig.MTF_SQL_BATCH_SIZE,
            )
            logger.info(
                "Saved %d MTF signal rows to stock_mtf_signals | "
                "weekly_trend=1: %d | monthly_trend=1: %d",
                len(final),
                int(final["mtf_weekly_trend"].sum())  if "mtf_weekly_trend"  in final.columns else 0,
                int(final["mtf_monthly_trend"].sum()) if "mtf_monthly_trend" in final.columns else 0,
            )
        except Exception as exc:
            logger.error("MTF signal insert failed: %s", exc, exc_info=True)

    @staticmethod
    def run() -> None:
        """Execute the full MTF pipeline.

        Prerequisite: stock_features must be populated (run main pipeline first).
        """
        logger.info("MTF pipeline started")

        raw_df = MTFPipeline._load_features()
        if raw_df.empty:
            logger.warning("No stock_features data — MTF pipeline aborted")
            return

        df = MTFPipeline._validate(raw_df)
        if df.empty:
            logger.warning("No valid rows after validation — aborting")
            return

        n_symbols = df["symbol"].nunique()
        logger.info("Computing MTF signals for %d symbols", n_symbols)

        all_frames: list[pd.DataFrame] = []

        for symbol, sym_df in tqdm(
            df.groupby("symbol", sort=False),
            desc="MTF signals",
            unit="sym",
            ncols=90,
            file=sys.stderr,
            leave=True,
            total=n_symbols,
        ):
            sym_df = sym_df.copy()

            mtf_df = MTFFeature.calculate(sym_df)
            if mtf_df.empty:
                logger.debug("MTF features empty for %s — skipping", symbol)
                continue

            mtf_df = MTFSignal.calculate(mtf_df)
            if mtf_df.empty:
                logger.debug("MTF signal empty for %s — skipping", symbol)
                continue

            all_frames.append(mtf_df)

        if not all_frames:
            logger.warning("No MTF signal frames generated")
            return

        final_df = pd.concat(all_frames, ignore_index=True)
        logger.info(
            "MTF pipeline complete | symbols=%d | rows=%d | "
            "weekly_trend=1: %d | monthly_trend=1: %d | score=2: %d",
            n_symbols,
            len(final_df),
            int(final_df["mtf_weekly_trend"].sum())              if "mtf_weekly_trend"  in final_df.columns else 0,
            int(final_df["mtf_monthly_trend"].sum())             if "mtf_monthly_trend" in final_df.columns else 0,
            int((final_df["mtf_score"] == 2).sum())              if "mtf_score"         in final_df.columns else 0,
        )

        MTFPipeline._clear_mtf_table()
        MTFPipeline._save(final_df)
        logger.info("MTF pipeline finished")
