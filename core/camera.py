import cv2
import os
import sys
import threading
from collections import Counter, defaultdict, deque
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import load_known_faces
from recognize import recognize_faces
from attendance import mark_attendance
from config import CAMERA_ID

# ── State ─────────────────────────────────────────────
_lock        = threading.Lock()
_cameras     = {}      # { camera_index: cv2.VideoCapture }
_recognizer  = None
_label_map   = None
_cascade     = None

# Attendance tracking { "YYYY-MM-DD": { name: datetime } }
_marked_today = {}

# Cheating detection { name: { cam_id: {type, time} } }
_location_log = {}

# Unknown alert throttle { grid_key: datetime }
_unknown_alerted = {}

# Process every Nth frame (speed vs accuracy tradeoff)
PROCESS_EVERY_N      = 2
CHEAT_WINDOW_MINUTES = 45
CAMPUS_NO_ATT_HOURS  = 1

# Majority vote before marking Present — dampens LBPH label flip-flop on one face.
_identity_vote_window   = 9
_identity_vote_min      = 6
# Best-known count must exceed second place by this much (ambiguous A/B mixes → no mark).
_identity_vote_margin   = 3
_identity_queues: dict[tuple, deque] = defaultdict(
    lambda: deque(maxlen=_identity_vote_window)
)
_identity_miss: dict[tuple, int] = defaultdict(int)

# One face in frame uses key (cam, '_single'). LBPH can still flip between two names;
# lock which identity may receive NEW marks until the face leaves (no box) for a while.
_single_face_slot: dict[int, dict] = defaultdict(
    lambda: {'locked_name': None, 'no_face_streak': 0}
)
# Recognition passes with zero face boxes → allow next person to be marked.
_SINGLE_SLOT_CLEAR_STREAK = 12


# ════════════════════════════════════════════
# MODEL LOADING
# ════════════════════════════════════════════

def get_recognizer():
    global _recognizer, _label_map, _cascade
    with _lock:
        if _recognizer is None:
            try:
                _recognizer, _label_map, _cascade = load_known_faces()
            except Exception as e:
                print(f'[MODEL ERROR] {e}')
                _recognizer, _label_map, _cascade = None, {}, None
    return _recognizer, _label_map, _cascade


def reload_model():
    global _recognizer, _label_map, _cascade
    with _lock:
        try:
            _recognizer, _label_map, _cascade = load_known_faces()
            print('[MODEL] Reloaded successfully')
        except Exception as e:
            print(f'[MODEL RELOAD ERROR] {e}')


# ════════════════════════════════════════════
# DATE HELPERS
# ════════════════════════════════════════════

def _today_key():
    return datetime.now().strftime('%Y-%m-%d')


def _marked_set():
    today = _today_key()
    stale = [k for k in list(_marked_today.keys()) if k != today]
    for k in stale:
        del _marked_today[k]
    if today not in _marked_today:
        _marked_today[today] = {}
    return _marked_today[today]


# ════════════════════════════════════════════
# CAMERA OPEN / RELEASE
# ════════════════════════════════════════════

def _open_camera(camera_index: int = 0):
    """
    Open camera with Windows-friendly backend.
    FIXED: robust check, proper settings, warm-up inside here.
    """
    with _lock:
        # Return existing if already open
        if camera_index in _cameras:
            try:
                if _cameras[camera_index].isOpened():
                    return _cameras[camera_index]
            except Exception:
                pass
            try:
                _cameras[camera_index].release()
            except Exception:
                pass
            del _cameras[camera_index]

        # Try DirectShow first (Windows — faster, less black frames)
        cap = None
        if hasattr(cv2, 'CAP_DSHOW'):
            cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap.release()
                cap = None

        # Fallback to default backend
        if cap is None:
            cap = cv2.VideoCapture(camera_index)

        if not cap or not cap.isOpened():
            print(f'[CAMERA ERROR] Cannot open camera {camera_index}')
            return None

        # Set properties BEFORE warm-up
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE,    1)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(cv2.CAP_PROP_FPS,           30)
        except Exception:
            pass

        _cameras[camera_index] = cap
        return cap


def release_camera(camera_index: int = None):
    with _lock:
        if camera_index is None:
            # Release all
            for idx, cap in list(_cameras.items()):
                try:
                    cap.release()
                except Exception:
                    pass
            _cameras.clear()
        else:
            if camera_index in _cameras:
                try:
                    _cameras[camera_index].release()
                except Exception:
                    pass
                del _cameras[camera_index]


# ════════════════════════════════════════════
# DJANGO DB HELPERS
# ════════════════════════════════════════════

def _get_camera_obj(camera_index: int):
    """Get Camera DB object for this index. Returns None if not found."""
    try:
        from core.models import Camera as CameraModel
        return CameraModel.objects.filter(
            camera_index=camera_index,
            is_active=True
        ).first()
    except Exception:
        return None


def _save_attendance_db(name: str, camera_obj) -> bool:
    """
    Save attendance to Django DB.
    Returns True if newly marked.

    FIX 1: Always verify against DB — in-memory cache gets stale
    when camera restarts after reload_model(), blocking legitimate marks.
    FIX 2: Only cache name when record is actually newly created.
    """
    try:
        from django.utils import timezone as tz
        from core.models import Student, Attendance as AttModel

        today  = tz.now().date()
        marked = _marked_set()

        # Fast-path: already marked this camera session
        if name in marked:
            return False

        student = Student.objects.filter(name__iexact=name).first()
        if not student:
            print(f'[DB WARN] "{name}" not in Student table')
            return False

        # Always check DB — cache may be stale after camera restart
        already_in_db = AttModel.objects.filter(
            student=student, date=today, status='present'
        ).exists()

        if already_in_db:
            marked[name] = datetime.now()  # sync cache, skip DB write
            return False

        obj, created = AttModel.objects.get_or_create(
            student  = student,
            date     = today,
            defaults = {
                'status':    'present',
                'marked_by': 'camera',
            }
        )

        if created:
            marked[name] = datetime.now()
            print(f'[DB] ✓ {name} marked Present at '
                  f'{datetime.now().strftime("%H:%M:%S")}')
        return created

    except Exception as e:
        print(f'[DB ERROR] {e}')
        return False


def _log_to_db(name: str, confidence: float,
               camera_obj, frame, x, y, w, h):
    """Log detection to CameraLog. Silent on failure."""
    try:
        from core.models import Student, Faculty, CameraLog as LogModel

        person_type = 'unknown'
        student     = None
        faculty     = None

        if name != 'Unknown':
            s = Student.objects.filter(name__iexact=name).first()
            if s:
                student     = s
                person_type = 'student'
            else:
                f = Faculty.objects.filter(name__iexact=name).first()
                if f:
                    faculty     = f
                    person_type = 'faculty'

        LogModel.objects.create(
            person_type  = person_type,
            student      = student,
            faculty      = faculty,
            unknown_name = name if person_type == 'unknown' else '',
            camera       = camera_obj,
            confidence   = confidence,
        )

        # Update location for cheating detection
        if person_type == 'student' and student and camera_obj:
            _update_location(student.name, camera_obj)

    except Exception:
        pass  # Silent — logging should never crash the camera


def _update_location(name: str, camera_obj):
    """Track location and trigger cheating check."""
    if not camera_obj:
        return
    now = datetime.now()
    if name not in _location_log:
        _location_log[name] = {}
    _location_log[name][camera_obj.id] = {
        'type': camera_obj.location_type,
        'name': camera_obj.name,
        'time': now,
    }
    _check_cheating(name, camera_obj, now)
    _check_campus_no_att(name, camera_obj, now)


def _check_cheating(name: str, cam, now: datetime):
    """Alert if student found outside classroom after marking attendance."""
    try:
        marked = _marked_set()
        if name not in marked:
            return
        if cam.location_type == 'classroom':
            return

        mins = (now - marked[name]).total_seconds() / 60
        if mins > CHEAT_WINDOW_MINUTES:
            return

        from core.models import Student, Attendance, Alert
        from django.utils import timezone as tz

        student = Student.objects.filter(name__iexact=name).first()
        if not student:
            return

        today = tz.now().date()
        if Alert.objects.filter(
            student=student, alert_type='cheating',
            created_at__date=today
        ).exists():
            return

        att = Attendance.objects.filter(
            student=student, date=today, status='present'
        ).select_related('lecture__faculty').first()

        faculty = None
        if att and att.lecture:
            faculty = att.lecture.faculty

        Alert.objects.create(
            alert_type     = 'cheating',
            severity       = 'high',
            title          = f'Attendance Cheating — {name}',
            message        = (
                f'{name} marked present at '
                f'{marked[name].strftime("%H:%M")} but detected at '
                f'{cam.name} ({cam.get_location_type_display()}) '
                f'at {now.strftime("%H:%M")} — '
                f'{int(mins)} min later.'
            ),
            student        = student,
            camera         = cam,
            notify_admin   = True,
            notify_hod     = True,
            notify_faculty = faculty,
        )
        print(f'[CHEAT] Alert created for {name}')

    except Exception as e:
        print(f'[CHEAT ERROR] {e}')


def _check_campus_no_att(name: str, cam, now: datetime):
    """Alert HOD if student on campus with no attendance."""
    try:
        from core.models import Student, Attendance, Alert
        from django.utils import timezone as tz

        today   = tz.now().date()
        student = Student.objects.filter(name__iexact=name).first()
        if not student:
            return

        if Attendance.objects.filter(
            student=student, date=today, status='present'
        ).exists():
            return

        logs = _location_log.get(name, {})
        if not logs:
            return

        first = min(v['time'] for v in logs.values())
        hrs   = (now - first).total_seconds() / 3600
        if hrs < CAMPUS_NO_ATT_HOURS:
            return

        if Alert.objects.filter(
            student=student, alert_type='campus_no_att',
            created_at__date=today
        ).exists():
            return

        Alert.objects.create(
            alert_type   = 'campus_no_att',
            severity     = 'medium',
            title        = f'On Campus Without Attendance — {name}',
            message      = (
                f'{name} on campus since {first.strftime("%H:%M")} '
                f'but no attendance today.'
            ),
            student      = student,
            camera       = cam,
            notify_admin = True,
            notify_hod   = True,
        )
        print(f'[CAMPUS ALERT] {name} on campus {hrs:.1f}h, no attendance')

    except Exception as e:
        print(f'[CAMPUS ERROR] {e}')


def _vote_key(camera_index: int,
              left: int, top: int, right: int, bottom: int,
              face_count: int) -> tuple:
    if face_count == 1:
        return (camera_index, '_single')
    cx = ((left + right) // 2) // 80
    cy = ((top + bottom) // 2) // 80
    return (camera_index, cx, cy)


def _consensus_name(votes: list[str]) -> str | None:
    known = [n for n in votes if n != 'Unknown']
    if not known:
        return None
    ctr = Counter(known)
    ranked = ctr.most_common(2)
    best, freq = ranked[0]
    if freq < _identity_vote_min:
        return None
    if len(ranked) > 1:
        _name2, freq2 = ranked[1]
        if freq - freq2 < _identity_vote_margin:
            return None
    return best


def _prune_stale_vote_keys(active: set[tuple]):
    stale = []
    for k in list(_identity_queues.keys()):
        if k not in active:
            _identity_miss[k] += 1
            if _identity_miss[k] > 15:
                stale.append(k)
        else:
            _identity_miss[k] = 0
    for k in stale:
        _identity_miss.pop(k, None)
        _identity_queues.pop(k, None)


def _alert_unknown(frame, x, y, w, h, camera_obj):
    """Create alert for unknown face. Rate-limited."""
    try:
        grid    = 80
        pos_key = f'{x // grid}_{y // grid}'
        now     = datetime.now()

        last = _unknown_alerted.get(pos_key)
        if last and (now - last).total_seconds() < 30:
            return
        _unknown_alerted[pos_key] = now

        from core.models import Alert
        cam_name = camera_obj.name if camera_obj else 'Unknown'
        Alert.objects.create(
            alert_type   = 'unknown_face',
            severity     = 'high',
            title        = 'Unknown Face Detected',
            message      = (
                f'Unregistered face at {cam_name} '
                f'at {now.strftime("%H:%M:%S")}.'
            ),
            camera       = camera_obj,
            notify_admin = True,
            notify_hod   = True,
        )
        print(f'[UNKNOWN] Alert created @ {cam_name}')

    except Exception as e:
        print(f'[UNKNOWN ALERT ERROR] {e}')


# ════════════════════════════════════════════
# MAIN FRAME GENERATOR
# ════════════════════════════════════════════

def generate_frames(camera_index: int = 0):
    """
    MJPEG frame generator for Django StreamingHttpResponse.

    FIXED:
    - Accepts camera_index parameter (was causing black screen)
    - Warm-up frames OUTSIDE lock to avoid blocking
    - Silent fallback if recognition model not loaded
    - JPEG quality 80% for faster streaming
    - Reuses last detection boxes on skipped frames
    """
    recognizer, label_map, cascade = get_recognizer()

    # Get Camera DB object (None if not in DB — that's fine)
    camera_obj = _get_camera_obj(camera_index)

    # Open camera
    cap = _open_camera(camera_index)
    if cap is None:
        print(f'[CAMERA] Failed to open index {camera_index}')
        return

    # Warm-up: read frames until brightness is acceptable OR timeout (5s)
    # FIX: Old code discarded only 10 frames — camera exposure takes longer,
    # causing 30s black screen. Now we wait until frame is actually visible.
    print(f'[CAMERA] Warming up camera {camera_index}...')
    import time as _time
    warmup_start = _time.time()
    warmup_timeout = 25.0   # max seconds to wait
    warmup_min_brightness = 20  # minimum mean pixel value

    while True:
        try:
            ret, warmup_frame = cap.read()
            if not ret or warmup_frame is None:
                break
            gray_check = cv2.cvtColor(warmup_frame, cv2.COLOR_BGR2GRAY)
            if gray_check.mean() >= warmup_min_brightness:
                print(f'[CAMERA] Warm-up done in {_time.time()-warmup_start:.1f}s (brightness={gray_check.mean():.1f})')
                break
            if _time.time() - warmup_start > warmup_timeout:
                print(f'[CAMERA] Warm-up timeout after {warmup_timeout}s')
                break
        except Exception:
            break
    print(f'[CAMERA] Stream started on index {camera_index}')

    frame_count  = 0
    last_results = []   # cached detection boxes

    try:
        while True:
            ret, frame = cap.read()

            if not ret or frame is None:
                print('[CAMERA] Frame read failed — stopping')
                break

            frame = cv2.flip(frame, 1)
            frame_count += 1

            # ── Run recognition every Nth frame ──
            run_recog = (
                frame_count % PROCESS_EVERY_N == 0
                and recognizer is not None
                and cascade is not None
            )

            if run_recog:
                try:
                    small        = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
                    raw_results  = recognize_faces(
                        small, recognizer, label_map,
                        cascade, save_unknown=False
                    )
                    # ── Deduplicate: if same name appears more than once,
                    # keep only the highest-confidence detection.
                    # (Overlapping duplicate boxes are merged in recognize_faces;
                    # this catches non-overlapping duplicates of the same label.)
                    seen_names = {}
                    for entry in raw_results:
                        name, conf = entry[0], entry[1]
                        if name == 'Unknown':
                            # Keep all unknowns (different positions)
                            seen_names[f'Unknown_{entry[2]}_{entry[5]}'] = entry
                        else:
                            if name not in seen_names or conf > seen_names[name][1]:
                                seen_names[name] = entry
                    last_results = list(seen_names.values())
                except Exception as e:
                    print(f'[RECOG ERROR] {e}')
                    last_results = []

            # ── Draw boxes + attendance (only when identity is vote-stable) ──
            vote_active: set[tuple] = set()
            n_faces = len(last_results)

            # Clear single-face "who may be marked" lock only after sustained no detection
            # (one real person left the frame). Prevents LBPH A↔B flip from marking twice.
            if run_recog:
                slot = _single_face_slot[camera_index]
                if len(last_results) == 0:
                    slot['no_face_streak'] += 1
                    if slot['no_face_streak'] >= _SINGLE_SLOT_CLEAR_STREAK:
                        slot['locked_name'] = None
                        slot['no_face_streak'] = 0
                        _identity_queues.pop((camera_index, '_single'), None)
                else:
                    slot['no_face_streak'] = 0

            for (name, conf, top, right, bottom, left) in last_results:
                s = 2
                t, r, b, l = top*s, right*s, bottom*s, left*s
                color = (0, 255, 0) if name != 'Unknown' else (0, 0, 255)
                label = f'{name} ({conf}%)'

                cv2.rectangle(frame, (l, t), (r, b), color, 2)
                cv2.rectangle(frame, (l, b-28), (r, b), color, cv2.FILLED)
                cv2.putText(
                    frame, label, (l+4, b-8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (255, 255, 255), 1
                )

                # Process only on recognition frames
                if run_recog:
                    if name != 'Unknown':
                        vk = _vote_key(camera_index, left, top, right, bottom, n_faces)
                        vote_active.add(vk)
                        _identity_queues[vk].append(name)
                        stable = _consensus_name(list(_identity_queues[vk]))
                        if stable:
                            if vk == (camera_index, '_single'):
                                sslot = _single_face_slot[camera_index]
                                if (
                                    sslot['locked_name'] is not None
                                    and stable != sslot['locked_name']
                                ):
                                    continue
                            mark_attendance(stable)
                            _save_attendance_db(stable, camera_obj)
                            _log_to_db(
                                stable, conf, camera_obj,
                                frame, l, t, r-l, b-t
                            )
                            if vk == (camera_index, '_single'):
                                if sslot['locked_name'] is None:
                                    sslot['locked_name'] = stable
                    else:
                        # Unknown — scale coords back
                        ox = left * 2
                        oy = top  * 2
                        ow = (right - left) * 2
                        oh = (bottom - top) * 2
                        try:
                            _alert_unknown(
                                frame, ox, oy, ow, oh, camera_obj
                            )
                        except Exception:
                            pass
            if run_recog:
                _prune_stale_vote_keys(vote_active)

            # ── Encode to JPEG ──
            try:
                ret2, buffer = cv2.imencode(
                    '.jpg', frame,
                    [cv2.IMWRITE_JPEG_QUALITY, 80]
                )
                if not ret2:
                    continue
            except Exception:
                continue

            yield (
                b'--frame\r\n'
                b'Content-Type: image/jpeg\r\n\r\n'
                + buffer.tobytes()
                + b'\r\n'
            )

    except GeneratorExit:
        pass
    except Exception as e:
        print(f'[CAMERA STREAM ERROR] {e}')
    finally:
        release_camera(camera_index)
        print(f'[CAMERA] Released camera {camera_index}')