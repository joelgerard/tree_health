import sqlite3
import os
import sys

# Configuration from app.py
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

def get_db_connection(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def verify_golden_age():
    print(f"Connecting to database at: {GARMIN_DB}")
    try:
        conn = get_db_connection(GARMIN_DB)
        cursor = conn.cursor()
        
        # Query May 2025 data
        start_date = "2025-05-01"
        end_date = "2025-05-31"
        
        print(f"Analyzing data from {start_date} to {end_date}...")
        
        cursor.execute("SELECT day, steps, stress_avg FROM daily_summary WHERE day >= ? AND day <= ?", (start_date, end_date))
        rows = cursor.fetchall()
        
        total_days = len(rows)
        flagged_days = []
        
        # Logic to test: Steps < 3000 AND Stress > 35
        for row in rows:
            steps = row['steps']
            stress = row['stress_avg']
            day = row['day']
            
            if steps is not None and steps < 3000 and stress is not None and stress > 35:
                flagged_days.append({'day': day, 'steps': steps, 'stress': stress})
                
        print(f"\nTotal Days Analyzed: {total_days}")
        print(f"Flagged Days (False Positives): {len(flagged_days)}")
        
        if len(flagged_days) > 0:
            print("\nList of Failures:")
            for item in flagged_days:
                print(f"[{item['day']}] Steps: {item['steps']}, Stress: {item['stress']}")
            print("\n❌ TEST FAILED: Logic is too strict. Adjust thresholds.")
        else:
             print("\n✅ TEST PASSED: Logic is safe.")
             
        conn.close()
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    verify_golden_age()
