import pandas as pd

from sqlalchemy import text

from app.config.db import engine


class MarketQueryService:

    REQUIRED_COLUMNS = [
        "symbol",
        "trade_date",
        "open_price",
        "high_price",
        "low_price",
        "close_price",
        "volume"
    ]

    @staticmethod
    def get_latest_trade_date(symbol: str):

        query = text("""
            SELECT MAX(trade_date) AS latest_date
            FROM daily_price_data
            WHERE symbol = :symbol
        """)

        with engine.connect() as conn:

            df = pd.read_sql(
                query,
                conn,
                params={"symbol": symbol}
            )

        if df.empty:
            return None

        latest_date = df.iloc[0]["latest_date"]

        return latest_date

    @staticmethod
    def get_stock_data(symbol: str) -> pd.DataFrame:

        query = text("""
            SELECT
                symbol,
                trade_date,
                open_price,
                high_price,
                low_price,
                close_price,
                volume
            FROM daily_price_data
            WHERE symbol = :symbol
            ORDER BY trade_date ASC
        """)

        with engine.connect() as conn:

            df = pd.read_sql(
                query,
                conn,
                params={"symbol": symbol}
            )

        if df.empty:

            print(f"No data found for {symbol}")

            return pd.DataFrame()

        # normalize column names
        df.columns = [
            col.strip().lower()
            for col in df.columns
        ]

        # validate schema
        missing_columns = [
            col
            for col in MarketQueryService.REQUIRED_COLUMNS
            if col not in df.columns
        ]

        if missing_columns:

            raise ValueError(
                f"Missing columns: {missing_columns}"
            )

        # normalize types
        df["trade_date"] = pd.to_datetime(
            df["trade_date"],
            errors="coerce"
        )

        numeric_columns = [
            "open_price",
            "high_price",
            "low_price",
            "close_price",
            "volume"
        ]

        for col in numeric_columns:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        # remove duplicates
        df.drop_duplicates(
            subset=["symbol", "trade_date"],
            inplace=True
        )

        # remove nulls
        df.dropna(
            subset=[
                "trade_date",
                "open_price",
                "high_price",
                "low_price",
                "close_price",
                "volume"
            ],
            inplace=True
        )

        # invalid candle removal
        df = df[
            (df["high_price"] >= df["low_price"])
            &
            (df["open_price"] > 0)
            &
            (df["close_price"] > 0)
            &
            (df["volume"] >= 0)
        ]

        # remove extreme split candles
        df = df[
            (
                abs(
                    df["close_price"]
                    /
                    df["open_price"]
                ) < 5
            )
        ]

        df.sort_values(
            by="trade_date",
            inplace=True
        )

        df.reset_index(
            drop=True,
            inplace=True
        )

        # indicators
        df["sma_20"] = (
            df["close_price"]
            .rolling(20)
            .mean()
        )

        df["sma_50"] = (
            df["close_price"]
            .rolling(50)
            .mean()
        )

        df["sma_150"] = (
            df["close_price"]
            .rolling(150)
            .mean()
        )

        df["sma_200"] = (
            df["close_price"]
            .rolling(200)
            .mean()
        )

        df["avg_volume_20"] = (
            df["volume"]
            .rolling(20)
            .mean()
        )

        return df