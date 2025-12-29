"""URL configuration for users service.

The `urlpatterns` list routes URLs to views.

Note: URLs are configured with /users/ prefix for future API gateway integration.
When deploying with an API gateway, route /users/* requests to this service.
"""

from django.contrib import admin
from django.urls import path
from django.views.generic import TemplateView

from users.api import api
from users.views import (
    account_created_page,
    account_page,
    confirm_email_page,
    credits_history_page,
    email_confirmation_success,
    forgot_password_page,
    login_page,
    logout_page,
    register_page,
    reset_password_page,
)

# Base patterns (when accessed directly on port 8010)
base_patterns = [
    path('admin/', admin.site.urls),
    path('api/', api.urls),
    path('', TemplateView.as_view(template_name='home.html'), name='home'),
    path('login/', login_page, name='login-page'),
    path('register/', register_page, name='register-page'),
    path('logout/', logout_page, name='logout-page'),
    path('account-created/', account_created_page, name='account-created-page'),
    path('forgot-password/', forgot_password_page, name='forgot-password-page'),
    path('reset-password/<str:token>/', reset_password_page, name='reset-password-page'),
    path('confirm-email/<str:token>/', confirm_email_page, name='confirm-email-page'),
    path('email-confirmed/', email_confirmation_success, name='email-confirmation-success'),
    path('account/', account_page, name='account-page'),
    path('account/credits/history/', credits_history_page, name='credits-history-page'),
]

# For API gateway deployment, use prefixed patterns
# Uncomment this and comment out the line below when deploying behind an API gateway:
# urlpatterns = [path('users/', include(base_patterns))]

# For standalone deployment (current setup)
urlpatterns = base_patterns
