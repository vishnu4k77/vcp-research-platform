"""
Smoke test — verifies SQL Server connectivity and table existence.
Run: python tests/test_connection.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import inspect, text
from app.config.db import engine

REQUIRED_TABLES = [
    "daily_price_data",
    "stock_features",
    "stock_signals",
    "market_regime",
    "market_environment",
    "pipeline_runs",
    "signal_audit",
]


def test_connection() -> None:

    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1 AS ok")).fetchone()
        assert result[0] == 1
    print("DB connection         OK")


def test_tables_exist() -> None:

    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    missing = [t for t in REQUIRED_TABLES if t not in existing]

    if missing:
        raise AssertionError(
            f"Missing tables: {missing}\n"
            "Run: python setup_db.py"
        )

    print(f"All {len(REQUIRED_TABLES)} required tables found   OK")


def test_price_data_has_rows() -> None:

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT COUNT(*) FROM daily_price_data")
        ).fetchone()
        count = row[0]

    print(f"daily_price_data rows : {count:,}")

    if count == 0:
        print("  WARNING: No price data yet. Run: python run.py")


if __name__ == "__main__":
    test_connection()
    test_tables_exist()
    test_price_data_has_rows()
    print("\nAll checks passed.")
