import sqlite3
import os

# Path to DB
DB_PATH = os.path.expanduser("/Users/joelgerard/Library/CloudStorage/GoogleDrive-joelgerard@gmail.com/My Drive/joel health/tree health/DBs/garmin.db")

def get_db_connection():
    return sqlite3.connect(DB_PATH)

def calculate_golden_era():
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Golden Era: March 1, 2025 - May 31, 2025
    start_date = '2025-03-01'
    end_date = '2025-05-31'
    
    try:
        # 1. RHR from resting_hr
        # Verify table exists first just in case
        cursor.execute("SELECT AVG(resting_heart_rate) FROM resting_hr WHERE day BETWEEN ? AND ?", (start_date, end_date))
        avg_rhr = cursor.fetchone()[0]
        
        # 2. HRV from hrv (using last_night_avg)
        cursor.execute("SELECT AVG(last_night_avg) FROM hrv WHERE day BETWEEN ? AND ?", (start_date, end_date))
        avg_hrv = cursor.fetchone()[0]
        
        # 3. Respiration Rate from daily_summary
        cursor.execute("SELECT AVG(rr_waking_avg) FROM daily_summary WHERE day BETWEEN ? AND ?", (start_date, end_date))
        avg_rr = cursor.fetchone()[0]
        
        print(f"--- Golden Era Baselines ({start_date} to {end_date}) ---")
        # Handle None just in case no data found
        print(f"Baseline RHR: {avg_rhr if avg_rhr else 0:.2f} bpm")
        print(f"Baseline HRV: {avg_hrv if avg_hrv else 0:.2f} ms")
        print(f"Baseline Respiration: {avg_rr if avg_rr else 0:.2f} brpm")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()
    
if __name__ == "__main__":
    calculate_golden_era()
