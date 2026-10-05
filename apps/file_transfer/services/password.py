"""Transfer download passwords: optional, set at creation, changeable later (spec section 3).
Stored hashed with Django's own password hasher -- never in the clear, same as user passwords.
"""

from django.contrib.auth.hashers import check_password as _check_password
from django.contrib.auth.hashers import make_password
from django.utils.crypto import salted_hmac

#: Session key prefix remembering a transfer's password was entered correctly this browser
#: session, so the visitor isn't asked again on every download from the same transfer.
SESSION_KEY_PREFIX = 'file_transfer.unlocked.'


def hash_password(raw_password: str) -> str:
    return make_password(raw_password)


def verify_password(raw_password: str, password_hash: str) -> bool:
    return _check_password(raw_password, password_hash)


def session_key(transfer_id: object) -> str:
    return f'{SESSION_KEY_PREFIX}{transfer_id}'


def _fingerprint(password_hash: str) -> str:
    """A short digest of the *current* password hash, stored in the session instead of a bare
    `True` so that changing the password invalidates sessions that unlocked the old one -- the
    session value only matches `is_unlocked_in_session`'s check while `password_hash` is
    unchanged from when it was unlocked."""
    return salted_hmac('file_transfer.unlock', password_hash, algorithm='sha256').hexdigest()


def is_unlocked_in_session(session, transfer_id: object, password_hash: str) -> bool:
    return session.get(session_key(transfer_id)) == _fingerprint(password_hash)


def unlock_in_session(session, transfer_id: object, password_hash: str) -> None:
    session[session_key(transfer_id)] = _fingerprint(password_hash)
