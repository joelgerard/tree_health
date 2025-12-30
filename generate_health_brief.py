# this shows trends for the AI

import sqlite3
import pandas as pd
import argparse
import os
import sys
from datetime import datetime, timedelta

def get_db_connection(db_path):
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        print(f"Error connecting to {db_path}: {e}")
        sys.exit(1)

def analyze_health(db_dir, num_days):
    garmin_db = os.path.join(db_dir, "garmin.db")
    
    if not os.path.exists(garmin_db):
        print(f"Error: Database not found at {garmin_db}")
        sys.exit(1)

    conn = get_db_connection(garmin_db)
    
    # Calculate date range
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=num_days + 1) # +1 for trend calculation
    
    query = """
    SELECT 
        d.day,
        d.rhr,
        d.steps,
        d.stress_avg,
        d.bb_charged,
        d.calories_active,
        h.last_night_avg as hrv,
        s.score as sleep_score,
        s.total_sleep_time
    FROM daily_summary d
    LEFT JOIN hrv h ON d.day = h.day
    LEFT JOIN sleep s ON d.day = s.day
    WHERE d.day >= ?
    ORDER BY d.day DESC
    """
    
    try:
        df = pd.read_sql_query(query, conn, params=(start_date.isoformat(),))
        conn.close()
    except Exception as e:
        print(f"Error executing query: {e}")
        sys.exit(1)

    if df.empty:
        print("No data found for the specified range.")
        return

    # Data Pre-processing
    df['cost'] = df.apply(lambda row: round((row['calories_active'] / row['steps'] * 1000), 1) if row['steps'] > 0 else 0, axis=1)
    
    # --- TREND ANALYSIS ---
    # We need at least 2 days for basic trends, more for 7-day
    if len(df) < 2:
        print("Insufficient data for trend analysis.")
        return

    today = df.iloc[0]
    yesterday = df.iloc[1]
    
    # Delta calculations
    rhr_delta = today['rhr'] - yesterday['rhr']
    batt_delta = today['bb_charged'] - yesterday['bb_charged']
    hrv_delta = (today['hrv'] if pd.notnull(today['hrv']) else 0) - (yesterday['hrv'] if pd.notnull(yesterday['hrv']) else 0)
    
    # 7-Day Battery Trend (Slope)
    batt_trend_msg = "STABLE"
    if len(df) >= 7:
        avg_last_3 = df.iloc[0:3]['bb_charged'].mean()
        avg_first_3 = df.iloc[-3:]['bb_charged'].mean()
        diff = avg_last_3 - avg_first_3
        if diff < -10:
            batt_trend_msg = f"DRAINING ({int(diff)}% over period)"
        elif diff > 10:
            batt_trend_msg = f"CHARGING (+{int(diff)}% over period)"
        else:
            batt_trend_msg = f"STABLE ({int(diff)}%)"
    else:
        batt_trend_msg = f"STABLE ({int(batt_delta)}% vs yesterday)"

    # --- RISK FLAGS ---
    flags = {
        "CRITICAL_BATTERY": False,
        "METABOLIC_FREEZE": False,
        "LAG_2_OVERLOAD": False,
        "FUNCTIONAL_INSOLVENCY": False,
        "CRASHING_TREND": False,
        "HIGH_COST": False,
        "NERVOUS_SYSTEM_STUCK": False,
        "INEFFICIENT_RECOVERY": False  # <--- NEW FLAG
    }
    
    # 1. Critical Battery (< 30)
    if today['bb_charged'] < 30:
        flags["CRITICAL_BATTERY"] = True
        
    # 2. Metabolic Freeze (RHR < 48)
    if today['rhr'] < 48:
        flags["METABOLIC_FREEZE"] = True
        
    # 3. Lag-2 Overload (Check 2 days ago)
    if len(df) >= 3:
        day_t2 = df.iloc[2]
        if day_t2['steps'] > 5000:
            flags["LAG_2_OVERLOAD"] = True
            
    # 4. Functional Insolvency (30-50%)
    if 30 <= today['bb_charged'] <= 50:
        flags["FUNCTIONAL_INSOLVENCY"] = True
        
    # 5. Crashing Trend
    if "DRAINING" in batt_trend_msg:
        flags["CRASHING_TREND"] = True
        
    # 6. High Cost (> 35)
    if today['cost'] > 35:
        flags["HIGH_COST"] = True
        
    # 7. Nervous System Stuck (HRV < 45)
    if pd.notnull(today['hrv']) and today['hrv'] < 45:
        flags["NERVOUS_SYSTEM_STUCK"] = True

    # 8. Inefficient Recovery (NEW)
    # Logic: Trying to rest (Steps < 2500) BUT Burning High Fuel (Cost > 35)
    if today['steps'] < 2500 and today['cost'] > 35:
        flags["INEFFICIENT_RECOVERY"] = True

    # --- STATUS LIGHT LOGIC ---
    status = "GREEN"
    primary_driver = "None"
    
    if flags["CRITICAL_BATTERY"] or flags["METABOLIC_FREEZE"] or flags["LAG_2_OVERLOAD"]:
        status = "RED"
        if flags["CRITICAL_BATTERY"]: primary_driver = f"Critical Battery ({int(today['bb_charged'])}%)"
        elif flags["METABOLIC_FREEZE"]: primary_driver = f"Metabolic Freeze ({int(today['rhr'])} bpm)"
        elif flags["LAG_2_OVERLOAD"]: primary_driver = "Lag-2 Overload Risk"
        
    elif flags["FUNCTIONAL_INSOLVENCY"] or flags["CRASHING_TREND"] or flags["HIGH_COST"] or flags["NERVOUS_SYSTEM_STUCK"]:
        status = "YELLOW"
        if flags["INEFFICIENT_RECOVERY"]: primary_driver = "Inefficient Recovery (Sensory Hangover)"
        elif flags["FUNCTIONAL_INSOLVENCY"]: primary_driver = f"Functional Insolvency ({int(today['bb_charged'])}%)"
        elif flags["HIGH_COST"]: primary_driver = f"High Cost Day ({today['cost']})"
        elif flags["CRASHING_TREND"]: primary_driver = "Battery Draining Trend"
        elif flags["NERVOUS_SYSTEM_STUCK"]: primary_driver = "Nervous System Stuck"

    # --- OUTPUT REPORT ---
    print(f"=== CURRENT STATUS ({today['day']}) ===")
    print(f"STATUS_LIGHT: [{status}]")
    print(f"PRIMARY_DRIVER: [{primary_driver}]")
    print("")
    print(f"=== TREND ANALYSIS (Last {num_days} Days) ===")
    
    rhr_trend_str = "STABLE"
    if rhr_delta > 0: rhr_trend_str = "RISING"
    elif rhr_delta < 0: rhr_trend_str = "FALLING"
    print(f"RHR_TREND:      {rhr_trend_str} ({int(rhr_delta):+d} bpm)")
    
    hrv_trend_str = "FLAT"
    if hrv_delta > 1: hrv_trend_str = "RISING"
    elif hrv_delta < -1: hrv_trend_str = "DROPPING"
    print(f"HRV_TREND:      {hrv_trend_str} ({int(yesterday['hrv'] if pd.notnull(yesterday['hrv']) else 0)} -> {int(today['hrv'] if pd.notnull(today['hrv']) else 0)})")
    
    print(f"BATTERY_TREND:  {batt_trend_msg}")
    
    # Sleep Trend
    avg_sleep = df['total_sleep_time'].mean() / 60 / 60 / 1000 if 'total_sleep_time' in df.columns else 0 # ms to hours
    # Simple check vs 8h
    sleep_diff = (today['total_sleep_time'] / 60 / 60 / 1000) - 8.0 if pd.notnull(today['total_sleep_time']) else 0
    print(f"SLEEP_TREND:    {'STEADY' if abs(sleep_diff) < 1 else ('REBOUND' if sleep_diff > 0 else 'DEBT')} ({sleep_diff:+.1f}h vs 8h target)")
    
    print("")
    print("=== RISK FLAGS ===")
    flag_labels = {
        "CRITICAL_BATTERY": "Critical Battery (<30)",
        "METABOLIC_FREEZE": "Metabolic Freeze (<48 bpm)",
        "LAG_2_OVERLOAD": "Lag-2 Overload (>5k steps 48h ago)",
        "FUNCTIONAL_INSOLVENCY": "Functional Insolvency (30-50%)",
        "CRASHING_TREND": "Crashing Trend (>10% drop)",
        "HIGH_COST": "High Cost Day (>35 cost)",
        "NERVOUS_SYSTEM_STUCK": "Nervous System Stuck (HRV < 45)",
        "INEFFICIENT_RECOVERY": "Inefficient Recovery (Low Steps + High Cost)"
    }
    
    for key, label in flag_labels.items():
        mark = "X" if flags.get(key) else " "
        print(f"[{mark}] {label}")

    print("")
    print(f"=== DATA TABLE (Last {num_days} Days) ===")
    print(f"{'Day':<10} | {'RHR':<3} | {'HRV':<3} | {'Batt':<4} | {'Sleep':<5} | {'Steps':<5} | {'Cost':<4}")
    print("-" * 52)
    
    for index, row in df.head(num_days).iterrows():
        day_str = row['day']
        rhr = int(row['rhr']) if pd.notnull(row['rhr']) else "--"
        hrv = int(row['hrv']) if pd.notnull(row['hrv']) else "--"
        batt = int(row['bb_charged']) if pd.notnull(row['bb_charged']) else "--"
        sleep = int(row['score']) if pd.notnull(row['score']) else "--"
        steps = int(row['steps']) if pd.notnull(row['steps']) else "--"
        cost = int(row['cost'])
        
        print(f"{day_str:<10} | {rhr:<3} | {hrv:<3} | {batt:<4} | {sleep:<5} | {steps:<5} | {cost:<4}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Health Commander's Brief")
    parser.add_argument('-f', '--folder', required=True, help="Path to DB folder")
    parser.add_argument('-n', '--days', type=int, default=7, help="Number of days to analyze")
    
    args = parser.parse_args()
    
    analyze_health(args.folder, args.days)