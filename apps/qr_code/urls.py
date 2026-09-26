from django.urls import path

from .views import (
    dashboard,
    qrcode_create_submit,
    qrcode_delete,
    qrcode_duplicate,
    qrcode_edit_submit,
    qrcode_editor,
    qrcode_preview,
    qrcode_short_code,
)

app_name = 'qr_code'

urlpatterns = [
    path('', dashboard, name='dashboard'),
    path('create/', qrcode_editor, name='create'),
    path('create/submit/', qrcode_create_submit, name='create-submit'),
    path('preview/', qrcode_preview, name='preview'),
    path('short-code/', qrcode_short_code, name='short-code'),
    path('edit/<uuid:qr_id>/', qrcode_editor, name='edit'),
    path('edit/<uuid:qr_id>/submit/', qrcode_edit_submit, name='edit-submit'),
    path('duplicate/<uuid:qr_id>/', qrcode_duplicate, name='duplicate'),
    path('delete/<uuid:qr_id>/', qrcode_delete, name='delete'),
]
