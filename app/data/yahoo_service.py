from datetime import timedelta

import pandas as pd
import yfinance as yf


class YahooFinanceService:

    REQUIRED_COLUMNS = [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume"
    ]

    MAX_DAILY_MOVE_RATIO = 5

    MAX_VOLUME_MULTIPLIER = 100

    @staticmethod
    def _validate_latest_date(
        latest_date
    ):

        if latest_date is None:

            return None

        try:

            latest_date = pd.to_datetime(
                latest_date,
                errors="coerce"
            ).date()

            if pd.isna(latest_date):

                return None

            today = pd.Timestamp.today().date()

            # future DB corruption protection
            if latest_date > today:

                print(
                    f"Future DB date detected: {latest_date}"
                )

                return None

            return latest_date

        except Exception:

            return None

    @staticmethod
    def _determine_download_mode(
        latest_date
    ):

        today = pd.Timestamp.today().date()

        # first-time historical load
        if latest_date is None:

            return {
                "mode": "historical",
                "start_date": None
            }

        # already latest
        if latest_date >= today:

            return {
                "mode": "skip",
                "start_date": None
            }

        next_day = latest_date + timedelta(days=1)

        # safety
        if next_day > today:

            return {
                "mode": "skip",
                "start_date": None
            }

        return {
            "mode": "incremental",
            "start_date": next_day
        }

    @staticmethod
    def _download_data(
        ticker: str,
        download_mode: dict
    ) -> pd.DataFrame:

        try:

            if download_mode["mode"] == "skip":

                print(
                    f"{ticker} already up-to-date"
                )

                return pd.DataFrame()

            if download_mode["mode"] == "historical":

                print(
                    f"Historical download for {ticker}"
                )

                data = yf.download(
                    tickers=ticker,
                    period="6mo",
                    progress=False,
                    auto_adjust=False
                )

            else:

                start_date = (
                    download_mode["start_date"]
                )

                print(
                    f"Incremental download from {start_date}"
                )

                data = yf.download(
                    tickers=ticker,
                    start=start_date.strftime(
                        "%Y-%m-%d"
                    ),
                    progress=False,
                    auto_adjust=False
                )

            return data

        except Exception as e:

            print(
                f"Yahoo download failed for {ticker}"
            )

            print(str(e))

            return pd.DataFrame()

    @staticmethod
    def _normalize_columns(
        data: pd.DataFrame
    ) -> pd.DataFrame:

        if data.empty:

            return pd.DataFrame()

        # flatten multi-index
        if isinstance(
            data.columns,
            pd.MultiIndex
        ):

            data.columns = (
                data.columns
                .get_level_values(0)
            )

        data.reset_index(inplace=True)

        possible_date_columns = [
            "Date",
            "Datetime",
            "index"
        ]

        actual_date_column = None

        for col in possible_date_columns:

            if col in data.columns:

                actual_date_column = col
                break

        if not actual_date_column:

            print(
                "Date column missing"
            )

            return pd.DataFrame()

        missing_columns = [
            col
            for col in YahooFinanceService.REQUIRED_COLUMNS
            if col not in data.columns
        ]

        if missing_columns:

            print(
                f"Missing columns: {missing_columns}"
            )

            return pd.DataFrame()

        normalized_df = data[
            [
                actual_date_column,
                "Open",
                "High",
                "Low",
                "Close",
                "Volume"
            ]
        ].copy()

        normalized_df.columns = [
            "trade_date",
            "open_price",
            "high_price",
            "low_price",
            "close_price",
            "volume"
        ]

        return normalized_df

    @staticmethod
    def _normalize_datatypes(
        df: pd.DataFrame
    ) -> pd.DataFrame:

        if df.empty:

            return pd.DataFrame()

        df["trade_date"] = pd.to_datetime(
            df["trade_date"],
            errors="coerce"
        ).dt.date

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

        return df

    @staticmethod
    def _apply_quality_filters(
        df: pd.DataFrame
    ) -> pd.DataFrame:

        if df.empty:

            return pd.DataFrame()

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

        # remove duplicates
        df.drop_duplicates(
            subset=["trade_date"],
            inplace=True
        )

        # invalid OHLC
        df = df[
            (df["high_price"] >= df["low_price"])
            &
            (df["open_price"] > 0)
            &
            (df["close_price"] > 0)
            &
            (df["volume"] >= 0)
        ]

        # split anomaly filter
        df = df[
            (
                abs(
                    df["close_price"]
                    /
                    df["open_price"]
                )
                <
                YahooFinanceService.MAX_DAILY_MOVE_RATIO
            )
        ]

        # abnormal volume filter
        volume_median = (
            df["volume"].median()
        )

        if volume_median > 0:

            df = df[
                df["volume"]
                <
                (
                    volume_median
                    *
                    YahooFinanceService
                    .MAX_VOLUME_MULTIPLIER
                )
            ]

        # final sort
        df.sort_values(
            by="trade_date",
            inplace=True
        )

        df.reset_index(
            drop=True,
            inplace=True
        )

        return df

    @staticmethod
    def fetch_stock_data(
        ticker: str,
        latest_date=None
    ) -> pd.DataFrame:

        print(f"\nFetching Yahoo data for {ticker}")

        validated_latest_date = (
            YahooFinanceService
            ._validate_latest_date(
                latest_date
            )
        )

        download_mode = (
            YahooFinanceService
            ._determine_download_mode(
                validated_latest_date
            )
        )

        raw_data = (
            YahooFinanceService
            ._download_data(
                ticker,
                download_mode
            )
        )

        if raw_data.empty:

            print(
                f"No Yahoo data returned for {ticker}"
            )

            return pd.DataFrame()

        normalized_df = (
            YahooFinanceService
            ._normalize_columns(
                raw_data
            )
        )

        if normalized_df.empty:

            return pd.DataFrame()

        normalized_df = (
            YahooFinanceService
            ._normalize_datatypes(
                normalized_df
            )
        )

        clean_df = (
            YahooFinanceService
            ._apply_quality_filters(
                normalized_df
            )
        )

        if clean_df.empty:

            print(
                f"No valid clean rows for {ticker}"
            )

            return pd.DataFrame()

        print(
            f"Validated rows: {len(clean_df)}"
        )

        return clean_df