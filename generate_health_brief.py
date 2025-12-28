import sqlite3
import pandas as pd
import os
from datetime import datetime, timedelta
import sys

import argparse

# Configuration
DEFAULT_DB_DIR = os.path.expanduser("~/HealthData/DBs")
TARGET_SLEEP_HOURS = 8.0

def get_db_connection(db_path):
    if not os.path.exists(db_path):
        print(f"Error: Database not found at {db_path}")
        sys.exit(1)
    return sqlite3.connect(db_path)

def parse_duration(duration_str):
    """Parses 'HH:MM:SS.ssssss' or 'HH:MM:SS' into hours (float)."""
    if not duration_str:
        return 0.0
    try:
        # Handle cases with or without microseconds
        if '.' in duration_str:
            t = datetime.strptime(duration_str, "%H:%M:%S.%f")
        else:
            t = datetime.strptime(duration_str, "%H:%M:%S")
        hours = t.hour + t.minute / 60.0 + t.second / 3600.0
        return hours
    except ValueError:
        return 0.0

def load_data(db_dir, limit=10):
    garmin_db = os.path.join(db_dir, "garmin.db")
    conn = get_db_connection(garmin_db)
    
    # Load separate tables
    # We need last ~10 days to calculate 7 day window trends/lags safely
    query_limit = limit
    
    # daily_summary
    df_daily = pd.read_sql_query(f"""
        SELECT day, rhr, steps, calories_active, bb_max
        FROM daily_summary 
        ORDER BY day DESC LIMIT {query_limit}
    """, conn)
    
    # sleep
    df_sleep = pd.read_sql_query(f"""
        SELECT day, total_sleep, score
        FROM sleep 
        ORDER BY day DESC LIMIT {query_limit}
    """, conn)
    
    # hrv
    df_hrv = pd.read_sql_query(f"""
        SELECT day, last_night_avg as hrv_avg
        FROM hrv 
        ORDER BY day DESC LIMIT {query_limit}
    """, conn)
    
    conn.close()
    
    # Merge
    # Ensure day is datetime for merging/sorting
    df_daily['day'] = pd.to_datetime(df_daily['day']).dt.date
    df_sleep['day'] = pd.to_datetime(df_sleep['day']).dt.date
    df_hrv['day'] = pd.to_datetime(df_hrv['day']).dt.date
    
    df = pd.merge(df_daily, df_sleep, on='day', how='outer')
    df = pd.merge(df, df_hrv, on='day', how='outer')
    
    # Sort descending
    df = df.sort_values(by='day', ascending=False).reset_index(drop=True)
    
    return df

def generate_report(db_dir, days=7):
    # Fetch enough data for trends/lags (days + buffer)
    query_limit = days + 5
    df = load_data(db_dir, limit=query_limit)
    
    if df.empty:
        print("No data found.")
        return

    # --- Pre-processing & Computations ---
    
    # Parse sleep
    df['sleep_hours'] = df['total_sleep'].apply(parse_duration)
    
    # Physio Cost: (Active Calories / Steps * 1000)
    # Avoid division by zero
    df['physio_cost'] = df.apply(
        lambda row: (row['calories_active'] / row['steps'] * 1000) if row['steps'] and row['steps'] > 0 else 0, axis=1
    )
    
    # Shifted values for comparison (Previous Day)
    # Since df is sorted DESC (Today is index 0, Yesterday is index 1)
    df['prev_rhr'] = df['rhr'].shift(-1)
    df['prev_bb_max'] = df['bb_max'].shift(-1)
    df['prev_hrv'] = df['hrv_avg'].shift(-1)
    df['prev_sleep_hours'] = df['sleep_hours'].shift(-1)
    
    # Deltas
    # Delta RHR: Today - Yesterday
    df['delta_rhr'] = df['rhr'] - df['prev_rhr']
    # Delta Battery
    df['delta_bb'] = df['bb_max'] - df['prev_bb_max']
    # Sleep Trend (Delta Duration)
    df['delta_sleep'] = df['sleep_hours'] - df['prev_sleep_hours']
    
    # Lag-2 Steps (Steps from 2 days ago)
    # Today is index 0. T-2 is index 2.
    # We can map it by shifting -2
    df['lag_2_steps'] = df['steps'].shift(-2)
    
    # Sleep Debt: Last 3 days average vs 8.0 hours
    # Rolling average in Pandas requires ascending order usually for 'window' to look back, 
    # but here we have DESC. window=3 on DESC means current row + 2 FUTURE rows (which are past dates).
    # So actually rolling(3) on DESC df starting at index 0 covers index 0, 1, 2 (Today, Yesterday, Day Before).
    # That is exactly "Last 3 days average" including today.
    # The requirement says "Last 3 days average". Usually implies T, T-1, T-2.
    indexer = pd.api.indexers.FixedForwardWindowIndexer(window_size=3)
    df['sleep_3d_avg'] = df['sleep_hours'].rolling(window=indexer, min_periods=1).mean()
    df['sleep_debt'] = 8.0 - df['sleep_3d_avg']

    # --- Current Snapshot (Today) ---
    today = df.iloc[0]
    
    # --- Logic & Status Determination ---
    
    # 1. Calc Trends Numeric
    # Compare Today (idx 0) vs Start of Period (idx days-1)
    trend_window_days = days if days > 1 else 1 
    compare_idx = days - 1 if days > 1 else 1
    if compare_idx >= len(df):
        compare_idx = len(df) - 1
    has_trend_data = (len(df) > compare_idx) and (compare_idx > 0)
    prev_period = df.iloc[compare_idx] if has_trend_data else None
    
    # Numeric Deltas for Logic
    bb_delta = (today['bb_max'] - prev_period['bb_max']) if (has_trend_data and pd.notnull(prev_period['bb_max']) and pd.notnull(today['bb_max'])) else 0
    
    # 2. Define Flags
    # RED (CRITICAL STOP)
    is_critical_battery = (today['bb_max'] < 30) if pd.notnull(today['bb_max']) else False
    is_metabolic_freeze = (today['rhr'] < 48) if pd.notnull(today['rhr']) else False
    is_lag_2_risk = (today['lag_2_steps'] > 5000) if pd.notnull(today['lag_2_steps']) else False
    
    # YELLOW (CAUTION)
    # Body Battery between 30 and 50
    is_low_battery = (today['bb_max'] >= 30 and today['bb_max'] <= 50) if pd.notnull(today['bb_max']) else False
    # Crashing Trend: Drop > 10%
    is_crashing_trend = (bb_delta <= -10)
    # High Cost
    is_high_cost = (today['physio_cost'] > 35) if pd.notnull(today['physio_cost']) else False
    # Low HRV
    is_low_hrv = (today['hrv_avg'] < 45) if pd.notnull(today['hrv_avg']) else False
    
    # 3. Determine Verdict
    status_light = "GREEN"
    primary_driver = "Stable Trend"
    
    # Check RED first
    if is_critical_battery:
        status_light = "RED"
        primary_driver = f"Critical Battery ({int(today['bb_max'])}%)"
    elif is_metabolic_freeze:
        status_light = "RED"
        primary_driver = f"Metabolic Freeze ({int(today['rhr'])} bpm)"
    elif is_lag_2_risk:
        status_light = "RED"
        primary_driver = f"Lag-2 Overload ({int(today['lag_2_steps'])} steps)"
        
    # Check YELLOW if not RED
    elif is_low_battery:
        status_light = "YELLOW"
        primary_driver = f"Functional Insolvency ({int(today['bb_max'])}%)"
    elif is_crashing_trend:
        status_light = "YELLOW"
        primary_driver = f"Crashing Trend ({int(bb_delta)}% drop)"
    elif is_high_cost:
        status_light = "YELLOW"
        primary_driver = f"Inefficient Day (Cost {today['physio_cost']:.1f})"
    elif is_low_hrv:
        status_light = "YELLOW"
        primary_driver = f"Nervous System Stuck (HRV {int(today['hrv_avg'])})"
        
    # Formatting Helpers
    def fmt_trend(curr, prev, label_up="UP", label_down="DOWN", label_flat="FLAT", positive_is_good=True, is_rhr=False):
        return "" # Using custom blocks

    # --- Output ---
    days_label = f"{days} Days" if days > 1 else "48h"
    print(f"=== CURRENT STATUS ({today['day']}) ===")
    print(f"STATUS_LIGHT: [{status_light}]")
    print(f"PRIMARY_DRIVER: [{primary_driver}]")
    print("")
    print(f"=== TREND ANALYSIS (Last {days_label}) ===")
    
    # RHR Trend
    rhr_trend_str = "N/A"
    if has_trend_data and pd.notnull(today['rhr']) and pd.notnull(prev_period['rhr']):
        diff = today['rhr'] - prev_period['rhr']
        if abs(diff) < 2:
            rhr_trend_str = f"STABLE ({int(diff):+d} bpm)"
        elif diff > 0:
            rhr_trend_str = f"RISING ({int(diff):+d} bpm)"
        else:
            rhr_trend_str = f"DROPPING ({int(diff):+d} bpm)"

    # HRV Trend
    hrv_trend_str = "N/A"
    if has_trend_data and pd.notnull(today['hrv_avg']) and pd.notnull(prev_period['hrv_avg']):
        diff = today['hrv_avg'] - prev_period['hrv_avg']
        p = int(prev_period['hrv_avg'])
        c = int(today['hrv_avg'])
        if abs(diff) < 2:
            hrv_trend_str = f"FLAT ({p} -> {c})"
        elif diff > 0:
            hrv_trend_str = f"RISING ({p} -> {c})"
        else:
            hrv_trend_str = f"DROPPING ({p} -> {c})"
            
    # Battery Trend (Using computed bb_delta)
    batt_trend_str = "N/A"
    if has_trend_data and pd.notnull(today['bb_max']) and pd.notnull(prev_period['bb_max']):
        suffix = f"over {days_label}" if days > 1 else "from yesterday"
        if bb_delta <= -20:
            batt_trend_str = f"CRASHING ({int(bb_delta)}% {suffix})"
        elif bb_delta < -5:
            batt_trend_str = f"DRAINING ({int(bb_delta)}% {suffix})"
        elif bb_delta > 5:
            batt_trend_str = f"CHARGING ({int(bb_delta):+d}% {suffix})"
        else:
            batt_trend_str = f"STABLE ({int(bb_delta):+d}%)"

    # Sleep Trend
    sleep_trend_str = "N/A"
    if has_trend_data and pd.notnull(today['sleep_hours']) and pd.notnull(prev_period['sleep_hours']):
        diff = today['sleep_hours'] - prev_period['sleep_hours']
        if diff > 1.5:
            sleep_trend_str = f"REBOUND ({diff:+.1f}h duration)"
        elif diff < -1.5:
             sleep_trend_str = f"DEPRIVATION ({diff:+.1f}h duration)"
        else:
             sleep_trend_str = f"STEADY ({diff:+.1f}h)"
             
    print(f"RHR_TREND:      {rhr_trend_str}")
    print(f"HRV_TREND:      {hrv_trend_str}")
    print(f"BATTERY_TREND:  {batt_trend_str}")
    print(f"SLEEP_TREND:    {sleep_trend_str}")
    print("")
    print("=== RISK FLAGS ===")
    print(f"[{'X' if is_critical_battery else ' '}] Critical Battery (<30)")
    print(f"[{'X' if is_metabolic_freeze else ' '}] Metabolic Freeze (<48 bpm)")
    print(f"[{'X' if is_lag_2_risk else ' '}] Lag-2 Overload (>5k steps 48h ago)")
    print(f"[{'X' if is_low_battery else ' '}] Functional Insolvency (30-50%)")
    print(f"[{'X' if is_crashing_trend else ' '}] Crashing Trend (>10% drop)")
    print(f"[{'X' if is_high_cost else ' '}] High Cost Day (>35 cost)")
    print(f"[{'X' if is_low_hrv else ' '}] Nervous System Stuck (HRV < 45)")
    print("")
    print(f"=== DATA TABLE (Last {days} Days) ===")
    print("Day        | RHR | HRV | Batt | Sleep | Steps | Cost")
    print("----------------------------------------------------")
    
    # Print last N days
    for i in range(days):
        if i >= len(df):
            break
        row = df.iloc[i]
        
        d_str = str(row['day'])
        rhr = int(row['rhr']) if pd.notnull(row['rhr']) else "N/A"
        hrv = int(row['hrv_avg']) if pd.notnull(row['hrv_avg']) else "N/A"
        batt = int(row['bb_max']) if pd.notnull(row['bb_max']) else "N/A"
        
        sleep_val = "N/A"
        if pd.notnull(row['sleep_hours']):
            # Convert decimal hours to e.g. 7.5 -> maybe user wants minutes? 
            # Example shows "88" -> 88 what? 88 sleep score? Or 88 hours? No.
            # Example: "Sleep | 88". 
            # Ah, maybe sleep SCORE?
            # Requirement 2 says "Sleep Debt: (Last 3 days average vs 8.0 hours)".
            # But the table example shows "Sleep | 88", "Sleep | 63".
            # Those look like Sleep SCORES, not duration.
            # But "Computations" asked for "Sleep Debt".
            # If the user wants the TABLE to match the example, I should probably print Sleep SCORE in the table if available.
            # Schema had `sleep_score`?
            # Let's check schema for `sleep` table again. 
            # `score INTEGER`.
            # I should fetch `score` too!
            pass
        
        # Re-fetching sleep score to be safe or just printing duration if score missing?
        # User REQ 2: "Sleep Debt: (Last 3 days average vs 8.0 hours)". 
        # User REQ 4: "Sleep | 88". That is definitely a score.
        # I will modify load_data to fetch `score` too.
        
        steps = int(row['steps']) if pd.notnull(row['steps']) else "N/A"
        cost = f"{row['physio_cost']:.0f}" if pd.notnull(row['physio_cost']) and row['physio_cost'] > 0 else "--"
        
        # Let's pivot to fetch sleep score dynamically in this loop if I didn't fetch it, 
        # or better, Update load_data to fetch it. I will update load_data.
        # For now, I'll put a placeholder variable name and I will update the query above.
        
        # Wait, I am writing the file content right now. I can just edit the string I am writing.
        # I will scroll up and add `score` to `df_sleep` query.
        
        # Formatted line (using variables assuming I added score)
        # I will assume I added score to the query in the `load_data` function.
        
        # Update: I will modify the query string in `load_data` to `SELECT day, total_sleep, score ...`
        
        s_score = "N/A"
        if 'score' in row and pd.notnull(row['score']):
            s_score = int(row['score'])
        elif pd.notnull(row['sleep_hours']):
             # Fallback if no score but duration exists (unlikely in Garmin data if duration exists)
             s_score = f"{row['sleep_hours']:.1f}h"

        print(f"{d_str:10} | {str(rhr):3} | {str(hrv):3} | {str(batt):4} | {str(s_score):5} | {str(steps):5} | {cost}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Health Brief")
    parser.add_argument("-f", "--folder", type=str, default=DEFAULT_DB_DIR, help="Path to database folder")
    parser.add_argument("-n", "--days", type=int, default=7, help="Number of days to display")
    args = parser.parse_args()
    
    generate_report(args.folder, args.days)
