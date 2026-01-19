import sqlite3
import pandas as pd
import plotly.graph_objects as go
import plotly.subplots as sp
import os
from datetime import datetime, timedelta
import sys
import numpy as np

# Configuration
DB_PATH = "/Users/joelgerard/GarminDBSync/tree/HealthData/DBs/garmin_activities.db"
REPORT_DIR = "reports"
REPORT_FILE = os.path.join(REPORT_DIR, "activity_hr_report.html")

def ensure_report_dir():
    if not os.path.exists(REPORT_DIR):
        os.makedirs(REPORT_DIR)

def get_activity_data(days=100):
    if not os.path.exists(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}")
        sys.exit(1)

    print(f"Connecting to {DB_PATH}...")
    conn = sqlite3.connect(DB_PATH)
    
    # Calculate cutoff
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    print(f"Fetching data since {cutoff_date}...")
    
    # We need to join activities and records to get HR data grouped by day
    # We also need activity-level stats (duration)
    
    # 1. Fetch Activity Stats (Count, Duration)
    # Using 'activities' table
    query_stats = f"""
    SELECT 
        date(start_time) as day,
        COUNT(activity_id) as activity_count,
        SUM(elapsed_time) as total_duration_str
    FROM activities
    WHERE start_time > '{cutoff_date}'
    GROUP BY day
    ORDER BY day
    """
    
    try:
        df_stats = pd.read_sql_query(query_stats, conn)
    except Exception as e:
        print(f"Error fetching stats: {e}")
        conn.close()
        sys.exit(1)
        
    if df_stats.empty:
        print("No activities found in range.")
        conn.close()
        return pd.DataFrame(), pd.DataFrame()

    # Helper to parse duration string "HH:MM:SS" or similar
    def parse_time(t_str):
        if not t_str: return 0
        try:
            # Assuming format like "00:00:00" or similar
            # SQLite might return it as string
            # If it's already float/int in DB? Schema said TIME type usually strings in SQLite wrappers
            # Checking schema: "elapsed_time TIME NOT NULL"
            # It might be "HH:MM:SS.ssss"
            parts = str(t_str).split(':')
            if len(parts) == 3:
                h, m, s = parts
                return float(h)*3600 + float(m)*60 + float(s)
            elif len(parts) == 2:
                m, s = parts
                return float(m)*60 + float(s)
            return 0
        except:
            return 0
            
    # Apply parsing
    # Wait, getting SUM(elapsed_time) in SQL on TIME activity might be weird if SQLite doesn't handle TIME sum natively as we expect.
    # It takes strings and maybe concatenates or does weird math?
    # Better to fetch raw elapsed_time rows and sum in Pandas.
    
    # Re-querying Activity Metadata
    query_meta = f"""
    SELECT 
        activity_id,
        date(start_time) as day,
        elapsed_time
    FROM activities
    WHERE start_time > '{cutoff_date}'
    """
    df_meta = pd.read_sql_query(query_meta, conn)
    
    # Parse durations
    df_meta['duration_sec'] = df_meta['elapsed_time'].apply(parse_time)
    
    # Aggregate Stats in Pandas
    daily_stats = df_meta.groupby('day').agg(
        activity_count=('activity_id', 'count'),
        total_duration_sec=('duration_sec', 'sum')
    ).reset_index()
    
    daily_stats['total_duration_min'] = (daily_stats['total_duration_sec'] / 60).round(1)
    
    # 2. Fetch HR Records for Distribution
    # Note: Fetching timestamp and activity_id to calculate duration
    query_hr = f"""
    SELECT 
        date(a.start_time) as day,
        ar.activity_id,
        ar.timestamp,
        ar.hr
    FROM activities a
    JOIN activity_records ar ON a.activity_id = ar.activity_id
    WHERE a.start_time > '{cutoff_date}'
    AND ar.hr > 0
    ORDER BY ar.activity_id, ar.timestamp
    """
    
    print("Fetching detailed HR records (this might take a moment)...")
    try:
        df_hr = pd.read_sql_query(query_hr, conn)
    except Exception as e:
        print(f"Error fetching HR records: {e}")
        conn.close()
        sys.exit(1)
        
    conn.close()
    
    return daily_stats, df_hr

def analyze_distributions(daily_stats, df_hr):
    if df_hr.empty:
        return daily_stats
    
    print("Calculating HR percentiles and durations...")
    
    # Calculate Duration per sample
    # Convert timestamp
    df_hr['timestamp'] = pd.to_datetime(df_hr['timestamp'])
    
    # Shift-diff for duration
    # Group by activity to avoid cross-activity diffs
    # This can be slowish in Pandas with simple groupby.shift if many groups
    # Faster approach: sort by activity, timestamp. Diff. Mask where activity changes.
    # We already ordered by SQL.
    
    df_hr['next_ts'] = df_hr['timestamp'].shift(-1)
    df_hr['next_act'] = df_hr['activity_id'].shift(-1)
    
    # Duration = next - curr. 
    # Valid only if next_act == curr_act
    # Fill last sample of activity with 1s usually (or median)
    
    df_hr['duration_sec'] = (df_hr['next_ts'] - df_hr['timestamp']).dt.total_seconds()
    
    # Mask invalid (activity change) or huge gaps (>60s -> 1s)
    # Using numpy where
    mask_same_act = (df_hr['activity_id'] == df_hr['next_act'])
    
    # Default duration 1s
    # If same activity and duration < 60s, use calculated. Else 1s.
    # Vectorized logic
    default_dur = 1.0
    
    # If not same activity, duration is default
    # If same activity but huge gap, duration is default
    valid_durations = mask_same_act & (df_hr['duration_sec'] <= 60) & (df_hr['duration_sec'] > 0)
    
    df_hr['final_dur'] = np.where(valid_durations, df_hr['duration_sec'], default_dur)
    
    # Now analyze < 48 bpm Bouts
    # Define what constitutes a "bout": Consecutive samples with HR < 48
    # Must be same activity, and close in time (e.g. within 60s gap? or just consecutive rows?)
    # If there is a gap > 1 min, we probably shouldn't merge them.
    # We'll use the 'final_dur' we calculated. If we are summing them, they are effectively merged if consecutive.
    
    df_hr['is_low'] = df_hr['hr'] < 48
    
    # Detect changes that break a bout
    # 1. Activity Change
    # 2. Not Low anymore (HR >= 48)
    # 3. Time Gap > 120s? (Arbitrary buffer, but if we have 1 sample/min, a missed sample might break it)
    # Let's say if gap > 2 mins, it breaks.
    
    # Vectorized Bout ID detection
    # Shifted values
    s_act = df_hr['activity_id'].shift()
    s_low = df_hr['is_low'].shift()
    s_ts = df_hr['timestamp'].shift()
    
    # Compute gaps
    # Time diff in seconds
    time_diff = (df_hr['timestamp'] - s_ts).dt.total_seconds().fillna(0)
    
    # Helper booleans
    # Start new bout if:
    # - Activity changed
    # - Low status changed (False -> True)
    # - Gap > 120s (while staying True) -> Optional, but safer to split widely separated events
    # - Previous was False (so current True starts new)
    
    # We only care about identifying unique IDs for contiguous True blocks
    # A change in 'is_low' flags a new group potential
    # A change in activity flags a new group
    # A large time gap flags a new group
    
    is_new_group = (
        (df_hr['activity_id'] != s_act) | 
        (df_hr['is_low'] != s_low) | 
        (time_diff > 120)
    )
    
    df_hr['bout_id'] = is_new_group.cumsum()
    
    # Filter for low only
    low_bouts_df = df_hr[df_hr['is_low']].copy()
    
    if low_bouts_df.empty:
        # No low events
        merged = pd.merge(daily_stats, daily_dist, on='day', how='left')
        merged['count_48'] = 0
        merged['time_48_min'] = 0
        merged['bout_durations'] = [[] for _ in range(len(merged))]
        return merged

    # Group by Bout ID to get duration AND start time
    bout_stats = low_bouts_df.groupby(['day', 'bout_id']).agg(
        bout_duration=('final_dur', 'sum'),
        start_ts=('timestamp', 'min')
    ).reset_index()
    
    # Format display string: "Dur [HH:MM:SS]"
    # Ensure start_ts is datetime
    bout_stats['start_ts'] = pd.to_datetime(bout_stats['start_ts'])
    bout_stats['time_str'] = bout_stats['start_ts'].dt.strftime('%H:%M:%S')
    bout_stats['bout_display'] = bout_stats.apply(
        lambda x: f"{x['bout_duration']:.1f}s [{x['time_str']}]", axis=1
    )
    
    # Collect bouts per day
    # Sort by day, then chronological (bout_id implies order)
    bouts_per_day = bout_stats.groupby('day')['bout_display'].apply(list).reset_index()
    bouts_per_day.columns = ['day', 'bout_details']
    
    # Agg low stats (count and total time)
    # Re-derive from bout_stats (numeric)
    day_aggs = bout_stats.groupby('day').agg(
        count_48=('bout_id', 'count'), # Number of Bouts
        time_48_sec=('bout_duration', 'sum')
    ).reset_index()
    
    # Calculate HR Percentiles (daily_dist)
    ps = [10, 20, 30, 40, 50, 60, 70, 80, 90]
    
    def get_stats(group):
        stats = pd.Series(np.percentile(group['hr'], ps), index=[f'p{p}' for p in ps])
        stats['min_hr'] = group['hr'].min()
        return stats
        
    daily_dist = df_hr.groupby('day').apply(get_stats).reset_index()
    
    # Merge all
    merged = pd.merge(daily_stats, daily_dist, on='day', how='left')
    merged = pd.merge(merged, day_aggs, on='day', how='left')
    merged = pd.merge(merged, bouts_per_day, on='day', how='left')
    
    # Fill defaults
    merged['count_48'] = merged['count_48'].fillna(0).astype(int)
    merged['time_48_sec'] = merged['time_48_sec'].fillna(0)
    merged['time_48_min'] = (merged['time_48_sec'] / 60).round(1)
    
    # Fill empty lists for days with no bouts
    merged['bout_details'] = merged['bout_details'].apply(lambda x: x if isinstance(x, list) else [])
    
    return merged

def generate_html_report(df):
    ensure_report_dir()
    
    if df.empty:
        print("No data to report.")
        return

    # Create Chart (Rows 1 & 2 only)
    fig = sp.make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.7, 0.3],
        subplot_titles=("Daily Heart Rate Distribution", "Activity Stats"),
        specs=[[{"type": "xy"}], [{"type": "xy"}]]
    )
    
    # 1. HR Distribution (Lines/Ribbon)
    # p90
    fig.add_trace(go.Scatter(
        x=df['day'], y=df['p90'],
        name='90th Percentile',
        line=dict(color='firebrick', width=1),
        mode='lines'
    ), row=1, col=1)
    
    # p50
    fig.add_trace(go.Scatter(
        x=df['day'], y=df['p50'],
        name='Median (p50)',
        line=dict(color='black', width=2),
        mode='lines'
    ), row=1, col=1)
    
    # p10
    fig.add_trace(go.Scatter(
        x=df['day'], y=df['p10'],
        name='10th Percentile',
        line=dict(color='royalblue', width=1),
        mode='lines',
        fill='tonexty' # Fill to p50
    ), row=1, col=1)

    # Min HR
    fig.add_trace(go.Scatter(
        x=df['day'], y=df['min_hr'],
        name='Lowest BPM',
        line=dict(color='purple', width=1.5, dash='dash'),
        mode='lines+markers',
        marker=dict(size=4)
    ), row=1, col=1)
    
    # Faint deciles
    for p in [20, 30, 40, 60, 70, 80]:
        fig.add_trace(go.Scatter(
            x=df['day'], y=df[f'p{p}'],
            name=f'p{p}',
            line=dict(color='gray', width=0.5, dash='dot'),
            showlegend=False,
            mode='lines'
        ), row=1, col=1)

    # 2. Activity Stats
    fig.add_trace(go.Bar(
        x=df['day'], y=df['total_duration_min'],
        name='Total Duration (min)',
        marker_color='teal',
        opacity=0.6
    ), row=2, col=1)
    
    fig.add_trace(go.Scatter(
        x=df['day'], y=df['activity_count'],
        name='Activity Count',
        line=dict(color='orange', width=2),
        yaxis='y2',
        mode='lines+markers'
    ), row=2, col=1)
    
    fig.update_layout(
        title_text=f"Activity Heart Rate Distribution (Last 100 Days)",
        height=800,
        hovermode="x unified"
    )
    
    # Update axes
    fig.update_yaxes(title_text="Heart Rate (bpm)", row=1, col=1)
    fig.update_yaxes(title_text="Duration (min)", row=2, col=1)

    # Generate HTML Parts
    plot_html = fig.to_html(full_html=False, include_plotlyjs='cdn')
    
    # Prepare DataFrames for Tables
    # Table 1: Bradycardia Stats
    # Expand bout_details into columns
    
    # Find max bouts
    max_bouts = 0
    if 'bout_details' in df.columns:
        lengths = df['bout_details'].apply(len)
        if not lengths.empty:
            max_bouts = lengths.max()
    
    # Create bout columns
    bout_cols = []
    if max_bouts > 0:
        # Create a DataFrame from the lists
        bouts_expanded = pd.DataFrame(df['bout_details'].tolist(), index=df.index)
        # Rename columns
        bouts_expanded.columns = [f'Bout {i+1}' for i in range(bouts_expanded.shape[1])]
        # Identify columns valid for our max (in case tolist() created more than needed? No, it handles it)
        # Just use all columns created
        bout_cols = bouts_expanded.columns.tolist()
        
        # Join with original
        df_brady = pd.concat([df[['day', 'count_48', 'time_48_min']], bouts_expanded], axis=1)
    else:
        df_brady = df[['day', 'count_48', 'time_48_min']].copy()
        
    # Rename base columns
    df_brady = df_brady.rename(columns={
        'day': 'Date', 
        'count_48': 'Occurrences (<48 bpm)', 
        'time_48_min': 'Total Time (min)'
    })
    
    # Table 2: Detailed Data
    cols = ['day', 'activity_count', 'total_duration_min', 'min_hr'] + [f'p{p}' for p in [10, 20, 50, 90]]
    df_detail = df[cols].copy()
    df_detail.columns = ['Date', 'Activities', 'Duration (min)', 'Min HR', 'p10', 'p20', 'Median', 'p90']
    
    # CSS & JS
    custom_html = f"""
    <html>
    <head>
        <title>Activity HR Report</title>
        <style>
            body {{ font-family: sans-serif; margin: 20px; }}
            .table-container {{ margin-top: 30px; margin-bottom: 50px; overflow-x: auto; }}
            table {{ border-collapse: collapse; min-width: 100%; margin-top: 10px; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; white-space: nowrap; }}
            th {{ background-color: #f2f2f2; position: sticky; left: 0; z-index: 1; }}
            /* Sticky first column */
            td:first-child, th:first-child {{ 
                position: sticky; 
                left: 0; 
                background-color: #f9f9f9; 
                z-index: 2;
                border-right: 2px solid #ddd;
            }}
            tr:nth-child(even) td:first-child {{ background-color: #f2f2f2; }}
            tr:nth-child(even) {{ background-color: #f9f9f9; }}
            .btn-copy {{
                background-color: #4CAF50; border: none; color: white; 
                padding: 10px 20px; text-align: center; text-decoration: none; 
                display: inline-block; font-size: 14px; margin: 4px 2px; 
                cursor: pointer; border-radius: 4px;
            }}
            .btn-copy:hover {{ background-color: #45a049; }}
            h2 {{ color: #333; border-bottom: 2px solid #ccc; padding-bottom: 10px; }}
        </style>
        <script>
            function copyTableToCSV(tableId) {{
                var csv = [];
                var rows = document.querySelectorAll('#' + tableId + ' tr');
                
                for (var i = 0; i < rows.length; i++) {{
                    var row = [], cols = rows[i].querySelectorAll("td, th");
                    
                    for (var j = 0; j < cols.length; j++) 
                        row.push('"' + cols[j].innerText + '"');
                    
                    csv.push(row.join(","));        
                }}

                downloadCSV(csv.join("\\n"), tableId + '.csv');
            }}

            function downloadCSV(csv, filename) {{
                var csvFile;
                var downloadLink;

                csvFile = new Blob([csv], {{type: "text/csv"}});
                downloadLink = document.createElement("a");
                downloadLink.download = filename;
                downloadLink.href = window.URL.createObjectURL(csvFile);
                downloadLink.style.display = "none";
                document.body.appendChild(downloadLink);
                downloadLink.click();
                
                // Also copy to clipboard fallback/addition
                navigator.clipboard.writeText(csv).then(function() {{
                    alert('Table copied to clipboard (and downloaded as CSV)!');
                }}, function(err) {{
                    console.error('Async: Could not copy text: ', err);
                }});
            }}
        </script>
    </head>
    <body>
        
        {plot_html}
        
        <div class="table-container">
            <h2>Bradycardia Stats (< 48 bpm)</h2>
            <p><i>Note: Columns "Bout X (s)" represent individual durations in seconds for each event < 48 bpm.</i></p>
            <button class="btn-copy" onclick="copyTableToCSV('table_brady')">Copy Table to CSV</button>
            {df_brady.to_html(index=False, table_id="table_brady", na_rep="")}
        </div>
        
        <div class="table-container">
            <h2>Detailed Daily Data</h2>
            <button class="btn-copy" onclick="copyTableToCSV('table_detail')">Copy Table to CSV</button>
            {df_detail.to_html(index=False, table_id="table_detail")}
        </div>
        
    </body>
    </html>
    """
    
    with open(REPORT_FILE, 'w') as f:
        f.write(custom_html)
        
    print(f"Report generated: {REPORT_FILE}")

if __name__ == "__main__":
    print("Starting analysis...")
    daily_stats, df_hr = get_activity_data(days=100)
    
    if not daily_stats.empty and not df_hr.empty:
        print(f"Found {len(daily_stats)} days with activities.")
        final_df = analyze_distributions(daily_stats, df_hr)
        generate_html_report(final_df)
    else:
        print("No meaningful data found.")
