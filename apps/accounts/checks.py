"""System checks for the accounts app, run by `manage.py check` and at `runserver` startup."""

from django.conf import settings
from django.core.checks import Warning, register

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
