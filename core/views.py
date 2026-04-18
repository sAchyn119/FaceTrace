import os, sys, json, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import StreamingHttpResponse, JsonResponse
from django.contrib import messages
from django.utils import timezone
from django.conf import settings

from .models import Student, Attendance, VideoUpload
from .camera import generate_frames, release_camera, reload_model
from .video_processor import process_video
from attendance import get_today_attendance

# ── Auth ──────────────────────────────────────────────

def login_view(request):
    if request.method == "POST":
        user = authenticate(request,
                            username=request.POST['username'],
                            password=request.POST['password'])
        if user:
            login(request, user)
            return redirect('dashboard')
        messages.error(request, "Invalid username or password")
    return render(request, 'login.html')

def logout_view(request):
    release_camera()
    logout(request)
    return redirect('login')

# ── Dashboard ─────────────────────────────────────────

@login_required
def dashboard(request):
    today        = timezone.now().date()
    total        = Student.objects.count()
    present      = Attendance.objects.filter(date=today).count()
    absent       = total - present
    attendance   = Attendance.objects.filter(date=today).select_related('student')
    videos       = VideoUpload.objects.order_by('-uploaded_at')[:5]
    total_videos = VideoUpload.objects.filter(processed=True).count()
    return render(request, 'dashboard.html', {
        'total':        total,
        'present':      present,
        'absent':       absent,
        'attendance':   attendance,
        'today':        today,
        'videos':       videos,
        'total_videos': total_videos,
    })

# ── Camera ────────────────────────────────────────────

@login_required
def camera_view(request):
    return render(request, 'camera.html')

def video_feed(request):
    return StreamingHttpResponse(
        generate_frames(),
        content_type='multipart/x-mixed-replace; boundary=frame'
    )

@login_required
def stop_camera(request):
    release_camera()
    return JsonResponse({'status': 'stopped'})

@login_required
def attendance_status(request):
    records = get_today_attendance()
    return JsonResponse({'attendance': records, 'count': len(records)})

# ── Students ──────────────────────────────────────────

@login_required
def add_student(request):
    if request.method == "POST":
        name    = request.POST['name']
        roll_no = request.POST['roll_no']
        photo   = request.FILES.get('photo')

        if Student.objects.filter(roll_no=roll_no).exists():
            messages.error(request, f"Roll No {roll_no} already exists!")
            return redirect('add_student')

        student = Student.objects.create(name=name, roll_no=roll_no, photo=photo)

        # Copy photo to known_faces/ with person name
        src  = os.path.join(settings.MEDIA_ROOT, str(student.photo))
        ext  = os.path.splitext(str(student.photo))[1]
        dest = os.path.join(settings.BASE_DIR, 'known_faces', f"{name}{ext}")
        shutil.copy(src, dest)

        reload_model()
        messages.success(request, f"Student {name} added and model retrained!")
        return redirect('dashboard')

    return render(request, 'add_student.html')

# ── Attendance Report ─────────────────────────────────

@login_required
def attendance_report(request):
    date_str = request.GET.get('date', str(timezone.now().date()))
    records  = Attendance.objects.filter(date=date_str).select_related('student')
    students = Student.objects.all()
    present_ids = records.values_list('student_id', flat=True)
    absent   = students.exclude(id__in=present_ids)
    return render(request, 'attendance.html', {
        'records': records, 'absent': absent, 'date': date_str,
        'present_count': records.count(), 'absent_count': absent.count()
    })

# ── Video Upload & Processing ─────────────────────────

@login_required
def video_upload(request):
    if request.method == "POST":
        title = request.POST.get('title', 'Untitled Video')
        video = request.FILES.get('video')

        if not video:
            messages.error(request, "Please select a video file!")
            return redirect('video_upload')

        # Save video record
        vid_obj = VideoUpload.objects.create(title=title, video=video)

        # Process video
        video_path = os.path.join(settings.MEDIA_ROOT, str(vid_obj.video))
        output_dir = os.path.join(settings.MEDIA_ROOT, 'video_thumbs', str(vid_obj.id))

        results = process_video(video_path, output_dir)

        # Save results as JSON
        vid_obj.result_json = json.dumps(results)
        vid_obj.processed   = True
        vid_obj.save()

        return redirect('video_results', vid_id=vid_obj.id)

    return render(request, 'video_upload.html')

@login_required
def video_results(request, vid_id):
    vid_obj  = get_object_or_404(VideoUpload, id=vid_id)
    results  = json.loads(vid_obj.result_json)
    selected = request.GET.get('person', None)

    # Build person list with first thumbnail
    persons = []
    for name, appearances in results.items():
        persons.append({
            'name':       name,
            'count':      len(appearances),
            'thumbnail':  appearances[0]['thumbnail'] if appearances else None,
            'thumb_url':  f"/media/video_thumbs/{vid_id}/{appearances[0]['thumbnail']}" if appearances else None,
        })

    # Selected person detail
    selected_data = None
    if selected and selected in results:
        selected_data = {
            'name':        selected,
            'appearances': results[selected]
        }
        # Add full URL to each thumbnail
        for app in selected_data['appearances']:
            app['thumb_url'] = f"/media/video_thumbs/{vid_id}/{app['thumbnail']}"

    return render(request, 'video_results.html', {
        'video':         vid_obj,
        'persons':       persons,
        'selected':      selected,
        'selected_data': selected_data,
        'total_persons': len(persons),
    })

@login_required
def video_list(request):
    videos = VideoUpload.objects.order_by('-uploaded_at')
    return render(request, 'video_list.html', {'videos': videos})