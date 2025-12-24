import sqlite3
import os
import math

DB_PATH = "/Users/joelgerard/Library/CloudStorage/GoogleDrive-joelgerard@gmail.com/My Drive/joel health/tree health/DBs/garmin.db"
START_DATE = '2025-03-01'
END_DATE = '2025-05-31'

def get_stats():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # RHR from resting_hr (Mean & SD)
        cursor.execute("SELECT resting_heart_rate FROM resting_hr WHERE day BETWEEN ? AND ?", (START_DATE, END_DATE))
        rhr_rows = cursor.fetchall()
        rhr_values = [r['resting_heart_rate'] for r in rhr_rows if r['resting_heart_rate'] is not None]
        
        if rhr_values:
            rhr_mean = sum(rhr_values) / len(rhr_values)
            rhr_variance = sum((x - rhr_mean) ** 2 for x in rhr_values) / len(rhr_values)
            rhr_sd = math.sqrt(rhr_variance)
        else:
            rhr_mean = 0
            rhr_sd = 0

        # Stress from daily_summary (Mean)
        cursor.execute("SELECT stress_avg FROM daily_summary WHERE day BETWEEN ? AND ?", (START_DATE, END_DATE))
        stress_rows = cursor.fetchall()
        stress_values = [r['stress_avg'] for r in stress_rows if r['stress_avg'] is not None and r['stress_avg'] > 0]
        
        if stress_values:
            stress_mean = sum(stress_values) / len(stress_values)
        else:
            stress_mean = 0

        # HRV from hrv (Mean)
        cursor.execute("SELECT last_night_avg FROM hrv WHERE day BETWEEN ? AND ?", (START_DATE, END_DATE))
        hrv_rows = cursor.fetchall()
        hrv_values = [r['last_night_avg'] for r in hrv_rows if r['last_night_avg'] is not None]
        
        if hrv_values:
            hrv_mean = sum(hrv_values) / len(hrv_values)
        else:
            hrv_mean = 0
            
        # RR from daily_summary (Mean) - just in case
        cursor.execute("SELECT rr_waking_avg FROM daily_summary WHERE day BETWEEN ? AND ?", (START_DATE, END_DATE))
        rr_rows = cursor.fetchall()
        rr_values = [r['rr_waking_avg'] for r in rr_rows if r['rr_waking_avg'] is not None]
        
        if rr_values:
            rr_mean = sum(rr_values) / len(rr_values)
        else:
            rr_mean = 0

        print(f"RHR_MEAN={rhr_mean:.4f}")
        print(f"RHR_SD={rhr_sd:.4f}")
        print(f"STRESS_MEAN={stress_mean:.4f}")
        print(f"HRV_MEAN={hrv_mean:.4f}")
        print(f"RR_MEAN={rr_mean:.4f}")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    get_stats()
