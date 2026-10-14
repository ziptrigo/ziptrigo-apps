"""Signup logic (issue #52) for the register session view (`apps.accounts.views.register`), the
only way to create an account since issue #117 removed `POST /api/auth/signup`. Kept as a service
per CLAUDE.md's "HTMX form views" convention, so the view holds no account-creation rules itself.

Password strength is validated by the caller before this runs (`RegisterForm.clean_password`),
since that's a per-field rule the form is best placed to report. This module only re-checks the one
rule a form field can't guard against by itself: whether `email` is already taken. It reports that
to the caller as `EmailAlreadyRegistered`, not to the user: the register view answers the same
either way and emails the address's owner instead (issue #125,
`apps.accounts.services.existing_account`). The email is normalized (`User.objects.normalize_email`,
which lower-cases only the domain) before that check, since `UserManager.create_user` normalizes it
too when it actually saves the row. The check itself ignores case altogether (`email__iexact`), so
`Alice@gmail.com` is the existing `alice@gmail.com` account too, not a second row the database's
case-sensitive unique constraint would let through. The ordinary race between this call's
pre-check and a concurrent signup for the same address, both funnel into the same
`EmailAlreadyRegistered`: `create_user` runs inside `transaction.atomic()`, and a
`django.db.utils.IntegrityError` from the constraint is caught and converted rather than left to
escape as a 500.
"""

from django.db import IntegrityError, transaction

from apps.core.services.queue import enqueue_after_commit

from ..models import User
from .email_confirmation import send_signup_confirmation_email


class EmailAlreadyRegistered(Exception):
    """Raised by `create_account` when `email` already belongs to an account. `email` is the
    normalized address, for the caller to hand to
    `apps.accounts.services.existing_account.handle_signup_for_existing_email`."""

    def __init__(self, email: str) -> None:
        super().__init__(email)
        self.email = email


def create_account(*, name: str, email: str, password: str) -> User:
    """Create a new user account and queue its confirmation email (a `django.tasks` task, so the
    request doesn't wait for the mail server -- see `send_signup_confirmation_email`).

    Raises:
        EmailAlreadyRegistered: `email` is already registered (including when it only differs
            from an existing account by domain case -- see module docstring).
    """
    email = User.objects.normalize_email(email)
    # Case-insensitive: `Alice@gmail.com` next to `alice@gmail.com` would be two rows that
    # Google sign-in (which matches `iexact`) then refuses as a conflict forever.
    if User.objects.filter(email__iexact=email).exists():
        raise EmailAlreadyRegistered(email)

    try:
        with transaction.atomic():
            user = User.objects.create_user(email=email, password=password, name=name)
    except IntegrityError:
        raise EmailAlreadyRegistered(email) from None

    enqueue_after_commit(send_signup_confirmation_email, str(user.pk), user.email)
    return user
