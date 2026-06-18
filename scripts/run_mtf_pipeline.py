"""Run the Multi-Timeframe (MTF) pipeline standalone.

NOTE: As of the current MasterPipeline, this step runs automatically as
Step 9 of run.py (non-blocking).  Use this script only to re-run MTF signals
without running the full pipeline (e.g. after changing MTFConfig periods).

Prerequisite: main pipeline (run.py) must have run first so stock_features
              has close_price populated for all symbols.

Usage:
    python scripts/run_mtf_pipeline.py

What it does:
    1. Reads stock_features (read-only — no changes to existing tables)
    2. Per symbol: resamples daily close to weekly (W-FRI) and monthly (ME)
    3. Computes EMA fast + slow on each timeframe using MTFConfig periods
    4. Merges EMA values back to daily resolution (backward-fill, no lookahead)
    5. Emits mtf_weekly_trend and mtf_monthly_trend BIT signals + mtf_score (0-2)
    6. Truncates and repopulates stock_mtf_signals

After running:
    - Scanner shows W Trend, M Trend, MTF columns per stock
    - MTF Score = 2 means both weekly AND monthly macro trend confirmed
    - MTF Score = 0 means daily setup exists but macro is not supportive
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config.logging_config import get_logger
from app.pipeline.mtf_pipeline import MTFPipeline

logger = get_logger("run_mtf_pipeline")


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("MTF Pipeline — Multi-Timeframe Trend Signal Computation")
    logger.info("=" * 60)
    MTFPipeline.run()
    logger.info("Done.")
