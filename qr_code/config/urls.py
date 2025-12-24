"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from src.qr_code.admin import custom_admin_site
from src.qr_code.api.router import api

urlpatterns = [
    path('admin/', custom_admin_site.urls),
    path('api/', api.urls),  # Django Ninja API with built-in docs at /api/docs
    path('', include('src.qr_code.urls')),
]

# Serve media files (WhiteNoise automatically handles static files)
if settings.DEBUG:
    # mypy struggles with the return type of static(); this is fine at runtime.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)  # type: ignore[arg-type]
else:
    # Serve media files even when DEBUG=False for local testing
    # In production, use a proper web server (nginx, Apache, etc.) for media files
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)  # type: ignore[arg-type]
