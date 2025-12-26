import argparse
import os
import sys
import sqlite3
import json
from datetime import datetime, timedelta
import glob

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
    Heuristic to find the relevant date column in a table.
    """
    try:
        cursor.execute(f"PRAGMA table_info(\"{table_name}\")")
        columns = [row['name'] for row in cursor.fetchall()]
        
        # Priority list of columns that usually contain the date/timestamp
        candidates = ['day', 'start_time', 'timestamp', 'first_day', 'calendar_date', 'begin_timestamp']
        
        for cand in candidates:
            if cand in columns:
                return cand
                
        # Fallback: check for any column ending in '_date' or 'time'
        for col in columns:
            if 'date' in col.lower() or 'time' in col.lower():
                # Avoid generic 'time' columns if they are just duration (like 'elapsed_time')
                if 'elapsed' not in col.lower() and 'duration' not in col.lower() and 'zone' not in col.lower():
                    return col
    except Exception as e:
        print(f"Warning: Could not get info for table {table_name}: {e}")
                 
    return None

def dump_table(conn, table_name, date_str):
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

def main():
    parser = argparse.ArgumentParser(description="Dump health data to JSON.")
    parser.add_argument('-f', '--folder', default=os.path.expanduser("~/HealthData/DBs/"), 
                        help="Directory containing the database files.")
    parser.add_argument('-d', '--date', default=datetime.now().strftime('%Y-%m-%d'),
                        help="Target date (YYYY-MM-DD). Defaults to today.")
    parser.add_argument('-w', '--week', action='store_true',
                        help="Dump the last 7 days ending on the target date.")
    
    args = parser.parse_args()

    # If no flags are provided, print help
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)
    
    db_dir = os.path.expanduser(args.folder)
    target_date = args.date
    
    if not os.path.exists(db_dir):
        print(f"Error: Directory not found: {db_dir}")
        return

    # Determine dates to dump
    try:
        target_dt = datetime.strptime(target_date, '%Y-%m-%d')
    except ValueError:
        print("Error: Invalid date format. Use YYYY-MM-DD.")
        return

    dates_to_dump = [target_date]
    if args.week:
        dates_to_dump = [(target_dt - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(6, -1, -1)]
        print(f"Dumping 7 days of data ({dates_to_dump[0]} to {dates_to_dump[-1]}) from {db_dir}...")
    else:
        print(f"Dumping data for {target_date} from {db_dir}...")
    
    output_data = {}
    db_files = glob.glob(os.path.join(db_dir, "*.db"))
    
    if not db_files:
        print("No .db files found in the directory.")
        return

    for db_path in db_files:
        db_name = os.path.basename(db_path)
        print(f"Processing {db_name}...")
        
        conn = get_db_connection(db_path)
        if not conn:
            continue
            
        db_data = {}
        
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row['name'] for row in cursor.fetchall()]
            
            for table in tables:
                if table.startswith('sqlite_'):
                    continue
                
                # Collect rows for all requested dates
                all_rows = []
                for date_str in dates_to_dump:
                    rows = dump_table(conn, table, date_str)
                    if rows:
                        all_rows.extend(rows)
                        
                if all_rows:
                    db_data[table] = all_rows
                    print(f"  Found {len(all_rows)} records in '{table}'")
                
        except sqlite3.Error as e:
            print(f"Error reading {db_name}: {e}")
        finally:
            conn.close()
            
        if db_data:
            output_data[db_name] = db_data

    # Determine output filename
    if args.week:
        output_filename = f"health_dump_week_ending_{target_date}.json"
    else:
        output_filename = f"health_dump_{target_date}.json"
    
    try:
        with open(output_filename, 'w') as f:
            json.dump(output_data, f, indent=2, default=str)
        print(f"\nDump complete! Saved to {os.path.abspath(output_filename)}")
    except Exception as e:
        print(f"Error saving file: {e}")

if __name__ == "__main__":
    main()
