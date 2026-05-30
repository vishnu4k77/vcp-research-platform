import pandas as pd
from sqlalchemy import text

from app.config.db import engine
from app.config.logging_config import get_logger

from app.signals.trend_signal import TrendSignal
from app.signals.stage_signal import StageSignal
from app.signals.vcp_signal import VCPSignal
from app.signals.liquidity_signal import LiquiditySignal
from app.signals.quality_signal import QualitySignal
from app.signals.breakout_signal import BreakoutSignal
from app.signals.composite_ranker import CompositeRanker

logger = get_logger(__name__)


class SignalPipeline:
    """
    Consumes stock_features rows, applies all signal detectors in dependency
    order, and persists the result to stock_signals.

    Signal execution order matters — later signals may consume outputs from
    earlier ones (e.g. VCPSignal reads stage2_signal produced by StageSignal).
    """

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
        "trend_score",
    ]

    # Columns written to stock_signals after all signals are computed.
    # Must exactly match what the signal chain produces.
    OUTPUT_COLUMNS = [
        "symbol",
        "trade_date",
        # core binary signals
        "trend_signal",
        "stage2_signal",
        "vcp_signal",
        "breakout_signal",
        "liquidity_signal",
        "quality_signal",
        # sub-signals (transparency + UI drilling)
        "ema_alignment_signal",
        "volume_contraction_signal",
        "volatility_contraction_signal",
        "breakout_ready_signal",
        "rs_signal",
        # composite scoring
        "composite_score",
        "composite_rank",
        "institutional_candidate",
        # context metrics for the scanner UI
        "distance_from_pivot_pct",
        "distance_from_52w_high_pct",
    ]

    @staticmethod
    def load_feature_data() -> pd.DataFrame:

        # Explicit column list — avoid SELECT * in production
        query = """
        SELECT
            symbol,
            trade_date,
            close_price,
            volume,
            ema_10,
            ema_21,
            ema_50,
            ema_150,
            ema_200,
            trend_score,
            avg_volume_10,
            avg_volume_20,
            relative_volume,
            atr_14,
            daily_range_pct,
            volatility_10,
            volatility_contraction,
            weinstein_stage
        FROM stock_features
        ORDER BY symbol, trade_date ASC
        """

        try:
            df = pd.read_sql(query, engine)
            logger.info("Loaded %d feature rows from stock_features", len(df))
            return df

        except Exception as exc:
            logger.error("Feature load failed: %s", exc, exc_info=True)
            return pd.DataFrame()

    @staticmethod
    def validate_feature_data(df: pd.DataFrame) -> pd.DataFrame:

        if df.empty:
            logger.warning("Feature dataframe is empty")
            return pd.DataFrame()

        df = df.copy()

        missing = [
            col for col in SignalPipeline.REQUIRED_COLUMNS
            if col not in df.columns
        ]

        if missing:
            logger.error("Signal pipeline missing feature columns: %s", missing)
            return pd.DataFrame()

        df.drop_duplicates(
            subset=["symbol", "trade_date"],
            inplace=True,
        )

        numeric_columns = [
            c for c in SignalPipeline.REQUIRED_COLUMNS
            if c not in ("symbol", "trade_date")
        ]

        for col in numeric_columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df.dropna(subset=SignalPipeline.REQUIRED_COLUMNS, inplace=True)

        df.sort_values(
            by=["symbol", "trade_date"],
            inplace=True,
        )
        df.reset_index(drop=True, inplace=True)

        return df

    @staticmethod
    def process_symbol(symbol_df: pd.DataFrame) -> pd.DataFrame:
        """
        Runs the full signal chain for a single symbol.
        Each step may produce columns consumed by subsequent steps.
        """

        if symbol_df.empty:
            return pd.DataFrame()

        try:
            # Step 1 — base trend: requires close_price + EMA columns
            symbol_df = TrendSignal.calculate(symbol_df)

            # Step 2 — Weinstein Stage 2 confirmation
            symbol_df = StageSignal.calculate(symbol_df)

            # Step 3 — VCP: reads stage2_signal from step 2
            symbol_df = VCPSignal.calculate(symbol_df)

            # Step 4 — liquidity gate
            symbol_df = LiquiditySignal.calculate(symbol_df)

            # Step 5 — quality gate: reads stage2_signal + trend_score
            symbol_df = QualitySignal.calculate(symbol_df)

            # Step 6 — breakout: reads EMA stack + relative_volume
            symbol_df = BreakoutSignal.calculate(symbol_df)

            # Step 7 — composite rank: reads all signal columns above
            symbol_df = CompositeRanker.calculate(symbol_df)

            return symbol_df

        except Exception as exc:
            symbol = symbol_df["symbol"].iloc[0] if not symbol_df.empty else "UNKNOWN"
            logger.error("Signal generation failed for %s: %s", symbol, exc, exc_info=True)
            return pd.DataFrame()

    @staticmethod
    def _clear_signals_table() -> None:
        """
        TRUNCATE stock_signals if it exists — preserves the user's primary key,
        unique constraint, and created_at default.
        If the table does not yet exist, to_sql will create it on the next save.
        """

        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "IF OBJECT_ID('dbo.stock_signals', 'U') IS NOT NULL "
                        "TRUNCATE TABLE stock_signals"
                    )
                )
            logger.info("stock_signals cleared")

        except Exception as exc:
            logger.error("Failed to clear stock_signals: %s", exc, exc_info=True)

    @staticmethod
    def save_signals(df: pd.DataFrame) -> None:

        if df.empty:
            logger.warning("No signals to save")
            return

        # Keep only columns that exist in the dataframe (graceful on partial runs)
        available = [
            col for col in SignalPipeline.OUTPUT_COLUMNS
            if col in df.columns
        ]

        missing_output = [
            col for col in SignalPipeline.OUTPUT_COLUMNS
            if col not in df.columns
        ]

        if missing_output:
            logger.warning("Signal output missing columns (will be skipped): %s", missing_output)

        final_df = df[available].copy()

        final_df.drop_duplicates(
            subset=["symbol", "trade_date"],
            inplace=True,
        )

        try:
            # SQL Server limit: 2100 params. stock_signals has ~20 cols → max 105 rows/insert.
            final_df.to_sql(
                "stock_signals",
                engine,
                if_exists="append",
                index=False,
                method="multi",
                chunksize=100,
            )
            logger.info("Saved %d signal rows to stock_signals", len(final_df))

        except Exception as exc:
            logger.error("Signal insert failed: %s", exc, exc_info=True)

    @staticmethod
    def run() -> None:

        logger.info("Signal pipeline started")

        raw_df = SignalPipeline.load_feature_data()

        if raw_df.empty:
            logger.warning("No feature data available — signal pipeline aborted")
            return

        validated_df = SignalPipeline.validate_feature_data(raw_df)

        logger.info("Validated feature rows: %d", len(validated_df))

        if validated_df.empty:
            logger.warning("No valid feature data after validation — aborting")
            return

        # Truncate before repopulating (preserves user's schema + constraints)
        SignalPipeline._clear_signals_table()

        all_signal_frames = []

        unique_symbols = validated_df["symbol"].unique()

        logger.info("Processing signals for %d symbols", len(unique_symbols))

        for symbol in unique_symbols:

            symbol_df = validated_df[
                validated_df["symbol"] == symbol
            ].copy()

            signal_df = SignalPipeline.process_symbol(symbol_df)

            if signal_df.empty:
                logger.debug("No signals generated for %s", symbol)
                continue

            all_signal_frames.append(signal_df)

        if not all_signal_frames:
            logger.warning("No signal frames generated across all symbols")
            return

        final_signal_df = pd.concat(all_signal_frames, ignore_index=True)

        logger.info("Total signal rows: %d", len(final_signal_df))

        SignalPipeline.save_signals(final_signal_df)

        logger.info("Signal pipeline completed")


if __name__ == "__main__":
    SignalPipeline.run()
