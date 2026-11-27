from django.urls import path

from django.contrib.auth.views import LogoutView

from . import staff_views, views

app_name = 'lessons'

urlpatterns = [
    path('', views.lesson_upload, name='upload'),
    path('upload/', views.upload_screenshots, name='upload_screenshots'),
    path('quick-links/', views.quick_links, name='quick_links'),
    path('s/<str:token>/', views.student_gallery, name='student_gallery'),

    path('staff/', staff_views.staff_home, name='staff_home'),
    path('staff/login/', staff_views.StaffLoginView.as_view(), name='staff_login'),
    path('staff/logout/', LogoutView.as_view(next_page='lessons:upload'), name='staff_logout'),
    path('staff/trials/', staff_views.trials_page, name='staff_trials'),
    path('staff/trials/lookup/', staff_views.trial_lookup, name='staff_trial_lookup'),
    path('staff/trials/create/', staff_views.trials_create, name='staff_trials_create'),
    path('staff/trials/<int:pk>/update/', staff_views.trial_update, name='staff_trial_update'),
    path('staff/trials/<int:pk>/delete/', staff_views.trial_delete, name='staff_trial_delete'),
    path('staff/trials/<int:pk>/sent/', staff_views.trial_mark_sent, name='staff_trial_sent'),
    path('staff/feedback/', staff_views.feedback_page, name='staff_feedback'),
    path('staff/feedback/<int:pk>/', staff_views.feedback_save, name='staff_feedback_save'),
]
