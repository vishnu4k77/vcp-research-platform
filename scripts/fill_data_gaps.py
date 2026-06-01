"""fill_data_gaps.py — daily standalone data-integrity script.

Run this AFTER run.py every trading day to detect and heal any missing price data.

DAILY SETUP (run.py never touches this — completely separate)
------------------------------------------------------------
  Step 1 — 3:45 PM IST:  python run.py
  Step 2 — 4:15 PM IST:  python scripts/fill_data_gaps.py
  Step 3 — ONLY if output shows "still_missing > 0" was fixed:
                          python run.py --skip-ingestion
                          (recomputes features + signals on now-complete data)

Windows Task Scheduler (set once, runs forever):
  Task 1 — daily 15:45: python D:\VCPM\vcp-research-platform\run.py
  Task 2 — daily 16:15: python D:\VCPM\vcp-research-platform\scripts\fill_data_gaps.py

What this detects and fixes
---------------------------
  Type A — Gaps: ticker exists in DB but is missing 1+ recent trading days
            Cause: temporary Yahoo outage, rate-throttle, network issue
            Fix:   targeted incremental download from day before the gap

  Type B — New tickers: ticker in nse_universe but has ZERO rows in DB
            Cause: was just added to nse_universe, or its first download ever failed
            Fix:   full 5-year historical download

Usage:
  python scripts/fill_data_gaps.py                # last 5 trading days (default)
  python scripts/fill_data_gaps.py --lookback 10  # last 10 trading days
  python scripts/fill_data_gaps.py --dry-run      # detect only, no inserts

Exit codes:
  0  success (no gaps, or gaps found and filled)
  1  still_missing > 0 after all retries (check log for delisted tickers)
  2  fatal error (DB unreachable, no tickers configured)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config.logging_config import get_logger
from app.config.strategy_config import StrategyConfig
from app.config.ticker_loader import load_tickers
from app.data.gap_filler import DataGapFiller
from app.main import RateLimiter

logger = get_logger("fill_data_gaps")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect and fill missing NSE price data gaps",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/fill_data_gaps.py\n"
            "  python scripts/fill_data_gaps.py --lookback 10\n"
            "  python scripts/fill_data_gaps.py --dry-run\n"
        ),
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=StrategyConfig.INGESTION_GAP_CHECK_DAYS,
        metavar="DAYS",
        help=f"Trading days to audit for gaps (default: {StrategyConfig.INGESTION_GAP_CHECK_DAYS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Detect only — print gaps without writing to DB",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    # ── Load active tickers ───────────────────────────────────────────────────
    tickers = load_tickers()
    if not tickers:
        print("ERROR: No active tickers in nse_universe. Run fetch_nse_tickers.py first.")
        return 2

    # ── Load trading calendar ─────────────────────────────────────────────────
    trading_days = DataGapFiller.get_trading_days(args.lookback)
    if not trading_days:
        print(
            "ERROR: market_regime table is empty.\n"
            "Run first: python scripts/backfill_regime.py"
        )
        return 2

    print(f"\n{'=' * 55}")
    print(f"  NSE Data Gap Scanner — {trading_days[0]} → {trading_days[-1]}")
    print(f"  Checking {len(tickers)} tickers / last {args.lookback} trading days")
    print(f"{'=' * 55}")

    # ── Type B: new tickers with zero history ─────────────────────────────────
    new_tickers = DataGapFiller.find_new_tickers(tickers)
    if new_tickers:
        print(f"\n[TYPE B] New tickers with zero history: {len(new_tickers)}")
        for t in sorted(new_tickers):
            print(f"  {t}")
    else:
        print("\n[TYPE B] No new tickers found.")

    # ── Type A: gaps in existing history ─────────────────────────────────────
    existing = [t for t in tickers if t not in new_tickers]
    gaps = DataGapFiller.find_gaps(existing, trading_days)
    if gaps:
        print(f"\n[TYPE A] Gaps in existing history: {len(gaps)} ticker(s)")
        for ticker, earliest in sorted(gaps.items()):
            print(f"  {ticker:<22} earliest gap: {earliest}")
    else:
        print("[TYPE A] No gaps found in existing history.")

    total_issues = len(new_tickers) + len(gaps)

    if total_issues == 0:
        print(f"\nAll {len(tickers)} tickers have complete data. Nothing to do.")
        return 0

    if args.dry_run:
        print(f"\nDry-run: {total_issues} issue(s) detected. No inserts performed.")
        print("Re-run without --dry-run to fix.")
        return 0

    # ── Fill ──────────────────────────────────────────────────────────────────
    print(f"\nFixing {total_issues} issue(s)...")
    rate_limiter = RateLimiter(StrategyConfig.YAHOO_RATE_LIMIT_SLEEP_SECONDS)
    summary = DataGapFiller.run(tickers, rate_limiter, lookback_days=args.lookback)

    print(f"\n{'=' * 55}")
    print("  Results")
    print(f"{'=' * 55}")
    print(f"  New tickers:  found={summary['new_tickers_found']}  "
          f"filled={summary['new_tickers_filled']}  "
          f"failed={summary['new_tickers_failed']}")
    print(f"  History gaps: found={summary['gaps_found']}  "
          f"filled={summary['gaps_filled']}  "
          f"failed={summary['gaps_failed']}")
    print(f"  Still missing after all retries: {summary['still_missing']}")

    if summary["still_missing"] > 0:
        print(
            "\nACTION NEEDED for still-missing tickers:\n"
            "  1. Check if ticker exists on finance.yahoo.com\n"
            "  2. If delisted/renamed, mark inactive in SSMS:\n"
            "     UPDATE nse_universe SET is_active=0 WHERE symbol='TICKER'\n"
            "  3. If Yahoo temporarily down, re-run tomorrow."
        )
        return 1

    if summary["gaps_filled"] > 0 or summary["new_tickers_filled"] > 0:
        print(
            "\nGaps were fixed. Recompute features + signals on complete data:\n"
            "  python run.py --skip-ingestion"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
