import os

# ===== FACETRACE CONFIGURATION =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

KNOWN_FACES_DIR   = os.path.join(BASE_DIR, "known_faces")
ATTENDANCE_DIR    = os.path.join(BASE_DIR, "attendance_logs")
UNKNOWN_DIR       = os.path.join(BASE_DIR, "unknown_faces")
MODEL_PATH        = os.path.join(BASE_DIR, "face_model.yml")

# Haar cascade — bundled with OpenCV, no path needed
CASCADE_PATH      = cv2_data = "haarcascade_frontalface_default.xml"

CONFIDENCE_THRESHOLD = 30   # 0-100, higher = stricter match
CAMERA_ID            = 0    # 0 = default webcam, try 1 if 0 fails
FRAME_SCALE          = 0.5
FONT_SCALE           = 0.8
BOX_COLOR_KNOWN      = (0, 255, 0)   # green
BOX_COLOR_UNKNOWN    = (0, 0, 255)   # red
