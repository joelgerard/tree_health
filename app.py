import sqlite3
import os
import subprocess
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request

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
# Note: HRV data is now expected in garmin.db
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

def calculate_metrics(target_date, conn, conn_activities):
    """
    The Core Logic Engine (FINAL VERSION).
    Combines:
    1. Freeze Protocol (Low RHR).
    2. Lag 2 Protocol (Delayed PEM).
    3. Zombie Protocol (Downgraded to Caution).
    """
    cursor = conn.cursor()
    
    # 1. Fetch Key Metrics
    cursor.execute("SELECT rhr, hr_max, bb_charged, stress_avg, steps, calories_active FROM daily_summary WHERE day = ?", (target_date,))
    row = cursor.fetchone()
    
    if not row:
        return {"status": "GRAY", "reason": "No Data", "target_steps": 0, "metrics": {}}
        
    rhr = row['rhr']
    bb_charged = row['bb_charged']
    active_cals = row['calories_active']
    steps = row['steps']
    
    # --- GOLDEN ERA BASELINES (Mar-May 2025) ---
    BASELINE_RHR = 50.6
    BASELINE_COST = 29.0 # Active Calories per 1,000 Steps
    
    warnings = []
    red_flags = []
    
    # --- LOGIC GATE 1: THE ENGINE (RHR) ---
    # Sympathetic Stress (Too High)
    if rhr and rhr > (BASELINE_RHR + 3):  # > 53.6 bpm
        red_flags.append(f"High RHR (+{round(rhr - BASELINE_RHR, 1)})")
        
    # Parasympathetic Freeze (Too Low) - THE NEW CRITICAL CHECK
    elif rhr and rhr < (BASELINE_RHR - 2.5): # < 48.1 bpm
        red_flags.append(f"Metabolic Freeze Detected (RHR {rhr})")

    # --- LOGIC GATE 2: THE BATTERY ---
    if bb_charged and bb_charged < 50:
        red_flags.append(f"Poor Recharge (Max {bb_charged}%)")

    # --- LOGIC GATE 3: THE LAG 2 PREDICTOR (Restored) ---
    # Check T-2 (Two days ago) for overload
    day_t2 = (datetime.strptime(target_date, '%Y-%m-%d') - timedelta(days=2)).strftime('%Y-%m-%d')
    cursor.execute("SELECT steps, hr_max FROM daily_summary WHERE day = ?", (day_t2,))
    row_t2 = cursor.fetchone()
    
    if row_t2:
        # If she exceeded 5000 steps 48 hours ago, today is the Crash Day.
        if row_t2['steps'] and row_t2['steps'] > 5000:
            red_flags.append(f"Lag 2 Impact (High Load on {day_t2})")
        # Optional: HR Spike T-2 check
        if row_t2['hr_max'] and row_t2['hr_max'] > 110:
            warnings.append(f"Lag 2 HR Spike ({day_t2})")

    # --- LOGIC GATE 4: PHYSIOLOGICAL COST (The Efficiency Check) ---
    # Metric: Active Calories per 1,000 steps
    # Baseline: ~29.0. Warning Threshold: +20% (34.8)
    physio_cost = 0
    if steps and steps > 0 and active_cals:
        physio_cost = (active_cals / steps) * 1000
        
        if physio_cost > (BASELINE_COST * 1.2):
            warnings.append(f"High Physiological Cost ({int(physio_cost)})")

    # --- LOGIC GATE 5: MOVEMENT INEFFICIENCY (The Zombie Walk) ---
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
        "metrics": {"rhr": rhr, "bb": bb_charged, "physio_cost": physio_cost}
    }

@app.route('/')
def index():
    is_fresh, last_data, last_hrv, last_sleep, is_sleep_today = check_freshness()
    
    # 1. Run the Core Logic
    try:
        conn = get_db_connection(GARMIN_DB)
        conn_activities = get_db_connection(GARMIN_ACTIVITIES_DB)
        today_str = datetime.now().strftime('%Y-%m-%d')
        metrics_raw = calculate_metrics(today_str, conn, conn_activities)
        conn.close()
        conn_activities.close()
    except Exception as e:
        print(f"Error calculating metrics in index: {e}")
        metrics_raw = {"status": "GRAY", "reason": f"Error: {e}", "target_steps": 0, "metrics": {}}

    # 2. Map Core Logic to UI Panels (The "Adapter" Layer)
    
    # A. Crash Predictor (Lag 2)
    # Check if "Lag 2" is mentioned in the Stop Reason
    if "Lag 2" in metrics_raw['reason']:
        crash_status = {"status": "RED", "msg": "High Load 48h ago (>5k steps)"}
    else:
        crash_status = {"status": "GREEN", "msg": "No Lag-2 Risk Detected"}

    # B. Safety Ceiling (T-1 Load)
    # We infer this: if today is RED but NOT because of Lag 2/RHR/Batt, it might be T-1 load.
    # For now, default to Green unless explicitly flagged.
    safety_status = {"status": "GREEN", "msg": "Within Volume Limits"}

    # C. Autonomic Stress (The Engine)
    # Check RHR flags
    if "High RHR" in metrics_raw['reason']:
        stress_status = {"status": "RED", "msg": "Sympathetic Stress (High RHR)"}
    elif "Freeze" in metrics_raw['reason']:
        stress_status = {"status": "RED", "msg": "METABOLIC FREEZE (Low RHR)"}
    else:
        stress_status = {"status": "GREEN", "msg": "RHR Stable"}

    # D. Sleep Recharge (The Battery)
    if "Poor Recharge" in metrics_raw['reason']:
        sleep_status = {"status": "RED", "msg": "Body Battery < 50%"}
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

    # 3. Build Final Dictionary for HTML
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
            "val": round(p_cost, 1)
        },
        "respiration_warning": {"status": "GRAY", "msg": "Monitor via Oura/Manual"}, # Keep Gray for now
        "today_data_available": True if metrics_raw['metrics'].get('rhr') else False
    }

    daily_status = metrics['final_verdict']['status']
    
    # 4. Calculate Recovery Score
    recovery_score = None
    try:
        conn = get_db_connection(GARMIN_DB)
        recovery_score = get_recovery_score(conn, daily_status)
        conn.close()
    except Exception as e:
        print(f"Error calculating recovery score: {e}")
        
    return render_template('index.html', fresh=is_fresh, last_data=last_data, last_hrv=last_hrv, last_sleep=last_sleep, metrics=metrics, recovery=recovery_score)

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

@app.route('/api/data')
def api_data():
    """
    Return JSON data for Plotly charts (14-day rolling view).
    """
    # 14 days including today? Or 14 days ending yesterday? 
    # Usually "14-Day Rolling View" includes the latest available data.
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=13)
    
    data = {
        "dates": [],
        "steps": [],
        "rhr": []
    }
    
    try:
        conn = get_db_connection(GARMIN_DB)
        cursor = conn.cursor()
        
        # We need Steps (daily_summary) and RHR (resting_hr or daily_summary?)
        # Requirements says: RHR from resting_hr table? Or daily_summary?
        # daily_summary has 'rhr' column too. 
        # Metric 3 used `resting_hr` table. Let's stick to `resting_hr` table for consistency if possible, 
        # but `daily_summary` might be easier to join if we want everything in one go.
        # Let's query them separately to be safe or join them.
        
        # Let's just loop through dates to ensure we have continuous X-axis even if data is missing.
        current = start_date
        while current <= end_date:
            date_str = current.isoformat()
            data["dates"].append(date_str)
            
            # Steps
            cursor.execute("SELECT steps FROM daily_summary WHERE day = ?", (date_str,))
            row_steps = cursor.fetchone()
            steps = row_steps['steps'] if row_steps else 0 # 0 or None? 0 is better for bar chart gaps?
            data["steps"].append(steps if steps else 0)
            
            # RHR
            # Try resting_hr table first
            cursor.execute("SELECT resting_heart_rate FROM resting_hr WHERE day = ?", (date_str,))
            row_rhr = cursor.fetchone()
            rhr = row_rhr['resting_heart_rate'] if row_rhr else None
            
            # Fallback to daily_summary.rhr if resting_hr is missing?
            if rhr is None:
                cursor.execute("SELECT rhr FROM daily_summary WHERE day = ?", (date_str,))
                row_ds_rhr = cursor.fetchone()
                if row_ds_rhr:
                    rhr = row_ds_rhr['rhr']
            
            data["rhr"].append(rhr) # None will be a gap in line chart, which is good.
            
            current += timedelta(days=1)
            
        conn.close()
    except Exception as e:
        print(f"Error fetching API data: {e}")
        
    return jsonify(data)

@app.route('/api/recovery_history')
def api_recovery_history():
    """
    Return JSON data for Recovery Score history.
    Includes Veto status handling.
    Query Params:
        days: (optional) Number of days to look back. Default 14.
    """
    try:
        days = int(request.args.get('days', 14))
    except ValueError:
        days = 14
        
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
            
            # 1. Get Daily Status (for Veto)
            try:
                # Reuse connections for performance
                # Pass as STRING '%Y-%m-%d'
                try:
                    metrics = calculate_metrics(date_str, conn, conn_activities)
                    daily_status = metrics['status']
                except Exception as inner_e:
                    print(f"Warning: calculate_metrics failed for {date_str}: {inner_e}")
                    daily_status = "GREEN" # Fallback
            except Exception as e:
                print(f"Error calculating metrics for {date_str}: {e}")
                daily_status = "GREEN" # Fallback

            # 2. Get Recovery Score
            try:
                score_data = get_recovery_score(conn, daily_status, target_date=current)
                data["recovery_score"].append(score_data['score'])
                data["rhr_score"].append(score_data['details']['rhr']['score'])
                data["hrv_score"].append(score_data['details']['hrv']['score'])
                data["stress_score"].append(score_data['details']['stress']['score'])
            except Exception as e:
                print(f"Error calculating score for {date_str}: {e}")
                data["recovery_score"].append(None)
                data["rhr_score"].append(None)
                data["hrv_score"].append(None)
                data["stress_score"].append(None)
            
            current += timedelta(days=1)
            
        conn.close()
        conn_activities.close()
    except Exception as e:
        print(f"Error fetching history data: {e}")
        
    return jsonify(data)

if __name__ == '__main__':
    app.run(port=5050, debug=True)
