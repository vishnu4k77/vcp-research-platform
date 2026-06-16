"""Backtest simulation engine — entry extraction, forward price scan, trade recording.

Architecture
------------
BacktestEngine.run() is the single public entry point.

Simulation model (per trade):
  1. Entry:  close_price on signal date (no lookahead — signal fires EOD).
  2. Forward scan day-by-day:
       a. Stop hit (low_price ≤ stop_price) — exit at stop_price (conservative).
       b. Target hit (high_price ≥ target_price) — exit at target_price.
       c. Timeout (max_holding_days reached) — exit at close_price.
     Within the same bar, stop is checked before target (worst-case assumption).
  3. No future data → trade is skipped (open position at end of data window).

Bulk price loading: all required symbols' forward prices are fetched in one SQL
call, grouped into a dict keyed by symbol, so the inner simulation loop runs
at O(1) lookup cost per trade rather than one DB round-trip per trade.

All thresholds come from BacktestConfig — zero magic numbers here.
"""

from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from app.backtesting import backtest_service as _svc
from app.backtesting.backtest_service import BacktestService
from app.backtesting.performance_metrics import compute as compute_metrics
from app.config.logging_config import get_logger
from app.config.strategy_config import BacktestConfig

logger = get_logger(__name__)


class BacktestResult:
    """Container returned by BacktestEngine.run().

    Attributes:
        trades:  DataFrame with one row per completed trade.
        metrics: Dict produced by performance_metrics.compute().
        params:  Dict of the run parameters for traceability.
    """

    def __init__(
        self,
        trades: pd.DataFrame,
        metrics: dict,
        params: dict,
    ) -> None:
        """Initialise a BacktestResult.

        Args:
            trades: Trade-level result DataFrame.
            metrics: Aggregate performance metrics dict.
            params: Run parameters (signal, dates, stop%, target%, etc.).
        """
        self.trades  = trades
        self.metrics = metrics
        self.params  = params

    @property
    def is_empty(self) -> bool:
        """True if no trades were simulated."""
        return self.trades.empty


class BacktestEngine:
    """Signal backtesting engine — vectorised over entries, sequential per trade.

    Usage::

        result = BacktestEngine.run(
            start_date=date(2022, 1, 1),
            end_date=date(2025, 12, 31),
            entry_signal="breakout_signal",
        )
        print(result.metrics)
        result.trades.to_csv("trades.csv", index=False)
    """

    @staticmethod
    def run(
        start_date: date,
        end_date: date,
        entry_signal: str                   = BacktestConfig.DEFAULT_ENTRY_SIGNAL,
        stop_loss_pct: float                = BacktestConfig.STOP_LOSS_PCT,
        target_pct: float                   = BacktestConfig.TARGET_PCT,
        max_holding_days: int               = BacktestConfig.MAX_HOLDING_DAYS,
        min_composite_score: float          = BacktestConfig.MIN_COMPOSITE_SCORE,
        min_stage2_days: Optional[int]      = None,
        max_stage2_days: Optional[int]      = None,
        min_mtf_score: Optional[int]        = None,
    ) -> BacktestResult:
        """Run the full backtest simulation.

        Args:
            start_date: First entry date to consider (inclusive).
            end_date: Last entry date to consider (inclusive).
            entry_signal: Column in stock_signals to use as entry trigger.
            stop_loss_pct: Fraction below entry price that triggers stop exit.
            target_pct: Fraction above entry price that triggers target exit.
            max_holding_days: Maximum calendar days to hold before forced exit.
            min_composite_score: Minimum composite_score to accept an entry.
            min_stage2_days: Lower bound on stage2_days (None = no filter).
                             Used to restrict to Early / Mid / Advanced Stage 2.
            max_stage2_days: Upper bound on stage2_days (None = no filter).
            min_mtf_score:  Minimum MTF score gate (None = no filter).
                             1 = at least one timeframe (weekly OR monthly) aligned.
                             2 = full macro confirmation (weekly AND monthly aligned).
                             Requires stock_mtf_signals populated by run_mtf_pipeline.py.

        Returns:
            BacktestResult containing trades DataFrame, metrics dict, and params.
        """
        params = {
            "start_date":          str(start_date),
            "end_date":            str(end_date),
            "entry_signal":        entry_signal,
            "stop_loss_pct":       stop_loss_pct,
            "target_pct":          target_pct,
            "max_holding_days":    max_holding_days,
            "min_composite_score": min_composite_score,
            "min_stage2_days":     min_stage2_days,
            "max_stage2_days":     max_stage2_days,
            "min_mtf_score":       min_mtf_score,
        }

        logger.info(
            "Backtest started | signal=%s | %s→%s | stop=%.0f%% | target=%.0f%% "
            "| hold=%dd | s2d=[%s,%s] | mtf>=%s",
            entry_signal, start_date, end_date,
            stop_loss_pct * 100, target_pct * 100, max_holding_days,
            min_stage2_days or "—", max_stage2_days or "—",
            min_mtf_score if min_mtf_score is not None else "—",
        )

        # ── 1. Load entry signals ─────────────────────────────────────────────
        entries = BacktestService.get_signal_entries(
            start_date=start_date,
            end_date=end_date,
            entry_signal=entry_signal,
            min_composite_score=min_composite_score,
            min_stage2_days=min_stage2_days,
            max_stage2_days=max_stage2_days,
            min_mtf_score=min_mtf_score,
        )

        if entries.empty:
            logger.warning("No entry signals found for the given parameters")
            return BacktestResult(pd.DataFrame(), {}, params)

        # ── 2. Bulk-load forward price data ───────────────────────────────────
        # Load from the FIRST entry date (not backtest start_date) to reduce
        # data volume: earlier dates are never used as forward-scan targets.
        symbols        = entries["symbol"].unique().tolist()
        entries["entry_date"] = pd.to_datetime(entries["entry_date"])
        first_entry_date = entries["entry_date"].min().date()
        price_end      = end_date + timedelta(days=BacktestConfig.FORWARD_PRICE_BUFFER_DAYS)
        price_data     = BacktestService.get_bulk_price_data(symbols, first_entry_date, price_end)

        if price_data.empty:
            logger.error("No price data returned — backtest cannot proceed")
            return BacktestResult(pd.DataFrame(), {}, params)

        # Keep as datetime64 (not Python date) — required for numpy searchsorted.
        price_data["trade_date"] = pd.to_datetime(price_data["trade_date"])

        # Pre-build per-symbol numpy arrays ONCE (avoids all DataFrame overhead
        # inside the simulation loop — dates must be sorted ascending for searchsorted).
        price_arrays: dict[str, dict[str, np.ndarray]] = {}
        for sym, grp in price_data.groupby("symbol"):
            g = grp.sort_values("trade_date")
            price_arrays[sym] = {
                "dates":  g["trade_date"].values,                # datetime64[ns]
                "highs":  g["high_price"].values.astype(float),
                "lows":   g["low_price"].values.astype(float),
                "closes": g["close_price"].values.astype(float),
            }

        # ── 3. Simulate — numpy-vectorised inner scan, no iterrows() ──────────
        # Extract all entry columns as numpy arrays once.  Using range(n) + array
        # indexing is 5-10× faster than iterrows() which boxes each row in a Series.
        entry_dates_ns  = entries["entry_date"].values           # datetime64[ns]
        entry_prices    = entries["entry_price"].values.astype(float)
        entry_symbols   = entries["symbol"].values
        entry_scores    = entries["composite_score"].values.astype(float)
        entry_regimes   = entries["regime"].values

        max_hold_ns = np.timedelta64(max_holding_days, "D")

        trade_records: list[dict] = []

        for i in range(len(entries)):
            symbol      = entry_symbols[i]
            entry_dt_ns = entry_dates_ns[i]      # numpy datetime64 — no Timestamp overhead
            entry_price = entry_prices[i]

            if entry_price <= 0 or np.isnan(entry_price):
                continue

            arr = price_arrays.get(symbol)
            if arr is None:
                continue

            dates  = arr["dates"]
            highs  = arr["highs"]
            lows   = arr["lows"]
            closes = arr["closes"]

            # Binary search for the forward window — O(log n), no boolean mask.
            # "right" gives the first index strictly AFTER entry_dt_ns.
            start_idx = int(np.searchsorted(dates, entry_dt_ns, side="right"))
            end_idx   = int(np.searchsorted(dates, entry_dt_ns + max_hold_ns, side="right"))

            if start_idx >= len(dates) or start_idx >= end_idx:
                continue  # No forward bars within the holding window

            fwd_dates  = dates[start_idx:end_idx]
            fwd_highs  = highs[start_idx:end_idx]
            fwd_lows   = lows[start_idx:end_idx]
            fwd_closes = closes[start_idx:end_idx]

            stop_price   = entry_price * (1.0 - stop_loss_pct)
            target_price = entry_price * (1.0 + target_pct)

            # Vectorised stop / target detection — C-level numpy ops, no Python loop.
            stop_hit   = fwd_lows  <= stop_price    # bool array
            target_hit = fwd_highs >= target_price  # bool array

            any_stop   = bool(stop_hit.any())
            any_target = bool(target_hit.any())

            first_stop   = int(np.argmax(stop_hit))   if any_stop   else len(fwd_lows)
            first_target = int(np.argmax(target_hit)) if any_target else len(fwd_highs)

            # Stop wins on the same bar (conservative assumption)
            if any_stop and first_stop <= first_target:
                exit_idx    = first_stop
                exit_price  = stop_price
                exit_reason = "STOP"
            elif any_target:
                exit_idx    = first_target
                exit_price  = target_price
                exit_reason = "TARGET"
            else:
                exit_idx    = len(fwd_closes) - 1
                exit_price  = float(fwd_closes[-1])
                exit_reason = "TIMEOUT"

            exit_dt_ns   = fwd_dates[exit_idx]
            holding_days = int((exit_dt_ns - entry_dt_ns) / np.timedelta64(1, "D"))
            return_pct   = round((exit_price / entry_price - 1.0) * 100.0, 3)

            trade_records.append({
                "symbol":          symbol,
                "entry_date":      pd.Timestamp(entry_dt_ns).date(),
                "entry_price":     round(entry_price, 2),
                "exit_date":       pd.Timestamp(exit_dt_ns).date(),
                "exit_price":      round(exit_price, 2),
                "return_pct":      return_pct,
                "holding_days":    holding_days,
                "exit_reason":     exit_reason,
                "composite_score": round(float(entry_scores[i]), 1),
                "regime":          str(entry_regimes[i]),
            })

        if not trade_records:
            logger.warning("Simulation produced zero completed trades")
            return BacktestResult(pd.DataFrame(), {}, params)

        trades_df = pd.DataFrame(trade_records)
        trades_df.sort_values(["entry_date", "symbol"], inplace=True)
        trades_df.reset_index(drop=True, inplace=True)

        metrics = compute_metrics(trades_df)

        logger.info(
            "Backtest complete | trades=%d | win_rate=%.1f%% | avg_return=%.2f%% | "
            "max_drawdown=%.2f%% | sharpe=%.2f",
            metrics.get("total_trades", 0),
            metrics.get("win_rate", 0),
            metrics.get("avg_return_pct", 0),
            metrics.get("max_drawdown_pct", 0),
            metrics.get("sharpe_ratio", 0),
        )

        return BacktestResult(trades_df, metrics, params)
