from django.urls import path

from .views import (
    dashboard,
    qrcode_duplicate,
    qrcode_editor,
)

# Auth and Account pages are now handled by user-service
urlpatterns = [
    path('dashboard/', dashboard, name='dashboard'),
    path('qrcodes/create/', qrcode_editor, name='qrcode-create'),
    path('qrcodes/edit/<uuid:qr_id>/', qrcode_editor, name='qrcode-edit'),
    path('qrcodes/duplicate/<uuid:qr_id>/', qrcode_duplicate, name='qrcode-duplicate'),
]
