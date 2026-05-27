import pandas as pd

from sqlalchemy.exc import SQLAlchemyError

from app.config.db import engine
from app.config.ticker_loader import load_tickers

from app.data.yahoo_service import (
    YahooFinanceService
)

from app.services.market_query_service import (
    MarketQueryService
)


class DataIngestionPipeline:

    INSERT_COLUMNS = [
        "symbol",
        "trade_date",
        "open_price",
        "high_price",
        "low_price",
        "close_price",
        "volume"
    ]

    @staticmethod
    def _validate_latest_db_date(
        latest_date,
        ticker: str
    ):

        if latest_date is None:

            return None

        try:

            latest_date = pd.to_datetime(
                latest_date,
                errors="coerce"
            ).date()

            if pd.isna(latest_date):

                print(
                    f"Invalid DB date for {ticker}"
                )

                return None

            today = pd.Timestamp.today().date()

            # future DB corruption protection
            if latest_date > today:

                print(
                    f"Future DB date detected for {ticker}: {latest_date}"
                )

                return None

            return latest_date

        except Exception as e:

            print(
                f"Date validation failed for {ticker}"
            )

            print(str(e))

            return None

    @staticmethod
    def _should_skip_ingestion(
        latest_date,
        ticker: str
    ) -> bool:

        if latest_date is None:

            return False

        today = pd.Timestamp.today().date()

        # already latest candle
        if latest_date >= today:

            print(
                f"{ticker} already up-to-date"
            )

            return True

        return False

    @staticmethod
    def _validate_insert_dataframe(
        df: pd.DataFrame,
        ticker: str
    ) -> pd.DataFrame:

        if df.empty:

            return pd.DataFrame()

        required_columns = [
            "trade_date",
            "open_price",
            "high_price",
            "low_price",
            "close_price",
            "volume"
        ]

        missing_columns = [
            col
            for col in required_columns
            if col not in df.columns
        ]

        if missing_columns:

            print(
                f"Insert columns missing for {ticker}: {missing_columns}"
            )

            return pd.DataFrame()

        # add symbol
        df["symbol"] = ticker

        # final insert ordering
        final_df = df[
            DataIngestionPipeline
            .INSERT_COLUMNS
        ].copy()

        # remove duplicate rows
        final_df.drop_duplicates(
            subset=["symbol", "trade_date"],
            inplace=True
        )

        # final sorting
        final_df.sort_values(
            by="trade_date",
            inplace=True
        )

        final_df.reset_index(
            drop=True,
            inplace=True
        )

        return final_df

    @staticmethod
    def _insert_dataframe(
        df: pd.DataFrame,
        ticker: str
    ):

        if df.empty:

            print(
                f"No rows available for insertion: {ticker}"
            )

            return

        try:

            rows_inserted = df.to_sql(
                "daily_price_data",
                engine,
                if_exists="append",
                index=False,
                method="multi",
                chunksize=500
            )

            print(
                f"Inserted rows for {ticker}: {rows_inserted}"
            )

        except SQLAlchemyError as e:

            error_message = str(e)

            if "uq_symbol_date" in error_message:

                print(
                    f"Duplicate rows skipped for {ticker}"
                )

            else:

                print(
                    f"SQL insertion failed for {ticker}"
                )

                print(error_message)

    @staticmethod
    def _process_ticker(
        ticker: str
    ):

        print(f"\nProcessing {ticker}")

        # get latest DB date
        latest_db_date = (
            MarketQueryService
            .get_latest_trade_date(
                ticker
            )
        )

        validated_latest_date = (
            DataIngestionPipeline
            ._validate_latest_db_date(
                latest_db_date,
                ticker
            )
        )

        print(
            f"Validated latest DB date: {validated_latest_date}"
        )

        # skip unnecessary ingestion
        if (
            DataIngestionPipeline
            ._should_skip_ingestion(
                validated_latest_date,
                ticker
            )
        ):

            return

        # fetch incremental Yahoo data
        clean_df = (
            YahooFinanceService
            .fetch_stock_data(
                ticker=ticker,
                latest_date=validated_latest_date
            )
        )

        if clean_df.empty:

            print(
                f"No clean Yahoo data available for {ticker}"
            )

            return

        # final insert validation
        final_insert_df = (
            DataIngestionPipeline
            ._validate_insert_dataframe(
                clean_df,
                ticker
            )
        )

        if final_insert_df.empty:

            print(
                f"No valid insert rows for {ticker}"
            )

            return

        print(
            f"Final validated rows for insertion: {len(final_insert_df)}"
        )

        # insert into DB
        DataIngestionPipeline._insert_dataframe(
            final_insert_df,
            ticker
        )

    @staticmethod
    def run():

        tickers = load_tickers()

        if not tickers:

            print(
                "No tickers configured"
            )

            return

        print(
            f"\nStarting ingestion for {len(tickers)} tickers"
        )

        for ticker in tickers:

            try:

                DataIngestionPipeline._process_ticker(
                    ticker
                )

            except Exception as e:

                print(
                    f"Pipeline failure for {ticker}"
                )

                print(str(e))

        print("\nData ingestion completed")


if __name__ == "__main__":

    DataIngestionPipeline.run()