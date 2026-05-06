import cv2
import numpy as np
import os
from PIL import Image
from config import KNOWN_FACES_DIR, MODEL_PATH, CASCADE_PATH, SCALE_FACTOR, MIN_NEIGHBORS


def build_and_save_model():
    """
    Reads all images from known_faces/, trains LBPH model, saves it.

    FOLDER STRUCTURE SUPPORTED:
      Option A — Subfolders per person (webcam registration, 50 photos):
        known_faces/
          john/
            photo1.jpg ... photo50.jpg

      Option B — Single flat image per person (fallback):
        known_faces/
          john.jpg

    KEY FIX: Subfolder images are used directly without strict Haar detection.
    50 webcam photos include dark/blurry frames intentionally — LBPH can still
    learn from them. Only truly black frames (brightness < 10) are skipped.
    """
    print("[INFO] Loading known faces from:", KNOWN_FACES_DIR)

    if not os.path.exists(KNOWN_FACES_DIR):
        print("[ERROR] known_faces/ folder not found!")
        return {}, False

    face_cascade = cv2.CascadeClassifier(CASCADE_PATH)

    faces     = []
    labels    = []
    label_map = {}
    label_id  = 0

    # ── Collect (name, path, is_subfolder) tuples ─────────────────────────
    image_pairs = []

    # Option A: subfolders per person
    for entry in os.scandir(KNOWN_FACES_DIR):
        if entry.is_dir():
            name = entry.name
            for f in os.scandir(entry.path):
                if f.name.lower().endswith(('.jpg', '.jpeg', '.png')):
                    image_pairs.append((name, f.path, True))

    # Option B: flat root images (fallback)
    for f in os.scandir(KNOWN_FACES_DIR):
        if f.is_file() and f.name.lower().endswith(('.jpg', '.jpeg', '.png')):
            name = os.path.splitext(f.name)[0]
            image_pairs.append((name, f.path, False))

    if not image_pairs:
        print("[ERROR] No image files found in known_faces/")
        return {}, False

    print(f"[INFO] Found {len(image_pairs)} image(s) to process...")

    for name, path, is_subfolder in image_pairs:
        filename = os.path.basename(path)
        print(f"[INFO] Processing: {filename}  ->  name='{name}'")

        try:
            pil_img = Image.open(path).convert("L")

            w, h = pil_img.size
            if w > 800:
                ratio   = 800 / w
                pil_img = pil_img.resize((800, int(h * ratio)), Image.LANCZOS)

            img_np = np.array(pil_img, dtype=np.uint8)

            # Skip completely black frames
            brightness = img_np.mean()
            if brightness < 10:
                print(f"  [WARN] Skipping black frame: {filename} (brightness={brightness:.1f})")
                continue

            if is_subfolder:
                # Webcam captured frames — try lenient Haar first, fallback to full frame
                detected = face_cascade.detectMultiScale(
                    img_np,
                    scaleFactor=1.05,
                    minNeighbors=2,
                    minSize=(20, 20),
                )
                if len(detected) > 0:
                    x, y, fw, fh = detected[0]
                    face_roi = img_np[y:y+fh, x:x+fw]
                else:
                    # Use full frame — LBPH still learns texture from it
                    face_roi = img_np
                    print(f"  [INFO] No face detected, using full frame")
            else:
                # Flat root image — strict Haar detection
                detected = face_cascade.detectMultiScale(
                    img_np,
                    scaleFactor=SCALE_FACTOR,
                    minNeighbors=MIN_NEIGHBORS,
                    minSize=(30, 30),
                )
                if len(detected) == 0:
                    print(f"  [WARN] No face detected in {filename} — skipping")
                    continue
                x, y, fw, fh = detected[0]
                face_roi = img_np[y:y+fh, x:x+fw]

            face_roi = cv2.resize(face_roi, (200, 200))

            # Assign label id
            if name not in label_map.values():
                label_map[label_id] = name
                current_id = label_id
                label_id  += 1
            else:
                current_id = next(k for k, v in label_map.items() if v == name)

            faces.append(face_roi)
            labels.append(current_id)
            print(f"  [OK] Loaded face for '{name}' (id={current_id})")

        except Exception as e:
            print(f"  [ERROR] Failed on {filename}: {e}")
            continue

    if not faces:
        print("[ERROR] No valid faces found.")
        return {}, False

    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.train(faces, np.array(labels))
    recognizer.save(MODEL_PATH)

    print(f"\n[INFO] Model trained with {len(faces)} face(s): {list(label_map.values())}")
    print(f"[INFO] Saved to: {MODEL_PATH}\n")
    return label_map, True


def load_known_faces():
    face_cascade = cv2.CascadeClassifier(CASCADE_PATH)
    label_map, ok = build_and_save_model()
    if not ok:
        return None, {}, face_cascade
    recognizer = cv2.face.LBPHFaceRecognizer_create()
    recognizer.read(MODEL_PATH)
    return recognizer, label_map, face_cascade