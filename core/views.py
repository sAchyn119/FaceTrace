import os, sys, json, shutil
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import StreamingHttpResponse, JsonResponse
from django.contrib import messages
from django.utils import timezone
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.db import OperationalError, ProgrammingError

from .models import Student, Attendance, VideoUpload
from .camera import generate_frames, release_camera, reload_model
from .video_processor import process_video
from attendance import get_today_attendance

# ── Safe import of new models ─────────────────────────────────────
# Catches BOTH ImportError (model missing) AND
# OperationalError (model exists in Python but migration not run yet)
_NEW_MODELS = False
try:
    from .models import Faculty, Lecture, Camera, CameraLog, Alert, FaceRegistrationSession
    # Verify the DB table actually has the expected columns
    # by doing a cheap probe query — catches missing-migration errors
    Alert.objects.filter(is_read=False).count()
    _NEW_MODELS = True
except (ImportError, OperationalError, ProgrammingError, Exception):
    _NEW_MODELS = False


def _safe_alerts(filter_kwargs=None, limit=5):
    """
    Safely fetch alerts — returns [] if new models aren't ready.
    Catches OperationalError so a missing migration never crashes a page.
    """
    if not _NEW_MODELS:
        return []
    try:
        qs = Alert.objects.order_by('-created_at')
        if filter_kwargs:
            qs = qs.filter(**filter_kwargs)
        return list(qs[:limit])
    except (OperationalError, ProgrammingError, Exception):
        return []


def _safe_unread_count():
    """Returns 0 if alert table doesn't exist yet."""
    if not _NEW_MODELS:
        return 0
    try:
        return Alert.objects.filter(is_read=False).count()
    except (OperationalError, ProgrammingError, Exception):
        return 0


# ══════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════

def login_view(request):
    if request.method == 'POST':
        role     = (request.POST.get('role') or '').strip().lower()
        username = request.POST.get('username', '').strip()
        employee_id = request.POST.get('employee_id', '').strip() or username
        password = request.POST.get('password', '').strip()
        roll_no  = request.POST.get('roll_no',  '').strip()

        # ── Student login by Roll No ──────────────────────────────
        if role == 'student':
            student = Student.objects.filter(roll_no__iexact=roll_no).first()
            if not student:
                messages.error(request, 'Roll number not found.')
                return render(request, 'login.html')

            # If student has a linked Django user, try password auth first
            if student.user and password:
                user = authenticate(
                    request,
                    username=student.user.username,
                    password=password,
                )
                if user:
                    login(request, user)
                    request.session['facetrace_role'] = 'student'
                    return redirect('student_dashboard')

            # Roll-number-only login — no Django user needed
            if student.user:
                student.user.backend = 'django.contrib.auth.backends.ModelBackend'
                login(request, student.user)
            else:
                from django.contrib.auth.models import User as DjangoUser
                fallback = DjangoUser.objects.filter(is_superuser=True).first()
                if fallback:
                    fallback.backend = 'django.contrib.auth.backends.ModelBackend'
                    login(request, fallback)
                else:
                    messages.error(request, 'No system user configured. Ask admin to set up your account.')
                    return render(request, 'login.html')

            request.session['facetrace_role'] = 'student'
            request.session['student_roll']   = roll_no  # for dashboard lookup
            return redirect('student_dashboard')

        # ── Faculty / HOD login ───────────────────────────────────
        if role in ('faculty', 'hod') and _NEW_MODELS:
            try:
                faculty = Faculty.objects.filter(
                    employee_id__iexact=employee_id,
                    role=role
                ).first()
                if faculty and faculty.user:
                    user = authenticate(
                        request,
                        username=faculty.user.username,
                        password=password,
                    )
                    if user:
                        login(request, user)
                        request.session['facetrace_role'] = role
                        return redirect(
                            'hod_dashboard' if role == 'hod'
                            else 'faculty_dashboard'
                        )
            except Exception:
                pass
            # Fallback: standard username/password
            user = authenticate(request, username=username, password=password)
            if user:
                login(request, user)
                request.session['facetrace_role'] = role
                return redirect(
                    'hod_dashboard' if role == 'hod'
                    else 'faculty_dashboard'
                )
            messages.error(request, 'Invalid Employee ID or password.')
            return render(request, 'login.html')

        # ── Admin login ───────────────────────────────────────────
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            request.session['facetrace_role'] = 'admin'
            return redirect('dashboard')
        messages.error(request, 'Invalid username or password.')

    return render(request, 'login.html')


def logout_view(request):
    release_camera()
    logout(request)
    return redirect('login')


# ══════════════════════════════════════════
# DASHBOARDS
# ══════════════════════════════════════════

@login_required
def dashboard(request):
    today        = timezone.now().date()
    total        = Student.objects.count()
    present      = Attendance.objects.filter(date=today).count()
    absent       = max(0, total - present)
    att_rate     = round(present / total * 100, 1) if total else 0
    attendance   = (Attendance.objects
                    .filter(date=today)
                    .select_related('student')
                    .order_by('-time'))
    videos       = VideoUpload.objects.order_by('-uploaded_at')[:5]
    total_videos = VideoUpload.objects.filter(processed=True).count()

    # FIX: use _safe_alerts() — never crashes even if migration missing
    alerts       = _safe_alerts(filter_kwargs={'is_read': False}, limit=5)
    unread_count = _safe_unread_count()

    return render(request, 'dashboard.html', {
        'total':        total,
        'present':      present,
        'absent':       absent,
        'att_rate':     att_rate,
        'attendance':   attendance,
        'today':        today,
        'videos':       videos,
        'total_videos': total_videos,
        'alerts':       alerts,
        'unread_count': unread_count,
    })


@login_required
def student_dashboard(request):
    today   = timezone.now().date()
    student = None

    try:
        student = Student.objects.filter(user=request.user).first()
    except Exception:
        pass

    if not student:
        # Try session roll number (roll-number-only login path)
        session_roll = request.session.get('student_roll', '')
        if session_roll:
            student = Student.objects.filter(roll_no__iexact=session_roll).first()

    if not student:
        student = (
            Student.objects.filter(
                name__iexact=request.user.get_full_name()
            ).first()
            or Student.objects.filter(
                roll_no__iexact=request.user.username
            ).first()
        )

    attendance_records = []
    present_days = total_days = 0
    att_rate     = 0
    today_status = 'Absent'

    if student:
        attendance_records = (Attendance.objects
                              .filter(student=student)
                              .order_by('-date')[:30])
        present_days = Attendance.objects.filter(
            student=student, status='present'
        ).count()
        days_since = max((today - student.created_at.date()).days + 1, 1)
        total_days = max(days_since, present_days)
        att_rate   = round(present_days / total_days * 100, 1) if total_days else 0
        if Attendance.objects.filter(
            student=student, date=today, status='present'
        ).exists():
            today_status = 'Present'

    return render(request, 'student_dashboard.html', {
        'student':            student,
        'attendance_records': attendance_records,
        'present_days':       present_days,
        'total_days':         total_days,
        'att_rate':           att_rate,
        'today_status':       today_status,
        'today':              today,
    })


@login_required
def faculty_dashboard(request):
    today    = timezone.now().date()
    total    = Student.objects.count()
    present  = Attendance.objects.filter(date=today).count()
    absent   = max(0, total - present)
    att_rate = round(present / total * 100, 1) if total else 0
    attendance = (Attendance.objects
                  .filter(date=today)
                  .select_related('student')
                  .order_by('-time'))
    branch_stats = _branch_stats(today)
    videos       = VideoUpload.objects.order_by('-uploaded_at')[:5]

    return render(request, 'faculty_dashboard.html', {
        'total':        total,
        'present':      present,
        'absent':       absent,
        'att_rate':     att_rate,
        'attendance':   attendance,
        'branch_stats': branch_stats,
        'today':        today,
        'videos':       videos,
        'unread_count': _safe_unread_count(),
    })


@login_required
def hod_dashboard(request):
    today    = timezone.now().date()
    total    = Student.objects.count()
    present  = Attendance.objects.filter(date=today).count()
    absent   = max(0, total - present)
    att_rate = round(present / total * 100, 1) if total else 0
    branch_stats = _branch_stats(today)

    # 7-day trend
    trend = []
    for i in range(6, -1, -1):
        d      = today - timedelta(days=i)
        d_pres = Attendance.objects.filter(date=d).count()
        d_rate = round(d_pres / total * 100, 1) if total else 0
        trend.append({'date': str(d), 'present': d_pres, 'rate': d_rate})

    total_videos = VideoUpload.objects.filter(processed=True).count()
    videos       = VideoUpload.objects.order_by('-uploaded_at')[:5]

    # FIX: safe alert fetch
    alerts       = _safe_alerts(
        filter_kwargs={'notify_hod': True, 'is_read': False},
        limit=10
    )
    unread_count = _safe_unread_count()

    return render(request, 'hod_dashboard.html', {
        'total':        total,
        'present':      present,
        'absent':       absent,
        'att_rate':     att_rate,
        'branch_stats': branch_stats,
        'trend':        trend,
        'total_videos': total_videos,
        'videos':       videos,
        'alerts':       alerts,
        'unread_count': unread_count,
        'today':        today,
    })


def _branch_stats(date):
    """Branch-wise attendance breakdown."""
    branches = (Student.objects
                .values_list('branch', flat=True)
                .distinct()
                .exclude(branch=''))
    stats = []
    for branch in branches:
        b_total   = Student.objects.filter(branch=branch).count()
        b_present = Attendance.objects.filter(
            date=date, student__branch=branch
        ).count()
        stats.append({
            'branch':  branch,
            'total':   b_total,
            'present': b_present,
            'absent':  b_total - b_present,
            'rate':    round(b_present / b_total * 100, 1) if b_total else 0,
        })
    return stats


# ══════════════════════════════════════════
# CAMERA
# ══════════════════════════════════════════

@login_required
def camera_view(request):
    return render(request, 'camera.html')


@login_required
def video_feed(request):
    """
    Streams MJPEG frames.
    Camera index passed via ?cam=0 query param (default 0).
    """
    try:
        cam_index = int(request.GET.get('cam', 0))
    except (ValueError, TypeError):
        cam_index = 0

    return StreamingHttpResponse(
        generate_frames(cam_index),
        content_type='multipart/x-mixed-replace; boundary=frame'
    )


@login_required
def stop_camera(request):
    release_camera()
    return JsonResponse({'status': 'stopped'})


# ══════════════════════════════════════════
# STUDENTS
# ══════════════════════════════════════════
@login_required
def delete_student(request, sid):
    student = get_object_or_404(Student, id=sid)
    if request.method == 'POST':
        face_dir = os.path.join(settings.BASE_DIR, 'known_faces', student.name)
        if os.path.isdir(face_dir):
            shutil.rmtree(face_dir, ignore_errors=True)
        student.delete()
        try:
            reload_model()
        except Exception:
            pass
        messages.success(request, f"✓ '{student.name}' deleted and model retrained.")
        return redirect('student_list')
    return redirect('student_list')

@login_required
@csrf_exempt
def capture_temp_photo(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=400)
    try:
        session_id = request.POST.get('session_id', '').strip()
        index      = request.POST.get('index', '0')
        photo      = request.FILES.get('photo')
        if not session_id or not photo:
            return JsonResponse({'error': 'Missing data'}, status=400)
        temp_dir = os.path.join(settings.BASE_DIR, 'temp_faces', session_id)
        os.makedirs(temp_dir, exist_ok=True)
        filepath = os.path.join(temp_dir, f"face_{int(index):03d}.jpg")
        with open(filepath, 'wb') as f:
            for chunk in photo.chunks():
                f.write(chunk)
        return JsonResponse({'status': 'ok'})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@csrf_exempt
def add_student_final(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=400)
    try:
        session_id = request.POST.get('session_id', '').strip()
        name       = request.POST.get('name',    '').strip()
        roll_no    = request.POST.get('roll_no', '').strip()
        branch     = request.POST.get('branch',  '').strip()
        year       = request.POST.get('year',    '').strip()
        email      = request.POST.get('email',   '').strip()
        phone      = request.POST.get('phone',   '').strip()
        photo      = request.FILES.get('photo')
        if not name or not roll_no:
            return JsonResponse({'error': 'Name and Roll No are required.'}, status=400)
        if Student.objects.filter(roll_no=roll_no).exists():
            return JsonResponse({'error': f"Roll No '{roll_no}' already exists."}, status=400)
        student = Student.objects.create(
            name=name, roll_no=roll_no, branch=branch,
            year=year, email=email, phone=phone,
        )
        if photo:
            try:
                student.photo = photo
                student.save(update_fields=['photo'])
            except Exception as e:
                print(f'[WARN] Profile photo save failed: {e}')
        temp_dir = os.path.join(settings.BASE_DIR, 'temp_faces', session_id)
        face_dir = os.path.join(settings.BASE_DIR, 'known_faces', name)
        os.makedirs(face_dir, exist_ok=True)
        moved = 0
        if os.path.isdir(temp_dir):
            for fname in sorted(os.listdir(temp_dir)):
                if fname.lower().endswith(('.jpg', '.jpeg', '.png')):
                    src  = os.path.join(temp_dir, fname)
                    dest = os.path.join(face_dir, f"{name}_{moved+1:03d}.jpg")
                    shutil.move(src, dest)
                    moved += 1
            shutil.rmtree(temp_dir, ignore_errors=True)
        student.face_image_count = moved
        student.face_registered  = moved >= 10
        student.face_folder      = face_dir
        student.save(update_fields=['face_image_count','face_registered','face_folder'])
        try:
            reload_model()
        except Exception as e:
            print(f'[WARN] Retrain failed: {e}')
        return JsonResponse({'status': 'ok', 'student': student.id, 'photos': moved})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

@login_required
def student_list(request):
    query    = request.GET.get('q', '').strip()
    students = Student.objects.all().order_by('name')
    if query:
        students = (
            Student.objects.filter(name__icontains=query)
            | Student.objects.filter(roll_no__icontains=query)
        ).order_by('name')
    return render(request, 'student_list.html', {
        'students': students,
        'query':    query,
    })


@login_required
def add_student(request):
    if request.method == 'POST':
        name    = request.POST.get('name',    '').strip()
        roll_no = request.POST.get('roll_no', '').strip()
        branch  = request.POST.get('branch',  '').strip()
        year    = request.POST.get('year',    '').strip()
        email   = request.POST.get('email',   '').strip()
        photo   = request.FILES.get('photo')

        # Validation
        if not name or not roll_no:
            messages.error(request, 'Name and Roll No are required!')
            return redirect('add_student')

        if Student.objects.filter(roll_no=roll_no).exists():
            messages.error(request, f"Roll No '{roll_no}' already exists!")
            return redirect('add_student')

        # Create student record
        student = Student.objects.create(
            name    = name,
            roll_no = roll_no,
            branch  = branch,
            year    = year,
            email   = email,
            photo   = photo if photo else None,
        )

        # Copy uploaded photo into known_faces/<name>/ for recognition
        if photo:
            try:
                src      = os.path.join(settings.MEDIA_ROOT, str(student.photo))
                ext      = os.path.splitext(str(student.photo))[1].lower() or '.jpg'
                dest_dir = os.path.join(settings.BASE_DIR, 'known_faces', name)
                os.makedirs(dest_dir, exist_ok=True)
                dest = os.path.join(dest_dir, f"{name}_001{ext}")
                shutil.copy2(src, dest)
                # Retrain immediately with the new photo
                reload_model()
            except Exception as e:
                print(f'[WARN] Photo copy/retrain failed: {e}')

        messages.success(
            request,
            f"✓ Student '{name}' added! "
            f"Now capture their face photos for better recognition accuracy."
        )
        # Redirect to face-capture page WITH student ID
        return redirect('capture_photos', sid=student.id)

    # GET — render form with recent students
    recent = Student.objects.order_by('-created_at')[:5]
    return render(request, 'capture_photos.html', {
        'recent_students': recent,
    })


# ══════════════════════════════════════════
# FACE REGISTRATION
# ══════════════════════════════════════════

@login_required
def register_face_start(request, sid):
    """Show the webcam capture UI for a student."""
    student = get_object_or_404(Student, id=sid)

    session = None
    if _NEW_MODELS:
        try:
            session = FaceRegistrationSession.objects.filter(
                student=student,
                status__in=['pending', 'capturing']
            ).first()
            if not session:
                session = FaceRegistrationSession.objects.create(
                    student=student, target_count=50
                )
        except (OperationalError, ProgrammingError, Exception):
            session = None

    return render(request, 'capture_photos.html', {
        'student': student,
        'session': session,
    })


@login_required
@csrf_exempt
def register_face_capture(request, sid):
    """
    AJAX endpoint — receives base64 image from browser webcam,
    saves it to known_faces/<name>/ folder.
    Called by the JavaScript in capture_face.html.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=400)

    student = get_object_or_404(Student, id=sid)

    try:
        import base64
        data    = json.loads(request.body)
        img_b64 = data.get('image', '')
        angle   = data.get('angle', 'front')

        if not img_b64:
            return JsonResponse({'error': 'No image data'}, status=400)

        # Ensure folder exists
        face_dir = os.path.join(settings.BASE_DIR, 'known_faces', student.name)
        os.makedirs(face_dir, exist_ok=True)

        # Count existing images for sequential file naming
        existing = len([
            f for f in os.listdir(face_dir)
            if f.lower().endswith(('.jpg', '.jpeg', '.png'))
        ])
        count    = existing + 1
        filename = f"{student.name}_{count:03d}_{angle}.jpg"
        filepath = os.path.join(face_dir, filename)

        # Decode and save
        img_data = base64.b64decode(img_b64.split(',')[-1])
        with open(filepath, 'wb') as f:
            f.write(img_data)

        target = 50
        done   = count >= target

        # Update FaceRegistrationSession if available
        if _NEW_MODELS:
            try:
                session = FaceRegistrationSession.objects.filter(
                    student=student,
                    status__in=['pending', 'capturing']
                ).first()
                if session:
                    session.photos_taken = count
                    session.status = 'capturing'
                    if done:
                        session.status       = 'done'
                        session.completed_at = timezone.now()
                    session.save()
            except (OperationalError, ProgrammingError, Exception):
                pass

        # Update student face fields
        try:
            student.face_image_count = count
            student.face_registered  = count >= 10
            student.face_folder      = face_dir
            student.save(update_fields=[
                'face_image_count', 'face_registered', 'face_folder'
            ])
        except Exception:
            pass

        # Retrain model when done (or at every 10 photos for incremental improvement)
        if done or count % 10 == 0:
            try:
                reload_model()
            except Exception as e:
                print(f'[WARN] Model reload failed: {e}')

        progress = round(count / target * 100)
        return JsonResponse({
            'status':   'done' if done else 'capturing',
            'count':    count,
            'target':   target,
            'progress': progress,
            'message':  (
                f'✓ {count} photos captured! Model trained and ready.'
                if done else
                f'{count}/{target} photos captured'
            ),
        })

    except Exception as e:
        print(f'[CAPTURE ERROR] {e}')
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def register_face_status(request, sid):
    """Returns current capture progress for a student."""
    student = get_object_or_404(Student, id=sid)

    if _NEW_MODELS:
        try:
            session = FaceRegistrationSession.objects.filter(
                student=student
            ).order_by('-started_at').first()
            if session:
                return JsonResponse({
                    'status':   session.status,
                    'count':    session.photos_taken,
                    'progress': session.progress_pct,
                })
        except (OperationalError, ProgrammingError, Exception):
            pass

    # Fallback: count files on disk
    face_dir = os.path.join(settings.BASE_DIR, 'known_faces', student.name)
    count = 0
    if os.path.isdir(face_dir):
        count = len([
            f for f in os.listdir(face_dir)
            if f.lower().endswith(('.jpg', '.jpeg', '.png'))
        ])

    return JsonResponse({
        'status':   'done' if count >= 50 else 'capturing' if count > 0 else 'pending',
        'count':    count,
        'progress': min(100, round(count / 50 * 100)),
    })


# ══════════════════════════════════════════
# ATTENDANCE
# ══════════════════════════════════════════

@login_required
def attendance_report(request):
    date_str = request.GET.get('date', str(timezone.now().date()))
    records  = (Attendance.objects
                .filter(date=date_str)
                .select_related('student'))
    students    = Student.objects.all()
    present_ids = records.values_list('student_id', flat=True)
    absent      = students.exclude(id__in=present_ids)

    present_count = records.count()
    absent_count  = absent.count()
    total_count   = present_count + absent_count
    att_rate      = round(present_count / total_count * 100, 1) if total_count else 0

    return render(request, 'attendance.html', {
        'records':       records,
        'absent':        absent,
        'date':          date_str,
        'present_count': present_count,
        'absent_count':  absent_count,
        'att_rate':      att_rate,
    })


@login_required
def mark_absent_manual(request, sid):
    """Faculty manually marks a student absent (cheating detection)."""
    if request.method == 'POST':
        student = get_object_or_404(Student, id=sid)
        today   = timezone.now().date()
        att     = Attendance.objects.filter(student=student, date=today).first()
        if att:
            att.status    = 'absent'
            att.marked_by = 'manual'
            att.save()
            messages.success(request, f"'{student.name}' marked absent manually.")
        else:
            messages.error(request, 'No attendance record found for today.')
    return redirect(request.META.get('HTTP_REFERER', 'attendance'))


# ══════════════════════════════════════════
# API
# ══════════════════════════════════════════

@login_required
def attendance_status(request):
    """Live poll — returns today's attendance list as JSON."""
    today = timezone.now().date()
    db_records = (Attendance.objects
                  .filter(date=today)
                  .select_related('student')
                  .order_by('time'))
    records = [
        {
            'Name': r.student.name,
            'Roll': r.student.roll_no,
            'Time': str(r.time),
            'Date': str(r.date),
        }
        for r in db_records
    ]
    if not records:
        # Fallback to CSV attendance log
        records = get_today_attendance()
    return JsonResponse({'attendance': records, 'count': len(records)})


@login_required
def api_alerts(request):
    """Returns unread alerts as JSON for real-time notification badge."""
    if not _NEW_MODELS:
        return JsonResponse({'alerts': [], 'count': 0})
    try:
        alerts = Alert.objects.filter(
            is_read=False
        ).order_by('-created_at')[:10]
        data = [
            {
                'id':       a.id,
                'type':     a.alert_type,
                'title':    a.title,
                'message':  a.message,
                'severity': a.severity,
                'student':  a.student.name if a.student else None,
                'time':     a.created_at.strftime('%H:%M'),
            }
            for a in alerts
        ]
        return JsonResponse({'alerts': data, 'count': len(data)})
    except (OperationalError, ProgrammingError, Exception) as e:
        return JsonResponse({'alerts': [], 'count': 0, 'error': str(e)})


@login_required
def api_alert_read(request, alert_id):
    """Mark an alert as read."""
    if not _NEW_MODELS:
        return JsonResponse({'status': 'ok'})
    try:
        alert = get_object_or_404(Alert, id=alert_id)
        alert.mark_read()
    except Exception:
        pass
    return JsonResponse({'status': 'ok'})


@login_required
def api_alert_resolve(request, alert_id):
    """Mark an alert as resolved + read."""
    if not _NEW_MODELS:
        return JsonResponse({'status': 'ok'})
    try:
        alert = get_object_or_404(Alert, id=alert_id)
        alert.resolve()
        alert.mark_read()
    except Exception:
        pass
    return JsonResponse({'status': 'ok'})


@csrf_exempt
def unknown_face_detected(request):
    """Called by camera.py when an unregistered face is detected."""
    if request.method == 'POST':
        if _NEW_MODELS:
            try:
                data     = json.loads(request.body)
                cam_name = data.get('camera', 'Unknown Camera')
                camera   = None
                try:
                    camera = Camera.objects.filter(
                        name__icontains=cam_name
                    ).first()
                except Exception:
                    pass
                Alert.objects.create(
                    alert_type   = 'unknown_face',
                    severity     = 'high',
                    title        = 'Unknown Face Detected',
                    message      = f'Unregistered face detected at {cam_name}.',
                    camera       = camera,
                    notify_admin = True,
                    notify_hod   = True,
                )
            except (OperationalError, ProgrammingError, Exception) as e:
                print(f'[UNKNOWN API ERROR] {e}')
        return JsonResponse({'status': 'ok'})
    return JsonResponse({'error': 'POST required'}, status=400)


# ══════════════════════════════════════════
# ALERTS PAGE
# ══════════════════════════════════════════

@login_required
def alert_list(request):
    if not _NEW_MODELS:
        return render(request, 'alert_list.html', {'alerts': [], 'unread': 0})
    try:
        alerts = Alert.objects.select_related(
            'student', 'camera'
        ).order_by('-created_at')
        unread = _safe_unread_count()
        return render(request, 'alert_list.html', {
            'alerts': alerts,
            'unread': unread,
        })
    except (OperationalError, ProgrammingError, Exception) as e:
        messages.error(request, f'Could not load alerts: {e}')
        return redirect('dashboard')


# ══════════════════════════════════════════
# VIDEO
# ══════════════════════════════════════════

@login_required
def video_upload(request):
    if request.method == 'POST':
        title = request.POST.get('title', 'Untitled').strip()
        video = request.FILES.get('video')
        if not video:
            messages.error(request, 'Please select a video file!')
            return redirect('video_upload')

        vid_obj    = VideoUpload.objects.create(title=title, video=video)
        video_path = os.path.join(settings.MEDIA_ROOT, str(vid_obj.video))
        output_dir = os.path.join(
            settings.MEDIA_ROOT, 'video_thumbs', str(vid_obj.id)
        )
        results             = process_video(video_path, output_dir)
        vid_obj.result_json = json.dumps(results)
        vid_obj.processed   = True
        vid_obj.save()
        return redirect('video_results', vid_id=vid_obj.id)

    return render(request, 'video_upload.html')


@login_required
def video_list(request):
    videos = VideoUpload.objects.order_by('-uploaded_at')
    return render(request, 'video_list.html', {'videos': videos})


@login_required
def video_results(request, vid_id):
    vid_obj  = get_object_or_404(VideoUpload, id=vid_id)
    results  = json.loads(vid_obj.result_json or '{}')
    selected = request.GET.get('person')
    media_url = settings.MEDIA_URL.rstrip('/')

    persons = []
    for name, appearances in results.items():
        thumb_url = (
            f"{media_url}/video_thumbs/{vid_id}/{appearances[0]['thumbnail']}"
            if appearances else None
        )
        persons.append({
            'name':      name,
            'count':     len(appearances),
            'thumb_url': thumb_url,
        })

    selected_data = None
    if selected and selected in results:
        selected_data = {
            'name': selected,
            'appearances': [
                {
                    **app,
                    'thumb_url': (
                        f"{media_url}/video_thumbs/{vid_id}/{app['thumbnail']}"
                    ),
                }
                for app in results[selected]
            ],
        }

    return render(request, 'video_results.html', {
        'video':         vid_obj,
        'persons':       persons,
        'selected':      selected,
        'selected_data': selected_data,
        'total_persons': len(persons),
    })

# ══════════════════════════════════════════
# LECTURE SCHEDULE (HOD only)
# ══════════════════════════════════════════

@login_required
def lecture_schedule(request):
    """HOD view: list all lectures and add new ones."""
    if request.session.get('facetrace_role') not in ('hod', 'admin'):
        messages.error(request, 'Only HOD/Admin can manage lecture schedules.')
        return redirect('dashboard')

    if not _NEW_MODELS:
        messages.error(request, 'Run migrations first.')
        return redirect('dashboard')

    error = None
    if request.method == 'POST':
        action = request.POST.get('action', 'add')

        if action == 'delete':
            lec_id = request.POST.get('lecture_id')
            try:
                Lecture.objects.filter(id=lec_id).delete()
                messages.success(request, 'Lecture deleted.')
            except Exception as e:
                messages.error(request, f'Delete failed: {e}')
            return redirect('lecture_schedule')

        # ── Add / Edit ────────────────────────────────────────────
        subject    = request.POST.get('subject', '').strip()
        faculty_id = request.POST.get('faculty_id', '').strip()
        start_time = request.POST.get('start_time', '').strip()
        end_time   = request.POST.get('end_time', '').strip()
        day        = request.POST.get('day', '').strip()
        date_str   = request.POST.get('date', '').strip()
        camera_id  = request.POST.get('camera_id', '').strip()
        branch     = request.POST.get('branch', '').strip()
        year       = request.POST.get('year', '').strip()
        grace      = request.POST.get('grace_minutes', '15').strip()
        lec_id     = request.POST.get('lecture_id', '').strip()

        if not subject or not start_time or not end_time:
            error = 'Subject, start time and end time are required.'
        else:
            try:
                faculty_obj = None
                if faculty_id:
                    faculty_obj = Faculty.objects.filter(id=faculty_id).first()

                camera_obj = None
                if camera_id:
                    from .models import Camera as CameraModel
                    camera_obj = CameraModel.objects.filter(id=camera_id).first()

                date_obj = None
                if date_str:
                    from datetime import date as date_type
                    date_obj = date_type.fromisoformat(date_str)

                grace_int = int(grace) if grace.isdigit() else 15

                if lec_id:
                    # Edit existing
                    lec = get_object_or_404(Lecture, id=lec_id)
                    lec.subject       = subject
                    lec.faculty       = faculty_obj
                    lec.start_time    = start_time
                    lec.end_time      = end_time
                    lec.day           = day or 'mon'
                    lec.date          = date_obj
                    lec.camera        = camera_obj
                    lec.branch        = branch
                    lec.year          = year
                    lec.grace_minutes = grace_int
                    lec.save()
                    messages.success(request, f'Lecture "{subject}" updated.')
                else:
                    Lecture.objects.create(
                        subject       = subject,
                        faculty       = faculty_obj,
                        start_time    = start_time,
                        end_time      = end_time,
                        day           = day or 'mon',
                        date          = date_obj,
                        camera        = camera_obj,
                        branch        = branch,
                        year          = year,
                        grace_minutes = grace_int,
                    )
                    messages.success(request, f'Lecture "{subject}" added.')
                return redirect('lecture_schedule')
            except Exception as e:
                error = f'Save failed: {e}'

    lectures  = Lecture.objects.select_related('faculty', 'camera').order_by('day', 'start_time')
    faculties = Faculty.objects.all().order_by('name')
    try:
        from .models import Camera as CameraModel
        cameras = CameraModel.objects.filter(is_active=True)
    except Exception:
        cameras = []

    return render(request, 'lecture_schedule.html', {
        'lectures':  lectures,
        'faculties': faculties,
        'cameras':   cameras,
        'error':     error,
        'day_choices': Lecture.DAY_CHOICES,
    })