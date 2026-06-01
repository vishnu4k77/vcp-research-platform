"""run_backtest.py — CLI entry point for signal backtesting.

Pulls entry signals from stock_signals, simulates stop/target/timeout exits
using daily_price_data, and prints a performance summary. Optionally writes
the trade log to a CSV file.

Usage
-----
    python scripts/run_backtest.py
    python scripts/run_backtest.py --signal vcp_signal --stop 8 --target 25
    python scripts/run_backtest.py --start 2022-01-01 --end 2024-12-31 --out trades.csv
    python scripts/run_backtest.py --help

Exit codes
----------
    0   backtest ran successfully (even if no trades were found)
    1   fatal error (DB unavailable, invalid arguments, etc.)
"""

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

# Ensure project root is importable regardless of CWD
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.backtesting.backtest_engine import BacktestEngine
from app.backtesting.backtest_service import BacktestService
from app.config.logging_config import get_logger
from app.config.strategy_config import BacktestConfig

logger = get_logger("run_backtest")


def _parse_date(value: str) -> date:
    """Parse an ISO date string (YYYY-MM-DD) into a date object.

    Args:
        value: Date string in YYYY-MM-DD format.

    Returns:
        Parsed date object.

    Raises:
        argparse.ArgumentTypeError: If the format is invalid.
    """
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date format: '{value}'. Expected YYYY-MM-DD.")


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser.

    Returns:
        Configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        description="NSE VCP Breakout Scanner — Signal Backtester",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/run_backtest.py\n"
            "  python scripts/run_backtest.py --signal vcp_signal --stop 8 --target 25\n"
            "  python scripts/run_backtest.py --start 2022-01-01 --end 2024-12-31\n"
            "  python scripts/run_backtest.py --out trades.csv\n"
        ),
    )

    parser.add_argument(
        "--signal",
        choices=BacktestConfig.ENTRY_SIGNAL_OPTIONS,
        default=BacktestConfig.DEFAULT_ENTRY_SIGNAL,
        help=f"Entry signal column. Default: {BacktestConfig.DEFAULT_ENTRY_SIGNAL}",
    )
    parser.add_argument(
        "--start",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Backtest start date. Default: 3 years before end date.",
    )
    parser.add_argument(
        "--end",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help="Backtest end date. Default: latest date in stock_signals.",
    )
    parser.add_argument(
        "--stop",
        type=float,
        default=BacktestConfig.STOP_LOSS_PCT * 100,
        metavar="PCT",
        help=f"Stop-loss %% below entry. Default: {BacktestConfig.STOP_LOSS_PCT * 100:.0f}",
    )
    parser.add_argument(
        "--target",
        type=float,
        default=BacktestConfig.TARGET_PCT * 100,
        metavar="PCT",
        help=f"Target %% above entry. Default: {BacktestConfig.TARGET_PCT * 100:.0f}",
    )
    parser.add_argument(
        "--hold",
        type=int,
        default=BacktestConfig.MAX_HOLDING_DAYS,
        metavar="DAYS",
        help=f"Max holding calendar days. Default: {BacktestConfig.MAX_HOLDING_DAYS}",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=BacktestConfig.MIN_COMPOSITE_SCORE,
        metavar="SCORE",
        help=f"Min composite_score threshold. Default: {BacktestConfig.MIN_COMPOSITE_SCORE}",
    )
    parser.add_argument(
        "--min-stage2-days",
        type=int,
        default=None,
        metavar="DAYS",
        help="Only enter trades where stage2_days >= this (e.g. 1 = must be in Stage 2).",
    )
    parser.add_argument(
        "--max-stage2-days",
        type=int,
        default=None,
        metavar="DAYS",
        help="Only enter trades where stage2_days <= this (e.g. 20 = Early Stage 2 only).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="PATH",
        help="Optional CSV file path for the trade log (e.g. trades.csv).",
    )

    return parser


def _print_summary(metrics: dict, params: dict) -> None:
    """Print a formatted performance summary to stdout.

    Args:
        metrics: Dict produced by performance_metrics.compute().
        params: Dict of backtest run parameters.
    """
    sep = "-" * 60

    print(f"\n{sep}")
    print("  NSE VCP Scanner - Backtest Summary")
    print(sep)
    print(f"  Signal      : {params['entry_signal']}")
    print(f"  Period      : {params['start_date']} to {params['end_date']}")
    print(f"  Stop / Target / Hold  : "
          f"{float(params['stop_loss_pct'])*100:.0f}% / "
          f"{float(params['target_pct'])*100:.0f}% / "
          f"{params['max_holding_days']}d")
    print(f"  Min Score   : {params['min_composite_score']}")
    print(sep)

    if not metrics:
        print("  No trades found for the given parameters.")
        print(sep)
        return

    wr  = metrics["win_rate"]
    pf  = metrics["profit_factor"]
    pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"

    print(f"  Total trades    : {metrics['total_trades']}")
    print(f"  Win rate        : {wr:.1f}%")
    print(f"  Avg return      : {metrics['avg_return_pct']:+.2f}%")
    print(f"  Avg win         : {metrics['avg_win_pct']:+.2f}%")
    print(f"  Avg loss        : {metrics['avg_loss_pct']:+.2f}%")
    print(f"  Profit factor   : {pf_str}")
    print(f"  Max drawdown    : {metrics['max_drawdown_pct']:.1f}%")
    print(f"  Sharpe ratio    : {metrics['sharpe_ratio']:.2f}")
    print(f"  Total return    : {metrics['total_return_pct']:+.1f}%")
    print(f"  Best / Worst    : {metrics['best_trade_pct']:+.1f}% / {metrics['worst_trade_pct']:+.1f}%")
    print(f"  Avg holding     : {metrics['avg_holding_days']:.0f} days")
    print(sep)
    print(f"  Exits - Stop: {metrics['stop_count']}  "
          f"Target: {metrics['target_count']}  "
          f"Timeout: {metrics['timeout_count']}")

    if metrics.get("by_regime"):
        print(sep)
        print("  By Market Regime:")
        for regime, stats in metrics["by_regime"].items():
            print(
                f"    {regime:<15}  "
                f"trades={stats['count']}  "
                f"win={stats['win_rate']:.0f}%  "
                f"avg={stats['avg_return_pct']:+.2f}%"
            )

    print(sep)


def main() -> int:
    """CLI entry point.

    Returns:
        0 on success, 1 on fatal error.
    """
    parser = _build_parser()
    args   = parser.parse_args()

    # ── Resolve date range ────────────────────────────────────────────────────
    min_dt, max_dt = BacktestService.get_date_range()

    if min_dt is None or max_dt is None:
        logger.error("stock_signals is empty — run `python run.py` first")
        print("ERROR: No signal data found. Run `python run.py` to populate stock_signals.")
        return 1

    end_date   = args.end   if args.end   is not None else max_dt
    start_date = args.start if args.start is not None else date(end_date.year - 3, end_date.month, end_date.day)

    # Clamp to available range
    start_date = max(start_date, min_dt)
    end_date   = min(end_date, max_dt)

    if start_date >= end_date:
        logger.error("start_date (%s) must be before end_date (%s)", start_date, end_date)
        print(f"ERROR: start_date ({start_date}) must be before end_date ({end_date}).")
        return 1

    # ── Run backtest ──────────────────────────────────────────────────────────
    result = BacktestEngine.run(
        start_date          = start_date,
        end_date            = end_date,
        entry_signal        = args.signal,
        stop_loss_pct       = args.stop   / 100.0,
        target_pct          = args.target / 100.0,
        max_holding_days    = args.hold,
        min_composite_score = args.min_score,
        min_stage2_days     = args.min_stage2_days,
        max_stage2_days     = args.max_stage2_days,
    )

    _print_summary(result.metrics, result.params)

    # ── Optional CSV export ───────────────────────────────────────────────────
    if args.out and not result.is_empty:
        try:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            result.trades.to_csv(args.out, index=False)
            print(f"\n  Trade log saved: {args.out.resolve()}")
        except Exception as exc:
            logger.error("CSV export failed: %s", exc, exc_info=True)
            print(f"WARNING: Could not save CSV: {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
