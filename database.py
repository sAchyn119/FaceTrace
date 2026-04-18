import cv2
import numpy as np
import os
from PIL import Image
from config import KNOWN_FACES_DIR, MODEL_PATH

def build_and_save_model():
    """
    Reads all images from known_faces/, trains LBPH model, saves it.
    Returns (label_map: dict{id->name}, success: bool)
    """
    print("[INFO] Loading known faces from:", KNOWN_FACES_DIR)

    if not os.path.exists(KNOWN_FACES_DIR):
        print("[ERROR] known_faces/ folder not found!")
        return {}, False

    files = [f for f in os.listdir(KNOWN_FACES_DIR)
             if f.lower().endswith(('.jpg', '.jpeg', '.png'))]

    if not files:
        print("[ERROR] No image files found in known_faces/")
        print("        Add photos named like:  john.jpg  sara.png")
        return {}, False

    face_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

    faces      = []
    labels     = []
    label_map  = {}   # {int_id: "Name"}
    label_id   = 0

    for filename in files:
        name = os.path.splitext(filename)[0]   # "john.jpg" -> "john"
        path = os.path.join(KNOWN_FACES_DIR, filename)
        print(f"[INFO] Processing: {filename}  →  name='{name}'")

        try:
            # Load as grayscale
            pil_img = Image.open(path).convert("L")

            # Resize large images so dlib/OpenCV doesn't crash
            w, h = pil_img.size
            if w > 800:
                ratio   = 800 / w
                pil_img = pil_img.resize((800, int(h * ratio)), Image.LANCZOS)

            img_np = np.array(pil_img, dtype=np.uint8)

            detected = face_cascade.detectMultiScale(img_np, 1.3, 5)

            if len(detected) == 0:
                print(f"  [WARN] No face detected in {filename} — skipping")
                continue

            x, y, w, h = detected[0]
            face_roi   = img_np[y:y+h, x:x+w]
            face_roi   = cv2.resize(face_roi, (200, 200))

            if name not in [v for v in label_map.values()]:
                label_map[label_id] = name
                current_id = label_id
                label_id  += 1
            else:
                current_id = [k for k, v in label_map.items() if v == name][0]

            faces.append(face_roi)
            labels.append(current_id)
            print(f"  [OK] Loaded face for '{name}' (id={current_id})")

        except Exception as e:
            print(f"  [ERROR] Failed on {filename}: {e}")
            continue

    if not faces:
        print("[ERROR] No valid faces found. Check your known_faces/ images.")
        return {}, False

    # Train LBPH recognizer
    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.train(faces, np.array(labels))
    recognizer.save(MODEL_PATH)

    print(f"\n[INFO] Model trained with {len(faces)} face(s): {list(label_map.values())}")
    print(f"[INFO] Saved to: {MODEL_PATH}\n")
    return label_map, True


def load_known_faces():
    """
    Load or rebuild the face model.
    Returns (recognizer, label_map) or (None, {}) on failure.
    """
    face_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

    # Always rebuild model from known_faces/ so new photos are picked up
    label_map, ok = build_and_save_model()
    if not ok:
        return None, {}, face_cascade

    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.read(MODEL_PATH)
    return recognizer, label_map, face_cascade
