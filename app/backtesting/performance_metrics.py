"""Performance metrics computation for backtest trade DataFrames.

All computation is pure pandas/numpy — no DB calls, no side effects.
The single public function compute() accepts a trades DataFrame and returns
a plain dict so callers (dashboard, CLI) can format the output as needed.
"""

from typing import Any

import numpy as np
import pandas as pd

from app.config.logging_config import get_logger
from app.config.strategy_config import BacktestConfig

logger = get_logger(__name__)


def compute(trades: pd.DataFrame) -> dict[str, Any]:
    """Compute aggregate performance metrics from a backtest trades DataFrame.

    Args:
        trades: DataFrame produced by BacktestEngine.run() with columns:
            symbol, entry_date, entry_price, exit_date, exit_price,
            return_pct, holding_days, exit_reason, composite_score, regime.

    Returns:
        Dict with keys:
            total_trades, win_rate, avg_return_pct, avg_win_pct, avg_loss_pct,
            profit_factor, max_drawdown_pct, sharpe_ratio, total_return_pct,
            stop_count, target_count, timeout_count,
            best_trade_pct, worst_trade_pct, avg_holding_days,
            by_regime (nested dict of regime → win_rate / avg_return).
        Empty dict if trades is empty.
    """
    if trades.empty:
        logger.warning("compute() called with empty trades DataFrame")
        return {}

    returns = pd.to_numeric(trades["return_pct"], errors="coerce").fillna(0.0)
    n = len(trades)

    wins   = trades[returns > 0]
    losses = trades[returns <= 0]

    win_rate    = len(wins) / n * 100.0
    avg_return  = float(returns.mean())
    avg_win     = float(returns[returns > 0].mean()) if len(wins)   > 0 else 0.0
    avg_loss    = float(returns[returns <= 0].mean()) if len(losses) > 0 else 0.0

    total_gain  = float(returns[returns > 0].sum())
    total_loss  = float(abs(returns[returns <= 0].sum()))
    profit_factor = round(total_gain / total_loss, 2) if total_loss > 0 else float("inf")

    # Cumulative return curve — sum (not compounded product).
    # Compounding per-trade returns assumes 100% of capital in every trade
    # simultaneously, which overflows with thousands of trades. Cumulative sum
    # is the correct model for signal analysis with equal per-trade position sizing:
    # it shows "total % gained if every signal was traded with the same fixed size".
    cum_returns     = returns.cumsum()                     # cumulative % from trade 1 to N
    total_return    = float(cum_returns.iloc[-1])          # sum of all per-trade returns

    # Max drawdown — normalised as percentage of peak portfolio value.
    # Convert cumulative % to portfolio-value equity (base 100) so the drawdown
    # is expressed as "% loss from peak" rather than raw cumulative percentage points.
    # Example: peak=10000%, trough=7878% → equity_base peak=10100, trough=7978
    #          drawdown = (7978 - 10100) / 10100 = -21.0%  (not -2122 pp)
    # Clamp equity_base at 0 — real capital cannot go below zero.
    # Without this, a cumulative loss streak that exceeds 100% produces equity_base < 0,
    # making the normalised drawdown formula yield values below -100% (impossible in practice).
    equity_base     = (100.0 + cum_returns).clip(lower=0.0)
    rolling_max     = equity_base.expanding().max()
    # Guard against division by zero when rolling_max is 0 (all trades lost from the start)
    safe_max        = rolling_max.replace(0.0, np.nan)
    drawdown_series = (equity_base - safe_max) / safe_max * 100.0
    max_drawdown    = float(max(drawdown_series.min(), -100.0))  # floor at -100%

    # Sharpe ratio: annualised using average holding period
    avg_holding = float(
        pd.to_numeric(trades.get("holding_days", pd.Series([1] * n)), errors="coerce").fillna(1).mean()
    )
    trades_per_year = BacktestConfig.TRADING_DAYS_PER_YEAR / max(avg_holding, 1)
    std_return  = float(returns.std())
    sharpe = (avg_return / std_return) * np.sqrt(trades_per_year) if std_return > 0 else 0.0

    # Exit reason counts
    reason_counts = trades["exit_reason"].value_counts().to_dict() if "exit_reason" in trades.columns else {}

    # Per-regime breakdown
    by_regime: dict[str, dict[str, Any]] = {}
    if "regime" in trades.columns:
        for regime, grp in trades.groupby("regime"):
            grp_ret = pd.to_numeric(grp["return_pct"], errors="coerce").fillna(0.0)
            by_regime[str(regime)] = {
                "count":          len(grp),
                "win_rate":       round(float((grp_ret > 0).mean() * 100), 1),
                "avg_return_pct": round(float(grp_ret.mean()), 2),
            }

    return {
        "total_trades":    n,
        "win_rate":        round(win_rate, 1),
        "avg_return_pct":  round(avg_return, 2),
        "avg_win_pct":     round(avg_win, 2),
        "avg_loss_pct":    round(avg_loss, 2),
        "profit_factor":   profit_factor,
        "max_drawdown_pct": round(max_drawdown, 2),
        "sharpe_ratio":    round(sharpe, 2),
        "total_return_pct": round(total_return, 2),
        "stop_count":      int(reason_counts.get("STOP",    0)),
        "target_count":    int(reason_counts.get("TARGET",  0)),
        "timeout_count":   int(reason_counts.get("TIMEOUT", 0)),
        "best_trade_pct":  round(float(returns.max()), 2),
        "worst_trade_pct": round(float(returns.min()), 2),
        "avg_holding_days": round(avg_holding, 1),
        "by_regime":       by_regime,
    }
