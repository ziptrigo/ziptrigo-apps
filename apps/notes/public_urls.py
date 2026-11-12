"""The shared note pages, mounted at the site root rather than under the app's `notes/` prefix.

`/n/<code>` is the address of a share link (`services.links.link_url`): it is sent to other people and
printed in QR codes, so it must stay short and never move. The paths are spelled without a trailing
slash everywhere (one is accepted). The code is `secrets.token_urlsafe(16)`: letters, digits, `-`
and `_`, case-sensitive. These prefixes are in `settings.TIMEZONE_MIDDLEWARE_SKIP_PREFIXES`
(`/n/`), so the pages render in UTC and the public wording and are never terms-gated.
"""

from django.urls import re_path

from . import views

app_name = 'n'

_CODE = r'(?P<code>[A-Za-z0-9_-]+)'

urlpatterns = [
    re_path(rf'^n/{_CODE}/?$', views.shared_page, name='page'),
    re_path(rf'^n/{_CODE}/raw/?$', views.shared_raw, name='raw'),
    re_path(rf'^n/{_CODE}/download/?$', views.shared_download, name='download'),
    re_path(rf'^n/{_CODE}/edit/?$', views.shared_edit, name='edit'),
    re_path(rf'^n/{_CODE}/preview/?$', views.shared_preview, name='preview'),
    re_path(rf'^n/{_CODE}/checklist/?$', views.shared_checklist, name='checklist'),
    re_path(rf'^n/{_CODE}/duplicate/?$', views.shared_duplicate, name='duplicate'),
    re_path(rf'^n/{_CODE}/report/?$', views.report_note, name='report'),
]
