import sys

import pandas as pd
from sqlalchemy import text
from tqdm import tqdm

from app.config.db import engine
from app.config.logging_config import get_logger
from app.config.strategy_config import StrategyConfig

from app.features.ema_feature import EMAFeature
from app.features.stage_feature import StageFeature
from app.features.volume_feature import VolumeFeature
from app.features.volatility_feature import VolatilityFeature

logger = get_logger(__name__)


class FeaturePipeline:
    """
    Loads raw OHLCV data from daily_price_data, runs all feature calculators
    in dependency order, and persists results to stock_features.

    Feature execution order:
      EMAFeature       → ema_10/21/50/150/200, trend_score
      StageFeature     → weinstein_stage  (consumes EMA columns)
      VolumeFeature    → avg_volume_10/20, relative_volume
      VolatilityFeature→ atr_14, daily_range_pct, volatility_10, volatility_contraction
    """

    REQUIRED_COLUMNS = [
        "symbol",
        "trade_date",
        "open_price",
        "high_price",
        "low_price",
        "close_price",
        "volume",
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
        "weinstein_stage",
        "avg_volume_10",
        "avg_volume_20",
        "relative_volume",
        "atr_14",
        "daily_range_pct",
        "volatility_10",
        "volatility_contraction",
        # price context — used by BreakoutSignal (DRY: computed once here, not per signal)
        "high_52w",
        "low_52w",
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
        ORDER BY symbol, trade_date ASC
        """

        try:
            df = pd.read_sql(query, engine)
            logger.info("Loaded %d rows from daily_price_data", len(df))
            return df

        except Exception as exc:
            logger.error("Failed loading daily_price_data: %s", exc, exc_info=True)
            return pd.DataFrame()

    @staticmethod
    def validate_price_data(df: pd.DataFrame) -> pd.DataFrame:

        if df.empty:
            logger.warning("Input dataframe is empty")
            return pd.DataFrame()

        df = df.copy()

        missing = [
            col for col in FeaturePipeline.REQUIRED_COLUMNS
            if col not in df.columns
        ]

        if missing:
            logger.error("Missing price columns: %s", missing)
            return pd.DataFrame()

        df.drop_duplicates(
            subset=["symbol", "trade_date"],
            inplace=True,
        )

        numeric_columns = [
            "open_price", "high_price", "low_price", "close_price", "volume"
        ]

        for col in numeric_columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")

        df.dropna(subset=FeaturePipeline.REQUIRED_COLUMNS, inplace=True)

        # Remove physically impossible candles
        df = df[
            (df["open_price"] > 0)
            & (df["high_price"] > 0)
            & (df["low_price"] > 0)
            & (df["close_price"] > 0)
            & (df["volume"] >= 0)
            & (df["high_price"] >= df["low_price"])
            & (df["high_price"] >= df["open_price"])
            & (df["high_price"] >= df["close_price"])
            & (df["low_price"] <= df["open_price"])
            & (df["low_price"] <= df["close_price"])
        ]

        df.sort_values(by=["symbol", "trade_date"], inplace=True)
        df.reset_index(drop=True, inplace=True)

        logger.info("Price data validated: %d rows retained", len(df))

        return df

    @staticmethod
    def process_symbol(symbol_df: pd.DataFrame) -> pd.DataFrame:
        """
        Runs all feature calculators for a single symbol's price history.
        Order matters: StageFeature reads EMA columns produced by EMAFeature.
        """

        if symbol_df.empty:
            return pd.DataFrame()

        try:
            # 1. EMAs + trend_score
            symbol_df = EMAFeature.calculate(symbol_df)

            # 2. Weinstein stage classification (depends on ema_50/150/200)
            symbol_df = StageFeature.calculate(symbol_df)

            # 3. Volume averages + relative volume
            symbol_df = VolumeFeature.calculate(symbol_df)

            # 4. ATR, daily range %, rolling volatility + contraction ratio
            symbol_df = VolatilityFeature.calculate(symbol_df)

            return symbol_df

        except Exception as exc:
            symbol = symbol_df["symbol"].iloc[0] if not symbol_df.empty else "UNKNOWN"
            logger.error(
                "Feature processing failed for %s: %s", symbol, exc, exc_info=True
            )
            return pd.DataFrame()

    @staticmethod
    def _clear_features_table() -> None:
        """
        TRUNCATE stock_features if it exists — preserves the user's primary key,
        unique constraint, and created_at default.
        If the table does not yet exist, to_sql will create it on the next save.

        Run the provided migration SQL once to add any new columns before
        switching from a fresh install to this approach.
        """

        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "IF OBJECT_ID('dbo.stock_features', 'U') IS NOT NULL "
                        "TRUNCATE TABLE stock_features"
                    )
                )
            logger.info("stock_features cleared")

        except Exception as exc:
            logger.error("Failed to clear stock_features: %s", exc, exc_info=True)

    @staticmethod
    def save_features(df: pd.DataFrame) -> None:

        if df.empty:
            logger.warning("No features to save")
            return

        # Keep only declared feature columns that are present in the dataframe
        available = [
            col for col in FeaturePipeline.FEATURE_COLUMNS
            if col in df.columns
        ]

        final_df = df[available].copy()

        final_df.drop_duplicates(
            subset=["symbol", "trade_date"],
            inplace=True,
        )

        try:
            final_df.to_sql(
                "stock_features",
                engine,
                if_exists="append",
                index=False,
                method="multi",
                chunksize=StrategyConfig.SQL_BATCH_SIZE,
            )
            logger.info("Saved %d feature rows to stock_features", len(final_df))

        except Exception as exc:
            logger.error("Feature insert failed: %s", exc, exc_info=True)

    @staticmethod
    def run() -> None:

        logger.info("Feature pipeline started")

        raw_df = FeaturePipeline.load_price_data()

        if raw_df.empty:
            logger.warning("No price data available — feature pipeline aborted")
            return

        validated_df = FeaturePipeline.validate_price_data(raw_df)

        if validated_df.empty:
            logger.warning("No valid price data after validation — aborting")
            return

        # Truncate before repopulating (preserves user's schema + constraints)
        FeaturePipeline._clear_features_table()

        all_feature_frames = []

        n_symbols = validated_df["symbol"].nunique()
        logger.info("Processing features for %d symbols", n_symbols)

        for symbol, symbol_df in tqdm(
            validated_df.groupby("symbol", sort=False),
            desc="Features",
            unit="sym",
            ncols=90,
            file=sys.stderr,
            leave=True,
            total=n_symbols,
        ):
            symbol_df = symbol_df.copy()
            feature_df = FeaturePipeline.process_symbol(symbol_df)

            if feature_df.empty:
                logger.debug("No features generated for %s", symbol)
                continue

            all_feature_frames.append(feature_df)

        if not all_feature_frames:
            logger.warning("No feature frames generated across all symbols")
            return

        final_feature_df = pd.concat(all_feature_frames, ignore_index=True)

        logger.info("Total feature rows: %d", len(final_feature_df))

        FeaturePipeline.save_features(final_feature_df)

        logger.info("Feature pipeline completed")


if __name__ == "__main__":
    FeaturePipeline.run()
