import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

GARMIN_EPOCH = datetime(1989, 12, 31, 0, 0, 0, tzinfo=timezone.utc)

def debug_parse(file_path):
    tree = ET.parse(file_path)
    root = tree.getroot()
    namespaces = {'tcd': 'http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2'}
    
    first_tp = root.find('.//tcd:Trackpoint', namespaces)
    if first_tp:
        time_elem = first_tp.find('tcd:Time', namespaces)
        if time_elem is not None:
            ts = time_elem.text
            print(f"Raw Time String: '{ts}'")
            
            if ts.endswith('Z'):
                ts_iso = ts[:-1] + '+00:00'
            else:
                ts_iso = ts
                
            dt = datetime.fromisoformat(ts_iso)
            print(f"Parsed Datetime: {dt}")
            print(f"Garmin Epoch: {GARMIN_EPOCH}")
            
            delta = (dt - GARMIN_EPOCH).total_seconds()
            print(f"Delta Text: {delta}")
            print(f"Delta Int: {int(delta)}")
            
debug_parse(sys.argv[1])
