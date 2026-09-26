"""Short-link URLs, mounted at the site root rather than under the app's `qr/` prefix.

They're printed on physical QR codes, so they must stay short and never move. Both `/go/<code>`
(what `QRCode.get_redirect_url` produces) and `/go/<code>/` resolve.
"""

from django.urls import re_path

from .views import redirect_short_url

urlpatterns = [
    re_path(r'^go/(?P<short_code>[^/]+)/?$', redirect_short_url, name='qrcode-redirect'),
]
