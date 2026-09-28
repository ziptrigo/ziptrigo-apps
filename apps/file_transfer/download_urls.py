"""The public download page's URLs, mounted at the site root rather than under the app's
`transfer/` prefix (spec section 4): `/t/<slug>/`. Short and memorable, like `/go/<code>` for QR
codes (`apps/qr_code/redirect_urls.py`) -- this is the link that goes out in emails.
"""

from django.urls import path

from .views import download_file, download_page, unlock

app_name = 't'

urlpatterns = [
    path('t/<str:slug>/', download_page, name='download'),
    path('t/<str:slug>/unlock/', unlock, name='unlock'),
    path('t/<str:slug>/files/<uuid:file_id>/', download_file, name='download-file'),
]
