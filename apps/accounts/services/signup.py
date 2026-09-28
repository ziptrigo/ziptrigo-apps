"""Shared signup logic (issue #52) for the register session view (`apps.accounts.views.register`)
and `POST /api/auth/signup` (`apps.accounts.routers.auth.signup`) -- CLAUDE.md's "HTMX form views"
convention: put shared create/update logic in the app's `services/` so both surfaces enforce the
same rules, rather than duplicating the "does this email already exist" check and the user-creation
call in each caller.

Password strength is validated by each caller before this runs -- `RegisterForm.clean_password`
for the web form, `SignupRequest`'s pydantic validator for the API -- since that's a per-field rule
each surface's own validation layer is best placed to report. This module only re-checks the one
rule a form field can't guard against by itself: whether `email` is already taken. The email is
normalized (`User.objects.normalize_email`, which lower-cases only the domain) before that check,
since `UserManager.create_user` normalizes it too when it actually saves the row -- comparing the
raw, un-normalized address here would let e.g. `user@Example.COM` slip past this pre-check (and
`RegisterForm.clean_email`'s own uniqueness check) when `user@example.com` is already registered,
only to then hit the model's unique constraint. That case, and the ordinary race between this
call's pre-check and a concurrent signup for the same address, both funnel into the same
`EmailAlreadyRegistered`: `create_user` runs inside `transaction.atomic()`, and a
`django.db.utils.IntegrityError` from the constraint is caught and converted rather than left to
escape as a 500.
"""

from django.db import IntegrityError, transaction

from ..models import User
from .email_confirmation import get_email_confirmation_service


class EmailAlreadyRegistered(Exception):
    """Raised by `create_account` when `email` already belongs to an account."""


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
