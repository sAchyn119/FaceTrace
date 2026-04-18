import csv
import os
from datetime import datetime
from config import ATTENDANCE_DIR

_marked_today = set()

def mark_attendance(name):
    """Log a recognized person's attendance to today's CSV file."""
    if name == "Unknown":
        return

    os.makedirs(ATTENDANCE_DIR, exist_ok=True)   # create folder if missing

    today    = datetime.now().strftime("%Y-%m-%d")
    now_time = datetime.now().strftime("%H:%M:%S")
    log_file = os.path.join(ATTENDANCE_DIR, f"attendance_{today}.csv")

    file_exists = os.path.isfile(log_file)

    if name not in _marked_today:
        with open(log_file, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["Name", "Date", "Time", "Status"])
            writer.writerow([name, today, now_time, "Present"])
        _marked_today.add(name)
        print(f"[ATTENDANCE] {name} marked Present at {now_time}")

def get_today_attendance():
    """Return list of attendance records for today."""
    today    = datetime.now().strftime("%Y-%m-%d")
    log_file = os.path.join(ATTENDANCE_DIR, f"attendance_{today}.csv")
    records  = []
    if os.path.isfile(log_file):
        with open(log_file, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                records.append(row)
    return records

def reset_today():
    _marked_today.clear()
