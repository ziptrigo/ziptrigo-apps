"""Short links, mounted at the site root rather than under the app's `links/` prefix.

They are printed and shared, so they must stay short and never move. `/s/<code>`, with or without
a trailing slash, follows the link; a trailing `+` (`/s/<code>+`) shows where it goes first.
"""

from django.urls import re_path

from .views import redirect_link

app_name = 's'

urlpatterns = [
    re_path(r'^s/(?P<code>[A-Za-z0-9_-]+)(?P<preview>\+)?/?$', redirect_link, name='redirect'),
]
