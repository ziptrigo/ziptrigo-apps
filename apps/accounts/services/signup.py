"""Shared signup logic (issue #52) for the register session view (`apps.accounts.views.register`)
and `POST /api/auth/signup` (`apps.accounts.routers.auth.signup`) -- CLAUDE.md's "HTMX form views"
convention: put shared create/update logic in the app's `services/` so both surfaces enforce the
same rules, rather than duplicating the "does this email already exist" check and the user-creation
call in each caller.

Password strength is validated by each caller before this runs -- `RegisterForm.clean_password`
for the web form, `SignupRequest`'s pydantic validator for the API -- since that's a per-field rule
each surface's own validation layer is best placed to report. This module only re-checks the one
rule a form field can't guard against by itself: whether `email` is already taken, which can still
race between a caller's own validation and this call (e.g. two concurrent signups for the same
address) -- `create_account` raises `EmailAlreadyRegistered` for that, rather than letting a
`django.db.utils.IntegrityError` from the model's unique constraint escape instead.
"""

from ..models import User
from .email_confirmation import get_email_confirmation_service


class EmailAlreadyRegistered(Exception):
    """Raised by `create_account` when `email` already belongs to an account."""


def create_account(*, name: str, email: str, password: str) -> User:
    """Create a new user account and send its confirmation email.

    Raises:
        EmailAlreadyRegistered: `email` is already registered.
    """
    if User.objects.filter(email=email).exists():
        raise EmailAlreadyRegistered(email)

    user = User.objects.create_user(email=email, password=password, name=name)
    get_email_confirmation_service().send_confirmation_email(user)
    return user
