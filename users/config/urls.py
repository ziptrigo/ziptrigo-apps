"""URL configuration for users service.

The `urlpatterns` list routes URLs to views.

Note: URLs are configured with /users/ prefix for future API gateway integration.
When deploying with an API gateway, route /users/* requests to this service.
"""

from django.contrib import admin
from django.urls import path
from django.views.generic import TemplateView
from src.users.api import api

# Base patterns (when accessed directly on port 8010)
base_patterns = [
    path('admin/', admin.site.urls),
    path('api/', api.urls),
    path('', TemplateView.as_view(template_name='hello.html'), name='hello'),
]

# For API gateway deployment, use prefixed patterns
# Uncomment this and comment out the line below when deploying behind an API gateway:
# urlpatterns = [path('users/', include(base_patterns))]

# For standalone deployment (current setup)
urlpatterns = base_patterns
