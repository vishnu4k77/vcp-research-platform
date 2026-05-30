import os
import urllib

from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()

server = os.getenv("DB_SERVER")
database = os.getenv("DB_NAME")

params = urllib.parse.quote_plus(
    f"DRIVER={{ODBC Driver 17 for SQL Server}};"
    f"SERVER={server};"
    f"DATABASE={database};"
    f"Trusted_Connection=yes;"
)

engine = create_engine(f"mssql+pyodbc:///?odbc_connect={params}")