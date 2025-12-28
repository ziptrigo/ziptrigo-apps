from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from asgiref.sync import sync_to_async
from django.conf import settings
from django.urls import reverse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from ninja_jwt.exceptions import TokenError
from ninja_jwt.settings import api_settings

from ..models.user import User
from ..tokens import EmailConfirmationToken
from .email_service import EmailBackendClass, asend_email, get_email_backend


@dataclass(slots=True)
class EmailConfirmationService:
    """High-level operations for email confirmation flow."""

    email_backend_classes: list[EmailBackendClass]

    async def send_confirmation_email(self, user: User):
        """Create a confirmation token for the user and send confirmation email."""
        token = EmailConfirmationToken.for_user(user)
        confirmation_url = self._build_confirmation_url(str(token))
        subject, text_body, html_body = render_email_confirmation_email(
            user=user,
            confirmation_url=confirmation_url,
        )
        await asend_email(
            to=user.email,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            backend_classes=self.email_backend_classes,
        )

    def _build_confirmation_url(self, token: str) -> str:
        base = settings.BASE_URL.rstrip('/')
        path = reverse('confirm-email-page', args=[token])
        return f'{base}{path}'

    @staticmethod
    async def validate_token(token: str) -> User | None:
        """Validate a confirmation token and return user if valid."""
        try:
            token_obj = EmailConfirmationToken(token)
            user_id = token_obj.get(api_settings.USER_ID_CLAIM)
            if user_id is None:
                return None
            return await sync_to_async(User.objects.get)(pk=user_id)
        except (TokenError, User.DoesNotExist):
            return None

    @staticmethod
    async def confirm_email(user: User):
        """Mark the user's email as confirmed."""
        user.email_confirmed = True
        user.email_confirmed_at = datetime.now(UTC)
        await sync_to_async(user.save)(update_fields=['email_confirmed', 'email_confirmed_at'])


def get_email_confirmation_service() -> EmailConfirmationService:
    return EmailConfirmationService(email_backend_classes=get_email_backend())


def render_email_confirmation_email(*, user: User, confirmation_url: str) -> tuple[str, str, str]:
    """Render email subject, text, and HTML body for a confirmation email using Jinja2.

    Template: ``src/qr_code/static/emails/email_validation.j2``.
    """

    PROJECT_ROOT = Path(settings.PROJECT_ROOT)
    template_path = PROJECT_ROOT / 'src' / 'qr_code' / 'static' / 'emails'

    env = Environment(
        loader=FileSystemLoader(str(template_path)),
        autoescape=select_autoescape(['html', 'xml']),
    )
    template = env.get_template('email_validation.j2')

    rendered = template.render(
        user=user,
        confirmation_url=confirmation_url,
        ttl_hours=settings.EMAIL_CONFIRMATION_TOKEN_TTL_HOURS,
    )

    # The template is structured as three sections one after another:
    # subject (first line), then text body, then HTML body.
    lines = [line for line in rendered.splitlines() if not line.strip().startswith('{#')]

    # Subject: first non-empty line.
    non_empty = [line for line in lines if line.strip()]
    subject = non_empty[0] if non_empty else 'Confirm your QR Code account email'

    # Heuristically split into text and HTML by blank line before first '<p>' or '<' tag.
    joined = '\\n'.join(non_empty[1:])
    marker = '<p>'
    idx = joined.find(marker)
    if idx == -1:
        text_body = joined.strip()
        html_body = f'<pre>{text_body}</pre>' if text_body else ''
    else:
        text_body = joined[:idx].strip()
        html_body = joined[idx:].strip()

    return subject, text_body, html_body
