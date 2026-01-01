import sqlite3
import os
import sys

# Copy path logic from app.py
DEFAULT_DB_DIR = os.path.expanduser("/Users/joelgerard/Library/CloudStorage/GoogleDrive-joelgerard@gmail.com/My Drive/joel health/tree health/tree_home/HealthData/DBs")
LOCAL_CONFIG_PATH = os.path.expanduser("~/.tree_health_config")

if os.environ.get("TREE_HEALTH_DB_DIR"):
    DB_DIR = os.path.expanduser(os.environ["TREE_HEALTH_DB_DIR"])
elif os.path.exists(LOCAL_CONFIG_PATH):
    with open(LOCAL_CONFIG_PATH, 'r') as f:
        DB_DIR = os.path.expanduser(f.read().strip())
else:
    DB_DIR = DEFAULT_DB_DIR

GARMIN_DB = os.path.join(DB_DIR, "garmin.db")

def check_schema():
    if not os.path.exists(GARMIN_DB):
        print(f"DB not found at {GARMIN_DB}")
        return

    conn = sqlite3.connect(GARMIN_DB)
    cursor = conn.cursor()
    
    # Check daily_summary columns
    cursor.execute("PRAGMA table_info(daily_summary)")
    cols = [row[1] for row in cursor.fetchall()]
    print(f"daily_summary columns: {cols}")
    
    # Check sleep columns
    cursor.execute("PRAGMA table_info(sleep)")
    cols_sleep = [row[1] for row in cursor.fetchall()]
    print(f"sleep columns: {cols_sleep}")
    
    # Check one row of sleep to see format
    cursor.execute("SELECT total_sleep FROM sleep ORDER BY day DESC LIMIT 1")
    row = cursor.fetchone()
    print(f"Sample total_sleep: {row[0] if row else 'None'}")
    
    conn.close()

if __name__ == "__main__":
    check_schema()
