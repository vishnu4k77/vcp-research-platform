"""
Read-path smoke test — verifies MarketQueryService can query the DB
and returns well-formed data for a real NSE symbol.

Run: python tests/test_read.py
     python tests/test_read.py HDFCBANK.NS   (optional: specify symbol)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.market_query_service import MarketQueryService

_DEFAULT_SYMBOL = "RELIANCE.NS"


def test_latest_trade_date(symbol: str) -> None:

    latest = MarketQueryService.get_latest_trade_date(symbol)

    if latest is None:
        print(f"  {symbol}: no data found — run ingestion first")
        return

    print(f"Latest trade date    : {latest}")


def test_raw_price_data(symbol: str) -> None:

    df = MarketQueryService.get_raw_price_data(symbol)

    if df.empty:
        print(f"  {symbol}: no price data in DB — run ingestion first")
        return

    required_cols = {"symbol", "trade_date", "open_price", "high_price",
                     "low_price", "close_price", "volume"}
    missing = required_cols - set(df.columns)

    assert not missing, f"Missing columns: {missing}"

    print(f"Symbol               : {symbol}")
    print(f"Rows                 : {len(df):,}")
    print(f"Date range           : {df['trade_date'].min()} → {df['trade_date'].max()}")
    print(f"Latest close         : {df['close_price'].iloc[-1]:.2f}")
    print(f"Schema               : OK ({list(df.columns)})")


if __name__ == "__main__":

    symbol = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_SYMBOL

    print(f"\nTesting read path for {symbol}")
    print("-" * 45)

    test_latest_trade_date(symbol)
    test_raw_price_data(symbol)

    print("\nRead test completed.")
