"""The "Sign in with Google" URLs (issue #91), mounted at `/auth/google/` rather than under the
app's `account/` prefix.

`/auth/google/callback` is registered with Google as an authorized redirect URI and must match it
exactly -- including having no trailing slash -- so both paths are spelled out here without one.
Like `/go/<code>` and `/t/<slug>/`, a second root-mounted URLconf gets its own namespace
(`google_auth:`): Django can't serve two URLconfs under the one `accounts:` namespace.
"""

from django.urls import path

from .views import google_callback, google_login

app_name = 'google_auth'

urlpatterns = [
    path('login', google_login, name='login'),
    path('callback', google_callback, name='callback'),
]
