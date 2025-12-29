"""URL configuration for users service.

The `urlpatterns` list routes URLs to views.

Note: URLs are configured with /users/ prefix for future API gateway integration.
When deploying with an API gateway, route /users/* requests to this service.
"""

from django.contrib import admin
from django.urls import path
from django.views.generic import TemplateView

from users.api import api
from users.views import account_page, credits_history_page

# Base patterns (when accessed directly on port 8010)
base_patterns = [
    path('admin/', admin.site.urls),
    path('api/', api.urls),
    path('', TemplateView.as_view(template_name='home.html'), name='home'),
    path('account/', account_page, name='account-page'),
    path('account/credits/history/', credits_history_page, name='credits-history-page'),
]

# For API gateway deployment, use prefixed patterns
# Uncomment this and comment out the line below when deploying behind an API gateway:
# urlpatterns = [path('users/', include(base_patterns))]

# For standalone deployment (current setup)
urlpatterns = base_patterns
