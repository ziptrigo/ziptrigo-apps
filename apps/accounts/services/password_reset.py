"""Password reset service for user authentication.

A reset link is emailed to the account's address, so following it proves control of that address:
**a successful reset also confirms the account's email** (issue #125), whichever way the link was
requested -- forgot-password, or the "finish setting up your account" email an existing unconfirmed
account gets (`existing_account`). That is what lets someone who never confirmed an address finish
by choosing a password, and it replaces whatever password the account was created with.

The link is bound to the address it was sent to (`tokens.RESET_EMAIL_CLAIM`): once the account has
moved to another address, a link for the old one is refused outright (it could also reset the
password before this existed), and a link without the claim (sent before it was added) still
resets the password but doesn't confirm anything -- it can't say which address it went to.

It is also bound to the password the account had when it was sent (`tokens.PASSWORD_CLAIM`), so a
link works once: setting a password -- by this link, another, or the account page -- refuses every
link issued before it. A link without that claim (sent before it existed) is refused too.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from django.conf import settings
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from ninja_jwt.exceptions import TokenError
from ninja_jwt.settings import api_settings

from apps.core.services.email import EmailBackendClass, get_email_backend, send_email

from ..models import User
from ..tokens import RESET_EMAIL_CLAIM, PasswordResetToken, matches_password
from .email_confirmation import get_email_confirmation_service


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

        reset_url = self.reset_url_for(user)
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

    def reset_url_for(self, user: User) -> str:
        """A fresh absolute reset link for `user`, bound to their current address."""
        token = PasswordResetToken.for_user(user)
        return self._build_reset_url(str(token))

    def _build_reset_url(self, token: str) -> str:
        base = settings.BASE_URL.rstrip('/')
        path = reverse('accounts:reset-password', args=[token])
        return f'{base}{path}'

    @staticmethod
    def validate_token(token: str) -> User | None:
        """Validate a password reset token and return user if valid. A token for an address the
        account no longer has, or issued under a password it no longer has, is not valid (see the
        module docstring)."""
        try:
            token_obj = PasswordResetToken(token)
            user_id = token_obj.get(api_settings.USER_ID_CLAIM)
            if user_id is None:
                return None
            user = User.objects.get(pk=user_id)
        except TokenError, User.DoesNotExist:
            return None
        if not matches_password(token_obj, user):
            return None
        sent_to = token_obj.get(RESET_EMAIL_CLAIM)
        if sent_to is not None and sent_to != user.email:
            return None
        return user

    @staticmethod
    def reset_password(user: User, new_password: str, *, token: str) -> None:
        """Set `new_password` for `user`, whom `validate_token(token)` returned, and confirm their
        email when the token proves they hold it (see the module docstring). Shared by the reset
        page and `POST /api/auth/reset-password`, so the two can't drift.

        Changing the password also revokes every JWT and session issued under the old one
        (`tokens.password_fingerprint`; Django's session auth hash).
        """
        proves_email = False
        try:
            proves_email = PasswordResetToken(token).get(RESET_EMAIL_CLAIM) == user.email
        except TokenError:
            pass

        with transaction.atomic():
            user.set_password(new_password)
            user.save(update_fields=['password'])
            if proves_email and not user.email_confirmed:
                # Conditional on the address still being the one the link went to, so an email
                # change that lands between the page load and this write can't be confirmed by it.
                now = datetime.now(UTC)
                confirmed = User.objects.filter(
                    pk=user.pk, email=user.email, email_confirmed=False
                ).update(email_confirmed=True, email_confirmed_at=now)
                if confirmed:
                    user.email_confirmed = True
                    user.email_confirmed_at = now
                    # Any confirmation link still out for the address is moot now.
                    get_email_confirmation_service().invalidate_pending_for_email(user.email)


def get_password_reset_service() -> PasswordResetService:
    return PasswordResetService(email_backend_classes=get_email_backend())


def render_password_reset_email(*, user: User, reset_url: str) -> tuple[str, str, str]:
    """Render email subject, text, and HTML body for a reset email.

    Returns:
        Tuple of (subject, text_body, html_body).
    """
    subject = 'Reset your ZipTrigo account password'

    text_body = f"""Hi {user.name or user.email},

You requested to reset your password. Click the link below to set a new password:

{reset_url}

This link will expire in {settings.PASSWORD_RESET_TOKEN_TTL_HOURS} hours.

If you did not request this, please ignore this email.

--
The ZipTrigo Team
"""

    # Autoescaped: the greeting carries the account's free-text name, which (for an account that
    # was never confirmed) is whatever whoever created it typed.
    html_body = render_to_string(
        'accounts/emails/password_reset.html',
        {
            'greeting_name': user.name or user.email,
            'reset_url': reset_url,
            'ttl_hours': settings.PASSWORD_RESET_TOKEN_TTL_HOURS,
        },
    )

    return subject, text_body, html_body
