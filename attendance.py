import csv
import os
from datetime import datetime
from config import ATTENDANCE_DIR, KNOWN_FACES_DIR

_marked: dict[str, set] = {}
_current_date: str = datetime.now().strftime("%Y-%m-%d")


def _marked_today_set() -> set:
    """Get today's marked set, clearing stale dates on midnight rollover."""
    global _current_date
    today = datetime.now().strftime("%Y-%m-%d")

    if today != _current_date:
        print(f"[ATTENDANCE] Date changed from {_current_date} to {today}, clearing old records")
        _marked.clear()
        _current_date = today

    if today not in _marked:
        _marked[today] = set()
    return _marked[today]


def mark_attendance(name: str) -> None:
    """
    Log a recognised person as Present in today's CSV.
    Unknown faces are ignored here — they are saved to unknown_faces/ by recognize.py.

    FIX: Does NOT touch absent records. Absent is only written by close_day()
    which must be called explicitly at end of session.
    This was the root cause of the bug where present+absent were written together.
    """
    if name == "Unknown":
        return

    os.makedirs(ATTENDANCE_DIR, exist_ok=True)

    today    = datetime.now().strftime("%Y-%m-%d")
    now_time = datetime.now().strftime("%H:%M:%S")
    log_file = os.path.join(ATTENDANCE_DIR, f"attendance_{today}.csv")

    marked = _marked_today_set()
    if name not in marked:
        file_exists = os.path.isfile(log_file)
        with open(log_file, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["Name", "Date", "Time", "Status"])
            writer.writerow([name, today, now_time, "Present"])
        marked.add(name)
        print(f"[ATTENDANCE] {name} marked Present at {now_time}")


def close_day(date_str: str = None) -> None:
    """
    Mark all registered people who were NOT seen today as Absent.

    FIX: This is the ONLY place Absent is written. Call it explicitly
    at end of session (e.g. a 'Close Day' button in your UI).
    It is NEVER called automatically — that was causing the bug where
    registering a new person triggered absent records for everyone else.

    Args:
        date_str: Date in YYYY-MM-DD format. Defaults to today.
    """
    import re

    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    os.makedirs(ATTENDANCE_DIR, exist_ok=True)
    log_file = os.path.join(ATTENDANCE_DIR, f"attendance_{date_str}.csv")

    # Who is already marked Present today?
    present_names: set = set()
    if os.path.isfile(log_file):
        with open(log_file, "r") as f:
            for row in csv.DictReader(f):
                if row.get("Status") == "Present":
                    present_names.add(row["Name"])

    # All registered names from known_faces/ filenames
    all_names: set = set()
    if os.path.exists(KNOWN_FACES_DIR):
        for fname in os.listdir(KNOWN_FACES_DIR):
            if fname.lower().endswith(('.jpg', '.jpeg', '.png')):
                stem  = os.path.splitext(fname)[0]
                match = re.match(r'^(.+?)_\d+$', stem)
                all_names.add(match.group(1) if match else stem)

    absent_names = all_names - present_names
    if not absent_names:
        print("[ATTENDANCE] close_day: all registered people are present.")
        return

    now_time    = datetime.now().strftime("%H:%M:%S")
    file_exists = os.path.isfile(log_file)
    with open(log_file, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["Name", "Date", "Time", "Status"])
        for name in sorted(absent_names):
            writer.writerow([name, date_str, now_time, "Absent"])
            print(f"[ATTENDANCE] {name} marked Absent")


def get_today_attendance() -> list[dict]:
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


def get_attendance_for_date(date_str: str) -> list[dict]:
    """Return attendance records for a specific date (YYYY-MM-DD)."""
    log_file = os.path.join(ATTENDANCE_DIR, f"attendance_{date_str}.csv")
    records  = []
    if os.path.isfile(log_file):
        with open(log_file, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                records.append(row)
    return records


def reset_today() -> None:
    """Manually clear today's in-memory set (useful for testing)."""
    today = datetime.now().strftime("%Y-%m-%d")
    _marked.pop(today, None)