"""Standalone data quality runner.

Run manually to do an immediate cleanup without running the full pipeline:

    python scripts/run_data_quality.py              # run all three checks
    python scripts/run_data_quality.py --stale-only # only fix stale data
    python scripts/run_data_quality.py --report     # show today's log summary

Useful after:
  - Finding new Yahoo bad data in SSMS
  - Onboarding new stocks (thin history audit)
  - After a long weekend (gap check)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from app.config.db import engine
from app.data.data_quality_service import DataQualityService
from app.config.logging_config import get_logger

logger = get_logger("run_data_quality")


def print_report() -> None:
    """Print the last 7 days of data_quality_log grouped by date and type."""
    query = text("""
        SELECT
            run_date,
            check_type,
            COUNT(*)            AS issues,
            SUM(rows_affected)  AS rows_deleted,
            STRING_AGG(symbol, ', ') AS symbols
        FROM data_quality_log
        WHERE run_date >= DATEADD(day, -7, CAST(GETDATE() AS DATE))
        GROUP BY run_date, check_type
        ORDER BY run_date DESC, check_type
    """)
    try:
        with engine.connect() as conn:
            rows = conn.execute(query).fetchall()
        if not rows:
            print("No data quality issues logged in the last 7 days.")
            return
        print(f"\n{'Date':<12} {'Check':<20} {'Issues':>6} {'Rows Del':>9}  Symbols")
        print("-" * 80)
        for r in rows:
            syms = (r[4] or "")[:60]
            print(f"{str(r[0]):<12} {r[1]:<20} {r[2]:>6} {(r[3] or 0):>9}  {syms}")
    except Exception as exc:
        print(f"Report failed: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Data quality checks for daily_price_data")
    parser.add_argument("--stale-only", action="store_true", help="Only run stale data fix")
    parser.add_argument("--report",     action="store_true", help="Show 7-day log summary")
    args = parser.parse_args()

    if args.report:
        print_report()
        return

    if args.stale_only:
        deleted = DataQualityService.fix_stale_data()
        print(f"Stale rows deleted: {deleted}")
        return

    # Full run
    DataQualityService.run()
    print_report()


if __name__ == "__main__":
    main()
