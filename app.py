import sqlite3
import os
import subprocess
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request, make_response
import dump_daily

app = Flask(__name__)

# Configuration
# Configuration
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
GARMIN_ACTIVITIES_DB = os.path.join(DB_DIR, "garmin_activities.db")
GARMIN_HRV_DB = os.path.join(DB_DIR, "garmin_hrv.db")
# Note: HRV data is now expected in garmin.db (Legacy comment?)
SYNC_SCRIPT = os.path.expanduser("/Users/joelgerard/tree_home/export_garmin.sh")

# --- DYNAMIC CONFIGURATION ---
# The Garmin algorithms are currently skewed due to the Dec 23 age change.
# This block forces Raw Data analysis until Jan 20, 2026.

current_date = datetime.now().date()
RECALIBRATION_DATE = datetime(2026, 1, 20).date()

if current_date < RECALIBRATION_DATE:
    # --- PHASE 1: RAW SENSOR MODE (Active Now) ---
    print("⚠️  GARMIN AGE SKEW DETECTED: Enforcing Raw Biometric Limits.")
    USE_GARMIN_ZONES = False
    
    # Values derived from Sensitivity Analysis:
    STEP_CAP_LAG = 5000
    HR_MAX_CAP = 102
    
else:
    # --- PHASE 2: ALGORITHM MODE (Active after Jan 20) ---
    print("✅  GARMIN CALIBRATION COMPLETE: Re-enabling Zone Analysis.")
    USE_GARMIN_ZONES = True
    
    # Standard Baselines (Relaxed):
    STEP_CAP_LAG = 5000  # Default safe baseline
    HR_MAX_CAP = 135     # Reverts to Zone 3/4 threshold

# -----------------------------

def get_db_connection(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def parse_time_str(time_str):
    """
    Parses a time string like '00:34:00.000000' or '00:34:00' into minutes (float).
    Returns 0 if None or invalid.
    """
    if not time_str:
        return 0.0
    try:
        # split by dot to handle microseconds
        parts = time_str.split('.')
        time_part = parts[0]
        t = datetime.strptime(time_part, "%H:%M:%S")
        minutes = t.hour * 60 + t.minute + t.second / 60
        return minutes
    except Exception as e:
        print(f"Error parsing time {time_str}: {e}")
        return 0.0

def get_daily_data(day_date, conn=None):
    """
    Fetch daily summary for a specific date object.
    """
    try:
        should_close = False
        if conn is None:
            conn = get_db_connection(GARMIN_DB)
            should_close = True
            
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM daily_summary WHERE day = ?", (day_date.isoformat(),))
        row = cursor.fetchone()
        
        if should_close:
            conn.close()
            
        return row
    except Exception as e:
        print(f"Error fetching daily data for {day_date}: {e}")
        return None

def get_resting_hr(day_date, conn=None):
    try:
        should_close = False
        if conn is None:
            conn = get_db_connection(GARMIN_DB)
            should_close = True
            
        cursor = conn.cursor()
        cursor.execute("SELECT resting_heart_rate FROM resting_hr WHERE day = ?", (day_date.isoformat(),))
        row = cursor.fetchone()
        
        if should_close:
            conn.close()
            
        return row['resting_heart_rate'] if row else None
    except Exception:
        return None

def get_hrv_data(day_date, conn=None):
    try:
        should_close = False
        if conn is None:
            conn = get_db_connection(GARMIN_DB)
            should_close = True
            
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM hrv WHERE day = ?", (day_date.isoformat(),))
        row = cursor.fetchone()
        
        if should_close:
            conn.close()
            
        return row
    except Exception as e:
        print(f"Error fetching HRV data for {day_date}: {e}")
        return None

def get_activities(day_date, conn=None):
    """
    Fetch walking/hiking activities for a specific date.
    """
    try:
        should_close = False
        if conn is None:
            conn = get_db_connection(GARMIN_ACTIVITIES_DB)
            should_close = True
            
        cursor = conn.cursor()
        # Filter for walking/hiking and ensure valid cadence
        # Note: start_time is DATETIME, we need to match the date part
        # SQLite substr(start_time, 1, 10) gets YYYY-MM-DD
        query = """
            SELECT * FROM activities 
            WHERE substr(start_time, 1, 10) = ? 
            AND (type LIKE '%walking%' OR type LIKE '%hiking%' OR sport LIKE '%walking%')
        """
        cursor.execute(query, (day_date.isoformat(),))
        rows = cursor.fetchall()
        
        if should_close:
            conn.close()
            
        return rows
    except Exception as e:
        print(f"Error fetching activities for {day_date}: {e}")
        return []

def check_freshness():
    """
    Check if the latest entries in daily_summary, hrv, and sleep are within one day (today or yesterday).
    Returns (is_fresh, last_summary_str, last_hrv_str, last_sleep_str, is_sleep_today).
    """
    last_summary_date = None
    last_hrv_date = None
    last_sleep_date = None
    
    try:
        # Check daily summary
        conn = get_db_connection(GARMIN_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(day) as last_day FROM daily_summary")
        row = cursor.fetchone()
        if row and row['last_day']:
            last_summary_date = datetime.strptime(row['last_day'], '%Y-%m-%d').date()

        # Check HRV
        cursor.execute("SELECT MAX(day) as last_day FROM hrv")
        row_hrv = cursor.fetchone()
        if row_hrv and row_hrv['last_day']:
            last_hrv_date = datetime.strptime(row_hrv['last_day'], '%Y-%m-%d').date()

        # Check Sleep
        cursor.execute("SELECT MAX(day) as last_day FROM sleep")
        row_sleep = cursor.fetchone()
        if row_sleep and row_sleep['last_day']:
            last_sleep_date = datetime.strptime(row_sleep['last_day'], '%Y-%m-%d').date()
        
        conn.close()

        today = datetime.now().date()
        yesterday = today - timedelta(days=1)
        
        is_summary_fresh = last_summary_date and last_summary_date >= yesterday
        is_hrv_fresh = last_hrv_date and last_hrv_date >= yesterday
        # Specific requirement: "if the app doesn't have sleep data for the day, show the sync button"
        is_sleep_today = last_sleep_date and last_sleep_date == today
        
        is_fresh = is_summary_fresh and is_hrv_fresh and is_sleep_today
        
        # Format for UI display
        summary_info = last_summary_date.isoformat() if last_summary_date else "No data"
        hrv_info = last_hrv_date.isoformat() if last_hrv_date else "No data"
        sleep_info = last_sleep_date.isoformat() if last_sleep_date else "No data"
        
        return is_fresh, summary_info, hrv_info, sleep_info, is_sleep_today

    except Exception as e:
        print(f"Error checking freshness: {e}", flush=True)
        
    return False, "Error", "Error", "Error", False

def get_recovery_score(conn, daily_status, target_date=None):
    """
    Calculates Recovery Score (0-100%) using Gaussian/Bell Curve logic.
    Refined for "Parasympathetic Saturation" detection.
    """
    cursor = conn.cursor()
    
    # --- 1. Golden Era Baselines (Mar 1 - May 31, 2025) ---
    # Derived from get_golden_stats.py
    GOLDEN_RHR_MEAN = 50.61
    GOLDEN_RHR_SD = 1.78
    GOLDEN_STRESS_MEAN = 35.77  # Age-uncorrected baseline from that era
    
    # Weights
    WEIGHT_RHR = 0.4
    WEIGHT_HRV = 0.4
    WEIGHT_STRESS = 0.2
    
    # Time Window
    today = target_date if target_date else datetime.now().date()
    seven_days_ago = today - timedelta(days=7)
    start_str = seven_days_ago.isoformat()
    end_str = today.isoformat()
    
    # --- Fetch Data ---
    
    # RHR (Avg of last 7 days)
    cursor.execute("""
        SELECT AVG(resting_heart_rate) 
        FROM resting_hr 
        WHERE day BETWEEN ? AND ?
    """, (start_str, end_str))
    row = cursor.fetchone()
    current_rhr = row[0] if row and row[0] else GOLDEN_RHR_MEAN

    # HRV (Today vs 7-Day)
    # Get Today's HRV (Last Night)
    cursor.execute("SELECT last_night_avg FROM hrv WHERE day = ?", (today.isoformat(),))
    row_hrv_today = cursor.fetchone()
    hrv_today = row_hrv_today[0] if row_hrv_today and row_hrv_today[0] else None
    
    # Get 7-Day Avg HRV
    cursor.execute("""
        SELECT AVG(last_night_avg) 
        FROM hrv 
        WHERE day BETWEEN ? AND ?
    """, (start_str, end_str))
    row_hrv_7d = cursor.fetchone()
    hrv_7d = row_hrv_7d[0] if row_hrv_7d and row_hrv_7d[0] else 51.45 # Fallback to Golden Baseline
    
    # If no data for today, assume neutral (match 7-day)
    if hrv_today is None:
        hrv_today = hrv_7d

    # Stress (Avg of last 7 days)
    cursor.execute("""
        SELECT AVG(stress_avg) 
        FROM daily_summary 
        WHERE day BETWEEN ? AND ?
    """, (start_str, end_str))
    row_stress = cursor.fetchone()
    raw_stress = row_stress[0] if row_stress and row_stress[0] else 30 # Default if empty
    
    # --- Scoring Logic ---

    # 1. RHR Score (Gaussian Bell Curve)
    # Z-Score = (Current - Mean) / SD
    rhr_z = (current_rhr - GOLDEN_RHR_MEAN) / GOLDEN_RHR_SD
    abs_z = abs(rhr_z)
    
    if abs_z <= 0.5:
        # Sweet Spot (+/- 0.5 SD) -> 100%
        rhr_score = 100
    elif abs_z <= 1.5:
        # Warning Zone (0.5 to 1.5 SD) -> Linear decay 100->70
        # Decay factor: (abs_z - 0.5) ranges 0.0 to 1.0
        decay = (abs_z - 0.5) * 30 
        rhr_score = 100 - decay
    else:
        # Critical Zone (> 1.5 SD) -> Sharp drop 70->0
        # "Parasympathetic Saturation" OR "Sympathetic Overdrive"
        # Decay factor: (abs_z - 1.5) ranges 0.0 to ...
        # e.g. at 2.5 SD, score should be very low.
        extra_decay = (abs_z - 1.5) * 50
        rhr_score = max(0, 70 - extra_decay)

    # 2. HRV Balance Score (Stability vs Spike)
    # Check for "Parasympathetic Saturation" (Spike > 20% above baseline)
    # Check for "Sympathetic Stress" (Drop below baseline)
    
    hrv_ratio = hrv_today / hrv_7d if hrv_7d > 0 else 1.0
    
    if 0.9 <= hrv_ratio <= 1.2:
        # Green Zone: Within 90-120% of trend
        hrv_score = 100
    elif hrv_ratio > 1.2:
        # Saturation Spike (>120%)
        # Penalize: 100 -> 50 for 1.2->1.4
        excess = hrv_ratio - 1.2
        hrv_score = max(0, 100 - (excess * 250)) # e.g. 0.2 excess (1.4 ratio) -> 50 pts off
    else:
        # Sympathetic Drop (<90%)
        # Penalize: 100 -> 0 for 0.9->0.7
        deficit = 0.9 - hrv_ratio
        hrv_score = max(0, 100 - (deficit * 500)) # e.g. 0.2 deficit (0.7 ratio) -> 100 pts off

    # 3. Stress Score (Age Corrected)
    # Correction Factor: 1.15x
    adj_stress = raw_stress * 1.15
    
    if adj_stress <= GOLDEN_STRESS_MEAN:
        stress_score = 100
    else:
        # Linear penalty for excess stress
        # e.g. Stress 50 vs Baseline 35 -> Diff 15 -> Score 70
        diff = adj_stress - GOLDEN_STRESS_MEAN
        stress_score = max(0, 100 - (diff * 2))

    # --- Final Weighted Score ---
    final_score = (rhr_score * WEIGHT_RHR) + (hrv_score * WEIGHT_HRV) + (stress_score * WEIGHT_STRESS)
    
    # --- VETO PROTOCOL ---
    veto_msg = None
    if daily_status == "RED":
        final_score = 40
        veto_msg = "⚠️ Score Vetoed: System Crash Detected."
    elif daily_status == "YELLOW":
        if final_score > 75:
            final_score = 75
            veto_msg = "⚠️ Score Capped at 75% due to Caution status."
            
    return {
        "score": round(final_score, 1),
        "veto_msg": veto_msg,
        "details": {
            "rhr": {
                "val": round(current_rhr, 1), 
                "z_score": round(rhr_z, 2), 
                "score": round(rhr_score, 1)
            },
            "hrv": {
                "val": round(hrv_today, 1), 
                "7d_avg": round(hrv_7d, 1), 
                "ratio": round(hrv_ratio, 2), 
                "score": round(hrv_score, 1)
            },
            "stress": {
                "raw": round(raw_stress, 1), 
                "adj": round(adj_stress, 1), 
                "score": round(stress_score, 1)
            }
        }
    }

def get_trend_data(conn, target_date=None, days=7):
    """
    Fetch trend data for the Trend Command Center.
    Calculates 7-day vs 3-day slopes and efficiency costs.
    """
    cursor = conn.cursor()
    
    end_date = target_date if target_date else datetime.now().date()
    start_date = end_date - timedelta(days=days-1)
    
    cursor.execute(f"""
        SELECT day, rhr, bb_max, steps, calories_active, stress_avg 
        FROM daily_summary 
        WHERE day BETWEEN ? AND ?
        ORDER BY day DESC
    """, (start_date.isoformat(), end_date.isoformat()))
    
    rows = cursor.fetchall()
    
    # We need at least 7 days for 7d trend, or whatever available
    # rows are DESC: Today=0, Yesterday=1...
    
    trends = {
        "rhr": {"trend_7d": 0, "trend_3d": 0, "val": 0},
        "batt": {"trend_7d": 0, "trend_3d": 0, "val": 0},
        "stress": {"trend_7d": 0, "trend_3d": 0, "val": 0},
        "hrv": {"trend_7d": 0, "trend_3d": 0, "val": 0},
        "recent_costs": []
    }
    
    if not rows:
        return trends

    # Fetch HRV Data
    try:
        cursor.execute(f"""
            SELECT day, last_night_avg 
            FROM hrv 
            WHERE day BETWEEN ? AND ?
            ORDER BY day DESC
        """, (start_date.isoformat(), end_date.isoformat()))
        rows_hrv = cursor.fetchall()
        
        # Map HRV by day for easy lookup
        hrv_map = {row['day']: row['last_night_avg'] for row in rows_hrv if row['last_night_avg']}
    except Exception as e:
        print(f"Error fetching HRV trend data: {e}")
        hrv_map = {}
        
    # Helper for safe access
    def get_val(r, key):
        if key == 'hrv':
            return hrv_map.get(r['day'], 0)
        return r[key] if r and r[key] is not None else 0

    # 1. 7-Day Trend (Diff of Avgs)
    recent_rows = rows[:3]
    old_rows = rows[-3:] if len(rows) >= 6 else []
    
    if len(rows) >= 6:
        # RHR 7d
        rhr_recent = sum([get_val(r, 'rhr') for r in recent_rows]) / 3
        rhr_old = sum([get_val(r, 'rhr') for r in old_rows]) / 3
        trends['rhr']['trend_7d'] = round(rhr_recent - rhr_old, 1)
        
        # Batt 7d (bb_max)
        batt_recent = sum([get_val(r, 'bb_max') for r in recent_rows]) / 3
        batt_old = sum([get_val(r, 'bb_max') for r in old_rows]) / 3
        trends['batt']['trend_7d'] = round(batt_recent - batt_old, 1)

        # Stress 7d
        stress_recent = sum([get_val(r, 'stress_avg') for r in recent_rows]) / 3
        stress_old = sum([get_val(r, 'stress_avg') for r in old_rows]) / 3
        trends['stress']['trend_7d'] = round(stress_recent - stress_old, 1)

        # HRV 7d
        hrv_recent = sum([get_val(r, 'hrv') for r in recent_rows]) / 3
        hrv_old = sum([get_val(r, 'hrv') for r in old_rows]) / 3
        trends['hrv']['trend_7d'] = round(hrv_recent - hrv_old, 1)
        
    # 2. 3-Day Trend (Today - 3 Days Ago)
    if len(rows) > 3:
        today_row = rows[0]
        ago3_row = rows[3]
        
        trends['rhr']['trend_3d'] = get_val(today_row, 'rhr') - get_val(ago3_row, 'rhr')
        trends['batt']['trend_3d'] = get_val(today_row, 'bb_max') - get_val(ago3_row, 'bb_max')
        trends['stress']['trend_3d'] = get_val(today_row, 'stress_avg') - get_val(ago3_row, 'stress_avg')
        trends['hrv']['trend_3d'] = get_val(today_row, 'hrv') - get_val(ago3_row, 'hrv')
        
    trends['rhr']['val'] = get_val(rows[0], 'rhr')
    trends['batt']['val'] = get_val(rows[0], 'bb_max')
    trends['stress']['val'] = get_val(rows[0], 'stress_avg')
    trends['hrv']['val'] = get_val(rows[0], 'hrv')

    # 3. Efficiency Costs (Last 3 Days)
    # Indices 0, 1, 2
    for i in range(min(3, len(rows))):
        r = rows[i]
        cals = get_val(r, 'calories_active')
        steps = get_val(r, 'steps')
        cost = (cals / steps * 1000) if steps > 0 else 0
        
        # Date label
        d_obj = datetime.strptime(r['day'], '%Y-%m-%d').date()
        # Relative to target_date (which acts as "Today" in this context)
        # But for UI consistency, we might want actual names or dates.
        # Let's keep Today/Yesterday logic relative to ACTUAL today or TARGET today?
        # User wants "look at dashboard on a specific day in the past".
        # So "Today" should probably refer to the Selected Date to make sense in that context?
        # Or should it just show the date?
        # Let's stick to showing Date if not actual today/yesterday.
        
        if d_obj == datetime.now().date():
            lbl = "Today"
        elif d_obj == datetime.now().date() - timedelta(days=1):
            lbl = "Yesterday"
        else:
            lbl = d_obj.strftime("%a %d") # e.g. Mon 23
            
        trends['recent_costs'].append({
            "label": lbl,
            "cost": int(cost),
            "status": "GREEN" if cost < 30 else ("RED" if cost > 50 else "YELLOW")
        })
        
    return trends

def calculate_metrics(target_date, conn, conn_activities):
    """
    The Core Logic Engine (FINAL VERSION).
    Combines:
    1. Freeze Protocol (Low RHR).
    2. Sensory Load Flag (High Stress / Low Steps).
    3. Crash Predictor (T-2 Risk Score).
    4. Mitochondrial Efficiency (Recharge Ratio).
    5. Zombie Protocol (Downgraded to Caution).
    """
    cursor = conn.cursor()
    
    # 1. Fetch Key Metrics
    cursor.execute("SELECT rhr, hr_max, bb_charged, stress_avg, steps, calories_active FROM daily_summary WHERE day = ?", (target_date,))
    row = cursor.fetchone()
    
    # Fetch Sleep Data for Mitochondrial Efficiency
    cursor.execute("SELECT total_sleep FROM sleep WHERE day = ?", (target_date,))
    row_sleep = cursor.fetchone()
    
    if not row:
        return {"status": "GRAY", "reason": "No Data", "target_steps": 0, "metrics": {}}
        
    rhr = row['rhr']
    bb_charged = row['bb_charged']
    active_cals = row['calories_active']
    steps = row['steps']
    stress_avg = row['stress_avg']
    
    # Parse total_sleep to hours
    sleep_hours = 0
    if row_sleep and row_sleep['total_sleep']:
        sleep_hours = parse_time_str(row_sleep['total_sleep']) / 60 # parse_time_str returns minutes
    
    # --- GOLDEN ERA BASELINES (Mar-May 2025) ---
    BASELINE_RHR = 50.6
    BASELINE_COST = 29.0 # Active Calories per 1,000 Steps
    BASELINE_STEPS = 4000
    BASELINE_STRESS = 35
    
    warnings = []
    red_flags = []
    
    # --- LOGIC GATE 1: THE ENGINE (RHR & Sensory Load) ---
    # Sympathetic Stress (Too High)
    if rhr and rhr > (BASELINE_RHR + 3):  # > 53.6 bpm
        red_flags.append(f"High RHR (+{round(rhr - BASELINE_RHR, 1)})")
        
    # Parasympathetic Freeze (Too Low)
    elif rhr and rhr < (BASELINE_RHR - 2.5): # < 48.1 bpm
        red_flags.append(f"Metabolic Freeze Detected (RHR {rhr})")
        
    # SENSORY LOAD FLAG (New)
    # IF Steps < 3000 AND Average_Stress > 35
    if steps is not None and steps < 3000 and stress_avg and stress_avg > 35:
        red_flags.append("High Idle / Sensory Overload")
        
    # --- LOGIC GATE 2: THE BATTERY (Mitochondrial Efficiency) ---
    if bb_charged and bb_charged < 50:
        red_flags.append(f"Poor Recharge (Max {bb_charged}%)")
        
    # Mitochondrial Efficiency Check (New)
    # Recharge_Ratio = Body_Battery_Gain / Sleep_Hours
    if sleep_hours > 0 and bb_charged:
        recharge_ratio = bb_charged / sleep_hours
        if recharge_ratio < 5.0:
            warnings.append(f"Unrefreshing Sleep (Ratio {round(recharge_ratio, 1)} < 5.0)")
            
    # --- LOGIC GATE 3: THE LAG 2 PREDICTOR (Risk Formula) ---
    # Risk = (Steps_48h / Baseline_Steps) + (Stress_48h / Baseline_Stress)
    # Threshold > 1.5
    # OR Sensory Overload: Stress > 35 AND Steps < 1000
    day_t2 = (datetime.strptime(target_date, '%Y-%m-%d') - timedelta(days=2)).strftime('%Y-%m-%d')
    cursor.execute("SELECT steps, hr_max, stress_avg FROM daily_summary WHERE day = ?", (day_t2,))
    row_t2 = cursor.fetchone()
    
    if row_t2:
        val_steps_t2 = row_t2['steps'] if row_t2['steps'] else 0
        val_stress_t2 = row_t2['stress_avg'] if row_t2['stress_avg'] else 0
        
        # Calculate Risk Score (Keep for general mixed load)
        risk_score = (val_steps_t2 / BASELINE_STEPS) + (val_stress_t2 / BASELINE_STRESS)
        
        # Check 1: Sensory Overload (Specific Request)
        if val_stress_t2 > 35 and val_steps_t2 < 3000:
            warnings.append("Lag-2 Risk: Sensory Overload Detected")
            
        # Check 2: High Volume (Old Rule)
        elif val_steps_t2 > 5000:
            warnings.append(f"Lag-2 Impact (High Load on {day_t2})")
            
        # Check 3: Combined Risk (New Formula Catch-all)
        elif risk_score > 1.5:
             warnings.append(f"Lag-2 Warning (Risk {round(risk_score, 2)})")

        # Optional: HR Spike T-2 check (Preserved)
        if row_t2['hr_max'] and row_t2['hr_max'] > 110:
            warnings.append(f"Lag 2 HR Spike ({day_t2})")

    # 3. Safety Ceiling (T-1) Logic [UPDATED]
    day_t1 = (datetime.strptime(target_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    cursor.execute("SELECT steps, calories_active FROM daily_summary WHERE day = ?", (day_t1,))
    row_t1 = cursor.fetchone()
    
    if row_t1:
        val_steps_t1 = row_t1['steps'] if row_t1['steps'] else 0
        val_active_cals_t1 = row_t1['calories_active'] if row_t1['calories_active'] else 0
        
        # Calculate Physiological Cost
        physio_cost_t1 = (val_active_cals_t1 / val_steps_t1 * 1000) if val_steps_t1 > 0 else 0
        
        VOLUME_LIMIT = 3000
        COST_LIMIT = 150
        
        if val_steps_t1 > VOLUME_LIMIT:
             red_flags.append(f"Volume Ceiling Breached ({val_steps_t1} steps)")
        elif physio_cost_t1 > COST_LIMIT:
             red_flags.append(f"High Metabolic Tax (Cost: {int(physio_cost_t1)})")

    # --- LOGIC GATE 5: PHYSIOLOGICAL COST (The Efficiency Check) ---
    # Metric: Active Calories per 1,000 steps
    # Baseline: ~29.0. Warning Threshold: +20% (34.8)
    physio_cost = 0
    if steps and steps > 0 and active_cals:
        physio_cost = (active_cals / steps) * 1000
        
        if physio_cost > (BASELINE_COST * 1.2):
            warnings.append(f"High Physiological Cost ({int(physio_cost)})")

    # --- LOGIC GATE 6: MOVEMENT INEFFICIENCY (The Zombie Walk) ---
    # Downgraded to YELLOW (Caution)
    cursor_act = conn_activities.cursor()
    yesterday = (datetime.strptime(target_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    
    try:
        # Use 'activities' table (verified to exist)
        cursor_act.execute("""
            SELECT avg_cadence 
            FROM activities 
            WHERE substr(start_time, 1, 10) = ? 
            AND (type LIKE '%walking%' OR type LIKE '%hiking%' OR sport LIKE '%walking%')
        """, (yesterday,))
        rows = cursor_act.fetchall()
        
        for row in rows:
            if row['avg_cadence'] and row['avg_cadence'] > 0 and row['avg_cadence'] < 95: # < 95 spm suggests inefficient/shuffle
                warnings.append("Inefficient Movement (Shuffle)")
                break
    except Exception as e:
        print(f"Warning: Could not fetch activity data: {e}")

    # --- FINAL VERDICT ---
    if len(red_flags) > 0:
        status = "RED"
        # Special check for Sensory Load text
        if "High Idle / Sensory Overload" in red_flags:
             reason = "System is idling high. Physical rest is not enough; requires sensory deprivation."
        else:
             reason = "STOP. " + ", ".join(red_flags)
        target_steps = 1500
    elif len(warnings) >= 1:
        status = "YELLOW"
        reason = "CAUTION. " + ", ".join(warnings)
        target_steps = 3000
    else:
        status = "GREEN"
        reason = "Go for it. Maintain pacing."
        target_steps = 4500
        
    return {
        "status": status,
        "reason": reason,
        "target_steps": target_steps,
        "metrics": {"rhr": rhr, "bb": bb_charged, "physio_cost": physio_cost, "steps": steps}
    }

def get_dashboard_context(date_str):
    """
    Encapsulates all the logic to fetch and process data for the dashboard.
    Returns a dict with: metrics, recovery, trends, flags, etc.
    """
    # Parse date
    selected_date = None
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = None

    target_date_str = date_str if selected_date else datetime.now().strftime('%Y-%m-%d')
    target_date_obj = selected_date if selected_date else datetime.now().date()
    
    # 1. Run the Core Logic
    try:
        conn = get_db_connection(GARMIN_DB)
        conn_activities = get_db_connection(GARMIN_ACTIVITIES_DB)
        metrics_raw = calculate_metrics(target_date_str, conn, conn_activities)
        trends = get_trend_data(conn, target_date=target_date_obj)
        conn.close()
        conn_activities.close()
    except Exception as e:
        print(f"Error calculating metrics for {target_date_str}: {e}")
        metrics_raw = {"status": "GRAY", "reason": f"Error: {e}", "target_steps": 0, "metrics": {}}
        trends = {
            "rhr": {"trend_7d": 0, "trend_3d": 0, "val": 0},
            "batt": {"trend_7d": 0, "trend_3d": 0, "val": 0},
            "stress": {"trend_7d": 0, "trend_3d": 0, "val": 0},
            "hrv": {"trend_7d": 0, "trend_3d": 0, "val": 0},
            "recent_costs": []
        }

    # 2. Map Core Logic to UI Panels (The "Adapter" Layer)
    
    # A. Crash Predictor (Lag 2)
    if "sensory deprivation" in metrics_raw['reason'] or "Sensory Overload" in metrics_raw['reason']:
        crash_status = {"status": "RED", "msg": "Lag-2 Risk: Sensory Overload"}
    elif "Lag 2" in metrics_raw['reason']:
        crash_status = {"status": "RED", "msg": "High Load 48h ago (>5k steps)"}
    else:
        crash_status = {"status": "GREEN", "msg": "No Lag-2 Risk Detected"}


    # B. Safety Ceiling (T-1 Load)
    if "System is idling high" in metrics_raw['reason'] or "T-1 High Idle" in metrics_raw['reason']:
        safety_status = {"status": "YELLOW", "msg": "T-1 Sensory Overload"}
    elif "Volume Ceiling" in metrics_raw['reason']:
        safety_status = {"status": "RED", "msg": "Volume Ceiling Breached"}
    elif "High Metabolic Tax" in metrics_raw['reason']:
        safety_status = {"status": "RED", "msg": "High Metabolic Tax Detected"}
    else:
        safety_status = {"status": "GREEN", "msg": "Within Volume Limits"}

    # C. Autonomic Stress (The Engine)
    if "High RHR" in metrics_raw['reason']:
        stress_status = {"status": "RED", "msg": "Sympathetic Stress (High RHR)"}
    elif "Freeze" in metrics_raw['reason']:
        stress_status = {"status": "RED", "msg": "METABOLIC FREEZE (Low RHR)"}
    else:
        stress_status = {"status": "GREEN", "msg": "RHR Stable"}

    # D. Sleep Recharge (The Battery)
    if "Poor Recharge" in metrics_raw['reason']:
        sleep_status = {"status": "RED", "msg": f"Battery Gain {metrics_raw['metrics'].get('bb', 0)}% < 50%"}
    else:
        sleep_status = {"status": "GREEN", "msg": "Recharge Sufficient"}
        
    # E. Efficiency Check (The Zombie Walk)
    if "Shuffle" in metrics_raw['reason']:
        eff_status = {"status": "YELLOW", "msg": "Inefficient Gait Detected"}
    else:
        eff_status = {"status": "GREEN", "msg": "Gait Normal"}

    # F. Physiological Cost
    p_cost = metrics_raw['metrics'].get('physio_cost', 0)
    if "High Physiological Cost" in metrics_raw['reason']:
         p_cost_status = {"status": "YELLOW", "msg": "High Cost (>+20% Baseline)"}
    else:
         p_cost_status = {"status": "GREEN", "msg": "Efficiency Normal"}

    # 3. Build Final Dictionary
    metrics = {
        "final_verdict": {
            "status": metrics_raw["status"],
            "msg": metrics_raw["reason"],
            "target": f"{metrics_raw['target_steps']:,} Steps"
        },
        "crash_predictor": crash_status,
        "safety_ceiling": safety_status,
        "autonomic_stress": stress_status,
        "sleep_recharge": sleep_status,
        "efficiency_check": eff_status,
        "physio_cost": {
            "status": p_cost_status["status"],
            "msg": p_cost_status["msg"],
            "val": round(p_cost, 1) if p_cost else 0
        },
        "respiration_warning": {"status": "GRAY", "msg": "Monitor via Oura/Manual"},
        "today_data_available": True if metrics_raw['metrics'].get('rhr') else False,
        # Keep raw metrics access if needed
        "_raw": metrics_raw['metrics']
    }

    daily_status = metrics['final_verdict']['status']
    
    # 4. Calculate Recovery Score
    recovery_score = None
    try:
        conn = get_db_connection(GARMIN_DB)
        recovery_score = get_recovery_score(conn, daily_status, target_date=target_date_obj)
        conn.close()
    except Exception as e:
        print(f"Error calculating recovery score: {e}")

    # 5. Logic Flags
    current_steps = metrics_raw['metrics'].get('steps', 0) or 0
    current_cost = metrics_raw['metrics'].get('physio_cost', 0) or 0
    
    flags = {
        "INEFFICIENT_RECOVERY": (current_steps < 2500 and current_cost > 35)
    }
    
    # 6. Chart Data
    chart_data = get_time_series_data(target_date_obj)
    recovery_history = get_recovery_history_data(365)
    
    return {
        "metrics": metrics,
        "recovery": recovery_score,
        "trends": trends,
        "flags": flags,
        "target_date_str": target_date_str,
        "chart_data": chart_data,
        "recovery_history": recovery_history
    }

def dict_to_csv(data_dict):
    """
    Helper to convert a dict of lists into a CSV string.
     Assumes all lists are same length.
    """
    if not data_dict:
        return ""
        
    keys = list(data_dict.keys())
    # Find length of first list
    if not keys:
        return ""
        
    num_rows = len(data_dict[keys[0]])
    
    lines = []
    # Header
    lines.append(",".join(keys))
    
    # Rows
    for i in range(num_rows):
        row = []
        for k in keys:
            val = data_dict[k][i]
            if val is None:
                row.append("")
            else:
                row.append(str(val))
        lines.append(",".join(row))
        
    return "\n".join(lines)

def format_dashboard_report(context):
    """
    Formats the dashboard context into a detailed text string.
    """
    m = context['metrics']
    r = context['recovery']
    t = context['trends']
    date_str = context['target_date_str']
    
    lines = []
    lines.append(f"TREE HEALTH DASHBOARD REPORT")
    lines.append(f"Date: {date_str}")
    lines.append("=" * 40)
    lines.append("")
    
    # 1. DAILY VERDICT
    v = m['final_verdict']
    lines.append(f"[ {v['status']} ] {v['msg']}")
    lines.append(f"Target: {v['target']}")
    lines.append("-" * 40)
    lines.append("")

    # 2. RECOVERY INDEX
    lines.append("RECOVERY INDEX")
    if r:
        lines.append(f"Score: {r['score']}%")
        lines.append(f"  RHR:    {r['details']['rhr']['val']} bpm (Score: {r['details']['rhr']['score']}%)")
        lines.append(f"  HRV:    {r['details']['hrv']['val']} ms  (Score: {r['details']['hrv']['score']}%)")
        lines.append(f"  Stress: {r['details']['stress']['adj']}     (Score: {r['details']['stress']['score']}%)")
        if r['veto_msg']:
            lines.append(f"  * {r['veto_msg']}")
    else:
        lines.append("  (No Data)")
    lines.append("")

    # 3. PHYSIOLOGICAL STATUS
    lines.append("PHYSIOLOGICAL STATUS")
    
    def status_line(label, obj):
        return f"  {label:<20} [{obj['status']}] {obj['msg']}"
        
    lines.append(status_line("Crash Predictor", m['crash_predictor']))
    lines.append(status_line("Safety Ceiling", m['safety_ceiling']))
    lines.append(status_line("Autonomic Stress", m['autonomic_stress']))
    lines.append(status_line("Sleep Recharge", m['sleep_recharge']))
    lines.append(status_line("Efficiency Check", m['efficiency_check']))
    lines.append(f"  Physio Cost          [{m['physio_cost']['status']}] {m['physio_cost']['val']} Cals/1k ({m['physio_cost']['msg']})")
    lines.append("")

    # 4. TREND COMMAND CENTER
    lines.append("TREND COMMAND CENTER")
    
    def trend_line(label, obj, unit):
        t7 = f"{obj['trend_7d']:+}"
        t3 = f"{obj['trend_3d']:+}"
        return f"  {label:<12} {obj['val']:>5}{unit} | 7d: {t7:>5} | 3d: {t3:>5}"
        
    lines.append(trend_line("Body Batt", t['batt'], "%"))
    lines.append(trend_line("HRV (Avg)", t['hrv'], "ms"))
    lines.append(trend_line("Resting HR", t['rhr'], "bp"))
    lines.append(trend_line("Stress", t['stress'], "  "))
    lines.append("")
    
    lines.append("Efficiency Report (Last 3 Days):")
    for cost in t['recent_costs']:
        lines.append(f"  {cost['label']}: {cost['cost']} ({cost['status']})")
        
    lines.append("")
    lines.append("=" * 40)
    lines.append("")
    
    # 5. CHART DATA (CSV BLOCKS)
    lines.append("DATA: Activity & Capacity (14-Day Rolling)")
    lines.append("-" * 40)
    lines.append(dict_to_csv(context['chart_data']))
    lines.append("")
    lines.append("=" * 40)
    lines.append("")
    
    # 6. RECOVERY HISTORY (CSV BLOCK)
    lines.append("DATA: Recovery History (365-Day)")
    lines.append("-" * 40)
    lines.append(dict_to_csv(context['recovery_history']))
    lines.append("")
    lines.append("End of Report")
    
    return "\n".join(lines)

@app.route('/')
def index():
    is_fresh, last_data, last_hrv, last_sleep, is_sleep_today = check_freshness()
    
    # Parse date param
    date_str = request.args.get('date')
    
    # Get Dashboard Context
    ctx = get_dashboard_context(date_str)
    
    return render_template('index.html', 
                           fresh=is_fresh, 
                           last_data=last_data, 
                           last_hrv=last_hrv, 
                           last_sleep=last_sleep, 
                           metrics=ctx['metrics'], 
                           recovery=ctx['recovery'], 
                           trends=ctx['trends'], 
                           flags=ctx['flags'], 
                           selected_date=ctx['target_date_str'], 
                           db_dir=DB_DIR)

@app.route('/download_summary')
def download_summary():
    date_str = request.args.get('date')
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')
        
    try:
        # Get context
        ctx = get_dashboard_context(date_str)
        # Generate text
        text_content = format_dashboard_report(ctx)
        
        response = make_response(text_content)
        response.headers["Content-Disposition"] = f"attachment; filename=dashboard_report_{date_str}.txt"
        response.mimetype = 'text/plain'
        return response
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Error generating summary: {e}", 500

@app.route('/sync', methods=['POST'])
def sync():
    """
    Trigger the sync script.
    """
    try:
        print(f"[{datetime.now()}] Starting sync triggered via web...", flush=True)
        # Check if script exists
        if not os.path.exists(SYNC_SCRIPT):
             print(f"[{datetime.now()}] ERROR: Sync script not found at {SYNC_SCRIPT}", flush=True)
             return jsonify({"status": "error", "message": f"Sync script not found at {SYNC_SCRIPT}"}), 404

        # Run script with Popen for real-time streaming
        print(f"[{datetime.now()}] Executing: {SYNC_SCRIPT}", flush=True)
        process = subprocess.Popen(
            [SYNC_SCRIPT], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT, 
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        # Read output in real-time
        for line in process.stdout:
            print(f"[{datetime.now()}] SYNC: {line.strip()}", flush=True)
            
        process.wait()
        
        if process.returncode == 0:
            print(f"[{datetime.now()}] Sync completed successfully.", flush=True)
            return jsonify({"status": "success", "message": "Sync completed"})
        else:
            print(f"[{datetime.now()}] ERROR: Sync failed with exit code {process.returncode}", flush=True)
            return jsonify({"status": "error", "message": f"Sync failed with exit code {process.returncode}"}), 500

    except Exception as e:
        print(f"[{datetime.now()}] UNEXPECTED ERROR: {e}", flush=True)
        return jsonify({"status": "error", "message": f"An error occurred: {e}"}), 500


def get_time_series_data(target_date):
    """
    Fetch 14-day rolling data for charts.
    Returns a dict with lists for each metric.
    """
    end_date = target_date if target_date else datetime.now().date()
    start_date = end_date - timedelta(days=13)
    
    data = {
        "dates": [],
        "steps": [],
        "rhr": [],
        "stress": [],
        "batt": [],
        "batt_gain": [],
        "cost": [],
        "active_cals": [],
        "hrv": []
    }
    
    try:
        conn = get_db_connection(GARMIN_DB)
        cursor = conn.cursor()
        
        current = start_date
        while current <= end_date:
            date_str = current.isoformat()
            data["dates"].append(date_str)
            
            # Steps
            cursor.execute("SELECT steps FROM daily_summary WHERE day = ?", (date_str,))
            row_steps = cursor.fetchone()
            steps = row_steps['steps'] if row_steps else 0
            data["steps"].append(steps if steps else 0)
            
            # RHR
            cursor.execute("SELECT resting_heart_rate FROM resting_hr WHERE day = ?", (date_str,))
            row_rhr = cursor.fetchone()
            rhr = row_rhr['resting_heart_rate'] if row_rhr else None
            
            if rhr is None:
                cursor.execute("SELECT rhr FROM daily_summary WHERE day = ?", (date_str,))
                row_ds_rhr = cursor.fetchone()
                if row_ds_rhr:
                    rhr = row_ds_rhr['rhr']
            
            data["rhr"].append(rhr) 
            
            # Stress
            cursor.execute("SELECT stress_avg FROM daily_summary WHERE day = ?", (date_str,))
            row_stress = cursor.fetchone()
            stress = row_stress['stress_avg'] if row_stress else None
            data["stress"].append(stress)
            
            # Body Battery
            cursor.execute("SELECT bb_max, bb_charged FROM daily_summary WHERE day = ?", (date_str,))
            row_bb = cursor.fetchone()
            batt = row_bb['bb_max'] if row_bb else None
            batt_gain = row_bb['bb_charged'] if row_bb else None
            data["batt"].append(batt)
            data["batt_gain"].append(batt_gain)

            # Physiological Cost
            cursor.execute("SELECT calories_active FROM daily_summary WHERE day = ?", (date_str,))
            row_cals = cursor.fetchone()
            active_cals = row_cals['calories_active'] if row_cals else 0
            
            cost = 0
            if steps and steps > 0 and active_cals:
                cost = round((active_cals / steps) * 1000, 1)
            data["cost"].append(cost)
            data["active_cals"].append(active_cals)
            
            # HRV
            cursor.execute("SELECT last_night_avg FROM hrv WHERE day = ?", (date_str,))
            row_hrv = cursor.fetchone()
            hrv = row_hrv['last_night_avg'] if row_hrv else None
            data["hrv"].append(hrv)

            current += timedelta(days=1)
            
        conn.close()
    except Exception as e:
        print(f"Error fetching time series data: {e}")
        
    return data

def get_recovery_history_data(days=14):
    """
    Fetch historical recovery scores.
    """
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=days - 1)
    
    data = {
        "dates": [],
        "recovery_score": [],
        "rhr_score": [],
        "hrv_score": [],
        "stress_score": []
    }
    
    try:
        conn = get_db_connection(GARMIN_DB)
        conn_activities = get_db_connection(GARMIN_ACTIVITIES_DB)
        
        current = start_date
        while current <= end_date:
            date_str = current.isoformat()
            data["dates"].append(date_str)
            
            # 1. Get Daily Status
            try:
                try:
                    metrics = calculate_metrics(date_str, conn, conn_activities)
                    daily_status = metrics['status']
                except Exception as inner_e:
                    # print(f"Warning: calculate_metrics failed for {date_str}: {inner_e}")
                    daily_status = "GREEN" 
            except Exception:
                daily_status = "GREEN"

            # 2. Get Recovery Score
            try:
                score_data = get_recovery_score(conn, daily_status, target_date=current)
                data["recovery_score"].append(score_data['score'])
                data["rhr_score"].append(score_data['details']['rhr']['score'])
                data["hrv_score"].append(score_data['details']['hrv']['score'])
                data["stress_score"].append(score_data['details']['stress']['score'])
            except Exception:
                data["recovery_score"].append(None)
                data["rhr_score"].append(None)
                data["hrv_score"].append(None)
                data["stress_score"].append(None)
            
            current += timedelta(days=1)
            
        conn.close()
        conn_activities.close()
    except Exception as e:
        print(f"Error fetching history data: {e}")
        
    return data

@app.route('/api/data')
def api_data():
    """
    Return JSON data for Plotly charts (14-day rolling view).
    """
    date_str = request.args.get('date')
    if date_str:
        try:
            end_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
             end_date = datetime.now().date()
    else:
        end_date = datetime.now().date()
        
    data = get_time_series_data(end_date)
    return jsonify(data)

@app.route('/api/recovery_history')
def api_recovery_history():
    """
    Return JSON data for Recovery Score history.
    """
    try:
        days = int(request.args.get('days', 14))
    except ValueError:
        days = 14
        
    data = get_recovery_history_data(days)
    return jsonify(data)

if __name__ == '__main__':
    app.run(port=5050, debug=True)
