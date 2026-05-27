class ScannerService:

    @staticmethod
    def is_above_50ma(df):

        latest = df.iloc[-1]

        return (
            latest["close_price"]
            >
            latest["sma_50"]
        )