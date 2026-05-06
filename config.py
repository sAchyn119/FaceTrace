import os
import cv2

# ===== FACETRACE CONFIGURATION =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

KNOWN_FACES_DIR = os.path.join(BASE_DIR, "known_faces")
ATTENDANCE_DIR  = os.path.join(BASE_DIR, "attendance_logs")
UNKNOWN_DIR     = os.path.join(BASE_DIR, "unknown_faces")
MODEL_PATH      = os.path.join(BASE_DIR, "face_model.yml")

CASCADE_PATH = os.path.join(
    cv2.data.haarcascades, "haarcascade_frontalface_default.xml"
)

# ── CONFIDENCE THRESHOLD ──────────────────────────────
# LBPH: confidence = 100 - raw_distance
#   Higher value = more lenient (catches faces at distance/bad lighting)
#   Lower value  = stricter  (fewer false positives)
#
# LBPH always picks the nearest label. Weak scores (~50% conf) often mean wrong person.
# Wrong name -> raise toward 58-62; enrolled user often Unknown -> lower toward 52-54.
CONFIDENCE_THRESHOLD = 45

# ── LBPH DISTANCE REJECTION ───────────────────────────
# Raw LBPH distance above this = reject as Unknown regardless of confidence.
# This is the KEY fix for false positives — LBPH always returns SOME label,
# so we must reject matches where raw distance is too high.
# Lower = stricter. With threshold 56, good matches need dist <= 44; cap rejects outliers.
MAX_LBPH_DISTANCE = 68.0

# ── DETECTION PARAMETERS ─────────────────────────────
SCALE_FACTOR = 1.1
MIN_NEIGHBORS = 6
MIN_FACE_SIZE = (80, 80)

# ── QUALITY GATES ────────────────────────────────────
MIN_RECOGNITION_FACE_SIZE = (60, 60)  # FIX: raised from 30 — tiny faces cause false matches
BLUR_THRESHOLD = 15.0

# Set True when tuning LBPH thresholds (very noisy every frame).
RECOGNITION_DEBUG_PRINTS = False

CAMERA_ID         = 0
FRAME_SCALE       = 0.5
FONT_SCALE        = 0.8
BOX_COLOR_KNOWN   = (0, 255, 0)   # green BGR
BOX_COLOR_UNKNOWN = (0, 0, 255)   # red   BGR