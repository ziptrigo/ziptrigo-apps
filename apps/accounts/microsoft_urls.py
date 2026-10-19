"""The "Sign in with Microsoft" URLs (issue #156), mounted at `/auth/microsoft/` like Google's.

`/auth/microsoft/callback` is registered with Microsoft as a redirect URI and must match it
exactly -- including having no trailing slash -- so both paths are spelled out here without one.
Its own namespace (`microsoft_auth:`), for the same reason as `google_auth:`.
"""

from django.urls import path

from .views import microsoft_callback, microsoft_login

app_name = 'microsoft_auth'

urlpatterns = [
    path('login', microsoft_login, name='login'),
    path('callback', microsoft_callback, name='callback'),
]
