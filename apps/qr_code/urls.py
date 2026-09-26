from django.urls import path

from .views import (
    dashboard,
    qrcode_duplicate,
    qrcode_editor,
)

urlpatterns = [
    path('', dashboard, name='dashboard'),
    path('create/', qrcode_editor, name='qrcode-create'),
    path('edit/<uuid:qr_id>/', qrcode_editor, name='qrcode-edit'),
    path('duplicate/<uuid:qr_id>/', qrcode_duplicate, name='qrcode-duplicate'),
]
