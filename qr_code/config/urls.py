"""
URL configuration for qr_code service.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/

Note: URLs are configured with /qr-code/ prefix for future API gateway integration.
When deploying with an API gateway, route /qr-code/* requests to this service.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from ..qr_code.admin import custom_admin_site
from ..qr_code.api.router import api

# Base patterns (when accessed directly on port 8020)
base_patterns = [
    path('admin/', custom_admin_site.urls),
    path('api/', api.urls),  # Django Ninja API with built-in docs at /api/docs
    path('', include('src.qr_code.urls')),
]

# Serve media files (WhiteNoise automatically handles static files)
if settings.DEBUG:
    # mypy struggles with the return type of static(); this is fine at runtime.
    base_patterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)  # type: ignore[arg-type]
else:
    # Serve media files even when DEBUG=False for local testing
    # In production, use a proper web server (nginx, Apache, etc.) for media files
    base_patterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)  # type: ignore[arg-type]

# For API gateway deployment, use prefixed patterns
# Uncomment this and comment out the line below when deploying behind an API gateway:
# urlpatterns = [path('qr-code/', include(base_patterns))]

# For standalone deployment (current setup)
urlpatterns = base_patterns
