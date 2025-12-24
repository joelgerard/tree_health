import sqlite3
import os
import math
from datetime import datetime, timedelta
import csv

# Configuration
DB_DIR = os.path.expanduser("/Users/joelgerard/Library/CloudStorage/GoogleDrive-joelgerard@gmail.com/My Drive/joel health/tree health/DBs")
GARMIN_DB = os.path.join(DB_DIR, "garmin.db")
GARMIN_ACTIVITIES_DB = os.path.join(DB_DIR, "garmin_activities.db")

def get_connection(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def parse_duration(d_str):
    # Parses duration string (e.g., "00:03:00" or "00:03:00.000") to total minutes
    if not d_str:
        return 0.0
    try:
        parts = d_str.split(':')
        if len(parts) == 3:
            h = int(parts[0])
            m = int(parts[1])
            s = float(parts[2])
            return h * 60 + m + s/60
        # Handle cases like "123.45" just in case? Unlikely for TIME type.
        return 0.0
    except:
        return 0.0
        
def analyze_sleep_fragmentation():
    print("\n--- Analysis 1: Sleep Fragmentation ---")
    conn = get_connection(GARMIN_DB)
    
    # Query Sleep + RHR (Next Day)
    # We join Sleep (Day T) with RHR (Day T+1)
    # Actually, let's just fetch all and match in python
    sleep_rows = conn.execute("SELECT day, deep_sleep, total_sleep FROM sleep").fetchall()
    rhr_rows = conn.execute("SELECT day, resting_heart_rate FROM resting_hr").fetchall()
    
    rhr_map = {row['day']: row['resting_heart_rate'] for row in rhr_rows if row['day']}
    
    data_points = []
    
    for row in sleep_rows:
        day_t = row['day']
        if not day_t: continue
        
        # Calculate Next Day
        try:
            date_t = datetime.strptime(day_t, "%Y-%m-%d").date()
            date_next = date_t + timedelta(days=1)
            day_next_str = date_next.isoformat()
            
            if day_next_str in rhr_map:
                rhr_next = rhr_map[day_next_str]
                deep = parse_duration(row['deep_sleep'])
                total = parse_duration(row['total_sleep'])
                
                if total > 0 and rhr_next is not None:
                    ratio = deep / total
                    data_points.append({'ratio': ratio, 'rhr_next': rhr_next, 'date': day_t})
                    
        except Exception as e:
            continue
            
    # Correlation
    if not data_points:
        print("Not enough data points.")
        return

    # Helper for correlation
    n = len(data_points)
    sum_x = sum(d['ratio'] for d in data_points)
    sum_y = sum(d['rhr_next'] for d in data_points)
    sum_xy = sum(d['ratio'] * d['rhr_next'] for d in data_points)
    sum_x2 = sum(d['ratio']**2 for d in data_points)
    sum_y2 = sum(d['rhr_next']**2 for d in data_points)
    
    denominator = math.sqrt((n * sum_x2 - sum_x**2) * (n * sum_y2 - sum_y**2))
    corr = (n * sum_xy - sum_x * sum_y) / denominator if denominator != 0 else 0
    
    print(f"Data Points: {n}")
    print(f"Correlation (Deep Ratio vs Next Day RHR): {corr:.4f}")
    
    # Check "Low Deep Sleep Ratio (<15%)" prediction
    low_ratio_rhr = [d['rhr_next'] for d in data_points if d['ratio'] < 0.15]
    high_ratio_rhr = [d['rhr_next'] for d in data_points if d['ratio'] >= 0.15]
    
    avg_rhr_low = sum(low_ratio_rhr)/len(low_ratio_rhr) if low_ratio_rhr else 0
    avg_rhr_high = sum(high_ratio_rhr)/len(high_ratio_rhr) if high_ratio_rhr else 0
    
    print(f"Avg Next-Day RHR when Ratio < 15%: {avg_rhr_low:.2f} ({len(low_ratio_rhr)} days)")
    print(f"Avg Next-Day RHR when Ratio >= 15%: {avg_rhr_high:.2f} ({len(high_ratio_rhr)} days)")
    print(f"Difference: {avg_rhr_low - avg_rhr_high:.2f} bpm")

    conn.close()

def analyze_cadence_cost():
    print("\n--- Analysis 2: Cadence Cost ---")
    conn_act = get_connection(GARMIN_ACTIVITIES_DB)
    conn_bio = get_connection(GARMIN_DB)
    
    # Fetch Hiking/Walking activities
    query = """
        SELECT start_time, avg_cadence, avg_hr, type, sport 
        FROM activities 
        WHERE (type LIKE '%walking%' OR type LIKE '%hiking%' OR sport LIKE '%walking%')
        AND avg_cadence > 0 AND avg_hr > 0
    """
    activities = conn_act.execute(query).fetchall()
    
    # Fetch RHR for crash detection (Next Day)
    rhr_rows = conn_bio.execute("SELECT day, resting_heart_rate FROM resting_hr").fetchall()
    rhr_map = {row['day']: row['resting_heart_rate'] for row in rhr_rows if row['day']}
    
    inefficient_days = []
    efficient_days = []
    
    # Thresholds (Hypothesis: Cadence < 90 is shuffling)
    CADENCE_THRESHOLD = 90
    
    for act in activities:
        start_time = act['start_time'] # '2023-12-01 10:00:00'
        try:
            # Extract date
            # Handling different formats if necessary, but usually YYYY-MM-DD HH:MM:SS
            date_t = datetime.strptime(start_time[:10], "%Y-%m-%d").date()
            date_next = date_t + timedelta(days=1)
            day_next_str = date_next.isoformat()
            
            if day_next_str in rhr_map:
                rhr_next = rhr_map[day_next_str]
                if rhr_next is None: continue
                
                cadence = act['avg_cadence'] * 2 # Garmin sometimes stores steps per min (one foot) or both? 
                # "avg_cadence" in Garmin Connect is usually spm (total).
                # But sometimes it's 1-foot? Usually walking is 80-120 spm total.
                # If value is ~40-60, it's one foot. If ~80-120, it's two feet.
                # Let's inspect the data range.
                if cadence < 150 and cadence > 0: 
                    # Probably already SPM total. If slightly low, it confirms shuffling logic?
                    # Or if it's < 60, it might be single foot.
                    pass
                
                # Assume "spm" is the value in DB.
                # User says "e.g., <90 spm".
                
                # Metric: Efficency = Cadence / HR (Higher is better?)
                # Or just checking the specific condition: Low Cadence AND High HR.
                # "Cadence LOW but HR HIGH"
                
                if cadence < CADENCE_THRESHOLD:
                    inefficient_days.append(rhr_next)
                else:
                    efficient_days.append(rhr_next)
                    
        except Exception as e:
            continue
    
    # Debug Cadence distribution
    all_cadences = [x['avg_cadence'] for x in activities if x['avg_cadence']]
    if all_cadences:
        print(f"Cadence Stats: Min={min(all_cadences)}, Max={max(all_cadences)}, Avg={sum(all_cadences)/len(all_cadences):.1f}")
            
    avg_rhr_ineff = sum(inefficient_days)/len(inefficient_days) if inefficient_days else 0
    avg_rhr_eff = sum(efficient_days)/len(efficient_days) if efficient_days else 0
    
    print(f"Avg Next-Day RHR after LOW Cadence (<{CADENCE_THRESHOLD}): {avg_rhr_ineff:.2f} ({len(inefficient_days)} activities)")
    print(f"Avg Next-Day RHR after NORMAL Cadence (>={CADENCE_THRESHOLD}): {avg_rhr_eff:.2f} ({len(efficient_days)} activities)")
    print(f"Difference: {avg_rhr_ineff - avg_rhr_eff:.2f} bpm")
    
    conn_act.close()
    conn_bio.close()

def analyze_respiration():
    print("\n--- Analysis 3: Respiration Rate ---")
    conn = get_connection(GARMIN_DB)
    
    # rr_waking_avg vs HRV
    # Check if Rise > 1 correlates with Drop in HRV
    
    rows = conn.execute("SELECT day, rr_waking_avg, rr_min FROM daily_summary").fetchall()
    hrv_rows = conn.execute("SELECT day, last_night_avg FROM hrv").fetchall()
    
    cols_map = {}
    for r in rows:
        cols_map[r['day']] = {'rr': r['rr_waking_avg']}
    
    for r in hrv_rows:
        if r['day'] in cols_map:
            cols_map[r['day']]['hrv'] = r['last_night_avg']
    
    # Sort by date
    sorted_days = sorted([d for d in cols_map.keys() if d])
    
    # Check diffs
    rise_events = [] # HRV change when RR rises
    stable_events = [] # HRV change when RR stable
    
    for i in range(1, len(sorted_days)):
        day_curr = sorted_days[i]
        day_prev = sorted_days[i-1]
        
        # Ensure consecutive
        try:
            d_curr = datetime.strptime(day_curr, "%Y-%m-%d")
            d_prev = datetime.strptime(day_prev, "%Y-%m-%d")
            if (d_curr - d_prev).days != 1:
                continue
        except:
            continue
            
        data_curr = cols_map[day_curr]
        data_prev = cols_map[day_prev]
        
        rr_curr = data_curr.get('rr')
        rr_prev = data_prev.get('rr')
        hrv_curr = data_curr.get('hrv')
        hrv_prev = data_prev.get('hrv')
        
        if rr_curr is not None and rr_prev is not None and hrv_curr is not None and hrv_prev is not None:
            rr_delta = rr_curr - rr_prev
            hrv_delta = hrv_curr - hrv_prev
            
            if rr_delta > 1.0:
                rise_events.append(hrv_delta)
            elif abs(rr_delta) <= 0.5:
                stable_events.append(hrv_delta)
                
    avg_hrv_drop_rise = sum(rise_events)/len(rise_events) if rise_events else 0
    avg_hrv_drop_stable = sum(stable_events)/len(stable_events) if stable_events else 0
    
    print(f"Avg HRV Change when RR spiked (>1 brpm): {avg_hrv_drop_rise:.2f} ms ({len(rise_events)} events)")
    print(f"Avg HRV Change when RR stable: {avg_hrv_drop_stable:.2f} ms ({len(stable_events)} events)")
    print(f"Signal: HRV drops by {abs(avg_hrv_drop_rise - avg_hrv_drop_stable):.2f} ms more when RR spikes?")
    
    analyze_respiration_lag(conn)
    conn.close()

def analyze_respiration_lag(conn):
    print("\n--- Analysis 3B: Respiration Rate (Lagged T -> HRV T+1) ---")
    
    rows = conn.execute("SELECT day, rr_waking_avg FROM daily_summary").fetchall()
    hrv_rows = conn.execute("SELECT day, last_night_avg FROM hrv").fetchall()
    
    # Map Day -> Data
    rr_map = {r['day']: r['rr_waking_avg'] for r in rows if r['day']}
    hrv_map = {r['day']: r['last_night_avg'] for r in hrv_rows if r['day']}
    
    # Look for Rise in RR(T) vs RR(T-1) -> Impact on HRV(T+1)
    
    rise_impacts = []
    stable_impacts = []
    
    sorted_days = sorted(rr_map.keys())
    
    for i in range(1, len(sorted_days)):
        day_t = sorted_days[i]
        day_prev = sorted_days[i-1] # T-1
        
        # Check consecutive
        try:
            dt = datetime.strptime(day_t, "%Y-%m-%d")
            dp = datetime.strptime(day_prev, "%Y-%m-%d")
            if (dt - dp).days != 1: continue
            
            # T+1
            dt_next = dt + timedelta(days=1)
            day_next = dt_next.strftime("%Y-%m-%d")
            
        except: continue
        
        rr_t = rr_map.get(day_t)
        rr_prev = rr_map.get(day_prev)
        
        hrv_next = hrv_map.get(day_next)
        hrv_t = hrv_map.get(day_t) # Baseline for change? Or just absolute value?
        # User asked: "correlates with a drop in HRV". Usually implies HRV(T+1) < HRV(T).
        
        if rr_t and rr_prev and hrv_next and hrv_t:
            rr_delta = rr_t - rr_prev
            hrv_delta = hrv_next - hrv_t
            
            if rr_delta > 1.0:
                rise_impacts.append(hrv_delta)
            elif abs(rr_delta) <= 0.5:
                stable_impacts.append(hrv_delta)
                
    avg_rise = sum(rise_impacts)/len(rise_impacts) if rise_impacts else 0
    avg_stable = sum(stable_impacts)/len(stable_impacts) if stable_impacts else 0
    
    print(f"Avg HRV Change (T -> T+1) when RR(T) spiked: {avg_rise:.2f} ms ({len(rise_impacts)} events)")
    print(f"Avg HRV Change (T -> T+1) when RR(T) stable: {avg_stable:.2f} ms ({len(stable_impacts)} events)")
    diff = avg_rise - avg_stable
    print(f"Prediction: After RR spike, HRV changes by {diff:.2f} ms compared to stable RR.")


if __name__ == "__main__":
    analyze_sleep_fragmentation()
    analyze_cadence_cost()
    analyze_respiration()
