from django.urls import path

from .views import (
    account_created_page,
    account_page,
    confirm_email_page,
    email_confirmation_success,
    forgot_password_page,
    login_page,
    logout_page,
    profile_update,
    register_page,
    resend_confirmation_page,
    reset_password_page,
)

app_name = 'accounts'

urlpatterns = [
    path('', account_page, name='account'),
    path('profile/', profile_update, name='profile-update'),
    path('login/', login_page, name='login'),
    path('register/', register_page, name='register'),
    path('logout/', logout_page, name='logout'),
    path('created/', account_created_page, name='created'),
    path('forgot-password/', forgot_password_page, name='forgot-password'),
    path('reset-password/<str:token>/', reset_password_page, name='reset-password'),
    path('confirm-email/<str:token>/', confirm_email_page, name='confirm-email'),
    path('resend-confirmation/', resend_confirmation_page, name='resend-confirmation'),
    path('email-confirmed/', email_confirmation_success, name='email-confirmed'),
]
