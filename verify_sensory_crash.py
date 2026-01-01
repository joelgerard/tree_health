import unittest
from unittest.mock import MagicMock
import sys
import os
from datetime import datetime, timedelta

# Add current dir to path
sys.path.append(os.getcwd())
try:
    from app import calculate_metrics
except ImportError:
    print("Could not import app.py")

class TestSensoryCrash(unittest.TestCase):
    def setUp(self):
        self.conn = MagicMock()
        self.conn_activities = MagicMock()
        self.cursor = MagicMock()
        self.conn.cursor.return_value = self.cursor
        self.conn_activities.cursor.return_value = MagicMock()

        # Mock Data as per Request
        self.data_map = {
            "2025-12-28": {'steps': 2202, 'stress_avg': 39, 'hr_max': 100},  # Wheelchair Day (Phantom Steps)
            "2025-12-29": {'steps': 4000, 'stress_avg': 39, 'hr_max': 100},  # Safe Day (to see T-1 warning)
            "2025-12-30": {'steps': 4000, 'stress_avg': 41, 'hr_max': 100}   # Safe Day (to see T-2 warning)
        }
        
    def mock_db_queries(self, target_date):
        day_str = target_date
        t1_str = (datetime.strptime(day_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        t2_str = (datetime.strptime(day_str, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")

        # Define data for specific calls in order
        # 1. Main Daily Data (Target)
        target_row = self.data_map.get(day_str, {})
        main_row = {
            'rhr': 50, 'hr_max': target_row.get('hr_max'), 
            'bb_charged': 80, 'stress_avg': target_row.get('stress_avg'),
            'steps': target_row.get('steps'), 'calories_active': 200
        }

        # 2. Sleep Data (Mock Generic)
        sleep_row = {'total_sleep': "08:00:00"}

        # 3. T-2 Data
        t2_row_data = self.data_map.get(t2_str, {})
        t2_row = {
            'steps': t2_row_data.get('steps'), 
            'hr_max': t2_row_data.get('hr_max'), 
            'stress_avg': t2_row_data.get('stress_avg')
        } if t2_row_data else None

        # 4. T-1 Data (New Check)
        t1_row_data = self.data_map.get(t1_str, {})
        t1_row = {
            'steps': t1_row_data.get('steps'), 
            'stress_avg': t1_row_data.get('stress_avg')
        } if t1_row_data else None

        self.cursor.fetchone.side_effect = [main_row, sleep_row, t2_row, t1_row]

    def test_case_a_dec_29_safety_ceiling(self):
        # Target: Dec 29
        # T-1: Dec 28 (Steps 116, Stress 39) -> Should Trigger "T-1 High Idle / Sensory Overload"
        print("\n--- Test Case A: Dec 29 (Safety Ceiling Check) ---")
        self.mock_db_queries("2025-12-29")
        
        result = calculate_metrics("2025-12-29", self.conn, self.conn_activities)
        print(f"Reason: {result['reason']}")
        
        # UI Adapter Logic Requirement:
        # Check if the reason contains the specific trigger allowing the UI to say "T-1 Sensory Overload"
        self.assertIn("T-1 High Idle / Sensory Overload", result['reason'])

    def test_case_b_dec_30_crash_predictor(self):
        # Target: Dec 30
        # T-2: Dec 28 (Steps 116, Stress 39) -> Should Trigger "Lag-2 Risk: Sensory Overload Detected"
        print("\n--- Test Case B: Dec 30 (Crash Predictor T-2 Check) ---")
        self.mock_db_queries("2025-12-30")
        
        result = calculate_metrics("2025-12-30", self.conn, self.conn_activities)
        print(f"Reason: {result['reason']}")
        
        # UI Adapter Logic Requirement:
        # Check if the reason contains the specific trigger allowing the UI to say "Lag-2 Risk: Sensory Overload"
        self.assertIn("Lag-2 Risk: Sensory Overload Detected", result['reason'])

if __name__ == '__main__':
    unittest.main()
