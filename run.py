"""
NSE Breakout Intelligence Scanner — EOD Pipeline Entry Point

Runs the full 4-step pipeline:
  1. Ingestion  — pulls incremental OHLCV from Yahoo Finance into daily_price_data
  2. Features   — computes EMA, ATR, volume, volatility, Weinstein stage
  3. Regime     — classifies Nifty 50 Bull/Bear/Choppy, writes to market_regime
  4. Signals    — runs VCP + Breakout + Quality detectors, composite ranking

Usage:
    python run.py                     # full run
    python run.py --skip-ingestion    # reprocess existing price data only

Windows Task Scheduler (run daily at 15:45 IST — 15 min after market close):
    Program:   python
    Arguments: D:\\VCPM\\vcp-research-platform\\run.py
    Start in:  D:\\VCPM\\vcp-research-platform
    Trigger:   Daily, 15:45

Exit codes:
    0  pipeline succeeded
    1  one or more steps failed (check logs/pipeline.log)
"""
import argparse
import sys
from pathlib import Path

# Make project importable regardless of working directory
sys.path.insert(0, str(Path(__file__).parent))

from app.pipeline.master_pipeline import MasterPipeline


def _parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description="NSE VCP Breakout Scanner — EOD Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python run.py                  # full run\n"
            "  python run.py --skip-ingestion # re-score existing data\n"
        ),
    )

    parser.add_argument(
        "--skip-ingestion",
        action="store_true",
        help="Skip Yahoo Finance fetch and reuse data already in daily_price_data.",
    )

    return parser.parse_args()


def main() -> int:

    args = _parse_args()

    result = MasterPipeline.run(skip_ingestion=args.skip_ingestion)

    return 0 if result.pipeline_status == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())
