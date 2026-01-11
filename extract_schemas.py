import sqlite3
import os

databases = [
    "/Users/joelgerard/dev/git/tree_health/garmin_activities.db",
    "/Users/joelgerard/dev/git/tree_health/garmin.db",
    "/Users/joelgerard/GarminDBSync/tree/HealthData/DBs/garmin_monitoring.db",
    "/Users/joelgerard/GarminDBSync/tree/HealthData/DBs/summary.db",
    "/Users/joelgerard/GarminDBSync/tree/HealthData/DBs/garmin_hrv.db",
    "/Users/joelgerard/GarminDBSync/tree/HealthData/DBs/garmin_activities.db",
    "/Users/joelgerard/GarminDBSync/tree/HealthData/DBs/garmin_summary.db",
    "/Users/joelgerard/GarminDBSync/tree/HealthData/DBs/oura.db",
    "/Users/joelgerard/GarminDBSync/tree/HealthData/DBs/garmin.db",
    "/Users/joelgerard/GarminDBSync/joel/HealthData/DBs/garmin_monitoring.db",
    "/Users/joelgerard/GarminDBSync/joel/HealthData/DBs/summary.db",
    "/Users/joelgerard/GarminDBSync/joel/HealthData/DBs/garmin_activities.db",
    "/Users/joelgerard/GarminDBSync/joel/HealthData/DBs/garmin_summary.db",
    "/Users/joelgerard/GarminDBSync/joel/HealthData/DBs/garmin.db"
]

output_file = "all_database_schemas.txt"

with open(output_file, "w") as f:
    for db_path in databases:
        if not os.path.exists(db_path):
            continue
            
        db_name = os.path.basename(db_path)
        # Include profile if in GarminDBSync
        if "GarminDBSync" in db_path:
            parts = db_path.split("/")
            if "joel" in parts or "tree" in parts:
                profile = "joel" if "joel" in parts else "tree"
                db_display_name = f"{profile}/{db_name}"
            else:
                db_display_name = db_name
        else:
            db_display_name = f"workspace/{db_name}"

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # Get all tables
            cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='table';")
            tables = cursor.fetchall()
            
            for table_name, schema in tables:
                if schema:
                    f.write(f"--- DATABASE: {db_display_name} | TABLE: {table_name} ---\n")
                    f.write(f"{schema}\n\n")
            
            conn.close()
        except Exception as e:
            f.write(f"Error accessing {db_path}: {e}\n\n")

print(f"Schemas extracted to {output_file}")
