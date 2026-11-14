"""The public address of a shared event, mounted at the site root: `/w/<code>` (an optional
trailing slash). The code is 22 URL-safe characters, case-sensitive. The prefix is in
`settings.TIMEZONE_MIDDLEWARE_SKIP_PREFIXES` (`/w/`), so the page renders in the public wording and
is never terms-gated.
"""

from django.urls import re_path

from . import views

app_name = 'w'

urlpatterns = [
    re_path(r'^w/(?P<code>[A-Za-z0-9_-]+)/?$', views.event_public, name='event'),
]
