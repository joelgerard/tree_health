#!/usr/bin/env python3
import argparse
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.profile_type import FileType, Manufacturer, Sport, SubSport, Event, EventType

# Garmin Epoch not needed if fit_tool handles Unix Ms conversion


def parse_tcx(file_path):
    """
    Parses a TCX file and returns a list of (dt, hr, watts, lat, lon, alt, dist, speed) tuples.
    """
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
    except Exception as e:
        print(f"Error parsing TCX file: {e}")
        sys.exit(1)

    namespaces = {
        'tcd': 'http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2',
        'ext': 'http://www.garmin.com/xmlschemas/ActivityExtension/v2'
    }
    
    def find_val(elem, path):
        return elem.find(path, namespaces)

    points = []
    
    for trackpoint in root.findall('.//tcd:Trackpoint', namespaces):
        time_elem = find_val(trackpoint, 'tcd:Time')
        if time_elem is None:
            continue
            
        time_str = time_elem.text
        if time_str.endswith('Z'):
            time_str = time_str[:-1] + '+00:00'
        try:
            dt = datetime.fromisoformat(time_str)
        except:
             # Very basic fallback
            dt = datetime.strptime(time_str[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
            
        # Optional fields
        hr = None
        hr_elem = find_val(trackpoint, 'tcd:HeartRateBpm/tcd:Value')
        if hr_elem is not None:
            try: hr = int(hr_elem.text)
            except: pass
            
        watts = None
        speed = None
        dist = None
        lat = None
        lon = None
        alt = None

        # Position
        pos = find_val(trackpoint, 'tcd:Position')
        if pos is not None:
            lat_elem = find_val(pos, 'tcd:LatitudeDegrees')
            lon_elem = find_val(pos, 'tcd:LongitudeDegrees')
            if lat_elem is not None and lon_elem is not None:
                try:
                    lat = float(lat_elem.text)
                    lon = float(lon_elem.text)
                except: pass
        
        alt_elem = find_val(trackpoint, 'tcd:AltitudeMeters')
        if alt_elem is not None:
            try: alt = float(alt_elem.text)
            except: pass
            
        dist_elem = find_val(trackpoint, 'tcd:DistanceMeters')
        if dist_elem is not None:
            try: dist = float(dist_elem.text)
            except: pass
            
        # Extensions (Watts, Speed)
        extensions = find_val(trackpoint, 'tcd:Extensions')
        if extensions is not None:
            tpx = find_val(extensions, 'ext:TPX')
            if tpx is not None:
                watts_elem = find_val(tpx, 'ext:Watts')
                if watts_elem is not None:
                    try: watts = int(float(watts_elem.text))
                    except: pass
                
                speed_elem = find_val(tpx, 'ext:Speed')
                if speed_elem is not None:
                    try: speed = float(speed_elem.text)
                    except: pass
                    
        if speed_elem is not None:
                    try: speed = float(speed_elem.text)
                    except: pass
        
        # Validate timestamp
        # fit_tool likely expects Unix Ms.
        ts_ms = dt.timestamp() * 1000
            
        points.append({
            'dt': dt,
            'ts': ts_ms,
            'hr': hr,
            'watts': watts,
            'lat': lat,
            'lon': lon,
            'alt': alt,
            'dist': dist,
            'speed': speed
        })
    
    # Sort by time
    points.sort(key=lambda x: x['ts'])
        
    return points

def to_semicircles(degrees):
    if degrees is None:
        return None
    return int(degrees * (2**31) / 180)

def main():
    parser = argparse.ArgumentParser(description='Convert TCX to FIT.')
    parser.add_argument('input', help='Input TCX file')
    parser.add_argument('output', help='Output FIT file')
    
    args = parser.parse_args()
    
    print(f"Parsing {args.input}...")
    points = parse_tcx(args.input)
    
    if not points:
        print("No points found!")
        sys.exit(1)
        
    print(f"Found {len(points)} points. Building FIT file...")
    
    builder = FitFileBuilder(auto_define=True, min_string_size=50)
    
    # File ID
    file_id = FileIdMessage()
    file_id.type = FileType.ACTIVITY
    file_id.manufacturer = Manufacturer.GARMIN
    file_id.product = 0
    file_id.serial_number = 12345
    
    
    start_dt = points[0]['dt']
    start_ts = points[0]['ts']
    
    print(f"Start Time: {start_dt} (Unix Ms: {start_ts})")
    
    file_id.time_created = start_ts
    builder.add(file_id)
    
    # Start Event
    event = EventMessage()
    event.timestamp = start_ts
    event.event = Event.TIMER
    event.event_type = EventType.START
    event.event_group = 0
    builder.add(event)
    
    # Records
    for p in points:
        ts = p['ts']
        
        # Check if gap > 5 seconds, maybe insert stop/start? 
        # For simplicity, we just dump records.
        
        record = RecordMessage()
        record.timestamp = ts
        
        if p['hr'] is not None:
            record.heart_rate = p['hr']
        if p['watts'] is not None:
            record.power = p['watts']
        if p['lat'] is not None:
            record.position_lat = to_semicircles(p['lat'])
        if p['lon'] is not None:
            record.position_long = to_semicircles(p['lon'])
        if p['alt'] is not None:
             # Altitude is usually scaled in FIT?
             # fit_tool likely handles scaling if we use the property, OR we need to match scaling.
             # According to SDK, altitude is (value + 500) * 5. Or similar.
             # fit_tool generated code usually expects raw value or scaled value?
             # Usually properties in these libraries take the 'physical' value and scale it internally
             # OR they take the integer raw value.
             # Let's inspect `RecordMessage` property setters if I could...
             # Most "nice" SDKs take physical value.
             # Let's assume physical (float) for now. If it's weird, we check.
             # Wait, fit_tool by 'miv' / stages might require raw. 
             # I'll check carefully.
             # Actually, simpler: Timestamp is definitely INT. 
             # Altitude: usually float in high level libs.
             # Let's try setting it as float. If it crashes, we know.
             record.altitude = p['alt']
             
        if p['dist'] is not None:
            record.distance = p['dist']
        if p['speed'] is not None:
            record.speed = p['speed']
            
        builder.add(record)
        
    # Stop Event
    end_ts = points[-1]['ts']
    end_dt = points[-1]['dt']
    
    event_stop = EventMessage()
    event_stop.timestamp = end_ts
    event_stop.event = Event.TIMER
    event_stop.event_type = EventType.STOP_ALL
    event_stop.event_group = 0
    builder.add(event_stop)
    
    # Lap (Summary) - Minimal
    lap = LapMessage()
    lap.timestamp = end_ts
    lap.start_time = start_ts
    lap.total_elapsed_time = (end_dt - start_dt).total_seconds()
    lap.total_timer_time = lap.total_elapsed_time
    builder.add(lap)
    
    # Session (Summary)
    session = SessionMessage()
    session.timestamp = end_ts
    session.start_time = start_ts
    session.total_elapsed_time = lap.total_elapsed_time
    session.total_timer_time = lap.total_elapsed_time
    session.sport = Sport.CYCLING
    session.sub_sport = SubSport.GENERIC
    session.first_lap_index = 0
    session.num_laps = 1
    builder.add(session)
    
    # Activity (Summary)
    activity = ActivityMessage()
    activity.timestamp = end_ts
    activity.num_sessions = 1
    activity.type = FileType.ACTIVITY
    activity.event = Event.ACTIVITY
    activity.event_type = EventType.STOP
    builder.add(activity)
    
    fit_file = builder.build()
    
    print(f"Writing to {args.output}...")
    fit_file.to_file(args.output)
    print("Done!")

if __name__ == "__main__":
    main()
