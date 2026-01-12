import sqlite3
import pandas as pd
import matplotlib.pyplot as plt
import os
from datetime import datetime, timedelta

# --- Configuration ---
DB_DIR = os.path.expanduser("~/GarminDBSync/tree/HealthData/DBs")
GLUCOSE_DB = os.path.join(DB_DIR, "glucose.db")
GARMIN_MONITORING_DB = os.path.join(DB_DIR, "garmin_monitoring.db")
GARMIN_ACTIVITIES_DB = os.path.join(DB_DIR, "garmin_activities.db")

# Analyze last 14 days by default
DAYS_TO_ANALYZE = 14
END_DATE = datetime.now()
START_DATE = END_DATE - timedelta(days=DAYS_TO_ANALYZE)

print(f"Analysis Range: {START_DATE.date()} to {END_DATE.date()}")

def get_db_connection(db_path):
    if not os.path.exists(db_path):
        print(f"Error: DB not found at {db_path}")
        return None
    return sqlite3.connect(db_path)

def load_glucose():
    print("Loading Glucose...")
    conn = get_db_connection(GLUCOSE_DB)
    if not conn: return pd.DataFrame()
    
    query = f"""
        SELECT timestamp, glucose_value 
        FROM glucose_readings 
        WHERE timestamp >= '{START_DATE}'
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if df.empty:
        print("Warning: No glucose data found.")
        return df

    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values('timestamp')
    df = df.set_index('timestamp')
    
    # Resample to 5T (5 mins) and ffill as requested
    df = df.resample('5min').mean()
    df['glucose_value'] = df['glucose_value'].ffill()
    
    return df

def load_heart_rate():
    """
    Loads HR from TWO sources:
    1. Low Res: monitoring_hr (24/7, ~1 min)
    2. High Res: activity_records (Workouts, 1 sec) -> Prioritized
    """
    print("Loading Heart Rate (Dual Source)...")
    
    # 1. Low Res (Monitoring)
    df_low = pd.DataFrame()
    conn_mon = get_db_connection(GARMIN_MONITORING_DB)
    if conn_mon:
        query_mon = f"""
            SELECT timestamp, heart_rate 
            FROM monitoring_hr 
            WHERE timestamp >= '{START_DATE}'
        """
        df_low = pd.read_sql_query(query_mon, conn_mon)
        conn_mon.close()
        
    if not df_low.empty:
        df_low['timestamp'] = pd.to_datetime(df_low['timestamp'])
        df_low = df_low.set_index('timestamp')
        df_low['source'] = 'LOW_RES'

    # 2. High Res (Activities)
    df_high = pd.DataFrame()
    conn_act = get_db_connection(GARMIN_ACTIVITIES_DB)
    if conn_act:
        query_act = f"""
            SELECT timestamp, hr as heart_rate 
            FROM activity_records 
            WHERE timestamp >= '{START_DATE}' AND hr IS NOT NULL
        """
        try:
            df_high = pd.read_sql_query(query_act, conn_act)
        except Exception: 
            pass # Table might not exist or be empty
        conn_act.close()
        
    if not df_high.empty:
        df_high['timestamp'] = pd.to_datetime(df_high['timestamp'])
        df_high = df_high.set_index('timestamp')
        df_high['source'] = 'HIGH_RES'
        
    # 3. Merge Strategy
    # We want a single stream. If High Res exists for a given timestamp (or close to it), use it.
    # Otherwise use Low Res.
    # Actually, we can just concatenate and sort.
    # BUT, we are resampling to 5-min for Tremor Logic matching (Glucose 5-min).
    # IF we resample, High Res allows for a MUCH better "Average" or "Max" within that window.
    # Let's combine raw frames first.
    
    # Combine
    df_combined = pd.concat([df_high, df_low])
    
    # Sort
    df_combined = df_combined.sort_index()
    
    # Dedup? If duplicate timestamps exist, prefer HIGH_RES.
    # "source" column helps?
    # sort by timestamp asc, source asc (HIGH_RES < LOW_RES alphabetically? No H < L).
    # Actually 'HIGH_RES' comes before 'LOW_RES'.
    # So if we drop_duplicates(keep='first'), we keep HIGH_RES.
    df_combined = df_combined.reset_index().drop_duplicates(subset=['timestamp'], keep='first').set_index('timestamp')
    
    print(f"Loaded {len(df_combined)} HR points ({len(df_high)} High Res).")
    
    # Resample to 5T
    # We take the MEAN of the 5-min window.
    # This effectively uses high-res density to give a better average.
    df_resampled = df_combined[['heart_rate']].resample('5min').mean()
    
    return df_resampled

def load_steps():
    print("Loading Steps...")
    conn = get_db_connection(GARMIN_MONITORING_DB)
    if not conn: return pd.DataFrame()
    
    # Steps are in 'monitoring' table
    query = f"""
        SELECT timestamp, steps 
        FROM monitoring 
        WHERE timestamp >= '{START_DATE}'
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if df.empty:
        print("Warning: No Steps data found.")
        return df

    df['timestamp'] = pd.to_datetime(df['timestamp'])
    # Group by timestamp first to sum steps if there are duplicates
    df = df.groupby('timestamp')['steps'].sum().reset_index()
    
    df = df.sort_values('timestamp')
    df = df.set_index('timestamp')
    
    # Resample to 5T - SUM steps
    df = df.resample('5min').sum()
    
    return df

def get_daily_rhr_map(hr_df):
    """
    Calculate rough Daily RHR from min HR of the day.
    """
    # Resample to 1 day, taking the minimum
    daily_min = hr_df['heart_rate'].resample('D').min()
    daily_min = daily_min.ffill()
    return daily_min

def main():
    # 1. Ingestion
    df_glucose = load_glucose()
    df_hr = load_heart_rate()
    df_steps = load_steps()
    
    if df_glucose.empty or df_hr.empty:
        print("Critical Data Missing. Aborting.")
        return

    # 2. Alignment
    print("Aligning Data...")
    
    # We focus on rows where we have Glucose AND HR.
    df = df_glucose.join(df_hr, how='inner', lsuffix='_g', rsuffix='_h')
    df = df.join(df_steps, how='left')
    
    # Fill NaN steps with 0
    df['steps'] = df['steps'].fillna(0)
    
    # Calculate ROC
    df['glucose_roc'] = df['glucose_value'].diff()
    
    # Calculate Daily RHR for thresholds
    daily_rhr_series = get_daily_rhr_map(df_hr)
    
    # Map Daily RHR
    df['date'] = df.index.date
    rhr_map = {ts.date(): rhr for ts, rhr in daily_rhr_series.items()}
    df['daily_rhr'] = df['date'].map(rhr_map)
    df['daily_rhr'] = df['daily_rhr'].ffill().bfill()
    
    # 3. Adrenaline Dump Logic
    print("Running Detection Logic...")
    
    # Thresholds
    cond_glucose_drop = (df['glucose_roc'] < -1.5) | (df['glucose_value'] < 85)
    cond_hr_elevated = df['heart_rate'] > (df['daily_rhr'] + 15)
    cond_low_movement = df['steps'] < 50
    
    df['is_tremor'] = cond_glucose_drop & cond_hr_elevated & cond_low_movement
    
    tremors = df[df['is_tremor']].copy()
    tremors = tremors.sort_values('glucose_roc') # Most negative first
    
    # 4. Output Report
    print(f"\nFound {len(tremors)} Potential Tremor Events:")
    print("-" * 60)
    
    for ts, row in tremors.head(30).iterrows():
        g_current = row['glucose_value']
        g_prev = g_current - row['glucose_roc']
        roc = row['glucose_roc']
        hr = row['heart_rate']
        steps = row['steps']
        print(f"[{ts}] Glucose: {g_prev:.1f} -> {g_current:.1f} ({roc:.1f}) | HR: {hr:.0f}bpm | Steps: {steps:.0f} | TYPE: METABOLIC CRASH")

    # 4b. Evening Crash Report (Dinner Spike Analysis)
    print("\n" + "="*60)
    print("EVENING CRASH REPORT (6 PM - 9 PM) - The 'Dinner Spike' Check")
    print("="*60)
    
    evening_tremors = tremors[tremors.index.hour.isin([18, 19, 20])]
    
    if evening_tremors.empty:
        print("No tremors detected in the evening window (18:00 - 21:00).")
    else:
        # Sort by time for easier reading in evening report? Or severity?
        # Let's sort by time within the evening list
        evening_tremors = evening_tremors.sort_index()
        
        for ts, row in evening_tremors.iterrows():
            g_current = row['glucose_value']
            g_prev = g_current - row['glucose_roc']
            roc = row['glucose_roc']
            hr = row['heart_rate']
            steps = row['steps']
            print(f"[{ts}] Glucose Drop: {g_prev:.1f} -> {g_current:.1f} ({roc:.1f}) | HR: {hr:.0f} | Steps: {steps:.0f}")
    print("-" * 60)

    # 5. Scatter Plot
    print("\nGenerating Scatter Plot...")
    plt.figure(figsize=(10, 6))
    
    plot_df = df.dropna(subset=['glucose_roc', 'heart_rate', 'steps'])
    
    # Colors: Red if steps<10, Blue if >=10
    colors = ['red' if s < 10 else 'blue' for s in plot_df['steps']]
    
    plt.scatter(plot_df['glucose_roc'], plot_df['heart_rate'], c=colors, alpha=0.5, s=15)
    
    plt.axvline(x=0, color='gray', linestyle='--', alpha=0.3)
    # Average threshold line (approx)
    avg_thresh = plot_df['daily_rhr'].mean() + 15
    plt.axhline(y=avg_thresh, color='orange', linestyle='--', label=f'Avg Threshold ({avg_thresh:.0f} bpm)')
    
    plt.title('Glucose ROC vs Heart Rate (Hypoglycemia Tremor Analysis)')
    plt.xlabel('Glucose Rate of Change (mg/dL per 5 min)')
    plt.ylabel('Heart Rate (bpm)')
    plt.legend(['Zero ROC', 'Threshold'])
    
    out_file = "glucose_vs_hr_tremors.png"
    plt.savefig(out_file)
    print(f"Plot saved to {out_file}")

if __name__ == "__main__":
    main()
