import sqlite3
import os
import subprocess
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)

# Configuration
# DB_DIR = os.path.expanduser("/Users/joelgerard/tree_home/HealthData/DBs")

DB_DIR = os.path.expanduser("/Users/joelgerard/Library/CloudStorage/GoogleDrive-joelgerard@gmail.com/My Drive/joel health/tree health/DBs")
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

def get_daily_data(day_date):
    """
    Fetch daily summary for a specific date object.
    """
    try:
        conn = get_db_connection(GARMIN_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM daily_summary WHERE day = ?", (day_date.isoformat(),))
        row = cursor.fetchone()
        conn.close()
        return row
    except Exception as e:
        print(f"Error fetching daily data for {day_date}: {e}")
        return None

def get_resting_hr(day_date):
    try:
        conn = get_db_connection(GARMIN_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT resting_heart_rate FROM resting_hr WHERE day = ?", (day_date.isoformat(),))
        row = cursor.fetchone()
        conn.close()
        return row['resting_heart_rate'] if row else None
    except Exception:
        return None

def get_hrv_data(day_date):
    try:
        conn = get_db_connection(GARMIN_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM hrv WHERE day = ?", (day_date.isoformat(),))
        row = cursor.fetchone()
        conn.close()
        return row
    except Exception as e:
        print(f"Error fetching HRV data for {day_date}: {e}")
        return None

def get_activities(day_date):
    """
    Fetch walking/hiking activities for a specific date.
    """
    try:
        conn = get_db_connection(GARMIN_ACTIVITIES_DB)
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

def get_recovery_score(conn, daily_status):
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
    today = datetime.now().date()
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

def calculate_metrics():
    """
    Query databases and apply strict logic rules for the dashboard.
    """
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    two_days_ago = today - timedelta(days=2)

    metrics = {
        "crash_predictor": {"status": "GREEN", "msg": "No risk detected"},
        "safety_ceiling": {"status": "GREEN", "msg": "Within limits"},
        "autonomic_stress": {"status": "GREEN", "msg": "Normal"},
        "sleep_recharge": {"status": "GREEN", "msg": "Good recharge"},
        "efficiency_check": {"status": "GREEN", "msg": "Efficient movement"},
        "respiration_warning": {"status": "GREEN", "msg": "Stable breathing"},
        "final_verdict": {"status": "GREEN", "msg": "Safe to proceed", "target": "4,500 Steps"},
        "warnings_count": 0
    }

    # --- Metric 1: Crash Predictor (T-2) ---
    t2_data = get_daily_data(two_days_ago)
    if t2_data:
        steps_t2 = t2_data['steps'] or 0
        vigorous_t2_str = t2_data['vigorous_activity_time']
        vigorous_t2_mins = parse_time_str(vigorous_t2_str)
        
        # Crash Logic: 
        # In Raw Mode (Phase 1), we ONLY look at Step Count (Raw). 
        # We ignore Vigorous Activity because it relies on HR Zones (which are skewed).
        
        is_risk = False
        risk_msg = ""
        
        if steps_t2 > STEP_CAP_LAG:
            is_risk = True
            risk_msg = "⚠️ High Risk: Step count delayed fatigue."
            
        if USE_GARMIN_ZONES:
             if vigorous_t2_mins > 20:
                 is_risk = True
                 risk_msg = "⚠️ High Risk: Vigorous activity delayed fatigue."
        
        if is_risk:
            metrics["crash_predictor"] = {"status": "RED", "msg": risk_msg if risk_msg else "⚠️ High Risk detected."}
            metrics["warnings_count"] += 1

    # --- Metric 2: Safety Ceiling (T-1) ---
    t1_data = get_daily_data(yesterday)
    if t1_data:
        steps_t1 = t1_data['steps'] or 0
        hr_max_t1 = t1_data['hr_max'] or 0
        
        # Check raw sensor limits
        if steps_t1 > 4500 or hr_max_t1 > HR_MAX_CAP:
            msg = "⚠️ Warning: You exceeded the safety cap yesterday."
            if hr_max_t1 > HR_MAX_CAP:
                msg += f" (HR Max {hr_max_t1} > {HR_MAX_CAP})"
            metrics["safety_ceiling"] = {"status": "YELLOW", "msg": msg}
            metrics["warnings_count"] += 1

    # --- Metric 3: Autonomic Stress (Today/Yesterday) ---
    # RHR usually has today's value if sync happened, but might rely on yesterday's if morning.
    # Requirement says: Query `overnight_hrv` (last night) and `resting_heart_rate` (yesterday/today).
    # We'll try today's RHR first, then yesterday's? "resting_heart_rate (yesterday/today)" usually implies latest available.
    # Let's try today first.
    rhr = get_resting_hr(today)
    if rhr is None:
        rhr = get_resting_hr(yesterday)
    
    # HRV for "Last Night" is usually logged with Today's date in Garmin exports (Sleep date).
    # Let's verify this assumption. Usually "Overnight HRV" for the sleep ending today is dated Today.
    hrv_row = get_hrv_data(today)
    
    if rhr is not None and hrv_row:
        hrv_val = hrv_row['last_night_avg']
        seven_day = hrv_row['weekly_avg']
        
        # Rule: If RHR > 53 bpm OR HRV < [7-day-avg minus 5ms]
        is_stress = False
        if rhr > 53:
            is_stress = True
        if hrv_val and seven_day and (hrv_val < (seven_day - 5)):
            is_stress = True
            
        if is_stress:
            metrics["autonomic_stress"] = {"status": "RED", "msg": "❤️ Physiological Stress detected."}
            metrics["warnings_count"] += 1
    
    # --- Metric 4: Sleep Recharge (Today) ---
    # Looking for BB Charged from last night's sleep (Today's summary)
    today_data = get_daily_data(today)
    metrics["today_data_available"] = False
    
    if today_data:
        metrics["today_data_available"] = True
        bb_charged = today_data['bb_charged']
        # Rule: If bb_charged < 50
        if bb_charged is not None and bb_charged < 50:
            metrics["sleep_recharge"] = {"status": "YELLOW", "msg": "🔋 Poor Recharge."}
            # Note: The prompt implies this contributes to the verdict. 
            # "If 2+ warnings or High RHR".
            # Is "Poor Recharge" a warning? 
            # Prompt says "High Risk" (Metric 1), "Warning" (Metric 2), "Physiological Stress" (Metric 3).
            # "Poor Recharge" (Metric 4).
            # Let's count it as a warning for now.
            metrics["warnings_count"] += 1



    # --- Metric 5: Efficiency Check (Cadence Cost T-1) ---
    # Low Cadence yesterday (<90 spm total, ~45 1-foot) -> Fatigue Risk Today
    activities_t1 = get_activities(yesterday)
    if activities_t1:
        for act in activities_t1:
            cadence = act['avg_cadence'] # 1-foot cadence from DB
            if cadence and cadence > 0 and cadence < 45:
                # 45 * 2 = 90 spm threshold
                metrics["efficiency_check"] = {"status": "YELLOW", "msg": "⚠️ Inefficient Movement (Shuffling) detected yesterday."}
                metrics["warnings_count"] += 1
                break

    # --- Metric 6: Respiration Early Warning (T-1 vs T-2) ---
    # Rise in RR > 1.0 brpm predicts crash
    if t1_data and t2_data:
        rr_t1 = t1_data['rr_waking_avg']
        rr_t2 = t2_data['rr_waking_avg']
        
        if rr_t1 and rr_t2:
            rr_delta = rr_t1 - rr_t2
            if rr_delta > 1.0:
                metrics["respiration_warning"] = {"status": "YELLOW", "msg": f"⚠️ Elevated Breathing (+{rr_delta:.1f} brpm) detected."}
                metrics["warnings_count"] += 1

    # --- Final Verdict ---
    # Collect reasons
    reasons = []
    
    # Check individual metrics for non-GREEN status
    if metrics["crash_predictor"]["status"] != "GREEN":
        reasons.append("T-2 Fatigue Trigger")
    if metrics["safety_ceiling"]["status"] != "GREEN":
        reasons.append("Safety Ceiling Exceeded")
    if metrics["autonomic_stress"]["status"] != "GREEN":
        reasons.append("Physiological Stress")
    if metrics["sleep_recharge"]["status"] != "GREEN":
        reasons.append("Poor Sleep Recharge")
    if metrics["efficiency_check"]["status"] != "GREEN":
        reasons.append("Inefficient Movement")
    if metrics["respiration_warning"]["status"] != "GREEN":
        reasons.append("Elevated Breathing")

    # RHR Override Reason
    rhr_high = False
    if rhr and rhr > 53:
        rhr_high = True
        if "Physiological Stress" not in reasons:
            reasons.append("High RHR (>53)")

    warnings = metrics["warnings_count"]
    
    if warnings >= 2 or rhr_high:
        base_msg = "🛑 STOP. Rest Day."
        if rhr_high:
             base_msg = "🛑 STOP. High RHR detected."
             
        reason_str = ", ".join(reasons)
        metrics["final_verdict"] = {
            "status": "RED", 
            "msg": f"{base_msg} Reasons: {reason_str}. Target: <1,500 Steps.",
            "target": "< 1,500 Steps"
        }
    elif warnings == 1:
        reason_str = ", ".join(reasons)
        metrics["final_verdict"] = {
            "status": "YELLOW", 
            "msg": f"⚠️ Caution. Reasons: {reason_str}. Target: 3,000 Steps.",
            "target": "3,000 Steps"
        }
    else:
        metrics["final_verdict"] = {
            "status": "GREEN", 
            "msg": "✅ Safe to proceed. Target: 4,500 Steps.",
            "target": "4,500 Steps"
        }

    return metrics

@app.route('/')
def index():
    is_fresh, last_data, last_hrv, last_sleep, is_sleep_today = check_freshness()
    metrics = calculate_metrics()
    daily_status = metrics['final_verdict']['status']
    
    # Calculate Recovery Score
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

if __name__ == '__main__':
    app.run(port=5050, debug=True)
