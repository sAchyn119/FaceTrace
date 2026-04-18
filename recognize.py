import cv2
from config import CONFIDENCE_THRESHOLD

def recognize_faces(frame, recognizer, label_map, face_cascade):
    """
    Detect and recognize all faces in a given frame using OpenCV LBPH.
    Returns list of (name, confidence%, top, right, bottom, left)
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    detected = face_cascade.detectMultiScale(gray, scaleFactor=1.3, minNeighbors=5)

    results = []

    for (x, y, w, h) in detected:
        roi  = gray[y:y+h, x:x+w]
        roi  = cv2.resize(roi, (200, 200))

        name       = "Unknown"
        confidence = 0

        try:
            label, dist = recognizer.predict(roi)
            # LBPH dist: 0=perfect match, 100+=bad match
            # Convert to 0-100 confidence % (lower dist = higher confidence)
            confidence = round(max(0, 100 - dist), 1)

            if confidence >= CONFIDENCE_THRESHOLD:
                name = label_map.get(label, "Unknown")

        except Exception as e:
            print(f"[WARN] predict error: {e}")

        # face_recognition returns (top, right, bottom, left)
        # we map (x,y,w,h) → same format
        top, right, bottom, left = y, x+w, y+h, x
        results.append((name, confidence, top, right, bottom, left))

    return results
