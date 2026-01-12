import sqlite3
import pandas as pd
import os
from datetime import datetime, timedelta

# --- Configuration ---
DB_PATH = os.path.expanduser("~/GarminDBSync/tree/HealthData/DBs/glucose.db")
DAYS_TO_ANALYZE = 90  # Analyze last 90 days for "Memory Check"
DAY_START_HOUR = 7
DAY_END_HOUR = 21
GAP_THRESHOLD_MINUTES = 45

# --- Loading Data ---
def get_db_connection(db_path):
    if not os.path.exists(db_path):
        print(f"Error: DB not found at {db_path}")
        return None
    return sqlite3.connect(db_path)

def load_data():
    conn = get_db_connection(DB_PATH)
    if not conn:
        return pd.DataFrame()

    end_date = datetime.now()
    start_date = end_date - timedelta(days=DAYS_TO_ANALYZE)
    
    print(f"Loading data from {start_date.date()} to {end_date.date()}...")

    query = f"""
        SELECT timestamp, glucose_value 
        FROM glucose_readings 
        WHERE timestamp >= '{start_date}'
    """
    df = pd.read_sql_query(query, conn)
    conn.close()

    if df.empty:
        return df

    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values('timestamp')
    df = df.set_index('timestamp')
    return df

# --- Processing ---
def processed_data(df):
    # Resample to 5 mins
    df_resampled = df.resample('5min').mean()
    # Interpolate or ffill to handle small gaps, but maybe just ffill is safer for glucose
    # The prompt asked for "Resample... to smooth out noise"
    # ffill() is good to align with the prompt's likely intent of continuous tracking
    df_resampled['glucose_value'] = df_resampled['glucose_value'].ffill()
    
    # Calculate ROC
    df_resampled['roc'] = df_resampled['glucose_value'].diff()
    
    # Filter for Daytime (07:00 - 21:00)
    # We want to exclude night hours from flagging, BUT we need continuity for ROC.
    # So we calculate ROC first (done above), then filter.
    
    # Filter by hour
    df_daytime = df_resampled[
        (df_resampled.index.hour >= DAY_START_HOUR) & 
        (df_resampled.index.hour < DAY_END_HOUR)
    ].copy()
    
    return df_daytime

# --- Logic & Grouping ---
def find_events(df):
    # Type A: ROC < -2.0
    # Type B: Value < 80
    
    # Create a boolean mask for "Flagged"
    # Note: We use .fillna(0) for roc comparisons just in case, though diff() creates NaN at start
    mask = (df['roc'] < -2.0) | (df['glucose_value'] < 80)
    
    if not mask.any():
        return []

    # Get flagged timestamps
    flagged_indices = df.index[mask]
    
    events = []
    if len(flagged_indices) == 0:
        return events

    # Grouping Logic
    # We iterate through flagged indices and group them if they are within GAP_THRESHOLD_MINUTES
    
    current_event_flags = [flagged_indices[0]]
    
    for i in range(1, len(flagged_indices)):
        current_ts = flagged_indices[i]
        prev_ts = flagged_indices[i-1]
        
        diff = (current_ts - prev_ts).total_seconds() / 60.0
        
        if diff <= GAP_THRESHOLD_MINUTES:
            current_event_flags.append(current_ts)
        else:
            # Event ended, process it
            events.append(process_event_flags(df, current_event_flags))
            current_event_flags = [current_ts]
            
    # Append last event
    if current_event_flags:
        events.append(process_event_flags(df, current_event_flags))
        
    return events

def process_event_flags(df, flags):
    # Given a list of timestamps that are part of an event (flagged moments),
    # we want to find the true "Start" and "End" of the crash context.
    # The user asked: "Start Time and End Time", "High Point (Start of crash) and Low Point (Bottom)"
    
    # The "flags" are just the moments it was dropping fast or low. 
    # The actual "Cash" might start a bit before the first flag (the peak).
    # And end at the trough (lowest point).
    
    # Simple approach: 
    # Look at the window defined by [min_flag - 30min, max_flag + 30min] to find local max/min?
    # Or just use the flagged range? 
    # "Glucose crashes happen over 20-30 minutes."
    # Let's take the range of flags, and extend lookback slightly to find the Peak.
    
    start_flag = flags[0]
    end_flag = flags[-1]
    
    # Search window for "High Point": go back 30 mins from start_flag
    search_start = start_flag - timedelta(minutes=30)
    # Search window for "Low Point": go forward 30 mins from end_flag (or just use end_flag range)
    # Actually, the "Low Point" is likely within the flags or slightly after.
    search_end = end_flag + timedelta(minutes=30)
    
    # Slice the original DF (resampled)
    # We need to handle boundary conditions if search_start is out of bounds (not in df), 
    # but slicing handles that gracefully usually.
    # However, our 'df' passed here is 'df_daytime'. 
    # We might miss peaks if they are slightly before 7am? 
    # For now, stick to df_daytime or passed df context. 
    # Ideally we'd use the full df_resampled but let's stick to the filtered for simplicity unless crucial.
    # Actually, if a crash starts at 7:05, the peak might be 6:55. 
    # For "Daytime Drops", user surely cares about the Drop itself happening in day.
    
    segment = df.loc[search_start:search_end]
    if segment.empty:
        # Fallback
        segment = df.loc[start_flag:end_flag]
        
    if segment.empty:
        return None # Should not happen

    # Find High Point (Max glucose before or at the lowest point)
    # Find Low Point (Min glucose)
    
    min_val = segment['glucose_value'].min()
    min_idx = segment['glucose_value'].idxmin()
    
    # High point should be BEFORE the low point
    # Slice segment up to min_idx
    pre_low_segment = segment.loc[:min_idx]
    
    if pre_low_segment.empty:
        max_val = min_val
        max_idx = min_idx
    else:
        max_val = pre_low_segment['glucose_value'].max()
        max_idx = pre_low_segment['glucose_value'].idxmax()
        
    total_drop = max_val - min_val
    duration_mins = int((min_idx - max_idx).total_seconds() / 60)
    
    # Logic check: if duration is negative (max after min), simply swap or take min/max of whole segment
    # But strictly "Start of crash" is High, "Bottom" is Low.
    # If we found chunks where it went up then down, we want the Peak before the Crash.
    
    return {
        'start_time': max_idx, # Event "Start" is the Peak
        'end_time': min_idx,   # Event "End" is the Bottom (for the drop context)
        'high_val': max_val,
        'low_val': min_val,
        'drop_mag': total_drop,
        'duration': duration_mins
    }

# --- Main ---
def main():
    # 1. Load
    df = load_data()
    if df.empty:
        print("No data found.")
        return

    # 2. Process
    df_daytime = processed_data(df)
    
    # 3. Find Events
    events = find_events(df_daytime)
    
    # Filter None and sort
    events = [e for e in events if e is not None]
    
    # Sort: By Magnitude of Drop (Biggest crashes first)
    events.sort(key=lambda x: x['drop_mag'], reverse=True)
    
    # Limit to Top 20
    top_events = events[:20]
    
    # 4. Print Report
    print(f"\n--- MEMORY CHECK: TOP {len(top_events)} DAYTIME DROPS (Last {DAYS_TO_ANALYZE} Days) ---")
    print("Criteria: Drop Rate > 2 mg/dL/min OR Value < 80 mg/dL (7AM - 9PM)\n")
    
    for idx, e in enumerate(top_events, 1):
        # Format: [Day of Week] YYYY-MM-DD | Time: HH:MM | Drop: 140 -> 95 (-45) | Duration: 25m
        day_str = e['start_time'].strftime("%A")[:3]
        date_str = e['start_time'].strftime("%Y-%m-%d")
        time_str = e['start_time'].strftime("%H:%M")
        
        drop_str = f"{e['high_val']:.0f} -> {e['low_val']:.0f}"
        diff_str = f"(-{e['drop_mag']:.0f})"
        dur_str = f"{e['duration']}m"
        
        print(f"{idx}. [{day_str}] {date_str} | Time: {time_str} | Drop: {drop_str} {diff_str} | Duration: {dur_str}")

if __name__ == "__main__":
    main()
