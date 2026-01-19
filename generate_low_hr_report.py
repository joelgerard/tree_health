import sqlite3
import pandas as pd
import plotly.graph_objects as go
import plotly.subplots as sp
import os
from datetime import datetime
import sys

# Configuration
DB_PATH = "/Users/joelgerard/GarminDBSync/tree/HealthData/DBs/garmin_monitoring.db"
REPORT_DIR = "reports"
REPORT_FILE = os.path.join(REPORT_DIR, "low_hr_report.html")

def ensure_report_dir():
    if not os.path.exists(REPORT_DIR):
        os.makedirs(REPORT_DIR)

def get_low_hr_data():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}")
        sys.exit(1)

    print(f"Connecting to {DB_PATH}...")
    conn = sqlite3.connect(DB_PATH)
    
    # Query for low HR events (< 40 bpm)
    # Using 'heart_rate' column from 'monitoring_hr'
    # Assuming valid non-zero heart rates only
    query = """
    SELECT 
        timestamp,
        heart_rate
    FROM monitoring_hr
    WHERE heart_rate < 40 AND heart_rate > 0
    ORDER BY timestamp ASC
    """
    
    try:
        df = pd.read_sql_query(query, conn)
        conn.close()
    except Exception as e:
        print(f"Error executing query: {e}")
        conn.close()
        sys.exit(1)

    if df.empty:
        print("No heart rate events < 40 bpm found.")
        return pd.DataFrame()
        
    # Process Data
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['date'] = df['timestamp'].dt.date
    
    return df

def analyze_data(df):
    if df.empty:
        return pd.DataFrame()

    # Aggregate by Day
    daily_stats = df.groupby('date').agg(
        events_count=('timestamp', 'count'),  # Each record is typically 1 minute? Or variable? 
        # Checking schema earlier, timestamps were 1 min apart (00:01, 00:02...)
        # But let's verify if they are seconds or minutes.
        # usually monitoring_hr is minutely or similarly sparse unless in activity?
        # Let's assume 1 row = 1 measurement.
        # If we assume 1 sample per minute for 24/7 monitoring (common in older Garmins) 
        # or 1 sample per 2 mins.
        # Actually `monitoring_hr` usually doesn't store duration, just timestamp.
        # We will count "Occurrences" and estimate time if we can infer interval.
        # For now, "Count" is safer than "Time" unless we check gaps.
        # The user asked for "how many times" and "sum of time".
        # Let's assume roughly 1-minute intervals for non-activity tracking if continuous?
        # Or just report "Count of Samples" as proxy for "Minutes" if we assume 1 min res.
        # Let's recalculate time by checking gaps? No, that's complex.
        # Let's stick to "Count of Samples < 40". 
        min_hr=('heart_rate', 'min'),
        avg_hr=('heart_rate', 'mean')
    ).reset_index()
    
    # Calculate duration? 
    # If we assume each sample represents a duration until the next sample?
    # Or, if these are just spot checks. 
    # Let's report "Number of Samples" which is "How many times it was recorded < 40".
    # And for "Sum of Time", we might just say "Approx. Minutes (1 sample = ~1 min)" 
    # OR explicitely label it "Sample Count".
    # However the user asked for "sum of time".
    # I will add a column "estimated_minutes" assuming 1 sample = 1 minute for now, 
    # but I'll add a disclaimer or just call it "Minutes (Est)"
    # Actually, looking at the previous tool output:
    # 2025-11-11 00:01:00... 00:02:00... 00:03:00... 00:05:00...
    # It seems to be 1-minute intervals but with gaps (00:04 missing).
    # So "1 sample ~= 1 minute" is a reasonable approximation for "Time spent < 40".
    
    daily_stats['estimated_minutes'] = daily_stats['events_count'] 
    
    return daily_stats

def generate_report(daily_stats):
    ensure_report_dir()
    
    if daily_stats.empty:
        print("No data to report.")
        return

    # Create Subplots
    fig = sp.make_subplots(
        rows=2, cols=1,
        subplot_titles=("Daily Count of Heart Rate Drops (< 40 bpm)", "Detailed Daily Statistics"),
        specs=[[{"type": "xy"}], [{"type": "table"}]],
        vertical_spacing=0.1
    )
    
    # Bar Chart: Events Count
    fig.add_trace(go.Bar(
        x=daily_stats['date'],
        y=daily_stats['events_count'],
        name='Events < 40 bpm',
        marker_color='crimson'
    ), row=1, col=1)
    
    # Table: Detailed Stats
    fig.add_trace(go.Table(
        header=dict(
            values=['Date', 'Events Count', 'Lowest HR', 'Avg HR (of low events)', 'Est. Duration (min)'],
            fill_color='paleturquoise',
            align='left'
        ),
        cells=dict(
            values=[
                daily_stats['date'],
                daily_stats['events_count'],
                daily_stats['min_hr'],
                daily_stats['avg_hr'].round(1),
                daily_stats['estimated_minutes']
            ],
            fill_color='lavender',
            align='left'
        )
    ), row=2, col=1)
    
    # Layout
    fig.update_layout(
        title_text=f"Low Heart Rate Analysis (< 40 bpm)<br>Source: {os.path.basename(DB_PATH)}",
        height=1000,
        showlegend=False
    )
    
    fig.update_xaxes(title_text="Date", row=1, col=1)
    fig.update_yaxes(title_text="Count of Samples", row=1, col=1)
    
    # Save
    print(f"Saving report to {REPORT_FILE}...")
    fig.write_html(REPORT_FILE)
    print("Done.")

if __name__ == "__main__":
    print("Starting Low HR Data Extraction...")
    df = get_low_hr_data()
    
    print(f"Found {len(df)} samples < 40 bpm.")
    
    daily_stats = analyze_data(df)
    
    if not daily_stats.empty:
        print(f"Aggregated into {len(daily_stats)} days with low HR events.")
        print(f"Top 5 Days by Occurrence:\n{daily_stats.sort_values('events_count', ascending=False).head(5)}")
        generate_report(daily_stats)
