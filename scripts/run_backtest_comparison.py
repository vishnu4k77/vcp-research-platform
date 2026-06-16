"""run_backtest_comparison.py — side-by-side backtest of six signal strategies.

Runs all six strategies over the same date range and parameters, then prints a
detailed comparison table to prove whether MTF confirmation improves win rate.

All numbers come from real DB data — zero fabricated statistics.

Strategies compared
-------------------
    1. Composite (baseline)    — breakout_signal, no MTF filter
    2. Breakout MTF>=1         — breakout_signal, at least one TF aligned
    3. Breakout MTF=2          — breakout_signal, weekly AND monthly aligned
    4. VCP MTF=2               — vcp_signal, weekly AND monthly aligned
    5. Price Action            — pa_signal, no MTF filter
    6. Combined PA+EMA         — breakout_signal AND pa_signal, no MTF filter

Prerequisites
-------------
    python setup_db.py                    # creates all tables (stock_mtf_signals)
    python run.py                         # populate stock_signals
    python scripts/run_pa_pipeline.py     # populate stock_pa_signals
    python scripts/run_mtf_pipeline.py    # populate stock_mtf_signals

Usage
-----
    python scripts/run_backtest_comparison.py
    python scripts/run_backtest_comparison.py --start 2023-05-29 --end 2026-05-29
    python scripts/run_backtest_comparison.py --stop 7 --target 20 --hold 60
    python scripts/run_backtest_comparison.py --out comparison.csv

Exit codes
----------
    0   comparison ran (even if some strategies had zero trades)
    1   fatal error (no signal data, invalid args)
"""

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.backtesting.backtest_engine import BacktestEngine
from app.backtesting.backtest_service import BacktestService
from app.config.logging_config import get_logger
from app.config.strategy_config import BacktestConfig

logger = get_logger("run_backtest_comparison")

# Six strategies compared — in display order (name, signal, min_mtf_score)
_STRATEGIES: list[tuple[str, str, Optional[int]]] = [
    ("Composite",       "breakout_signal",  None),  # baseline — no MTF gate
    ("Breakout MTF>=1", "breakout_signal",  1),      # at least one TF aligned
    ("Breakout MTF=2",  "breakout_signal",  2),      # both TFs aligned
    ("VCP MTF=2",       "vcp_signal",       2),      # VCP-specific with full MTF
    ("Price Action",    "pa_signal",        None),   # PA standalone
    ("Combined PA+EMA", "combined_pa_ema",  None),   # PA + breakout_signal
]


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date format: '{value}'. Expected YYYY-MM-DD.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="NSE VCP Scanner — Strategy Comparison Backtest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Compares 6 strategies:\n"
            "  1. Composite       — breakout_signal, no MTF filter\n"
            "  2. Breakout MTF>=1 — breakout_signal, 1+ timeframe aligned\n"
            "  3. Breakout MTF=2  — breakout_signal, weekly AND monthly\n"
            "  4. VCP MTF=2       — vcp_signal, weekly AND monthly\n"
            "  5. Price Action    — pa_signal (run run_pa_pipeline.py first)\n"
            "  6. Combined PA+EMA — breakout_signal AND pa_signal\n"
            "\nRequires run_mtf_pipeline.py for strategies 2-4."
        ),
    )
    parser.add_argument("--start", type=_parse_date, default=None, metavar="YYYY-MM-DD")
    parser.add_argument("--end",   type=_parse_date, default=None, metavar="YYYY-MM-DD")
    parser.add_argument("--stop",   type=float, default=BacktestConfig.STOP_LOSS_PCT * 100, metavar="PCT")
    parser.add_argument("--target", type=float, default=BacktestConfig.TARGET_PCT * 100,    metavar="PCT")
    parser.add_argument("--hold",   type=int,   default=BacktestConfig.MAX_HOLDING_DAYS,    metavar="DAYS")
    parser.add_argument("--min-score", type=float, default=BacktestConfig.MIN_COMPOSITE_SCORE, metavar="SCORE")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="PATH",
        help="Optional CSV path. All three trade logs are saved as <PATH>_<strategy>.csv",
    )
    return parser


def _fmt_pf(pf: float) -> str:
    return "inf" if pf == float("inf") else f"{pf:.2f}"


def _print_comparison(results: list[tuple[str, dict]], params: dict) -> None:
    col_w   = 16
    label_w = 20
    total_w = label_w + col_w * len(results)
    sep  = "=" * total_w
    sep2 = "-" * total_w

    print(f"\n{sep}")
    print("  NSE VCP Scanner — Strategy Comparison Backtest")
    print(sep)
    print(f"  Period   : {params['start_date']} -> {params['end_date']}")
    print(f"  Stop / Target / Hold : "
          f"{float(params['stop_loss_pct'])*100:.0f}% / "
          f"{float(params['target_pct'])*100:.0f}% / "
          f"{params['max_holding_days']}d")
    print(f"  Min Score: {params['min_composite_score']}")
    print(sep)

    # ── Summary header ───────────────────────────────────────────────────────
    header = f"{'Metric':<{label_w}}" + "".join(f"{name:>{col_w}}" for name, _ in results)
    print(header)
    print(sep2)

    def _row(label: str, getter) -> str:
        vals = [getter(m) for _, m in results]
        return f"{label:<{label_w}}" + "".join(f"{v:>{col_w}}" for v in vals)

    def _g(m: dict, key: str, fmt: str = "") -> str:
        if not m:
            return "—"
        v = m.get(key, "—")
        if v == "—":
            return "—"
        return f"{v:{fmt}}" if fmt else str(v)

    print(_row("Trades",       lambda m: _g(m, "total_trades")))
    print(_row("Win rate",     lambda m: f"{_g(m, 'win_rate')}%" if m else "—"))
    print(_row("Avg return",   lambda m: f"{_g(m, 'avg_return_pct', '+.2f')}%" if m else "—"))
    print(_row("Avg win",      lambda m: f"{_g(m, 'avg_win_pct', '+.2f')}%" if m else "—"))
    print(_row("Avg loss",     lambda m: f"{_g(m, 'avg_loss_pct', '+.2f')}%" if m else "—"))
    print(sep2)
    print(_row("Profit factor",    lambda m: _fmt_pf(m["profit_factor"]) if m else "—"))
    print(_row("Sharpe ratio",     lambda m: _g(m, "sharpe_ratio", ".2f") if m else "—"))
    print(_row("Max drawdown",     lambda m: f"{_g(m, 'max_drawdown_pct', '.1f')}%" if m else "—"))
    print(_row("Total return",     lambda m: f"{_g(m, 'total_return_pct', '+.1f')}%" if m else "—"))
    print(sep2)

    # Exit breakdown — wins / stops / timeouts
    print(_row("TARGET exits (wins)", lambda m: _g(m, "target_count") if m else "—"))
    print(_row("STOP exits (losses)", lambda m: _g(m, "stop_count")   if m else "—"))
    print(_row("TIMEOUT exits",       lambda m: _g(m, "timeout_count") if m else "—"))
    print(sep2)
    print(_row("Best trade",   lambda m: f"{_g(m, 'best_trade_pct', '+.1f')}%" if m else "—"))
    print(_row("Worst trade",  lambda m: f"{_g(m, 'worst_trade_pct', '+.1f')}%" if m else "—"))
    print(_row("Avg hold",     lambda m: f"{_g(m, 'avg_holding_days', '.0f')}d" if m else "—"))
    print(sep)

    # ── By-regime breakdown per strategy ─────────────────────────────────────
    for name, m in results:
        if not m or not m.get("by_regime"):
            continue
        print(f"\n  {name} — by market regime:")
        print(f"  {'Regime':<18} {'Trades':>7} {'Win%':>7} {'Avg Ret':>10}")
        print(f"  {'-'*46}")
        for regime, stats in sorted(m["by_regime"].items()):
            print(
                f"  {regime:<18} {stats['count']:>7} "
                f"{stats['win_rate']:>6.0f}% "
                f"{stats['avg_return_pct']:>+9.2f}%"
            )

    print(f"\n{sep}\n")


def main() -> int:
    parser  = _build_parser()
    args    = parser.parse_args()

    min_dt, max_dt = BacktestService.get_date_range()
    if min_dt is None or max_dt is None:
        print("ERROR: No signal data found. Run `python run.py` to populate stock_signals.")
        return 1

    end_date   = args.end   if args.end   is not None else max_dt
    start_date = args.start if args.start is not None else date(end_date.year - 3, end_date.month, end_date.day)
    start_date = max(start_date, min_dt)
    end_date   = min(end_date,   max_dt)

    if start_date >= end_date:
        print(f"ERROR: start_date ({start_date}) must be before end_date ({end_date}).")
        return 1

    stop_pct   = args.stop   / 100.0
    target_pct = args.target / 100.0

    shared_params = {
        "start_date":         str(start_date),
        "end_date":           str(end_date),
        "stop_loss_pct":      stop_pct,
        "target_pct":         target_pct,
        "max_holding_days":   args.hold,
        "min_composite_score": args.min_score,
    }

    results: list[tuple[str, dict]] = []

    for display_name, signal, min_mtf in _STRATEGIES:
        mtf_label = f" mtf>={min_mtf}" if min_mtf is not None else ""
        print(f"  Running: {display_name} ({signal}{mtf_label}) ...", flush=True)
        try:
            result = BacktestEngine.run(
                start_date          = start_date,
                end_date            = end_date,
                entry_signal        = signal,
                stop_loss_pct       = stop_pct,
                target_pct          = target_pct,
                max_holding_days    = args.hold,
                min_composite_score = args.min_score,
                min_mtf_score       = min_mtf,
            )
            results.append((display_name, result.metrics))

            if args.out and not result.is_empty:
                safe_name = display_name.lower().replace(" ", "-").replace(">=", "gte").replace("=", "eq")
                out_path  = args.out.parent / f"{args.out.stem}_{safe_name}{args.out.suffix}"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                result.trades.to_csv(out_path, index=False)
                print(f"    Trade log saved: {out_path.resolve()}")

        except Exception as exc:
            logger.error("Backtest failed for %s: %s", display_name, exc, exc_info=True)
            print(f"    ERROR: {exc}")
            results.append((display_name, {}))

    _print_comparison(results, shared_params)
    return 0


if __name__ == "__main__":
    sys.exit(main())
