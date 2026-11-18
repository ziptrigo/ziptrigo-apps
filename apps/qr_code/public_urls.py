"""The shared QR code pages, mounted at the site root rather than under the app's `qr/` prefix.

`/q/<code>` is the address of a share link (`services.share.link_url`): it is sent to other people,
so it must stay short and never move. The paths are spelled without a trailing slash everywhere
(one is accepted). The code is `secrets.token_urlsafe(16)`: letters, digits, `-` and `_`,
case-sensitive. The prefix is in `settings.TIMEZONE_MIDDLEWARE_SKIP_PREFIXES` (`/q/`), so the pages
render in UTC and the public wording and are never terms-gated.
"""

from django.urls import re_path

from .views import report_qrcode, shared_image, shared_page

app_name = 'q'

_CODE = r'(?P<code>[A-Za-z0-9_-]+)'

urlpatterns = [
    re_path(rf'^q/{_CODE}/?$', shared_page, name='page'),
    re_path(rf'^q/{_CODE}/image/?$', shared_image, name='image'),
    re_path(rf'^q/{_CODE}/report/?$', report_qrcode, name='report'),
]
