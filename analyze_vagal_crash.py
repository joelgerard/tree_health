import sqlite3
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import os
from datetime import datetime, timedelta

# Configuration
ACTIVITIES_DB = "/Users/joelgerard/Library/CloudStorage/GoogleDrive-joelgerard@gmail.com/My Drive/joel health/tree health/DBs/garmin_activities.db"
GARMIN_DB = "/Users/joelgerard/Library/CloudStorage/GoogleDrive-joelgerard@gmail.com/My Drive/joel health/tree health/DBs/garmin.db"
REPORT_DIR = "reports"
REPORT_FILE = os.path.join(REPORT_DIR, "vagal_crash_report.html")

# Analysis Config
START_DATE = '2025-06-01'
BRADY_THRESHOLD_BPM = 45
BRADY_DURATION_SEC = 15     # UPDATED: Was 5
ZOMBIE_CADENCE_THRESHOLD = 90
ZOMBIE_INEFFICIENCY_PCT = 50 # UPDATED: Was 40
CRASH_RHR_OFFSET = 2  # RHR > Baseline + 2 OR RHR < Baseline - 2
CRASH_BB_MAX_THRESHOLD = 50

def ensure_report_dir():
    if not os.path.exists(REPORT_DIR):
        os.makedirs(REPORT_DIR)

def get_activity_events():
    conn = sqlite3.connect(ACTIVITIES_DB)
    
    # Get activities since START_DATE
    activities_query = f"""
    SELECT activity_id, start_time, name
    FROM activities 
    WHERE start_time >= '{START_DATE}'
    """
    activities = pd.read_sql_query(activities_query, conn)
    activities['start_time'] = pd.to_datetime(activities['start_time'])
    activities['date'] = activities['start_time'].dt.date
    
    events = []
    
    print(f"Scanning {len(activities)} activities for Vagal Events...")
    
    for idx, act in activities.iterrows():
        act_id = act['activity_id']
        
        # Get records for this activity
        records_query = f"SELECT hr, cadence, speed FROM activity_records WHERE activity_id = '{act_id}'"
        records = pd.read_sql_query(records_query, conn)
        
        if len(records) == 0:
            continue
            
        # 1. Brady Event Detection
        brady_seconds = len(records[records['hr'] < BRADY_THRESHOLD_BPM])
        has_brady = brady_seconds > BRADY_DURATION_SEC
        
        # 2. Zombie Walk Detection
        # Filter for moving time (speed > 0.5 m/s) to avoid stopped time
        moving_records = records[records['speed'] > 0.5]
        if len(moving_records) > 60: # Minimum 1 minute moving
            inefficient_seconds = len(moving_records[moving_records['cadence'] < ZOMBIE_CADENCE_THRESHOLD])
            inefficiency_pct = (inefficient_seconds / len(moving_records)) * 100
            is_zombie = inefficiency_pct > ZOMBIE_INEFFICIENCY_PCT
        else:
            is_zombie = False
            
        if has_brady or is_zombie:
            events.append({
                'date': act['date'],
                'has_brady': has_brady,
                'is_zombie': is_zombie,
                'activity_id': act_id
            })
            
    conn.close()
    return pd.DataFrame(events)

def get_daily_outcomes():
    conn = sqlite3.connect(GARMIN_DB)
    
    query = f"""
    SELECT day, rhr, bb_max
    FROM daily_summary
    WHERE day >= '{pd.to_datetime(START_DATE) - timedelta(days=14)}' 
    ORDER BY day
    """
    
    df = pd.read_sql_query(query, conn)
    df['day'] = pd.to_datetime(df['day']).dt.date
    conn.close()
    
    # Calculate Rolling Baseline
    df = df.set_index('day')
    df['rhr_baseline'] = df['rhr'].rolling(window=7, min_periods=3).mean().shift(1) # Shift 1 to use PAST 7 days
    
    # Define Crash (Stress OR Freeze OR Low Battery)
    # NEW: Added Freeze condition (RHR < Baseline - 2)
    df['is_crash'] = (
        (df['rhr'] > (df['rhr_baseline'] + CRASH_RHR_OFFSET)) | 
        (df['rhr'] < (df['rhr_baseline'] - CRASH_RHR_OFFSET)) |
        (df['bb_max'] < CRASH_BB_MAX_THRESHOLD)
    )
    
    return df.reset_index()

def analyze_lag(events_df, outcomes_df):
    # Create a master timeline
    timeline = outcomes_df[['day', 'is_crash']].copy()
    timeline['has_brady'] = False
    timeline['is_zombie'] = False
    
    # Mark event days
    if not events_df.empty:
        daily_events = events_df.groupby('date').agg({
            'has_brady': 'max',
            'is_zombie': 'max'
        }).reset_index()
        
        for _, row in daily_events.iterrows():
            mask = timeline['day'] == row['date']
            timeline.loc[mask, 'has_brady'] = row['has_brady']
            timeline.loc[mask, 'is_zombie'] = row['is_zombie']
            
    # Add Outcome metrics (Crash T+1, Crash T+2)
    timeline['crash_T1'] = timeline['is_crash'].shift(-1)
    timeline['crash_T2'] = timeline['is_crash'].shift(-2)
    
    # Any crash in next 48h
    timeline['crash_next_48h'] = timeline['crash_T1'] | timeline['crash_T2']
    
    # Filter to analysis window (remove buffer days)
    analysis_df = timeline[timeline['day'] >= pd.to_datetime(START_DATE).date()].dropna(subset=['crash_next_48h'])
    
    return analysis_df

def print_stats(df):
    total_days = len(df)
    baseline_crash_rate = df['crash_next_48h'].mean()
    
    print("\n" + "="*40)
    print("VAGAL CRASH ANALYSIS V2 (With Freeze Detection)")
    print("="*40)
    print(f"Total Days Analyzed: {total_days}")
    print(f"Baseline Crash Rate: {baseline_crash_rate:.1%} (Chance of crash in next 48h random day)")
    print("-" * 40)
    
    # Brady Analysis
    brady_days = df[df['has_brady']]
    if len(brady_days) > 0:
        brady_crash_rate = brady_days['crash_next_48h'].mean()
        brady_multiplier = brady_crash_rate / baseline_crash_rate if baseline_crash_rate > 0 else 0
        print(f"\n[BRADY EVENTS]")
        print(f"Count: {len(brady_days)} days")
        print(f"Crash Rate: {brady_crash_rate:.1%}")
        print(f"Risk Multiplier: {brady_multiplier:.1f}x")
    else:
        print("\n[BRADY EVENTS] No events found.")

    # Zombie Analysis
    zombie_days = df[df['is_zombie']]
    if len(zombie_days) > 0:
        zombie_crash_rate = zombie_days['crash_next_48h'].mean()
        zombie_multiplier = zombie_crash_rate / baseline_crash_rate if baseline_crash_rate > 0 else 0
        print(f"\n[ZOMBIE WALKS]")
        print(f"Count: {len(zombie_days)} days")
        print(f"Crash Rate: {zombie_crash_rate:.1%}")
        print(f"Risk Multiplier: {zombie_multiplier:.1f}x")
    else:
        print("\n[ZOMBIE WALKS] No events found.")
        
    print("-" * 40)

def generate_html_report(df):
    ensure_report_dir()
    
    total_days = len(df)
    baseline_crash_rate = df['crash_next_48h'].mean()
    
    # Calculate stats
    brady_days = df[df['has_brady']]
    zombie_days = df[df['is_zombie']]
    
    brady_rate = brady_days['crash_next_48h'].mean() if len(brady_days) > 0 else 0
    zombie_rate = zombie_days['crash_next_48h'].mean() if len(zombie_days) > 0 else 0
    
    brady_mult = brady_rate / baseline_crash_rate if baseline_crash_rate > 0 else 0
    zombie_mult = zombie_rate / baseline_crash_rate if baseline_crash_rate > 0 else 0
    
    # Create Figure
    fig = go.Figure()
    
    categories = ['Baseline (Random Day)', 'After Brady Event', 'After Zombie Walk']
    rates = [baseline_crash_rate, brady_rate, zombie_rate]
    counts = [total_days, len(brady_days), len(zombie_days)]
    colors = ['gray', 'blue', 'green']
    
    fig.add_trace(go.Bar(
        x=categories,
        y=rates,
        text=[f"{r:.1%}" for r in rates],
        textposition='auto',
        marker_color=colors,
        hovertext=[f"N={c}" for c in counts]
    ))
    
    fig.update_layout(
        title="Vagal Crash Risk Analysis V2 (Includes Freeze Detection)",
        yaxis=dict(title="Crash Probability (Next 48h)", tickformat=".0%"),
        template="plotly_white"
    )
    
    # Generate Recommendation HTML
    rec_html = ""
    if brady_mult > 1.3:
        rec_html += f"<li><b>Brady Events:</b> Elevated Risk ({brady_mult:.1f}x). Recommendation: <b>CONSIDER TRIGGER</b>.</li>"
    else:
        rec_html += f"<li><b>Brady Events:</b> Low/No Added Risk ({brady_mult:.1f}x). Recommendation: <b>IGNORE</b>.</li>"
        
    if zombie_mult > 1.3:
        rec_html += f"<li><b>Zombie Walks:</b> Elevated Risk ({zombie_mult:.1f}x). Recommendation: <b>CONSIDER TRIGGER</b>.</li>"
    else:
        rec_html += f"<li><b>Zombie Walks:</b> Low/No Added Risk ({zombie_mult:.1f}x). Recommendation: <b>IGNORE</b>.</li>"

    # Save HTML
    with open(REPORT_FILE, 'w') as f:
        f.write(f"""
        <html>
        <head><title>Vagal Crash Analysis V2</title></head>
        <body style="font-family: sans-serif; max-width: 800px; margin: 0 auto; padding: 20px;">
            <h1>Vagal Crash Analysis Report (V2)</h1>
            <p><b>Analysis Period:</b> {START_DATE} to Present</p>
            <p><b>Total Days Analyzed:</b> {total_days}</p>
            <p><b>Updates in V2:</b> Included "Freeze" crashes (Low RHR) and stricter event thresholds.</p>
            
            <h2>Crash Probability</h2>
            {fig.to_html(full_html=False, include_plotlyjs='cdn')}
            
            <h2>Detailed Statistics</h2>
            <table border="1" style="border-collapse: collapse; width: 100%; text-align: center;">
                <tr style="background-color: #f2f2f2;"><th>Event Type</th><th>Days Observed</th><th>Crash Rate</th><th>Risk Multiplier</th></tr>
                <tr><td>Baseline (Random)</td><td>{total_days}</td><td>{baseline_crash_rate:.1%}</td><td>1.0x</td></tr>
                <tr><td>Brady Event (>15s @ &lt;45bpm)</td><td>{len(brady_days)}</td><td>{brady_rate:.1%}</td><td>{brady_mult:.1f}x</td></tr>
                <tr><td>Zombie Walk (&gt;50% Inefficiency)</td><td>{len(zombie_days)}</td><td>{zombie_rate:.1%}</td><td>{zombie_mult:.1f}x</td></tr>
            </table>
            
            <h2>Recommendation</h2>
            <ul>
                {rec_html}
            </ul>
        </body>
        </html>
        """)
        
    print(f"Report report generated: {REPORT_FILE}")

if __name__ == "__main__":
    print("Step 1: Detecting Events...")
    events_df = get_activity_events()
    
    print("Step 2: Calculating Outcomes...")
    outcomes_df = get_daily_outcomes()
    
    print("Step 3: Analyzing Lag Correlations...")
    analysis_df = analyze_lag(events_df, outcomes_df)
    
    print("Step 4: Generating Reports...")
    print_stats(analysis_df)
    generate_html_report(analysis_df)
