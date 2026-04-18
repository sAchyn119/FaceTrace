from django.db import models

class Student(models.Model):
    name       = models.CharField(max_length=100)
    roll_no    = models.CharField(max_length=20, unique=True)
    photo      = models.ImageField(upload_to='known_faces/')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.roll_no})"

class Attendance(models.Model):
    student    = models.ForeignKey(Student, on_delete=models.CASCADE)
    date       = models.DateField(auto_now_add=True)
    time       = models.TimeField(auto_now_add=True)
    status     = models.CharField(max_length=10, default='Present')

    class Meta:
        unique_together = ('student', 'date')

    def __str__(self):
        return f"{self.student.name} - {self.date}"

class VideoUpload(models.Model):
    title      = models.CharField(max_length=200)
    video      = models.FileField(upload_to='videos/')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    processed  = models.BooleanField(default=False)
    result_json = models.TextField(blank=True, default='{}')

    def __str__(self):
        return self.title