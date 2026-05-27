import pandas as pd

from app.config.db import engine


data = pd.DataFrame({
    "symbol": ["TEST.NS"],
    "trade_date": ["2026-05-01"],
    "open_price": [100],
    "high_price": [110],
    "low_price": [95],
    "close_price": [108],
    "volume": [100000]
})


rows = data.to_sql(
    "daily_price_data",
    engine,
    if_exists="append",
    index=False
)

print(f"Inserted rows: {rows}")