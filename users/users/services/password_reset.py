"""Password reset service for user authentication."""

from dataclasses import dataclass

from django.conf import settings
from django.urls import reverse
from ninja_jwt.exceptions import TokenError
from ninja_jwt.settings import api_settings

from ..models import User
from ..tokens import PasswordResetToken
from .email_service import EmailBackendClass, get_email_backend, send_email


@dataclass(slots=True)
class PasswordResetService:
    """High-level operations for password reset flow."""

    email_backend_classes: list[EmailBackendClass]

    def request_reset(self, email: str):
        """Create a reset token for the user with the given email and send email.

        If the user does not exist, this method does nothing. This keeps behavior
        indistinguishable from the caller's perspective.
        """
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return

        token = PasswordResetToken.for_user(user)
        reset_url = self._build_reset_url(str(token))
        subject, text_body, html_body = render_password_reset_email(
            user=user,
            reset_url=reset_url,
        )
        send_email(
            to=user.email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            backend_classes=self.email_backend_classes,
        )

    def _build_reset_url(self, token: str) -> str:
        base = settings.BASE_URL.rstrip('/')
        path = reverse('reset-password-page', args=[token])
        return f'{base}{path}'

    @staticmethod
    def validate_token(token: str) -> User | None:
        """Validate a password reset token and return user if valid."""
        try:
            token_obj = PasswordResetToken(token)
            user_id = token_obj.get(api_settings.USER_ID_CLAIM)
            if user_id is None:
                return None
            return User.objects.get(pk=user_id)
        except (TokenError, User.DoesNotExist):
            return None


def get_password_reset_service() -> PasswordResetService:
    return PasswordResetService(email_backend_classes=get_email_backend())


def render_password_reset_email(*, user: User, reset_url: str) -> tuple[str, str, str]:
    """Render email subject, text, and HTML body for a reset email.

    Returns:
        Tuple of (subject, text_body, html_body).
    """
    subject = 'Reset your Ziptrigo Users account password'

    text_body = f'''Hi{' ' + user.name if user.name else ''},

You requested to reset your password. Click the link below to set a new password:

{reset_url}

This link will expire in {settings.PASSWORD_RESET_TOKEN_TTL_HOURS} hours.

If you did not request this, please ignore this email.

--
The Ziptrigo Users Team
'''

    html_body = f'''<html>
<head></head>
<body>
<p>Hi{' ' + user.name if user.name else ''},</p>
<p>You requested to reset your password. Click the link below to set a new password:</p>
<p><a href="{reset_url}">{reset_url}</a></p>
<p>This link will expire in {settings.PASSWORD_RESET_TOKEN_TTL_HOURS} hours.</p>
<p>If you did not request this, please ignore this email.</p>
<p>--<br>
The Ziptrigo Users Team</p>
</body>
</html>'''

    return subject, text_body, html_body
