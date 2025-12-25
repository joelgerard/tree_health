import sqlite3
import pandas as pd
import plotly.graph_objects as go
import plotly.subplots as sp
import os
from datetime import datetime, timedelta

# Configuration
DB_PATH = "/Users/joelgerard/Library/CloudStorage/GoogleDrive-joelgerard@gmail.com/My Drive/joel health/tree health/DBs/garmin_activities.db"
REPORT_DIR = "reports"
REPORT_FILE = os.path.join(REPORT_DIR, "hr_event_analysis.html")

def ensure_report_dir():
    if not os.path.exists(REPORT_DIR):
        os.makedirs(REPORT_DIR)

def get_activity_data(days=60):
    conn = sqlite3.connect(DB_PATH)
    
    # Calculate cutoff date
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    query = f"""
    SELECT 
        a.activity_id,
        a.start_time,
        a.name as activity_name,
        ar.timestamp,
        ar.hr,
        ar.cadence,
        ar.speed
    FROM activities a
    JOIN activity_records ar ON a.activity_id = ar.activity_id
    WHERE a.start_time > '{cutoff_date}'
    ORDER BY a.start_time, ar.timestamp
    """
    
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    # Convert timestamps
    df['start_time'] = pd.to_datetime(df['start_time'])
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['date'] = df['start_time'].dt.date
    
    return df

def analyze_activities(df):
    results = []
    
    # Group by activity
    for (activity_id, start_time, activity_name), group in df.groupby(['activity_id', 'start_time', 'activity_name']):
        total_seconds = len(group)
        if total_seconds == 0:
            continue
            
        # 1. Tachy Spikes (> 120 bpm)
        tachy_seconds = len(group[group['hr'] > 120])
        
        # 2. Brady Drops (< 45 bpm)
        brady_seconds = len(group[group['hr'] < 45])
        
        # 3. Inefficiency (Cadence < 90 spm AND Speed > 0.5 m/s to exclude stops)
        # Using speed > 0.5 m/s (approx 1.1 mph) to filter out true pauses/standing
        inefficient_seconds = len(group[(group['cadence'] < 90) & (group['speed'] > 0.5)])
        inefficiency_score = (inefficient_seconds / total_seconds) * 100 if total_seconds > 0 else 0
        
        avg_cadence = group[group['speed'] > 0.5]['cadence'].mean()
        hr_variance = group['hr'].var()
        
        results.append({
            'date': start_time.date(),
            'activity_id': activity_id,
            'activity_name': activity_name,
            'start_time': start_time,
            'duration_min': total_seconds / 60,
            'tachy_seconds': tachy_seconds,
            'brady_seconds': brady_seconds,
            'inefficiency_score': inefficiency_score,
            'avg_cadence': avg_cadence,
            'hr_variance': hr_variance
        })
        
    return pd.DataFrame(results)

def generate_report(analysis_df):
    ensure_report_dir()
    
    # Aggregate by Date
    daily_stats = analysis_df.groupby('date').agg({
        'tachy_seconds': 'sum',
        'brady_seconds': 'sum',
        'inefficiency_score': 'mean', # Average inefficiency per day
        'hr_variance': 'mean'
    }).reset_index()
    
    # Create Subplots
    fig = sp.make_subplots(
        rows=2, cols=2,
        specs=[[{"colspan": 2}, None], [{"type": "xy"}, {"type": "table"}]],
        subplot_titles=("Anomaly Timeline (Tachy > 120, Brady < 45)", "Cadence vs HR Stability", "Top Anomalous Activities"),
        vertical_spacing=0.15
    )
    
    # Chart 1: Anomaly Timeline (Bar Chart)
    fig.add_trace(go.Bar(
        x=daily_stats['date'],
        y=daily_stats['tachy_seconds'],
        name='Tachy Seconds (>120)',
        marker_color='red'
    ), row=1, col=1)
    
    fig.add_trace(go.Bar(
        x=daily_stats['date'],
        y=-daily_stats['brady_seconds'], # Negative for visual separation
        name='Brady Seconds (<45)',
        marker_color='blue'
    ), row=1, col=1)
    
    # Chart 2: Cadence vs HR Stability (Scatter)
    # Filter out NaN variance
    scatter_data = analysis_df.dropna(subset=['hr_variance', 'avg_cadence'])
    
    fig.add_trace(go.Scatter(
        x=scatter_data['avg_cadence'],
        y=scatter_data['hr_variance'],
        mode='markers',
        text=scatter_data['activity_name'] + '<br>' + scatter_data['date'].astype(str),
        marker=dict(
            size=10,
            color=scatter_data['brady_seconds'], # Color by Brady severity
            colorscale='Bluered',
            showscale=True,
            colorbar=dict(title="Brady Secs", x=0.45, len=0.5)
        ),
        name='Activity'
    ), row=2, col=1)
    
    # Table: Top Anomalous Activities (Sort by Brady Seconds)
    top_activities = analysis_df.sort_values('brady_seconds', ascending=False).head(10)
    
    fig.add_trace(go.Table(
        header=dict(
            values=['Date', 'Activity', 'Brady (s)', 'Tachy (s)', 'Inefficiency (%)'],
            fill_color='paleturquoise',
            align='left'
        ),
        cells=dict(
            values=[
                top_activities['date'],
                top_activities['activity_name'],
                top_activities['brady_seconds'],
                top_activities['tachy_seconds'],
                top_activities['inefficiency_score'].round(1)
            ],
            fill_color='lavender',
            align='left'
        )
    ), row=2, col=2)
    
    # Layout Updates
    fig.update_layout(
        title_text="Heart Rate Event Analysis (Last 60 Days)",
        height=900,
        showlegend=True,
        barmode='overlay' # or relative/group
    )
    
    # Update axes
    fig.update_yaxes(title_text="Seconds", row=1, col=1)
    fig.update_xaxes(title_text="Date", row=1, col=1)
    
    fig.update_xaxes(title_text="Avg Cadence (spm)", row=2, col=1)
    fig.update_yaxes(title_text="HR Variance", row=2, col=1)
    
    # Save Report
    fig.write_html(REPORT_FILE)
    print(f"Report generated: {REPORT_FILE}")
    print("\nSummary Stats:")
    print(f"Total Activities Analyzed: {len(analysis_df)}")
    print(f"Total Brady Events Detected: {analysis_df['brady_seconds'].sum()} seconds")
    print(f"Total Tachy Events Detected: {analysis_df['tachy_seconds'].sum()} seconds")

if __name__ == "__main__":
    try:
        print("Fetching data from garmin_activities.db...")
        df = get_activity_data()
        
        print("Analyzing heart rate anomalies...")
        analysis_df = analyze_activities(df)
        
        print("Generating HTML report...")
        generate_report(analysis_df)
        
    except Exception as e:
        print(f"Error: {e}")
