import sqlite3
import os
from datetime import datetime, timedelta

# --- Configuration (Replicated from app.py) ---
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
    if not os.path.exists(db_path):
        print(f"Error: Database not found at {db_path}")
        return None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return None

def main():
    print(f"Connecting to database: {GARMIN_DB}")
    conn = get_db_connection(GARMIN_DB)
    if not conn:
        return

    # Calculate date range (Last 6 Months)
    today = datetime.now().date()
    six_months_ago = today - timedelta(days=180)
    
    print(f"Analyzing data from {six_months_ago} to {today}...")
    
    query = """
        SELECT day, steps, calories_active, stress_avg 
        FROM daily_summary 
        WHERE day >= ? 
        ORDER BY day DESC
    """
    
    try:
        cursor = conn.cursor()
        cursor.execute(query, (six_months_ago.isoformat(),))
        rows = cursor.fetchall()
    except Exception as e:
        print(f"Error executing query: {e}")
        conn.close()
        return

    conn.close()

    high_cost_days = []

    for row in rows:
        steps = row['steps']
        active_cals = row['calories_active']
        stress_avg = row['stress_avg']
        day = row['day']

        # Filter: Low movement days (Steps < 3000)
        # We also need steps > 0 to avoid division by zero
        if steps and steps > 0 and steps < 3000 and active_cals is not None:
            physio_cost = (active_cals / steps) * 1000
            
            high_cost_days.append({
                'date': day,
                'steps': steps,
                'active_cals': active_cals,
                'cost': physio_cost,
                'stress': stress_avg if stress_avg else 0
            })

    # Sort by Physiological Cost (Highest to Lowest)
    high_cost_days.sort(key=lambda x: x['cost'], reverse=True)

    # Print Top 20
    print("\nTop 20 'Most Expensive' Low-Step Days (< 3000 Steps):")
    print("-" * 80)
    print(f"{'Date':<12} | {'Steps':<6} | {'Active Cals':<12} | {'Cost':<6} | {'Avg Stress':<10} | {'Notes'}")
    print("-" * 80)

    for i, data in enumerate(high_cost_days[:20]):
        # Simple auto-notes based on known dates (optional, just for context if helpful)
        note = ""
        # Example logic for Jan 8/9 if they appear
        if "01-08" in data['date'] or "01-09" in data['date']:
            note = "<-- Check This"
            
        print(f"{data['date']:<12} | {data['steps']:<6} | {data['active_cals']:<12} | {int(data['cost']):<6} | {data['stress']:<10} | {note}")

    print("-" * 80)
    print(f"Total Low-Step Days Found: {len(high_cost_days)}")
    
    # Simple recommendation check
    if len(high_cost_days) > 5:
        # Heuristic: Look at the difference between the top extreme and the 'tail' of the top 20
        # If there's a clear jump, might suggest a threshold.
        pass

if __name__ == "__main__":
    main()
