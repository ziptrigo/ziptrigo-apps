"""Draft ownership for anonymous senders (spec section 2): tied to the browser's Django session,
but *not* to the session's own key (`session.session_key`) -- that key is exactly what
`django.contrib.auth.login()` rotates (`cycle_key()`) the moment a sender logs in mid-flow, and,
separately, storing it verbatim on `Transfer` would mean an authenticated user's real session key
-- session *takeover* material -- sits in a read-only admin list (`apps.file_transfer.admin`) next
to a completely unrelated feature. Instead, a random, opaque token is generated per draft and kept
only in the session's own data (`request.session[...]`), which `cycle_key()` *preserves* (it
copies the data over to a new key, see its docstring) -- so a rotation mid-flow doesn't lose
ownership. Only a salted HMAC-SHA256 digest of the token (the same pattern
`apps.core.services.email_verification` uses for codes/tokens) is ever persisted on `Transfer`
itself, as `draft_token_hash`.
"""

import hmac
import secrets
from hashlib import sha256

from django.conf import settings
from django.contrib.sessions.backends.base import SessionBase

from ..models import Transfer

#: `{str(transfer_id): token}` for every draft/pending/active transfer this session has ever
#: created -- so a sender who sends more than once in the same browser session can still come
#: back to an earlier one (e.g. its "sent" page) without the later draft's token overwriting it.
_DRAFTS_SESSION_KEY = 'file_transfer_anon_drafts'

#: `[str(transfer_id), ...]`: transfers this session confirmed by *link* (see
#: `mark_confirmed_via_link`) -- a link deliberately works from a browser session that never
#: created the draft at all (spec: "the sender opens their email on their phone"), so that
#: session needs its own, separate proof of "was just involved in confirming this one specific
#: transfer" to view its sent page, rather than the draft-ownership check above.
_CONFIRMED_SESSION_KEY = 'file_transfer_anon_confirmed'

#: Which transfer `send_page` resumes into on a bare revisit, with no id in the URL.
_CURRENT_SESSION_KEY = 'file_transfer_anon_current'


def _digest(token: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), token.encode(), sha256).hexdigest()


def new_draft_token() -> str:
    """A fresh opaque per-draft token. Hash it (`hash_draft_token`) before storing it on
    `Transfer.draft_token_hash`; keep the raw value only in the session."""
    return secrets.token_urlsafe(32)


def hash_draft_token(token: str) -> str:
    return _digest(token)


def remember_draft(session: SessionBase, transfer_id: object, token: str) -> None:
    """Record that `session` created (and so owns) `transfer_id`, and make it the session's
    "current" draft (`current_draft_id`). Called once, right when the draft is created."""
    drafts = dict(session.get(_DRAFTS_SESSION_KEY, {}))
    drafts[str(transfer_id)] = token
    session[_DRAFTS_SESSION_KEY] = drafts
    session[_CURRENT_SESSION_KEY] = str(transfer_id)


def current_draft_id(session: SessionBase) -> str | None:
    """The transfer id `send_page` should resume into, or `None` for a session that's never
    started one (or whose only one this session no longer owns -- see `owns_draft`)."""
    return session.get(_CURRENT_SESSION_KEY)


def owns_draft(session: SessionBase, transfer: Transfer) -> bool:
    """Whether `session` is the one that created `transfer` (which must be an anonymous
    transfer -- a logged-in one is never session-owned): its per-draft token, compared against
    `transfer.draft_token_hash` in constant time."""
    token = session.get(_DRAFTS_SESSION_KEY, {}).get(str(transfer.id))
    if not token or not transfer.draft_token_hash:
        return False
    return hmac.compare_digest(_digest(token), transfer.draft_token_hash)


def mark_confirmed_via_link(session: SessionBase, transfer_id: object) -> None:
    """Record that `session` just confirmed `transfer_id` by clicking its email link -- so its
    sent page (`views.anonymous.sent_page`) can be shown here too, even though this session never
    created the draft (see the module docstring)."""
    confirmed = list(session.get(_CONFIRMED_SESSION_KEY, []))
    key = str(transfer_id)
    if key not in confirmed:
        confirmed.append(key)
        session[_CONFIRMED_SESSION_KEY] = confirmed


def can_view_sent_page(session: SessionBase, transfer: Transfer) -> bool:
    """Whether `session` may see `transfer`'s sent page (spec section 8's "copy" page): either it
    created the draft, or it's the session that clicked the transfer's confirmation link. Neither
    is true for an arbitrary visitor who merely guesses/receives the transfer's UUID -- which the
    page's URL, the download link and the manage link all otherwise expose (see
    `views.anonymous.sent_page`)."""
    if owns_draft(session, transfer):
        return True
    return str(transfer.id) in session.get(_CONFIRMED_SESSION_KEY, [])
