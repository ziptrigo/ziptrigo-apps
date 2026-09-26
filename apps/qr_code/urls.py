from django.urls import path

from .views import (
    dashboard,
    qrcode_create_submit,
    qrcode_delete,
    qrcode_duplicate,
    qrcode_edit_submit,
    qrcode_editor,
    qrcode_preview,
)

urlpatterns = [
    path('', dashboard, name='dashboard'),
    path('create/', qrcode_editor, name='qrcode-create'),
    path('create/submit/', qrcode_create_submit, name='qrcode-create-submit'),
    path('preview/', qrcode_preview, name='qrcode-preview'),
    path('edit/<uuid:qr_id>/', qrcode_editor, name='qrcode-edit'),
    path('edit/<uuid:qr_id>/submit/', qrcode_edit_submit, name='qrcode-edit-submit'),
    path('duplicate/<uuid:qr_id>/', qrcode_duplicate, name='qrcode-duplicate'),
    path('delete/<uuid:qr_id>/', qrcode_delete, name='qrcode-delete'),
]
