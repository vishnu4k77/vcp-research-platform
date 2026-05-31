"""Profile: raw pyodbc cursor vs SQLAlchemy fetchall."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date, timedelta
import numpy as np, pandas as pd
from app.config.db import engine
from app.config.strategy_config import BacktestConfig
from app.backtesting.backtest_service import BacktestService

entries = BacktestService.get_signal_entries(
    start_date=date(2023, 5, 30), end_date=date(2026, 5, 29),
    entry_signal="breakout_signal", min_composite_score=50.0,
)
entries["entry_date"] = pd.to_datetime(entries["entry_date"])
symbols     = entries["symbol"].unique().tolist()
first_entry = entries["entry_date"].min().date()
price_end   = date(2026, 5, 29) + timedelta(days=BacktestConfig.FORWARD_PRICE_BUFFER_DAYS)
print(f"Symbols={len(symbols)}  rows expected=~320k")

# Build SQL with ? placeholders for raw pyodbc
placeholders = ",".join(["?"]*len(symbols))
sql = (
    f"SELECT symbol, trade_date, high_price, low_price, close_price "
    f"FROM daily_price_data "
    f"WHERE symbol IN ({placeholders}) "
    f"AND trade_date BETWEEN ? AND ?"
)
params = tuple(symbols) + (first_entry, price_end)

print("\n--- Option A: raw pyodbc cursor ---")
t0 = time.perf_counter()
raw = engine.raw_connection()
cur = raw.cursor()
t1 = time.perf_counter()
cur.execute(sql, params)
t2 = time.perf_counter()
rows = cur.fetchall()
t3 = time.perf_counter()

if rows:
    cols = list(zip(*rows))
    df = pd.DataFrame({
        "symbol":      list(cols[0]),
        "trade_date":  list(cols[1]),
        "high_price":  np.array(cols[2], dtype=float),
        "low_price":   np.array(cols[3], dtype=float),
        "close_price": np.array(cols[4], dtype=float),
    })
t4 = time.perf_counter()
raw.close()
print(f"  execute  : {t2-t1:.2f}s")
print(f"  fetchall : {t3-t2:.2f}s  rows={len(rows)}")
print(f"  df build : {t4-t3:.2f}s")
print(f"  TOTAL    : {t4-t0:.2f}s")
