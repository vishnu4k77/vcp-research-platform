from app.config.db import engine


def test_connection():

    with engine.connect() as conn:

        print("SQL Server connection successful")


if __name__ == "__main__":
    test_connection()