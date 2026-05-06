from django.contrib import admin
from .models import (
    Student, Faculty, Lecture, Attendance,
    LectureAttendance, Camera, CameraLog,
    Alert, VideoUpload, FaceRegistrationSession
)

admin.site.register(Student)
admin.site.register(Faculty)
admin.site.register(Lecture)
admin.site.register(Attendance)
admin.site.register(LectureAttendance)
admin.site.register(Camera)
admin.site.register(CameraLog)
admin.site.register(Alert)
admin.site.register(VideoUpload)
admin.site.register(FaceRegistrationSession)