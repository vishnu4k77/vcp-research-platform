from pathlib import Path


PROJECT_STRUCTURE = [
    "app",
    "app/config",
    "app/data",
    "app/services"
]


FILES = [
    "requirements.txt",
    ".env",
    "README.md",
    "app/main.py",
    "app/config/tickers.txt",
    "app/config/ticker_loader.py"
]


def create_folders():

    for folder in PROJECT_STRUCTURE:

        path = Path(folder)

        path.mkdir(parents=True, exist_ok=True)

        print(f"Created folder: {folder}")


def create_files():

    for file in FILES:

        path = Path(file)

        path.touch(exist_ok=True)

        print(f"Created file: {file}")


if __name__ == "__main__":

    create_folders()

    create_files()

    print("\nProject setup completed.")