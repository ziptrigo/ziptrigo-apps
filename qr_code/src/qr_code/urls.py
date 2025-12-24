from django.urls import path

from .views import (
    account_created_page,
    account_page,
    confirm_email_page,
    credits_history_page,
    dashboard,
    email_confirmation_success,
    forgot_password_page,
    home_page,
    login_page,
    logout_page,
    qrcode_duplicate,
    qrcode_editor,
    register_page,
    reset_password_page,
)

# All API endpoints are now handled by Django Ninja in config/urls.py
# These are only page/template views
urlpatterns = [
    path('', home_page, name='home'),
    path('login/', login_page, name='login-page'),
    path('account/', account_page, name='account-page'),
    path('account/credits/history/', credits_history_page, name='credits-history-page'),
    path('account-created/', account_created_page, name='account-created'),
    path('forgot-password/', forgot_password_page, name='forgot-password-page'),
    path('reset-password/<str:token>/', reset_password_page, name='reset-password-page'),
    path('confirm-email/success/', email_confirmation_success, name='email-confirmation-success'),
    path('confirm-email/<str:token>/', confirm_email_page, name='confirm-email-page'),
    path('logout/', logout_page, name='logout-page'),
    path('register/', register_page, name='register-page'),
    path('dashboard/', dashboard, name='dashboard'),
    path('qrcodes/create/', qrcode_editor, name='qrcode-create'),
    path('qrcodes/edit/<uuid:qr_id>/', qrcode_editor, name='qrcode-edit'),
    path('qrcodes/duplicate/<uuid:qr_id>/', qrcode_duplicate, name='qrcode-duplicate'),
]
