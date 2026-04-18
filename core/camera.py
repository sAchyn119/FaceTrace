import cv2
import numpy as np
import sys
import os

# Add parent directory so we can import our existing modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import load_known_faces
from recognize import recognize_faces
from attendance import mark_attendance

# Global camera state
_camera    = None
_recognizer = None
_label_map  = None
_cascade    = None

def get_recognizer():
    global _recognizer, _label_map, _cascade
    if _recognizer is None:
        _recognizer, _label_map, _cascade = load_known_faces()
    return _recognizer, _label_map, _cascade

def generate_frames():
    """Generator — yields JPEG frames for live video stream."""
    global _camera

    _camera = cv2.VideoCapture(0)
    recognizer, label_map, cascade = get_recognizer()

    while True:
        success, frame = _camera.read()
        if not success:
            break

        frame = cv2.flip(frame, 1)

        if recognizer:
            small  = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
            results = recognize_faces(small, recognizer, label_map, cascade)

            for (name, confidence, top, right, bottom, left) in results:
                scale  = 2
                top    *= scale
                right  *= scale
                bottom *= scale
                left   *= scale

                color = (0, 255, 0) if name != "Unknown" else (0, 0, 255)
                label = f"{name} ({confidence}%)"

                cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
                cv2.rectangle(frame, (left, bottom-28), (right, bottom), color, cv2.FILLED)
                cv2.putText(frame, label, (left+4, bottom-8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255,255,255), 1)

                if name != "Unknown":
                    mark_attendance(name)

        ret, buffer = cv2.imencode('.jpg', frame)
        frame_bytes = buffer.tobytes()

        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

def release_camera():
    global _camera
    if _camera:
        _camera.release()
        _camera = None

def reload_model():
    """Call this after adding a new student photo."""
    global _recognizer, _label_map, _cascade
    _recognizer, _label_map, _cascade = load_known_faces()