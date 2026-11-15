"""Who is uploading to a file request (issue #306), and which files are theirs.

A signed-in uploader is their account: the name and email stored with each file are copies of the
account's at upload time. An anonymous uploader is a *browser*: a random token kept in the
session (the `services.anon_session` pattern -- never the session's own key, which login rotates;
only an HMAC-SHA256 digest of the token is stored, on `TransferFile.uploader_token_hash`), with an
optional typed name remembered in the session too.

**An uploader sees and removes only their own files**: the files of this browser or this account
(a visitor who uploaded anonymously and then signed in still sees what that browser uploaded).
"""

from dataclasses import dataclass

from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.base import SessionBase
from django.db.models import Q, QuerySet

from apps.accounts.models import User

from ..models import Transfer, TransferFile
from .anon_session import hash_draft_token, new_draft_token

#: The one per-browser token an anonymous uploader is known by, in the session.
_TOKEN_SESSION_KEY = 'file_transfer_request_uploader_token'

#: The optional name an anonymous uploader typed, remembered for the rest of the session.
_NAME_SESSION_KEY = 'file_transfer_request_uploader_name'

#: Longest name or email stored with a file (`TransferFile.uploader_name`).
MAX_NAME_LENGTH = 100


@dataclass(slots=True, frozen=True)
class Uploader:
    """The identity a file is stored under. `user` is set for a signed-in uploader, `token_hash`
    for an anonymous one (both can be known: a signed-in visitor's browser may have uploaded
    anonymously earlier, which `own_files` still counts)."""

    user: User | None
    token_hash: str
    name: str
    email: str

    @property
    def is_signed_in(self) -> bool:
        return self.user is not None


def clean_name(raw: object) -> str:
    """A typed name: control characters dropped, whitespace collapsed, at most 100 characters."""
    text = ''.join(ch for ch in str(raw or '') if ch.isprintable() or ch == ' ')
    return ' '.join(text.split())[:MAX_NAME_LENGTH]


def remember_name(session: SessionBase, name: object) -> str:
    """Keep the anonymous uploader's typed name for the session (an empty one forgets it)."""
    cleaned = clean_name(name)
    if cleaned:
        session[_NAME_SESSION_KEY] = cleaned
    elif _NAME_SESSION_KEY in session:
        del session[_NAME_SESSION_KEY]
    return cleaned


def remembered_name(session: SessionBase) -> str:
    return clean_name(session.get(_NAME_SESSION_KEY, ''))


def _existing_token_hash(session: SessionBase) -> str:
    token = session.get(_TOKEN_SESSION_KEY)
    return hash_draft_token(token) if token else ''


def _token_hash(session: SessionBase, *, create: bool) -> str:
    token = session.get(_TOKEN_SESSION_KEY)
    if not token and create:
        token = new_draft_token()
        session[_TOKEN_SESSION_KEY] = token
    return hash_draft_token(token) if token else ''


def identify(session: SessionBase, user: User | AnonymousUser, *, create: bool = False) -> Uploader:
    """The uploader behind this session. `create=True` (an upload is about to be recorded) makes
    the per-browser token if the session has none; reading the page never does."""
    if isinstance(user, User) and user.is_authenticated:
        return Uploader(
            user=user,
            token_hash=_existing_token_hash(session),
            name=clean_name(getattr(user, 'name', '') or ''),
            email=(user.email or '')[:254],
        )
    return Uploader(
        user=None,
        token_hash=_token_hash(session, create=create),
        name=remembered_name(session),
        email='',
    )


def own_files(transfer: Transfer, uploader: Uploader) -> QuerySet:
    """The files `uploader` uploaded to `transfer` -- the only ones they are shown or may remove."""
    match = Q(pk__in=[])
    if uploader.user is not None:
        match |= Q(uploader_user=uploader.user)
    if uploader.token_hash:
        match |= Q(uploader_token_hash=uploader.token_hash)
    return TransferFile.objects.filter(match, transfer=transfer)
