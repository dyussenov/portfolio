from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = 'Результаты уроков'
admin.site.site_title = 'Результаты уроков'
admin.site.index_title = 'Управление'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('lessons.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
