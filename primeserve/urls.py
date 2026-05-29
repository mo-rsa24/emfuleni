"""URL configuration for primeserve project.

Per-app urls live in `<app>/urls.py` and get mounted here.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('portal.urls')),
]

if settings.DEBUG:
    # Dev-only: serve uploaded evidence from MEDIA_ROOT. In prod (Slice 13)
    # this is Nginx's job — Django does not need to be in the file path.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
