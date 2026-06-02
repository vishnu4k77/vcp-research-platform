import os
import urllib
from contextlib import contextmanager
from typing import Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

load_dotenv()

server = os.getenv("DB_SERVER")
database = os.getenv("DB_NAME")

params = urllib.parse.quote_plus(
    f"DRIVER={{ODBC Driver 17 for SQL Server}};"
    f"SERVER={server};"
    f"DATABASE={database};"
    f"Trusted_Connection=yes;"
)

engine = create_engine(
    f"mssql+pyodbc:///?odbc_connect={params}",
    fast_executemany=True,   # pyodbc bulk-batch mode — reduces 548K-row insert from 20min → ~2min
)


@contextmanager
def nolock_connect() -> Generator[Connection, None, None]:
    """Yield a SQL Server connection set to READ UNCOMMITTED isolation.

    Dashboard read queries use this instead of engine.connect() to avoid
    blocking on pipeline write transactions.  When the pipeline inserts
    547k rows into stock_signals the write lock is held for ~20 minutes;
    a default READ COMMITTED reader blocks for that entire duration.

    READ UNCOMMITTED (NOLOCK) lets the dashboard read whatever rows are
    already committed or even partially written — acceptable for a display
    dashboard where slightly stale data is far better than a 20-minute hang.

    Usage::

        with nolock_connect() as conn:
            rows = conn.execute(query, params).fetchall()

    Yields:
        SQLAlchemy Connection with READ UNCOMMITTED isolation active.
    """
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED"))
        yield conn