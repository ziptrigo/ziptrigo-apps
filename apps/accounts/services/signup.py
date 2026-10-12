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
too when it actually saves the row -- comparing the raw, un-normalized address here would let e.g.
`user@Example.COM` slip past this pre-check when `user@example.com` is already registered, only to
then hit the model's unique constraint. That case, and the ordinary race between this call's
pre-check and a concurrent signup for the same address, both funnel into the same
`EmailAlreadyRegistered`: `create_user` runs inside `transaction.atomic()`, and a
`django.db.utils.IntegrityError` from the constraint is caught and converted rather than left to
escape as a 500.
"""

from django.db import IntegrityError, transaction

from ..models import User
from .email_confirmation import get_email_confirmation_service


class EmailAlreadyRegistered(Exception):
    """Raised by `create_account` when `email` already belongs to an account. `email` is the
    normalized address, for the caller to hand to
    `apps.accounts.services.existing_account.handle_signup_for_existing_email`."""

    def __init__(self, email: str) -> None:
        super().__init__(email)
        self.email = email


def create_account(*, name: str, email: str, password: str) -> User:
    """Create a new user account and send its confirmation email.

    Raises:
        EmailAlreadyRegistered: `email` is already registered (including when it only differs
            from an existing account by domain case -- see module docstring).
    """
    email = User.objects.normalize_email(email)
    if User.objects.filter(email=email).exists():
        raise EmailAlreadyRegistered(email)

    try:
        with transaction.atomic():
            user = User.objects.create_user(email=email, password=password, name=name)
    except IntegrityError:
        raise EmailAlreadyRegistered(email) from None

    get_email_confirmation_service().send_confirmation_email(user)
    return user
