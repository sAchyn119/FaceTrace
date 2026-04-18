from django.urls import path
from . import views

urlpatterns = [
    path('',                        views.dashboard,        name='dashboard'),
    path('login/',                  views.login_view,        name='login'),
    path('logout/',                 views.logout_view,       name='logout'),
    path('camera/',                 views.camera_view,       name='camera'),
    path('video-feed/',             views.video_feed,        name='video_feed'),
    path('stop-camera/',            views.stop_camera,       name='stop_camera'),
    path('add-student/',            views.add_student,       name='add_student'),
    path('attendance/',             views.attendance_report, name='attendance'),
    path('api/status/',             views.attendance_status, name='attendance_status'),
    path('video/upload/',           views.video_upload,      name='video_upload'),
    path('video/results/<int:vid_id>/', views.video_results, name='video_results'),
    path('video/list/',             views.video_list,        name='video_list'),
]