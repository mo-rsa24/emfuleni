"""URL configuration for primeserve project.

Per-app urls live in `<app>/urls.py` and get mounted here.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('portal.urls')),
]
