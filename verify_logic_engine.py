import unittest
from unittest.mock import MagicMock
import sys
import os
from datetime import datetime

# Add current dir to path
sys.path.append(os.getcwd())
try:
    from app import calculate_metrics
except ImportError:
    print("Could not import app.py")

class TestLogicEngine(unittest.TestCase):
    def setUp(self):
        self.conn = MagicMock()
        self.conn_activities = MagicMock()
        self.cursor = MagicMock()
        self.conn.cursor.return_value = self.cursor
        self.conn_activities.cursor.return_value = MagicMock() # Mock activities cursor

    def set_mock_data(self, day_data, sleep_data=None, t2_data=None):
        # Mock fetchone for calculate_metrics queries
        
        # 1. Main Daily Data
        # Query: SELECT rhr, hr_max, bb_charged, stress_avg, steps, calories_active FROM daily_summary ...
        # Result needs to be dict-like (sqlite3.Row)
        main_row = {
            'rhr': day_data.get('rhr'),
            'hr_max': day_data.get('hr_max'),
            'bb_charged': day_data.get('bb_charged'),
            'stress_avg': day_data.get('stress_avg'),
            'steps': day_data.get('steps'),
            'calories_active': day_data.get('calories_active')
        }
        
        # 2. Sleep Data (New Requirement)
        # Query: SELECT total_sleep FROM sleep ...
        sleep_row = {'total_sleep': sleep_data} if sleep_data else None

        # 3. T-2 Data
        # Query: SELECT steps, hr_max, stress_avg FROM daily_summary ... (Checking T-2)
        # Note: We need to update app.py to fetch stress_avg for T-2 first
        t2_row = {
            'steps': t2_data.get('steps'),
            'hr_max': t2_data.get('hr_max'),
            'stress_avg': t2_data.get('stress_avg')
        } if t2_data else None

        # Determine side_effect based on calls
        # We need to match the order of calls in calculate_metrics
        # 1. Main Data
        # 2. Sleep Data (We will add this)
        # 3. T-2 Data
        
        self.cursor.fetchone.side_effect = [main_row, sleep_row, t2_row]

    def test_sensory_load_flag(self):
        # Steps < 1000 AND Stress > 35 -> HIGH RISK
        day_data = {'rhr': 50, 'steps': 800, 'stress_avg': 45, 'bb_charged': 80, 'calories_active': 200}
        self.set_mock_data(day_data, sleep_data="08:00:00", t2_data={'steps': 3000, 'stress_avg': 25})
        
        result = calculate_metrics("2025-01-01", self.conn, self.conn_activities)
        
        print(f"\nSensory Load Test: Status={result['status']}, Reason={result['reason']}")
        # The reason text is replaced when this flag is present
        self.assertIn("System is idling high", result['reason'])
        self.assertEqual(result['status'], "RED")

    def test_crash_predictor_t2(self):
        # T-2: Steps 6000 > 5000 -> Expect "Lag-2 Impact"
        day_data = {'rhr': 50, 'steps': 5000, 'stress_avg': 25, 'bb_charged': 80, 'calories_active': 200}
        t2_data = {'steps': 6000, 'stress_avg': 35} 
        
        self.set_mock_data(day_data, sleep_data="08:00:00", t2_data=t2_data)
        
        result = calculate_metrics("2025-01-01", self.conn, self.conn_activities)
        
        print(f"Crash Predictor High Load Test: Reason={result['reason']}")
        self.assertIn("Lag-2 Impact", result['reason'])

    def test_crash_predictor_risk_fallback(self):
        # T-2: Steps 4000 (1.0) + Stress 45 (1.28) = 2.28 > 1.5 -> Lag-2 Warning
        # Steps <= 5000, so "Impact" check skipped.
        # Stress 45 > 35 but Steps 4000 > 1000, so "Sensory" check skipped.
        day_data = {'rhr': 50, 'steps': 4000, 'stress_avg': 25, 'bb_charged': 80, 'calories_active': 150}
        t2_data = {'steps': 4000, 'stress_avg': 45} 
        
        self.set_mock_data(day_data, sleep_data="08:00:00", t2_data=t2_data)
        
        result = calculate_metrics("2025-01-01", self.conn, self.conn_activities)
        
        print(f"Crash Predictor Risk Fallback Test: Reason={result['reason']}")
        self.assertIn("Lag-2 Warning", result['reason'])

    def test_mitochondrial_efficiency(self):
        # Ratio = Gain / Sleep Hours
        # We need to avoid bb_charged < 50 (Red Flag) to see the Warning
        # Set Gain 55, Sleep 12h -> Ratio 4.58 < 5.0 -> Warning
        day_data = {'rhr': 50, 'steps': 5000, 'stress_avg': 25, 'bb_charged': 55, 'calories_active': 200}
        sleep_data = "12:00:00" # 12 hours
        
        self.set_mock_data(day_data, sleep_data=sleep_data, t2_data={'steps': 3000, 'stress_avg': 25})
        
        result = calculate_metrics("2025-01-01", self.conn, self.conn_activities)
        
        print(f"Mito Efficiency Test: Reason={result['reason']}")
        self.assertIn("Unrefreshing Sleep", result['reason'])

    def test_crash_predictor_sensory_t2(self):
        # T-2: Steps 500 (Low) + Stress 45 (High) -> Sensory Overload
        day_data = {'rhr': 50, 'steps': 4000, 'stress_avg': 25, 'bb_charged': 80, 'calories_active': 150}
        t2_data = {'steps': 500, 'stress_avg': 45} 
        
        self.set_mock_data(day_data, sleep_data="08:00:00", t2_data=t2_data)
        
        result = calculate_metrics("2026-01-01", self.conn, self.conn_activities)
        
        print(f"Crash Predictor Sensory Test: Reason={result['reason']}")
        self.assertIn("Lag-2 Risk: Sensory Overload Detected", result['reason'])

    def test_clean_day(self):
        # All good
        # Lower calories_active to avoid High Physiological Cost (>29 * 1.2 = 34.8)
        # 100 / 4000 * 1000 = 25
        day_data = {'rhr': 50, 'steps': 4000, 'stress_avg': 25, 'bb_charged': 80, 'calories_active': 100}
        sleep_data = "08:00:00" # 8h, Ratio 10
        t2_data = {'steps': 3000, 'stress_avg': 25} # Risk 0.75 + 0.71 = 1.46 < 1.5
        
        self.set_mock_data(day_data, sleep_data=sleep_data, t2_data=t2_data)
        
        result = calculate_metrics("2025-01-01", self.conn, self.conn_activities)
        
        print(f"Clean Day Test: Status={result['status']}")
        self.assertEqual(result['status'], "GREEN")

if __name__ == '__main__':
    unittest.main()
