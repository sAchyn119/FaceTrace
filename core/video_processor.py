import cv2
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import load_known_faces
from recognize import recognize_faces
from config import CONFIDENCE_THRESHOLD


def process_video(video_path, output_dir):
    """
    Process uploaded video — detect & recognise all faces.
    Returns dict: { person_name: [ {timestamp, confidence, thumbnail} ] }

    FIX: Confidence filter was inverted — was skipping valid detections.
         Now correctly skips only genuinely low-confidence matches.
    """
    os.makedirs(output_dir, exist_ok=True)

    recognizer, label_map, cascade = load_known_faces()
    if recognizer is None:
        print("[VIDEO] No model loaded — add students first.")
        return {}

    cap       = cv2.VideoCapture(video_path)
    fps       = cap.get(cv2.CAP_PROP_FPS) or 25
    results   = {}   # { name: [ {second, timestamp, confidence, thumbnail} ] }
    seen      = {}   # { name: last_second_saved } — avoid duplicates per 3s
    frame_num = 0

    print(f"[VIDEO] Processing: {video_path}  FPS={fps}")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_num += 1

        # Process every 10th frame — fast enough for video analysis
        if frame_num % 10 != 0:
            continue

        second     = round(frame_num / fps, 1)
        small      = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
        detections = recognize_faces(
            small, recognizer, label_map, cascade, save_unknown=False
        )

        for (name, confidence, top, right, bottom, left) in detections:

            # ── FIX: correct confidence filter ──
            # OLD (wrong): skipped if confidence < threshold AND name != Unknown
            #   Problem: recognize_faces already applies threshold internally,
            #   so this double-filtered and dropped borderline valid detections.
            # NEW: only skip truly unknown or zero-confidence results
            if name == "Unknown":
                continue
            if confidence <= 0:
                continue

            # Skip if we saved this person in the last 3 seconds
            last_saved = seen.get(name, -999)
            if second - last_saved < 3:
                continue

            seen[name] = second

            # Crop and save face thumbnail
            scale = 2
            t  = top    * scale
            r  = right  * scale
            b  = bottom * scale
            l  = left   * scale

            face_crop = frame[t:b, l:r]
            if face_crop.size == 0:
                continue

            face_crop  = cv2.resize(face_crop, (120, 120))
            thumb_name = f"{name}_{frame_num}.jpg"
            thumb_path = os.path.join(output_dir, thumb_name)
            cv2.imwrite(thumb_path, face_crop)

            if name not in results:
                results[name] = []

            results[name].append({
                'second':     second,
                'timestamp':  _format_time(second),
                'confidence': confidence,
                'thumbnail':  thumb_name,
                'frame':      frame_num,
            })
            print(f"[VIDEO] {name} @ {_format_time(second)} conf={confidence}%")

    cap.release()
    print(f"[VIDEO] Done. Detected: {list(results.keys())}")
    return results


def _format_time(seconds: float) -> str:
    """Convert float seconds → MM:SS string."""
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"