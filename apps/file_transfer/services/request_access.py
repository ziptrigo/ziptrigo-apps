"""Who may use a file request's link (issue #306), in the order every request page and endpoint
checks it (upload, remove, done, download, zip, unlock):

1. **Password**, if the request has one: the existing unlock page and rules. The owner, signed in,
   is never asked.
2. **Audience** (`Transfer.upload_audience`): *anyone* needs nothing more; *signed in* needs a
   session; *selected* needs a session whose account is the owner or whose **confirmed** email
   matches an invite (ignoring case). The owner always passes.

The same audience applies to downloading after the request is done (there is no separate download
setting). `check_access` only decides; the views turn each answer into a page, a redirect to the
sign-in page or a refusal. Nothing it returns (or the pages built on it) says who the invitees are.
"""

from enum import StrEnum

from django.contrib.sessions.backends.base import SessionBase

from ..models import Transfer, UploadAudience
from .password import is_unlocked_in_session


class Access(StrEnum):
    """What a visitor may do with a request link right now."""

    OK = 'ok'
    #: The request has a password this session has not entered yet.
    NEEDS_PASSWORD = 'needs_password'
    #: The audience needs a signed-in account and there is no session.
    NEEDS_SIGN_IN = 'needs_sign_in'
    #: Signed in with an invited address that has not been confirmed.
    NEEDS_CONFIRMED_EMAIL = 'needs_confirmed_email'
    #: Signed in, but not as one of the invited people.
    NOT_INVITED = 'not_invited'


def is_owner(transfer: Transfer, user) -> bool:
    return bool(user is not None and user.is_authenticated and transfer.owner_id == user.pk)


def invited_by_email(transfer: Transfer, email: str) -> bool:
    """Whether `email` is one of the request's invitees, ignoring case."""
    email = (email or '').strip()
    if not email:
        return False
    return transfer.invites.filter(email__iexact=email).exists()


def check_access(transfer: Transfer, user, session: SessionBase) -> Access:
    """The access decision for `user` (possibly anonymous) on `transfer`'s link, given the
    browser's `session` (for the unlocked-password memory)."""
    if is_owner(transfer, user):
        return Access.OK

    if transfer.password_hash and not is_unlocked_in_session(
        session, transfer.id, transfer.password_hash
    ):
        return Access.NEEDS_PASSWORD

    audience = transfer.upload_audience
    if audience == UploadAudience.ANYONE:
        return Access.OK

    if user is None or not user.is_authenticated:
        return Access.NEEDS_SIGN_IN
    if audience == UploadAudience.SIGNED_IN:
        return Access.OK

    # Selected people.
    if not invited_by_email(transfer, user.email):
        return Access.NOT_INVITED
    if not getattr(user, 'email_confirmed', False):
        return Access.NEEDS_CONFIRMED_EMAIL
    return Access.OK
