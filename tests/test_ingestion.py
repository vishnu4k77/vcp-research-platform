"""
Ingestion smoke test — inserts one test candle and verifies it was stored,
then cleans it up.  Validates the write path end-to-end without touching
real market data.

Run: python tests/test_ingestion.py
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from sqlalchemy import text

from app.config.db import engine

_TEST_SYMBOL = "PYTEST_CANARY.NS"
_TEST_DATE = date(2000, 1, 3)   # intentionally old — won't clash with real data


def _cleanup() -> None:
    """Remove the canary row so the test is idempotent."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM daily_price_data "
                "WHERE symbol = :s AND trade_date = :d"
            ),
            {"s": _TEST_SYMBOL, "d": _TEST_DATE},
        )


def test_insert_and_read() -> None:

    _cleanup()   # start clean

    candle = pd.DataFrame([{
        "symbol":      _TEST_SYMBOL,
        "trade_date":  _TEST_DATE,
        "open_price":  100.00,
        "high_price":  110.00,
        "low_price":    95.00,
        "close_price": 108.00,
        "volume":      100_000,
    }])

    candle.to_sql(
        "daily_price_data",
        engine,
        if_exists="append",
        index=False,
    )
    print("Insert               OK")

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT close_price FROM daily_price_data "
                "WHERE symbol = :s AND trade_date = :d"
            ),
            {"s": _TEST_SYMBOL, "d": _TEST_DATE},
        ).fetchone()

    assert row is not None, "Row was not found after insert"
    assert float(row[0]) == 108.00, f"Unexpected close price: {row[0]}"
    print("Read back            OK")

    _cleanup()
    print("Cleanup              OK")


if __name__ == "__main__":
    test_insert_and_read()
    print("\nIngestion test passed.")
