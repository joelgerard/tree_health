import argparse
import os
import sys
import sqlite3
import json
from datetime import datetime, timedelta
import glob

# --- Constants ---
# Map commonly used table names to their likely database if needed, 
# though we primarily search by filename for the generic dump.

def get_db_connection(db_path):
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        print(f"Error connecting to {db_path}: {e}")
        return None

def find_date_column(cursor, table_name):
    """
    Heuristic to find the relevant date column in a table (for generic JSON dump).
    """
    try:
        cursor.execute(f"PRAGMA table_info(\"{table_name}\")")
        columns = [row['name'] for row in cursor.fetchall()]
        
        candidates = ['day', 'start_time', 'timestamp', 'first_day', 'calendar_date', 'begin_timestamp']
        
        for cand in candidates:
            if cand in columns:
                return cand
                
        for col in columns:
            if 'date' in col.lower() or 'time' in col.lower():
                if 'elapsed' not in col.lower() and 'duration' not in col.lower() and 'zone' not in col.lower():
                    return col
    except Exception as e:
        print(f"Warning: Could not get info for table {table_name}: {e}")
                 
    return None

def dump_table(conn, table_name, date_str):
    """
    Generic table dumper for JSON mode.
    """
    cursor = conn.cursor()
    date_col = find_date_column(cursor, table_name)
    
    if not date_col:
        return []

    # Construct query
    query = f"SELECT * FROM \"{table_name}\" WHERE \"{date_col}\" LIKE ?"
    
    try:
        cursor.execute(query, (f"{date_str}%",))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error as e:
        print(f"  [Error] Query failed for table '{table_name}' using col '{date_col}': {e}")
        return []

# --- Smart Summary Helpers ---

def format_duration(time_str):
    """
    Convert 'HH:MM:SS.ssssss' or milliseconds to 'Xh Ym'.
    """
    if not time_str:
        return "N/A"
    
    try:
        # Check if it's a string like "08:39:38.000000"
        if isinstance(time_str, str) and ":" in time_str:
            parts = time_str.split(":")
            hours = int(parts[0])
            minutes = int(parts[1])
            return f"{hours}h {minutes}m"
        
        # Check if it's milliseconds (int)
        val = int(time_str)
        # Assuming ms if large, or seconds if small? 
        # Garmin duration often ms. 1 hour = 3,600,000 ms.
        if val > 100000: 
            seconds = val // 1000
        else:
            seconds = val # assume seconds
            
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m"
        
    except Exception:
        return str(time_str)

def get_row_as_dict(cursor, query, params=()):
    try:
        cursor.execute(query, params)
        row = cursor.fetchone()
        return dict(row) if row else {}
    except sqlite3.Error:
        return {}

def get_rows_as_list(cursor, query, params=()):
    try:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []

def get_smart_summary_for_date(db_dir, date_str):
    """
    Aggregates high-value tables for a single date.
    """
    data = {
        'date': date_str,
        'metrics': {},
        'activities': [],
        'visible': {}
    }

    # 1. Garmin Daily (garmin.db)
    garmin_db_path = os.path.join(db_dir, "garmin.db")
    if os.path.exists(garmin_db_path):
        conn = get_db_connection(garmin_db_path)
        if conn:
            cur = conn.cursor()
            
            # Daily Summary
            daily = get_row_as_dict(cur, "SELECT * FROM daily_summary WHERE day = ?", (date_str,))
            if daily:
                data['metrics']['Resting HR'] = daily.get('rhr')
                data['metrics']['Steps'] = daily.get('steps')
                data['metrics']['Stress Avg'] = daily.get('stress_avg')
                data['metrics']['Body Battery'] = daily.get('bb_charged')
                data['metrics']['Active Calories'] = daily.get('calories_active')

            # HRV
            hrv = get_row_as_dict(cur, "SELECT * FROM hrv WHERE day = ?", (date_str,))
            if hrv:
                data['metrics']['HRV (Last Night)'] = hrv.get('last_night_avg')
                data['metrics']['HRV Status'] = hrv.get('status')
            
            # Sleep
            sleep = get_row_as_dict(cur, "SELECT * FROM sleep WHERE day = ?", (date_str,))
            if sleep:
                data['metrics']['Sleep Score'] = sleep.get('score')
                data['metrics']['Sleep Duration'] = format_duration(sleep.get('total_sleep'))
            
            conn.close()

    # 2. Garmin Activities (garmin_activities.db)
    activities_db_path = os.path.join(db_dir, "garmin_activities.db")
    if os.path.exists(activities_db_path):
        conn = get_db_connection(activities_db_path)
        if conn:
            cur = conn.cursor()
            # Find activities starting on this date (start_time is likely 'YYYY-MM-DD HH:MM:SS')
            acts = get_rows_as_list(cur, "SELECT * FROM activities WHERE start_time LIKE ?", (f"{date_str}%",))
            for act in acts:
                data['activities'].append({
                    'name': act.get('name', 'Unknown'),
                    'type': act.get('type', 'Unknown'),
                    'duration': format_duration(act.get('elapsed_time')),
                    'avg_hr': act.get('avg_hr')
                })
            conn.close()
            
    # 3. Visible (If available) - Placeholder / Best Effort
    # Note: If visible data is added later to a specific DB, add logic here.
    
    return data

def write_smart_summary(dates, db_dir, output_file):
    """
    Writes the human-readable report.
    """
    with open(output_file, 'w') as f:
        for date_str in dates:
            summary = get_smart_summary_for_date(db_dir, date_str)
            
            # Header
            try:
                dt_obj = datetime.strptime(date_str, '%Y-%m-%d')
                day_name = dt_obj.strftime('%A')
            except:
                day_name = "Unknown"
                
            f.write(f"DATE: {date_str} ({day_name})\n")
            f.write("-" * 40 + "\n")
            
            # Section 1: METRICS
            f.write("METRICS:\n")
            if summary['metrics']:
                # Filter None values
                clean_metrics = {k: v for k, v in summary['metrics'].items() if v is not None}
                if clean_metrics:
                    # Print in a nice block? Or list? User asked for "Single line or tight block"
                    # Let's do a tight block of key=value
                    pairs = [f"{k}={v}" for k, v in clean_metrics.items()]
                    # Wrap every 3 items for readability
                    chunk_size = 3
                    for i in range(0, len(pairs), chunk_size):
                        f.write("  " + ", ".join(pairs[i:i+chunk_size]) + "\n")
                else:
                    f.write("  (No data)\n")
            else:
                f.write("  (No data)\n")
            f.write("\n")
            
            # Section 2: ACTIVITIES
            f.write("ACTIVITIES:\n")
            if summary['activities']:
                for act in summary['activities']:
                    f.write(f"  - {act['name']} ({act['type']}): {act['duration']}, Avg HR: {act['avg_hr']}\n")
            else:
                f.write("  (None)\n")
            
            f.write("\n" + "="*40 + "\n\n")

def main():
    parser = argparse.ArgumentParser(description="Dump health data.")
    parser.add_argument('-f', '--folder', default=os.path.expanduser("~/HealthData/DBs/"), 
                        help="Directory containing the database files.")
    parser.add_argument('-d', '--date', default=datetime.now().strftime('%Y-%m-%d'),
                        help="Target date (YYYY-MM-DD). Defaults to today.")
    parser.add_argument('-w', '--week', action='store_true',
                        help="Dump the last 7 days ending on the target date.")
    parser.add_argument('-t', '--text', action='store_true',
                        help="Output in a human-readable text format (Smart Summary).")
    
    args = parser.parse_args()

    # Expand DB dir
    db_dir = os.path.expanduser(args.folder)
    target_date = args.date
    
    if not os.path.exists(db_dir):
        print(f"Error: Directory not found: {db_dir}")
        return

    # Determine dates
    try:
        target_dt = datetime.strptime(target_date, '%Y-%m-%d')
    except ValueError:
        print("Error: Invalid date format. Use YYYY-MM-DD.")
        return

    dates_to_dump = [target_date]
    if args.week:
        dates_to_dump = [(target_dt - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6, -1, -1)]
        label = "7 days"
    else:
        label = "1 day"

    # --- Mode Selection ---
    if args.text:
        # Smart Summary Mode
        output_filename = f"health_dump_{target_date}.txt"
        if args.week:
            output_filename = f"health_dump_week_ending_{target_date}.txt"
            
        print(f"Generating Smart Summary ({label}) for {output_filename}...")
        write_smart_summary(dates_to_dump, db_dir, output_filename)
        print(f"Done! Saved to {os.path.abspath(output_filename)}")
        
    else:
        # Legacy JSON Dump Mode
        print(f"Dumping RAW JSON ({label}) from {db_dir}...")
        
        output_data = {}
        db_files = glob.glob(os.path.join(db_dir, "*.db"))
        
        if not db_files:
            print("No .db files found.")
            return

        for db_path in db_files:
            db_name = os.path.basename(db_path)
            conn = get_db_connection(db_path)
            if not conn:
                continue
                
            db_data = {}
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row['name'] for row in cursor.fetchall()]
                
                for table in tables:
                    if table.startswith('sqlite_'): continue
                    
                    all_rows = []
                    for date_str in dates_to_dump:
                        rows = dump_table(conn, table, date_str)
                        if rows:
                            all_rows.extend(rows)
                            
                    if all_rows:
                        db_data[table] = all_rows
                        
            except sqlite3.Error as e:
                print(f"Error reading {db_name}: {e}")
            finally:
                conn.close()
                
            if db_data:
                output_data[db_name] = db_data
        
        # Save JSON
        ext = "json"
        if args.week:
            output_filename = f"health_dump_week_ending_{target_date}.{ext}"
        else:
            output_filename = f"health_dump_{target_date}.{ext}"
            
        with open(output_filename, 'w') as f:
            json.dump(output_data, f, indent=2, default=str)
        print(f"\nDump complete! Saved to {os.path.abspath(output_filename)}")

if __name__ == "__main__":
    main()
