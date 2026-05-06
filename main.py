import cv2
import os
from datetime import datetime
from database import load_known_faces
from recognize import recognize_faces
from attendance import mark_attendance, get_today_attendance
from config import CAMERA_ID, FRAME_SCALE, BOX_COLOR_KNOWN, BOX_COLOR_UNKNOWN, FONT_SCALE, UNKNOWN_DIR

NO_FACE_TIMEOUT  = 10   # seconds of NO face → camera auto-closes
UNKNOWN_SAVE_GAP = 5    # seconds between saving same unknown face position


def draw_results(frame, results):
    scale = int(1 / FRAME_SCALE)
    for (name, confidence, top, right, bottom, left) in results:
        top    *= scale
        right  *= scale
        bottom *= scale
        left   *= scale
        color  = BOX_COLOR_KNOWN if name != "Unknown" else BOX_COLOR_UNKNOWN
        label  = f"{name} ({confidence}%)"
        cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
        cv2.rectangle(frame, (left, bottom - 30), (right, bottom), color, cv2.FILLED)
        cv2.putText(frame, label, (left + 6, bottom - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, FONT_SCALE * 0.6, (255, 255, 255), 1)
    return frame


def save_unknown_face(frame, top, right, bottom, left):
    # FIX: exist_ok=True (bool) instead of exist_ok=1
    os.makedirs(UNKNOWN_DIR, exist_ok=True)
    scale     = int(1 / FRAME_SCALE)
    top       *= scale
    right     *= scale
    bottom    *= scale
    left      *= scale
    face_crop = frame[top:bottom, left:right]
    if face_crop.size == 0:
        return
    # Include microseconds so multiple saves in the same second do not share one path.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    filename  = os.path.join(UNKNOWN_DIR, f"unknown_{timestamp}.jpg")
    cv2.imwrite(filename, face_crop)
    print(f"[UNKNOWN] Saved: {filename}")


def run():
    recognizer, label_map, face_cascade = load_known_faces()
    if recognizer is None:
        print("\n[ERROR] No faces loaded! Add photos to known_faces/ folder.\n")
        return

    print(f"[INFO] Loaded {len(label_map)} person(s): {list(label_map.values())}")

    # Windows-friendly open: prefer DirectShow (faster/more reliable on many machines)
    backend = cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else cv2.CAP_ANY
    cap = cv2.VideoCapture(CAMERA_ID, backend)
    if not cap.isOpened():
        cap = cv2.VideoCapture(CAMERA_ID)

    if not cap.isOpened():
        print(f"\n[ERROR] Cannot open camera (id={CAMERA_ID})\n")
        return

    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass

    # Warm up a few frames (reduces slow/black first frame)
    for _ in range(8):
        cap.read()

    print("\n[FaceTrace] Running...")
    print(f"  -> Auto-closes after {NO_FACE_TIMEOUT}s of no face detected")
    print(f"  → Unknown faces saved to: {UNKNOWN_DIR}")
    print("  → Press Q to quit manually\n")

    last_face_time    = datetime.now()
    last_unknown_save = {}

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[ERROR] Cannot read frame!")
            break

        frame       = cv2.flip(frame, 1)
        small_frame = cv2.resize(frame, (0, 0), fx=FRAME_SCALE, fy=FRAME_SCALE)
        results     = recognize_faces(small_frame, recognizer, label_map, face_cascade, save_unknown=False)
        now         = datetime.now()

        if results:
            last_face_time = now

        for (name, confidence, top, right, bottom, left) in results:
            if name != "Unknown":
                mark_attendance(name)
            else:
                pos_key   = f"{left // 50}_{top // 50}"
                last_save = last_unknown_save.get(pos_key)
                if last_save is None or (now - last_save).seconds >= UNKNOWN_SAVE_GAP:
                    save_unknown_face(frame, top, right, bottom, left)
                    last_unknown_save[pos_key] = now

        frame           = draw_results(frame, results)
        attendance      = get_today_attendance()
        seconds_no_face = (now - last_face_time).seconds

        cv2.putText(frame, f"Present today: {len(attendance)}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

        if 0 < seconds_no_face < NO_FACE_TIMEOUT:
            remaining = NO_FACE_TIMEOUT - seconds_no_face
            cv2.putText(frame, f"No face... closing in {remaining}s",
                        (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2)

        cv2.imshow("FaceTrace — Face Recognition Attendance", frame)

        if seconds_no_face >= NO_FACE_TIMEOUT:
            print(f"\n[FaceTrace] No face for {NO_FACE_TIMEOUT}s → camera closed.")
            break

        if cv2.waitKey(1) & 0xFF == ord('q'):
            print("\n[FaceTrace] Quit by user.")
            break

    cap.release()
    cv2.destroyAllWindows()
    print("\n--- TODAY'S ATTENDANCE ---")
    for r in get_today_attendance():
        print(f"  {r['Name']} — {r['Time']}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "runserver":
        print(
            "[FaceTrace] `main.py` is the desktop OpenCV app, not Django.\n"
            "For the web UI, run:\n  python manage.py runserver\n",
            file=sys.stderr,
        )
        sys.exit(2)

    run()