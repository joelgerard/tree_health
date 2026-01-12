import sqlite3
import pandas as pd
import os
from datetime import datetime, timedelta

# --- Configuration ---
DB_DIR = os.path.expanduser("~/GarminDBSync/tree/HealthData/DBs")
GLUCOSE_DB = os.path.join(DB_DIR, "glucose.db")
GARMIN_MON_DB = os.path.join(DB_DIR, "garmin_monitoring.db")
GARMIN_ACT_DB = os.path.join(DB_DIR, "garmin_activities.db")

def get_db_connection(db_path):
    if not os.path.exists(db_path):
        # Activity DB might be optional if file sync didn't pull it yet
        if "garmin_activities.db" in db_path:
             return None
        print(f"Error: DB not found at {db_path}")
        return None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        print(f"Error connecting to {db_path}: {e}")
        return None

def main():
    print("Analyze Sleep Cost: The Heart Rate Tax")
    print("Correlating Glucose Instability with Overnight Heart Rate Stress.")
    print("-" * 60)

    # 1. Database Connections
    conn_glucose = get_db_connection(GLUCOSE_DB)
    # We open connections per need or keep them open? keeping open is fine for script.
    # Actually let's open them fresh in the loop or functions to avoid timeouts if long run?
    # Script is short. Keep open.
    
    if not conn_glucose:
        print("Error: Could not connect to Glucose DB.")
        return

    # 2. The Data Loop
    try:
        dates_query = "SELECT DISTINCT date(timestamp) as day FROM glucose_readings ORDER BY day"
        dates_df = pd.read_sql_query(dates_query, conn_glucose)
        glucose_dates = pd.to_datetime(dates_df['day']).dt.date.tolist()
    except Exception as e:
        print(f"Error fetching dates from glucose DB: {e}")
        return

    print(f"Found {len(glucose_dates)} days of glucose data.")
    
    results = []

    for target_date in glucose_dates:
        # Window: Previous Day 22:00 to Current Day 08:00
        prev_night = target_date - timedelta(days=1)
        window_start = datetime.combine(prev_night, datetime.min.time()).replace(hour=22)
        window_end = datetime.combine(target_date, datetime.min.time()).replace(hour=8)
        
        # A. Glucose Stability
        g_query = f"""
            SELECT glucose_value 
            FROM glucose_readings 
            WHERE timestamp >= '{window_start}' AND timestamp <= '{window_end}'
        """
        try:
            g_df = pd.read_sql_query(g_query, conn_glucose)
        except Exception:
            continue

        if len(g_df) < 10:
            continue

        g_std = g_df['glucose_value'].std()
        if pd.isna(g_std):
            g_std = 0

        # B. Average Overnight Heart Rate (Prioritize High Res)
        # Fetch Low Res
        hr_df_low = pd.DataFrame()
        conn_mon = get_db_connection(GARMIN_MON_DB)
        if conn_mon:
            hr_query = f"""
                SELECT timestamp, heart_rate
                FROM monitoring_hr
                WHERE timestamp >= '{window_start}' AND timestamp <= '{window_end}'
            """
            try:
                hr_df_low = pd.read_sql_query(hr_query, conn_mon)
            except Exception: pass
            conn_mon.close()

        # Fetch High Res
        hr_df_high = pd.DataFrame()
        conn_act = get_db_connection(GARMIN_ACT_DB)
        if conn_act:
            act_query = f"""
                SELECT timestamp, hr as heart_rate
                FROM activity_records
                WHERE timestamp >= '{window_start}' AND timestamp <= '{window_end}' AND hr IS NOT NULL
            """
            try:
                hr_df_high = pd.read_sql_query(act_query, conn_act)
            except Exception: pass
            conn_act.close()

        # Combine
        # Standardize columns
        if not hr_df_low.empty:
            hr_df_low['timestamp'] = pd.to_datetime(hr_df_low['timestamp'])
            hr_df_low = hr_df_low.set_index('timestamp')
        
        if not hr_df_high.empty:
            hr_df_high['timestamp'] = pd.to_datetime(hr_df_high['timestamp'])
            hr_df_high = hr_df_high.set_index('timestamp')

        # Concat
        hr_combined = pd.concat([hr_df_high, hr_df_low])
        
        if hr_combined.empty:
            avg_hr = None
        else:
            # Drop duplicates if any (High Res prioritized if we sorted/deduped, but for *Average*, 
            # simply taking the mean of all points usually works unless extreme overlap bias.
            # E.g. 1 minute has 60 high res points and 1 low res point. 
            # The 60 points weigh 60x more. This IS what we want for accurate time-weighted average!
            # (Assuming Low Res is an instantaneous sample or 1-min avg? It's usually avg or sample at minute start).
            # If Low Res is sample-at-minute, and High Res is sample-every-second, 
            # then combining them gives 61 samples for that minute. 
            # Average is fine.
            # Actually, duplicate timestamps should be removed to avoid double counting the *same* moment.
            # But High Res timestamps are 12:00:01, 12:00:02... Low Res is 12:00:00.
            # They don't usually clash exactly on the millisecond/second unless lucky.
            # Just averaging all available data points is a robust way to get "Average HR".
            avg_hr = hr_combined['heart_rate'].mean()

        if avg_hr is not None:
            results.append({
                "Date": target_date,
                "Glucose_StdDev": g_std,
                "Avg_Sleep_HR": avg_hr
            })

    # 3. The Analysis
    if not results:
        print("No paired Glucose/HR data found.")
        conn_glucose.close()
        return

    df = pd.DataFrame(results)
    
    # Sort by Stability
    df = df.sort_values('Glucose_StdDev', ascending=True)

    # 4. The Report
    print("\n" + "="*80)
    print(f"{'TOP 5 MOST STABLE (Flat Glucose)':<40} | {'BOTTOM 5 LEAST STABLE (Spiky Glucose)':<40}")
    print("="*80)
    print(f"{'Date':<12} | {'SD':<6} | {'Avg HR':<8}   | {'Date':<12} | {'SD':<6} | {'Avg HR':<8}")
    print("-" * 80)

    top_5 = df.head(5).reset_index(drop=True)
    bottom_5 = df.tail(5).sort_values('Glucose_StdDev', ascending=False).reset_index(drop=True)
    
    max_len = max(len(top_5), len(bottom_5))
    
    for i in range(max_len):
        t_str = ""
        b_str = ""
        
        if i < len(top_5):
            r = top_5.iloc[i]
            t_str = f"{r['Date']}   | {r['Glucose_StdDev']:.1f}   | {r['Avg_Sleep_HR']:.1f} bpm"
            
        if i < len(bottom_5):
            r = bottom_5.iloc[i]
            b_str = f"{r['Date']}   | {r['Glucose_StdDev']:.1f}   | {r['Avg_Sleep_HR']:.1f} bpm"
            
        print(f"{t_str:<40} | {b_str:<40}")

    print("-" * 80)

    # Correlation
    if len(df) > 2:
        corr = df['Glucose_StdDev'].corr(df['Avg_Sleep_HR'])
        print(f"\nCorrelation between Glucose Stability (SD) and Avg Sleep HR: r = {corr:.3f}")
        if corr > 0:
             print("Hypothesis SUPPORTED: Spiky Glucose is correlated with Higher Overnight Heart Rate.")
        else:
             print("Hypothesis NOT Supported (Negative or Zero Correlation).")
    else:
        print("\nNot enough data points for correlation.")

    conn_glucose.close()

if __name__ == "__main__":
    main()
