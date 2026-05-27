from sqlalchemy import create_engine
from dotenv import load_dotenv
import urllib
import os


load_dotenv()

server = os.getenv("DB_SERVER")
database = os.getenv("DB_NAME")

params = urllib.parse.quote_plus(
    f"DRIVER={{ODBC Driver 17 for SQL Server}};"
    f"SERVER={server};"
    f"DATABASE={database};"
    f"Trusted_Connection=yes;"
)

connection_string = f"mssql+pyodbc:///?odbc_connect={params}"

engine = create_engine(connection_string)

print("\nSQL Server connection initialized")