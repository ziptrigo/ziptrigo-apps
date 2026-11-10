"""Acceptance of the Terms of Use and Privacy Policy (issue #96).

Every account has the version of the documents it accepted on record (`User.terms_version`,
`User.terms_accepted_at`). The sign-up form records it at creation (`signup.create_account`); an
account that never ticked the form's checkbox ("Sign up with Google/Microsoft", accounts older
than this feature, `inv users create`) or accepted an older version is sent to the accept page by
`apps.accounts.middleware.TermsAcceptanceMiddleware` on its next visit to the website. The JWT API
is not gated: accounts are only created in the browser.

The current version is `apps.core.legal.TERMS_VERSION`; bumping it asks everyone again.
"""

from datetime import datetime

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core import legal

from ..models import User

TERMS_REQUIRED_MESSAGE = 'You need to agree to the Terms of Use to continue.'


class TermsNotAccepted(ValidationError):
    """An account can't be created without accepting the terms."""

    def __init__(self) -> None:
        super().__init__({'accept_terms': TERMS_REQUIRED_MESSAGE})


def current_version() -> str:
    return legal.TERMS_VERSION


def needs_acceptance(user: User) -> bool:
    """Whether `user` has yet to accept the documents in force (never, or an older version)."""
    return user.terms_version != current_version()


def acceptance_fields(now: datetime | None = None) -> dict[str, object]:
    """The two column values that record an acceptance of the current version, made now."""
    return {'terms_version': current_version(), 'terms_accepted_at': now or timezone.now()}


def accept(user: User) -> bool:
    """Record that `user` accepted the current version. Returns whether anything changed: a user
    already on it keeps the date of the first acceptance. Writes only its own columns."""
    if user.terms_version == current_version():
        return False
    fields = acceptance_fields()
    for name, value in fields.items():
        setattr(user, name, value)
    user.save(update_fields=[*fields, 'updated_at'])
    return True
