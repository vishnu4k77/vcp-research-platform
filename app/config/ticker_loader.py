from pathlib import Path


def load_tickers():

    ticker_file = Path("app/config/tickers.txt")

    if not ticker_file.exists():
        print("tickers.txt file not found")
        return []

    with open(ticker_file, "r") as file:

        tickers = [
            line.strip()
            for line in file.readlines()
            if line.strip()
        ]

    print(f"\nLoaded {len(tickers)} tickers")

    return tickers