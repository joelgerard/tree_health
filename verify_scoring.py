import unittest
from unittest.mock import MagicMock
from datetime import datetime, timedelta
import sys
import os

# Add current dir to path to import app
sys.path.append(os.getcwd())
try:
    from app import get_recovery_score
except ImportError as e:
    print(f"Could not import app.py: {e}")
    # Also print sys.path to see where it's looking
    print(f"sys.path: {sys.path}")
    sys.exit(1)
except Exception as e:
    print(f"Unexpected error during import: {e}")
    sys.exit(1)

class TestRecoveryScore(unittest.TestCase):
    def setUp(self):
        self.conn = MagicMock()
        self.cursor = MagicMock()
        self.conn.cursor.return_value = self.cursor

    def set_mock_data(self, rhr, hrv_today, hrv_7d, stress_avg):
        # Mock fetchone returns for the 4 queries in order:
        # 1. RHR (7-day avg) -> usage says "Current RHR (Avg of last 7 days)" but code treats it as "current_rhr"
        # Wait, the code queries AVG(resting_heart_rate) over last 7 days as "current_rhr".
        # So "rhr" arg here is that avg.
        
        # 2. HRV Today
        # 3. HRV 7d
        # 4. Stress
        
        # The query order in get_recovery_score:
        # 1. RHR Avg
        # 2. HRV Today (last_night_avg)
        # 3. HRV 7d Avg
        # 4. Stress Avg
        
        self.cursor.fetchone.side_effect = [
            (rhr,),          # RHR Avg
            (hrv_today,),    # HRV Today
            (hrv_7d,),       # HRV 7d
            (stress_avg,)    # Stress Avg
        ]

    def test_baseline_day(self):
        # Perfect day matching Golden Era
        # RHR 50.61 -> Z=0
        # HRV Today 51.45, 7d 51.45 -> Ratio 1.0
        # Stress 30 -> Adj 34.5 (vs 35.77) -> Score 100
        self.set_mock_data(50.61, 51.45, 51.45, 30.0)
        
        result = get_recovery_score(self.conn)
        print(f"\nBaseline Day: {result}")
        self.assertAlmostEqual(result['score'], 100.0, delta=1.0)

    def test_saturation_day(self):
        # RHR too low (45) -> Z = (45-50.61)/1.78 = -3.15
        # |Z| > 1.5 -> Score = max(0, 70 - (3.15-1.5)*50) = 70 - 82.5 = 0
        
        # HRV Spike -> Today 70, 7d 50 -> Ratio 1.4
        # Excess 0.2 -> Score = 100 - 0.2*250 = 50
        
        # Stress 30 -> Score 100
        
        # Weighted: 0*0.4 + 50*0.4 + 100*0.2 = 0 + 20 + 20 = 40
        self.set_mock_data(45.0, 70.0, 50.0, 30.0)
        
        result = get_recovery_score(self.conn)
        print(f"Saturation Day: {result}")
        self.assertAlmostEqual(result['score'], 40.0, delta=5.0)

    def test_stress_day(self):
        # RHR High (55) -> Z = (55-50.61)/1.78 = 2.46
        # |Z| > 1.5 -> Score = max(0, 70 - (2.46-1.5)*50) = 70 - 48 = 22
        
        # HRV Drop -> Today 40, 7d 50 -> Ratio 0.8
        # Deficit 0.1 -> Score = 100 - 0.1*500 = 50
        
        # Stress 45 -> Adj 51.75 -> Diff 16 -> Score 100 - 32 = 68
        
        # Weighted: 22*0.4 + 50*0.4 + 68*0.2 = 8.8 + 20 + 13.6 = 42.4
        self.set_mock_data(55.0, 40.0, 50.0, 45.0)
        
        result = get_recovery_score(self.conn)
        print(f"Stress Day: {result}")
        self.assertTrue(35 < result['score'] < 50)

if __name__ == '__main__':
    unittest.main()
