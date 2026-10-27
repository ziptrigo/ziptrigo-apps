"""Short codes and aliases.

A random code is 7 base62 characters (about 3.5 trillion). An alias is 3-32 characters of
`[A-Za-z0-9_-]`. Both live in one namespace, compared **case-insensitively**: `code_key` is the
lower-cased code, unique in the database, so `/s/Sale` and `/s/sale` cannot both exist and the
redirect accepts any case.
"""

import re
import secrets
import string

from ..models import ShortLink
from .errors import AliasTakenError, field_error

ALPHABET = string.ascii_letters + string.digits
CODE_LENGTH = 7
ALIAS_PATTERN = re.compile(r'[A-Za-z0-9_-]{3,32}')
MIN_ALIAS_LENGTH = 3
MAX_ALIAS_LENGTH = 32

#: How many random codes `generate_code` tries before giving up (a collision is a one-in-billions
#: event, so reaching this means something is wrong, not that the space is full).
MAX_GENERATION_ATTEMPTS = 20

#: Aliases nobody may take: the site's own paths and words that would make a link look official.
#: Compared on the lower-cased alias.
RESERVED = frozenset(
    {
        'about',
        'account',
        'accounts',
        'admin',
        'api',
        'app',
        'auth',
        'billing',
        'contact',
        'docs',
        'faq',
        'feedback',
        'files',
        'go',
        'help',
        'home',
        'links',
        'login',
        'logout',
        'media',
        'privacy',
        'qr',
        'receipts',
        'register',
        'root',
        's',
        'settings',
        'signin',
        'signup',
        'static',
        'status',
        'support',
        't',
        'terms',
        'transfer',
        'ziptrigo',
    }
)


def normalize_key(code: str) -> str:
    """What the redirect looks a code up by: the lower-cased code."""
    return code.strip().lower()


def is_reserved(code: str) -> bool:
    return normalize_key(code) in RESERVED


def is_available(code: str) -> bool:
    """Whether `code` can still be given to a new link: not reserved and not any link's code,
    deleted ones included (a code is never issued twice)."""
    key = normalize_key(code)
    return key not in RESERVED and not ShortLink.objects.filter(code_key=key).exists()


def generate_code() -> str:
    """A random, unused code of `CODE_LENGTH` base62 characters."""
    for _ in range(MAX_GENERATION_ATTEMPTS):
        code = ''.join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
        if is_available(code):
            return code
    raise RuntimeError('Could not find a free short code.')


def validate_alias(alias: str) -> str:
    """The alias, stripped, if it is well formed and free. Raises `ValidationError` for a bad
    shape and `AliasTakenError` for one that is reserved or used."""
    alias = alias.strip()
    if not ALIAS_PATTERN.fullmatch(alias):
        raise field_error(
            'alias',
            f'Use {MIN_ALIAS_LENGTH}-{MAX_ALIAS_LENGTH} letters, digits, hyphens or underscores.',
        )
    if not is_available(alias):
        raise AliasTakenError()
    return alias
