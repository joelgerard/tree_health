import csv
import sqlite3
import os

csv_file = '/Users/joelgerard/dev/git/tree_health/Clarity_Export_Carroll_Theresa_2026-01-11_225626.csv'
db_file = '/Users/joelgerard/dev/git/tree_health/glucose.db'

def import_data():
    # Remove existing DB if it exists to start fresh (for this task)
    if os.path.exists(db_file):
        os.remove(db_file)

    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    # Create table
    cursor.execute('''
        CREATE TABLE glucose_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            idx INTEGER,
            timestamp DATETIME,
            event_type TEXT,
            event_subtype TEXT,
            glucose_value REAL,
            insulin_value REAL,
            carb_value REAL,
            duration TEXT,
            rate_of_change REAL,
            transmitter_time INTEGER,
            transmitter_id TEXT
        )
    ''')
    
    # Indexes
    cursor.execute('CREATE INDEX idx_timestamp ON glucose_readings(timestamp)')

    with open(csv_file, 'r', encoding='utf-8-sig') as f:
        # Some Dexcom reports have leading whitespace or BOM
        reader = csv.DictReader(f)
        to_db = []
        for row in reader:
            # Helper to convert empty string to None
            def clean(val):
                if val is None: return None
                v = val.strip()
                return v if v != '' else None

            to_db.append((
                clean(row.get('Index')),
                clean(row.get('Timestamp (YYYY-MM-DDThh:mm:ss)')),
                clean(row.get('Event Type')),
                clean(row.get('Event Subtype')),
                clean(row.get('Glucose Value (mg/dL)')),
                clean(row.get('Insulin Value (u)')),
                clean(row.get('Carb Value (grams)')),
                clean(row.get('Duration (hh:mm:ss)')),
                clean(row.get('Glucose Rate of Change (mg/dL/min)')),
                clean(row.get('Transmitter Time (Long Integer)')),
                clean(row.get('Transmitter ID'))
            ))

    cursor.executemany('''
        INSERT INTO glucose_readings (
            idx, timestamp, event_type, event_subtype, glucose_value, 
            insulin_value, carb_value, duration, rate_of_change, 
            transmitter_time, transmitter_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', to_db)

    conn.commit()
    conn.close()
    print(f"Imported {len(to_db)} rows into {db_file}")

if __name__ == '__main__':
    try:
        import_data()
    except Exception as e:
        print(f"Error: {e}")
