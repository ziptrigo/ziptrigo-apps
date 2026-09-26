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
    reset_password_page,
)

urlpatterns = [
    path('', account_page, name='account-page'),
    path('profile/', profile_update, name='profile-update'),
    path('login/', login_page, name='login-page'),
    path('register/', register_page, name='register-page'),
    path('logout/', logout_page, name='logout-page'),
    path('created/', account_created_page, name='account-created-page'),
    path('forgot-password/', forgot_password_page, name='forgot-password-page'),
    path('reset-password/<str:token>/', reset_password_page, name='reset-password-page'),
    path('confirm-email/<str:token>/', confirm_email_page, name='confirm-email-page'),
    path('email-confirmed/', email_confirmation_success, name='email-confirmation-success'),
]
