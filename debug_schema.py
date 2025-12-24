import sqlite3
import os

DB_DIR = os.path.expanduser("/Users/joelgerard/Library/CloudStorage/GoogleDrive-joelgerard@gmail.com/My Drive/joel health/tree health/DBs")
GARMIN_DB = os.path.join(DB_DIR, "garmin.db")

def print_schema(db_path):
    print(f"--- Schema for {os.path.basename(db_path)} ---")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        for table in tables:
            table_name = table[0]
            print(f"Table: {table_name}")
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = cursor.fetchall()
            col_names = [col[1] for col in columns]
            print(f"  Columns: {col_names}")
        conn.close()
    except Exception as e:
        print(f"Error reading {db_path}: {e}")

print_schema(GARMIN_DB)
