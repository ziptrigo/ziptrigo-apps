"""Draft ownership for anonymous senders (spec section 2): tied to the browser's Django session
(`Transfer.session_key`), so only the browser that created a draft may upload to, or finalize,
it -- there's no user account to check ownership against instead.
"""

from django.contrib.sessions.backends.base import SessionBase

from ..models import Transfer


def ensure_session_key(session: SessionBase) -> str:
    """Force the session to actually exist (and so send a cookie) if it doesn't have a key yet.

    An anonymous visitor's session is normally created lazily, the first time something is
    written to it -- but the very first request to the anonymous send page needs a key *before*
    anything else happens, to record on the draft it creates.
    """
    if not session.session_key:
        session.save()
    assert session.session_key is not None
    return session.session_key


def owns_draft(session: SessionBase, transfer: Transfer) -> bool:
    """Whether `session` is the one that created `transfer` (which must be an anonymous
    transfer -- a logged-in one is never session-owned)."""
    return bool(transfer.session_key) and transfer.session_key == session.session_key
