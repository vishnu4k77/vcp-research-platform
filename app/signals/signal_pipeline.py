"""Signal pipeline — loads features, runs all detectors, applies regime gate."""

import sys
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf
from sqlalchemy import text
from tqdm import tqdm

from app.config.db import engine
from app.config.logging_config import get_logger
from app.config.strategy_config import StrategyConfig
from app.services.market_query_service import MarketQueryService

from app.signals.trend_signal import TrendSignal
from app.signals.stage_signal import StageSignal
from app.signals.vcp_signal import VCPSignal
from app.signals.liquidity_signal import LiquiditySignal
from app.signals.quality_signal import QualitySignal, _load_latest_quality_scores, _load_quality_pass_score
from app.signals.breakout_signal import BreakoutSignal
from app.signals.rs_signal import RSSignal
from app.signals.minervini_signal import MinerviniSignal
from app.signals.darvas_signal import DarvasSignal
from app.signals.composite_ranker import CompositeRanker

logger = get_logger(__name__)


class SignalPipeline:
    """Consumes stock_features rows, applies all signal detectors, persists to stock_signals.

    Signal execution order matters — later signals consume outputs from earlier ones:
      1.  TrendSignal     → trend_signal
      2.  StageSignal     → stage2_signal      (reads EMA columns)
      3.  _stage2_age     → stage2_days/date   (reads stage2_signal)
      4.  VCPSignal       → vcp_signal         (reads stage2_signal; pre-earnings guard)
      5.  LiquiditySignal → liquidity_signal
      6.  QualitySignal   → quality_signal     (reads stage2_signal, trend_score)
      7.  BreakoutSignal  → breakout_signal    (reads high_52w from features)
      8.  RSSignal        → rs_signal, rs_value, rs_new_high  (reads Nifty series)
      9.  MinerviniSignal → minervini_signal   (reads rs_signal — must follow RSSignal)
      10. DarvasSignal    → darvas_signal      (reads high_52w, ema_150)
      11. CompositeRanker → composite_score, composite_rank, institutional_candidate
    """

    REQUIRED_COLUMNS: list[str] = [
        "symbol",
        "trade_date",
        "open_price",
        "high_price",
        "low_price",
        "close_price",
        "ema_10",
        "ema_21",
        "ema_50",
        "ema_150",
        "ema_200",
        "avg_volume_10",   # used by BreakoutSignal base volume dry-up guard
        "avg_volume_20",
        "relative_volume",
        "atr_14",
        "volatility_10",
        "volatility_contraction",
        "trend_score",
        "high_52w",        # pre-computed 52-week high (consumed by BreakoutSignal)
        "low_52w",         # pre-computed 52-week low (consumed by BreakoutSignal prior-uptrend guard)
        "is_earnings_day", # 1 on earnings release day — suppresses breakout signal
    ]

    # Columns persisted to stock_signals — must match what the signal chain produces.
    OUTPUT_COLUMNS: list[str] = [
        "symbol",
        "trade_date",
        # core binary signals
        "trend_signal",
        "stage2_signal",
        "vcp_signal",
        "breakout_signal",
        "liquidity_signal",
        "quality_signal",
        # sub-signals (transparency + UI drill-down)
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
        # Stage 2 age — consecutive trading days in current Stage 2 run
        "stage2_days",
        "stage2_started_date",
        # Phase 2A additions — RS value + new strategy signals
        "rs_value",         # excess return vs Nifty 50 (float, %)
        "rs_new_high",      # rs_value at 52w rolling max (BIT)
        "minervini_signal", # all 8 Trend Template conditions (BIT)
        "darvas_signal",    # Darvas box breakout near 52w high (BIT)
    ]

    # ── Data loading ──────────────────────────────────────────────────────────

    @staticmethod
    def load_feature_data() -> pd.DataFrame:
        """Load all rows from stock_features required by the signal chain.

        Returns:
            DataFrame sorted by symbol, trade_date ascending.
            Empty DataFrame on error.
        """
        query = text("""
            SELECT
                symbol,
                trade_date,
                open_price,
                high_price,
                low_price,
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
                weinstein_stage,
                high_52w,
                low_52w,
                ISNULL(is_earnings_day, 0) AS is_earnings_day
            FROM stock_features
            ORDER BY symbol, trade_date ASC
        """)

        try:
            with engine.connect() as conn:
                df = pd.read_sql(query, conn)
            logger.info("Loaded %d feature rows from stock_features", len(df))
            return df

        except Exception as exc:
            logger.error("Feature load failed: %s", exc, exc_info=True)
            return pd.DataFrame()

    @staticmethod
    def _load_nifty_returns() -> Optional[pd.Series]:
        """Download 5y Nifty 50 data and return RS_LOOKBACK-day returns indexed by date.

        Uses StrategyConfig.NIFTY_TICKER and StrategyConfig.RS_LOOKBACK — no magic strings.

        Returns:
            pd.Series indexed by datetime.date, values = RS_LOOKBACK-day pct_change.
            None if download fails or data is insufficient.
        """
        try:
            raw = yf.download(
                tickers=StrategyConfig.NIFTY_TICKER,
                period="1y",   # RS_LOOKBACK=63 days; 1y gives ample buffer, not 5y
                progress=False,
                auto_adjust=True,
            )

            if raw.empty:
                logger.warning("Nifty download returned empty DataFrame — rs_signal will be 0")
                return None

            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)

            raw.reset_index(inplace=True)

            date_col = next(
                (c for c in ("Date", "Datetime", "index") if c in raw.columns),
                None,
            )

            if date_col is None or "Close" not in raw.columns:
                logger.error("Unexpected Nifty data structure: %s", raw.columns.tolist())
                return None

            nifty = raw[[date_col, "Close"]].copy()
            nifty.columns = ["trade_date", "close"]
            nifty["close"] = pd.to_numeric(nifty["close"], errors="coerce")
            nifty.dropna(subset=["close"], inplace=True)
            nifty.sort_values("trade_date", inplace=True)
            nifty["trade_date"] = pd.to_datetime(nifty["trade_date"]).dt.date

            nifty["nifty_return"] = nifty["close"].pct_change(StrategyConfig.RS_LOOKBACK)

            series = nifty.set_index("trade_date")["nifty_return"]
            logger.info(
                "Nifty returns loaded | %d rows | RS_LOOKBACK=%d bars",
                len(series),
                StrategyConfig.RS_LOOKBACK,
            )
            return series

        except Exception as exc:
            logger.error("Nifty RS download failed: %s", exc, exc_info=True)
            return None

    # ── Validation ────────────────────────────────────────────────────────────

    @staticmethod
    def validate_feature_data(df: pd.DataFrame) -> pd.DataFrame:
        """Validate and clean feature data before signal computation.

        Args:
            df: Raw DataFrame from load_feature_data().

        Returns:
            Validated DataFrame, or empty DataFrame if unrecoverable.
        """
        if df.empty:
            logger.warning("Feature dataframe is empty")
            return pd.DataFrame()

        df = df.copy()

        missing = [c for c in SignalPipeline.REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            logger.error("Signal pipeline missing feature columns: %s", missing)
            return pd.DataFrame()

        df.drop_duplicates(subset=["symbol", "trade_date"], inplace=True)

        numeric_cols = [
            c for c in SignalPipeline.REQUIRED_COLUMNS
            if c not in ("symbol", "trade_date")
        ]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df.dropna(subset=["symbol", "trade_date", "close_price"], inplace=True)
        df.sort_values(by=["symbol", "trade_date"], inplace=True)
        df.reset_index(drop=True, inplace=True)

        return df

    # ── Stage 2 age computation ───────────────────────────────────────────────

    @staticmethod
    def _compute_stage2_age(df: pd.DataFrame) -> pd.DataFrame:
        """Add stage2_days and stage2_started_date to a per-symbol signal DataFrame.

        stage2_days:         Consecutive trading days in the current Stage 2 run
                             (1-indexed). 0 when stage2_signal = 0.
        stage2_started_date: trade_date on which the current Stage 2 streak began.
                             NULL when stage2_signal = 0.

        Fully vectorised — no Python loops. Handles multiple separate Stage 2 runs
        in a symbol's history (each new entry into Stage 2 resets the counter).

        Algorithm:
          1. Mark the first day of each Stage 2 run (0→1 transition).
          2. cumsum() of those markers gives a unique run_id per streak.
          3. cumcount() within each run_id gives 0-based position → +1 = day count.
          4. transform("first") over trade_date within each run gives start date.

        Args:
            df: Per-symbol DataFrame sorted ascending by trade_date. Must contain
                stage2_signal (int 0/1) and trade_date columns.

        Returns:
            df copy with stage2_days (int) and stage2_started_date (date/NaT) added.
        """
        df = df.copy()

        if "stage2_signal" not in df.columns or df.empty:
            df["stage2_days"]         = 0
            df["stage2_started_date"] = pd.NaT
            return df

        s2 = (df["stage2_signal"] == 1)

        if not s2.any():
            df["stage2_days"]         = 0
            df["stage2_started_date"] = pd.NaT
            return df

        # run_id: increments on every 0→1 transition into Stage 2.
        # Non-Stage-2 rows share the run_id of the preceding Stage 2 run
        # (or 0 before any Stage 2), but are masked out below.
        is_new_run = s2 & ~s2.shift(1, fill_value=False)
        run_id     = is_new_run.cumsum()

        # Day count: 1 on the entry day, 2 on the next, etc.
        days_in_run = df.groupby(run_id).cumcount() + 1
        df["stage2_days"] = days_in_run.where(s2, 0).astype(int)

        # Start date: first trade_date in each Stage 2 run
        df["stage2_started_date"] = pd.NaT
        s2_idx      = df.index[s2]
        start_dates = (
            df.loc[s2_idx]
            .groupby(run_id[s2])["trade_date"]
            .transform("first")
        )
        df.loc[s2_idx, "stage2_started_date"] = start_dates

        return df

    # ── Per-symbol signal computation ─────────────────────────────────────────

    @staticmethod
    def process_symbol(
        symbol_df: pd.DataFrame,
        nifty_returns: Optional[pd.Series] = None,
        fundamental_scores: Optional[dict] = None,
        quality_pass_score: Optional[int] = None,
    ) -> pd.DataFrame:
        """Run the full signal chain for a single symbol.

        Args:
            symbol_df: Feature rows for one symbol, sorted ascending by trade_date.
            nifty_returns: Series of Nifty 50 RS_LOOKBACK-day returns indexed by
                           datetime.date. Passed through to RSSignal.calculate().
            fundamental_scores: Pre-loaded dict of bare symbol → quality_score.
                                 Avoids one DB query per symbol when batching.
            quality_pass_score: Pre-loaded quality pass threshold. Same rationale.

        Returns:
            symbol_df with all signal columns appended.
            Empty DataFrame on error.
        """
        if symbol_df.empty:
            return pd.DataFrame()

        try:
            symbol_df = TrendSignal.calculate(symbol_df)
            symbol_df = StageSignal.calculate(symbol_df)
            symbol_df = SignalPipeline._compute_stage2_age(symbol_df)
            symbol_df = VCPSignal.calculate(symbol_df)
            symbol_df = LiquiditySignal.calculate(symbol_df)
            symbol_df = QualitySignal.calculate(symbol_df, fundamental_scores, quality_pass_score)
            symbol_df = BreakoutSignal.calculate(symbol_df)
            symbol_df = RSSignal.calculate(symbol_df, nifty_returns)
            # MinerviniSignal must follow RSSignal — uses rs_signal (condition 8)
            symbol_df = MinerviniSignal.calculate(symbol_df)
            symbol_df = DarvasSignal.calculate(symbol_df)
            symbol_df = CompositeRanker.calculate(symbol_df)
            return symbol_df

        except Exception as exc:
            symbol = symbol_df["symbol"].iloc[0] if not symbol_df.empty else "UNKNOWN"
            logger.error("Signal generation failed for %s: %s", symbol, exc, exc_info=True)
            return pd.DataFrame()

    # ── Regime gate ───────────────────────────────────────────────────────────

    @staticmethod
    def _apply_regime_gate(
        df: pd.DataFrame,
        allow_breakouts: bool,
        cash_mode: bool,
    ) -> pd.DataFrame:
        """Suppress signals on the latest trade_date based on the current regime.

        Only the latest date's rows are affected — historical signals are
        preserved intact for backtesting accuracy.

        When allow_breakouts=False:  breakout_signal, vcp_signal, breakout_ready_signal → 0
        When cash_mode=True:         all directional signals → 0 (fully defensive)

        After zeroing signals, composite_score and institutional_candidate are
        recomputed for affected rows to stay consistent.

        Args:
            df: All signal rows across all symbols and dates.
            allow_breakouts: From MarketQueryService.get_regime_environment().
            cash_mode: From MarketQueryService.get_regime_environment().

        Returns:
            df with gate applied to latest date rows.
        """
        if df.empty or (allow_breakouts and not cash_mode):
            return df

        latest_date = df["trade_date"].max()
        mask = df["trade_date"] == latest_date

        if not allow_breakouts:
            for col in ("breakout_signal", "vcp_signal", "breakout_ready_signal"):
                if col in df.columns:
                    df.loc[mask, col] = 0
            logger.info(
                "Regime gate applied: breakout/VCP suppressed for %s", latest_date
            )

        if cash_mode:
            directional = [
                "trend_signal", "stage2_signal", "vcp_signal",
                "breakout_signal", "breakout_ready_signal",
            ]
            for col in directional:
                if col in df.columns:
                    df.loc[mask, col] = 0
            logger.info(
                "Regime gate applied: cash mode — all directional signals suppressed for %s",
                latest_date,
            )

        # Recompute composite_score for affected rows so it reflects zeroed signals
        weights = StrategyConfig.SIGNAL_WEIGHTS
        total_weight = sum(weights.values())

        score = sum(
            df.loc[mask, signal].clip(0, 1) * weight
            for signal, weight in weights.items()
            if signal in df.columns
        )

        df.loc[mask, "composite_score"] = (score / total_weight * 100).round(2)
        df.loc[mask, "institutional_candidate"] = np.where(
            df.loc[mask, "composite_score"] >= StrategyConfig.MIN_COMPOSITE_SCORE,
            1,
            0,
        )

        return df

    # ── Persistence ───────────────────────────────────────────────────────────

    @staticmethod
    def _clear_signals_table() -> None:
        """TRUNCATE stock_signals, preserving schema and constraints.

        Safe to call on a fresh install (IF OBJECT_ID guard).
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
        """Persist signal rows to stock_signals.

        Only columns listed in OUTPUT_COLUMNS are written. Missing output
        columns are logged as warnings but do not abort the save.

        Args:
            df: Final signal DataFrame (all symbols, all dates).
        """
        if df.empty:
            logger.warning("No signals to save")
            return

        available = [c for c in SignalPipeline.OUTPUT_COLUMNS if c in df.columns]
        missing_output = [c for c in SignalPipeline.OUTPUT_COLUMNS if c not in df.columns]

        if missing_output:
            logger.warning("Signal output missing columns (skipped): %s", missing_output)

        final_df = df[available].copy()
        final_df.drop_duplicates(subset=["symbol", "trade_date"], inplace=True)

        try:
            final_df.to_sql(
                "stock_signals",
                engine,
                if_exists="append",
                index=False,
                method="multi",
                chunksize=StrategyConfig.SQL_BATCH_SIZE,
            )
            logger.info("Saved %d signal rows to stock_signals", len(final_df))

        except Exception as exc:
            logger.error("Signal insert failed: %s", exc, exc_info=True)

    # ── Entry point ───────────────────────────────────────────────────────────

    @staticmethod
    def run(market_status: Optional[str] = None) -> None:
        """Execute the full signal pipeline.

        Args:
            market_status: Current regime state_code from market_states table
                           (e.g. "BEAR", "BULL"). When provided, the regime gate
                           suppresses breakout/VCP signals on the latest date if
                           allow_breakouts=False, or all directional signals if
                           cash_mode=True. Pass None to skip the regime gate.
        """
        logger.info(
            "Signal pipeline started | regime=%s",
            market_status or "unknown (gate disabled)",
        )

        # Load Nifty 50 returns once — shared across all per-symbol calls
        nifty_returns = SignalPipeline._load_nifty_returns()

        raw_df = SignalPipeline.load_feature_data()
        if raw_df.empty:
            logger.warning("No feature data — signal pipeline aborted")
            return

        validated_df = SignalPipeline.validate_feature_data(raw_df)
        if validated_df.empty:
            logger.warning("No valid feature data after validation — aborting")
            return

        logger.info("Validated feature rows: %d", len(validated_df))

        # Truncate before repopulating (preserves schema + constraints)
        SignalPipeline._clear_signals_table()

        # Load quality data once — avoids 2 DB round-trips × N symbols in the loop
        fundamental_scores = _load_latest_quality_scores()
        quality_pass_score = _load_quality_pass_score()

        n_symbols = validated_df["symbol"].nunique()
        logger.info("Processing signals for %d symbols", n_symbols)

        all_signal_frames: list[pd.DataFrame] = []

        for symbol, symbol_df in tqdm(
            validated_df.groupby("symbol", sort=False),
            desc="Signals ",
            unit="sym",
            ncols=90,
            file=sys.stderr,
            leave=True,
            total=n_symbols,
        ):
            symbol_df = symbol_df.copy()
            signal_df = SignalPipeline.process_symbol(symbol_df, nifty_returns, fundamental_scores, quality_pass_score)

            if signal_df.empty:
                logger.debug("No signals generated for %s", symbol)
                continue

            all_signal_frames.append(signal_df)

        if not all_signal_frames:
            logger.warning("No signal frames generated across all symbols")
            return

        final_df = pd.concat(all_signal_frames, ignore_index=True)
        logger.info("Total signal rows: %d", len(final_df))

        # Apply regime gate to latest date only (historical rows untouched)
        if market_status is not None:
            env = MarketQueryService.get_regime_environment(market_status)
            final_df = SignalPipeline._apply_regime_gate(
                final_df,
                allow_breakouts=env["allow_breakouts"],
                cash_mode=env["cash_mode"],
            )

        SignalPipeline.save_signals(final_df)
        logger.info("Signal pipeline completed")


if __name__ == "__main__":
    SignalPipeline.run()
