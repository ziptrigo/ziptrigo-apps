"""Email confirmation service for user email verification."""

from dataclasses import dataclass
from datetime import UTC, datetime

from django.conf import settings
from django.urls import reverse
from ninja_jwt.exceptions import TokenError
from ninja_jwt.settings import api_settings

from apps.core.services.email import EmailBackendClass, get_email_backend, send_email

from ..models import User
from ..tokens import EmailConfirmationToken


@dataclass(slots=True)
class EmailConfirmationService:
    """High-level operations for email confirmation flow."""

    email_backend_classes: list[EmailBackendClass]

    def send_confirmation_email(self, user: User):
        """Create a confirmation token for the user and send confirmation email."""
        token = EmailConfirmationToken.for_user(user)
        confirmation_url = self._build_confirmation_url(str(token))
        subject, text_body, html_body = render_email_confirmation_email(
            user=user,
            confirmation_url=confirmation_url,
        )
        send_email(
            to=user.email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            backend_classes=self.email_backend_classes,
        )

    def _build_confirmation_url(self, token: str) -> str:
        base = settings.BASE_URL.rstrip('/')
        path = reverse('accounts:confirm-email', args=[token])
        return f'{base}{path}'

    @staticmethod
    def validate_token(token: str) -> User | None:
        """Validate a confirmation token and return user if valid."""
        try:
            token_obj = EmailConfirmationToken(token)
            user_id = token_obj.get(api_settings.USER_ID_CLAIM)
            if user_id is None:
                return None
            return User.objects.get(pk=user_id)
        except TokenError, User.DoesNotExist:
            return None

    @staticmethod
    def confirm_email(user: User):
        """Mark the user's email as confirmed."""
        user.email_confirmed = True
        user.email_confirmed_at = datetime.now(UTC)
        user.save(update_fields=['email_confirmed', 'email_confirmed_at'])


def get_email_confirmation_service() -> EmailConfirmationService:
    return EmailConfirmationService(email_backend_classes=get_email_backend())


def render_email_confirmation_email(*, user: User, confirmation_url: str) -> tuple[str, str, str]:
    """Render email subject, text, and HTML body for a confirmation email.

    Returns:
        Tuple of (subject, text_body, html_body).
    """
    subject = 'Confirm your ZipTrigo account email'

    text_body = f"""Hi {user.name or user.email},

Thank you for creating an account! Please confirm your email address by clicking the link below:

{confirmation_url}

This link will expire in {settings.EMAIL_CONFIRMATION_TOKEN_TTL_HOURS} hours.

If you did not create this account, please ignore this email.

--
The ZipTrigo Team
"""

    html_body = f'''<html>
<head></head>
<body>
<p>Hi {user.name or user.email},</p>
<p>Thank you for creating an account! Please confirm your email address by clicking the link below:</p>
<p><a href="{confirmation_url}">{confirmation_url}</a></p>
<p>This link will expire in {settings.EMAIL_CONFIRMATION_TOKEN_TTL_HOURS} hours.</p>
<p>If you did not create this account, please ignore this email.</p>
<p>--<br>
The ZipTrigo Team</p>
</body>
</html>'''

    return subject, text_body, html_body
