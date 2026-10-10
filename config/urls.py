"""
URL configuration for the ZipTrigo site.

Each app owns its URLs and is mounted under its own prefix here. See `CLAUDE.md` for the full URL
map.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from apps.core.admin_site import custom_admin_site

from .api import api

urlpatterns = [
    path('admin/', custom_admin_site.urls),
    path('api/', api.urls),  # Django Ninja API with built-in docs at /api/docs
    path('account/', include('apps.accounts.urls')),
    path('billing/', include('apps.billing.urls')),
    path('qr/', include('apps.qr_code.urls')),
    path('transfer/', include('apps.file_transfer.urls')),
    path('feedback/', include('apps.feedback.urls')),
    # QR code short links live at the root (`/go/<code>`), outside the `qr/` prefix: they're
    # printed on physical QR codes, so they must stay short and never move.
    path('', include('apps.qr_code.redirect_urls')),
    # File transfer download links live at the root (`/t/<slug>/`) for the same reason: they go
    # out in emails and must stay short.
    path('', include('apps.file_transfer.download_urls')),
    path('', include('apps.core.urls')),
]

# Serve media files. WhiteNoise handles static files; in production, media files should come from
# object storage or a proper web server instead.
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
