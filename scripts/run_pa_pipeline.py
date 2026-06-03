"""Run the Price Action pipeline.

Prerequisite: main pipeline (run.py) must have run first so stock_features
              has EMA50 and relative_volume populated.

Usage:
    python scripts/run_pa_pipeline.py

What it does:
    1. Reads stock_features (read-only — no changes to existing tables)
    2. Computes daily + weekly HH/HL structure, candle quality, volume quality,
       momentum acceleration, and EMA50 extension per symbol
    3. Computes pa_score (0-100) and pa_signal (BIT) per symbol per day
    4. Truncates and repopulates stock_pa_signals

After running:
    - Open scanner → "Price Action" preset shows stocks where pa_signal=1
    - Open scanner → "Combined PA+EMA" preset shows highest conviction setups
    - Compare vs existing "Composite (Default)" to backtest PA accuracy
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config.logging_config import get_logger
from app.pipeline.pa_pipeline import PAPipeline

logger = get_logger("run_pa_pipeline")


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("PA Pipeline — Price Action Signal Computation")
    logger.info("=" * 60)
    PAPipeline.run()
    logger.info("Done.")
