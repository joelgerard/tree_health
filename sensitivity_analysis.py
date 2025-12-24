import sqlite3
import os
import datetime
import statistics

# Configuration
DB_DIR = os.path.expanduser("/Users/joelgerard/Library/CloudStorage/GoogleDrive-joelgerard@gmail.com/My Drive/joel health/tree health/DBs")
GARMIN_DB = os.path.join(DB_DIR, "garmin.db")

def get_db_connection(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def analyze_sensitivity():
    print(f"Connecting to {GARMIN_DB}...")
    try:
        conn = get_db_connection(GARMIN_DB)
        
        # 1. Fetch Daily Summary
        print("Fetching Daily Summary...")
        cursor = conn.cursor()
        cursor.execute("SELECT day, steps, hr_max FROM daily_summary")
        daily_rows = cursor.fetchall()
        daily_data = {row['day']: dict(row) for row in daily_rows}
        
        # 2. Fetch HRV Data
        print("Fetching HRV Data...")
        cursor.execute("SELECT day, last_night_avg, weekly_avg FROM hrv")
        hrv_rows = cursor.fetchall()
        hrv_data = {row['day']: dict(row) for row in hrv_rows}
        
        conn.close()
    except Exception as e:
        print(f"Error reading DB: {e}")
        return

    # Merge Data
    merged = []
    
    for day_str, daily in daily_data.items():
        day_date = datetime.datetime.strptime(day_str, "%Y-%m-%d").date()
        
        # T+2 for HRV
        t_plus_2 = day_date + datetime.timedelta(days=2)
        t_plus_2_str = t_plus_2.isoformat()
        
        # T+1 for HRV (for HR Max calc)
        t_plus_1 = day_date + datetime.timedelta(days=1)
        t_plus_1_str = t_plus_1.isoformat()
        
        hrv_t2 = hrv_data.get(t_plus_2_str)
        hrv_t1 = hrv_data.get(t_plus_1_str)
        
        if hrv_t2 and hrv_t2['last_night_avg'] and hrv_t2['weekly_avg']:
            # Check crash T+2
            # Crash: HRV < (Weekly - 5)
            is_crash_t2 = hrv_t2['last_night_avg'] < (hrv_t2['weekly_avg'] - 5)
        else:
            is_crash_t2 = False # Missing data assumed no crash for analysis strictly?
            
        if hrv_t1 and hrv_t1['last_night_avg'] and hrv_t1['weekly_avg']:
             # Check crash T+1
            is_crash_t1 = hrv_t1['last_night_avg'] < (hrv_t1['weekly_avg'] - 5)
        else:
            is_crash_t1 = False
            
        merged.append({
            'day': day_str,
            'steps': daily['steps'] or 0,
            'hr_max': daily['hr_max'],
            'is_crash_t2': is_crash_t2,
            'is_crash_t1': is_crash_t1,
            'is_crash_sequence': is_crash_t2 or is_crash_t1
        })
    
    if not merged:
        print("No matching records found.")
        return

    print(f"Total Days Analyzed: {len(merged)}")
    crashes_t2 = sum(1 for m in merged if m['is_crash_t2'])
    print(f"Total Crashes Detected (T+2): {crashes_t2}")
    
    # --- STEP 1: CALCULATED_STEP_CAP ---
    print("\n--- STEP 1: CALCULATED_STEP_CAP ---")
    
    # Binning
    bins = {} # key: bin_start (0, 1000, 2000...), val: [matches, crashes]
    
    for m in merged:
        steps = m['steps']
        bin_start = (steps // 1000) * 1000
        if bin_start not in bins:
            bins[bin_start] = {'count': 0, 'crashes': 0}
        
        bins[bin_start]['count'] += 1
        if m['is_crash_t2']:
             bins[bin_start]['crashes'] += 1
             
    # Analyze Bins
    unsafe_bins = []
    print(f"{'Bin':<10} {'Count':<10} {'Crashes':<10} {'Prob':<10}")
    for b in sorted(bins.keys()):
        stats = bins[b]
        prob = stats['crashes'] / stats['count'] if stats['count'] > 0 else 0
        print(f"{b:<10} {stats['count']:<10} {stats['crashes']:<10} {prob:.2f}")
        
        if prob > 0.5 and stats['count'] >= 3:
            unsafe_bins.append(b)
            
    if unsafe_bins:
        calculated_step_cap = min(unsafe_bins)
        print(f"FOUND CRASH THRESHOLD: {calculated_step_cap} steps")
    else:
        print("No clear >50% crash threshold found.")
        calculated_step_cap = 5000
        
    # --- STEP 2: CALCULATED_HR_MAX_CAP ---
    print("\n--- STEP 2: CALCULATED_HR_MAX_CAP ---")
    
    crash_days_hr = [m['hr_max'] for m in merged if m['is_crash_sequence'] and m['hr_max']]
    print(f"Days leading to crash (T+1 or T+2) with HR data: {len(crash_days_hr)}")
    
    if crash_days_hr:
        calculated_hr_max_cap = int(statistics.median(crash_days_hr))
        print(f"Median HR Max on Crash-Inducing Days: {calculated_hr_max_cap}")
    else:
        print("No crash sequences found.")
        calculated_hr_max_cap = 130

    print("\n--- FINAL RESULTS ---")
    print(f"CALCULATED_STEP_CAP = {calculated_step_cap}")
    print(f"CALCULATED_HR_MAX_CAP = {calculated_hr_max_cap}")

if __name__ == "__main__":
    analyze_sensitivity()
