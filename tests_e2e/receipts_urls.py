"""The real URLconf plus the fake bucket (see `receipts_e2e_settings.py`)."""

from django.urls import include, path

from . import receipts_fakes

urlpatterns = [
    path('__e2e__/s3/<path:key>', receipts_fakes.fake_bucket),
    path('', include('config.urls')),
]
