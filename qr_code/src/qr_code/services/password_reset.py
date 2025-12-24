from dataclasses import dataclass

from asgiref.sync import sync_to_async
from django.conf import settings
from django.urls import reverse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from ninja_jwt.exceptions import TokenError
from ninja_jwt.settings import api_settings

from ..models.user import User
from ..tokens import PasswordResetToken
from .email_service import EmailBackendClass, asend_email, get_email_backend


@dataclass(slots=True)
class PasswordResetService:
    """High-level operations for password reset flow."""

    email_backend_classes: list[EmailBackendClass]

    async def request_reset(self, email: str):
        """Create a reset token for the user with the given email and send email.

        If the user does not exist, this method does nothing. This keeps behavior
        indistinguishable from the caller's perspective.
        """
        try:
            user = await sync_to_async(User.objects.get)(email=email)
        except User.DoesNotExist:
            return

        token = PasswordResetToken.for_user(user)
        reset_url = self._build_reset_url(str(token))
        subject, text_body, html_body = render_password_reset_email(
            user=user,
            reset_url=reset_url,
        )
        await asend_email(
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
    async def validate_token(token: str) -> User | None:
        """Validate a password reset token and return user if valid."""
        try:
            token_obj = PasswordResetToken(token)
            user_id = token_obj.get(api_settings.USER_ID_CLAIM)
            if user_id is None:
                return None
            return await sync_to_async(User.objects.get)(pk=user_id)
        except (TokenError, User.DoesNotExist):
            return None


def get_password_reset_service() -> PasswordResetService:
    return PasswordResetService(email_backend_classes=get_email_backend())


def render_password_reset_email(*, user: User, reset_url: str) -> tuple[str, str, str]:
    """Render email subject, text, and HTML body for a reset email using Jinja2.

    Template: ``src/qr_code/static/emails/password_reset.j2``.
    """

    template_path = settings.PROJECT_ROOT / 'src' / 'qr_code' / 'static' / 'emails'

    env = Environment(
        loader=FileSystemLoader(str(template_path)),
        autoescape=select_autoescape(['html', 'xml']),
    )
    template = env.get_template('password_reset.j2')

    rendered = template.render(
        user=user,
        reset_url=reset_url,
        ttl_hours=settings.PASSWORD_RESET_TOKEN_TTL_HOURS,
    )

    # The template is structured as three sections one after another:
    # subject (first line), then text body, then HTML body.
    lines = [line for line in rendered.splitlines() if not line.strip().startswith('{#')]

    # Subject: first non-empty line.
    non_empty = [line for line in lines if line.strip()]
    subject = non_empty[0] if non_empty else 'Reset your QR Code account password'

    # Heuristically split into text and HTML by blank line before first '<p>' or '<' tag.
    joined = '\n'.join(non_empty[1:])
    marker = '<p>'
    idx = joined.find(marker)
    if idx == -1:
        text_body = joined.strip()
        html_body = f'<pre>{text_body}</pre>' if text_body else ''
    else:
        text_body = joined[:idx].strip()
        html_body = joined[idx:].strip()

    return subject, text_body, html_body
