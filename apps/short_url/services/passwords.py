"""A link's optional password: set on the owner's form, asked of visitors before the redirect.

Stored hashed with Django's own password hasher, like file transfer's download passwords. A
visitor who enters it correctly is remembered in their session *for that link*; the session holds
a fingerprint of the password hash rather than a bare flag, so changing or removing the password
locks out everyone who had unlocked the old one.
"""

from django.contrib.auth.hashers import check_password as _check_password
from django.contrib.auth.hashers import make_password
from django.utils.crypto import salted_hmac

from ..models import ShortLink
from .errors import field_error

MAX_PASSWORD_LENGTH = 128

#: The session key: `{link id: fingerprint}`. Kept to the last `MAX_REMEMBERED` links so a
#: visitor who opens many protected links can't grow their session without bound.
SESSION_KEY = 'short_url_unlocked'
MAX_REMEMBERED = 100


def validate_password(raw: str) -> str:
    """The password as given, or a `ValidationError` on `password`: it is kept as typed (spaces
    included), must have something other than spaces in it, and at most `MAX_PASSWORD_LENGTH`
    characters (a long one only makes the hash slower to check)."""
    if not raw.strip():
        raise field_error('password', 'Enter a password.')
    if len(raw) > MAX_PASSWORD_LENGTH:
        raise field_error('password', f'Use at most {MAX_PASSWORD_LENGTH} characters.')
    return raw


def hash_password(raw: str) -> str:
    return make_password(validate_password(raw))


def check_password(link: ShortLink, raw: str) -> bool:
    """Whether `raw` is the link's password (False for a link without one or an over-long guess,
    which is refused before it is hashed)."""
    if not link.password_hash or len(raw) > MAX_PASSWORD_LENGTH:
        return False
    return _check_password(raw, link.password_hash)


def _fingerprint(password_hash: str) -> str:
    return salted_hmac('short_url.unlock', password_hash, algorithm='sha256').hexdigest()


def is_unlocked(session, link: ShortLink) -> bool:
    """Whether this session already entered the link's *current* password."""
    if not link.password_hash:
        return True
    remembered = session.get(SESSION_KEY)
    return isinstance(remembered, dict) and remembered.get(str(link.pk)) == _fingerprint(
        link.password_hash
    )


def unlock(session, link: ShortLink) -> None:
    """Remember in `session` that `link`'s password was entered correctly."""
    remembered = session.get(SESSION_KEY)
    remembered = dict(remembered) if isinstance(remembered, dict) else {}
    remembered.pop(str(link.pk), None)  # re-insert last, so it is the newest
    remembered[str(link.pk)] = _fingerprint(link.password_hash or '')
    while len(remembered) > MAX_REMEMBERED:
        del remembered[next(iter(remembered))]
    session[SESSION_KEY] = remembered
