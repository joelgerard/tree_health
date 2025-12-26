#!/usr/bin/env python3
import argparse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import sys
import pandas as pd
import numpy as np

def parse_tcx(file_path):
    """
    Parses a TCX file and returns a list of dictionaries with Time, HeartRate, and Watts.
    """
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
    except Exception as e:
        print(f"Error parsing TCX file: {e}")
        sys.exit(1)

    # Namespaces
    namespaces = {
        'tcd': 'http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2',
        'ext': 'http://www.garmin.com/xmlschemas/ActivityExtension/v2'
    }

    data = []
    
    # helper to find with namespace
    def find_val(elem, path):
        return elem.find(path, namespaces)

    for trackpoint in root.findall('.//tcd:Trackpoint', namespaces):
        time_elem = find_val(trackpoint, 'tcd:Time')
        if time_elem is None:
            continue
            
        # Parse time
        time_str = time_elem.text
        # Handle 'Z' for UTC
        if time_str.endswith('Z'):
            time_str = time_str[:-1]
            
        try:
            # Simple ISO parse
            dt = datetime.fromisoformat(time_str)
        except ValueError:
             # Fallback
            dt = datetime.strptime(time_str[:19], '%Y-%m-%dT%H:%M:%S')

        # Parse HeartRate
        hr = None
        hr_elem = find_val(trackpoint, 'tcd:HeartRateBpm/tcd:Value')
        if hr_elem is not None:
            try:
                hr = float(hr_elem.text)
            except ValueError:
                pass

        # Parse Watts
        watts = None
        extensions = find_val(trackpoint, 'tcd:Extensions')
        if extensions is not None:
            tpx = find_val(extensions, 'ext:TPX')
            if tpx is not None:
                watts_elem = find_val(tpx, 'ext:Watts')
                if watts_elem is not None:
                    try:
                        watts = float(watts_elem.text)
                    except ValueError:
                        pass
        
        # Only add if we have both HR and Watts (and Time)
        if hr is not None and watts is not None:
            data.append({
                'Time': dt,
                'HeartRate': hr,
                'Watts': watts
            })
        
    return data

def main():
    parser = argparse.ArgumentParser(description='Calculate Cycling VO2Max using Sub-Maximal HR Extrapolation.')
    parser.add_argument('file', help='Path to the TCX file (optional if --file is used)', nargs='?')
    parser.add_argument('--file', help='Path to the TCX file')
    parser.add_argument('--weight', '-w', type=float, default=72.0, help='Body weight in kg (default: 72)')
    parser.add_argument('--max_hr', type=int, default=181, help='Max Heart Rate (default: 181)')

    args = parser.parse_args()
    
    # Handle positional or named file arg
    file_path = args.file if args.file else sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('-') else None
    
    # The argparse definition above is a bit ambiguous for 'file' if flags are mixed. 
    # Let's clean it up: Use args.file if present, else check positional.
    # Actually, let's just re-parse specifically or rely on the parser. 
    # If the user does `python script.py reports/ride.tcx`, `args.file` (positional) gets it.
    # If they do `python script.py --file reports/ride.tcx`, `args.file` (named) gets it.
    # Wait, I defined `file` as positional and `--file` as optional.
    # If I provide a positional, it goes to `file`. If I provide `--file`, it goes to `file` attribute?
    # Python argparse might get confused if I reuse the dest.
    # Let's simplify:
    target_file = args.file
    
    if target_file is None:
         print("Error: Please provide a TCX file path.")
         sys.exit(1)

    print(f"Processing: {target_file}")
    print(f"Parameters: Weight={args.weight}kg, MaxHR={args.max_hr}bpm")

    raw_data = parse_tcx(target_file)
    
    if not raw_data:
        print("No valid trackpoints (with both HR and Watts) found.")
        sys.exit(1)
        
    df = pd.DataFrame(raw_data)
    
    # Calculate elapsed time in seconds to identify warm-up
    start_time = df['Time'].min()
    df['ElapsedSeconds'] = (df['Time'] - start_time).dt.total_seconds()
    
    print(f"Total Data Points: {len(df)}")
    
    # Filter 1: Remove first 5 minutes (300 seconds) - Warm-up
    df_clean = df[df['ElapsedSeconds'] > 300].copy()
    print(f"Points after removing 5min warm-up: {len(df_clean)}")
    
    # Filter 2: Remove stopped time (0 watts)
    df_clean = df_clean[df_clean['Watts'] > 0].copy()
    print(f"Points after removing 0 Watts: {len(df_clean)}")
    
    if len(df_clean) < 60:
        print("Error: Not enough data points remain after filtering (less than 1 minute).")
        sys.exit(1)

    # Perform Linear Regression
    # x = HeartRate, y = Watts
    x = df_clean['HeartRate'].values
    y = df_clean['Watts'].values
    
    # Simple linear regression: y = mx + c
    slope, intercept = np.polyfit(x, y, 1)
    
    # Correlation Coefficient (r)
    correlation_matrix = np.corrcoef(x, y)
    r_squared = correlation_matrix[0, 1] ** 2
    
    print("-" * 30)
    print(f"Regression Stats:")
    print(f" Slope: {slope:.4f}")
    print(f" Intercept: {intercept:.4f}")
    print(f" R-Squared: {r_squared:.4f}")
    print("-" * 30)
    
    # Extrapolate to Max HR
    theoretical_max_watts = (slope * args.max_hr) + intercept
    
    print(f"Extrapolated Max Power at {args.max_hr} bpm: {theoretical_max_watts:.2f} Watts")
    
    # Calculate VO2 Max
    # Formula: VO2_Max = (10.8 * Theoretical_Max_Watts / Weight_kg) + 7
    vo2_max = (10.8 * theoretical_max_watts / args.weight) + 7
    
    print(f"Estimated VO2 Max: {vo2_max:.2f} ml/kg/min")
    print("-" * 30)
    
    if r_squared < 0.3:
        print("WARNING: Low correlation between HR and Power. Data may be too noisy or not steady-state.")
        print("The estimate might be unreliable.")

if __name__ == "__main__":
    main()
