from django.urls import path

from . import views

app_name = 'lessons'

urlpatterns = [
    path('', views.lesson_upload, name='upload'),
    path('upload/', views.upload_screenshots, name='upload_screenshots'),
    path('s/<str:token>/', views.student_gallery, name='student_gallery'),
]
