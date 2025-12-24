import sqlite3
import os
from datetime import datetime, timedelta

# Constants (Golden Era Baselines)
BASELINE_RHR = 50.61
BASELINE_HRV = 51.45
BASELINE_RR = 15.11

# Weights
WEIGHT_RHR = 0.4
WEIGHT_HRV = 0.4
WEIGHT_RR = 0.2

# DB Path (Live)
DB_PATH = os.path.expanduser("/Users/joelgerard/Library/CloudStorage/GoogleDrive-joelgerard@gmail.com/My Drive/joel health/tree health/DBs/garmin.db")

def get_db_connection(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def get_recovery_score(conn):
    """
    Calculates Recovery Score (0-100%) based on comparison with Golden Era baselines.
    Returns a dictionary with score details.
    """
    cursor = conn.cursor()
    
    # Calculate Last 7 Days dates
    today = datetime.now().date()
    seven_days_ago = today - timedelta(days=7)
    
    # We want valid data from the last 7 days window
    start_str = seven_days_ago.isoformat()
    end_str = today.isoformat()
    
    # 1. Current RHR (Avg of last 7 days from resting_hr)
    cursor.execute("""
        SELECT AVG(resting_heart_rate) 
        FROM resting_hr 
        WHERE day BETWEEN ? AND ?
    """, (start_str, end_str))
    current_rhr = cursor.fetchone()[0]
    
    # 2. Current HRV (Avg of last 7 days from hrv.last_night_avg)
    cursor.execute("""
        SELECT AVG(last_night_avg) 
        FROM hrv 
        WHERE day BETWEEN ? AND ?
    """, (start_str, end_str))
    current_hrv = cursor.fetchone()[0]
    
    # 3. Current RR (Avg of last 7 days from daily_summary.rr_waking_avg)
    cursor.execute("""
        SELECT AVG(rr_waking_avg) 
        FROM daily_summary 
        WHERE day BETWEEN ? AND ?
    """, (start_str, end_str))
    current_rr = cursor.fetchone()[0]
    
    # Handle missing data cleanly
    if current_rhr is None: current_rhr = BASELINE_RHR # Neutral fallback
    if current_hrv is None: current_hrv = BASELINE_HRV
    if current_rr is None: current_rr = BASELINE_RR

    # --- Calculation Logic ---
    
    # RHR Score (Lower is better)
    # Deduct 4 points per 1 bpm dev
    rhr_diff = current_rhr - BASELINE_RHR
    if rhr_diff <= 0:
        rhr_score = 100
    else:
        rhr_score = max(0, 100 - (rhr_diff * 4))
        
    # HRV Score (Higher is better)
    # Deduct 3 points per 1 ms dev
    hrv_diff = BASELINE_HRV - current_hrv
    if hrv_diff <= 0:
        hrv_score = 100
    else:
        hrv_score = max(0, 100 - (hrv_diff * 3))
        
    # RR Score (Lower is better)
    # Deduct 10 points per 1 brpm dev
    rr_diff = current_rr - BASELINE_RR
    if rr_diff <= 0:
        rr_score = 100
    else:
        rr_score = max(0, 100 - (rr_diff * 10))
        
    # Weighted Final Score
    final_score = (rhr_score * WEIGHT_RHR) + (hrv_score * WEIGHT_HRV) + (rr_score * WEIGHT_RR)
    
    return {
        "total_score": round(final_score, 1),
        "metrics": {
            "rhr": {
                "current": round(current_rhr, 1),
                "baseline": BASELINE_RHR,
                "score": round(rhr_score, 1),
                "weight": "40%"
            },
            "hrv": {
                "current": round(current_hrv, 1),
                "baseline": BASELINE_HRV,
                "score": round(hrv_score, 1),
                "weight": "40%"
            },
            "rr": {
                "current": round(current_rr, 1),
                "baseline": BASELINE_RR,
                "score": round(rr_score, 1),
                "weight": "20%"
            }
        }
    }

if __name__ == "__main__":
    print(f"--- Golden Baseline Values ---")
    print(f"Baseline RHR: {BASELINE_RHR} bpm")
    print(f"Baseline HRV: {BASELINE_HRV} ms")
    print(f"Baseline RR:  {BASELINE_RR} brpm")
    print("-" * 30)
    
    try:
        conn = get_db_connection(DB_PATH)
        result = get_recovery_score(conn)
        conn.close()
        
        print("\n--- Current Recovery Status (Last 7 Days) ---")
        print(f"Recovery Score: {result['total_score']}%")
        print("\nDetails:")
        m = result['metrics']
        print(f"RHR: {m['rhr']['current']} bpm (Score: {m['rhr']['score']})")
        print(f"HRV: {m['hrv']['current']} ms  (Score: {m['hrv']['score']})")
        print(f"RR:  {m['rr']['current']} brpm (Score: {m['rr']['score']})")
        
    except Exception as e:
        print(f"Error: {e}")
