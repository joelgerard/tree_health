import sqlite3
import os
import sys
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

# Ensure we can import from app.py
sys.path.append(os.getcwd())
try:
    from app import get_db_connection, get_recovery_score, calculate_metrics, GARMIN_DB, GARMIN_ACTIVITIES_DB
except ImportError as e:
    print(f"Error: Could not import from app.py. Make sure you are in the correct directory.\nDetails: {e}")
    sys.exit(1)

def analyze_decline():
    print("Starting Health Decline Analysis...")
    
    # Define Periods
    # "Healthy": Jan 1 to May 31
    # "Decline": June 1 to Dec 24 (Today)
    
    start_date = datetime(2025, 1, 1).date()
    split_date = datetime(2025, 6, 1).date()
    end_date = datetime(2025, 12, 24).date()
    
    print(f"Period 1 (Healthy): {start_date} to {split_date - timedelta(days=1)}")
    print(f"Period 2 (Decline): {split_date} to {end_date}")
    
    data = []
    
    conn = get_db_connection(GARMIN_DB)
    conn_activities = get_db_connection(GARMIN_ACTIVITIES_DB)
    
    current = start_date
    total_days = (end_date - start_date).days + 1
    
    print(f"Processing {total_days} days of data... (this might take a moment)")
    
    try:
        count = 0
        while current <= end_date:
            count += 1
            if count % 30 == 0:
                print(f"Processed {count}/{total_days} days...", end='\r')
                
            # determine period
            period = "Healthy" if current < split_date else "Decline"
            
            # Calculate daily status (for Veto)
            try:
                metrics = calculate_metrics(target_date=current, conn=conn, conn_activities=conn_activities)
                daily_status = metrics['final_verdict']['status']
            except Exception:
                daily_status = "GREEN"

            # Calculate Scores
            try:
                score_data = get_recovery_score(conn, daily_status, target_date=current)
                
                row = {
                    "date": current,
                    "period": period,
                    "Recovery Score": score_data['score'],
                    "RHR Score": score_data['details']['rhr']['score'],
                    "HRV Score": score_data['details']['hrv']['score'],
                    "Stress Score": score_data['details']['stress']['score']
                }
                data.append(row)
            except Exception as e:
                # pass on missing data
                pass
            
            current += timedelta(days=1)
            
    finally:
        conn.close()
        conn_activities.close()
        
    print("\nData collecting complete. Calculating statistics...")
    
    df = pd.DataFrame(data)
    
    results = []
    
    signals = ["Recovery Score", "RHR Score", "HRV Score", "Stress Score"]
    
    for signal in signals:
        # Filter groups
        group_healthy = df[df['period'] == 'Healthy'][signal].dropna()
        group_decline = df[df['period'] == 'Decline'][signal].dropna()
        
        if len(group_healthy) == 0 or len(group_decline) == 0:
            continue
            
        mean_healthy = group_healthy.mean()
        mean_decline = group_decline.mean()
        
        # Calculate Delta
        delta = mean_decline - mean_healthy
        pct_change = (delta / mean_healthy) * 100 if mean_healthy != 0 else 0
        
        # Calculate Cohen's d (Effect Size)
        # d = (mean1 - mean2) / pooled_std
        n1, n2 = len(group_healthy), len(group_decline)
        var1, var2 = group_healthy.var(), group_decline.var()
        
        pooled_std = np.sqrt(((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2))
        cohens_d = (mean_decline - mean_healthy) / pooled_std if pooled_std != 0 else 0
        
        results.append({
            "Signal": signal,
            "Healthy Avg": round(mean_healthy, 1),
            "Decline Avg": round(mean_decline, 1),
            "Delta": round(delta, 1),
            "Effect Size (Cohen's d)": round(abs(cohens_d), 2)  # Absolute value for magnitude ranking
        })
        
    # Sort by Effect Size (Magnitude of shift)
    results_df = pd.DataFrame(results).sort_values(by="Effect Size (Cohen's d)", ascending=False)
    
    print("\n" + "="*80)
    print(f"ANALYSIS RESULT: Which signal correlates most with the health decline?")
    print("="*80)
    print(results_df.to_string(index=False))
    print("="*80)
    
    winner = results_df.iloc[0]
    print(f"\n🏆 The MOST ACCURATE signal is: {winner['Signal']}")
    print(f"   It shifted by {winner['Delta']} points (Average {winner['Healthy Avg']} -> {winner['Decline Avg']})")
    print(f"   Effect Size: {winner['Effect Size (Cohen\'s d)']} (Anything > 0.8 is 'Large', > 1.2 is 'Very Large')")

if __name__ == "__main__":
    analyze_decline()
