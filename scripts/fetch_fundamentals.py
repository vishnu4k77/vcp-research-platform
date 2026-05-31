"""fetch_fundamentals.py — monthly Screener.in fundamental data refresh.

Run cadence:
  - ONCE after setup_db.py + fetch_nse_tickers.py (initial full scrape ~500 stocks)
  - MONTHLY thereafter (stale guard skips fresh symbols automatically)

Usage:
    python scripts/fetch_fundamentals.py              # scrape all stale symbols
    python scripts/fetch_fundamentals.py RELIANCE TCS # scrape specific symbols only

What it does:
  1. Queries nse_universe for active symbols not scraped within STALE_AFTER_DAYS.
  2. Fetches each company page from Screener.in with rate limiting + retry.
  3. Parses ROE, ROCE, D/E, growth rates, promoter holding, etc.
  4. Persists to stock_fundamentals (upsert-by-symbol+date).
  5. Reports success/failure summary.

After running, quality_signal.py will use real fundamentals instead of the
Phase 1 technical proxy on the next pipeline run.
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path regardless of CWD
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import date

from app.config.logging_config import get_logger
from app.data.screener_service import ScreenerService

logger = get_logger("fetch_fundamentals")


def main() -> int:
    """Entry point — parse optional symbol args and run the scraper.

    Returns:
        0 on success, 1 if more than 10% of symbols failed.
    """
    explicit_symbols = [s.upper().strip() for s in sys.argv[1:]] if len(sys.argv) > 1 else None

    if explicit_symbols:
        logger.info("Targeted scrape: %s", explicit_symbols)

    result = ScreenerService.run(
        trade_date=date.today(),
        symbols=explicit_symbols,
    )

    logger.info(
        "Done | total=%d success=%d failed=%d",
        result["total"], result["success"], result["failed"],
    )

    # Non-zero exit if failure rate > 10% (useful for cron alerting)
    if result["total"] > 0 and result["failed"] / result["total"] > 0.10:
        logger.error("Failure rate above 10%% — check Screener.in connectivity")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
