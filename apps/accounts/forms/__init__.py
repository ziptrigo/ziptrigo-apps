from .admin import UserAdminChangeForm, UserAdminCreationForm
from .forgot_password import ForgotPasswordForm
from .login import LoginForm
from .profile import ProfileForm
from .register import RegisterForm
from .resend_confirmation import ResendConfirmationForm
from .reset_password import ResetPasswordForm

__all__ = [
    'ForgotPasswordForm',
    'LoginForm',
    'ProfileForm',
    'RegisterForm',
    'ResendConfirmationForm',
    'ResetPasswordForm',
    'UserAdminChangeForm',
    'UserAdminCreationForm',
]
