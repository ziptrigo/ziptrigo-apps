from .forgot_password import forgot_password_page
from .login import login_page, logout_page
from .pages import (
    account_created_page,
    account_page,
    confirm_email_page,
    email_confirmation_success,
)
from .profile import profile_update
from .register import register_page
from .resend_confirmation import resend_confirmation_page
from .reset_password import reset_password_page

__all__ = [
    'account_created_page',
    'account_page',
    'confirm_email_page',
    'email_confirmation_success',
    'forgot_password_page',
    'login_page',
    'logout_page',
    'profile_update',
    'register_page',
    'resend_confirmation_page',
    'reset_password_page',
]
