"""System checks for the accounts app, run by `manage.py check` and at `runserver` startup."""

from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Warning, register

from .services.captcha import TEST_SECRET_KEYS

_GOOGLE_SETTINGS = ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REDIRECT_URI')


@register()
def check_google_oauth(*args, **kwargs):
    """Warn when "Sign in with Google" is only partly configured (issue #91).

    All three `GOOGLE_*` settings empty is fine -- the feature is simply off, which is the state
    of an environment without a Google OAuth client -- and so is all three set. Anything in
    between is almost certainly a forgotten variable, and would otherwise surface only as a
    button that silently isn't there.
    """
    missing = [name for name in _GOOGLE_SETTINGS if not getattr(settings, name, '')]
    if not missing or len(missing) == len(_GOOGLE_SETTINGS):
        return []
    return [
        Warning(
            f'Google sign-in is only partly configured: {", ".join(missing)} not set.',
            hint=(
                'Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI to enable '
                'it, or leave all three empty to disable it. Until then the "Sign in with '
                'Google" button is hidden.'
            ),
            id='accounts.W001',
        )
    ]


_MICROSOFT_SETTINGS = ('MICROSOFT_CLIENT_ID', 'MICROSOFT_CLIENT_SECRET', 'MICROSOFT_REDIRECT_URI')


@register()
def check_microsoft_oauth(*args, **kwargs):
    """Warn when "Sign in with Microsoft" is only partly configured (issue #156): the same rule
    as `check_google_oauth` -- all three `MICROSOFT_*` settings empty or all set is fine."""
    missing = [name for name in _MICROSOFT_SETTINGS if not getattr(settings, name, '')]
    if not missing or len(missing) == len(_MICROSOFT_SETTINGS):
        return []
    return [
        Warning(
            f'Microsoft sign-in is only partly configured: {", ".join(missing)} not set.',
            hint=(
                'Set MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET and MICROSOFT_REDIRECT_URI to '
                'enable it, or leave all three empty to disable it. Until then the "Sign in with '
                'Microsoft" button is hidden.'
            ),
            id='accounts.W004',
        )
    ]


_TURNSTILE_SETTINGS = ('TURNSTILE_SITE_KEY', 'TURNSTILE_SECRET_KEY')


@register()
def check_turnstile(*args, **kwargs):
    """Warn when the register page's captcha is only partly configured (issue #117).

    Both keys empty means the captcha is off and both set means it's on; one without the other is
    almost certainly a forgotten variable, and would otherwise surface only as a register page
    with no captcha on it.
    """
    missing = [name for name in _TURNSTILE_SETTINGS if not getattr(settings, name, '')]
    if not missing or len(missing) == len(_TURNSTILE_SETTINGS):
        return []
    return [
        Warning(
            f'The signup captcha is only partly configured: {", ".join(missing)} not set.',
            hint=(
                'Set TURNSTILE_SITE_KEY and TURNSTILE_SECRET_KEY to enable it, or leave both '
                'empty to disable it. Until then the register page has no captcha.'
            ),
            id='accounts.W002',
        )
    ]


_LOCAL_HOSTS = frozenset({'localhost', '127.0.0.1', '::1'})


@register()
def check_turnstile_test_secret(*args, **kwargs):
    """Warn when a deployed site uses one of Cloudflare's Turnstile *test* secrets.

    Those always pass (or always fail) whatever the token, and `captcha.verify` skips its hostname
    and action checks for them, so on a real host the captcha would protect nothing -- and nothing
    else would say so. `.env.example` documents them for local runs, which makes copying one into
    `.env.prod`/`.env.staging` an easy slip. Shows in `manage.py check`, at `runserver` startup and
    in the container's `migrate` step at every deploy.
    """
    if settings.TURNSTILE_SECRET_KEY not in TEST_SECRET_KEYS:
        return []
    host = urlsplit(settings.BASE_URL).hostname
    if host in _LOCAL_HOSTS:
        return []
    return [
        Warning(
            f'TURNSTILE_SECRET_KEY is a Cloudflare test secret, but BASE_URL is {host!r}.',
            hint=(
                "Test secrets pass every token, so the register page's captcha does nothing. "
                'Create a real Turnstile widget for this host and use its keys.'
            ),
            id='accounts.W003',
        )
    ]
