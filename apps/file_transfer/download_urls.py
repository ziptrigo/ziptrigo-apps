"""The public download page's URLs, mounted at the site root rather than under the app's
`transfer/` prefix (spec section 4): `/t/<slug>/`. Short and memorable, like `/go/<code>` for QR
codes (`apps/qr_code/redirect_urls.py`) -- this is the link that goes out in emails. The anonymous
manage link (spec section 6) lives here too, for the same reason.
"""

from django.urls import path

from .views import download_file, download_page, download_zip, unlock, zip_status
from .views import manage as manage_views

app_name = 't'

urlpatterns = [
    path('t/<str:slug>/', download_page, name='download'),
    path('t/<str:slug>/unlock/', unlock, name='unlock'),
    path('t/<str:slug>/files/<uuid:file_id>/', download_file, name='download-file'),
    path('t/<str:slug>/zip/', download_zip, name='download-zip'),
    path('t/<str:slug>/zip/status/', zip_status, name='zip-status'),
    path('t/<str:slug>/manage/<str:token>/', manage_views.manage_page, name='manage'),
    path(
        't/<str:slug>/manage/<str:token>/disable/',
        manage_views.manage_disable,
        name='manage-disable',
    ),
]
