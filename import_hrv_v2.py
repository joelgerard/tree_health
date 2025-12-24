#!/usr/bin/env python3
import os
import sqlite3
import csv
import re
import argparse
from pathlib import Path
from datetime import datetime

def setup_db(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS hrv (
            day DATE NOT NULL,
            weekly_avg INTEGER,
            last_night_avg INTEGER,
            last_night_5min_high INTEGER,
            baseline_low INTEGER,
            baseline_upper INTEGER,
            status TEXT,
            PRIMARY KEY (day)
        )
    ''')
    conn.commit()
    return conn

def parse_ms_value(value_str):
    if not value_str or value_str == '--':
        return None
    match = re.search(r'(\d+)', value_str)
    return int(match.group(1)) if match else None

def parse_baseline(value_str):
    if not value_str or value_str == '--':
        return None, None
    matches = re.findall(r'(\d+)', value_str)
    if len(matches) >= 2:
        return int(matches[0]), int(matches[1])
    return None, None

def parse_csv_file(file_path):
    data = {}
    try:
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) < 2:
                    continue
                key = row[0].strip()
                val = row[1].strip()
                if key == 'Date':
                    data['day'] = val
                elif key == 'Overnight HRV':
                    data['last_night_avg'] = parse_ms_value(val)
                elif key == 'Baseline':
                    data['baseline_low'], data['baseline_upper'] = parse_baseline(val)
                elif key == '7d Avg':
                    data['weekly_avg'] = parse_ms_value(val)
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return None
    return data if 'day' in data else None

def process_directory(directory, conn):
    cursor = conn.cursor()
    files = list(Path(directory).glob("HRV Status - *.csv"))
    print(f"Found {len(files)} files in {directory}")
    
    inserted_count = 0
    updated_count = 0
    
    for file_path in files:
        record = parse_csv_file(file_path)
        if not record:
            continue
            
        try:
            cursor.execute('''
                INSERT INTO hrv 
                (day, weekly_avg, last_night_avg, baseline_low, baseline_upper)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(day) DO UPDATE SET
                    weekly_avg=excluded.weekly_avg,
                    last_night_avg=excluded.last_night_avg,
                    baseline_low=excluded.baseline_low,
                    baseline_upper=excluded.baseline_upper
            ''', (
                record['day'], 
                record.get('weekly_avg'), 
                record.get('last_night_avg'), 
                record.get('baseline_low'), 
                record.get('baseline_upper')
            ))
            if cursor.rowcount == 1:
                inserted_count += 1
            else:
                updated_count += 1
        except sqlite3.Error as e:
            print(f"Database error on {file_path.name}: {e}")

    conn.commit()
    print(f"Summary: Imported {inserted_count}, Updated {updated_count}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--dir', required=True)
    parser.add_argument('--db', required=True)
    args = parser.parse_args()
    
    conn = setup_db(args.db)
    process_directory(args.dir, conn)
    conn.close()
