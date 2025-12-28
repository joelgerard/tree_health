import unittest
import os
import sys
import json
from datetime import datetime

# Add parent directory to path to import app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, get_db_connection, calculate_metrics, get_recovery_score, get_trend_data, GARMIN_DB, GARMIN_ACTIVITIES_DB

class TestTreeHealthIntegration(unittest.TestCase):
    
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
        # Check if DBs exist
        if not os.path.exists(GARMIN_DB):
            self.skipTest(f"Garmin DB not found at {GARMIN_DB}")
        if not os.path.exists(GARMIN_ACTIVITIES_DB):
            self.skipTest(f"Garmin Activities DB not found at {GARMIN_ACTIVITIES_DB}")

    def test_db_connection(self):
        """Verify we can connect to the database."""
        try:
            conn = get_db_connection(GARMIN_DB)
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            conn.close()
            self.assertIsNotNone(result)
        except Exception as e:
            self.fail(f"Database connection failed: {e}")

    def test_calculate_metrics_structure(self):
        """Verify calculate_metrics returns expected keys."""
        conn = get_db_connection(GARMIN_DB)
        conn_act = get_db_connection(GARMIN_ACTIVITIES_DB)
        today = datetime.now().strftime('%Y-%m-%d')
        
        try:
            metrics = calculate_metrics(today, conn, conn_act)
            self.assertIn('status', metrics)
            self.assertIn('reason', metrics)
            self.assertIn('target_steps', metrics)
            self.assertIn('metrics', metrics)
        finally:
            conn.close()
            conn_act.close()

    def test_trend_data_structure(self):
        """Verify get_trend_data returns expected structure."""
        conn = get_db_connection(GARMIN_DB)
        try:
            trends = get_trend_data(conn)
            self.assertIn('rhr', trends)
            self.assertIn('batt', trends)
            self.assertIn('stress', trends)
            self.assertIn('recent_costs', trends)
            
            # Check nested keys
            self.assertIn('trend_7d', trends['rhr'])
            self.assertIn('trend_3d', trends['rhr'])
            self.assertIn('val', trends['rhr'])
        finally:
            conn.close()

    def test_recovery_score_structure(self):
        """Verify get_recovery_score returns expected structure."""
        conn = get_db_connection(GARMIN_DB)
        try:
            # We need a dummy status for input
            score_data = get_recovery_score(conn, "GREEN")
            self.assertIn('score', score_data)
            self.assertIn('details', score_data)
            self.assertIn('rhr', score_data['details'])
            self.assertIn('hrv', score_data['details'])
            self.assertIn('stress', score_data['details'])
        finally:
            conn.close()

    def test_index_route(self):
        """Verify the index page loads (Status 200)."""
        response = self.app.get('/')
        self.assertEqual(response.status_code, 200)
        # Check if new widgets are present in HTML
        html = response.data.decode('utf-8')
        self.assertIn("Trend Command Center", html)
        self.assertIn("Trend Compass", html)
        self.assertIn("Efficiency Report", html)

    def test_api_data_route(self):
        """Verify API data route returns JSON."""
        response = self.app.get('/api/data')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn('dates', data)
        self.assertIn('steps', data)
        self.assertIn('rhr', data)
        self.assertIn('stress', data)
        self.assertIn('batt', data)
        self.assertIn('cost', data)
        self.assertIn('active_cals', data)

if __name__ == '__main__':
    unittest.main()
